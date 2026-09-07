"""TickPipeline — post-policy stages of the live tick pipeline.

P1 seam L7 (god-file decomposition, extraction 7 of the live-engine wave).
The stages between signal-policy and the L2 decision executor leave
``application/live_engine.py`` verbatim (behavior-preserving extraction):

    1. PHASE 08 experience-intelligence gate (down-rank / NO_TRADE only)
    2. PHASE 09 suitability / WARN intelligence gate (downgrade only)
    3. PHASE 12 news intelligence gate (bounded, isolated, never forces)
    4. BUG-169 terminal outcome emission for pre-dispatch rejections
    5. G29 safety freshness gate (frozen-chain -> NO_TRADE/BLOCKED_BY_STALE)
    6. G29 freshness instrumentation (monotonic clocks + change hashes —
       observational only, never gates)
    7. PHASE 11 Challenger shadow recording + BUG-105 70D observation hook
    8. Live SMC chart-overlay snapshot for the web canvas (cached)

State ownership: all throttles/counters/hashes/web-snapshot state stay at
the composition root (LiveEngine) and are reached through ``self.om`` —
the freshness counters are cross-service coordination state. This module
owns the STAGE LOGIC only. Every stage is failure-isolated per its contract.
"""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from typing import Any

import numpy as np

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.application.live.tick_pipeline")


class TickPipeline:
    """Post-policy pipeline stages (composition root: LiveEngine)."""

    def __init__(self, om: Any) -> None:
        self.om = om

    def run_post_policy_stages(
        self,
        tick: Any,
        account: Any,
        fv: Any,
        probs: Any,
        regime_state: Any,
        proposal: Any,
        active_positions: list[Any],
        current_pos_count: int,
        completed_bars: list[Any],
        is_new_bar: bool,
    ) -> Any:
        """Runs gates + freshness instrumentation + shadow recording + chart
        overlays. Returns the final proposal (policy_decision input for L2)."""

        # =================================================================
        # PHASE 08 PRE-TRADE EXPERIENCE INTELLIGENCE GATE
        # -----------------------------------------------------------------
        # Runs AFTER the signal policy and BEFORE risk sizing / dispatch, so
        # a rejection here happens strictly before any order placement. The
        # gate can only down-rank or convert to NO_TRADE; it never sizes,
        # places or modifies an order, and it never blocks the tick loop
        # (score lookups are TTL-cached and rate-limited).
        # =================================================================
        proposal, exp_decision = self.om.experience_engine.evaluate_proposal(
            proposal=proposal,
            feature_vector=fv,
            regime_state=regime_state,
        )
        self.om._last_experience_decision = exp_decision

        # =================================================================
        # PHASE 09 PRE-TRADE INTELLIGENCE GATE (suitability / WARN tier)
        # -----------------------------------------------------------------
        # Layers a bounded suitability + WARN decision on top of the Phase 08
        # gate. It can only DOWNGRADE (WARN / PENALIZE / REJECT), never
        # upgrade; rejection is a NO_TRADE before risk sizing / dispatch.
        # =================================================================
        proposal, exp_decision, suitability = self.om.intelligence_gate.evaluate(
            proposal=proposal, fv=fv, regime=regime_state
        )
        self.om._last_experience_decision = exp_decision
        self.om._last_suitability_verdict = suitability

        # =================================================================
        # PHASE 12: NEWS INTELLIGENCE GATE (bounded, isolated, optional)
        # -----------------------------------------------------------------
        # Applies a BOUNDED confidence adjustment from the current news
        # context. News can NEVER force a direction: alignment gives at
        # most max_confidence_boost (default 0.05), conflict lowers
        # confidence by at most max_confidence_penalty (default 0.10).
        # Position-protection actions are never gated; when the news
        # subsystem is disabled/unavailable this is a pure no-op.
        # =================================================================
        if self.om._news_enabled and self.om.news_gate is not None:
            try:
                news_ctx = self.om.news_engine.current_context()
                news_verdict = self.om.news_gate.evaluate(
                    context=news_ctx,
                    proposal_action=(
                        proposal.action.value
                        if hasattr(proposal.action, "value")
                        else str(proposal.action)
                    ),
                    strategy_direction=self.om._news_strategy_direction(proposal),
                    proposal_confidence=float(getattr(proposal, "confidence", 0.0) or 0.0),
                    regime_aligned=True,
                )
                self.om._last_news_gate = news_verdict
                adjustment = news_verdict.confidence_adjustment
                if adjustment != 0.0:
                    proposal = proposal.model_copy(
                        update={
                            "confidence": round(
                                max(0.0, min(1.0, proposal.confidence + adjustment)), 4
                            )
                        }
                    )
                logger.debug(
                    "[NEWS_GATE] decision=%s strategy=%s adjustment=%+.4f",
                    news_verdict.decision,
                    news_verdict.strategy_direction,
                    adjustment,
                )
            except Exception as news_gate_err:
                # News must never disturb trading: failure = no-op.
                self.om._last_news_gate = None
                logger.debug(
                    "[NEWS_GATE] event=FAILED (isolated, no-op)", error=str(news_gate_err)
                )

        self.om.audit.log_signal(proposal)

        # =================================================================
        # BUG-169: TERMINAL OUTCOME FOR PRE-DISPATCH REJECTIONS.
        # -----------------------------------------------------------------
        # The Phase 08/09 gates convert an ENTRY proposal to NO_TRADE
        # BEFORE any dispatch. The experience row for that decision was
        # already written (_record_decision_experience), so without a
        # terminal outcome it hangs in the ledger as MISSING_OUTCOME
        # forever (295 rows / 22k log lines on 2026-08-31). Emit an
        # explicit NOT_DISPATCHED outcome for entry proposals that the
        # pre-trade stack rejected. Idempotent via the ledger's unique
        # key; failure-isolated (learning never disturbs trading).
        # =================================================================
        if (
            proposal.action == ActionType.NO_TRADE
            and str(getattr(proposal, "model_action", "") or "") != "NO_TRADE"
            and proposal.decision_stage
            in ("EXPERIENCE_INTELLIGENCE_GATE", "TRADE_INTELLIGENCE_GATE")
            and self.om.experience_engine is not None
        ):
            try:
                from nexus_scalp.execution.terminal_outcome import (
                    emit_terminal_pending_outcome,
                )
                from nexus_scalp.experience.lifecycle import (
                    DecisionLifecycle as DecisionLifecycleAlias,
                )

                emit_terminal_pending_outcome(
                    experience_engine=self.om.experience_engine,
                    request_id=str(getattr(proposal, "request_id", "") or ""),
                    state=DecisionLifecycleAlias.NOT_DISPATCHED,
                    detail=f"pre-dispatch gate rejection: {proposal.rejection_reason or proposal.reason_code}",
                )
            except Exception as _term_err:
                logger.debug(
                    "[TERMINAL_OUTCOME] pre-dispatch emission skipped",
                    error=str(_term_err),
                )

        # =====================================================================
        # NEXUS-LIVE-INFERENCE-FROZEN-STATE-G29: SAFETY FRESHNESS GATE
        # ---------------------------------------------------------------------
        # Runs AFTER all model/experience/news/intelligence gates. If the
        # feature->inference->decision chain is proven STALE (frozen), the
        # proposal is converted to NO_TRADE / BLOCKED_BY_STALE so a frozen
        # intelligence state can NEVER masquerade as a live BUY/SELL.
        # is a pure downgrade to NO_TRADE; it relaxes NO existing guard and
        # fabricates NO confidence. It is the only production touchpoint of
        # the freshness model.
        # =====================================================================
        proposal, _fresh_blocked = self.om.live_freshness_gate(proposal)
        if _fresh_blocked:
            logger.warning(
                "[FRESHNESS_GATE] event=BLOCKED reason=BLOCKED_BY_STALE "
                "(inference chain frozen; proposal downgraded to NO_TRADE)"
            )

        # =====================================================================
        # NEXUS-LIVE-INFERENCE-FROZEN-STATE-G29: FRESHNESS INSTRUMENTATION
        # ---------------------------------------------------------------------
        # Purely OBSERVATIONAL bookkeeping at the live sync point. Records
        # the authoritative stage timestamps, bumps monotonic sequence ids
        # ONLY when the substantive input/output actually changed (so the
        # UI/QA can prove inference progressed without trusting
        # state_version), and stores change-detection hashes. This does NOT
        # gate or block trading; gates live in `live_freshness_gate()`.
        # =====================================================================
        import hashlib

        now_utc = datetime.now(UTC)
        # Monotonic tick timestamp: strictly increasing ms of the newest
        # market tick on the live path.
        tick_ms = int(tick.timestamp.timestamp() * 1000.0)
        if tick_ms > self.om._monotonic_tick_ms:
            self.om._monotonic_tick_ms = tick_ms
            self.om._last_tick_timestamp = tick.timestamp
            self.om._tick_sequence += 1
            self.om._market_updates_total += 1
        # Deterministic raw-market hash (price/spread/regime, NOT timestamp)
        raw_market = (
            f"{tick.bid:.5f}|{tick.ask:.5f}|{tick.last:.5f}|"
            f"{getattr(tick, 'spread', '')}|{regime_state}"
        )
        raw_market_hash = hashlib.sha1(raw_market.encode()).hexdigest()[:16]
        # Feature change detection
        feat_vals = list(getattr(fv, "to_tensor_input", lambda: [])())
        feature_hash = hashlib.sha1(
            ("|".join(f"{v:.6g}" for v in feat_vals)).encode()
        ).hexdigest()[:16]
        self.om.last_feature_update = now_utc
        self.om._feature_builds_total += 1
        if feature_hash != self.om._last_feature_hash:
            self.om._feature_sequence += 1
            self.om._last_feature_hash = feature_hash
        # Model input + output change detection
        with self.om._bundle_lock:
            _b = self.om._bundle
        try:
            if _b is not None:
                x_np = np.array(feat_vals, dtype=np.float32).reshape(1, -1)
                x_scaled = _b.scaler.transform(x_np)
                model_input_hash = hashlib.sha1(x_scaled.tobytes()).hexdigest()[:16]
            else:
                model_input_hash = ""
        except Exception:
            model_input_hash = ""
        probs_list = probs.cpu().numpy().flatten().tolist() if probs is not None else []
        model_output_hash = hashlib.sha1(
            ("|".join(f"{v:.8g}" for v in probs_list)).encode()
        ).hexdigest()[:16]
        self.om.last_inference_timestamp = now_utc
        self.om.last_successful_inference = now_utc
        self.om._inference_runs_total += 1
        if model_input_hash and model_input_hash != self.om._last_model_input_hash:
            self.om._inference_sequence += 1
            self.om._last_model_input_hash = model_input_hash
        if model_output_hash != self.om._last_model_output_hash:
            self.om._last_model_output_hash = model_output_hash
        self.om._last_raw_market_hash = raw_market_hash
        # Decision stage
        self.om.last_decision_timestamp = getattr(proposal, "generated_at", now_utc)
        self.om._decision_updates_total += 1
        self.om._decision_sequence += 1

        # Update synchronization properties for the Web backend
        self.om._last_tick = tick
        self.om._last_fv = fv
        self.om._last_regime_state = regime_state
        self.om._last_probs = probs
        self.om._last_proposal = proposal

        # =================================================================
        # PHASE 11: CHALLENGER SHADOW RECORDING (SAME live feature vector)
        # -----------------------------------------------------------------
        # Records the Champion's real decision and runs the Challenger on
        # the IDENTICAL feature vector used by the live path. Purely
        # observational: the Challenger produces a hypothetical proposal
        # only and can never place an order. Bounded + failure-isolated.
        # =================================================================
        self.om._record_shadow_decision(
            tick=tick,
            fv=fv,
            regime_state=regime_state,
            proposal=proposal,
        )

        # =================================================================
        # TASK-05-70D-SHADOW: 70D OBSERVATION HOOK (observability ONLY)
        # -----------------------------------------------------------------
        # Independent of the 50D shadow gate (BUG-105): runs on EVERY tick
        # once a validated 70D candidate is attached and enabled, building
        # the 70D vector from the SAME canonical state (50D + news +
        # liquidity). A failure here is isolated (INV-018).
        # =================================================================
        self.om._record_shadow70_observation(
            tick=tick,
            fv=fv,
            proposal=proposal,
        )

        # (Liquidity governor is pre-warmed on every tick/new-bar above)

        # Extract and update real SMC overlays for the live chart canvas.
        # Recomputed ONLY when the completed-bar series changes (new bar)
        # or on the first tick; between bars the series cannot change, so
        # the O(n) extraction + 900-bar serialization is CACHED (measured
        # ~6-7ms/tick at 900 bars vs ~0 for the cached path).
        if getattr(self, "server_state", None) is not None:
            snapshot_key = completed_bars[-1].timestamp if completed_bars else None
            # Also refresh on a 10s cadence so the forming bar's live
            # OHLC updates reach the UI even without a bar close.
            if (
                self.om._last_chart_snapshot_key is None
                or snapshot_key != self.om._last_chart_snapshot_key
                or (time.time() - self.om._last_chart_snapshot_time) >= 10.0
            ):
                real_overlays = self.om.signal_policy.extract_live_chart_overlays(
                    completed_bars=completed_bars, atr_val=fv.atr_m1
                )
                bars_list = []
                for b in completed_bars[-900:]:
                    bars_list.append(
                        {
                            "time": b.timestamp.isoformat(),
                            "open": b.open,
                            "high": b.high,
                            "low": b.low,
                            "close": b.close,
                            "volume": b.tick_volume,
                            "is_complete": True,
                        }
                    )
                forming_bar = self.om.aggregator.get_current_forming_bar()
                if forming_bar:
                    bars_list.append(
                        {
                            "time": forming_bar.timestamp.isoformat(),
                            "open": forming_bar.open,
                            "high": forming_bar.high,
                            "low": forming_bar.low,
                            "close": forming_bar.close,
                            "volume": forming_bar.tick_volume,
                            "is_complete": False,
                        }
                    )
                self.om._last_chart_snapshot_key = snapshot_key
                self.om._last_chart_snapshot_bars = bars_list
                self.om._last_chart_snapshot_overlays = real_overlays
                self.om._last_chart_snapshot_time = time.time()
                self.om.server_state.update_live_visuals(bars_list, real_overlays)
        return proposal
