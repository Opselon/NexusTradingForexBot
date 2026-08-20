"""Isolated Strategy Research Store — generated-strategy persistence.

STRATEGY RESEARCH STORE (2026-08-20)
====================================
Generated strategies (the LLM factory output) are research memory, NOT audit
truth.  They are persisted in a DEDICATED database so that:

  * the audit database stays small, fast and reserved for trade truth
    (orders, signals, ledger, executions);
  * factory churn (generations/candidates/failures/events/runs) can never
    grow unboundedly inside the live audit path;
  * the store is portable: the SAME code runs on SQLite (default,
    ``artifacts/strategies.db``) and PostgreSQL (production / large scale).

Portability contract (mirrors the DATABASE PORTABILITY mission):

  * business logic NEVER branches on provider; all statements go through the
    :class:`DatabaseDriver` abstraction (``nexus_scalp.database.drivers``);
  * placeholders come from ``driver.qmarks()``;
  * upserts go through ``driver.upsert()`` (INSERT OR REPLACE on SQLite vs
    ON CONFLICT ... DO UPDATE on PostgreSQL);
  * DDL is provider-portable: CREATE TABLE statements are written in the
    SQLite dialect and ported with ``port_create_table`` for PostgreSQL
    (BIGSERIAL identity, DOUBLE PRECISION, TIMESTAMPTZ, BYTEA, JSONB);
  * JSON payload columns stay TEXT/JSONB-neutral — values are serialized in
    code, never through dialect-specific JSON operators.

The store owns the schema of the seven factory tables (generations,
candidates, failures, events, runs, provider_usage, loop_state) plus a
small meta table tracking schema/portability version.  When the active
provider is PostgreSQL the table names are left as-is (the audit domain
already uses plain names on PG; a dedicated database is the isolation
boundary, not a prefix).
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

logger = get_logger("nexus_scalp.strategies.research_store")

#: Persistence domain name for the isolated strategy research database.
DOMAIN = "strategies"

#: Default SQLite file name under artifacts/.
DEFAULT_DB_FILENAME = "strategies.db"

#: Schema version recorded in strategy_research_meta (bump on DDL change).
SCHEMA_VERSION = 1

#: Tables owned by this store.
TABLES = (
    "factory_generations",
    "factory_candidates",
    "factory_failures",
    "factory_events",
    "factory_runs",
    "factory_provider_usage",
    "factory_loop_state",
    "strategy_research_meta",
)

# ---------------------------------------------------------------------------
# DDL (SQLite dialect; ported for PostgreSQL via port_create_table)
# ---------------------------------------------------------------------------

#: One row per generated population (spec 25).
DDL_FACTORY_GENERATIONS = """
CREATE TABLE IF NOT EXISTS factory_generations (
    generation_id TEXT PRIMARY KEY,
    number INTEGER NOT NULL,
    mode TEXT DEFAULT 'MANUAL',
    parent_generation TEXT DEFAULT '',
    population_target INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    completed_at TEXT DEFAULT NULL,
    status TEXT DEFAULT 'PENDING',
    config TEXT DEFAULT '{}'
);
"""

#: One row per generated candidate + structural verdict.
DDL_FACTORY_CANDIDATES = """
CREATE TABLE IF NOT EXISTS factory_candidates (
    candidate_id TEXT PRIMARY KEY,
    definition_hash TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    source TEXT DEFAULT 'TEMPLATE',
    operator TEXT DEFAULT 'NONE',
    parent_ids TEXT DEFAULT '[]',
    family TEXT DEFAULT 'HYBRID',
    population_index INTEGER DEFAULT 0,
    dsl TEXT DEFAULT '{}',
    structural TEXT DEFAULT '{}',
    lifecycle TEXT DEFAULT 'GENERATED',
    failure_reasons TEXT DEFAULT '[]',
    llm_response_id TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
"""

#: Structured rejection reasons per candidate.
DDL_FACTORY_FAILURES = """
CREATE TABLE IF NOT EXISTS factory_failures (
    failure_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    strategy_id TEXT DEFAULT '',
    generation_id TEXT DEFAULT '',
    stage TEXT DEFAULT 'DSL_VALIDATION',
    reason TEXT DEFAULT '',
    detail TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""

#: Immutable event stream for the UI.
DDL_FACTORY_EVENTS = """
CREATE TABLE IF NOT EXISTS factory_events (
    event_id TEXT PRIMARY KEY,
    generation_id TEXT DEFAULT '',
    candidate_id TEXT DEFAULT '',
    event_type TEXT NOT NULL,
    message TEXT DEFAULT '',
    payload TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""

#: Research-run ledger (reproducibility).
DDL_FACTORY_RUNS = """
CREATE TABLE IF NOT EXISTS factory_runs (
    run_id TEXT PRIMARY KEY,
    generation_id TEXT DEFAULT '',
    strategy_id TEXT DEFAULT '',
    experiment_kind TEXT DEFAULT 'GENERATE',
    executed_at TEXT NOT NULL,
    config TEXT DEFAULT '{}',
    result_summary TEXT DEFAULT '{}'
);
"""

#: LLM request/cost ledger.
DDL_FACTORY_PROVIDER_USAGE = """
CREATE TABLE IF NOT EXISTS factory_provider_usage (
    usage_id TEXT PRIMARY KEY,
    generation_id TEXT DEFAULT '',
    requests INTEGER DEFAULT 0,
    failures INTEGER DEFAULT 0,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    estimated_cost_usd REAL DEFAULT 0.0,
    last_latency_ms REAL DEFAULT 0.0,
    last_error TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
"""

#: Autonomous loop control-plane state.
DDL_FACTORY_LOOP_STATE = """
CREATE TABLE IF NOT EXISTS factory_loop_state (
    scope TEXT PRIMARY KEY,
    state TEXT DEFAULT 'STOPPED',
    reason TEXT DEFAULT '',
    last_cycle_at TEXT DEFAULT '',
    cycle_count INTEGER DEFAULT 0,
    checkpoint TEXT DEFAULT '{}',
    updated_at TEXT DEFAULT ''
);
"""

#: Store meta (schema/portability version).
DDL_META = """
CREATE TABLE IF NOT EXISTS strategy_research_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

ALL_DDL: tuple[tuple[str, str], ...] = (
    ("factory_generations", DDL_FACTORY_GENERATIONS),
    ("factory_candidates", DDL_FACTORY_CANDIDATES),
    ("factory_failures", DDL_FACTORY_FAILURES),
    ("factory_events", DDL_FACTORY_EVENTS),
    ("factory_runs", DDL_FACTORY_RUNS),
    ("factory_provider_usage", DDL_FACTORY_PROVIDER_USAGE),
    ("factory_loop_state", DDL_FACTORY_LOOP_STATE),
    ("strategy_research_meta", DDL_META),
)

#: Indexes kept provider-portable (plain CREATE INDEX).
INDEXES: tuple[tuple[str, str, str], ...] = (
    (
        "idx_factory_candidates_gen",
        "factory_candidates",
        "generation_id",
    ),
    (
        "idx_factory_candidates_lifecycle",
        "factory_candidates",
        "lifecycle",
    ),
    (
        "idx_factory_failures_candidate",
        "factory_failures",
        "candidate_id",
    ),
    (
        "idx_factory_events_generation",
        "factory_events",
        "generation_id",
    ),
    (
        "idx_factory_runs_strategy",
        "factory_runs",
        "strategy_id",
    ),
)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _json(value: Any) -> str:
    """Deterministic JSON-text encoding; None/null literals become '{}'."""
    if value is None:
        return "{}"
    try:
        encoded = json.dumps(value, default=str, sort_keys=True)
        return "{}" if encoded == "null" else encoded
    except Exception:
        return "{}"


def _json_parse(value: Any) -> Any:
    """Parse a JSON-text column; the historical ``null`` literal -> {}."""
    if value is None:
        return {}
    text = str(value).strip()
    if not text or text.lower() == "null":
        return {}
    try:
        return json.loads(text)
    except Exception:
        return {}


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def default_config(workspace: str | None = None) -> DatabaseConfig:
    """SQLite config for the isolated strategy database (default path)."""
    from nexus_scalp.database.provider import DEFAULT_DB_FILES

    path = default_sqlite_path(DOMAIN, workspace)
    # Register the domain so default_sqlite_path keeps resolving it.
    if DOMAIN not in DEFAULT_DB_FILES:
        DEFAULT_DB_FILES[DOMAIN] = DEFAULT_DB_FILENAME
    return DatabaseConfig.for_sqlite(DOMAIN, path=path)


def config_for(provider: DatabaseProvider, workspace: str | None = None) -> DatabaseConfig:
    """Build the store config for an explicit provider.

    SQLite -> ``artifacts/strategies.db``; PostgreSQL -> the default
    postgres URL for the ``strategies`` domain (same server as the audit
    domain, separate database name resolved by DatabaseConfig / secret
    store at connect time).
    """
    if provider.is_sqlite:
        return default_config(workspace)
    from nexus_scalp.database.provider import url_for_provider

    url = url_for_provider(provider, DOMAIN, workspace)
    cfg = DatabaseConfig(
        provider=provider,
        domain=DOMAIN,
    )
    # url_for_provider returns the placeholder form for PG; DatabaseConfig
    # resolves the real URL (secret store) at connect time.  We keep the
    # host/db fields explicit so the driver can build the URL.
    if url.startswith("postgresql://"):
        # url_for_provider advertises the default values explicitly; parsing
        # the placeholder string is brittle (the ":***@" password field
        # corrupts host parsing), so set the defaults directly and let
        # build_postgres_url inject the real password from the secret store.
        from nexus_scalp.database.config import DEFAULT_PG_PORT

        cfg.host = "localhost"
        cfg.port = DEFAULT_PG_PORT
        cfg.database = "nse_audit"
        cfg.username = "nse_user"
    return cfg


class StrategyResearchStore:
    """Portable persistence for generated strategies (SQLite + PostgreSQL).

    The store is the single write/read path for factory research memory.
    It opens connections through the provider driver; every statement uses
    driver placeholders and driver upsert semantics, so the same class runs
    unchanged on SQLite and PostgreSQL.

    Schema is created idempotently on first use (``ensure_schema()``) and
    recorded in ``strategy_research_meta``.
    """

    def __init__(self, config: DatabaseConfig | None = None) -> None:
        self.config = config or default_config()
        self.driver: DatabaseDriver = get_driver(self.config)
        self._schema_ready = False

    # -- lifecycle ---------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create tables + indexes idempotently; record schema version."""
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
            "strategy_research_meta",
            {"key": key, "value": value, "updated_at": _now()},
            conn=conn,
        )

    def meta(self, key: str) -> str | None:
        """Read one meta value (None when absent)."""
        row = self.driver.query_one(
            "SELECT value FROM strategy_research_meta WHERE key = ?",
            (key,),
        )
        return str(row["value"]) if row else None

    # -- writes ------------------------------------------------------------

    def _write(self, fn: Any) -> bool:
        """Run a write callable inside an explicit committed transaction.

        The drivers DO NOT auto-commit when they open their own connection
        (verified 2026-08-20: SQLite execute/upsert without an explicit
        connection rolls back silently).  Every write opens one connection,
        runs the statement, commits, and closes — portable across SQLite
        and PostgreSQL.
        """
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
            logger.error("[STRATEGY_STORE] write failed", error=str(e))
            return False
        finally:
            conn.close()

    def upsert_generation(self, generation: dict[str, Any]) -> bool:
        """Insert/update one factory generation row."""
        return self._write(
            lambda conn: self.driver.upsert(
                "factory_generations",
                {
                    "generation_id": str(generation.get("generation_id", "")),
                    "number": int(generation.get("number", 0)),
                    "mode": str(generation.get("mode", "MANUAL")),
                    "parent_generation": str(generation.get("parent_generation", "")),
                    "population_target": int(generation.get("population_target", 0)),
                    "created_at": str(generation.get("created_at", _now())),
                    "completed_at": str(generation.get("completed_at"))
                    if generation.get("completed_at")
                    else None,
                    "status": str(generation.get("status", "PENDING")),
                    "config": _json(generation.get("config")),
                },
                conn=conn,
            )
        )

    def upsert_candidate(self, candidate: dict[str, Any]) -> bool:
        """Insert/update one generated candidate row."""
        return self._write(
            lambda conn: self.driver.upsert(
                "factory_candidates",
                {
                    "candidate_id": str(candidate.get("candidate_id", "")),
                    "definition_hash": str(candidate.get("definition_hash", "")),
                    "generation_id": str(candidate.get("generation_id", "")),
                    "source": str(candidate.get("source", "TEMPLATE")),
                    "operator": str(candidate.get("operator", "NONE")),
                    "parent_ids": _json(candidate.get("parent_ids")),
                    "family": str(candidate.get("family", "HYBRID")),
                    "population_index": int(candidate.get("population_index", 0)),
                    "dsl": _json(candidate.get("dsl")),
                    "structural": _json(candidate.get("structural")),
                    "lifecycle": str(candidate.get("lifecycle", "GENERATED")),
                    "failure_reasons": _json(candidate.get("failure_reasons")),
                    "llm_response_id": str(candidate.get("llm_response_id", "")),
                    "created_at": str(candidate.get("created_at", _now())),
                },
                conn=conn,
            )
        )

    def record_failure(self, failure: dict[str, Any]) -> bool:
        """Insert one factory failure row (idempotent on failure_id)."""
        return self._write(
            lambda conn: self.driver.upsert(
                "factory_failures",
                {
                    "failure_id": str(failure.get("failure_id", "")),
                    "candidate_id": str(failure.get("candidate_id", "")),
                    "strategy_id": str(failure.get("strategy_id", "")),
                    "generation_id": str(failure.get("generation_id", "")),
                    "stage": str(failure.get("stage", "DSL_VALIDATION")),
                    "reason": str(failure.get("reason", "")),
                    "detail": _json(failure.get("detail")),
                    "created_at": str(failure.get("created_at", _now())),
                },
                conn=conn,
            )
        )

    def emit_event(self, event: dict[str, Any]) -> bool:
        """Insert one factory event (immutable stream, idempotent)."""
        return self._write(
            lambda conn: self.driver.upsert(
                "factory_events",
                {
                    "event_id": str(event.get("event_id", "")),
                    "generation_id": str(event.get("generation_id", "")),
                    "candidate_id": str(event.get("candidate_id", "")),
                    "event_type": str(event.get("event_type", "GENERIC")),
                    "message": str(event.get("message", "")),
                    "payload": _json(event.get("payload")),
                    "created_at": str(event.get("created_at", _now())),
                },
                conn=conn,
            )
        )

    def record_run(self, run: dict[str, Any]) -> bool:
        """Insert one research-run ledger row (idempotent on run_id)."""
        return self._write(
            lambda conn: self.driver.upsert(
                "factory_runs",
                {
                    "run_id": str(run.get("run_id", "")),
                    "generation_id": str(run.get("generation_id", "")),
                    "strategy_id": str(run.get("strategy_id", "")),
                    "experiment_kind": str(run.get("experiment_kind", "GENERATE")),
                    "executed_at": str(run.get("executed_at", _now())),
                    "config": _json(run.get("config")),
                    "result_summary": _json(run.get("result_summary")),
                },
                conn=conn,
            )
        )

    def record_provider_usage(self, usage: dict[str, Any]) -> bool:
        """Insert one LLM provider usage/cost row."""
        return self._write(
            lambda conn: self.driver.upsert(
                "factory_provider_usage",
                {
                    "usage_id": str(usage.get("usage_id", "")),
                    "generation_id": str(usage.get("generation_id", "")),
                    "requests": int(usage.get("requests", 0)),
                    "failures": int(usage.get("failures", 0)),
                    "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                    "completion_tokens": int(usage.get("completion_tokens", 0)),
                    "total_tokens": int(usage.get("total_tokens", 0)),
                    "estimated_cost_usd": float(usage.get("estimated_cost_usd", 0.0)),
                    "last_latency_ms": float(usage.get("last_latency_ms", 0.0)),
                    "last_error": str(usage.get("last_error", "")),
                    "created_at": str(usage.get("created_at", _now())),
                },
                conn=conn,
            )
        )

    def set_loop_state(self, loop: dict[str, Any]) -> bool:
        """Upsert the autonomous loop control-plane state."""
        return self._write(
            lambda conn: self.driver.upsert(
                "factory_loop_state",
                {
                    "scope": str(loop.get("scope", "default")),
                    "state": str(loop.get("state", "STOPPED")),
                    "reason": str(loop.get("reason", "")),
                    "last_cycle_at": str(loop.get("last_cycle_at", "")),
                    "cycle_count": int(loop.get("cycle_count", 0)),
                    "checkpoint": _json(loop.get("checkpoint")),
                    "updated_at": str(loop.get("updated_at", _now())),
                },
                conn=conn,
            )
        )

    # -- reads -------------------------------------------------------------

    def get_generation(self, generation_id: str) -> dict[str, Any] | None:
        row = self.driver.query_one(
            "SELECT * FROM factory_generations WHERE generation_id = ?",
            (generation_id,),
        )
        return _row_safe(dict(row)) if row else None

    def list_generations(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.driver.query(
            "SELECT * FROM factory_generations ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 2000)),),
        )
        return [_row_safe(dict(r)) for r in rows]

    def list_candidates(
        self,
        generation_id: str | None = None,
        lifecycle: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM factory_candidates"
        clauses: list[str] = []
        args: list[Any] = []
        if generation_id:
            clauses.append("generation_id = ?")
            args.append(generation_id)
        if lifecycle:
            clauses.append("lifecycle = ?")
            args.append(lifecycle)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(int(limit), 2000)))
        rows = self.driver.query(sql, tuple(args))
        return [_row_safe(dict(r)) for r in rows]

    def list_failures(
        self, candidate_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM factory_failures"
        args: list[Any] = []
        if candidate_id:
            sql += " WHERE candidate_id = ?"
            args.append(candidate_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(int(limit), 2000)))
        rows = self.driver.query(sql, tuple(args))
        return [_row_safe(dict(r)) for r in rows]

    def list_events(
        self, generation_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM factory_events"
        args: list[Any] = []
        if generation_id:
            sql += " WHERE generation_id = ?"
            args.append(generation_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(int(limit), 2000)))
        rows = self.driver.query(sql, tuple(args))
        return [_row_safe(dict(r)) for r in rows]

    def list_runs(self, strategy_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM factory_runs"
        args: list[Any] = []
        if strategy_id:
            sql += " WHERE strategy_id = ?"
            args.append(strategy_id)
        sql += " ORDER BY executed_at DESC LIMIT ?"
        args.append(max(1, min(int(limit), 2000)))
        rows = self.driver.query(sql, tuple(args))
        return [_row_safe(dict(r)) for r in rows]

    def get_candidate_structural(self, candidate_id: str) -> dict[str, Any]:
        row = self.driver.query_one(
            "SELECT structural FROM factory_candidates WHERE candidate_id = ?",
            (candidate_id,),
        )
        if not row:
            return {}
        return _json_parse(row.get("structural"))

    def get_loop_state(self, scope: str = "default") -> dict[str, Any]:
        row = self.driver.query_one(
            "SELECT * FROM factory_loop_state WHERE scope = ?",
            (scope,),
        )
        if not row:
            return {"scope": scope, "state": "STOPPED"}
        return _row_safe(dict(row))

    def provider_usage_total(self) -> dict[str, Any]:
        row = self.driver.query_one(
            "SELECT COALESCE(SUM(requests), 0) AS requests, "
            "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
            "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
            "COALESCE(SUM(total_tokens), 0) AS total_tokens, "
            "COALESCE(SUM(estimated_cost_usd), 0.0) AS estimated_cost_usd "
            "FROM factory_provider_usage"
        )
        if not row:
            return {
                "requests": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
            }
        return {
            "requests": int(row.get("requests") or 0),
            "prompt_tokens": int(row.get("prompt_tokens") or 0),
            "completion_tokens": int(row.get("completion_tokens") or 0),
            "total_tokens": int(row.get("total_tokens") or 0),
            "estimated_cost_usd": float(row.get("estimated_cost_usd") or 0.0),
        }

    def count_rows(self, table: str) -> int:
        """Row count for a store-owned table (0 when the table is absent)."""
        if table not in TABLES:
            raise ValueError(f"not a store table: {table}")
        try:
            return int(self.driver.scalar(f"SELECT COUNT(*) FROM {table}") or 0)
        except Exception:
            return 0

    def close(self) -> None:
        """Release driver resources (shared connections)."""
        try:
            self.driver.close()
        except Exception:
            pass


def _row_safe(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize JSON-text columns for reads (null literal -> '{}')."""
    out = dict(row)
    for col in (
        "config",
        "parent_ids",
        "dsl",
        "structural",
        "failure_reasons",
        "detail",
        "payload",
        "checkpoint",
        "result_summary",
    ):
        if col in out:
            out[col] = _json_parse(out[col])
    return out


# ---------------------------------------------------------------------------
# Convenience module-level functions (mirror the legacy store API so callers
# can switch without changing call sites).
# ---------------------------------------------------------------------------


def open_store(config: DatabaseConfig | None = None) -> StrategyResearchStore:
    """Open (and schema-ensure) a store; returns the ready instance."""
    store = StrategyResearchStore(config)
    store.ensure_schema()
    return store


__all__ = [
    "ALL_DDL",
    "DEFAULT_DB_FILENAME",
    "DOMAIN",
    "SCHEMA_VERSION",
    "TABLES",
    "StrategyResearchStore",
    "config_for",
    "default_config",
    "open_store",
]
