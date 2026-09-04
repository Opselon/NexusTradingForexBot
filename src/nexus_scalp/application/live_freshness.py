"""
LiveFreshnessService — extracted Cluster 3 (Freshness & Ingestion Diagnostics).

Single owner for staleness policy, freshness aggregation, freshness gate,
and freshness diagnostics. No broker write, no trading, no OrderManager/
RiskEngine/AuditRepository/Web authority. LiveEngine retains ownership of
stamps, sequences, counters, and runtime state; this service is pure
apart from the explicit snapshot it receives.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.domain.enums import ActionType


@dataclass(frozen=True)
class LiveFreshnessSnapshot:
    """Immutable captured inputs for freshness computation."""

    freshness_max_age_sec: float
    last_tick_timestamp: datetime | None
    last_feature_update: datetime | None
    last_inference_timestamp: datetime | None
    last_decision_timestamp: datetime | None
    tick_sequence: int
    feature_sequence: int
    inference_sequence: int
    decision_sequence: int
    monotonic_tick_ms: int
    last_raw_market_hash: str
    last_feature_hash: str
    last_model_input_hash: str
    last_model_output_hash: str
    market_updates_total: int
    feature_builds_total: int
    inference_runs_total: int
    inference_failures_total: int
    decision_updates_total: int
    stale_state_detected_total: int


class LiveFreshnessService:
    """Cohesive freshness policy owner (no LiveEngine back-reference)."""

    # --- pure stage classifier (verbatim from LiveEngine) -----------------

    @staticmethod
    def stage_freshness(
        stamp: datetime | None, max_age_sec: float
    ) -> tuple[str, float | None]:
        """Return (state, age_ms) for one stage given its last-update stamp."""
        if stamp is None:
            return "UNKNOWN", None
        age = (datetime.now(UTC) - stamp).total_seconds()
        if age < 0:
            age = 0.0
        if age > max_age_sec:
            return "STALE", round(age * 1000.0, 1)
        return "FRESH", round(age * 1000.0, 1)

    # --- pure freshness aggregation (no counter mutation) -----------------

    def compute_freshness(self, snapshot: LiveFreshnessSnapshot) -> dict[str, Any]:
        """Pure freshness aggregation — caller owns counter bump on STALE."""
        max_age = float(snapshot.freshness_max_age_sec)
        mkt_state, mkt_age = self.stage_freshness(snapshot.last_tick_timestamp, max_age)
        feat_state, feat_age = self.stage_freshness(snapshot.last_feature_update, max_age)
        inf_state, inf_age = self.stage_freshness(snapshot.last_inference_timestamp, max_age)
        dec_state, dec_age = self.stage_freshness(snapshot.last_decision_timestamp, max_age)
        stage_states = [mkt_state, feat_state, inf_state, dec_state]
        if "STALE" in stage_states:
            overall = "STALE"
        elif "UNKNOWN" in stage_states:
            overall = "UNKNOWN"
        else:
            overall = "FRESH"
        return {
            "market": {"state": mkt_state, "age_ms": mkt_age},
            "features": {"state": feat_state, "age_ms": feat_age},
            "inference": {"state": inf_state, "age_ms": inf_age},
            "decision": {"state": dec_state, "age_ms": dec_age},
            "overall": overall,
            "max_age_sec": max_age,
            "sequences": {
                "tick": snapshot.tick_sequence,
                "feature": snapshot.feature_sequence,
                "inference": snapshot.inference_sequence,
                "decision": snapshot.decision_sequence,
            },
            "monotonic_tick_ms": snapshot.monotonic_tick_ms,
            "hashes": {
                "raw_market": snapshot.last_raw_market_hash,
                "feature": snapshot.last_feature_hash,
                "model_input": snapshot.last_model_input_hash,
                "model_output": snapshot.last_model_output_hash,
            },
            "telemetry": {
                "market_updates_total": snapshot.market_updates_total,
                "feature_builds_total": snapshot.feature_builds_total,
                "inference_runs_total": snapshot.inference_runs_total,
                "inference_failures_total": snapshot.inference_failures_total,
                "decision_updates_total": snapshot.decision_updates_total,
                "stale_state_detected_total": snapshot.stale_state_detected_total,
            },
        }

    # --- pure gate downgrade (no counter mutation) ------------------------

    @staticmethod
    def gate_proposal(fresh: dict[str, Any], proposal: Any) -> tuple[Any, bool]:
        """Downgrade proposal to BLOCKED_BY_STALE when overall==STALE."""
        overall = fresh.get("overall")
        if overall != "STALE":
            return proposal, False
        try:
            return (
                proposal.model_copy(
                    update={
                        "action": ActionType.NO_TRADE,
                        "confidence": 0.0,
                        "reason_code": "BLOCKED_BY_STALE",
                    }
                ),
                True,
            )
        except Exception:
            return proposal, True

    # --- diagnostic (observational, exception-isolated) -------------------

    @staticmethod
    def diagnose(
        snapshot: LiveFreshnessSnapshot,
        *,
        adapter: Any,
        aggregator: Any,
        feature_engine: Any,
        build_vector_fn: Any,
        get_bundle_fn: Any,
        run_inference_fn: Any,
        symbol: str,
    ) -> dict[str, Any]:
        """No-cache diagnostic — localizes freeze, never propagates exception."""
        import numpy as np

        result: dict[str, Any] = {
            "frozen_at": None,
            "stages": {},
            "error": None,
        }
        try:
            tick = adapter.get_tick(symbol)
            if tick is None:
                result["frozen_at"] = "MARKET"
                result["error"] = "adapter.get_tick returned None"
                return result
            completed_bars = aggregator.get_completed_bars()
            fv = feature_engine.compute_from_bars(
                completed_bars=completed_bars, current_tick=tick
            )
            mkt_hash = hashlib.sha1(
                f"{tick.bid:.5f}|{tick.ask:.5f}|{tick.last:.5f}".encode()
            ).hexdigest()[:16]
            feat_vals = list(getattr(fv, "to_tensor_input", lambda: [])())
            feat_hash = hashlib.sha1(
                ("|".join(f"{v:.6g}" for v in feat_vals)).encode()
            ).hexdigest()[:16]
            changed_market = mkt_hash != snapshot.last_raw_market_hash
            changed_feat = feat_hash != snapshot.last_feature_hash
            result["stages"]["MARKET"] = {
                "changed": changed_market,
                "hash": mkt_hash,
            }
            result["stages"]["FEATURES"] = {
                "changed": changed_feat,
                "hash": feat_hash,
            }
            if not changed_market:
                result["frozen_at"] = "MARKET"
                return result
            if not changed_feat:
                result["frozen_at"] = "FEATURES"
                return result
            x_vec, _ = build_vector_fn(fv)
            _b = get_bundle_fn()
            if _b is None:
                result["frozen_at"] = "MODEL_INPUT"
                result["error"] = "bundle not initialized"
                return result
            x_scaled = _b.scaler.transform(np.array(x_vec, dtype=np.float32).reshape(1, -1))
            model_input_hash = hashlib.sha1(x_scaled.tobytes()).hexdigest()[:16]
            result["stages"]["MODEL_INPUT"] = {
                "changed": model_input_hash != snapshot.last_model_input_hash,
                "hash": model_input_hash,
            }
            probs = run_inference_fn(x_scaled)
            probs_list = probs.cpu().numpy().flatten().tolist()
            model_output_hash = hashlib.sha1(
                ("|".join(f"{v:.8g}" for v in probs_list)).encode()
            ).hexdigest()[:16]
            result["stages"]["MODEL_OUTPUT"] = {
                "changed": model_output_hash != snapshot.last_model_output_hash,
                "hash": model_output_hash,
                "probs": probs_list,
            }
            for stage in ("MARKET", "FEATURES", "MODEL_INPUT", "MODEL_OUTPUT"):
                if not result["stages"][stage]["changed"]:
                    result["frozen_at"] = stage
                    break
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
            result["frozen_at"] = result["frozen_at"] or "UNKNOWN"
        return result
