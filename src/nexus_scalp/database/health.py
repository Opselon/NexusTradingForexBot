"""Database health / diagnostics service.

Reports the active provider, connection status, database version, schema
version, migration status, latency, size, table count and critical-table
availability for every persistence domain.  Consumed by the DATABASE
MANAGEMENT UI panel and the CLI (``nexus db health``).
"""

from __future__ import annotations

import json
import time
from typing import Any

from nexus_scalp.database.config import DatabaseConfig, load_database_config
from nexus_scalp.database.drivers import get_driver
from nexus_scalp.database.provider import DatabaseProvider
from nexus_scalp.database.query_logging import query_observability_snapshot
from nexus_scalp.database.views import ensure_analytics_views

#: Tables whose availability matters for trading safety.
CRITICAL_TABLES: dict[str, tuple[str, ...]] = {
    "audit": ("audit_ledger", "audit_orders", "audit_signals", "audit_account_snapshots"),
    "news": ("news_articles", "news_impacts"),
    "candle_intel": ("candles", "candle_closures"),
}


class DatabaseHealthService:
    """Snapshot health of every persistent domain for the active provider."""

    def __init__(self, workspace: str | None = None, settings_db_path: str | None = None) -> None:
        self.workspace = workspace
        self.settings_db_path = settings_db_path
        self.domains: tuple[str, ...] = ("audit", "news", "candle_intel")

    def resolve_config(self, domain: str) -> DatabaseConfig:
        return load_database_config(domain, settings_db_path=self.settings_db_path, env=None)

    def check_domain(self, domain: str) -> dict[str, Any]:
        """Health snapshot for one domain (never raises)."""
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

            t0 = time.perf_counter()
            ping = driver.ping()
            out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            if not ping:
                out["status"] = "DISCONNECTED"
                out["health"] = "Error"
                out["error"] = "connection failed"
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
                pool_stats.update({"error": f"{type(exc).__name__}: {exc}"})
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
            views = {"_ensure": {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}}

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
