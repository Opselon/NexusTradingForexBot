"""InferenceService — live feature-vector assembly, validation, model inference.

P1 seam L6 (god-file decomposition, extraction 6 of the live-engine wave):
the inference path leaves ``application/live_engine.py`` verbatim
(behavior-preserving extraction). Responsibilities moved: schema-gated
feature validation (50D/70D), canonical live tensor assembly (Base 0..49 +
News 50..59 + Liquidity 60..69, causal VALID-only), and the staged-latency
model forward with nan_to_num guarding + latency-regression observation.

State ownership: bundle / bundle_lock / latency counters stay at the
composition root (LiveEngine), reached via ``self`` (the engine surface); this module
owns the INFERENCE LOGIC only. Never blocks the tick loop.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import torch

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.application.live.inference")


class InferenceService:
    """Feature assembly + validation + model inference (composition root)."""

    def __init__(self, om: Any) -> None:
        self.om = om

    def validate_feature_vector(self, features: Sequence[float], context: str) -> list[float]:
        """Schema-gated validation dispatching to 50D or 70D gate."""
        eff = int(self.effective_feature_dim)
        if eff == 70 and len(features) == 70:
            from nexus_scalp.features.schema_contract import (
                feature_schema_hash,
                validate_70d_vector,
            )

            return validate_70d_vector(
                list(features), schema_hash=feature_schema_hash(), context=context
            )
        return self.__class__._validate_50d_tensor(features, context=context)

    def build_live_feature_vector(self, fv) -> tuple[list[float], dict[str, float]]:
        """Assembles the canonical live tensor (50D or 70D) for this tick.

        50D CHAMPION (scalp_v1/50D): returns the 50D vector; liquidity is
        never injected. 70D CHAMPION (validated 70D model): assembles
        0..49 Base + 50..59 News + 60..69 Liquidity (causal, VALID only).
        STALE/INVALID liquidity raises so the caller can degrade safely.
        """
        import time as _time

        _t0 = _time.perf_counter()
        base50 = fv.to_tensor_input()
        base50 = self._validate_50d_tensor(base50, context="live_base50")
        _t_base = _time.perf_counter()

        eff_dim = int(self.effective_feature_dim)
        if eff_dim != 70:
            return base50, {
                "feature_ms": round((_t_base - _t0) * 1e3, 3),
                "liquidity_ms": 0.0,
                "news_ms": 0.0,
                "assembly_ms": 0.0,
            }

        # News 10D (indices 50..59): CANONICAL projection of the live context.
        # BUG-190 (fidelity audit): a raw CurrentNewsContext.model_dump() has
        # DIFFERENT key names than the canonical training-frame schema
        # (active_event_count vs active_high_impact_events, bullish_score/
        # bearish_score vs bullish/bearish_pressure, state-as-string vs
        # news_state encoding, novelty absent) - feeding it straight into
        # news_10d_from_context zeroes/loses 4 of 10 slots. The canonical
        # named mapping (vectorize_news_context -> build_news_10, the same
        # mapping shadow70 and the debug feature matrix already use) is the
        # single projection for live inference.
        news10: list[float]
        try:
            from nexus_scalp.shadow.shadow70.news_provider import build_news_10

            news_ctx = None
            if (
                getattr(self, "_news_enabled", False)
                and getattr(self, "news_engine", None) is not None
            ):
                try:
                    news_ctx = self.news_engine.current_context()
                except Exception:
                    news_ctx = None
            if news_ctx is None:
                news10 = [0.0] * 10
            else:
                from nexus_scalp.governance.alignment import vectorize_news_context

                news10, _ = build_news_10(vectorize_news_context(news_ctx))
        except Exception:
            news10 = [0.0] * 10
        _t_news = _time.perf_counter()

        # Liquidity 10D (indices 60..69): real, causal, causality-checked.
        liq10: list[float] | None = None
        gov = getattr(self, "liquidity_governor", None)
        if gov is not None:
            snap = getattr(gov, "last_snapshot", None)
            causal = getattr(gov, "causal_state", lambda: "INVALID")()
            if snap is not None and causal == "VALID":
                try:
                    vec = list(snap.features)
                    if len(vec) == 10 and all(-3.0 <= float(v) <= 3.0 for v in vec):
                        liq10 = [float(v) for v in vec]
                except Exception:
                    liq10 = None
        _t_liq = _time.perf_counter()

        if liq10 is None:
            raise RuntimeError(
                "70D inference requested but liquidity snapshot is not VALID "
                "(stale/missing) - refusing to feed fabricated values into the 70D model"
            )

        try:
            from nexus_scalp.features.liquidity_runtime import build_70d_vector

            vec70 = build_70d_vector(base50, family_10=news10, liquidity_10=liq10)
        except Exception as e:
            raise RuntimeError(f"70D assembly failed: {e}") from e
        _t_asm = _time.perf_counter()
        try:
            from nexus_scalp.features.schema_contract import (
                feature_schema_hash,
                validate_70d_vector,
            )

            validate_70d_vector(vec70, schema_hash=feature_schema_hash(), context="live_70d")
        except Exception as e:
            raise RuntimeError(f"70D contract validation failed: {e}") from e
        return vec70, {
            "feature_ms": round((_t_base - _t0) * 1e3, 3),
            "news_ms": round((_t_news - _t_base) * 1e3, 3),
            "liquidity_ms": round((_t_liq - _t_news) * 1e3, 3),
            "assembly_ms": round((_t_asm - _t_liq) * 1e3, 3),
        }

    # ==================================================================
    # NEXUS-LIVE-INFERENCE-FROZEN-STATE-G29: LIVE-FRESHNESS TRUTH MODEL
    # Delegates to LiveFreshnessService (Cluster 3 extraction).
    # ==================================================================

    def infer_probabilities(self, fv) -> torch.Tensor:
        import time as _time

        # --- honest staged latency trace (monotonic, TASK: latency forensics) ---
        from nexus_scalp.features.latency_tracer import LatencyStage, LatencyTracer

        _trace = LatencyTracer(prediction_id=f"inf_{_time.perf_counter_ns()}")
        _trace.mark(LatencyStage.T0_MARKET_EVENT)
        _trace.mark(LatencyStage.T1_FEATURE_START)

        # BUG-125: Canonical live tensor: 50D for the production Champion,
        # 70D when a validated 70D model is hot-swapped. Assembly does
        # per-family telemetry bookkeeping and validates the liquidity snapshot.
        try:
            x_vec, asm_timings = self._build_live_feature_vector(fv)
            self._last_live_tensor_dim = len(x_vec)
            self._last_live_tensor_schema = self.effective_feature_schema_id
            self._last_70d_assembly_timings = asm_timings
        except RuntimeError as asm_err:
            if int(self.effective_feature_dim) == 70:
                # OBS-PERF-RESILIENCE: a 70D assembly failure BLOCKS inference
                # for this tick. That DEGRADED->BLOCKED transition must be
                # visible in telemetry, not only in a log line: bump the
                # failure gauge and emit an incident event (bounded by the
                # incident pipeline's own rate limiting).
                self._inference_failures_total = getattr(self, "_inference_failures_total", 0) + 1
                self.emit_incident_telemetry(
                    event_type="INFERENCE_BLOCKED_70D_ASSEMBLY",
                    component="inference",
                    error_code="FEATURE_UNAVAILABLE",
                    severity="HIGH",
                    correlation_id="tick-pipeline",
                )
                logger.warning(
                    "[INFERENCE] 70D assembly failed - inference blocked for this tick",
                    error=str(asm_err),
                )
                self._last_70d_assembly_timings = {}
                self._last_live_tensor_dim = 70
                self._last_live_tensor_schema = self.effective_feature_schema_id
                raise
            # Non-70D defensive fallback
            logger.warning(
                "[INFERENCE] feature assembly failed - falling back to 50D", error=str(asm_err)
            )
            x_vec = self._validate_50d_tensor(
                fv.to_tensor_input(), context="live_inference_fallback_50d"
            )
            self._last_70d_assembly_timings = {}
            self._last_live_tensor_dim = len(x_vec)
            self._last_live_tensor_schema = "scalp_v1"
        _trace.mark(LatencyStage.T2_FEATURE_DONE)
        x_np = np.array(x_vec, dtype=np.float32).reshape(1, -1)

        with self._bundle_lock:
            bundle = self._bundle
        if bundle is None:
            raise RuntimeError("Model bundle not initialized")

        x_np = bundle.scaler.transform(x_np)
        _trace.mark(LatencyStage.T3_SCALER_DONE)
        seq_x = None
        try:
            seq_x = self._maybe_build_live_sequence_tensor(
                x_scaled_now=x_np[0].tolist(), bar_ts=None
            )
        except Exception:
            seq_x = None
        if seq_x is not None:
            x = seq_x
        else:
            x = torch.tensor(x_np, dtype=torch.float32)
        x = torch.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0)
        _trace.mark(LatencyStage.T4_TENSOR_DONE)

        # Debug/forensics: keep the exact model input the live path consumed
        # (post-scaler, pre-softmax). Read-only observability (INV-018);
        # never used for execution. SAMPLED (every 64th) to keep the hot
        # path allocation-free; full capture available in debug mode.
        # Debug/forensics input capture: sampled (every 64th) to keep
        # the hot path allocation-free; full capture in debug mode.
        _dbg_every = getattr(self, "_latency_dbg_every", 64) or 64
        try:
            if (self._inference_count % _dbg_every) == 0:
                self._last_model_input_tensor = x.detach().cpu().numpy().reshape(-1).tolist()
            else:
                self._last_model_input_tensor = None
        except Exception:
            self._last_model_input_tensor = None

        # HONEST Model Forward stage (T5..T6) — nothing else in between.
        _trace.mark(LatencyStage.T5_MODEL_START)
        bundle.model.eval()
        # Latency fix: intra-op multithreading on a 267k-param net is pure
        # overhead under host contention (~60ms vs 0.25ms single-threaded,
        # same logits — verified). Pin to 1 thread for the forward and
        # restore; safe under the bundle lock (no concurrent model call).
        _prior_threads = torch.get_num_threads()
        torch.set_num_threads(1)
        try:
            with torch.inference_mode():
                logits = bundle.model(x, return_logits=True)
                # MODEL_CLASS_CONTRACT v1 (Fix #3): WAIT (index 3) is a legacy
                # policy bridge — it is MASKED before softmax so it cannot
                # steal probability mass from the trained 3 classes.  3-wide
                # logits pass through unchanged; 4-wide logits have WAIT
                # forced to -1e4 (≈0 prob) while keeping the on-disk 4-head
                # geometry intact.  No shape change, no calibration drift on
                # the trained slice.
                from nexus_scalp.model_lifecycle.model_class_contract import (
                    masked_softmax,
                )

                probs = masked_softmax(logits)
        finally:
            torch.set_num_threads(_prior_threads)
        _trace.mark(LatencyStage.T6_MODEL_DONE)

        self._inference_count = getattr(self, "_inference_count", 0) + 1
        _trace.mark(LatencyStage.T7_DECODE_DONE)
        _trace.mark(LatencyStage.T8_CONFIDENCE_DONE)
        _trace.mark(LatencyStage.T10_PUBLISHED)
        self._last_inference_latency_ms = _trace.model_ms()
        # keep the honest staged breakdown for the API/UI
        self._last_latency_breakdown = _trace.to_dict()
        # OBS-PERF-RESILIENCE: feed the bounded rolling window and alert once
        # per regression epoch. Fully exception-isolated — observability
        # failures can never disturb inference (INV-018).
        try:
            detector = self._latency_regression
            if detector is None:
                from nexus_scalp.observability.latency_regression import (
                    LatencyRegressionDetector,
                )

                detector = self._latency_regression = LatencyRegressionDetector()
            detector.observe_breakdown(self._last_latency_breakdown)
            if detector.should_alert():
                p95 = detector.summary().get("e2e_ms", {}).get("p95_ms")
                logger.warning(
                    "[LATENCY_REGRESSION] event=E2E_P95_REGRESSED "
                    "p95_ms=%s budget_p95_ms=%s epochs=%s",
                    p95,
                    detector.summary().get("budget_p95_ms"),
                    detector.regression_epochs_total,
                )
                self.emit_incident_telemetry(
                    event_type="INFERENCE_LATENCY_REGRESSION",
                    component="inference",
                    error_code="SLOW_INFERENCE",
                    severity="MEDIUM",
                    correlation_id="latency-watch",
                )
        except Exception as _lat_err:  # never disturb the hot path
            logger.debug("[LATENCY_REGRESSION] observe failed", error=str(_lat_err))
        self._last_model_forward_ms = _trace.model_ms()
        self._last_feature_ms = _trace.feature_ms()
        self._last_e2e_ms = _trace.e2e_ms()
        return probs

    # ------------------------------------------------------------------
    # P1 seam L1: shadow recording delegates (implementation moved to
    # application/live/shadow_recorder.py; the unbound-method contract
    # `LiveEngine._record_shadow_decision(harness, ...)` used by tests is
    # preserved — `self` may be any object with the engine attribute surface).
    # ------------------------------------------------------------------
