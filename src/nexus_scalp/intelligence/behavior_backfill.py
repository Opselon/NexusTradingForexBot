"""Behavior analysis backfiller (historical sweep).

Extracted VERBATIM from intelligence/behavior.py (Agent-5 modularization,
behavior-preserving). Runs analyze_canonical_trades over historical
experience records that predate the pipeline (one-shot/repair utility,
idempotency by analysis key).

USED BY: intelligence/worker.py, tests (via the behavior facade).
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.intelligence.behavior_canonical import analyze_canonical_trades
from nexus_scalp.intelligence.behavior_detect import BehaviorDetectionEngine
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.intelligence.behavior_backfill")


class BehaviorAnalysisBackfiller:
    """
    Bounded historical behavioral-analysis backfill driver.

    Runs offline (never on the tick path) and is fully idempotent: tickets
    already analyzed under the same (behavior_version, anomaly_version) are
    skipped. `max_trades_per_run` bounds one pass so the engine never scans
    unbounded history in a single tick.
    """

    def __init__(
        self,
        audit_repo: AuditRepository,
        max_trades_per_run: int = 200,
        behavior_version: str = "behavior-v1",
        anomaly_version: str = "anomaly-v1",
    ) -> None:
        self.audit_repo = audit_repo
        self.max_trades_per_run = max(1, int(max_trades_per_run))
        self.behavior_version = behavior_version
        self.anomaly_version = anomaly_version
        self.engine = BehaviorDetectionEngine(audit_repo=audit_repo)

    def run(
        self, behavior_version: str = "behavior-v1", anomaly_version: str = "anomaly-v1"
    ) -> dict[str, Any]:
        """Runs one bounded pass; returns the summary dict."""
        return analyze_canonical_trades(
            audit_repo=self.audit_repo,
            engine=self.engine,
            behavior_version=behavior_version,
            anomaly_version=anomaly_version,
            max_trades=self.max_trades_per_run,
        )
