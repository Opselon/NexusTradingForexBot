"""Concurrency & Exposure Policy (ECON v1 phase 5)
=================================================

Separates the two distinct risk dimensions that the current code tangles
into a single integer (``MAX_TOTAL_EXPOSURE = 1`` in order_manager.py and
signals/policy.py, plus ``RiskConfig.max_concurrent_positions`` in the
RiskEngine):

  1. POSITION COUNT LIMIT  — how many positions may be open at once.
     Guards against correlated clustered losses and signal overlap.
  2. TOTAL EXPOSURE LIMIT  — how many LOTS of gross directional volume may
     be committed. Guards against leverage/margin risk regardless of count.

EVIDENCE (audit.db, 158 closed executed outcomes probed 2026-09-07):
  * maximum simultaneous open positions ever observed = 6
  * overlap-depth histogram: {1:132, 2:10, 3:1, 4:4, 5:10, 6:1}
    -> concurrency above 1 DID occur repeatedly under the old gate and the
    ledger survived; the count limit is the protective quantity to reason
    about, not a revenue lever.
  * median holding 80s, p90 762s -> overlap windows are short and signal
    clustering (not strategy intent) drove the historical stacks.

POLICY (fail-closed, no tuning by intuition):
  * The defaults keep today's behavior: position_count=1, exposure lots =
    RiskConfig.max_allowed_lots (2.0), unchanged until an operator opts in
    with measured OOS evidence.
  * Any raise is CONFIG-GATED, measured, and validated out-of-sample before
    it can influence sizing/promotion. No code path here increases limits
    on its own.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

#: The historical single-position convention (kept as the fail-closed default).
DEFAULT_POSITION_COUNT_LIMIT: int = 1

#: Depth histogram of simultaneous opens from the probe above (documentation
#: of measured reality, NOT a recommendation to raise limits).
HISTORICAL_OVERLAP_DEPTH_HISTOGRAM: dict[int, int] = {1: 132, 2: 10, 3: 1, 4: 4, 5: 10, 6: 1}
HISTORICAL_MAX_SIMULTANEOUS_OPENS: int = 6


class ConcurrencyPolicy(BaseModel):
    """Explicit, separable concurrency limits (ECON v1).

    Both limits are CONFIG-GATED: the code ships conservative defaults and
    never raises them by itself. ``total_exposure_lots`` interacts with the
    RiskEngine's own ceilings (HARD_MAX_LOTS=10, tier caps, margin clamp):
    the effective limit is the MINIMUM of every layer, so this policy can
    only ever be MORE restrictive than live sizing, never less.
    """

    #: Maximum simultaneous open positions (count dimension).
    position_count_limit: int = Field(default=DEFAULT_POSITION_COUNT_LIMIT, ge=1)
    #: Maximum gross committed volume in lots (exposure dimension).
    total_exposure_lots: float = Field(default=2.0, gt=0.0)
    #: Whether the operator has provided OOS evidence validating a raise.
    #: False keeps the policy at its conservative defaults; promotion of a
    #: candidate that assumes higher concurrency must set this explicitly.
    evidence_validated: bool = Field(default=False)

    def effective_position_count_limit(self, hard_max_positions: int = 10) -> int:
        """Count limit clamped by the absolute platform ceiling."""
        return min(self.position_count_limit, max(1, int(hard_max_positions)))

    def admits_new_position(
        self,
        *,
        open_position_count: int,
        gross_open_lots: float,
        new_position_lots: float,
        hard_max_lots: float = 10.0,
    ) -> tuple[bool, str]:
        """Pure admission decision separating count vs exposure dimensions.

        Returns (allowed, reason). Never mutates state; the caller supplies
        the authoritative open-position snapshot. The exposure check is
        against the platform HARD_MAX_LOTS too (defense in depth).
        """
        if new_position_lots <= 0.0:
            return False, "ZERO_OR_NEGATIVE_VOLUME"
        count_limit = self.effective_position_count_limit()
        if open_position_count >= count_limit:
            return False, "POSITION_COUNT_LIMIT"
        effective_exposure_cap = min(self.total_exposure_lots, float(hard_max_lots))
        if gross_open_lots + new_position_lots > effective_exposure_cap + 1e-9:
            return False, "TOTAL_EXPOSURE_LIMIT"
        return True, "ALLOWED"


def measure_overlap_depth(
    open_intervals: list[tuple[datetime, datetime]],
) -> dict[str, Any]:
    """Measures the concurrency evidence from (open, close) intervals.

    Deterministic sweep-line over the recorded ledger intervals. Returns
    max depth, the depth histogram, and the interval count. This is the
    measurement primitive any future limit-raise proposal must cite.
    """
    if not open_intervals:
        return {"intervals": 0, "max_depth": 0, "depth_histogram": {}}
    events: list[tuple[datetime, int]] = []
    for start, end in open_intervals:
        if end <= start:
            continue
        events.append((start, 1))
        events.append((end, -1))
    events.sort(key=lambda e: (e[0], e[1]))  # closes before opens at ties
    current = 0
    max_depth = 0
    histogram: Counter[int] = Counter()
    for _, delta in events:
        current += delta
        if delta == 1 and current > 0:
            histogram[current] += 1
        max_depth = max(max_depth, current)
    return {
        "intervals": len([1 for s, e in open_intervals if e > s]),
        "max_depth": max_depth,
        "depth_histogram": {int(k): int(v) for k, v in sorted(histogram.items())},
    }


def measure_overlap_depth_from_audit_db(db_path: str) -> dict[str, Any]:
    """Concurrency evidence directly from the canonical audit DB.

    Reads closed executed experiences (decision_timestamp x outcome_timestamp)
    — the same authoritative rows the accounting layer uses. Never writes.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    try:
        rows = conn.execute(
            """
            SELECT e.decision_timestamp, o.outcome_timestamp
            FROM audit_experiences e
            JOIN audit_experience_outcomes o ON o.idempotency_key = e.idempotency_key
            WHERE o.is_closed = 1 AND o.is_executed = 1
            ORDER BY e.decision_timestamp
            """
        ).fetchall()
    finally:
        conn.close()
    intervals: list[tuple[datetime, datetime]] = []
    for decision_ts, outcome_ts in rows:
        try:
            s = datetime.fromisoformat(str(decision_ts))
            e = datetime.fromisoformat(str(outcome_ts))
            intervals.append((s, e))
        except (TypeError, ValueError):
            continue
    result = measure_overlap_depth(intervals)
    result["source"] = "audit_db"
    return result
