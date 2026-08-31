"""Centralized forensic health checks (TASK-11 POST-70D monitoring).

Every check is a small, read-only, failure-isolated function producing a
CheckResult with the five-level status vocabulary. No check mutates
production databases, artifacts, or runtime state. No check auto-repairs:
it detects, classifies and reports (TASK-11 §0/§55).

The engine (forensics/engine.py) wires these into groups and the aggregate
FORENSIC_HEALTH_SNAPSHOT.
"""

from __future__ import annotations

from nexus_scalp.forensics.checks_accounting import (  # noqa: F401
    check_accounting_divergence,
    check_database_growth,
    check_dataset_manifest_health,
    check_duplicate_economic_outcome,
    check_experience_outcome_gap,
    check_impossible_excursion,
    check_migration_state,
    check_queue_growth,
)

# 70D contract constants (historical surface: tests access them via checks.*)
from nexus_scalp.forensics.checks_features import (  # noqa: F401  # noqa: F401
    BASE_INDICES,
    EXPECTED_LIQUIDITY_INDEX_60_NAME,
    FEATURE_REF_REGISTRY,
    LIQUIDITY_INDICES,
    NEWS_INDICES,
    check_causal_canary,
    check_feature_contract_70d,
    check_feature_contract_vector,
    check_feature_liquidity_contract,
    check_feature_schema_registry,
    check_liquidity_feature_health,
    check_model_artifact,
    check_model_dimension_contract,
    check_training_live_parity_canary,
)
from nexus_scalp.forensics.checks_governance import (  # noqa: F401
    check_champion_identity,
    check_governance_consistency,
    check_performance_regression,
    check_runtime_mode_integrity,
    check_worker_progress,
)
from nexus_scalp.forensics.checks_news import (  # noqa: F401
    check_news_availability_matrix,
    check_news_health,
    check_news_worker_progress,
    check_shadow_health,
    check_telegram_delivery,
)
from nexus_scalp.forensics.checks_observability import (  # noqa: F401
    _SILENT_FALLBACK_PATTERNS,
    check_api_200_but_wrong,
    check_chart_semantic_health,
    check_correlation_propagation,
    check_database_integrity,
    check_silent_fallback,
    check_trace_completeness,
    check_ui_bundle_drift,
    check_ui_canonical_state,
)

# ---------------------------------------------------------------------------
# CHG-0032 Step 2 — FACADE
#
# The former 2,758-line monolith was decomposed verbatim into domain slices
# (checks_features / checks_accounting / checks_news / checks_governance /
# checks_observability + checks_support helpers). This module remains THE
# import surface: forensics.engine (``checks.check_*``), cli doctor flows and
# the test suites all resolve every symbol from here, unchanged.
# Import graph: support <- domains <- facade (acyclic).
# ---------------------------------------------------------------------------
from nexus_scalp.forensics.checks_support import (  # noqa: F401
    _audit_path,
    _broker_ledger_divergence,
    _champion_artifact_info,
    _config_liquidity_enabled,
    _config_mode,
    _config_news_enabled,
    _extract_feature_columns,
    _fmt,
    _integrity_for,
    _iso_age_seconds,
    _last_feature_vectors,
    _load_runtime_config,
    _news_state,
    _ok,
    _parse_close_time,
    _probe_vector,
    _registered_families,
    _ro_connect,
    _row_count,
    _safe,
    _safe_mean,
    _safe_std,
    _sha256,
    _shadow_state,
    _table_names,
    _ui_bundle_files,
    _unknown,
)
