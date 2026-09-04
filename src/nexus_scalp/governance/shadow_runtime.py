"""
Governance Shadow Runtime
=========================
TASK-6 / CHG-0003 (spec 10 / 11 / 12 / 13 / 15 / 25).

A thin, failure-isolated wrapper around the PHASE 11 ChallengerRuntime that
adds the TASK-6 governance guarantees:

  * SAME-INPUT ALIGNMENT (spec 8): the Challenger never sees a different
    tick/bar; its input is derived from the Champion's live vector with the
    canonical 50D -> 60D/72D extension (governance.alignment).
  * LATENCY GOVERNANCE (spec 12): champion latency, challenger latency and
    total comparison latency are measured per comparison.
  * FAILURE ISOLATION (spec 11): every challenger fault (crash, timeout,
    invalid probability, schema mismatch) is caught, telemetried as
    [MODEL_SHADOW] event=FAILURE_ISOLATED and NEVER propagated to the
    trading path.
  * EXECUTION ISOLATION (spec 10 / property 8): this module imports no
    adapter, no order manager, no risk engine. There is no code path from a
    shadow prediction to an order.
  * DETERMINISM (spec 15 / TEST-LG-15): inference is torch.inference_mode;
    identical input -> identical prediction.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.governance.alignment import (
    challenger_input_for,
    feature_parity,
    news_context_hash,
)
from nexus_scalp.governance.models import GovernanceEvent, GovernanceStage
from nexus_scalp.governance.store import GovernanceStore
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.shadow.challenger import ChallengerRuntime

logger = get_logger("nexus_scalp.governance.shadow_runtime")

#: Latency budget for a shadow inference (ms). Exceeding it marks the
#: comparison SHADOW_TIMEOUT but NEVER blocks or delays the Champion path.
SHADOW_LATENCY_BUDGET_MS: float = 50.0

#: Bounded in-memory decision window (spec 13: no unbounded backlog).
MAX_INMEMORY_DECISIONS: int = 2000


class GovernanceShadowRuntime:
    """Bounded, isolated shadow runner with parity + latency telemetry."""

    def __init__(
        self,
        runtime: ChallengerRuntime,
        store: GovernanceStore | None = None,
        latency_budget_ms: float = SHADOW_LATENCY_BUDGET_MS,
    ) -> None:
        self.runtime = runtime
        self.store = store
        self.latency_budget_ms = float(latency_budget_ms)
        self.ref = runtime.ref
        #: Telemetry counters (spec 11 / 13 / 36)
        self.comparisons: int = 0
        self.errors: int = 0
        self.dropped: int = 0
        self.timeouts: int = 0
        self.invalid_probability: int = 0
        self.schema_mismatches: int = 0
        self.latency_ms: list[float] = []
        self._total_ms: float = 0.0
        self._max_ms: float = 0.0
        #: bounded decision window for drift/calibration aggregation
        self._recent: list[dict[str, Any]] = []
        self.last_comparison: dict[str, Any] | None = None
        self.last_error: str = ""

    # ------------------------------------------------------------------
    # Single comparison (the only entry the engine calls)
    # ------------------------------------------------------------------

    def compare(
        self,
        *,
        champion_vector: list[float],
        reference_vector: list[float] | None,
        news_context: dict[str, Any] | None,
        champion_ref: dict[str, Any],
        champion_action: str,
        champion_confidence: float,
        champion_probabilities: list[float],
        timestamp: datetime | None = None,
        symbol: str = "XAUUSD",
        timeframe: str = "M1",
        regime: str = "UNKNOWN",
        session: str = "ALL",
        run_id: str = "",
        decision_id: str = "",
        champion_latency_ms: float = 0.0,
        feature_context_id: str = "",
        extras_60d: list[float] | None = None,
    ) -> dict[str, Any]:
        """Runs one parallel comparison. ALWAYS returns a dict (never
        raises): a challenger fault is FAILURE_ISOLATED, never propagated."""
        started = time.perf_counter()
        ts = timestamp or datetime.now(UTC)
        comparison_id = f"cmp_{uuid.uuid4().hex[:16]}"
        news_id = news_context_hash(news_context)
        challenger_action: str = "N/A"
        challenger_conf: float = 0.0
        challenger_probs: list[float] = []
        latency_challenger_ms: float = -1.0
        valid: bool = True
        invalid_reason: str = ""
        alignment: str = ""

        # ---- 1. same-input alignment (spec 8 / 5) ----
        try:
            chal_vector, alignment = challenger_input_for(
                champion_vector,
                champion_schema_id=str(champion_ref.get("feature_schema_id", "scalp_v1")),
                challenger_schema_id=str(self.ref.feature_schema_id if self.ref else ""),
                challenger_dimension=int(self.ref.feature_dimension if self.ref else 0),
                news_context=news_context,
                extras_60d=extras_60d,
            )
        except Exception as e:
            valid = False
            alignment = "NONE"
            invalid_reason = f"input alignment failed: {e}"
            self.schema_mismatches += 1
            self._record_failure("SCHEMA_MISMATCH", invalid_reason, champion_ref, ts, run_id)

        # ---- 2. challenger inference (latency-bounded) ----
        if valid:
            try:
                t0 = time.perf_counter()
                result = self.runtime.infer(chal_vector)
                latency_challenger_ms = (time.perf_counter() - t0) * 1000.0
                challenger_action = str(result.get("action", "N/A"))
                challenger_conf = float(result.get("confidence", 0.0) or 0.0)
                challenger_probs = [float(p) for p in result.get("probabilities", [])]
                if not 0.0 <= challenger_conf <= 1.0:
                    valid = False
                    invalid_reason = "invalid probability (out of range)"
                    self.invalid_probability += 1
                    self._record_failure(
                        "PREDICTION_INVALID", invalid_reason, champion_ref, ts, run_id
                    )
            except Exception as e:
                valid = False
                invalid_reason = f"challenger inference failed: {e}"
                self.errors += 1
                self.last_error = str(e)
                self._record_failure(
                    "SHADOW_INFERENCE_FAILED", invalid_reason, champion_ref, ts, run_id
                )

        # ---- 3. latency governance (spec 12) ----
        total_ms = (time.perf_counter() - started) * 1000.0
        if valid and latency_challenger_ms > self.latency_budget_ms:
            self.timeouts += 1
            valid = False
            invalid_reason = f"challenger exceeded latency budget {latency_challenger_ms:.1f}ms"
            self._record_failure("SHADOW_TIMEOUT", invalid_reason, champion_ref, ts, run_id)

        # ---- 4. feature parity vs offline reference (spec 6) ----
        parity = feature_parity(champion_vector, reference_vector)
        parity_ok = bool(parity.get("parity_ok", False))
        if not parity_ok and parity.get("state") == "MISMATCH":
            valid = False
            invalid_reason = "feature parity failure (live vs replay)"
            self._record_failure("FEATURE_PARITY_FAILURE", invalid_reason, champion_ref, ts, run_id)

        agreement = valid and champion_action == challenger_action

        comparison = {
            "comparison_id": comparison_id,
            "run_id": run_id,
            "decision_id": decision_id,
            "timestamp": ts,
            "symbol": symbol,
            "timeframe": timeframe,
            "regime": regime,
            "session": session,
            "champion_model_id": champion_ref.get("model_id", ""),
            "champion_version": champion_ref.get("model_version", ""),
            "challenger_model_id": self.ref.model_id if self.ref else "",
            "challenger_version": self.ref.model_version if self.ref else "",
            "feature_schema_id": champion_ref.get("feature_schema_id", "scalp_v1"),
            "feature_context_id": feature_context_id,
            "news_context_id": news_id,
            "alignment": alignment,
            "champion_action": champion_action,
            "champion_confidence": champion_confidence,
            "champion_probabilities": champion_probabilities,
            "challenger_action": challenger_action,
            "challenger_confidence": challenger_conf,
            "challenger_probabilities": challenger_probs,
            "agreement": agreement,
            "valid": valid,
            "invalid_reason": invalid_reason,
            "feature_parity_max_abs": parity.get("max_abs_diff", -1.0),
            "feature_parity_mean_abs": parity.get("mean_abs_diff", -1.0),
            "feature_parity_mismatch": parity.get("mismatch_count", -1),
            "latency_champion_ms": champion_latency_ms,
            "latency_challenger_ms": latency_challenger_ms,
            "total_comparison_ms": total_ms,
            "simulated": True,
        }

        self.comparisons += 1
        self._total_ms += total_ms
        self._max_ms = max(self._max_ms, total_ms)
        self.latency_ms.append(latency_challenger_ms if latency_challenger_ms >= 0 else total_ms)
        if len(self.latency_ms) > 500:
            self.latency_ms = self.latency_ms[-500:]
        self.last_comparison = comparison
        self._recent.append(comparison)
        if len(self._recent) > MAX_INMEMORY_DECISIONS:
            self._recent = self._recent[-MAX_INMEMORY_DECISIONS:]
        if not valid:
            self.dropped += 1

        # ---- 5. bounded persistence (spec 14: canonical row, no raw ticks) ----
        if self.store is not None and valid:
            try:
                self.store.save_shadow_comparison(comparison)
            except Exception as e:
                logger.error("[MODEL_SHADOW] comparison persist failed (isolated)", error=str(e))

        logger.debug(
            "[MODEL_SHADOW] event=COMPARISON",
            comparison_id=comparison_id,
            champion=champion_action,
            challenger=challenger_action,
            agreement=agreement,
            total_ms=round(total_ms, 2),
        )
        return comparison

    # ------------------------------------------------------------------
    # Telemetry / summaries
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        return {
            "model_id": self.ref.model_id if self.ref else "",
            "model_version": self.ref.model_version if self.ref else "",
            "schema_id": self.ref.feature_schema_id if self.ref else "",
            "input_dimension": self.ref.feature_dimension if self.ref else 0,
            "artifact_hash": self.ref.artifact_hash if self.ref else "",
            "state": "SHADOW",
            "comparisons": self.comparisons,
            "errors": self.errors,
            "dropped": self.dropped,
            "timeouts": self.timeouts,
            "invalid_probability": self.invalid_probability,
            "schema_mismatches": self.schema_mismatches,
            "avg_latency_ms": round(self._total_ms / self.comparisons, 3)
            if self.comparisons
            else 0.0,
            "max_latency_ms": round(self._max_ms, 3),
            "p95_latency_ms": round(
                sorted(self.latency_ms)[int(len(self.latency_ms) * 0.95) - 1], 3
            )
            if len(self.latency_ms) >= 20
            else round(self._max_ms, 3),
            "last_update": (
                self.last_comparison.get("timestamp").isoformat()
                if self.last_comparison
                and hasattr(self.last_comparison.get("timestamp"), "isoformat")
                else ""
            ),
            "last_error": self.last_error,
        }

    def recent_window(self, limit: int = 300) -> list[dict[str, Any]]:
        return self._recent[-limit:]

    # ------------------------------------------------------------------

    def _record_failure(
        self,
        code: str,
        reason: str,
        champion_ref: dict[str, Any],
        ts: datetime,
        run_id: str,
    ) -> None:
        self.errors += 1
        self.last_error = reason
        logger.warning(
            "[MODEL_SHADOW] event=FAILURE_ISOLATED",
            candidate_id=self.ref.model_id if self.ref else "",
            stage="SHADOW",
            error_code=code,
        )
        if self.store is not None:
            with contextlib.suppress(Exception):
                self.store.record_event(
                    GovernanceEvent(
                        event_id=f"ev_{uuid.uuid4().hex[:16]}",
                        event=code,
                        stage=GovernanceStage.SHADOW,
                        model_id=self.ref.model_id if self.ref else "",
                        model_version=self.ref.model_version if self.ref else "",
                        schema_id=self.ref.feature_schema_id if self.ref else "",
                        correlation_id=run_id,
                        error_code=code,
                        error_type="SHADOW_ISOLATED",
                        reason=reason,
                        payload={"champion": champion_ref},
                    )
                )
