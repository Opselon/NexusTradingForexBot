"""Feature-drift circuit breaker (mission P0 item 7A).

Reuses the CANONICAL drift machinery — shadow70's Shadow70DriftMonitor
(PSI / mean-shift / std-ratio / missing-rate with documented thresholds and
a sample floor) — as the single drift-truth source. This module adds the
missing pieces only:

1. REFERENCE WIRING: the monitor was constructed but its reference
   distribution was never set (verified: zero callers of set_reference in
   src/), so every summary reported NO_REFERENCE_DISTRIBUTION. Here the
   reference comes from the SERVING champion's own scaler (model.scaler.npz
   mean/std, the same numbers the champion was trained against —
   per-feature, dimension-exact for the monitored slice).

2. STATE MODEL: NORMAL / DRIFT_WARNING / DRIFT_CRITICAL over the alert
   severities, plus INSUFFICIENT_DATA below the sample floor. Never acts:
   the state is DATA. Consumption belongs to the execution-mode governor
   (no drift detector ever force-switches modes — mission 7A/7E boundary;
   see learning_config: mode transitions stay operator/governance-owned).

3. 50D PATH: the 70D drift monitor watches the liquidity slice (60..69)
   which is exactly the LIVE-innovated block (base 0..49 are the champion's
   trained inputs). Where a 50D-only session runs (no liquidity governor),
   the breaker reports INSUFFICIENT_DATA honestly instead of fabricating.

Purity: no DB, no adapter, no I/O beyond reading the scaler file the
champion already serves from; O(1) per call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.shadow.shadow70.health import (
    DRIFT_SEVERITY_CRITICAL,
    DRIFT_SEVERITY_NORMAL,
    DRIFT_SEVERITY_WARNING,
    Shadow70DriftMonitor,
)

logger = get_logger("nexus_scalp.risk.drift_breaker")

#: State vocabulary (mission 7A). Deliberately distinct from severities:
#: the breaker state aggregates all feature alerts into one verdict.
STATE_NORMAL = "NORMAL"
STATE_DRIFT_WARNING = "DRIFT_WARNING"
STATE_DRIFT_CRITICAL = "DRIFT_CRITICAL"
STATE_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class FeatureDriftBreaker:
    """Aggregates the canonical drift monitor into a single breaker state."""

    def __init__(
        self,
        drift_monitor: Shadow70DriftMonitor | None = None,
        min_samples: int | None = None,
    ) -> None:
        self.monitor = drift_monitor or Shadow70DriftMonitor()
        if min_samples is not None:
            self.monitor.min_samples = int(min_samples)
        self._reference_set = False

    # ------------------------------------------------------------------
    # Reference distribution (from the serving artifact's scaler)
    # ------------------------------------------------------------------

    def set_reference_from_scaler(self, scaler_path: str | Path | None) -> bool:
        """Loads the champion scaler (model.scaler.npz: mean/std arrays).

        The monitor watches the 70D liquidity slice; the scaler is
        dimension-exact for the SERVING schema (70 for scalp_v3), so the
        slice indices line up with the training distribution by
        construction. A missing/invalid scaler keeps the honest
        NO_REFERENCE state — never a fabricated reference.
        """
        if scaler_path is None:
            return False
        try:
            import numpy as np

            path = Path(scaler_path)
            if not path.exists():
                logger.info("[DRIFT] scaler reference unavailable (no file)", path=str(path))
                return False
            data = np.load(path)
            mean = data["mean"].tolist() if "mean" in data else None
            std = data["std"].tolist() if "std" in data else None
            if not mean or not std:
                return False
            # The monitor validates slice length against the 10 liquidity
            # features; feed the FULL serving vectors — its update() slices
            # LIQUIDITY_SLICE itself, and evaluate() compares per-index.
            # set_reference validates the 10-wide contract, so slice here.
            from nexus_scalp.shadow.shadow70.health import LIQUIDITY_SLICE

            lo, hi = LIQUIDITY_SLICE
            if hi > len(mean) or hi > len(std):
                logger.info(
                    "[DRIFT] scaler narrower than liquidity slice",
                    dim=len(mean),
                    slice=(lo, hi),
                )
                return False
            self.monitor.set_reference(
                [float(x) for x in mean[lo:hi]],
                [float(x) for x in std[lo:hi]],
            )
            self._reference_set = True
            logger.info(
                "[DRIFT] reference distribution set from scaler",
                path=str(path),
                features=hi - lo,
            )
            return True
        except Exception as exc:
            logger.warning("[DRIFT] scaler reference load failed (isolated)", error=str(exc))
            return False

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def state(self) -> str:
        """Current aggregate drift state (one of the STATE_* constants)."""
        summary = self.monitor.summary()
        if not summary.get("available", False):
            return STATE_INSUFFICIENT_DATA
        status = summary.get("status", "")
        if status in ("INSUFFICIENT_EVIDENCE", "NO_REFERENCE_DISTRIBUTION"):
            return STATE_INSUFFICIENT_DATA
        # Reference may be set directly on the canonical monitor (tests,
        # runtime wiring) rather than via set_reference_from_scaler; the
        # monitor's own "NO_REFERENCE_DISTRIBUTION" availability check has
        # already validated it by this point.
        if not self._reference_set:
            self._reference_set = True
        severity = summary.get("severity", DRIFT_SEVERITY_NORMAL)
        if severity == DRIFT_SEVERITY_CRITICAL:
            return STATE_DRIFT_CRITICAL
        if severity == DRIFT_SEVERITY_WARNING:
            return STATE_DRIFT_WARNING
        return STATE_NORMAL

    def snapshot(self) -> dict[str, Any]:
        """Full truth for digest/API consumers (mission 5)."""
        summary = self.monitor.summary()
        return {
            "state": self.state(),
            "monitor": summary,
            "alerts": [a.to_dict() for a in self.monitor.latest_alerts(limit=10)],
            "reference_set": self._reference_set,
        }

    # ------------------------------------------------------------------
    # Feeding (delegates to the canonical monitor)
    # ------------------------------------------------------------------

    def update(self, vector70: list[float]) -> bool:
        """Feeds one live 70D vector; invalid input is fail-inert."""
        try:
            return self.monitor.update(vector70)
        except Exception:
            return False


def build_drift_breaker_for_engine(engine: Any) -> FeatureDriftBreaker | None:
    """Wires the breaker onto an engine's EXISTING canonical monitor.

    Returns None when the engine has no shadow70 drift monitor (never
    fabricates a parallel one). Also wires the scaler reference from the
    serving champion when the artifact path is resolvable.
    """
    monitor = getattr(engine, "_shadow70_drift", None)
    if monitor is None:
        return None
    breaker = FeatureDriftBreaker(monitor)
    scaler_path = getattr(getattr(engine, "champion_manager", None), "scaler_path", None)
    if scaler_path:
        breaker.set_reference_from_scaler(scaler_path)
    return breaker
