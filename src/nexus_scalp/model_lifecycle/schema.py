"""Schema for the operational tables the model_lifecycle package owns.

``learning_cycles`` / ``learning_cycle_events`` are created by
``LearningCycleStore.ensure_schema`` and ``training_runs`` / ``model_comparisons``
by ``TrainingRunStore.ensure_schema`` — SQLite-only paths until now. Under
PostgreSQL those tables live in the ``audit`` domain's database but were NOT
part of its authored DDL, so a fresh PostgreSQL install had no
``learning_cycles`` at all while the live migrated ``nexusdb`` did (the
table-set divergence recorded in ``tests/unit/test_pg_schema_convergence.py``).

This module authors that DDL once, in the SQLite dialect, so the audit
domain's provisioning path can create the tables on PostgreSQL idempotently
(``IF NOT EXISTS`` makes re-provisioning non-destructive) — exactly the
contract ``ai_providers.store._SCHEMA`` already has for its ledger.
"""

from __future__ import annotations

_LEARNING_CYCLES_DDL = """
CREATE TABLE IF NOT EXISTS learning_cycles (
    cycle_id TEXT PRIMARY KEY,
    trigger TEXT NOT NULL DEFAULT '',
    trigger_identity TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT DEFAULT '',
    completed_at TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'IDLE',
    dataset_id TEXT DEFAULT '',
    dataset_hash TEXT DEFAULT '',
    training_run_id TEXT DEFAULT '',
    candidate_model_id TEXT DEFAULT '',
    candidate_artifact_hash TEXT DEFAULT '',
    validation_run_id TEXT DEFAULT '',
    shadow_run_id TEXT DEFAULT '',
    promotion_evaluation_id TEXT DEFAULT '',
    decision TEXT DEFAULT '',
    reasons TEXT DEFAULT '[]',
    error_code TEXT DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0,
    payload TEXT DEFAULT '{}'
);
"""

_LEARNING_CYCLE_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS learning_cycle_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    previous_state TEXT NOT NULL,
    new_state TEXT NOT NULL,
    reason TEXT DEFAULT '',
    payload TEXT DEFAULT '{}'
);
"""

_TRAINING_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS training_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT UNIQUE NOT NULL,
    dataset_id TEXT NOT NULL,
    feature_schema_id TEXT DEFAULT 'scalp_v1',
    feature_dimension INTEGER DEFAULT 50,
    model_id TEXT DEFAULT '',
    model_version TEXT DEFAULT '',
    parent_champion_id TEXT DEFAULT '',
    parent_champion_version TEXT DEFAULT '',
    hyperparameters TEXT DEFAULT '{}',
    random_seed INTEGER DEFAULT 42,
    architecture TEXT DEFAULT 'scalp_net',
    train_range TEXT DEFAULT '{}',
    validation_range TEXT DEFAULT '{}',
    oos_range TEXT DEFAULT '{}',
    embargo_bars INTEGER DEFAULT 15,
    purge_bars INTEGER DEFAULT 15,
    started_at TEXT NOT NULL,
    finished_at TEXT DEFAULT '',
    artifacts TEXT DEFAULT '[]',
    metrics TEXT DEFAULT '{}',
    gates TEXT DEFAULT '[]',
    status TEXT NOT NULL,
    failure_reason TEXT DEFAULT '',
    build_identity TEXT DEFAULT ''
);
"""

_MODEL_COMPARISONS_DDL = """
CREATE TABLE IF NOT EXISTS model_comparisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT UNIQUE NOT NULL,
    candidate_model_id TEXT NOT NULL,
    candidate_version TEXT NOT NULL,
    champion_model_id TEXT DEFAULT '',
    champion_version TEXT DEFAULT '',
    comparison TEXT DEFAULT '{}',
    improvement_score REAL DEFAULT 0.0,
    eligible INTEGER DEFAULT 0,
    compared_at TEXT NOT NULL
);
"""

_STATEMENTS: tuple[str, ...] = (
    _LEARNING_CYCLES_DDL,
    _LEARNING_CYCLE_EVENTS_DDL,
    _TRAINING_RUNS_DDL,
    _MODEL_COMPARISONS_DDL,
    "CREATE INDEX IF NOT EXISTS idx_learning_cycles_status ON learning_cycles(status);",
    "CREATE INDEX IF NOT EXISTS idx_learning_cycles_trigger ON learning_cycles(trigger_identity);",
    "CREATE INDEX IF NOT EXISTS idx_learning_cycle_events_cycle ON learning_cycle_events(cycle_id);",
    "CREATE INDEX IF NOT EXISTS idx_train_runs_dataset ON training_runs(dataset_id);",
    "CREATE INDEX IF NOT EXISTS idx_train_runs_status ON training_runs(status);",
    "CREATE INDEX IF NOT EXISTS idx_model_comp_candidate ON model_comparisons(candidate_model_id);",
)


def model_lifecycle_schema_statements() -> tuple[str, ...]:
    """The model_lifecycle-owned tables, in the SQLite dialect.

    The statements are also the SQLite bootstrap path: the stores' own
    ``ensure_schema`` emits the same DDL, so a SQLite database converges to
    the same shape whether it is created by the store or by the fabric.
    """
    return _STATEMENTS
