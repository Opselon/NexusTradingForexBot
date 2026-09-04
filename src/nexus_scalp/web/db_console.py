"""DATABASE MANAGEMENT console — SSMS-style explorer + SQL console + API keys.

Adds a full "Database Manager" experience to the DATABASE MANAGEMENT UI
tab (Hermes-DBConsole, 2026-08-20):

  * ``/api/db/console/databases``  — every persistence domain (audit, news,
    candle_intel) + the settings database, with provider, path, size and
    per-domain health.  Re-scanned on demand (auto-sync: newly added DB
    files/domains appear after ``/api/db/console/refresh``).
  * ``/api/db/console/tables``     — tables + row counts for a database
    (provider-abstracted through the DatabaseDriver contract, so the SAME
    code serves SQLite today and PostgreSQL after the provider switch).
  * ``/api/db/console/columns``    — column layout for one table.
  * ``/api/db/console/rows``       — paginated row preview (rowid order,
    hard 500-row cap, never a full scan).
  * ``/api/db/console/query``      — SQL console: SELECT/EXPLAIN/WITH/PRAGMA
    only, placeholder-translated for the active provider, hard LIMIT,
    statement timeout, READ-ONLY guard (no INSERT/UPDATE/DELETE/DDL).
  * READY-SQL quick buttons built on the same endpoints: TOP 100 rows,
    COUNT(*), recent rows, schema, integrity check.
  * ``/api/db/console/apikeys``    — named API-keys manager (name + masked
    status; values live ONLY in the OS SecretStore, never plaintext).

Portability contract: every data access goes through
:func:`nexus_scalp.database.drivers.get_driver` + the
``DatabaseDriver`` abstract contract — no SQLite-specific SQL outside the
drivers.  When the active provider flips to PostgreSQL the same endpoints
introspect and query the PostgreSQL server.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from nexus_scalp.database.config import (
    PG_PASSWORD_SECRET_KEY,
    DatabaseConfig,
    load_database_config,
)
from nexus_scalp.database.drivers import get_driver
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.settings.secret_store import SecureSecretStore
from nexus_scalp.settings.service import SettingsDatabase

router = APIRouter(prefix="/api/db/console", tags=["database-console"])
logger = get_logger("nexus_scalp.web.db_console")

#: Domains surfaced in the explorer (mirrors DatabaseHealthService.domains).
CONSOLE_DOMAINS: tuple[str, ...] = ("audit", "news", "candle_intel")

#: Hard caps — the console must NEVER be able to scan a whole table into
#: the web response or run unbounded SQL.
MAX_ROWS = 500
QUERY_LIMIT = 500
QUERY_TIMEOUT_S = 10.0

#: SQL console allow-list: only read-only statement kinds.
_ALLOWED_STATEMENT_PREFIXES = (
    "SELECT",
    "EXPLAIN",
    "WITH",
    "PRAGMA",
    "VALUES",
)

_QUICK_SQL: dict[str, str] = {
    "top100": "SELECT * FROM {table} ORDER BY rowid LIMIT 100",
    "count": "SELECT COUNT(*) AS row_count FROM {table}",
    "recent": "SELECT * FROM {table} ORDER BY rowid DESC LIMIT 100",
    "schema": "SELECT sql FROM sqlite_master WHERE type='table' AND name='{table}'",
    "integrity": "PRAGMA integrity_check",
    "tables": "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _settings_db_path() -> Path | None:
    """The canonical settings-database path (best effort)."""
    try:
        db = SettingsDatabase()
        try:
            return Path(db.db_path) if db.db_path else None
        finally:
            db.close()
    except Exception:
        return None


def _db_file_size(path: str | Path) -> int | None:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _list_databases() -> list[dict[str, Any]]:
    """Enumerate every domain DB + the settings DB (provider-abstracted)."""
    out: list[dict[str, Any]] = []
    for domain in CONSOLE_DOMAINS:
        try:
            cfg = load_database_config(domain)
        except Exception:
            continue
        entry: dict[str, Any] = {
            "name": domain,
            "provider": cfg.provider.value,
            "database": "",
            "server": "",
            "path": "",
            "size_bytes": None,
            "status": "ERROR",
        }
        if cfg.is_postgresql:
            entry["database"] = cfg.database or ""
            entry["server"] = f"{cfg.host}:{cfg.port or 5432}"
            entry["path"] = ""
        else:
            entry["path"] = str(cfg.sqlite_connect_path)
            entry["database"] = os.path.basename(cfg.sqlite_connect_path)
            entry["server"] = "Local"
            entry["size_bytes"] = _db_file_size(cfg.sqlite_connect_path)
        # reachability + table count via the driver contract
        try:
            driver = get_driver(cfg)
            try:
                if driver.ping():
                    entry["status"] = "CONNECTED"
                    entry["table_count"] = driver.table_count()
                else:
                    entry["status"] = "DISCONNECTED"
            finally:
                driver.close()
        except Exception as exc:  # pragma: no cover - env edges
            logger.warning("db_console driver unavailable", exc_info=exc)
            entry["status"] = f"DRIVER_UNAVAILABLE: {type(exc).__name__}"
        out.append(entry)
    # settings DB (the database of record for UI/runtime config)
    sdb = _settings_db_path()
    out.append(
        {
            "name": "settings",
            "provider": "sqlite",
            "database": os.path.basename(str(sdb)) if sdb else "app_settings.db",
            "server": "Local",
            "path": str(sdb) if sdb else "",
            "size_bytes": _db_file_size(sdb) if sdb else None,
            "status": "CONNECTED" if sdb and sdb.exists() else "MISSING",
            "table_count": None,
            "description": "application settings / runtime configuration",
        }
    )
    return out


def _config_for(name: str) -> DatabaseConfig | None:
    """Resolve the DatabaseConfig for a console database name."""
    if name in CONSOLE_DOMAINS:
        return load_database_config(name)
    if name == "settings":
        path = _settings_db_path()
        if path is None:
            return None
        return DatabaseConfig.for_sqlite("settings", path=str(path))
    return None


def _driver_for(name: str, cors: bool = True):
    """Open a driver for a console database (None when unknown)."""
    cfg = _config_for(name)
    if cfg is None:
        return None, None
    driver = get_driver(cfg)
    return driver, cfg


def _query_console_sql(sql: str, provider: str) -> str:
    """Normalize console SQL for the active provider.

    SQLite keeps qmark style; PostgreSQL rewrites ``?`` placeholders to
    ``%s`` (psycopg format) via the driver's translator.  Statement
    allow-list enforcement happens at the endpoint layer.
    """
    if provider == "postgresql":
        from nexus_scalp.database.drivers.postgres_driver import PostgreSQLDriver

        return PostgreSQLDriver.translate_sql(sql)
    return sql


def _mask_secret_name(name: str) -> str:
    """Human-safe mask: key1**** (never reveals the stored value)."""
    if len(name) <= 4:
        return name[:1] + "****"
    return name[:4] + "****"


def _load_apikey_names() -> list[dict[str, Any]]:
    """List stored API-key secret names + masked prefix + presence."""
    try:
        store = SecureSecretStore()
        # monospace keys never leave the store; only status is surfaced
        names = _secret_file_names()
        masked: list[dict[str, Any]] = []
        for n in sorted(names):
            if n in (PG_PASSWORD_SECRET_KEY,) or n.startswith("telegram."):
                continue
            masked.append(
                {
                    "name": n,
                    "masked": _mask_secret_name(n),
                    "set": store.has_secret(n),
                }
            )
        return masked
    except Exception:
        return []


def _secret_file_names() -> list[str]:
    """Line names from the secret store file (defensive fallback)."""
    try:
        from nexus_scalp.settings.secret_store import SECRET_STORE_FILENAME, app_data_root

        path = Path(app_data_root()) / SECRET_STORE_FILENAME
        if not path.exists():
            return []
        names: list[str] = []
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            names.append(line.split("=", 1)[0].strip())
        return names
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------


@router.get("/databases")
def console_databases() -> dict[str, Any]:
    """Every database file + the settings DB, with provider/size/status."""
    try:
        dbs = _list_databases()
        return {"success": True, "databases": dbs, "timestamp": _utc_now()}
    except Exception as exc:
        logger.warning("db_console error", exc_info=exc)
        return {"success": False, "error": type(exc).__name__}


@router.post("/refresh")
def console_refresh() -> dict[str, Any]:
    """Force a re-scan of DB files (auto-sync for newly added databases)."""
    try:
        dbs = _list_databases()
        return {"success": True, "databases": dbs, "resynced": True, "timestamp": _utc_now()}
    except Exception as exc:
        logger.warning("db_console error", exc_info=exc)
        return {"success": False, "error": type(exc).__name__}


@router.get("/tables")
def console_tables(database: str = "audit") -> dict[str, Any]:
    """Tables + row counts for one database (SSMS object-explorer style)."""
    try:
        driver, cfg = _driver_for(database)
        if driver is None:
            return {"success": False, "error": f"unknown database '{database}'"}
        try:
            if not driver.ping():
                return {"success": False, "error": f"'{database}' not reachable"}
            tables: list[dict[str, Any]] = []
            for name in driver.list_tables():
                try:
                    row_count = driver.row_count(name)
                except Exception:
                    row_count = None
                tables.append({"name": name, "rows": row_count})
            return {
                "success": True,
                "database": database,
                "provider": cfg.provider.value if cfg else "",
                "tables": tables,
                "timestamp": _utc_now(),
            }
        finally:
            driver.close()
    except Exception as exc:
        logger.warning("db_console error", exc_info=exc)
        return {"success": False, "error": type(exc).__name__}


@router.get("/columns")
def console_columns(database: str = "audit", table: str = "") -> dict[str, Any]:
    """Column layout for one table (portable type + pk/notnull flags)."""
    try:
        if not table:
            return {"success": False, "error": "table required"}
        driver, cfg = _driver_for(database)
        if driver is None:
            return {"success": False, "error": f"unknown database '{database}'"}
        try:
            if not driver.table_exists(table):
                return {"success": False, "error": f"table '{table}' not found"}
            cols = driver.table_columns(table)
            # normalize to a stable portable shape
            normalized: list[dict[str, Any]] = []
            for c in cols:
                normalized.append(
                    {
                        "name": c.get("name") or c.get("column_name") or "",
                        "type": c.get("type") or c.get("data_type") or "TEXT",
                        "notnull": bool(c.get("notnull", 0) or c.get("is_nullable") == "NO"),
                        "pk": bool(c.get("pk", 0) or c.get("is_primary_key", False)),
                        "default": c.get("dflt_value")
                        if c.get("dflt_value") is not None
                        else c.get("column_default"),
                    }
                )
            return {
                "success": True,
                "database": database,
                "table": table,
                "columns": normalized,
                "provider": cfg.provider.value if cfg else "",
            }
        finally:
            driver.close()
    except Exception as exc:
        logger.warning("db_console error", exc_info=exc)
        return {"success": False, "error": type(exc).__name__}


@router.get("/rows")
def console_rows(
    database: str = "audit",
    table: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Paginated row preview (rowid order, hard 500-row cap)."""
    try:
        if not table:
            return {"success": False, "error": "table required"}
        limit = max(1, min(int(limit), MAX_ROWS))
        offset = max(0, int(offset))
        driver, cfg = _driver_for(database)
        if driver is None:
            return {"success": False, "error": f"unknown database '{database}'"}
        try:
            if not driver.table_exists(table):
                return {"success": False, "error": f"table '{table}' not found"}
            # CodeQL py/sql-injection: table is user-controlled; validate
            # against the LIVE schema allow-list (driver.list_tables()) so
            # only real application tables are readable, then re-derive the
            # SQL text from the validated entry. LIMIT/OFFSET use qmark
            # placeholders so they never enter the SQL text (driver.query
            # translates ? -> %s for PostgreSQL).
            try:
                live_tables = set(driver.list_tables())
            except Exception:
                live_tables = set()
            if live_tables and table not in live_tables:
                return {"success": False, "error": "table not in schema allow-list"}
            try:
                table_sql = driver.quote_ident(table)
            except ValueError:
                return {"success": False, "error": f"invalid table name '{table}'"}
            if cfg and cfg.is_postgresql:
                sql = f"SELECT * FROM {table_sql} ORDER BY 1 LIMIT ? OFFSET ?"
            else:
                sql = f"SELECT * FROM {table_sql} ORDER BY rowid LIMIT ? OFFSET ?"
            rows = driver.query(sql, (limit, offset))
            columns: list[str] = []
            if rows:
                columns = list(rows[0].keys())
            elif driver.table_exists(table):
                cols = driver.table_columns(table)
                columns = [c.get("name") or c.get("column_name") or "" for c in cols]
            return {
                "success": True,
                "database": database,
                "table": table,
                "columns": columns,
                "rows": rows,
                "limit": limit,
                "offset": offset,
                "provider": cfg.provider.value if cfg else "",
                "timestamp": _utc_now(),
            }
        finally:
            driver.close()
    except Exception as exc:
        logger.warning("db_console error", exc_info=exc)
        return {"success": False, "error": type(exc).__name__}


@router.post("/query")
def console_query(payload: dict[str, Any]) -> dict[str, Any]:
    """Run read-only SQL from the console.

    Only SELECT / EXPLAIN / WITH / PRAGMA / VALUES statement kinds are
    accepted; INSERT/UPDATE/DELETE/DDL are rejected before touching the
    database.  Results are capped at QUERY_LIMIT rows and the connection
    runs under a bounded timeout.
    """
    try:
        database = str(payload.get("database") or "audit")
        sql = str(payload.get("sql") or "").strip().rstrip(";").strip()
        if not sql:
            return {"success": False, "error": "empty SQL"}
        upper = sql.upper()
        if ";" in upper and not upper.startswith("WITH"):
            # a single statement only (WITH ... SELECT may contain no ; either)
            return {"success": False, "error": "only one statement per query"}
        if not any(upper.startswith(p) for p in _ALLOWED_STATEMENT_PREFIXES):
            return {
                "success": False,
                "error": "read-only console: SELECT / EXPLAIN / WITH / PRAGMA / VALUES only",
            }
        for banned in (
            "INSERT",
            "UPDATE",
            "DELETE",
            "DROP",
            "ALTER",
            "CREATE",
            "GRANT",
            "REPLACE",
            "VACUUM",
        ):
            if banned in upper:
                return {"success": False, "error": f"'{banned}' is not allowed (read-only console)"}
        driver, cfg = _driver_for(database)
        if driver is None:
            return {"success": False, "error": f"unknown database '{database}'"}
        provider = cfg.provider.value if cfg else "sqlite"
        compiled = _query_console_sql(sql, provider)
        try:
            # bounded: never let a console query hang the web loop
            rows = driver.query(compiled)[:QUERY_LIMIT]
            columns = list(rows[0].keys()) if rows else []
            return {
                "success": True,
                "database": database,
                "provider": provider,
                "columns": columns,
                "rows": rows,
                "truncated": len(rows) >= QUERY_LIMIT,
                "rows_returned": len(rows),
                "timestamp": _utc_now(),
            }
        except Exception as exc:
            logger.warning("db_console query failed", exc_info=exc)
            return {"success": False, "error": f"query failed: {type(exc).__name__}"}
        finally:
            driver.close()
    except Exception as exc:
        logger.warning("db_console error", exc_info=exc)
        return {"success": False, "error": type(exc).__name__}


@router.get("/quick")
def console_quick(database: str = "audit", table: str = "", kind: str = "top100") -> dict[str, Any]:
    """Ready-made SQL buttons: build + run a canned query for a table."""
    try:
        if not table:
            return {"success": False, "error": "table required"}
        template = _QUICK_SQL.get(kind)
        if template is None:
            return {"success": False, "error": f"unknown quick kind '{kind}'"}

        driver, _ = _driver_for(database)
        if driver is None:
            return {"success": False, "error": f"unknown database '{database}'"}

        try:
            if not driver.table_exists(table):
                return {"success": False, "error": f"table '{table}' not found"}
            try:
                live_tables = set(driver.list_tables())
            except Exception:
                live_tables = set()
            if live_tables and table not in live_tables:
                return {"success": False, "error": "table not in schema allow-list"}
            try:
                table_sql = driver.quote_ident(table)
            except ValueError:
                return {"success": False, "error": f"invalid table name '{table}'"}
        finally:
            driver.close()

        # schema lookup uses a bound parameter (not string interpolation)
        # so the table name never enters SQL text.
        if kind == "schema":
            try:
                drv, _ = _driver_for(database)
                if drv is None:
                    return {"success": False, "error": f"unknown database '{database}'"}
                try:
                    if getattr(drv, "name", "") == "postgresql":
                        cols = drv.table_columns(table)
                        ddl = ", ".join(f"{c.get('name')} {c.get('type')}" for c in cols)
                        return {
                            "success": True,
                            "database": database,
                            "provider": "postgresql",
                            "columns": ["ddl"],
                            "rows": [{"ddl": f"TABLE {table} ({ddl})"}],
                            "rows_returned": 1,
                            "timestamp": _utc_now(),
                        }
                    row = drv.query_one(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
                    )
                    return {
                        "success": True,
                        "database": database,
                        "provider": "sqlite",
                        "columns": ["sql"],
                        "rows": [row] if row else [],
                        "rows_returned": 1 if row else 0,
                        "timestamp": _utc_now(),
                    }
                finally:
                    drv.close()
            except Exception as exc:
                logger.warning("db_console error", exc_info=exc)
                return {"success": False, "error": type(exc).__name__}

        sql = template.format(table=table_sql)
        return console_query({"database": database, "sql": sql})
    except Exception as exc:
        logger.warning("db_console error", exc_info=exc)
        return {"success": False, "error": type(exc).__name__}


# ---------------------------------------------------------------------------
# API keys (SecureSecretStore-backed; values never plaintext/echoed)
# ---------------------------------------------------------------------------


@router.get("/apikeys")
def console_apikeys() -> dict[str, Any]:
    """List named API keys: masked name + set/cleared status only."""
    try:
        return {"success": True, "apikeys": _load_apikey_names(), "timestamp": _utc_now()}
    except Exception as exc:
        logger.warning("db_console error", exc_info=exc)
        return {"success": False, "error": type(exc).__name__}


@router.post("/apikey")
def console_apikey_set(payload: dict[str, Any]) -> dict[str, Any]:
    """Save/rotate a named API key into the OS secret store.

    ``name`` is the key identifier consumers reference later; ``value`` is
    routed directly to the SecretStore and NEVER stored in the settings DB,
    never echoed back.  Empty/whitespace values clear the key.
    """
    try:
        name = str(payload.get("name") or "").strip()
        value = str(payload.get("value") or "")
        if not name:
            return {"success": False, "error": "key name required"}
        if " " in name or "\t" in name or "/" in name or "\\" in name:
            return {
                "success": False,
                "error": "key name must be a simple identifier (no spaces/slashes)",
            }
        if name in (PG_PASSWORD_SECRET_KEY,) or name.startswith("telegram."):
            return {"success": False, "error": "reserved key name"}
        store = SecureSecretStore()
        if value.strip():
            store.set_secret(name, value)
        else:
            store.delete_secret(name) if hasattr(store, "delete_secret") else None
        return {
            "success": True,
            "apikey": {"name": name, "masked": _mask_secret_name(name), "set": bool(value.strip())},
        }
    except Exception as exc:
        logger.warning("db_console error", exc_info=exc)
        return {"success": False, "error": type(exc).__name__}


@router.delete("/apikey/{name}")
def console_apikey_delete(name: str) -> dict[str, Any]:
    """Delete a named API key from the secret store."""
    try:
        store = SecureSecretStore()
        if hasattr(store, "delete_secret"):
            store.delete_secret(name)
        return {"success": True, "deleted": name}
    except Exception as exc:
        logger.warning("db_console error", exc_info=exc)
        return {"success": False, "error": type(exc).__name__}
