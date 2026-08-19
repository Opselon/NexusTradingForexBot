"""70D Liquidity Shadow Runtime (TASK-05-70D-SHADOW).

Observability-only runtime that evaluates a validated 70D candidate against
the live Champion WITHOUT any execution authority:

* SHADOW_70D v1 contract (agents/contracts.md)
* Load validation: manifest / artifact hash / schema / dimension / scaler
* 70D vector = 50 Base + 10 News + 10 Liquidity (POST_70D contract)
* Idempotent deterministic observations
* Disagreement taxonomy (8 classes)
* Feature health + drift (NORMAL/WATCH/WARNING/CRITICAL)
* Bounded queue + async persistence (no sync DB on tick path)

SAFETY: this package imports NO adapter, NO order manager, NO risk engine
and NO execution/policy object (INV-018).
"""

from __future__ import annotations

from nexus_scalp.shadow.shadow70.health import (
    DRIFT_SEVERITY_CRITICAL,
    DRIFT_SEVERITY_NORMAL,
    DRIFT_SEVERITY_WARNING,
    DRIFT_SEVERITY_WATCH,
    Shadow70DriftAlert,
    Shadow70DriftMonitor,
    Shadow70FeatureHealth,
    Shadow70FeatureHealthMonitor,
)
from nexus_scalp.shadow.shadow70.models import (
    SHADOW70_SCHEMA_ID,
    DisagreementClass,
    Shadow70CandidateContract,
    Shadow70FeatureProvenance,
    Shadow70Observation,
    Shadow70RuntimeState,
    Shadow70VectorReport,
    classify_disagreement,
)
from nexus_scalp.shadow.shadow70.runtime import (
    MAX_INMEMORY_OBSERVATIONS,
    SHADOW70_LATENCY_BUDGET_MS,
    Shadow70LoadResult,
    Shadow70LoadValidator,
    Shadow70Runtime,
)
from nexus_scalp.shadow.shadow70.store import (
    Shadow70BackpressurePolicy,
    Shadow70Persistence,
    Shadow70Store,
)
from nexus_scalp.shadow.shadow70.worker import (
    Shadow70QueueItem,
    Shadow70Worker,
    format_shadow70_status,
)

__all__ = [
    "DRIFT_SEVERITY_CRITICAL",
    "DRIFT_SEVERITY_NORMAL",
    "DRIFT_SEVERITY_WARNING",
    "DRIFT_SEVERITY_WATCH",
    "MAX_INMEMORY_OBSERVATIONS",
    "SHADOW70_LATENCY_BUDGET_MS",
    "SHADOW70_SCHEMA_ID",
    "DisagreementClass",
    "Shadow70BackpressurePolicy",
    "Shadow70CandidateContract",
    "Shadow70DriftAlert",
    "Shadow70DriftMonitor",
    "Shadow70FeatureHealth",
    "Shadow70FeatureHealthMonitor",
    "Shadow70FeatureProvenance",
    "Shadow70LoadResult",
    "Shadow70LoadValidator",
    "Shadow70Observation",
    "Shadow70Persistence",
    "Shadow70QueueItem",
    "Shadow70Runtime",
    "Shadow70RuntimeState",
    "Shadow70Store",
    "Shadow70VectorReport",
    "Shadow70Worker",
    "classify_disagreement",
    "format_shadow70_status",
]
