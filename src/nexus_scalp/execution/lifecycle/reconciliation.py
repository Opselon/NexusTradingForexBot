"""ReconciliationEngine — broker-truth close reconciliation + vanished-ticket autopsy.

P0 seam S8 (god-file decomposition, extraction 2 of the order-manager wave):
the reconciliation close-loop, the vanished-ticket autopsy pipeline, and the
experience-outcome forwarding move OUT of ``OrderLifecycleManager`` verbatim
(behavior-preserving extraction). The manager composes this engine and keeps
thin delegates; state remains in the canonical stores the manager already
owns — the engine reads/writes THROUGH the manager's audited access surface
(``om``), never through copies.

Moved methods (verbatim bodies, ``self`` -> ``om``):
    reconcile_missed_closes      : BUG-045 spec-23 restart close-loop
    _sweep-era per-ticket autopsy: BUG-046/088/089 evidence chain
    _record_experience_outcome   : Phase-08 learning hand-off (never raises)

Ownership contract:
    READS   : adapter (broker truth), audit (durable deals), canonical
              ticket-state views, account/equity probes
    WRITES  : audit autopsy rows, experience outcomes, tombstone sets
              (via om's canonical stores)
    AUTHORITY: NONE — no dispatch, no protective modification, no risk math.
"""

from __future__ import annotations

import contextlib
import json
import time
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.domain.models import SymbolInfo, TickData
from nexus_scalp.experience.outcome_recovery import (
    classify_exit_with_evidence,
    reconstruct_broker_outcome,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.execution.lifecycle.reconciliation")


class ReconciliationEngine:
    """Broker-truth reconciliation + autopsy + experience attribution."""

    def __init__(self, om: Any) -> None:
        # Composition root (OrderLifecycleManager). The engine deliberately
        # accesses the manager's canonical state surface instead of copying
        # per-ticket dicts (single source of truth, S5 discipline).
        self.om = om


    def reconcile_missed_closes(
        self,
        symbol: str,
        current_tick: TickData,
        symbol_info: SymbolInfo | None = None,
        hours_back: int = 24,
    ) -> int:
        """
        Phase 14 reconciliation close-loop (BUG-045 spec 23).

        After a restart (or a missed broker close event) the internal ticket
        trackers may be empty while the broker history shows a position that
        was already closed. This method queries the authoritative broker deal
        history and, for every closed position ticket that has a ledger OPENED
        row but no CLOSED row AND no internal tracking, emits the same
        autopsy + experience outcome path as a live close.

        Never raises: reconciliation is a best-effort background concern.
        Learning never blocks protective execution (the close already
        happened at the broker - this only records it).

        Returns the number of missed closes reconciled.
        """
        try:
            # BUG-090 (TASK-7 perf): the reconciliation close-loop must never fetch
            # broker history on every tick. Gate the fetch to once per 60s and skip
            # it entirely when no OPENED-without-CLOSED ledger row exists (the only
            # condition that can produce a reconcile).
            now_mono = time.monotonic()
            if (now_mono - self.om._last_reconcile_attempt) < 60.0:
                return 0
            self.om._last_reconcile_attempt = now_mono
            try:
                pending = self.om.audit.count_ledger_opened_unclosed()
            except Exception:
                pending = -1  # pre-check unavailable: fall through to the fetch
            if pending == 0:
                return 0
            history_deals = self.om.adapter.get_closed_deals_history(
                symbol=symbol, hours_back=hours_back
            )
            if not history_deals:
                return 0

            # Ticket -> aggregated deal evidence (partial closes merge into one).
            ticket_deals: dict[int, list[dict[str, Any]]] = {}
            for d in history_deals:
                pt = d.get("position_ticket")
                if pt is not None:
                    ticket_deals.setdefault(int(pt), []).append(d)

            reconciled = 0
            now = current_tick.timestamp
            for ticket, deals in ticket_deals.items():
                if ticket in self.om._entry_timestamps:
                    continue  # already tracked/closed through the live path
                if self.om._reconcile_seen.get(ticket, False):
                    continue
                # Only reconcile positions we can attribute to this engine
                # (a ledger OPENED placeholder exists for the ticket).
                if not self.om.audit.has_ledger_opened(ticket):
                    continue

                matched = deals[0]
                entry = float(matched.get("entry_price", 0.0) or 0.0)
                exit_price = float(matched.get("price", 0.0) or 0.0)
                direction = str(matched.get("direction", "BUY") or "BUY")
                vol = float(matched.get("volume", 0.0) or 0.0)
                profit_usd = float(matched.get("profit", 0.0) or 0.0)

                # Entry context recovered from the ledger OPENED row.
                opened = self.om.audit.get_ledger_opened(ticket)
                if opened:
                    entry = float(opened.get("entry_price", entry) or entry)
                    direction = str(opened.get("direction", direction) or direction)
                    vol = float(opened.get("volume", vol) or vol)
                    # Phase 14: restore the originating request_id so the
                    # experience outcome is attributed to the ORIGINAL decision
                    # (ORIGINAL_REQUEST provenance), not a fallback.
                    opened_order_id = str(opened.get("order_id", "") or "")
                    if opened_order_id:
                        self.om._entry_order_ids[ticket] = opened_order_id
                        self.om._entry_reasons[ticket] = (
                            str(opened.get("entry_reason", "") or "") or "PURE_AI"
                        )
                        self.om._entry_confidences[ticket] = float(
                            opened.get("ai_confidence_at_open", 0.0) or 0.0
                        )
                        self.om._entry_regimes[ticket] = str(
                            opened.get("market_regime_at_open", "") or ""
                        )

                atr = max(self.om._safe_feature_float(None, "atr_m1", 0.80), 0.50)
                initial_sl = float(opened.get("initial_sl_price", 0.0) or 0.0)
                final_sl = float(matched.get("sl", initial_sl) or initial_sl)
                broker_outcome = reconstruct_broker_outcome(
                    ticket=ticket,
                    symbol=symbol,
                    direction=direction,
                    deals=deals,
                    matched_deal=None,
                    entry_price=entry,
                    initial_sl=initial_sl,
                    final_sl=final_sl,
                    tp_price=float(matched.get("tp", 0.0) or 0.0),
                    volume=vol,
                    fallback_exit_price=exit_price,
                    close_time=now,
                    entry_time=None,
                )
                (
                    exit_mechanism,
                    exit_reason_source,
                    exit_evidence,
                    exit_reason_confidence,
                ) = classify_exit_with_evidence(
                    deal_reason_code=int(matched.get("reason", 0) or 0),
                    comment=matched.get("comment", ""),
                    profit_usd=profit_usd,
                    exit_price=exit_price,
                    tp_price=float(matched.get("tp", 0.0) or 0.0),
                    sl_price=float(matched.get("sl", 0.0) or 0.0),
                    final_sl=final_sl,
                    entry_price=entry,
                    was_sl_modified=bool(initial_sl and abs(final_sl - initial_sl) > 1e-9),
                    direction=direction,
                )

                # Persist the same single autopsy row the live path writes.
                self.om.audit.log_ledger_closed(
                    ticket=ticket,
                    symbol=symbol,
                    direction=direction,
                    volume=vol,
                    entry_price=entry,
                    exit_price=broker_outcome.exit_price,
                    status="RECONCILED",
                    pnl=broker_outcome.gross_profit,
                    commission=broker_outcome.commission,
                    swap=broker_outcome.swap,
                    duration_sec=0.0,
                    timestamp_str=now.isoformat() if hasattr(now, "isoformat") else str(now),
                    mae=0.0,
                    mfe=0.0,
                    initial_sl_price=initial_sl,
                    final_sl_price=final_sl,
                    is_risk_free_hit=1 if "BREAK_EVEN" in exit_mechanism else 0,
                    exit_mechanism=exit_mechanism,
                    order_id=opened.get("order_id", "") if opened else "",
                    open_time=opened.get("open_time", "") if opened else "",
                    close_time=now.isoformat() if hasattr(now, "isoformat") else str(now),
                    entry_reason=opened.get("entry_reason", "") if opened else "",
                    ai_confidence_at_open=float(opened.get("ai_confidence_at_open", 0.0) or 0.0),
                    market_regime_at_open=opened.get("market_regime_at_open", "") if opened else "",
                    was_sl_modified=1 if (initial_sl and abs(final_sl - initial_sl) > 1e-9) else 0,
                    mae_usd=0.0,
                    mfe_usd=0.0,
                    account_balance_after=self.om._last_account_balance,
                    account_equity_after=self.om._last_account_equity,
                    drawdown_percent_after=self.om._current_drawdown_percent(),
                    exit_reason_source=exit_reason_source,
                    exit_evidence=exit_evidence,
                    exit_reason_confidence=exit_reason_confidence,
                    reversal_events_json=json.dumps(self.om._reversal_events.get(ticket, [])),
                    account_source=self.om._ledger_account_source(),
                )

                if self.om.experience_engine is not None:
                    self._record_experience_outcome(
                        dead_ticket=ticket,
                        now=now,
                        entry=entry,
                        exit_price=broker_outcome.exit_price,
                        initial_sl_val=initial_sl,
                        vol=vol,
                        atr=atr,
                        symbol_info=symbol_info,
                        profit_usd=broker_outcome.gross_profit,
                        comm_usd=broker_outcome.commission,
                        swap_usd=broker_outcome.swap,
                        mae_val=0.0,
                        mfe_val=0.0,
                        mae_usd=0.0,
                        mfe_usd=0.0,
                        duration_sec=0.0,
                        exit_mechanism=exit_mechanism,
                        was_sl_modified=bool(initial_sl and abs(final_sl - initial_sl) > 1e-9),
                        broker_outcome=broker_outcome,
                    )

                self.om._reconcile_seen[ticket] = True
                self.om._closed_tickets[ticket] = True
                self.om._exit_pending_final_reason.pop(ticket, None)
                reconciled += 1
                logger.info(
                    "[RECONCILIATION] missed close recorded",
                    ticket=ticket,
                    exit_mechanism=exit_mechanism,
                    pnl=broker_outcome.gross_profit,
                )
            return reconciled
        except Exception as err:
            logger.error("[RECONCILIATION] pass failed (isolated)", error=str(err))
            return 0

    def _record_experience_outcome(
        self,
        dead_ticket: int,
        now: datetime,
        entry: float,
        exit_price: float,
        initial_sl_val: float,
        vol: float,
        atr: float,
        symbol_info: SymbolInfo | None,
        profit_usd: float,
        comm_usd: float,
        swap_usd: float,
        mae_val: float,
        mfe_val: float,
        mae_usd: float,
        mfe_usd: float,
        duration_sec: float,
        exit_mechanism: str,
        was_sl_modified: bool,
        request_id: str = "",
        broker_outcome: Any = None,
    ) -> None:
        """
        Forwards a closed position to the Phase 08 experience layer.

        Responsibilities kept strictly here (never inside the experience layer):
          * resolve the originating proposal `request_id` for attribution
          * convert USD PnL into a risk-normalised R multiple
          * hand over the observed execution/behaviour evidence

        Phase 14 (BUG-045): when the in-memory request_id map is empty (lost
        across restart / reconciliation), the broker ticket is forwarded so the
        experience layer can attempt deterministic correlation recovery. The
        outcome is NEVER silently discarded while a correlatable decision may
        exist. The reconstructed broker outcome is passed through so the
        authoritative deal result survives into the experience record.

        This method NEVER raises: the learning layer is non-critical and the
        financial autopsy row has already been persisted by the caller.
        """
        try:
            req_id = self.om._entry_order_ids.get(dead_ticket, "") or request_id
            if not req_id:
                # No originating request id in memory: forward the broker ticket
                # as the correlation key; the experience layer attempts
                # deterministic recovery (ORIGINAL_REQUEST / POSITION_STATE /
                # BROKER_TICKET_FALLBACK) and only then may reject with full
                # diagnostics. It never silently discards (BUG-045).
                req_id = ""
                correlation_ticket = str(dead_ticket)
            else:
                correlation_ticket = req_id

            sl_distance = abs(entry - initial_sl_val) if initial_sl_val > 0.0 else (atr * 1.5)
            contract_sz = self.om._resolve_contract_size(symbol_info)
            risk_usd = max(1.0, sl_distance * max(vol, 0.0) * contract_sz)
            # BUG-046: never silently treat missing broker truth as zero PnL.
            # When profit_usd is unknown (no broker deal AND no price evidence),
            # record the outcome as UNKNOWN so research never sees a fake R=0.
            if profit_usd is None:
                net_pnl_usd = 0.0
                r_multiple = 0.0
                logger.warning(
                    "[BROKER_OUTCOME] event=RECONSTRUCTION_UNKNOWN",
                    ticket=dead_ticket,
                    reason="NO_BROKER_DEAL_AND_NO_PRICE_EVIDENCE",
                    realized_r="UNKNOWN",
                )
            else:
                net_pnl_usd = profit_usd - (comm_usd or 0.0) - (swap_usd or 0.0)
                r_multiple = net_pnl_usd / risk_usd

            expected_entry = self.om._entry_expected_price.get(dead_ticket, 0.0)
            direction = self.om._entry_directions.get(dead_ticket, "BUY")
            slippage_points = 0.0
            if expected_entry > 0.0 and entry > 0.0:
                raw = entry - expected_entry
                slippage_points = raw if "BUY" in str(direction).upper() else -raw

            broker_payload = None
            if broker_outcome is not None:
                try:
                    broker_payload = broker_outcome.model_dump()
                except Exception:
                    broker_payload = None

            self.om.experience_engine.record_trade_outcome(
                request_id=req_id,
                execution_id=correlation_ticket if not req_id else str(dead_ticket),
                outcome_timestamp=now,
                is_executed=True,
                is_closed=True,
                exit_reason=exit_mechanism,
                realized_pnl_usd=net_pnl_usd,
                realized_r_multiple=r_multiple,
                mae_points=mae_val,
                mfe_points=mfe_val,
                mae_usd=mae_usd,
                mfe_usd=mfe_usd,
                holding_duration_seconds=duration_sec,
                approved_volume=vol,
                actual_entry=entry,
                slippage_points=slippage_points,
                execution_latency_ms=self.om._entry_fill_latency_ms.get(dead_ticket, 0.0),
                spread_at_execution=self.om._entry_spread.get(dead_ticket, 0.0),
                initial_sl_distance=sl_distance,
                sl_moved=was_sl_modified,
                partial_closed=bool(self.om._partial_closed_tickets.get(dead_ticket, False)),
                atr_at_entry=self.om._entry_atr.get(dead_ticket, atr),
                time_to_mae_sec=self.om._time_to_mae_sec.get(dead_ticket, 0.0),
                time_to_mfe_sec=self.om._time_to_mfe_sec.get(dead_ticket, 0.0),
                broker_outcome=broker_payload,
            )
        except Exception as exp_err:
            logger.error(
                "[EXPERIENCE] outcome forwarding failed (isolated)",
                ticket=dead_ticket,
                error=str(exp_err),
            )

    def _autopsy_vanished_ticket(
        self,
        dead_ticket: int,
        history_deals: list[dict[str, Any]],
        symbol: str,
        now: datetime,
        current_tick: TickData,
        symbol_info: SymbolInfo | None,
        atr: float,
        hours_back: int,
    ) -> None:
        """Per-ticket vanished-position autopsy (S6): resolve the closing
        deal (live window + BUG-088/089 durable fallback), write the single
        data-rich autopsy row, record the experience outcome, emit telemetry,
        and release per-ticket state. Moved VERBATIM from
        _sweep_dead_tickets' per-ticket loop (no accumulators, no skips)."""
        entry = self.om._entry_prices.get(dead_ticket, 0.0)
        tp_price = self.om._entry_tps.get(dead_ticket, 0.0)
        sl_price = self.om._entry_sls.get(dead_ticket, 0.0)
        entry_time = self.om._entry_timestamps.get(dead_ticket)
        duration_sec = (now - entry_time).total_seconds() if entry_time else 0.0
        vol = self.om._last_known_volume.get(dead_ticket, 0.0)
        direction = self.om._entry_directions.get(dead_ticket, "BUY")

        matched_deal = next(
            (d for d in history_deals if d.get("position_ticket") == dead_ticket), None
        )
        if matched_deal is None:
            # BUG-088/089 (TASK-7): the live 24h deal window can miss a
            # close (restart gap, window expiry). Fall back to the DURABLE
            # broker-deal capture (audit_broker_deals, position_id join)
            # before conceding FALLBACK_ESTIMATE/UNKNOWN.
            try:
                durable = self.om.audit.get_broker_deals_for_position(dead_ticket)
            except Exception:
                durable = []
            if durable:
                matched_deal = durable[0]
                history_deals = list(history_deals) + durable
                logger.debug(
                    "[BROKER_OUTCOME] event=DURABLE_DEAL_FALLBACK",
                    ticket=dead_ticket,
                    deals=len(durable),
                )

        # ------------------------------------------------------------------
        # BUG-046 FIX: never default missing broker truth to zero.
        # When no deal matched, realized PnL is UNKNOWN, not $0. The old
        # code wrote profit_usd=0.0 which corrupted every closed outcome
        # (R=0) and starved the research engine. A deterministic
        # price-delta FALLBACK_ESTIMATE is used ONLY when entry/exit
        # prices + volume + contract size are all authoritative; otherwise
        # the value is left None and the outcome layer records UNKNOWN.
        # ------------------------------------------------------------------
        profit_usd = None
        swap_usd = None
        comm_usd = None
        exit_price = entry
        status_str = "CLOSED"

        if matched_deal:
            profit_usd = matched_deal.get("profit", 0.0)
            swap_usd = matched_deal.get("swap", 0.0)
            comm_usd = matched_deal.get("commission", 0.0)
            exit_price = matched_deal.get("price", 0.0)
            deal_reason_code = matched_deal.get("reason", 0)
            comment = matched_deal.get("comment", "")
            logger.debug(
                "[BROKER_OUTCOME] event=MATCHED",
                ticket=dead_ticket,
                source="BROKER_HISTORY",
                position_ticket=matched_deal.get("position_ticket"),
                profit=profit_usd,
            )

            if "NSE_CLOSE" in comment or "emergency" in comment.lower() or "cut" in comment.lower():
                status_str = "MANUALLY_CLOSED"
            elif (
                deal_reason_code == 5  # DEAL_REASON_TP (BUG-083)
                or "tp" in comment.lower()
                or (profit_usd > 0 and abs(exit_price - tp_price) < 0.10)
            ):
                status_str = "CLOSED_TP"
            elif (
                deal_reason_code in (4, 6)  # DEAL_REASON_SL / SO (BUG-083)
                or "sl" in comment.lower()
                or (profit_usd < 0 and abs(exit_price - sl_price) < 0.10)
            ):
                status_str = "CLOSED_SL"
            else:
                status_str = "MANUALLY_CLOSED" if deal_reason_code in (1, 2) else "CLOSED"
        else:
            # No broker deal matched. Fall back to a deterministic price
            # estimate ONLY when authoritative prices are available, and
            # flag it explicitly (FALLBACK_ESTIMATE) so consumers know it
            # is not broker truth.
            exit_price = current_tick.bid if direction == "BUY" else current_tick.ask
            if entry > 0.0 and exit_price > 0.0 and vol > 0.0:
                contract_sz = self.om._resolve_contract_size(symbol_info)
                price_delta = (
                    (exit_price - entry)
                    if "BUY" in str(direction).upper()
                    else (entry - exit_price)
                )
                profit_usd = float(price_delta) * float(vol) * contract_sz
                swap_usd = 0.0
                comm_usd = 0.0
                logger.debug(
                    "[BROKER_OUTCOME] event=RECONSTRUCTION_FALLBACK",
                    ticket=dead_ticket,
                    source="FALLBACK_ESTIMATE",
                    entry=entry,
                    exit=exit_price,
                    volume=vol,
                    estimated_profit=profit_usd,
                )
            else:
                # Not enough evidence: explicit UNKNOWN (never zero).
                logger.warning(
                    "[BROKER_OUTCOME] event=MATCH_FAILED",
                    ticket=dead_ticket,
                    reason="NO_BROKER_DEAL_AND_NO_PRICE_EVIDENCE",
                    searched_hours_back=hours_back,
                    deals_found=len(history_deals),
                )

        # =============================================================
        # MODULE A: SINGLE DATA-RICH AUTOPSY ROW PER CLOSED TRADE
        # =============================================================
        mae_val = float(self.om._mae_tracker.get(dead_ticket, 0.0))
        mfe_val = float(self.om._mfe_tracker.get(dead_ticket, 0.0))
        initial_sl_val = float(sl_price)
        final_sl_val = float(self.om._last_modify_sl.get(dead_ticket, initial_sl_val))

        # was_sl_modified: True only when trailing/breakeven actually shifted the SL.
        # Phase 14: also honour the explicit modification flag - a
        # breakeven/trailing lock that was applied in-process but later
        # reconciled must survive the autopsy (BUG-045 anomaly E).
        was_sl_modified = bool(
            self.om._sl_modified_flags.get(dead_ticket, False)
            or abs(final_sl_val - initial_sl_val) > 1e-9
        )

        # is_risk_free_hit: closed on a stop that had already been moved into profit.
        is_risk_free_hit = 0
        if direction == "BUY":
            if final_sl_val >= entry and abs(exit_price - final_sl_val) < 0.15:
                is_risk_free_hit = 1
        elif final_sl_val <= entry and final_sl_val > 0.0 and abs(exit_price - final_sl_val) < 0.15:
            is_risk_free_hit = 1

        # ---- Exit mechanism resolution (engine intent overrides broker heuristic) ----
        forced_mechanism = self.om._forced_exit_mechanisms.pop(dead_ticket, None)
        # Phase 14: map to the canonical taxonomy via broker evidence
        # (DEAL_REASON + SL/TP geometry + protective context). A stop-out
        # is NEVER labelled MANUAL_CLOSE merely because the internal
        # state machine performed protection logic first (BUG-045).
        (
            exit_mechanism,
            exit_reason_source,
            exit_evidence,
            exit_reason_confidence,
        ) = classify_exit_with_evidence(
            deal_reason_code=matched_deal.get("reason", 0) if matched_deal else 0,
            comment=matched_deal.get("comment", "") if matched_deal else "",
            profit_usd=profit_usd,
            exit_price=exit_price,
            tp_price=tp_price,
            sl_price=sl_price,
            final_sl=final_sl_val,
            entry_price=entry,
            was_sl_modified=bool(was_sl_modified),
            direction=direction,
            forced_mechanism=forced_mechanism,
        )

        # Phase 14: authoritative broker closure reconstruction. When the
        # broker deal evidence is available (multi-deal aggregation
        # included), the realized result comes from the DEAL path - never
        # from a stale floating-PnL default of zero (BUG-045).
        broker_outcome = reconstruct_broker_outcome(
            ticket=dead_ticket,
            symbol=symbol,
            direction=direction,
            deals=history_deals,
            matched_deal=matched_deal,
            entry_price=entry,
            initial_sl=initial_sl_val,
            final_sl=final_sl_val,
            tp_price=tp_price,
            volume=vol,
            fallback_exit_price=exit_price,
            close_time=now,
            entry_time=entry_time,
        )
        if broker_outcome.reconstruction_source != "NONE":
            profit_usd = broker_outcome.gross_profit
            comm_usd = broker_outcome.commission
            swap_usd = broker_outcome.swap
            exit_price = broker_outcome.exit_price
            # BUG-088 (TASK-7): when the broker reconstruction aggregated
            # multiple OUT deals, reclassify on the AGGREGATE PnL + the
            # aggregated comment/reason so a partial-fill family is never
            # classified by a single deal's sign.
            if len(history_deals) > 1:
                with contextlib.suppress(Exception):
                    deal_gross = sum(
                        float(d.get("profit", 0.0) or 0.0)
                        for d in history_deals
                        if d.get("position_ticket") == dead_ticket
                    )
                    profit_usd = deal_gross

        # ---- Quant risk excursions converted to account currency ----
        mae_usd = self.om._price_delta_to_usd(min(mae_val, 0.0), vol, symbol_info)
        mfe_usd = self.om._price_delta_to_usd(max(mfe_val, 0.0), vol, symbol_info)
        # Prefer directly observed USD peaks when the tick loop tracked them.
        peak_dd_usd = float(self.om._peak_drawdown_usd.get(dead_ticket, 0.0))
        peak_win_usd = float(self.om._peak_profit_usd.get(dead_ticket, 0.0))
        if peak_dd_usd < 0.0:
            mae_usd = peak_dd_usd
        if peak_win_usd > 0.0:
            mfe_usd = peak_win_usd

        open_time_str = (
            entry_time.isoformat() if hasattr(entry_time, "isoformat") else str(entry_time or "")
        )
        close_time_str = now.isoformat() if hasattr(now, "isoformat") else str(now)

        self.om.audit.log_ledger_closed(
            ticket=dead_ticket,
            symbol=symbol,
            direction=direction,
            volume=vol,
            entry_price=entry,
            exit_price=exit_price,
            status=status_str,
            pnl=profit_usd,
            commission=comm_usd,
            swap=swap_usd,
            duration_sec=duration_sec,
            timestamp_str=close_time_str,
            mae=mae_val,
            mfe=mfe_val,
            initial_sl_price=initial_sl_val,
            final_sl_price=final_sl_val,
            is_risk_free_hit=is_risk_free_hit,
            exit_mechanism=exit_mechanism,
            # --- Institutional autopsy fields ---
            order_id=self.om._entry_order_ids.get(dead_ticket, ""),
            open_time=open_time_str,
            close_time=close_time_str,
            entry_reason=self.om._entry_reasons.get(dead_ticket, ""),
            ai_confidence_at_open=self.om._entry_confidences.get(dead_ticket, 0.0),
            market_regime_at_open=self.om._entry_regimes.get(dead_ticket, ""),
            was_sl_modified=int(was_sl_modified),
            mae_usd=mae_usd,
            mfe_usd=mfe_usd,
            account_balance_after=self.om._last_account_balance,
            account_equity_after=self.om._last_account_equity,
            drawdown_percent_after=self.om._current_drawdown_percent(),
            # DEBUG-AUDIT (2026-08-18): full chart-state fingerprint at
            # dispatch persisted to the ledger for post-hoc setup/strategy
            # attribution of every closed trade.
            entry_setup_snapshot=json.dumps(self.om._entry_setup_snapshots.get(dead_ticket, {})),
            exit_reason_source=exit_reason_source,
            exit_evidence=exit_evidence,
            exit_reason_confidence=exit_reason_confidence,
            reversal_events_json=json.dumps(self.om._reversal_events.get(dead_ticket, [])),
            account_source=self.om._ledger_account_source(),
        )

        # =============================================================
        # PHASE 08: EXPERIENCE OUTCOME ATTRIBUTION
        # -------------------------------------------------------------
        # Records the append-only outcome for the decision that produced
        # this ticket, including full execution/behaviour evidence so the
        # experience layer can attribute the result across strategy,
        # entry, management, exit and execution quality.
        #
        # Fully isolated: any failure here is logged and ignored. The
        # autopsy row above is already persisted, and no execution
        # decision depends on this call.
        # =============================================================
        if self.om.experience_engine is not None:
            self._record_experience_outcome(
                dead_ticket=dead_ticket,
                now=now,
                entry=entry,
                exit_price=exit_price,
                initial_sl_val=initial_sl_val,
                vol=vol,
                atr=atr,
                symbol_info=symbol_info,
                profit_usd=profit_usd,
                comm_usd=comm_usd,
                swap_usd=swap_usd,
                mae_val=mae_val,
                mfe_val=mfe_val,
                mae_usd=mae_usd,
                mfe_usd=mfe_usd,
                duration_sec=duration_sec,
                exit_mechanism=exit_mechanism,
                was_sl_modified=bool(was_sl_modified),
                broker_outcome=broker_outcome,
            )

        net_pnl_log = "UNKNOWN"
        if profit_usd is not None:
            net_pnl_log = f"${(profit_usd - (comm_usd or 0.0) - (swap_usd or 0.0)):+.2f}"
            self.om._net_pnl_by_ticket[dead_ticket] = (
                float(profit_usd) - float(comm_usd or 0.0) - float(swap_usd or 0.0)
            )
        self.om._exit_mechanism_by_ticket[dead_ticket] = exit_mechanism

        logger.info(
            "[LEDGER AUTOPSY] Closed trade recorded",
            ticket=dead_ticket,
            direction=direction,
            exit_mechanism=exit_mechanism,
            entry_reason=self.om._entry_reasons.get(dead_ticket, ""),
            net_pnl=net_pnl_log,
            mae_usd=f"${mae_usd:+.2f}",
            mfe_usd=f"${mfe_usd:+.2f}",
            was_sl_modified=was_sl_modified,
        )

        if self.om.notifier:
            try:
                msg_id = self.om._order_message_ids.get(dead_ticket)
                orig_risk = self.om._initial_risks.get(dead_ticket, 0.0)
                profit_pct = 0.0
                if profit_usd is not None and entry > 0.0:
                    profit_pct = abs(exit_price - entry) / entry * 100.0
                    if (profit_usd + (swap_usd or 0.0) + (comm_usd or 0.0)) < 0:
                        profit_pct = -profit_pct

                total_net_profit = (
                    profit_usd + (swap_usd or 0.0) + (comm_usd or 0.0)
                    if profit_usd is not None
                    else 0.0
                )

                if (
                    status_str == "MANUALLY_CLOSED"
                    and matched_deal
                    and (
                        "NSE_CLOSE" in matched_deal.get("comment", "")
                        or "emergency" in matched_deal.get("comment", "").lower()
                        or "cut" in matched_deal.get("comment", "").lower()
                    )
                ):
                    mae_val = self.om._mae_tracker.get(dead_ticket, 0.0)
                    dd_pct = (abs(mae_val) / max(atr, 0.50)) * 100.0
                    self.om.notifier.notify_emergency_cut(
                        ticket=dead_ticket,
                        score=self.om._hold_score_tracker.get(dead_ticket, 100),
                        reasons=matched_deal.get("comment", "")
                        if matched_deal
                        else "NSE Emergency Cut",
                        saved_usd=abs(total_net_profit)
                        if total_net_profit < 0
                        else total_net_profit,
                        trigger_source="Algorithm Position Router",
                        drawdown_pct=dd_pct,
                        reply_to_message_id=msg_id,
                    )
                elif status_str == "CLOSED_TP":
                    self.om.notifier.notify_tp_touched(
                        ticket=dead_ticket,
                        symbol=symbol,
                        entry=entry,
                        tp_price=tp_price,
                        exit_price=exit_price,
                        profit_usd=total_net_profit,
                        profit_pct=profit_pct,
                        duration_sec=duration_sec,
                        reply_to_message_id=msg_id,
                    )
                elif status_str == "CLOSED_SL":
                    self.om.notifier.notify_sl_touched(
                        ticket=dead_ticket,
                        symbol=symbol,
                        entry=entry,
                        sl_price=sl_price,
                        exit_price=exit_price,
                        loss_usd=total_net_profit,
                        loss_pct=profit_pct,
                        duration_sec=duration_sec,
                        risk_usd=orig_risk,
                        reply_to_message_id=msg_id,
                    )
                else:
                    # BUG-081: Telegram consumes the CANONICAL outcome.
                    # The exit label/evidence come from the same classifier
                    # result written to the ledger (AccountingCore /
                    # ExperienceLedger) — never re-inferred from the broker
                    # reason code, and never defaulted to MANUAL.
                    self.om.notifier.notify_canonical_close(
                        ticket=dead_ticket,
                        symbol=symbol,
                        entry=entry,
                        exit_price=exit_price,
                        profit_usd=total_net_profit,
                        duration_sec=duration_sec,
                        exit_reason=exit_mechanism,
                        evidence=f"{exit_reason_source} | {exit_evidence}",
                        initial_sl=initial_sl_val,
                        final_sl=final_sl_val,
                        strategy=self.om._entry_reasons.get(dead_ticket, ""),
                        regime=self.om._entry_regimes.get(dead_ticket, ""),
                        confidence=self.om._entry_confidences.get(dead_ticket, 0.0),
                        realized_r=total_net_profit / max(orig_risk, 1e-9)
                        if orig_risk > 0.0
                        else 0.0,
                        mfe_usd=mfe_usd,
                        mae_usd=mae_usd,
                        reply_to_message_id=msg_id,
                    )
            except Exception as e:
                logger.error("Failed to notify closed trade", error=e)
