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
from nexus_scalp.domain.models import TickData, TradeOrder, TradeProposal
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.observability.trace_contract import TraceState as _TraceState

logger = get_logger("nexus_scalp.application.live.decision_executor")

# DECISION-TRACE OBSERVER (observability only). Guarded import (BUG-311):
# a fault in observability code must never reach the execution path.
try:
    from nexus_scalp.observability.trace_contract import proposal_summary as _proposal_summary
    from nexus_scalp.observability.trace_observer import trace_observer as _trace
except Exception:  # pragma: no cover - observability failure isolation
    _trace = None  # type: ignore[assignment]

    def _proposal_summary(proposal, *, status, trace_id=None):  # type: ignore[misc]
        return {}


def _trace_decision_id(policy_decision: Any) -> str | None:
    """Canonical id for the decision: EXEC execution_id, else request_id."""
    return (
        str(
            getattr(policy_decision, "execution_id", None)
            or getattr(policy_decision, "request_id", None)
            or ""
        )
        or None
    )


# ---------------------------------------------------------------------------
# DECISION-TRACE enrichment helpers (LIVE-CAUSAL-TOPOLOGY, lane B).
# Contract fields are read ONLY from real runtime objects with getattr; an
# unknown/absent fact stays None and the key is omitted downstream ("absence
# is data", §60). No prose explanation and no latency is ever manufactured.
# ---------------------------------------------------------------------------


def _tr_engine_mode(om: Any) -> str | None:
    """Frozen ``mode`` vocab word from the engine's real execution config.

    Returns None when the config/mode is unreadable — the UI renders UNKNOWN
    instead of a guessed trading mode (§44: LIVE/PAPER/SHADOW never conflated).
    """
    try:
        mode = getattr(getattr(getattr(om, "config", None), "execution", None), "mode", None)
    except Exception:
        return None
    if mode is None:
        return None
    return str(getattr(mode, "value", mode)).upper() or None


def _tr_account_freshness(om: Any) -> str | None:
    """Freshness word (FRESH/STALE/MISSING) of the account snapshot the risk
    gate consumed (§40). Absent on any engine that never classified it."""
    try:
        value = getattr(om, "_account_freshness", None)
    except Exception:
        return None
    if value is None:
        return None
    return str(getattr(value, "value", value)) or None


def _tr_ticket_ids(action: Any, ticket: Any) -> tuple[str | None, str | None]:
    """Map a real broker ticket to ``(position_id, order_id)`` by the entity
    the ACTION targets: position actions carry a position ticket, CANCEL_ORDER
    an order ticket, entries carry no ticket at all. Unknown action or an
    absent/zero ticket -> (None, None); a ticket is never guessed onto a field
    that would misstate what the broker identified (§66).

    NOTE: ``position_id`` is the position TICKET the action targeted — the
    honest lineage fact this call site holds. The broker's deal/order ids
    arrive with the gateway response (adapter seam, out of lane scope), so
    ``deal_id`` stays absent here.
    """
    try:
        raw = int(ticket or 0)
    except Exception:
        return None, None
    if raw <= 0:
        return None, None
    name = str(getattr(action, "value", action))
    if name in {"CLOSE_POSITION", "PARTIAL_CLOSE", "MODIFY_SL_TP"}:
        return str(raw), None
    if name == "CANCEL_ORDER":
        return None, str(raw)
    return None, None


def _tr_risk_evidence(
    base: dict[str, Any], *, om: Any, proposal: Any, account: Any
) -> dict[str, Any]:
    """Risk-gate evidence for a RISK event (§19 risk evidence, §28 margin
    lineage, §40 freshness): the proposal's own rule record, the free-margin
    figure the gate was handed, and the account-snapshot freshness word — all
    read from the objects the runtime actually passed in. Keys whose value the
    runtime does not carry are left out; nothing is zero-filled."""
    out = dict(base)
    checks = getattr(proposal, "risk_checks", None)
    if checks is not None:
        out["risk_checks"] = checks
    margin_free = getattr(account, "margin_free", None)
    if isinstance(margin_free, int | float) and not isinstance(margin_free, bool):
        out["margin_free"] = float(margin_free)
    fresh = _tr_account_freshness(om)
    if fresh:
        out["account_freshness"] = fresh
    return out


def _tr_emit(observer: Any, *, extra: dict[str, Any] | None = None, **emit_fields: Any) -> None:
    """One guarded ``emit()`` that also carries the frozen v2 causal fields.

    ``extra`` holds only REAL, non-None contract fields (``state`` /
    ``reason_code`` / ``mode`` / ``position_id`` / ``order_id`` / ``freshness``
    / ``request_id`` / ``execution_id``). Every value is a getattr on a real
    runtime object (never a literal the caller invented); None stays omitted
    ("absence is data", §60). Never raises (BUG-311).
    """
    payload = {k: v for k, v in (extra or {}).items() if v is not None}
    try:
        observer.emit(**{**emit_fields, **payload})
    except Exception:  # pragma: no cover - observability failure isolation
        pass


def _tr_state_verdict(rejected: bool) -> str:
    """Frozen ``TraceState`` verdict word for a gate result (PASSED/REJECTED)."""
    return _TraceState.REJECTED if rejected else _TraceState.PASSED


def _tr_state_outcome(failed: bool) -> str | None:
    """Frozen ``TraceState`` outcome word for a terminal order state.

    Success yields None: a completed request does NOT claim CONFIRMED — that
    word belongs to broker confirmation evidence this call site never sees
    (§66: only CONFIRMED/EXECUTED backend states may render executed).
    """
    return _TraceState.FAILED if failed else None


def _tr_execution_id(proposal: Any) -> str | None:
    """The proposal's own EXEC id — the trace's canonical decision id."""
    try:
        eid = getattr(proposal, "execution_id", None)
    except Exception:
        return None
    return str(eid) or None


class DecisionExecutor:
    """Executes the post-policy decision stage of the live tick pipeline."""

    def __init__(self, om: Any) -> None:
        # The composition root (LiveEngine). All engine state stays there.
        self.om = om

    @staticmethod
    def _build_directional_reversal_proposal(reversal: Any) -> TradeProposal | None:
        """Derives the DIRECTIONAL flip proposal from a CLOSE_POSITION reversal.

        The policy-built reversal proposal carries the NEW direction geometry
        (proposed_entry / stop_loss / take_profit, signals/policy.py) with
        action=CLOSE_POSITION, so the domain validator skipped the directional
        price invariants. Rebuilding a TradeProposal with the reversal_action
        re-runs the full model validation - an inverted or degenerate geometry
        is REJECTED here (fail-closed: no proposal, no risk evaluation, no flip
        order) instead of reaching the broker. Returns None when the reversal
        carries no actionable direction or the directional proposal fails
        validation.
        """
        reversal_action = getattr(reversal, "reversal_action", None)
        if reversal_action is None:
            return None
        try:
            return TradeProposal(
                request_id=str(getattr(reversal, "request_id", "") or ""),
                execution_id=getattr(reversal, "execution_id", None),
                symbol=str(getattr(reversal, "symbol", "") or ""),
                generated_at=getattr(reversal, "generated_at", None),
                action=reversal_action,
                confidence=float(getattr(reversal, "confidence", 0.0) or 0.0),
                proposed_entry=float(getattr(reversal, "proposed_entry", 0.0) or 0.0),
                stop_loss=float(getattr(reversal, "stop_loss", 0.0) or 0.0),
                take_profit=float(getattr(reversal, "take_profit", 0.0) or 0.0),
                risk_reward_ratio=float(getattr(reversal, "risk_reward_ratio", 0.0) or 0.0),
                reason_code=str(getattr(reversal, "reason_code", "") or ""),
                ticket=int(getattr(reversal, "ticket", 0) or 0),
                regime=getattr(reversal, "regime", None),
                regime_confidence=getattr(reversal, "regime_confidence", None),
                is_ai_reversal=True,
                model_action=reversal_action.value,
                execution_mode=getattr(reversal, "execution_mode", None),
            )
        except Exception as build_err:
            logger.warning(
                "[AI_REVERSAL] directional proposal build failed (fail-closed)",
                error=str(build_err),
                ticket=getattr(reversal, "ticket", 0) or 0,
            )
            return None

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
        # DECISION-TRACE: which executor boundary (if any) suppressed this
        # decision. Observability only — never read by execution logic.
        _trace_suppressed_reason: str | None = None
        # =================================================================
        # RUNTIME RESILIENCE (Agent-7 failure injection): a proposal whose
        # decision_stage is DEDUP_GATE is the BUG-169 duplicate re-surface —
        # an OBSERVABILITY artifact only. It must never execute: the
        # re-surfaced payload carries a FRESH request_id, so the
        # dispatch-layer duplicate guard is blind to it and a replayed
        # market event would re-dispatch the cached order. Duplicates are
        # zero-information by contract; the engine prefers NO TRADE.
        # =================================================================
        if (
            str(getattr(policy_decision, "decision_stage", "") or "") == "DEDUP_GATE"
            and getattr(policy_decision, "action", ActionType.NO_TRADE) != ActionType.NO_TRADE
        ):
            policy_decision = policy_decision.model_copy(
                update={
                    "action": ActionType.NO_TRADE,
                    "final_action": "NO_TRADE",
                    "reason_code": "TICK_DUPLICATE_SUPPRESSED",
                    "rejection_reason": "duplicate market event re-surface is never executable",
                }
            )
            _trace_suppressed_reason = "TICK_DUPLICATE_SUPPRESSED"
            logger.info(
                "[DEDUP_BOUNDARY] event=ORDER_MUTATION_SUPPRESSED suppressed_action=%s",
                "replayed duplicate",
            )
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
            _trace_suppressed_reason = "SHADOW_OBSERVATION_ONLY"
            logger.info(
                "[SHADOW_BOUNDARY] event=ORDER_MUTATION_SUPPRESSED suppressed_action=%s ticket=%s",
                _shadow_action.value,
                getattr(policy_decision, "ticket", 0) or 0,
            )
        # DECISION-TRACE: exactly one decision record per evaluated proposal.
        # Source NO_TRADE was already recorded terminally at the post-policy
        # seam; here we record (a) executor-boundary suppressions as terminal
        # REJECTED and (b) surviving actionable decisions as non-terminal
        # APPROVED so RISK/EXECUTION/GATEWAY/ORDER chain onto the same trace.
        if _trace is not None:
            _act_v = (
                policy_decision.action.value
                if hasattr(policy_decision.action, "value")
                else str(policy_decision.action)
            )
            if _trace_suppressed_reason is not None:
                _trace.emit_decision(
                    summary=_proposal_summary(policy_decision, status="REJECTED"),
                    detail={
                        "suppressed_by": _trace_suppressed_reason,
                        # Frozen v2 contract fields as REAL data (lane-A seam:
                        # a non-keyword kwarg would break older observers, so
                        # they ride in the detail payload — see lane-B status).
                        "reason_code": str(_trace_suppressed_reason),
                        "engine_mode": _tr_engine_mode(self.om),
                    },
                    terminal=True,
                    component="decision_executor",
                )
            elif _act_v != "NO_TRADE":
                _trace.emit_decision(
                    summary=_proposal_summary(policy_decision, status="APPROVED"),
                    terminal=False,
                    component="decision_executor",
                )
        if policy_decision.action != ActionType.NO_TRADE:
            # ---------------------------------------------------------------
            # AI POSITION REVERSAL: close-then-flip, never stack
            # ---------------------------------------------------------------
            if getattr(policy_decision, "is_ai_reversal", False) or (
                policy_decision.action == ActionType.CLOSE_POSITION
                and "AI_REVERSAL_SIGNAL" in (policy_decision.reason_code or "")
            ):
                # BUG-258 (Agent-15 capital-protection wave 3): the flip
                # entry is gated through RiskEngine.evaluate_proposal on a
                # DIRECTIONAL TradeProposal (kill switch, breakers, RR,
                # spread, stops-level, exposure, margin, impact). None =>
                # close-only: the protective close still happens, no flip
                # order. The old mirrored-volume fallback is removed.
                reversal_volume = 0.0
                if (
                    self.om._symbol_info
                    and getattr(policy_decision, "reversal_action", None) is not None
                ):
                    directional = self._build_directional_reversal_proposal(policy_decision)
                    if directional is not None:
                        atr_for_risk = max(float(getattr(fv, "atr_m1", 1.5) or 0.0), 0.5)
                        reversal_risk_order = self.om.risk_engine.evaluate_proposal(
                            proposal=directional,
                            account=account,
                            symbol_info=self.om._symbol_info,
                            active_positions=active_positions,
                            current_tick=tick,
                            regime_state=regime_state,
                            atr=atr_for_risk,
                            peak_equity=getattr(self.om, "_peak_equity", None),
                        )
                        # DECISION-TRACE: the reversal flip's risk verdict.
                        # NOT terminal — the protective close still executes
                        # after a flip rejection (close-only is a real path).
                        if _trace is not None and _trace.active:
                            _tr_pos, _ = _tr_ticket_ids(
                                policy_decision.action,
                                getattr(policy_decision, "ticket", 0),
                            )
                            _tr_emit(
                                _trace,
                                stage="RISK",
                                component="risk_engine",
                                event_type="RISK_EVALUATION",
                                status=("REJECT" if reversal_risk_order is None else "OBSERVED"),
                                symbol=policy_decision.symbol,
                                decision_id=_trace_decision_id(policy_decision),
                                # Real contract fields (frozen v2): the verdict
                                # word, the runtime's own rejection code, the
                                # engine mode, the position the flip targeted and
                                # the account-snapshot freshness the gate read.
                                extra={
                                    "state": _tr_state_verdict(reversal_risk_order is None),
                                    "reason_code": (
                                        "AI_REVERSAL_RISK_REJECTED"
                                        if reversal_risk_order is None
                                        else None
                                    ),
                                    "mode": _tr_engine_mode(self.om),
                                    "position_id": _tr_pos,
                                    "freshness": _tr_account_freshness(self.om),
                                    "request_id": policy_decision.request_id,
                                    "execution_id": _tr_execution_id(policy_decision),
                                },
                                detail=_tr_risk_evidence(
                                    {
                                        "allowed": reversal_risk_order is not None,
                                        "flip_allowed": reversal_risk_order is not None,
                                        "close_only": reversal_risk_order is None,
                                        "context": "ai_reversal_flip",
                                        "reason": (
                                            "AI_REVERSAL_RISK_REJECTED"
                                            if reversal_risk_order is None
                                            else None
                                        ),
                                        "volume": (
                                            reversal_risk_order.volume
                                            if reversal_risk_order is not None
                                            else None
                                        ),
                                        "request_id": policy_decision.request_id,
                                    },
                                    om=self.om,
                                    proposal=policy_decision,
                                    account=account,
                                ),
                            )
                        if reversal_risk_order is None:
                            logger.warning(
                                "[ENTRY_BLOCKED] layer=RISK_ENGINE reason=AI_REVERSAL_RISK_REJECTED "
                                "reversal_action=%s ticket=%s request_id=%s - close-only, "
                                "no flip order will be dispatched",
                                getattr(policy_decision.reversal_action, "value", None),
                                getattr(policy_decision, "ticket", 0) or 0,
                                getattr(policy_decision, "request_id", ""),
                            )
                        else:
                            reversal_volume = reversal_risk_order.volume
                    else:
                        logger.warning(
                            "[ENTRY_BLOCKED] layer=RISK_ENGINE reason=AI_REVERSAL_GEOMETRY_UNAVAILABLE "
                            "ticket=%s - close-only, no flip order will be dispatched",
                            getattr(policy_decision, "ticket", 0) or 0,
                        )
                elif not self.om._symbol_info:
                    logger.warning(
                        "[ENTRY_BLOCKED] layer=RISK_ENGINE reason=AI_REVERSAL_NO_SYMBOL_INFO "
                        "ticket=%s - close-only, no flip order will be dispatched",
                        getattr(policy_decision, "ticket", 0) or 0,
                    )

                # DECISION-TRACE: execution stage boundary for the reversal
                # (pre-dispatch evidence; the gateway event follows inside
                # order dispatch, then the terminal ORDER state below).
                if _trace is not None and _trace.active:
                    _tr_pos, _ = _tr_ticket_ids(
                        policy_decision.action, getattr(policy_decision, "ticket", 0)
                    )
                    _tr_emit(
                        _trace,
                        stage="EXECUTION",
                        component="order_manager",
                        event_type="ORDER_BUILD",
                        status="OBSERVED",
                        symbol=policy_decision.symbol,
                        decision_id=_trace_decision_id(policy_decision),
                        extra={
                            "mode": _tr_engine_mode(self.om),
                            "position_id": _tr_pos,
                            # The proposal's own reason code: the real code
                            # that made this order build happen (§16).
                            "reason_code": getattr(policy_decision, "reason_code", None) or None,
                            "request_id": policy_decision.request_id,
                            "execution_id": _tr_execution_id(policy_decision),
                        },
                        detail={
                            "action": "AI_REVERSAL",
                            "reversal_action": getattr(
                                policy_decision.reversal_action, "value", None
                            ),
                            "volume": reversal_volume,
                            "close_only": reversal_volume <= 0,
                            "ticket": getattr(policy_decision, "ticket", 0) or None,
                            "engine_mode": getattr(
                                getattr(self.om.config, "execution", None), "mode", None
                            ),
                            "request_id": policy_decision.request_id,
                        },
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
                # DECISION-TRACE: terminal order-state for this trace path.
                if _trace is not None:
                    _tr_pos, _ = _tr_ticket_ids(
                        policy_decision.action, getattr(policy_decision, "ticket", 0)
                    )
                    _tr_emit(
                        _trace,
                        stage="ORDER",
                        component="order_manager",
                        event_type="ORDER_STATE",
                        status="EXECUTED" if success else "FAILED",
                        symbol=policy_decision.symbol,
                        decision_id=_trace_decision_id(policy_decision),
                        terminal=True,
                        extra={
                            "state": _tr_state_outcome(not success),
                            "mode": _tr_engine_mode(self.om),
                            "position_id": _tr_pos,
                            "request_id": policy_decision.request_id,
                            "execution_id": _tr_execution_id(policy_decision),
                        },
                        detail={
                            "success": bool(success),
                            "action": "AI_REVERSAL",
                            "volume": reversal_volume,
                            "ticket": getattr(policy_decision, "ticket", 0) or None,
                            "request_id": policy_decision.request_id,
                        },
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
                    # DECISION-TRACE: the entry risk verdict. A rejection is
                    # terminal (no dispatch can follow); an approval chains
                    # into the EXECUTION stage below.
                    if _trace is not None and _trace.active:
                        _tr_rejected = risk_order is None
                        _tr_emit(
                            _trace,
                            stage="RISK",
                            component="risk_engine",
                            event_type="RISK_EVALUATION",
                            status="REJECT" if _tr_rejected else "OBSERVED",
                            symbol=policy_decision.symbol,
                            decision_id=_trace_decision_id(policy_decision),
                            terminal=_tr_rejected,
                            extra={
                                "state": _tr_state_verdict(_tr_rejected),
                                "reason_code": (
                                    "RISK_EVALUATION_REJECTED" if _tr_rejected else None
                                ),
                                "mode": _tr_engine_mode(self.om),
                                "freshness": _tr_account_freshness(self.om),
                                "request_id": policy_decision.request_id,
                                "execution_id": _tr_execution_id(policy_decision),
                            },
                            detail=_tr_risk_evidence(
                                {
                                    "allowed": not _tr_rejected,
                                    "context": "primary_entry",
                                    "reason": (
                                        "RISK_EVALUATION_REJECTED" if _tr_rejected else None
                                    ),
                                    "action": policy_decision.action.value,
                                    "atr_for_risk": atr_for_risk,
                                    "volume": (
                                        risk_order.volume if risk_order is not None else None
                                    ),
                                    "request_id": policy_decision.request_id,
                                },
                                om=self.om,
                                proposal=policy_decision,
                                account=account,
                            ),
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
                        # DECISION-TRACE: execution-stage entry evidence
                        # (order construction, pre-dispatch).
                        if _trace is not None and _trace.active:
                            _tr_emit(
                                _trace,
                                stage="EXECUTION",
                                component="order_manager",
                                event_type="ORDER_BUILD",
                                status="OBSERVED",
                                symbol=policy_decision.symbol,
                                decision_id=_trace_decision_id(policy_decision),
                                extra={
                                    "mode": _tr_engine_mode(self.om),
                                    "reason_code": (
                                        getattr(policy_decision, "reason_code", None) or None
                                    ),
                                    "request_id": policy_decision.request_id,
                                    "execution_id": _tr_execution_id(policy_decision),
                                },
                                detail={
                                    "action": policy_decision.action.value,
                                    "volume": dynamic_volume,
                                    "price": policy_decision.proposed_entry,
                                    "sl": policy_decision.stop_loss,
                                    "tp": policy_decision.take_profit,
                                    "magic": 888101,
                                    "comment": "NSE_HFT_SIZED",
                                    "engine_mode": getattr(
                                        getattr(self.om.config, "execution", None),
                                        "mode",
                                        None,
                                    ),
                                    "request_id": policy_decision.request_id,
                                },
                            )
                        success = self.om.order_manager.dispatch_order(
                            policy_decision, dynamic_volume, setup_snapshot=setup_snapshot
                        )
                        logger.info(
                            f"[info] DISPATCH ORDER action={policy_decision.action.value} price={policy_decision.proposed_entry} volume={dynamic_volume}"
                        )
                        # DECISION-TRACE: terminal order state for the entry
                        # path (gateway response events are chained between
                        # ORDER_BUILD and this event by the adapter seam).
                        if _trace is not None:
                            _tr_emit(
                                _trace,
                                stage="ORDER",
                                component="order_manager",
                                event_type="ORDER_STATE",
                                status="EXECUTED" if success else "FAILED",
                                symbol=policy_decision.symbol,
                                decision_id=_trace_decision_id(policy_decision),
                                terminal=True,
                                extra={
                                    "state": _tr_state_outcome(not success),
                                    "mode": _tr_engine_mode(self.om),
                                    "request_id": policy_decision.request_id,
                                    "execution_id": _tr_execution_id(policy_decision),
                                },
                                detail={
                                    "success": bool(success),
                                    "action": policy_decision.action.value,
                                    "volume": dynamic_volume,
                                    "request_id": policy_decision.request_id,
                                },
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

                # DECISION-TRACE: entry approved but dispatch was never
                # attempted (no symbol info) — terminal, honestly labeled.
                elif _trace is not None:
                    _tr_emit(
                        _trace,
                        stage="EXECUTION",
                        component="order_manager",
                        event_type="ORDER_BUILD",
                        status="NOT_DISPATCHED",
                        symbol=policy_decision.symbol,
                        decision_id=_trace_decision_id(policy_decision),
                        terminal=True,
                        extra={
                            # Real stop reason already carried by this call
                            # site (never invented): dispatch was BLOCKED.
                            "state": _TraceState.BLOCKED,
                            "reason_code": "SYMBOL_INFO_UNAVAILABLE",
                            "mode": _tr_engine_mode(self.om),
                            "request_id": policy_decision.request_id,
                            "execution_id": _tr_execution_id(policy_decision),
                        },
                        detail={
                            "reason": "SYMBOL_INFO_UNAVAILABLE",
                            "action": policy_decision.action.value,
                            "request_id": policy_decision.request_id,
                        },
                    )

            # FOR POSITION LIFECYCLE ACTIONS
            elif policy_decision.action in (
                ActionType.CLOSE_POSITION,
                ActionType.PARTIAL_CLOSE,
                ActionType.MODIFY_SL_TP,
                ActionType.CANCEL_ORDER,
            ):
                ticket = getattr(policy_decision, "ticket", 0) or 0
                # DECISION-TRACE: lifecycle dispatch boundaries. The ticket is
                # mapped to the entity THIS action targets (position vs pending
                # order) so a follow-position/follow-order filter sees the id the
                # broker itself identified — absent tickets stay absent.
                _tr_pos, _tr_ord = _tr_ticket_ids(policy_decision.action, ticket)
                if _trace is not None and _trace.active:
                    _tr_emit(
                        _trace,
                        stage="EXECUTION",
                        component="order_manager",
                        event_type="ORDER_BUILD",
                        status="OBSERVED",
                        symbol=policy_decision.symbol,
                        decision_id=_trace_decision_id(policy_decision),
                        extra={
                            "mode": _tr_engine_mode(self.om),
                            "position_id": _tr_pos,
                            "order_id": _tr_ord,
                            "reason_code": (getattr(policy_decision, "reason_code", None) or None),
                            "request_id": policy_decision.request_id,
                            "execution_id": _tr_execution_id(policy_decision),
                        },
                        detail={
                            "action": policy_decision.action.value,
                            "ticket": ticket or None,
                            "context": "position_lifecycle",
                            "engine_mode": getattr(
                                getattr(self.om.config, "execution", None), "mode", None
                            ),
                            "request_id": policy_decision.request_id,
                        },
                    )
                # Real execution result: the order manager's own boolean (a
                # False means the lifecycle action did NOT happen). Observability
                # reads it only — no engine branch changes on this value.
                _tr_lifecycle_ok = bool(
                    self.om.order_manager.execute_lifecycle_action(policy_decision)
                )
                logger.info(
                    f"[info] DISPATCH LIFECYCLE ACTION action={policy_decision.action.value} ticket={ticket}"
                )
                # DECISION-TRACE: terminal path end. "DISPATCHED" claims only
                # what this call site proves when the order manager reported
                # success (the action was handed over) — broker response
                # evidence, if any, is the gateway event chained in between.
                # A reported FAILURE is terminal FAILED (real execution result,
                # never dressed up as dispatched).
                if _trace is not None:
                    _tr_emit(
                        _trace,
                        stage="ORDER",
                        component="order_manager",
                        event_type="ORDER_STATE",
                        status="DISPATCHED" if _tr_lifecycle_ok else "FAILED",
                        symbol=policy_decision.symbol,
                        decision_id=_trace_decision_id(policy_decision),
                        terminal=True,
                        extra={
                            "state": _tr_state_outcome(not _tr_lifecycle_ok),
                            "mode": _tr_engine_mode(self.om),
                            "position_id": _tr_pos,
                            "order_id": _tr_ord,
                            "request_id": policy_decision.request_id,
                            "execution_id": _tr_execution_id(policy_decision),
                        },
                        detail={
                            "success": _tr_lifecycle_ok,
                            "action": policy_decision.action.value,
                            "ticket": ticket or None,
                            "request_id": policy_decision.request_id,
                        },
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
