"""Schema for the operational tables the shadow / shadow70 / governance
stores own (DB-FABRIC-002).

``shadow_runs`` / ``shadow_decisions`` / ``shadow_comparisons`` /
``shadow_promotions`` come from ``ShadowStore.ensure_schema``,
``shadow70_*`` from ``Shadow70Store.ensure_schema`` and
``model_governance_events`` / ``model_governance_state`` /
``model_shadow_comparisons`` / ``model_runtime_health`` from
``GovernanceStore.ensure_schema`` — all SQLite-only paths until now. Under
PostgreSQL those tables live in the ``ops_shadow`` domain's database and had
NO authored DDL, so ``provision_domain`` could not create them and every
operational write from these stores was dead-lettered on the first batch.

This module authors that DDL once, in the SQLite dialect, exactly mirroring
the ownership contract ``model_lifecycle.schema`` established: the statements
the stores' own ``ensure_schema`` emit, captured here so the fabric's
provisioner and the SQLite bootstrap converge on the same physical schema
(``IF NOT EXISTS`` keeps re-provisioning non-destructive — the same contract
``ai_providers.store._SCHEMA`` and ``model_lifecycle.schema`` already hold).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# PHASE 11 shadow evaluation (spec 20 / 24 / 25)
# ---------------------------------------------------------------------------

_SHADOW_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS shadow_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT UNIQUE NOT NULL,
    champion_model_id TEXT NOT NULL,
    champion_version TEXT NOT NULL,
    challenger_model_id TEXT NOT NULL,
    challenger_version TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT DEFAULT '',
    decision_count INTEGER DEFAULT 0,
    error TEXT DEFAULT '',
    git_revision TEXT DEFAULT '',
    configuration_version TEXT DEFAULT '',
    challenger_artifact_hash TEXT DEFAULT '',
    champion_artifact_hash TEXT DEFAULT ''
);
"""

_SHADOW_DECISIONS_DDL = """
CREATE TABLE IF NOT EXISTS shadow_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shadow_decision_id TEXT UNIQUE NOT NULL,
    run_id TEXT NOT NULL,
    decision_id TEXT DEFAULT '',
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT DEFAULT '',
    champion_model_id TEXT NOT NULL,
    champion_version TEXT NOT NULL,
    challenger_model_id TEXT NOT NULL,
    challenger_version TEXT NOT NULL,
    feature_schema_id TEXT DEFAULT 'scalp_v1',
    feature_dimension INTEGER DEFAULT 50,
    feature_hash TEXT DEFAULT '',
    regime TEXT DEFAULT '',
    session TEXT DEFAULT '',
    champion_action TEXT DEFAULT '',
    champion_confidence REAL DEFAULT 0.0,
    challenger_action TEXT DEFAULT '',
    challenger_confidence REAL DEFAULT 0.0,
    action_agreement INTEGER DEFAULT 0,
    valid_comparison INTEGER DEFAULT 1,
    invalid_reason TEXT DEFAULT '',
    hypothetical_pnl_usd REAL DEFAULT 0.0,
    hypothetical_r REAL DEFAULT 0.0,
    mfe_r REAL DEFAULT 0.0,
    mae_r REAL DEFAULT 0.0,
    holding_duration_sec REAL DEFAULT 0.0,
    exit_reason TEXT DEFAULT '',
    simulated INTEGER DEFAULT 1,
    champion_entry REAL DEFAULT 0.0,
    champion_sl REAL DEFAULT 0.0,
    champion_tp REAL DEFAULT 0.0,
    shadow_entry REAL DEFAULT 0.0,
    shadow_sl REAL DEFAULT 0.0,
    shadow_tp REAL DEFAULT 0.0,
    spread_usd REAL DEFAULT 0.0,
    shadow_r REAL,
    shadow_mfe_r REAL,
    shadow_mae_r REAL,
    shadow_pnl_usd REAL,
    shadow_holding_sec REAL,
    shadow_exit_reason TEXT DEFAULT '',
    delta_r REAL,
    outcome_status TEXT DEFAULT 'NOT_RECORDED',
    payload TEXT DEFAULT '{}'
);
"""

_SHADOW_COMPARISONS_DDL = """
CREATE TABLE IF NOT EXISTS shadow_comparisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT UNIQUE NOT NULL,
    champion_model_id TEXT NOT NULL,
    champion_version TEXT NOT NULL,
    challenger_model_id TEXT NOT NULL,
    challenger_version TEXT NOT NULL,
    sample_count INTEGER DEFAULT 0,
    valid_comparisons INTEGER DEFAULT 0,
    invalid_comparisons INTEGER DEFAULT 0,
    action_agreement_rate REAL DEFAULT 0.0,
    champion_expectancy_r REAL DEFAULT 0.0,
    challenger_expectancy_r REAL DEFAULT 0.0,
    champion_drawdown_r REAL DEFAULT 0.0,
    challenger_drawdown_r REAL DEFAULT 0.0,
    evidence_status TEXT DEFAULT 'INSUFFICIENT_EVIDENCE',
    samples_required INTEGER DEFAULT 30,
    samples_observed INTEGER DEFAULT 0,
    by_regime TEXT DEFAULT '{}',
    by_strategy TEXT DEFAULT '{}',
    best_regimes TEXT DEFAULT '[]',
    worst_regimes TEXT DEFAULT '[]',
    degraded_regimes TEXT DEFAULT '[]',
    degraded_strategies TEXT DEFAULT '[]',
    evaluated_at TEXT NOT NULL,
    payload TEXT DEFAULT '{}'
);
"""

_SHADOW_PROMOTIONS_DDL = """
CREATE TABLE IF NOT EXISTS shadow_promotions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT UNIQUE NOT NULL,
    candidate_model_id TEXT NOT NULL,
    candidate_version TEXT NOT NULL,
    champion_model_id TEXT NOT NULL,
    champion_version TEXT NOT NULL,
    final_score REAL DEFAULT 0.0,
    eligible INTEGER DEFAULT 0,
    vetoes TEXT DEFAULT '[]',
    reasons TEXT DEFAULT '[]',
    evaluated_at TEXT NOT NULL,
    payload TEXT DEFAULT '{}'
);
"""

# ---------------------------------------------------------------------------
# 70D shadow runtime (TASK-05-70D-SHADOW)
# ---------------------------------------------------------------------------

_SHADOW70_OBSERVATIONS_DDL = """
CREATE TABLE IF NOT EXISTS shadow70_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id TEXT UNIQUE NOT NULL,
    snapshot_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    symbol TEXT DEFAULT 'XAUUSD',
    timeframe TEXT DEFAULT 'M1',
    simulated INTEGER DEFAULT 1,
    model_id TEXT DEFAULT '',
    model_version TEXT DEFAULT '',
    model_hash TEXT DEFAULT '',
    scaler_hash TEXT DEFAULT '',
    schema_id TEXT DEFAULT 'scalp_v3',
    schema_dimension INTEGER DEFAULT 70,
    champion_action TEXT DEFAULT '',
    champion_probabilities TEXT DEFAULT '[]',
    champion_confidence REAL DEFAULT 0.0,
    shadow_action TEXT DEFAULT '',
    shadow_probabilities TEXT DEFAULT '[]',
    shadow_confidence REAL DEFAULT 0.0,
    confidence_delta REAL DEFAULT 0.0,
    disagreement TEXT DEFAULT 'AGREEMENT',
    agreement INTEGER DEFAULT 1,
    valid INTEGER DEFAULT 1,
    reason TEXT DEFAULT '',
    regime TEXT DEFAULT '',
    session TEXT DEFAULT '',
    news_state TEXT DEFAULT '',
    liquidity_state TEXT DEFAULT '',
    news_context_hash TEXT DEFAULT '',
    liquidity_feature_hash TEXT DEFAULT '',
    liquidity_features_10 TEXT DEFAULT '[]',
    feature_hash TEXT DEFAULT '',
    sample_source TEXT DEFAULT 'LIVE',
    latency_ms REAL DEFAULT 0.0,
    error_code TEXT DEFAULT '',
    outcome TEXT DEFAULT 'PENDING',
    outcome_resolved_at TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
"""

_SHADOW70_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS shadow70_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    event TEXT NOT NULL,
    stage TEXT DEFAULT 'SHADOW70',
    model_id TEXT DEFAULT '',
    model_version TEXT DEFAULT '',
    schema_id TEXT DEFAULT 'scalp_v3',
    error_code TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    correlation_id TEXT DEFAULT '',
    payload TEXT DEFAULT '{}',
    timestamp TEXT NOT NULL
);
"""

_SHADOW70_FEATURE_HEALTH_DDL = """
CREATE TABLE IF NOT EXISTS shadow70_feature_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    feature TEXT NOT NULL,
    feat_index INTEGER NOT NULL,
    samples INTEGER DEFAULT 0,
    finite_rate REAL DEFAULT 0.0,
    missing_rate REAL DEFAULT 0.0,
    stale_rate REAL DEFAULT 0.0,
    zero_rate REAL DEFAULT 0.0,
    mean REAL DEFAULT 0.0,
    std REAL DEFAULT 0.0,
    min REAL DEFAULT 0.0,
    max REAL DEFAULT 0.0,
    payload TEXT DEFAULT '{}'
);
"""

_SHADOW70_DRIFT_ALERTS_DDL = """
CREATE TABLE IF NOT EXISTS shadow70_drift_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id TEXT UNIQUE NOT NULL,
    timestamp TEXT NOT NULL,
    feature TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL DEFAULT 0.0,
    threshold REAL DEFAULT 0.0,
    severity TEXT DEFAULT 'NORMAL',
    reference_mean REAL DEFAULT 0.0,
    live_mean REAL DEFAULT 0.0,
    reference_std REAL DEFAULT 0.0,
    live_std REAL DEFAULT 0.0,
    samples INTEGER DEFAULT 0,
    payload TEXT DEFAULT '{}'
);
"""

# ---------------------------------------------------------------------------
# TASK-6 / CHG-0003 governance event ledger
# ---------------------------------------------------------------------------

_MODEL_GOVERNANCE_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS model_governance_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    event TEXT NOT NULL,
    stage TEXT NOT NULL,
    model_id TEXT DEFAULT '',
    model_version TEXT DEFAULT '',
    schema_id TEXT DEFAULT '',
    correlation_id TEXT DEFAULT '',
    error_code TEXT DEFAULT '',
    error_type TEXT DEFAULT '',
    duration_ms REAL DEFAULT 0.0,
    actor TEXT DEFAULT 'system',
    previous_state TEXT DEFAULT '',
    new_state TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    payload TEXT DEFAULT '{}',
    timestamp TEXT NOT NULL
);
"""

_MODEL_GOVERNANCE_STATE_DDL = """
CREATE TABLE IF NOT EXISTS model_governance_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id TEXT NOT NULL,
    model_version TEXT DEFAULT '',
    lifecycle_state TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    evidence TEXT DEFAULT '{}'
);
"""

_MODEL_SHADOW_COMPARISONS_DDL = """
CREATE TABLE IF NOT EXISTS model_shadow_comparisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comparison_id TEXT UNIQUE NOT NULL,
    run_id TEXT DEFAULT '',
    timestamp TEXT NOT NULL,
    symbol TEXT DEFAULT '',
    champion_model_id TEXT DEFAULT '',
    champion_version TEXT DEFAULT '',
    challenger_model_id TEXT DEFAULT '',
    challenger_version TEXT DEFAULT '',
    champion_action TEXT DEFAULT '',
    challenger_action TEXT DEFAULT '',
    agreement INTEGER DEFAULT 0,
    champion_probabilities TEXT DEFAULT '[]',
    challenger_probabilities TEXT DEFAULT '[]',
    feature_context_id TEXT DEFAULT '',
    news_context_id TEXT DEFAULT '',
    feature_schema_id TEXT DEFAULT '',
    feature_parity_max_abs REAL DEFAULT 0.0,
    feature_parity_mean_abs REAL DEFAULT 0.0,
    feature_parity_mismatch REAL DEFAULT 0.0,
    alignment TEXT DEFAULT '',
    latency_champion_ms REAL DEFAULT 0.0,
    latency_challenger_ms REAL DEFAULT 0.0,
    regime TEXT DEFAULT '',
    session TEXT DEFAULT '',
    simulated INTEGER DEFAULT 1,
    payload TEXT DEFAULT '{}'
);
"""

_MODEL_RUNTIME_HEALTH_DDL = """
CREATE TABLE IF NOT EXISTS model_runtime_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at TEXT NOT NULL,
    champion_id TEXT DEFAULT '',
    champion_version TEXT DEFAULT '',
    champion_schema TEXT DEFAULT '',
    champion_healthy INTEGER DEFAULT 0,
    challenger_id TEXT DEFAULT '',
    challenger_version TEXT DEFAULT '',
    challenger_state TEXT DEFAULT '',
    shadow_running INTEGER DEFAULT 0,
    shadow_comparisons INTEGER DEFAULT 0,
    shadow_errors INTEGER DEFAULT 0,
    shadow_dropped INTEGER DEFAULT 0,
    last_update TEXT DEFAULT '',
    payload TEXT DEFAULT '{}'
);
"""

_STATEMENTS: tuple[str, ...] = (
    _SHADOW_RUNS_DDL,
    _SHADOW_DECISIONS_DDL,
    _SHADOW_COMPARISONS_DDL,
    _SHADOW_PROMOTIONS_DDL,
    _SHADOW70_OBSERVATIONS_DDL,
    _SHADOW70_EVENTS_DDL,
    _SHADOW70_FEATURE_HEALTH_DDL,
    _SHADOW70_DRIFT_ALERTS_DDL,
    _MODEL_GOVERNANCE_EVENTS_DDL,
    _MODEL_GOVERNANCE_STATE_DDL,
    _MODEL_SHADOW_COMPARISONS_DDL,
    _MODEL_RUNTIME_HEALTH_DDL,
    "CREATE INDEX IF NOT EXISTS idx_shadow_decisions_run ON shadow_decisions(run_id, timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_shadow_decisions_symbol ON shadow_decisions(symbol, timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_shadow_runs_status ON shadow_runs(status);",
    "CREATE INDEX IF NOT EXISTS idx_shadow70_obs_ts ON shadow70_observations(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_shadow70_obs_model ON shadow70_observations(model_id, timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_shadow70_events_ts ON shadow70_events(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_gov_events_ts ON model_governance_events(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_gov_events_model ON model_governance_events(model_id, timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_gov_state_model ON model_governance_state(model_id);",
    "CREATE INDEX IF NOT EXISTS idx_gov_comp_ts ON model_shadow_comparisons(timestamp);",
)

#: The tables this domain provisions (the fabric's ``verify_domain_schema``
#: reference). Must stay in sync with ``SHADOW_TABLES`` in ops_provider.
TABLES: frozenset[str] = frozenset(
    {
        "shadow_runs",
        "shadow_decisions",
        "shadow_comparisons",
        "shadow_promotions",
        "shadow70_observations",
        "shadow70_events",
        "shadow70_feature_health",
        "shadow70_drift_alerts",
        "model_governance_events",
        "model_governance_state",
        "model_shadow_comparisons",
        "model_runtime_health",
    }
)


def ops_shadow_schema_statements() -> tuple[str, ...]:
    """The shadow / shadow70 / governance tables, in the SQLite dialect.

    The statements are also the SQLite bootstrap path: the stores' own
    ``ensure_schema`` emit the same DDL, so a SQLite database converges to
    the same shape whether it is created by the store or by the fabric.
    """
    return _STATEMENTS
