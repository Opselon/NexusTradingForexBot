"""DispatchEngine — broker dispatch composition (hedge + primary entry paths).

P0 seam S10 (god-file decomposition): the hedge-path broker submission
(``execute_order``), the unified primary entry router (``dispatch_order``),
the dispatch volume clamp and the canonical entry-reason resolver move OUT
of ``OrderLifecycleManager`` verbatim (behavior-preserving extraction). The
manager composes this engine and keeps thin delegates; state remains on the
manager's canonical surface — the engine reads/writes THROUGH the ``om``
composition root, never through copies.

Moved methods (verbatim bodies, ``self`` -> ``om``):
    execute_order          : hedge-path submission + SAFE_MODE breaker
    dispatch_order         : primary market/pending entry router
    _clamp_dispatch_volume : risk-engine + HARD_MAX_LOTS last defense
    _resolve_entry_reason  : canonical ledger entry reason

Ownership contract:
    READS   : adapter / mt5_adapter (broker), audit, experience_engine,
              risk_engine, global_state / _consecutive_failures (safety
              state machine), _processed_orders (idempotency guard)
    WRITES  : _processed_orders, _consecutive_failures, global_state,
              audit execution/order rows, terminal pending outcomes
    AUTHORITY: broker dispatch ONLY — no position management, no
              protective modification, no scoring.

The ``_processed_orders`` duplicate-dispatch guard dict deliberately stays
manager-owned (tests and the debug snapshot read it directly); this engine
mutates it via ``om``. HARD_MAX_LOTS / MAX_TOTAL_EXPOSURE remain defined on
the facade module (tests import them from there) and are bound lazily below
(import cycle breaker, same discipline as scoring.py's ``_om_symbols``).
"""

from __future__ import annotations

import math
import time
from typing import Any

from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import TradeOrder
from nexus_scalp.execution.terminal_outcome import emit_terminal_pending_outcome
from nexus_scalp.experience.lifecycle import DecisionLifecycle
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.execution.lifecycle.dispatch")


def _om_dispatch_symbols() -> tuple[float, int]:
    """Late-bound god-module dispatch constants (import cycle breaker)."""
    from nexus_scalp.execution.order_manager import HARD_MAX_LOTS, MAX_TOTAL_EXPOSURE

    return HARD_MAX_LOTS, MAX_TOTAL_EXPOSURE


def _is_directional_entry(action: Any) -> bool:
    """True for NEW directional entries (market/limit/stop, both sides).

    Position lifecycle actions (CLOSE_POSITION, PARTIAL_CLOSE,
    MODIFY_SL_TP, CANCEL_ORDER, CLOSE_ALL...) are NOT entries and must
    never be gated — protective exits stay reachable at all times.
    """
    name = str(getattr(action, "value", action) or "").upper()
    return name.startswith(("BUY", "SELL"))


class DispatchEngine:
    """Broker dispatch owner: hedge submission + unified entry router (S10)."""

    def __init__(self, om: Any) -> None:
        # Composition root (OrderLifecycleManager). The engine deliberately
        # accesses the manager's canonical state surface (adapter, audit,
        # safety state, _processed_orders) instead of copying any of it
        # (single source of truth, S5 discipline).
        self.om = om

    def execute_order(self, order: TradeOrder) -> bool:
        """Submits trade deal to broker adapter with duplicate submission prevention."""
        if self.om.global_state == "SAFE_MODE":
            logger.warning("Order blocked: Safety State is SAFE_MODE.")
            return False

        if order.order_id in self.om._processed_orders:
            logger.warning(
                "Duplicate order submission blocked by idempotency check", order_id=order.order_id
            )
            return False

        # BUG-247 (RESIDUAL P2): hedge entry (execute_order) must carry the same
        # HARD_MAX_LOTS last-defense clamp as the primary dispatch path; the
        # normal RiskEngine-sized hedge volume is unchanged (byte-identical).
        clamped_vol = self.om._clamp_dispatch_volume(order.volume, symbol=order.symbol)
        if clamped_vol <= 0.0:
            logger.warning("Hedge entry blocked: clamped volume is zero", requested=order.volume)
            return False
        if abs(clamped_vol - float(order.volume)) > 1e-9:
            try:
                order = order.model_copy(update={"volume": float(clamped_vol)})
            except Exception:
                logger.error(
                    "Hedge volume clamp copy failed (isolated)",
                    requested=order.volume,
                    clamped=clamped_vol,
                )
                return False

        logger.info(
            "Dispatching trade order to broker adapter",
            order_id=order.order_id,
            symbol=order.symbol,
            volume=order.volume,
        )

        _dispatch_started = time.monotonic()
        success: bool = bool(self.om.adapter.send_order(order))

        if not success:
            self.om._consecutive_failures += 1
            if self.om._consecutive_failures >= 3:
                self.om.global_state = "SAFE_MODE"
                logger.critical("TRANSITIONED TO SAFE_MODE: 3 consecutive rejections detected!")
        else:
            self.om._consecutive_failures = 0

        status_str = "FILLED" if success else "REJECTED"

        self.om._processed_orders[order.order_id] = success
        self.om.audit.log_execution(order, status_str)

        # OBS-TRACE (2026-09-09): latency is MEASURED (monotonic dispatch ->
        # adapter return), never a constant. Constants in audit_orders.latency
        # previously made execution-latency forensics impossible (the
        # OBS-010 census: 81% zeros / synthetic values).
        self.om.audit.log_order(
            ticket=0,
            order_id=order.order_id,
            symbol=order.symbol,
            action="Executed order",
            price=order.price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            volume=order.volume,
            reason="execute_order executed",
            latency=max(0.0, time.monotonic() - _dispatch_started),
            execution_mode="STANDARD",
        )

        return success

    def _clamp_dispatch_volume(self, volume: float, symbol: str | None = None) -> float:
        """
        Routes every dispatch volume through the risk engine clamp when available and
        applies the absolute HARD_MAX_LOTS ceiling unconditionally.
        """
        HARD_MAX_LOTS = _om_dispatch_symbols()[0]
        try:
            vol = float(volume)
        except (TypeError, ValueError):
            return 0.0

        # BUG-248: NaN/inf must not propagate past the clamp
        # (nan <= 0.0 is False; min(nan, 10) is nan). A NaN volume reaching the
        # broker request would be an unexplainable order divergence.
        if not math.isfinite(vol) or vol <= 0.0:
            return 0.0

        if self.om.risk_engine is not None and hasattr(
            self.om.risk_engine, "get_clamped_position_size"
        ):
            account = None
            symbol_info = None
            try:
                account = self.om.adapter.get_account_info()
            except Exception:
                account = None
            try:
                if symbol:
                    symbol_info = self.om.adapter.get_symbol_info(symbol)
            except Exception:
                symbol_info = None

            try:
                vol = float(
                    self.om.risk_engine.get_clamped_position_size(
                        volume=vol,
                        account=account,
                        symbol_info=symbol_info,
                    )
                )
            except Exception as clamp_err:
                logger.error(
                    "Risk engine clamp failed; falling back to hard cap", error=str(clamp_err)
                )

        # Defense-in-depth: a misbehaving clamp implementation must not leak
        # NaN/inf into the broker request (nan survives min() and round()).
        if not math.isfinite(vol) or vol <= 0.0:
            return 0.0

        clamped = min(vol, HARD_MAX_LOTS)
        if clamped < vol:
            logger.warning(
                "LOT SIZE CLAMPED to HARD_MAX_LOTS",
                requested=round(vol, 2),
                clamped=round(clamped, 2),
                hard_max=HARD_MAX_LOTS,
            )
        return round(clamped, 2)

    def dispatch_order(
        self, decision: Any, volume: float, setup_snapshot: dict[str, Any] | None = None
    ) -> bool:
        """
        Unified dispatch router for new entry signals (BUY, SELL, BUY_LIMIT, SELL_LIMIT, BUY_STOP, SELL_STOP).

        Enforces, in order: SAFE_MODE, the MAINTENANCE-WINDOW guard, MAX_TOTAL_EXPOSURE,
        the HARD_MAX_LOTS clamp via the risk engine, and entry-context capture for the
        ledger autopsy.

        `setup_snapshot` (2026-08-18): the full chart-state fingerprint (HTF/SMC/ICT
        structure, displacement, sessions, guardian) captured at dispatch by the
        caller, attached to the entry context and persisted in the closed-trade
        autopsy row for post-hoc strategy/setup attribution.
        """
        MAX_TOTAL_EXPOSURE = _om_dispatch_symbols()[1]
        # BUG-241: the primary dispatch path now honors the engine safety
        # state machine. Previously only execute_order (hedge path) checked
        # SAFE_MODE, so the main entry path kept dispatching through a
        # 3-rejection circuit breaker it never fed and never read.
        if self.om.global_state == "SAFE_MODE":
            logger.warning(
                "[ENTRY_BLOCKED] layer=SAFE_MODE reason=CIRCUIT_OPEN action=%s symbol=%s",
                getattr(decision.action, "value", str(decision.action)),
                getattr(decision, "symbol", ""),
            )
            emit_terminal_pending_outcome(
                experience_engine=self.om.experience_engine,
                request_id=str(getattr(decision, "request_id", "") or ""),
                state=DecisionLifecycle.NOT_DISPATCHED,
                detail="SAFE_MODE circuit open at dispatch",
            )
            return False

        action = decision.action
        symbol = decision.symbol
        price = decision.proposed_entry
        sl = decision.stop_loss
        tp = decision.take_profit

        # --- MAINTENANCE-WINDOW ENTRY GUARD (ECON v1 phase 6, P1) ---
        # Blocks NEW entries inside the nightly server-time maintenance
        # break (23:00->01:00 server, +/-30m spread-evidence buffer) where
        # the raw feed shows spread p90 48 / p95 87 points vs 20 outside.
        # Semantics:
        #   * gate applies ONLY to directional NEW entries (BUY/SELL
        #     families). CLOSE / MODIFY / CANCEL lifecycle actions route
        #     through execute_lifecycle_action and are never blocked —
        #     protective SL/TP, trailing, reconciliation and exits stay
        #     fully intact.
        #   * the AI-REVERSAL flip reaches this router only AFTER the
        #     opposing positions were closed; the close itself is not
        #     gated. The fresh flip entry IS gated here (same predicate)
        #     — the reversal cannot bypass the window.
        #   * the predicate is the CANONICAL one
        #     (research/economics.in_maintenance_window) driven by the
        #     canonical broker/server clock offset
        #     (adapters.mt5.providers.BROKER_SERVER_UTC_OFFSET_MINUTES).
        #     No local UTC hardcode, no second time implementation.
        #   * fail-closed: an unknown/failed server-time derivation blocks
        #     the entry (safe side — a tick with no timestamp cannot be
        #     proven in-session).
        from nexus_scalp.adapters.mt5.providers import BROKER_SERVER_UTC_OFFSET_MINUTES
        from nexus_scalp.research.economics import in_maintenance_window

        if _is_directional_entry(action):
            tick_ts = getattr(decision, "generated_at", None)
            server_offset = BROKER_SERVER_UTC_OFFSET_MINUTES / 60.0
            in_window = (
                in_maintenance_window(tick_ts, server_utc_offset_hours=server_offset)
                if tick_ts is not None
                else True  # no timestamp -> cannot prove out-of-window
            )
            if in_window:
                logger.warning(
                    "[ENTRY_BLOCKED] layer=MAINTENANCE_WINDOW "
                    "reason=NIGHTLY_MAINTENANCE_BREAK action=%s symbol=%s "
                    "decision_ts=%s server_offset_min=%s",
                    getattr(action, "value", str(action)),
                    symbol,
                    tick_ts.isoformat() if tick_ts is not None else None,
                    BROKER_SERVER_UTC_OFFSET_MINUTES,
                )
                emit_terminal_pending_outcome(
                    experience_engine=self.om.experience_engine,
                    request_id=str(getattr(decision, "request_id", "") or ""),
                    state=DecisionLifecycle.NOT_DISPATCHED,
                    detail="NIGHTLY_MAINTENANCE_BREAK entry guard",
                )
                return False

        # --- ENGINE-LEVEL DUPLICATE DISPATCH GUARD (EXEC-QUALITY) ---
        # `execute_order` (hedge path) has had an idempotency guard via
        # `_processed_orders` since inception, but the PRIMARY market/pending
        # dispatch path never recorded its request_ids. A policy re-fire, a
        # duplicated decision object, or an AI-reversal double-intercept could
        # reach the broker twice under one request_id (silent order
        # duplication, INV-005/006 surface). Every request_id that has been
        # SENT to the broker once (filled or refused) is now terminal here.
        dispatch_request_id = str(getattr(decision, "request_id", "") or "")
        if dispatch_request_id and dispatch_request_id in self.om._processed_orders:
            logger.warning(
                "Duplicate dispatch blocked by idempotency check",
                request_id=dispatch_request_id,
                prior_result=self.om._processed_orders[dispatch_request_id],
            )
            return False

        # --- MAX EXPOSURE ENFORCEMENT (1 position OR 1 pending, engine-wide) ---
        if not self.om._is_exposure_available(symbol=symbol):
            positions, pendings = self.om.count_total_exposure(symbol=symbol)
            # BUG-072/073: the internal view is broker-reconciled every tick
            # (manage_active_positions + reconcile_pending_state) and after
            # every verified cancel, so this block reflects real broker state.
            logger.warning(
                "[ENTRY_BLOCKED] layer=EXPOSURE reason=MAX_EXPOSURE_REACHED "
                "open_positions=%s pending_internal=%s max_total_exposure=%s stale_state=false",
                positions,
                pendings,
                MAX_TOTAL_EXPOSURE,
                action=getattr(action, "value", str(action)),
            )
            # P0-A (BUG-140): the decision is terminal — it will never become a
            # trade. Record NOT_DISPATCHED so the experience ledger cannot hang.
            emit_terminal_pending_outcome(
                experience_engine=self.om.experience_engine,
                request_id=str(getattr(decision, "request_id", "") or ""),
                state=DecisionLifecycle.NOT_DISPATCHED,
                detail="MAX_EXPOSURE_REACHED at dispatch",
            )
            return False

        # --- STRICT LOT SIZING CLAMP (HARD_MAX_LOTS + free margin pre-check) ---
        volume = self.om._clamp_dispatch_volume(volume, symbol=symbol)
        if volume <= 0.0:
            logger.warning(
                "LOT_SIZE_REJECTED: clamped volume is zero (insufficient free margin or invalid size)",
                action=getattr(action, "value", str(action)),
                symbol=symbol,
            )
            # P0-A (BUG-140): terminal NOT_DISPATCHED (never sent to broker).
            emit_terminal_pending_outcome(
                experience_engine=self.om.experience_engine,
                request_id=str(getattr(decision, "request_id", "") or ""),
                state=DecisionLifecycle.NOT_DISPATCHED,
                detail="LOT_SIZE_REJECTED (zero volume after clamp)",
            )
            return False

        # OBS-TRACE (2026-09-09): monotonic dispatch clock for MEASURED
        # audit_orders.latency on every dispatch branch below.
        _dispatch_started = time.monotonic()

        # Stage the entry context so the ledger autopsy row carries WHY we entered,
        # plus the Phase 08 execution-quality baseline (expected fill + dispatch clock).
        self.om.register_entry_context(
            order_id=getattr(decision, "request_id", "") or "",
            entry_reason=self.om._resolve_entry_reason(decision),
            ai_confidence=float(getattr(decision, "confidence", 0.0) or 0.0),
            market_regime=str(getattr(decision, "regime", "") or ""),
            expected_entry=float(getattr(decision, "proposed_entry", 0.0) or 0.0),
            dispatch_monotonic=time.monotonic(),
            setup_snapshot=setup_snapshot,
        )

        logger.info(
            "dispatch_order mapping action to MT5 command",
            action=action,
            symbol=symbol,
            volume=volume,
            execution_id=getattr(decision, "execution_id", None),
        )

        if action in (
            ActionType.BUY,
            ActionType.BUY_MARKET,
            ActionType.SELL,
            ActionType.SELL_MARKET,
        ):
            order_type = OrderType.BUY if "BUY" in action.value else OrderType.SELL
            ticket = int(
                self.om.mt5_adapter.execute_market_order(
                    symbol=symbol,
                    order_type=order_type,
                    volume=volume,
                    price=price,
                    stop_loss=sl,
                    take_profit=tp,
                )
            )
            # OBS-TRACE (2026-09-09): the *** REAL ORDER/EXECUTION EXECUTED ***
            # banner previously logged BEFORE the ticket>0 verification - a
            # broker refusal (ticket=0 retcode path) printed a success claim.
            # Evidence, not authority: the banner now only fires on a
            # broker-confirmed ticket, and the refusal is its own WARNING.
            if ticket > 0:
                logger.info(
                    f"*** REAL ORDER/EXECUTION EXECUTED ON BROKER SERVER *** Ticket: {ticket} | Action: {action.value} | Lots: {volume}"
                )
                self.om.audit.log_order(
                    ticket=ticket,
                    order_id=decision.request_id,
                    symbol=symbol,
                    action="Executed order",
                    price=price,
                    stop_loss=sl,
                    take_profit=tp,
                    volume=volume,
                    reason=f"dispatch_order {action.value} | exec={getattr(decision, 'execution_id', '') or ''}",
                    latency=max(0.0, time.monotonic() - _dispatch_started),
                    execution_mode=getattr(decision, "execution_mode", "STANDARD") or "STANDARD",
                    execution_id=getattr(decision, "execution_id", None),
                )
            else:
                logger.warning(
                    "[DISPATCH] broker returned no ticket for market order",
                    action=action.value,
                    symbol=symbol,
                    volume=volume,
                )
                # P0-A (BUG-140): market dispatch refused (retcode/ticket=0) —
                # the decision can never fill; record the terminal state.
                emit_terminal_pending_outcome(
                    experience_engine=self.om.experience_engine,
                    request_id=str(getattr(decision, "request_id", "") or ""),
                    state=DecisionLifecycle.REJECTED_UNFILLED,
                    detail="broker refused market order at dispatch (ticket=0)",
                )
            # BUG-241: consecutive broker refusals feed the SAFE_MODE breaker
            # (same 3-rejection rule the hedge path has always enforced); a
            # success resets the counter.
            if ticket > 0:
                self.om._consecutive_failures = 0
            else:
                self.om._consecutive_failures += 1
                if self.om._consecutive_failures >= 3:
                    self.om.global_state = "SAFE_MODE"
                    logger.critical(
                        "TRANSITIONED TO SAFE_MODE: 3 consecutive dispatch refusals detected!"
                    )

            # EXEC-QUALITY: record the request_id AFTER the broker call so a
            # repeat of the same request (re-fire/replay) is refused by the
            # duplicate-dispatch guard above regardless of fill outcome.
            if dispatch_request_id:
                self.om._processed_orders[dispatch_request_id] = ticket > 0
            return ticket > 0

        elif action in (
            ActionType.BUY_LIMIT,
            ActionType.SELL_LIMIT,
            ActionType.BUY_STOP,
            ActionType.SELL_STOP,
        ):
            if action == ActionType.BUY_LIMIT:
                order_type = OrderType.BUY_LIMIT
            elif action == ActionType.SELL_LIMIT:
                order_type = OrderType.SELL_LIMIT
            elif action == ActionType.BUY_STOP:
                order_type = OrderType.BUY_STOP
            else:
                order_type = OrderType.SELL_STOP

            ticket = int(
                self.om.mt5_adapter.place_pending_order(
                    symbol=symbol,
                    order_type=order_type,
                    volume=volume,
                    price=price,
                    stop_loss=sl,
                    take_profit=tp,
                )
            )
            if ticket > 0:
                logger.info(
                    f"*** REAL ORDER/EXECUTION EXECUTED ON BROKER SERVER *** Ticket: {ticket} | Action: {action.value} | Lots: {volume}"
                )
                self.om.audit.log_order(
                    ticket=ticket,
                    order_id=decision.request_id,
                    symbol=symbol,
                    action="Generated candidate",
                    price=price,
                    stop_loss=sl,
                    take_profit=tp,
                    volume=volume,
                    reason=f"dispatch_order pending {action.value} | exec={getattr(decision, 'execution_id', '') or ''}",
                    latency=max(0.0, time.monotonic() - _dispatch_started),
                    execution_mode=getattr(decision, "execution_mode", "STANDARD") or "STANDARD",
                    execution_id=getattr(decision, "execution_id", None),
                )
            else:
                logger.error(
                    f"Pending order dispatch rejected by broker server | Action: {action.value} | Lots: {volume}"
                )
                # P0-A (BUG-140): broker refused the pending order at dispatch —
                # the decision can never fill; record the terminal state.
                emit_terminal_pending_outcome(
                    experience_engine=self.om.experience_engine,
                    request_id=str(getattr(decision, "request_id", "") or ""),
                    state=DecisionLifecycle.REJECTED_UNFILLED,
                    detail="broker rejected pending order at dispatch (ticket=0)",
                )
            # BUG-241: pending dispatch refusals feed the same breaker.
            if ticket > 0:
                self.om._consecutive_failures = 0
            else:
                self.om._consecutive_failures += 1
                if self.om._consecutive_failures >= 3:
                    self.om.global_state = "SAFE_MODE"
                    logger.critical(
                        "TRANSITIONED TO SAFE_MODE: 3 consecutive pending refusals detected!"
                    )

            # EXEC-QUALITY: same duplicate-dispatch bookkeeping as the market
            # path — every sent request_id is terminal (filled or refused).
            if dispatch_request_id:
                self.om._processed_orders[dispatch_request_id] = ticket > 0
            return ticket > 0

        return False

    def _resolve_entry_reason(self, decision: Any) -> str:
        """
        Normalizes the policy decision into one of the canonical ledger entry reasons:
        SMC_GOD_MODE, FAST_LIQUIDITY_SWEEP, or PURE_AI.
        """
        execution_mode = str(getattr(decision, "execution_mode", "") or "")
        reason_code = str(getattr(decision, "reason_code", "") or "")

        if "SMC_GOD_MODE" in execution_mode or "SMC_GOD_MODE" in reason_code:
            return "SMC_GOD_MODE"
        if "SWEEP" in reason_code.upper() or "TICK_SWEEP" in execution_mode:
            return "FAST_LIQUIDITY_SWEEP"
        return "PURE_AI"
