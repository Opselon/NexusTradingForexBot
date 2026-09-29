"""Database health / diagnostics service.

Reports the active provider, connection status, database version, schema
version, migration status, latency, size, table count and critical-table
availability for every persistence domain.  Consumed by the DATABASE
MANAGEMENT UI panel and the CLI (``nexus db health``).
"""

from __future__ import annotations

import json
import time
from typing import Any, ClassVar

from nexus_scalp.database.config import DatabaseConfig, load_database_config
from nexus_scalp.database.drivers import get_driver
from nexus_scalp.database.provider import DatabaseProvider
from nexus_scalp.database.query_logging import query_observability_snapshot
from nexus_scalp.database.views import ensure_analytics_views
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.database.health")

#: Identity sentinel for the R-1 memo's driver-injection bypass: if
#: ``get_driver`` on this module has been replaced (tests and diagnostics
#: inject their own driver), the memo is bypassed so an injected failure can
#: never read a cached healthy snapshot for the same target.
_ORIGINAL_GET_DRIVER = get_driver

#: Tables whose availability matters for trading safety.
CRITICAL_TABLES: dict[str, tuple[str, ...]] = {
    "audit": ("audit_ledger", "audit_orders", "audit_signals", "audit_account_snapshots"),
    "news": ("news_articles", "news_impacts"),
    "candle_intel": ("candles", "candle_closures"),
}


def _connection_reason(exc: BaseException) -> str:
    """The operator-facing reason a connection attempt failed.

    psycopg's message embeds the server's own diagnostic (``fe_sendauth: no
    password supplied``, ``FATAL: password authentication failed for user
    "postgres"``, ``Connection refused``, ``database "nexusdb" does not
    exist``). That is exactly the fact that separates a credential rotation
    from a service start from a provisioning step, so it is surfaced with the
    failure class prefixed for the health UI. The message is never logged or
    echoed in full to a UI: a driver exception carries no credential material
    (the password travels out of band via the secret store, and the libpq
    error text reports the AUTH OUTCOME, never the secret itself).
    """
    kind = type(exc).__name__
    text = str(exc).strip().replace("\n", " ")
    if not text:
        return kind
    # psycopg prefixes "connection failed:" itself; keep one layer.
    if text.lower().startswith("connection failed:"):
        text = text.split(":", 1)[1].strip()
    return f"{kind}: {text}"[:300]


def _last_connection_error(driver: Any) -> str:
    """Best-effort reason when ``ping()`` returned False without raising."""
    failure = getattr(driver, "last_failure", None)
    if isinstance(failure, BaseException) and str(failure).strip():
        return _connection_reason(failure)
    # Legacy drivers without the attribute: fall back to anything they expose.
    for attr in ("_last_error", "last_error", "_last_connection_error"):
        candidate = getattr(driver, attr, None)
        if isinstance(candidate, BaseException) and str(candidate).strip():
            return _connection_reason(candidate)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return ""


class DatabaseHealthService:
    """Snapshot health of every persistent domain for the active provider."""

    #: R-1 HOT-PATH MEMO (Phase-2 remediation R-1). ``check_domain`` costs up
    #: to ~152 ms per call (7 catalog round trips on PostgreSQL), and
    #: ``get_system_state()`` calls it for the "audit" domain on EVERY SSE
    #: tick — 5 times per second per connected client — via
    #: ``_build_health_section``. Both call sites construct a FRESH
    #: ``DatabaseHealthService`` per call, so the memo cannot live on the
    #: instance; it is class-level and keyed by the RESOLVED CONFIG (provider
    #: + target), which is what actually identifies the thing being probed.
    #:
    #: Correctness contract (see test_r1_health_domain_hot_path_memo):
    #:   * failures are NEVER memoized — an Error/Warning/disconnected
    #:     result is recomputed on every call so the operator sees the
    #:     recovery the instant it happens (errors must stay retryable, the
    #:     same rule the PERF-DB-STATUS cache pins);
    #:   * a healthy CONNECTED snapshot is replayed within the TTL, with its
    #:     original latency_ms preserved and the memo hit disclosed;
    #:   * the memo is bypassed whenever ``get_driver`` has been replaced on
    #:     this module (tests and diagnostics inject their own driver), so an
    #:     injected failure can never read a cached healthy snapshot;
    #:   * ``_memo_allow_injected_drivers`` is a TEST HOOK to exercise the
    #:     memo against a counting driver; production never sets it.
    DOMAIN_HEALTH_TTL_SECONDS: float = 5.0

    _domain_health_cache: ClassVar[dict[tuple, tuple[float, dict[str, Any]]]] = {}

    #: TEST HOOK ONLY. The driver-injection bypass disables the memo whenever
    #: ``get_driver`` has been replaced, which is how every test installs its
    #: own driver — so without this hook the memo path would be untestable.
    #: Tests set it to observe replay behaviour with a driver; production
    #: leaves it False, where the bypass is the only guard.
    _memo_allow_injected_drivers: bool = False

    def __init__(self, workspace: str | None = None, settings_db_path: str | None = None) -> None:
        self.workspace = workspace
        self.settings_db_path = settings_db_path
        self.domains: tuple[str, ...] = ("audit", "news", "candle_intel")

    def resolve_config(self, domain: str) -> DatabaseConfig:
        return load_database_config(domain, settings_db_path=self.settings_db_path, env=None)

    @staticmethod
    def _config_identity(cfg: Any) -> tuple:
        """Stable key for the database being probed (provider + target).

        Two services resolving to the same provider pointing at the same
        target share one memo entry; a config change (provider switch, path
        change) gets a fresh entry. Reads ``sqlite_connect_path`` (URI or
        path, whichever the driver actually opens) so a path-only config and
        a URI config for the same file do not get two entries, and a URI
        pointing elsewhere does not collide with the default path.
        """
        if getattr(cfg, "is_postgresql", False):
            return (
                "postgresql",
                str(getattr(cfg, "host", "")),
                int(getattr(cfg, "port", 0) or 0),
                str(getattr(cfg, "database", "")),
            )
        return ("sqlite", str(getattr(cfg, "sqlite_connect_path", "") or ":memory:"))

    @classmethod
    def invalidate_domain_cache(cls, domain: str | None = None) -> None:
        """Drop memoized health (after a provider switch or migration)."""
        if domain is None:
            cls._domain_health_cache.clear()
            return
        for key in [k for k in cls._domain_health_cache if k[0] == domain]:
            cls._domain_health_cache.pop(key, None)

    def check_domain(self, domain: str) -> dict[str, Any]:
        """Health snapshot for one domain (never raises)."""
        # R-1: replay a healthy snapshot within the TTL instead of re-running
        # ~7 catalog round trips at 5 Hz per client. The memo is keyed by the
        # RESOLVED CONFIG (provider + target) — the identity of the thing
        # actually being probed — and is bypassed whenever the resolution
        # surface has been overridden for this domain, because a test (or a
        # diagnostic) that installs its own driver must never read a cached
        # healthy snapshot for the same target.
        cfg = None
        try:
            cfg = self.resolve_config(domain)
        except Exception:
            cfg = None
        memo_enabled = cfg is not None and (
            get_driver is _ORIGINAL_GET_DRIVER or type(self)._memo_allow_injected_drivers
        )
        if memo_enabled:
            key = (domain, *self._config_identity(cfg))
            now_mono = time.monotonic()
            cached = type(self)._domain_health_cache.get(key)
            if cached is not None and (now_mono - cached[0]) < self.DOMAIN_HEALTH_TTL_SECONDS:
                snap = dict(cached[1])
                snap["memo"] = True
                return snap
        out = self._compute_domain_snapshot(domain)
        # Memoize only the healthy connected result; failures must stay live.
        if memo_enabled and out.get("connected") and out.get("health") == "Healthy":
            key = (domain, *self._config_identity(cfg))
            type(self)._domain_health_cache[key] = (time.monotonic(), dict(out))
        return out

    def _compute_domain_snapshot(self, domain: str) -> dict[str, Any]:
        """The uncached probe (never raises)."""
        out: dict[str, Any] = {
            "domain": domain,
            "provider": "UNKNOWN",
            "status": "UNKNOWN",
            "connected": False,
            "database": "",
            "server": "",
            "schema_version": 0,
            "expected_version": 0,
            "migration_state": "",
            "health": "ERROR",
            "latency_ms": None,
            "size_bytes": None,
            "table_count": 0,
            "critical_tables": {},
            "error": "",
        }
        try:
            cfg = self.resolve_config(domain)
            out["provider"] = cfg.provider.value
            if cfg.is_postgresql:
                out["database"] = cfg.database
                out["server"] = f"{cfg.host}:{cfg.port or 5432}"
            else:
                out["database"] = cfg.sqlite_connect_path.split("/")[-1]
                out["server"] = "Local"
            try:
                driver = get_driver(cfg)
            except RuntimeError as exc:  # psycopg missing
                out["error"] = str(exc)
                out["status"] = "DRIVER_UNAVAILABLE"
                out["health"] = "Warning"
                return out

            # HEALTH-DBREASON: a failed connection must report the driver's
            # OWN exception, not a fixed "connection failed" string. The real
            # message distinguishes the actionable cases an operator must
            # separate: ``fe_sendauth: no password supplied`` /
            # ``password authentication failed`` (rotate the credential in the
            # secret store) vs ``Connection refused`` (start the service) vs
            # ``database "x" does not exist`` (provision it). The fixed string
            # collapsed all of them into one useless red, so the box looked
            # merely "down" while the real defect was a bad password.
            t0 = time.perf_counter()
            try:
                ping = driver.ping()
            except Exception as ping_exc:  # never raises to the caller
                out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
                out["status"] = "DISCONNECTED"
                out["health"] = "Error"
                out["error"] = _connection_reason(ping_exc)
                return out
            out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            if not ping:
                out["status"] = "DISCONNECTED"
                out["health"] = "Error"
                out["error"] = _last_connection_error(driver) or "connection failed"
                return out
            out["connected"] = True
            out["status"] = "CONNECTED"
            out["database_version"] = driver.database_version()

            # SQLite uses the migration engine. PostgreSQL reads its own metadata.
            try:
                if cfg.is_postgresql:
                    version = driver.scalar(
                        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                    )
                    out["schema_version"] = int(version or 0)
                    out["migration_state"] = "CURRENT"
                else:
                    from nexus_scalp.database.engine import DatabaseMigrationEngine
                    from nexus_scalp.database.models import DatabaseDomain

                    eng = DatabaseMigrationEngine(
                        db_path=cfg.sqlite_connect_path, domain=DatabaseDomain(domain)
                    )
                    st = eng.status()
                    out["schema_version"] = st.get("current_version", 0)
                    out["expected_version"] = st.get("expected_version", 0)
                    out["migration_state"] = st.get("migration_state", "")
            except Exception:
                out["schema_version"] = None
                out["migration_state"] = "UNKNOWN"

            out["table_count"] = driver.table_count()
            try:
                size = driver.database_size_bytes()
                out["size_bytes"] = size
            except Exception:
                out["size_bytes"] = None

            # Critical table availability
            crit: dict[str, str] = {}
            for table in CRITICAL_TABLES.get(domain, ()):
                crit[table] = "OK" if driver.table_exists(table) else "MISSING"
            out["critical_tables"] = crit
            missing = [t for t, s in crit.items() if s != "OK"]
            if missing:
                out["health"] = "Warning"
            else:
                out["health"] = "Healthy"
            out["status"] = "CONNECTED"
            out.pop("error", None)
            return out
        except Exception as exc:
            out["error"] = str(exc)[:300]
            out["health"] = "Error"
            return out

    def snapshot(self) -> dict[str, Any]:
        """Health for all domains + the active provider summary."""
        domains = {d: self.check_domain(d) for d in self.domains}
        configured: set[str] = set()
        for domain in self.domains:
            try:
                configured.add(self.resolve_config(domain).provider.value)
            except Exception:
                continue
        active = (
            configured.pop()
            if len(configured) == 1
            else (",".join(sorted(configured)) or "UNKNOWN")
        )
        healthy = all(d["health"] == "Healthy" for d in domains.values() if d.get("connected"))
        warn = any(d["health"] in {"Warning", "Error"} for d in domains.values())
        return {
            "active_provider": active,
            "supported_providers": [p.value for p in DatabaseProvider],
            "overall": "Healthy" if healthy and not warn else ("Warning" if warn else "Error"),
            "domains": domains,
            "timestamp_utc": _utc_now(),
        }

    def dashboard_snapshot(self) -> dict[str, Any]:
        """Comprehensive dashboard; unavailable measurements are explicit."""
        base = self.snapshot()
        audit_domain = base["domains"].get("audit", {})
        cfg = self.resolve_config("audit")
        pool_stats: dict[str, Any]
        views: dict[str, dict[str, str]] = {}
        if cfg.is_postgresql:
            pool_stats = {"applicable": True, "source": "PgPool.stats"}
            try:
                from nexus_scalp.database.ops_provider import ensure_read_plane

                plane = ensure_read_plane("audit")
                pool = getattr(plane, "_primary", None)
                stats = pool.stats() if pool is not None else {}
                available = stats.get("available", {})
                pool_stats.update(
                    {
                        "active": (
                            available.get("pool_size") - available.get("pool_available")
                            if available.get("pool_size") is not None
                            and available.get("pool_available") is not None
                            else None
                        ),
                        "idle": available.get("pool_available"),
                        "waiting": available.get("requests_waiting"),
                        "raw": stats,
                    }
                )
            except Exception as exc:
                # Public-safe: ``pool_stats`` is embedded verbatim in the HTTP
                # dashboard body, so no exception text reaches the client.
                logger.error("Dashboard pool stats failed: %s", exc, exc_info=True)
                pool_stats.update({"error": "POOL_STATS_UNAVAILABLE"})
        else:
            pool_stats = {"applicable": False, "reason": "SQLite has no connection pool"}

        dead_tuples: int | None = None
        index_count: int | None = None
        largest_tables: list[dict[str, Any]] = []
        try:
            driver = get_driver(cfg)
            try:
                if cfg.is_postgresql:
                    index_count = int(
                        driver.scalar("SELECT COUNT(*) FROM pg_indexes WHERE schemaname = 'public'")
                        or 0
                    )
                    dead_tuples = int(
                        driver.scalar(
                            "SELECT COALESCE(SUM(n_dead_tup), 0) FROM pg_stat_user_tables"
                        )
                        or 0
                    )
                    rows = driver.query(
                        "SELECT relname AS table_name, n_live_tup AS row_count FROM pg_stat_user_tables ORDER BY n_live_tup DESC LIMIT 5"
                    )
                    largest_tables = [
                        {"table": r.get("table_name"), "rows": r.get("row_count")} for r in rows
                    ]
                else:
                    index_count = int(
                        driver.scalar("SELECT COUNT(*) FROM sqlite_master WHERE type = 'index'")
                        or 0
                    )
                views = ensure_analytics_views(driver)
            finally:
                driver.close()
        except Exception as exc:
            # Public-safe: the HTTP dashboard surfaces this verbatim, so the fixed
            # token carries no exception text, driver class or path; the real
            # failure (with traceback) goes to the log only.
            logger.error("Dashboard analytics views failed: %s", exc, exc_info=True)
            views = {"_ensure": {"status": "FAILED", "error": "VIEW_ENSURE_FAILED"}}

        unavailable = {
            "value": None,
            "status": "unavailable",
            "reason": "lifecycle accessor not available",
        }
        observability = query_observability_snapshot()
        return {
            "provider": base["active_provider"],
            "overall_health": base["overall"],
            "connected": audit_domain.get("connected", False),
            "latency_ms": audit_domain.get("latency_ms"),
            "database_size_bytes": audit_domain.get("size_bytes"),
            "table_count": audit_domain.get("table_count", 0),
            "index_count": index_count,
            "dead_tuples": dead_tuples,
            "largest_tables": largest_tables,
            "pool": pool_stats,
            "active_connections": pool_stats.get("active"),
            "idle_connections": pool_stats.get("idle"),
            "oldest_connection": unavailable,
            "failed_queries": observability.get("query_errors"),
            "slow_queries": observability.get("slow_queries"),
            "last_purge": unavailable,
            "next_purge": unavailable,
            "last_integrity_check": unavailable,
            "views": views,
            "schema_version": audit_domain.get("schema_version"),
            "critical_tables": audit_domain.get("critical_tables", {}),
            "timestamp_utc": _utc_now(),
        }


def _engine_path_for(cfg: DatabaseConfig) -> str:
    """TASK-10 migration engine still keys on a filesystem path; for
    PostgreSQL we pass the sqlite fallback path (per-domain migration state
    remains in the SQLite artifacts DB — schema migration of PG is owned by
    the migrator + app bootstrap)."""
    if cfg.is_postgresql:
        from nexus_scalp.database.provider import default_sqlite_path

        return default_sqlite_path(cfg.domain)
    return cfg.sqlite_connect_path


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


def health_snapshot(workspace: str | None = None) -> dict[str, Any]:
    """Convenience wrapper for CLI/UI."""
    return DatabaseHealthService(workspace=workspace).snapshot()


def load_ui_config(workspace: str | None = None) -> dict[str, Any]:
    """Current provider + postgres config (password NEVER included)."""
    from nexus_scalp.database.config import (
        PG_CONFIG_SETTING_KEY,
        PROVIDER_SETTING_KEY,
    )
    from nexus_scalp.settings.service import SettingsDatabase

    out: dict[str, Any] = {
        "provider": "sqlite",
        "postgres": None,
        "password_set": False,
    }
    try:
        db = SettingsDatabase()
        prov = db.get(PROVIDER_SETTING_KEY)
        if prov and prov.value:
            out["provider"] = DatabaseProvider.parse(prov.value).value
        raw = db.get(PG_CONFIG_SETTING_KEY)
        if raw and raw.value:
            try:
                out["postgres"] = json.loads(raw.value)
                if out["postgres"].get("password_secret"):
                    from nexus_scalp.settings.secret_store import SecureSecretStore

                    out["password_set"] = SecureSecretStore().has_secret(
                        out["postgres"]["password_secret"]
                    )
            except (TypeError, ValueError):
                out["postgres"] = None
        db.close()
    except Exception:
        pass
    return out
