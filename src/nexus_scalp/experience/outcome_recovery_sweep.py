"""Historical missing-outcome recovery sweep (P0-B, BUG-140).

Closes the research-evidence gap where a DECISION exists in the immutable
experience ledger but NO outcome row was ever persisted. Forensic evidence
(2026-08-28, production DB): 273 such decisions, all inside audit_orders
coverage, classified by broker truth as:

    * 7   filled AND closed with full deal evidence  -> reconstructed trade
      outcome (R/PnL from audit_broker_deals, never fabricated)
    * 11  canceled before any fill                   -> CANCELED_UNFILLED
    * 255 never dispatched (no audit_orders row)     -> left for the live
      terminal writer (P0-A wiring covers these going forward); the sweep
      reports them but does NOT backfill a lifecycle state without dispatch
      evidence, because "no dispatch row" is not proof of "not dispatched"
      (the row may have been purged).

Join chain (deterministic, evidence-based):

    audit_experiences.request_id
      -> audit_orders.order_id            (dispatch log, engine-written)
      -> audit_broker_orders.ticket       (broker order state: MT5 ORDER_STATE)
      -> audit_broker_deals.position_id   (fallback: deals."order" = ticket)

MT5 ORDER_STATE mapping used for classification:
    2 CANCELED, 3 PARTIAL, 4 FILLED, 5 REJECTED, 6 EXPIRED
    (0 STARTED / 1 PLACED => still live; the sweep leaves those alone)

Safety properties:
    * append-only: outcomes are written through the idempotent ExperienceLedger
      (UNIQUE idempotency_key); repeated runs converge, never duplicate.
    * no fabrication: R/PnL come only from broker deal rows; a fill without
      close deals is SKIPPED (open position or incomplete history), not
      zero-substituted.
    * causality guarded: an outcome whose close time precedes its decision is
      refused, never clamped.
    * bounded: max_decisions cap, per-decision failure isolation.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.experience.lifecycle import (
    RECOVERY_SOURCE_BROKER_HISTORY,
    DecisionLifecycle,
    build_terminal_non_trade_outcome,
)
from nexus_scalp.experience.models import (
    BrokerOutcome,
    ExecutionContext,
    ExperienceOutcome,
    OutcomeDecomposition,
    PositionBehavior,
)
from nexus_scalp.experience.outcome_recovery import (
    classify_exit_with_evidence,
    reconstruct_broker_outcome,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.experience.outcome_recovery_sweep")

#: MT5 DEAL_ENTRY: 0 in / 1 out / 2 inout / 3 out-by. 1/2/3 close volume.
_DEAL_ENTRY_IN = 0
_DEAL_ENTRY_OUT = (1, 2, 3)
#: MT5 ORDER_STATE values that prove a fill (partial or complete).
_ORDER_STATE_FILLED = (3, 4)
_ORDER_STATE_CANCELED = 2
_ORDER_STATE_EXPIRED = 6
_ORDER_STATE_REJECTED = 5

#: Fallback contract size (XAUUSD = 100 oz/lot) when the symbol is unknown.
#: Matches the OutcomeRepairJob convention; injectable for tests/other symbols.
DEFAULT_CONTRACT_SIZE = 100.0


def _broker_epoch_to_utc(epoch_sec: int) -> datetime | None:
    if not epoch_sec:
        return None
    try:
        return datetime.fromtimestamp(int(epoch_sec), tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


@dataclass
class RecoverySweepResult:
    """Aggregate evidence of one sweep pass (bounded, JSON-safe)."""

    scanned: int = 0
    recovered: int = 0
    filled_recovered: int = 0
    canceled_recovered: int = 0
    expired_recovered: int = 0
    rejected_recovered: int = 0
    skipped_no_dispatch: int = 0
    skipped_still_live: int = 0
    skipped_no_close_deals: int = 0
    skipped_causality: int = 0
    skipped_ambiguous: int = 0
    skipped_invalid: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "scanned": self.scanned,
            "recovered": self.recovered,
            "filled_recovered": self.filled_recovered,
            "canceled_recovered": self.canceled_recovered,
            "expired_recovered": self.expired_recovered,
            "rejected_recovered": self.rejected_recovered,
            "skipped_no_dispatch": self.skipped_no_dispatch,
            "skipped_still_live": self.skipped_still_live,
            "skipped_no_close_deals": self.skipped_no_close_deals,
            "skipped_causality": self.skipped_causality,
            "skipped_ambiguous": self.skipped_ambiguous,
            "skipped_invalid": self.skipped_invalid,
        }
        d["rows"] = self.rows[:200]
        return d


class HistoricalOutcomeRecoverySweep:
    """Idempotent, bounded recovery of decisions missing their outcome row.

    ``audit_repo`` defaults to ``ledger.audit_repo``. The sweep reads broker
    evidence DIRECTLY from the normalized broker-history copy
    (audit_broker_orders / audit_broker_deals, populated by the broker
    history sync) and writes outcomes through the idempotent ledger.
    """

    def __init__(
        self,
        ledger: Any,
        audit_repo: Any = None,
        max_decisions: int = 2000,
        contract_size: float = DEFAULT_CONTRACT_SIZE,
    ) -> None:
        self.ledger = ledger
        self.repo = audit_repo or ledger.audit_repo
        self.max_decisions = int(max_decisions)
        self.contract_size = float(contract_size) or DEFAULT_CONTRACT_SIZE

    # ------------------------------------------------------------------
    # Evidence queries (read-only, bounded)
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.repo._db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _missing_outcome_decisions(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT e.idempotency_key, e.request_id, e.execution_id, e.symbol,
                          e.action, e.decision_timestamp, e.proposed_entry,
                          e.stop_loss, e.take_profit
                   FROM audit_experiences e
                   LEFT JOIN audit_experience_outcomes o
                          ON o.idempotency_key = e.idempotency_key
                   WHERE o.idempotency_key IS NULL
                   ORDER BY e.decision_timestamp ASC
                   LIMIT ?""",
                (self.max_decisions,),
            ).fetchall()
            out = [dict(r) for r in rows]
        finally:
            conn.close()
        # BUG-174: attach pre-dispatch gate-rejection evidence. A decision whose
        # audit_signals row landed in EXPERIENCE_INTELLIGENCE_GATE /
        # TRADE_INTELLIGENCE_GATE was deterministically refused BEFORE any
        # dispatch could exist, so "no dispatch row" IS the expected truth for
        # it (not unknown provenance). The engine has emitted NOT_DISPATCHED
        # for these live since BUG-169b; this covers historical rows.
        conn = self._connect()
        try:
            for dec in out:
                row = conn.execute(
                    """SELECT decision_stage FROM audit_signals
                       WHERE request_id = ?
                         AND decision_stage IN
                             ('EXPERIENCE_INTELLIGENCE_GATE', 'TRADE_INTELLIGENCE_GATE')
                       LIMIT 1""",
                    (str(dec.get("request_id", "") or ""),),
                ).fetchone()
                if row is not None:
                    dec["gate_rejection_stage"] = row["decision_stage"]
        finally:
            conn.close()
        return out

    def _dispatch_tickets(self, conn: sqlite3.Connection, request_id: str) -> list[str]:
        """Broker tickets recorded by the engine's own dispatch log."""
        if not request_id:
            return []
        rows = conn.execute(
            "SELECT ticket FROM audit_orders WHERE order_id = ? AND ticket != 0 ORDER BY id LIMIT 3",
            (request_id,),
        ).fetchall()
        out: list[str] = []
        for r in rows:
            t = str(r["ticket"])
            if t and t not in out:
                out.append(t)
        return out

    def _broker_orders(self, conn: sqlite3.Connection, tickets: list[str]) -> list[dict[str, Any]]:
        ph = ",".join("?" * len(tickets))
        rows = conn.execute(
            f"""SELECT ticket, position_id, state, volume_initial, volume_current,
                       price_open, time_setup, time_done
                FROM audit_broker_orders WHERE ticket IN ({ph})""",
            tickets,
        ).fetchall()
        return [dict(r) for r in rows]

    def _deals_for(
        self, conn: sqlite3.Connection, tickets: list[str], position_ids: list[int]
    ) -> list[dict[str, Any]]:
        """Close-deal evidence, position_id join first, deals."order" fallback.

        Production reality (BUG-140 QA forensic): a FILLED broker order may
        carry ``position_id = 0`` (pre-BUG-140 sync builds) while the entry
        deal carries the real ``position_id``. The close deal additionally
        carries a *different* ``"order"`` ticket than the entry deal. So the
        ``deals."order" IN (<ticket>)`` fallback alone matches only the entry
        deal and never surfaces the close deal -> ``closes`` stays empty ->
        reconstruction skips a perfectly good filled-and-closed trade.

        Fix: when the ticket-based fallback matches deal rows, extract the
        populated ``position_id`` from those matched rows and re-query every
        deal belonging to that position. The deal rows always know their
        position even when the order row does not, so this deterministically
        recovers the full entry+close deal set for a real filled trade.
        """
        deals: list[dict[str, Any]] = []
        if position_ids:
            ph = ",".join("?" * len(position_ids))
            deals = [
                dict(r)
                for r in conn.execute(
                    f"""SELECT ticket, "order", position_id, entry, volume, price,
                               profit, commission, swap, time, reason, comment
                        FROM audit_broker_deals WHERE position_id IN ({ph}) ORDER BY time""",
                    position_ids,
                ).fetchall()
            ]
        if deals:
            return deals
        ph = ",".join("?" * len(tickets))
        deals = [
            dict(r)
            for r in conn.execute(
                f"""SELECT ticket, "order", position_id, entry, volume, price,
                           profit, commission, swap, time, reason, comment
                    FROM audit_broker_deals WHERE "order" IN ({ph}) ORDER BY time""",
                tickets,
            ).fetchall()
        ]
        if not deals:
            return []
        # Ticket fallback matched deal(s): recover the real position id from the
        # matched deal rows (entry deal carries it even when the order row does
        # not) and re-query ALL deals for that position to also catch the close
        # deal, which carries a different "order" ticket.
        extracted_pos_ids = sorted({int(d["position_id"]) for d in deals if d.get("position_id")})
        if extracted_pos_ids:
            ph_pos = ",".join("?" * len(extracted_pos_ids))
            all_deals = [
                dict(r)
                for r in conn.execute(
                    f"""SELECT ticket, "order", position_id, entry, volume, price,
                               profit, commission, swap, time, reason, comment
                        FROM audit_broker_deals WHERE position_id IN ({ph_pos}) ORDER BY time""",
                    extracted_pos_ids,
                ).fetchall()
            ]
            if all_deals:
                return all_deals
        return deals

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    def run(self, dry_run: bool = False) -> RecoverySweepResult:
        result = RecoverySweepResult()
        decisions = self._missing_outcome_decisions()
        result.scanned = len(decisions)
        logger.info(
            "[OUTCOME_RECOVERY_SWEEP] event=START", decisions=len(decisions), dry_run=dry_run
        )
        conn = self._connect()
        try:
            for dec in decisions:
                try:
                    self._recover_one(conn, dec, result, dry_run=dry_run)
                except Exception as e:
                    result.skipped_invalid += 1
                    logger.error(
                        "[OUTCOME_RECOVERY_SWEEP] event=CANDIDATE_FAILED",
                        idempotency_key=str(dec.get("idempotency_key", ""))[:24],
                        error=str(e),
                    )
        finally:
            conn.close()
        if not dry_run:
            try:
                self.repo._queue.join()
            except Exception:
                pass
        logger.info(
            "[OUTCOME_RECOVERY_SWEEP] event=DONE",
            scanned=result.scanned,
            recovered=result.recovered,
            filled=result.filled_recovered,
            canceled=result.canceled_recovered,
            skipped_no_dispatch=result.skipped_no_dispatch,
            dry_run=dry_run,
        )
        return result

    def _recover_one(
        self,
        conn: sqlite3.Connection,
        dec: dict[str, Any],
        result: RecoverySweepResult,
        *,
        dry_run: bool,
    ) -> None:
        request_id = str(dec.get("request_id", "") or "")
        tickets = self._dispatch_tickets(conn, request_id)
        if not tickets:
            # BUG-174: a recorded PRE-DISPATCH GATE REJECTION is positive
            # evidence the decision was refused before any dispatch could
            # exist — the engine's own audit_signals row proves it (Phase 08
            # EXPERIENCE_INTELLIGENCE_GATE / Phase 09 TRADE_INTELLIGENCE_GATE).
            # For these, "no dispatch row" is the EXPECTED truth, not unknown
            # provenance, so append the honest NOT_DISPATCHED terminal outcome
            # (the live writer has done exactly this since BUG-169b).
            if dec.get("gate_rejection_stage"):
                self._emit_terminal(
                    dec,
                    DecisionLifecycle.NOT_DISPATCHED,
                    result,
                    dry_run=dry_run,
                    detail=(
                        f"{dec['gate_rejection_stage']}: pre-dispatch gate rejection "
                        "(audit_signals evidence; BUG-174 backfill)"
                    ),
                )
                return
            # No dispatch evidence: honest state is "unknown provenance" — the
            # live P0-A wiring classifies NOT_DISPATCHED at the moment it
            # actually happens. Backfilling without evidence would guess.
            result.skipped_no_dispatch += 1
            return
        broker_orders = self._broker_orders(conn, tickets)
        states = [int(r["state"]) for r in broker_orders]
        position_ids = [int(r["position_id"]) for r in broker_orders if r["position_id"]]

        if any(s in _ORDER_STATE_FILLED for s in states):
            self._recover_filled(conn, dec, tickets, position_ids, result, dry_run=dry_run)
            return
        if any(s == _ORDER_STATE_CANCELED for s in states):
            # Partial-then-cancel leaves a position: only a pure cancel
            # (no position) is CANCELED_UNFILLED.
            if position_ids:
                self._recover_filled(conn, dec, tickets, position_ids, result, dry_run=dry_run)
                return
            self._emit_terminal(dec, DecisionLifecycle.CANCELED_UNFILLED, result, dry_run=dry_run)
            return
        if any(s == _ORDER_STATE_EXPIRED for s in states):
            self._emit_terminal(dec, DecisionLifecycle.EXPIRED_UNFILLED, result, dry_run=dry_run)
            return
        if any(s == _ORDER_STATE_REJECTED for s in states):
            self._emit_terminal(dec, DecisionLifecycle.REJECTED_UNFILLED, result, dry_run=dry_run)
            return
        # STARTED/PLACED (or unknown): still owned by the live lifecycle.
        result.skipped_still_live += 1

    # -- filled path ---------------------------------------------------

    def _recover_filled(
        self,
        conn: sqlite3.Connection,
        dec: dict[str, Any],
        tickets: list[str],
        position_ids: list[int],
        result: RecoverySweepResult,
        *,
        dry_run: bool,
    ) -> None:
        key = str(dec.get("idempotency_key", ""))
        deals = self._deals_for(conn, tickets, position_ids)
        fills = [d for d in deals if int(d.get("entry") or 0) == _DEAL_ENTRY_IN]
        closes = [d for d in deals if int(d.get("entry") or 0) in _DEAL_ENTRY_OUT]
        # The broker ORDER row may carry position_id=0 (pre-BUG-140 sync
        # builds) while the DEAL rows always know their position. Prefer the
        # deal-evidenced position id for reconstruction + correlation.
        deal_position_ids = sorted({int(d["position_id"]) for d in deals if d.get("position_id")})
        recon_position_id = (
            deal_position_ids[0] if deal_position_ids else (position_ids[0] if position_ids else 0)
        )
        if not closes:
            # A fill without close deals is an OPEN position or incomplete
            # history: never zero-substitute, never terminate a live trade.
            result.skipped_no_close_deals += 1
            logger.info(
                "[OUTCOME_RECOVERY_SWEEP] event=SKIP_NO_CLOSE_DEALS",
                idempotency_key=key[:24],
                fill_deals=len(fills),
            )
            return
        if not fills:
            result.skipped_ambiguous += 1
            return
        # Fully-closed guard: OUT volume must balance IN volume. A partial
        # close means the position is STILL OPEN -> never recover it as a
        # finished trade (the live close path owns it).
        fill_vol = sum(float(d.get("volume") or 0.0) for d in fills)
        close_vol = sum(float(d.get("volume") or 0.0) for d in closes)
        if fill_vol > 0.0 and close_vol < fill_vol * 0.999:
            result.skipped_no_close_deals += 1
            logger.info(
                "[OUTCOME_RECOVERY_SWEEP] event=SKIP_STILL_OPEN",
                idempotency_key=key[:24],
                fill_vol=fill_vol,
                close_vol=close_vol,
            )
            return

        decision_ts = datetime.fromisoformat(str(dec["decision_timestamp"]))
        fill_deals_dt = [_broker_epoch_to_utc(int(d["time"])) for d in fills]
        close_deals_dt = [_broker_epoch_to_utc(int(d["time"])) for d in closes]
        entry_time = next((t for t in fill_deals_dt if t is not None), None)
        close_time = next((t for t in reversed(close_deals_dt) if t is not None), None)
        if close_time is None:
            result.skipped_invalid += 1
            return
        if entry_time is not None and close_time < entry_time:
            result.skipped_causality += 1
            logger.error(
                "[OUTCOME_RECOVERY_SWEEP] event=CAUSALITY_REFUSED",
                idempotency_key=key[:24],
            )
            return
        if close_time < decision_ts:
            result.skipped_causality += 1
            logger.error(
                "[OUTCOME_RECOVERY_SWEEP] event=CAUSALITY_REFUSED_PRE_DECISION",
                idempotency_key=key[:24],
            )
            return

        rec = self.ledger.get_experience_by_key(key)
        if rec is None:
            result.skipped_invalid += 1
            return

        direction = "SELL" if "SELL" in str(dec.get("action", "")).upper() else "BUY"
        # reconstruct_broker_outcome aggregates CLOSE deals only: passing
        # fill deals too would double-count volume and mis-pick the exit
        # price. Entry evidence (price/time) is passed explicitly.
        # Fold entry-deal commissions and swaps into the first close deal row
        # so reconstruct_broker_outcome aggregates the full round-trip friction
        # without double-counting volume (passing in-deals directly would sum
        # in_vol + out_vol into total_vol).
        entry_comm = sum(float(d.get("commission") or 0.0) for d in fills)
        entry_swap = sum(float(d.get("swap") or 0.0) for d in fills)
        deal_rows = [
            {
                "position_ticket": d.get("position_id"),
                "ticket": d.get("ticket"),
                "profit": d.get("profit") or 0.0,
                "commission": ((d.get("commission") or 0.0) + (entry_comm if idx == 0 else 0.0)),
                "swap": ((d.get("swap") or 0.0) + (entry_swap if idx == 0 else 0.0)),
                "volume": d.get("volume") or 0.0,
                "price": d.get("price") or 0.0,
                "reason": d.get("reason") or 0,
                "comment": d.get("comment") or "",
                "order_ticket": d.get("order") or "",
            }
            for idx, d in enumerate(closes)
        ]
        entry_price = float(next((d.get("price") or 0.0) for d in fills))
        broker_outcome: BrokerOutcome = reconstruct_broker_outcome(
            ticket=recon_position_id,
            symbol=str(dec.get("symbol") or ""),
            direction=direction,
            deals=deal_rows,
            matched_deal=None,
            entry_price=entry_price or float(dec.get("proposed_entry") or 0.0),
            initial_sl=float(dec.get("stop_loss") or 0.0),
            final_sl=float(dec.get("stop_loss") or 0.0),
            tp_price=float(dec.get("take_profit") or 0.0),
            volume=float(close_vol or fill_vol),
            fallback_exit_price=float(closes[-1].get("price") or 0.0),
            close_time=close_time,
            entry_time=entry_time,
        )
        if broker_outcome.reconstruction_source == "NONE":
            # reconstruct_broker_outcome only returns NONE when it received no
            # usable deal rows; we just proved closes exist, so this is a bug
            # guard, not an expected path.
            result.skipped_invalid += 1
            return

        net = float(broker_outcome.net_pnl_usd)
        volume = float(broker_outcome.volume or 0.0)
        risk_distance = abs(
            float(dec.get("proposed_entry") or 0.0) - float(dec.get("stop_loss") or 0.0)
        )
        risk_usd = max(1.0, risk_distance * max(volume, 0.0) * self.contract_size)
        r_multiple = net / risk_usd

        exit_reason, _src, _detail, _conf = classify_exit_with_evidence(
            deal_reason_code=int(closes[-1].get("reason") or 0),
            comment=str(closes[-1].get("comment") or ""),
            profit_usd=net,
            exit_price=float(closes[-1].get("price") or 0.0),
            tp_price=float(dec.get("take_profit") or 0.0),
            sl_price=float(dec.get("stop_loss") or 0.0),
            final_sl=float(dec.get("stop_loss") or 0.0),
            entry_price=entry_price,
            was_sl_modified=False,
            direction=direction,
        )

        duration_sec = 0.0
        if entry_time is not None:
            duration_sec = max(0.0, (close_time - entry_time).total_seconds())
        expected_entry = float(dec.get("proposed_entry") or 0.0)
        slippage_points = 0.0
        if expected_entry > 0.0 and entry_price > 0.0:
            raw = entry_price - expected_entry
            slippage_points = raw if direction == "BUY" else -raw

        outcome = ExperienceOutcome(
            idempotency_key=key,
            execution_id=str(recon_position_id) if recon_position_id else tickets[0],
            outcome_timestamp=close_time,
            is_executed=True,
            is_closed=True,
            exit_reason=exit_reason or "BROKER_CLOSE",
            realized_pnl_usd=round(net, 2),
            realized_r_multiple=round(r_multiple, 6),
            approved_volume=volume,
            behavior=PositionBehavior(duration_sec=duration_sec),
            execution=ExecutionContext(
                expected_entry=expected_entry,
                actual_entry=entry_price,
                slippage_points=slippage_points,
            ),
            decomposition=OutcomeDecomposition(final_outcome_r=round(r_multiple, 6)),
            broker_outcome=broker_outcome,
            correlation_source="ORIGINAL_REQUEST",
            correlation_detail=f"{RECOVERY_SOURCE_BROKER_HISTORY}: request_id join via audit_orders",
        )
        if dry_run:
            result.recovered += 1
            result.filled_recovered += 1
            result.rows.append(
                {
                    "idempotency_key": key,
                    "recovery": "FILLED_CLOSED",
                    "realized_r": round(r_multiple, 4),
                    "realized_pnl": round(net, 2),
                    "source": broker_outcome.reconstruction_source,
                    "exit_reason": outcome.exit_reason,
                }
            )
            return
        written = self.ledger.record_outcome(outcome)
        if written:
            result.recovered += 1
            result.filled_recovered += 1
            result.rows.append(
                {
                    "idempotency_key": key,
                    "recovery": "FILLED_CLOSED",
                    "realized_r": round(r_multiple, 4),
                    "realized_pnl": round(net, 2),
                    "source": broker_outcome.reconstruction_source,
                    "exit_reason": outcome.exit_reason,
                }
            )
            logger.info(
                "[OUTCOME_RECOVERY_SWEEP] event=RECOVERED_TRADE",
                idempotency_key=key[:24],
                realized_r=round(r_multiple, 4),
                source=broker_outcome.reconstruction_source,
            )
        else:
            result.skipped_invalid += 1

    # -- terminal non-trade path ---------------------------------------

    def _emit_terminal(
        self,
        dec: dict[str, Any],
        state: DecisionLifecycle,
        result: RecoverySweepResult,
        *,
        dry_run: bool,
        detail: str = "",
    ) -> None:
        key = str(dec.get("idempotency_key", ""))
        outcome = build_terminal_non_trade_outcome(
            idempotency_key=key,
            state=state,
            detail=detail or f"{RECOVERY_SOURCE_BROKER_HISTORY}: broker order state evidence",
        )
        if dry_run:
            pass
        else:
            written = self.ledger.record_terminal_outcome(outcome)
            if not written:
                result.skipped_invalid += 1
                return
        result.recovered += 1
        if state is DecisionLifecycle.CANCELED_UNFILLED:
            result.canceled_recovered += 1
        elif state is DecisionLifecycle.EXPIRED_UNFILLED:
            result.expired_recovered += 1
        elif state is DecisionLifecycle.REJECTED_UNFILLED:
            result.rejected_recovered += 1
        result.rows.append(
            {
                "idempotency_key": key,
                "recovery": state.value,
                "realized_r": 0.0,
                "realized_pnl": 0.0,
                "source": RECOVERY_SOURCE_BROKER_HISTORY,
                "exit_reason": outcome.exit_reason,
            }
        )
        logger.info(
            "[OUTCOME_RECOVERY_SWEEP] event=RECOVERED_TERMINAL",
            idempotency_key=key[:24],
            state=state.value,
        )


__all__ = [
    "DEFAULT_CONTRACT_SIZE",
    "HistoricalOutcomeRecoverySweep",
    "RecoverySweepResult",
]
