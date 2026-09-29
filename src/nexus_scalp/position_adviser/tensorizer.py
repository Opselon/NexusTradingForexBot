"""Runtime tensorizer — shared feature contract → model input vector.

TASK-ML-CTRL §5/§11/§20: builds the EXACT vector the trainer builds, from
the SAME shared Indicator Engine snapshots. One formula, one order, one
schema version. Training consumes ``build_training_matrix``-equivalent rows
produced by ``MLFeatureTensorizer.tensorize_dataset_row``-shaped inputs, so
train/runtime parity is structural, not aspirational.

Look-ahead policy (§11): M1 uses the policy the caller declares (closed or
current candle — must match training); M5/M15 always use the LATEST VALID
completed snapshot at or before the M1 decision timestamp. Snapshots carry
their source candle timestamp; a snapshot newer than the decision time is
REJECTED (fail loud) rather than silently used.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from nexus_scalp.position_adviser.feature_schema import (
    POSITION_FEATURE_DIM,
    POSITION_FEATURE_SCHEMA_VERSION,
    TimeframeFeatures,
    build_timeframe_block,
)
from nexus_scalp.position_adviser.features import build_live_vector


class TensorizationError(RuntimeError):
    """Feature snapshot cannot produce a valid schema-v2 vector. Fail loud."""


class MLFeatureTensorizer:
    """Builds the (POSITION_FEATURE_DIM,) float32 vector per decision."""

    def __init__(self) -> None:
        self.d = POSITION_FEATURE_DIM
        self.schema_version = POSITION_FEATURE_SCHEMA_VERSION

    def tensorize(
        self,
        *,
        position_state: dict[str, Any],
        indicator_snapshots: dict[str, Any],
        atr: float,
        decision_time: datetime,
        m1_candle_policy: str = "closed",
    ) -> tuple[list[float], dict[str, Any]]:
        """Return (vector, provenance) for ONE decision.

        ``indicator_snapshots`` maps tf -> IndicatorSnapshot (the shared
        engine's object) with ``.snapshot_time`` attached (the source candle
        timestamp the snapshot was built from).

        ``m1_candle_policy`` 'closed' = M1 block built from completed bars
        only; 'current' = includes the forming candle. MUST match training.
        """
        if m1_candle_policy not in ("closed", "current"):
            raise TensorizationError(f"unknown m1_candle_policy {m1_candle_policy!r}")

        # Position-state block (v1 contract, unchanged) — fail loud on
        # missing/non-finite keys (same semantics as build_live_vector).
        pos_vec, _order = build_live_vector(position_state)

        # Indicator blocks per timeframe, with look-ahead enforcement.
        tf_blocks: list[TimeframeFeatures] = []
        provenance: dict[str, Any] = {
            "schema_version": self.schema_version,
            "d": self.d,
            "m1_candle_policy": m1_candle_policy,
            "timeframes": {},
        }
        for tf in ("M1", "M5", "M15"):
            snap = indicator_snapshots.get(tf)
            if snap is None:
                raise TensorizationError(f"missing {tf} indicator snapshot (fail loud, §5)")
            snap_time = getattr(snap, "snapshot_time", None)
            if snap_time is None:
                raise TensorizationError(f"{tf} snapshot has no source candle timestamp (§11)")
            if snap_time > decision_time:
                raise TensorizationError(
                    f"{tf} snapshot is NEWER than the decision time "
                    f"(snapshot={snap_time.isoformat()} > decision={decision_time.isoformat()}); "
                    "look-ahead refused (§11)"
                )
            block = build_timeframe_block(tf, snap, atr)
            tf_blocks.append(block)
            provenance["timeframes"][tf] = {
                "source_candle": snap_time.isoformat(),
                "bar_count": getattr(snap, "bar_count", None),
            }

        vec = [*pos_vec]
        for block in tf_blocks:
            vec.extend(block.values)

        if len(vec) != self.d:
            raise TensorizationError(
                f"vector length {len(vec)} != schema D {self.d} — schema/code drift"
            )
        return vec, provenance


__all__ = ["MLFeatureTensorizer", "TensorizationError"]
