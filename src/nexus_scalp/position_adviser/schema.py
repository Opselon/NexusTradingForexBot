"""Schema for the position_adviser package's operational tables.

TASK-POSA-001 persistence layer. Four tables live in the ``audit`` domain's
database (the same database ``model_lifecycle``'s training_runs /
model_comparisons live in):

    pa_training_runs   one immutable row per adviser training execution
    pa_model_registry  the registry of trained adviser checkpoints + their
                       lifecycle state (TRAINED/VALID/LOADED/ACTIVE/RETIRED/FAILED)
    pa_load_events     the LOAD/UNLOAD/ACTIVATE/DEACTIVATE/ROLLBACK event log
    pa_advisories      one row per runtime advisory the service emitted

Same ownership contract as ``model_lifecycle.schema`` / ``shadow.schema``: the
DDL is authored HERE, in the SQLite dialect, so the fabric's domain provisioning
and the SQLite bootstrap converge on the same physical schema
(``IF NOT EXISTS`` keeps re-provisioning non-destructive). The fabric's
translator renders it as Postgres-compatible DDL, so the statements stay
cross-provider: TEXT for timestamps and JSON payloads, BIGINT for counts,
DOUBLE PRECISION for floats.

Every table has a deterministic primary/unique key so the upsert is idempotent
(see ``nexus_scalp.database.upsert._UPSERT_KEYS``): re-queueing a training run
or replaying an advisory never duplicates a row.
"""

from __future__ import annotations

_PA_TRAINING_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS pa_training_runs (
    run_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL DEFAULT '',
    dataset_hash TEXT NOT NULL DEFAULT '',
    feature_schema_id TEXT NOT NULL DEFAULT 'adviser_v1',
    feature_dimension INTEGER NOT NULL DEFAULT 12,
    model_id TEXT NOT NULL DEFAULT '',
    hyperparameters TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT '',
    artifacts TEXT NOT NULL DEFAULT '[]',
    metrics TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'COMPLETED',
    failure_reason TEXT NOT NULL DEFAULT ''
);
"""

_PA_MODEL_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS pa_model_registry (
    model_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL DEFAULT '',
    weights_path TEXT NOT NULL DEFAULT '',
    scaler_path TEXT NOT NULL DEFAULT '',
    manifest_path TEXT NOT NULL DEFAULT '',
    weights_sha256 TEXT NOT NULL DEFAULT '',
    integrity TEXT NOT NULL DEFAULT 'UNVERIFIED',
    source_dataset_hash TEXT NOT NULL DEFAULT '',
    feature_dim INTEGER NOT NULL DEFAULT 12,
    oos_loss DOUBLE PRECISION,
    oos_accuracy DOUBLE PRECISION,
    lifecycle_state TEXT NOT NULL DEFAULT 'TRAINED',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
"""

_PA_LOAD_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS pa_load_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT 'LOAD',
    activation TEXT NOT NULL DEFAULT 'DISABLED',
    success INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'WEB_UI',
    operator TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);
"""

_PA_ADVISORIES_DDL = """
CREATE TABLE IF NOT EXISTS pa_advisories (
    advisory_id TEXT PRIMARY KEY,
    ticket BIGINT NOT NULL DEFAULT 0,
    action TEXT NOT NULL DEFAULT 'KEEP',
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    probabilities TEXT NOT NULL DEFAULT '{}',
    hold_score_adjustment DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    activation TEXT NOT NULL DEFAULT 'DISABLED',
    model_id TEXT NOT NULL DEFAULT '',
    model_dimension INTEGER NOT NULL DEFAULT 12,
    evaluated_at TEXT NOT NULL DEFAULT '',
    latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    applied INTEGER NOT NULL DEFAULT 0,
    not_applied_reason TEXT NOT NULL DEFAULT '',
    diagnostics TEXT NOT NULL DEFAULT '{}'
);
"""

_STATEMENTS: tuple[str, ...] = (
    _PA_TRAINING_RUNS_DDL,
    _PA_MODEL_REGISTRY_DDL,
    _PA_LOAD_EVENTS_DDL,
    _PA_ADVISORIES_DDL,
    "CREATE INDEX IF NOT EXISTS idx_pa_training_runs_model ON pa_training_runs(model_id);",
    "CREATE INDEX IF NOT EXISTS idx_pa_training_runs_status ON pa_training_runs(status);",
    "CREATE INDEX IF NOT EXISTS idx_pa_model_registry_run ON pa_model_registry(run_id);",
    "CREATE INDEX IF NOT EXISTS idx_pa_model_registry_state ON pa_model_registry(lifecycle_state);",
    "CREATE INDEX IF NOT EXISTS idx_pa_load_events_model ON pa_load_events(model_id);",
    "CREATE INDEX IF NOT EXISTS idx_pa_load_events_created ON pa_load_events(created_at);",
    # Composite uniqueness backing the upsert's ON CONFLICT (created_at): a
    # replay of a recorded load re-writes the row rather than duplicating it.
    # Both providers honour a UNIQUE INDEX.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_pa_load_events_created ON pa_load_events(created_at);",
    "CREATE INDEX IF NOT EXISTS idx_pa_advisories_model ON pa_advisories(model_id);",
    "CREATE INDEX IF NOT EXISTS idx_pa_advisories_ticket ON pa_advisories(ticket);",
    "CREATE INDEX IF NOT EXISTS idx_pa_advisories_evaluated ON pa_advisories(evaluated_at);",
)


def position_adviser_schema_statements() -> tuple[str, ...]:
    """The position_adviser-owned tables, in the SQLite dialect.

    The statements are also the SQLite bootstrap path: the store's own
    ``ensure_schema`` emits the same DDL, so a SQLite database converges to the
    same shape whether it is created by the store or by the fabric. Under
    PostgreSQL the ``audit`` domain's provisioning translates and applies them.
    """
    return _STATEMENTS


__all__ = ["position_adviser_schema_statements"]
