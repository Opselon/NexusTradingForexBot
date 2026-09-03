"""
Marketplace isolated store — artifacts/marketplace.db (CHG-0056, ARCH_SPEC §2).

Follows the canonical isolated-store recipe from
src/nexus_scalp/strategies/research_store.py:

  DOMAIN = \"marketplace\"
  DEFAULT_DB_FILENAME = \"marketplace.db\"
  registration via DEFAULT_DB_FILES dict + DatabaseConfig.for_sqlite
  + DatabaseDriver abstraction (qmarks/upsert/port_create_table)

Tables (versioned via strategy_research_meta-style row):
  mk_packages           installed pack ledger (idempotent by (pack_id, version))
  mk_seeds              one row per seed (UNIQUE (seed_id, version))
  mk_lifecycle_events   append-only lifecycle transition events
  mk_enablement         per-seed-per-mode enablement state (upsert)
  mk_score_snapshots    append-only 14-factor evaluation snapshots
  mk_repairs            repair attempts (one row per repair_id)
  mk_runtime_snapshots  append-only enabled-set snapshots (immutable history)

No destructive deletes: RETIRED is a lifecycle event, not a row deletion.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.ddl_port import port_create_table
from nexus_scalp.database.drivers import get_driver
from nexus_scalp.database.drivers.base import DatabaseDriver
from nexus_scalp.database.provider import DatabaseProvider, default_sqlite_path
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.marketplace.store")

DOMAIN = "marketplace"
DEFAULT_DB_FILENAME = "marketplace.db"
SCHEMA_VERSION = 1

TABLES = (
    "mk_packages",
    "mk_seeds",
    "mk_lifecycle_events",
    "mk_enablement",
    "mk_score_snapshots",
    "mk_repairs",
    "mk_runtime_snapshots",
    "mk_meta",
)

# ---------------------------------------------------------------------------
# DDL (SQLite dialect; ported for PostgreSQL via port_create_table)
# ---------------------------------------------------------------------------

DDL_MK_PACKAGES = """
CREATE TABLE IF NOT EXISTS mk_packages (
    pack_id TEXT PRIMARY KEY,
    version TEXT NOT NULL DEFAULT '1.0.0',
    name TEXT NOT NULL DEFAULT '',
    family TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    seed_count INTEGER NOT NULL DEFAULT 0,
    installed_at TEXT NOT NULL DEFAULT ''
);
"""

DDL_MK_SEEDS = """
CREATE TABLE IF NOT EXISTS mk_seeds (
    seed_id TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '1.0.0',
    pack_id TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    family TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'marketplace_pack',
    license TEXT NOT NULL DEFAULT 'proprietary',
    instrument_scope TEXT NOT NULL DEFAULT '[]',
    timeframe_scope TEXT NOT NULL DEFAULT '[]',
    required_features TEXT NOT NULL DEFAULT '[]',
    parameter_schema TEXT NOT NULL DEFAULT '{}',
    default_parameters TEXT NOT NULL DEFAULT '{}',
    risk_profile TEXT NOT NULL DEFAULT 'MODERATE',
    expected_market_regimes TEXT NOT NULL DEFAULT '[]',
    unsupported_market_regimes TEXT NOT NULL DEFAULT '[]',
    compatibility_contract TEXT NOT NULL DEFAULT '{}',
    dsl TEXT NOT NULL DEFAULT '{}',
    lifecycle TEXT NOT NULL DEFAULT 'INSTALLED',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (seed_id, version)
);
"""

DDL_MK_LIFECYCLE_EVENTS = """
CREATE TABLE IF NOT EXISTS mk_lifecycle_events (
    event_id TEXT PRIMARY KEY,
    seed_id TEXT NOT NULL DEFAULT '',
    from_lifecycle TEXT NOT NULL DEFAULT '',
    to_lifecycle TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT 'system',
    created_at TEXT NOT NULL DEFAULT ''
);
"""

DDL_MK_ENABLEMENT = """
CREATE TABLE IF NOT EXISTS mk_enablement (
    seed_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    reason TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT 'operator',
    updated_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (seed_id, mode)
);
"""

DDL_MK_SCORE_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS mk_score_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    seed_id TEXT NOT NULL DEFAULT '',
    profile_id TEXT NOT NULL DEFAULT 'default',
    profile_version INTEGER NOT NULL DEFAULT 1,
    total REAL NOT NULL DEFAULT 0.0,
    verdict TEXT NOT NULL DEFAULT 'INCONCLUSIVE',
    factors TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT ''
);
"""

DDL_MK_REPAIRS = """
CREATE TABLE IF NOT EXISTS mk_repairs (
    repair_id TEXT PRIMARY KEY,
    seed_id TEXT NOT NULL DEFAULT '',
    parent_seed_id TEXT NOT NULL DEFAULT '',
    trigger TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'PENDING',
    outcome TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT ''
);
"""

DDL_MK_RUNTIME_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS mk_runtime_snapshots (
    version INTEGER PRIMARY KEY,
    enabled_set TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT ''
);
"""

DDL_MK_META = """
CREATE TABLE IF NOT EXISTS mk_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

ALL_DDL: tuple[tuple[str, str], ...] = (
    ("mk_packages", DDL_MK_PACKAGES),
    ("mk_seeds", DDL_MK_SEEDS),
    ("mk_lifecycle_events", DDL_MK_LIFECYCLE_EVENTS),
    ("mk_enablement", DDL_MK_ENABLEMENT),
    ("mk_score_snapshots", DDL_MK_SCORE_SNAPSHOTS),
    ("mk_repairs", DDL_MK_REPAIRS),
    ("mk_runtime_snapshots", DDL_MK_RUNTIME_SNAPSHOTS),
    ("mk_meta", DDL_MK_META),
)

INDEXES: tuple[tuple[str, str, str], ...] = (
    ("idx_mk_seeds_family", "mk_seeds", "family"),
    ("idx_mk_seeds_lifecycle", "mk_seeds", "lifecycle"),
    ("idx_mk_lifecycle_events_seed", "mk_lifecycle_events", "seed_id"),
    ("idx_mk_enablement_seed", "mk_enablement", "seed_id"),
    ("idx_mk_score_snapshots_seed", "mk_score_snapshots", "seed_id"),
    ("idx_mk_repairs_seed", "mk_repairs", "seed_id"),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json(value: Any) -> str:
    if value is None:
        return "{}"
    try:
        encoded = json.dumps(value, default=str, sort_keys=True)
        return "{}" if encoded == "null" else encoded
    except Exception:
        return "{}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def default_config(workspace: str | None = None) -> DatabaseConfig:
    """SQLite config for the isolated marketplace database (artifacts/marketplace.db)."""
    from nexus_scalp.database.provider import DEFAULT_DB_FILES

    path = default_sqlite_path(DOMAIN, workspace)
    if DOMAIN not in DEFAULT_DB_FILES:
        DEFAULT_DB_FILES[DOMAIN] = DEFAULT_DB_FILENAME
    return DatabaseConfig.for_sqlite(DOMAIN, path=path)


def config_for(provider: DatabaseProvider, workspace: str | None = None) -> DatabaseConfig:
    if provider.is_sqlite:
        return default_config(workspace)
    from nexus_scalp.database.provider import url_for_provider

    url = url_for_provider(provider, DOMAIN, workspace)
    cfg = DatabaseConfig(provider=provider, domain=DOMAIN)
    if url.startswith("postgresql://"):
        from nexus_scalp.database.config import DEFAULT_PG_PORT

        cfg.host = "localhost"
        cfg.port = DEFAULT_PG_PORT
        cfg.database = "nse_audit"
        cfg.username = "nse_user"
    return cfg


class MarketplaceStore:
    """Portable persistence for the marketplace domain (SQLite + PostgreSQL)."""

    def __init__(self, config: DatabaseConfig | None = None) -> None:
        self.config = config or default_config()
        self.driver: DatabaseDriver = get_driver(self.config)
        self._schema_ready = False

    # -- lifecycle -----------------------------------------------------------

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        self.driver.ensure_directory()
        conn = self.driver.connect()
        try:
            for table, ddl in ALL_DDL:
                self._create_table(conn, table, ddl)
            for idx_name, table, cols in INDEXES:
                self.driver.execute(
                    f'CREATE INDEX IF NOT EXISTS "{idx_name}" ON {table} ({cols})',
                    conn=conn,
                )
            self._set_meta(conn, "schema_version", str(SCHEMA_VERSION))
            self._set_meta(conn, "provider", self.config.provider.value)
            self.driver.commit(conn)
            self._schema_ready = True
        finally:
            conn.close()

    def _create_table(self, conn: Any, table: str, ddl: str) -> None:
        if self.config.is_postgresql:
            ported = port_create_table(ddl)
            if ported:
                self.driver.execute(ported, conn=conn)
                return
        self.driver.execute(ddl, conn=conn)

    def _set_meta(self, conn: Any, key: str, value: str) -> None:
        self.driver.upsert(
            "mk_meta",
            {"key": key, "value": value, "updated_at": _now()},
            conn=conn,
        )

    def meta(self, key: str) -> str | None:
        row = self.driver.query_one(
            "SELECT value FROM mk_meta WHERE key = ?",
            (key,),
        )
        return str(row["value"]) if row else None

    # -- transactional helper ------------------------------------------------

    def _write(self, fn: Any) -> bool:
        conn = self.driver.connect()
        try:
            fn(conn)
            self.driver.commit(conn)
            return True
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            err_msg = str(e).replace("\r", " ").replace("\n", " ")[:400]
            logger.error("[MARKETPLACE_STORE] write failed", error=err_msg)
            return False
        finally:
            conn.close()

    # -- direct SQL (read-only helpers) --------------------------------------

    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        return self.driver.query_one(sql, params)

    def query_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        return self.driver.query(sql, params)

    def list_packages(self) -> list[Any]:
        return self.driver.query("SELECT * FROM mk_packages ORDER BY pack_id", ())

    # -- seeds ---------------------------------------------------------------

    def get_seed(self, seed_id: str, version: str = "") -> Any | None:
        if version:
            return self.driver.query_one(
                "SELECT * FROM mk_seeds WHERE seed_id = ? AND version = ?",
                (seed_id, version),
            )
        return self.driver.query_one(
            "SELECT * FROM mk_seeds WHERE seed_id = ? ORDER BY created_at DESC LIMIT 1",
            (seed_id,),
        )

    def latest_lifecycle_for_seed(self, seed_id: str) -> str | None:
        row = self.driver.query_one(
            "SELECT lifecycle FROM mk_seeds WHERE seed_id = ? LIMIT 1",
            (seed_id,),
        )
        return str(row["lifecycle"]) if row else None

    # no destructive deletes: RETIRED is a lifecycle event (ARCH_SPEC §2).


__all__ = [
    "DEFAULT_DB_FILENAME",
    "DOMAIN",
    "SCHEMA_VERSION",
    "MarketplaceStore",
    "config_for",
    "default_config",
]
