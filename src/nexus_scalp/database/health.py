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
        return load_database_config(
            domain, settings_db_path=self.settings_db_path, env=None
        )

    def check_domain(self, domain: str) -> dict[str, Any]:
        """Health snapshot for one domain (never raises)."""
        out: dict[str, Any] = {
            "domain": domain,
            "provider": "",
            "status": "ERROR",
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

            # Schema + migration status (TASK-10 engine, unchanged).
            try:
                from nexus_scalp.database.engine import DatabaseMigrationEngine
                from nexus_scalp.database.models import DatabaseDomain

                path = _engine_path_for(cfg)
                eng = DatabaseMigrationEngine(db_path=path, domain=DatabaseDomain(domain))
                st = eng.status()
                out["schema_version"] = st.get("current_version", 0)
                out["expected_version"] = st.get("expected_version", 0)
                out["migration_state"] = st.get("migration_state", "")
            except Exception:
                out["schema_version"] = 0
                out["migration_state"] = "N/A"

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
        providers = {d["provider"] for d in domains.values() if d.get("connected")}
        active = providers.pop() if len(providers) == 1 else (",".join(sorted(providers)) or "sqlite")
        healthy = all(d["health"] == "Healthy" for d in domains.values() if d.get("connected"))
        warn = any(d["health"] in {"Warning", "Error"} for d in domains.values())
        return {
            "active_provider": active,
            "supported_providers": [p.value for p in DatabaseProvider],
            "overall": "Healthy" if healthy and not warn else ("Warning" if warn else "Error"),
            "domains": domains,
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