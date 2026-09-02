"""Behavior intelligence — FACADE over cohesive modules.

Modularization (Agent-5): public surface unchanged — every name resolves
exactly as before while the implementations live in:

    behavior_detect.py     BehaviorDetectionEngine + detectors + thresholds
    behavior_canonical.py  analyze_canonical_trades + anomaly builders
    behavior_backfill.py   BehaviorAnalysisBackfiller

Existing imports (application/live_engine, intelligence/worker,
hygiene/retention, tests) keep working unchanged.
"""

from __future__ import annotations

from nexus_scalp.intelligence.behavior_backfill import BehaviorAnalysisBackfiller
from nexus_scalp.intelligence.behavior_canonical import (
    analyze_canonical_trades,
)
from nexus_scalp.intelligence.behavior_detect import (
    ANOMALY_ALGORITHM_VERSION,
    BEHAVIOR_ALGORITHM_VERSION,
    BehaviorDetectionEngine,
)

__all__ = [
    "ANOMALY_ALGORITHM_VERSION",
    "BEHAVIOR_ALGORITHM_VERSION",
    "BehaviorAnalysisBackfiller",
    "BehaviorDetectionEngine",
    "analyze_canonical_trades",
]
