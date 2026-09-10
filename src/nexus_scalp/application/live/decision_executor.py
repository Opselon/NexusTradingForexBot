"""DecisionExecutor — post-policy decision execution stage of the tick pipeline.

P1 seam L2 (god-file decomposition, extraction 2 of the live-engine wave):
the DECISION EXECUTION stage leaves ``application/live_engine.py`` verbatim
(behavior-preserving extraction). This is the tail of ``_process_tick_pipeline``:
SHADOW observation-only boundary (BUG-212), AI reversal close-then-flip,
new-entry risk sizing + dispatch + setup snapshot lineage, position-lifecycle
actions, intelligent hedging, and survival/equity audit.

State ownership: the composition root (LiveEngine) keeps every mutable field;
the executor reads/writes through ``self.om``. Explicit inputs only.
"""

from __future__ import annotations

import contextlib
from typing import Any

from nexus_scalp.domain.enums import ActionType, ExecutionMode
from nexus_scalp.domain.models import TickData, TradeOrder
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.application.live.decision_executor")


class DecisionExecutor:
    """Executes the post-policy decision stage of the live tick pipeline."""

    def __init__(self, om: Any) -> None:
        # The composition root (LiveEngine). All engine state stays there.
        self.om = om

    def execute_decision_stage(
        self,
        tick: TickData,
        account: Any,
        fv: Any,
        probs: Any,
        regime_state: Any,
        proposal: Any,
        policy_decision: Any,
        active_positions: list[Any],
        current_pos_count: int,
    ) -> None:
        """Runs the BUG-212 shadow boundary, dispatch/lifecycle actions,
        hedging, and survival/equity audit for one processed tick."""
        # =================================================================
        # BUG-212: SHADOW EXECUTION BOUNDARY (observation-only mutations).
        # -----------------------------------------------------------------
        # SHADOW means "live data, live prediction, NO execution". The
        # position-management pass above keeps running (protective
        # observation), but this engine must never MUTATE broker state
        # from the decision path: entries, lifecycle actions, AI
        # reversals and intelligent hedges are all downgraded to logged
        # NO_TRADE observations before any order authority is consulted.
        # The proposal itself stays recorded (audit + experience ledger
        # see the full counterfactual), so shadow evidence is preserved.
        # =================================================================
        if (
            self.om.config.execution.mode == ExecutionMode.SHADOW
            and policy_decision.action != ActionType.NO_TRADE
        ):
            _shadow_action = policy_decision.action
            policy_decision = proposal.model_copy(
                update={
                    "action": ActionType.NO_TRADE,
                    "reason_code": "SHADOW_OBSERVATION_ONLY",
                    "rejection_reason": (
                        f"SHADOW mode is observation-only: {_shadow_action.value} suppressed"
                    ),
                    "final_action": "NO_TRADE",
                    "is_ai_reversal": False,
                    "reversal_action": None,
                }
            )
            logger.info(
                "[SHADOW_BOUNDARY] event=ORDER_MUTATION_SUPPRESSED suppressed_action=%s ticket=%s",
                _shadow_action.value,
                getattr(proposal, "ticket", 0) or 0,
            )
        if policy_decision.action != ActionType.NO_TRADE:
            # ---------------------------------------------------------------
            # AI POSITION REVERSAL: close-then-flip, never stack
            # ---------------------------------------------------------------
            if getattr(policy_decision, "is_ai_reversal", False) or (
                policy_decision.action == ActionType.CLOSE_POSITION
                and "AI_REVERSAL_SIGNAL" in (policy_decision.reason_code or "")
            ):
                reversal_volume = 0.0
                if self.om._symbol_info:
                    reversal_volume = self.om.risk_engine.calculate_volume(
                        entry=policy_decision.proposed_entry,
                        sl=policy_decision.stop_loss,
                        tp=policy_decision.take_profit,
                        account=account,
                        symbol_info=self.om._symbol_info,
                    )
                    reversal_volume = self.om.risk_engine.get_clamped_position_size(
                        volume=reversal_volume,
                        account=account,
                        symbol_info=self.om._symbol_info,
                    )

                success = self.om.order_manager.execute_ai_reversal(
                    decision=policy_decision,
                    volume=reversal_volume,
                    current_tick=tick,
                    symbol_info=self.om._symbol_info,
                )
                logger.info(
                    f"[info] AI REVERSAL EXECUTED ticket={policy_decision.ticket} "
                    f"new_action={getattr(policy_decision.reversal_action, 'value', None)} "
                    f"volume={reversal_volume} success={success}"
                )

            # FOR NEW ENTRY SIGNALS
            elif policy_decision.action in (
                ActionType.BUY,
                ActionType.SELL,
                ActionType.BUY_MARKET,
                ActionType.SELL_MARKET,
                ActionType.BUY_LIMIT,
                ActionType.SELL_LIMIT,
                ActionType.BUY_STOP,
                ActionType.SELL_STOP,
            ):
                if self.om._symbol_info:
                    # ==================================================================
                    # AGENT-6 EXECUTION-CONTRACT FIX (2026-09-09):
                    # the primary entry path sized the order via
                    # calculate_volume + get_clamped_position_size but NEVER
                    # ran RiskEngine.evaluate_proposal — the documented entry
                    # gate (docs/architecture/execution-pipeline.md). The
                    # kill switch, profit-protection circuit breakers (daily/
                    # weekly loss budget, consecutive-loss cooldown), spread
                    # gate, RR gatekeeper, broker stops-level validation and
                    # the Almgren-Chriss impact guard therefore never gated a
                    # PRIMARY entry, while the hedge path HAS been gated via
                    # evaluate_proposal (live_engine._evaluate_hedging_policy).
                    # Fail-closed repair: the dispatch now uses the TradeOrder
                    # returned by evaluate_proposal — None means the risk
                    # engine REJECTED the entry, and a rejected decision can
                    # never become an order (the policy price-lock state is
                    # released exactly like a failed dispatch). Sizing
                    # semantics are unchanged: evaluate_proposal computes the
                    # same dynamic volume through calculate_dynamic_volume and
                    # the exposure-cap/margin/impact chain internally.
                    # ==================================================================
                    atr_for_risk = max(float(getattr(fv, "atr_m1", 1.5) or 0.0), 0.5)
                    risk_order = self.om.risk_engine.evaluate_proposal(
                        proposal=policy_decision,
                        account=account,
                        symbol_info=self.om._symbol_info,
                        active_positions=active_positions,
                        current_tick=tick,
                        regime_state=regime_state,
                        atr=atr_for_risk,
                        # BUG-252: forward the engine's authoritative peak so
                        # the drawdown risk cut engages (AccountInfo has no
                        # field). Hedge path forwards the same value.
                        peak_equity=getattr(self.om, "_peak_equity", None),
                    )
                    if risk_order is None:
                        logger.warning(
                            "[ENTRY_BLOCKED] layer=RISK_ENGINE reason=RISK_EVALUATION_REJECTED "
                            "action=%s symbol=%s request_id=%s",
                            policy_decision.action.value,
                            policy_decision.symbol,
                            getattr(policy_decision, "request_id", ""),
                        )
                        # Mirror the dispatch-failed cleanup: release the
                        # policy price/direction lock so a risk rejection
                        # cannot wedge the engine out of trading.
                        self.om.signal_policy.last_order_price = None
                        self.om.signal_policy.last_order_time = None
                        self.om.signal_policy._last_active_direction = None
                        self.om.signal_policy._last_active_direction_time = None
                        self.om.signal_policy._last_executed_price = 0.0
                    else:
                        dynamic_volume = risk_order.volume

                        # SETUP SNAPSHOT (2026-08-18): capture the full chart-state
                        # fingerprint the AI saw at dispatch (HTF/SMC/ICT structure,
                        # displacement, sessions, guardian) and attach it to the
                        # entry context so the closed-trade autopsy can attribute
                        # every trade to its exact setup.
                        setup_snapshot: dict = {}
                        try:
                            fv_snap = fv
                            session = (
                                "".join(
                                    seg
                                    for seg, flag in (
                                        ("tokyo", bool(getattr(fv_snap, "session_tokyo", False))),
                                        ("london", bool(getattr(fv_snap, "session_london", False))),
                                        ("ny", bool(getattr(fv_snap, "session_ny", False))),
                                        (
                                            "ov",
                                            bool(
                                                getattr(fv_snap, "session_overlap_london_ny", False)
                                            ),
                                        ),
                                    )
                                    if flag
                                )
                                or "?"
                            )
                            setup_snapshot = {
                                "execution_mode": str(
                                    getattr(policy_decision, "execution_mode", "")
                                ),
                                "model_action": str(getattr(policy_decision, "model_action", "")),
                                "htf_score": float(
                                    getattr(policy_decision, "htf_score", 0.0) or 0.0
                                ),
                                "smc_score": float(
                                    getattr(policy_decision, "smc_score", 0.0) or 0.0
                                ),
                                "conf_before": float(
                                    getattr(policy_decision, "confidence_before_filters", 0.0)
                                    or 0.0
                                ),
                                "conf_after": float(
                                    getattr(policy_decision, "confidence_after_filters", 0.0) or 0.0
                                ),
                                "buy_prob": float(
                                    getattr(policy_decision, "buy_probability", None) or 0.0
                                ),
                                "sell_prob": float(
                                    getattr(policy_decision, "sell_probability", None) or 0.0
                                ),
                                "disp": float(
                                    getattr(fv_snap, "live_tick_displacement", 0.0) or 0.0
                                ),
                                "atr": float(getattr(fv_snap, "atr_m1", 0.0) or 0.0),
                                "trend": float(getattr(fv_snap, "trend_strength", 0.0) or 0.0),
                                "sweep_sig": int(
                                    getattr(fv_snap, "liquidity_sweep_signal", 0) or 0
                                ),
                                "ob_type": int(getattr(fv_snap, "order_block_type", 0) or 0),
                                "fvg_bull": bool(getattr(fv_snap, "fvg_bullish_active", False)),
                                "fvg_bear": bool(getattr(fv_snap, "fvg_bearish_active", False)),
                                "choch_bull": bool(getattr(fv_snap, "choch_bullish", False)),
                                "choch_bear": bool(getattr(fv_snap, "choch_bearish", False)),
                                "broke_high": bool(getattr(fv_snap, "broke_previous_high", False)),
                                "broke_low": bool(getattr(fv_snap, "broke_previous_low", False)),
                                "z_score": float(
                                    getattr(fv_snap, "cross_asset_z_score", 0.0) or 0.0
                                ),
                                "h4": float(getattr(fv_snap, "htf_h4_trend", 0.0) or 0.0),
                                "h1": float(getattr(fv_snap, "htf_h1_momentum", 0.0) or 0.0),
                                "m30": float(getattr(fv_snap, "htf_m30_structure", 0.0) or 0.0),
                                "m15": float(getattr(fv_snap, "htf_m15_confirmation", 0.0) or 0.0),
                                "session": session,
                                "guardian": str(getattr(policy_decision, "guardian_status", "")),
                                "rr": float(
                                    getattr(policy_decision, "risk_reward_ratio", 0.0) or 0.0
                                ),
                            }
                        except Exception as snap_err:
                            logger.warning("[ENTRY] setup snapshot failed", error=str(snap_err))
                        success = self.om.order_manager.dispatch_order(
                            policy_decision, dynamic_volume, setup_snapshot=setup_snapshot
                        )
                        logger.info(
                            f"[info] DISPATCH ORDER action={policy_decision.action.value} price={policy_decision.proposed_entry} volume={dynamic_volume}"
                        )

                        if success:
                            risk_usd = account.equity * (
                                self.om.config.risk.risk_per_trade_pct / 100.0
                            )
                            with contextlib.suppress(Exception):
                                mapped_order_type = self.om.risk_engine._map_action_to_order_type(
                                    policy_decision.action
                                )
                                order_obj = TradeOrder(
                                    order_id=policy_decision.request_id,
                                    symbol=policy_decision.symbol,
                                    order_type=mapped_order_type,
                                    volume=dynamic_volume,
                                    price=policy_decision.proposed_entry,
                                    stop_loss=policy_decision.stop_loss,
                                    take_profit=policy_decision.take_profit,
                                    magic_number=888101,
                                    comment="NSE_HFT_SIZED",
                                )
                                self.om.notifier.notify_order_opened(
                                    order=order_obj,
                                    risk_usd=risk_usd,
                                    callback=lambda msg_id: (
                                        self.om.order_manager.register_order_message(
                                            order_obj.order_id, msg_id
                                        )
                                        if msg_id
                                        else None
                                    ),
                                )
                        else:
                            # Dispatch failed! Clear the price lock immediately so bot is not locked out of trading!
                            self.om.signal_policy.last_order_price = None
                            self.om.signal_policy.last_order_time = None
                            self.om.signal_policy._last_active_direction = None
                            self.om.signal_policy._last_active_direction_time = None
                            self.om.signal_policy._last_executed_price = 0.0

            # FOR POSITION LIFECYCLE ACTIONS
            elif policy_decision.action in (
                ActionType.CLOSE_POSITION,
                ActionType.PARTIAL_CLOSE,
                ActionType.MODIFY_SL_TP,
                ActionType.CANCEL_ORDER,
            ):
                self.om.order_manager.execute_lifecycle_action(policy_decision)
                ticket = getattr(policy_decision, "ticket", 0) or 0
                logger.info(
                    f"[info] DISPATCH LIFECYCLE ACTION action={policy_decision.action.value} ticket={ticket}"
                )

        # Evaluate intelligent hedging / counter-position policy
        self.om._evaluate_hedging_policy(
            active_positions=active_positions,
            tick=tick,
            probs=probs,
            regime_state=regime_state,
            fv=fv,
            account=account,
        )

        # Equity / drawdown tracking + audit
        self.om._update_survival_state(account=account, current_pos_count=current_pos_count)
        self.om.audit.log_account_snapshot(account=account, peak_equity=self.om._peak_equity)
        # Keep the order manager's account snapshot fresh so closed-trade autopsy rows
        # carry accurate balance/equity/drawdown values.
        self.om.order_manager.update_account_snapshot(
            account=account, peak_equity=self.om._peak_equity
        )
