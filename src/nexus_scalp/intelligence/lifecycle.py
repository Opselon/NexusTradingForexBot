"""
Position Lifecycle Intelligence
===============================
PHASE 09 immutable position-timeline recorder.

A position is a journey, not an on/off flag. This tracker turns every observed
state of an open position into a permanent, self-describing
`PositionLifecycleEvent` so the system can later reconstruct exactly WHY a
position moved the way it did.

* EVENTS ARE IMMUTABLE AND DEDUPLICATED by `event_key` (ticket + sequence +
  event type), so a replayed tick stream or a reconnect can never duplicate or
  reorder a position's timeline.
* The tracker is fed from the live path (LiveEngine) but is itself pure and
  isolated: it records and asks questions, never executes. It holds no adapter
  and no order manager.
* State is kept in memory and persisted through the AuditRepository background
  queue, exactly like the Phase 08 experience ledger.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.intelligence.models import (
    DecisionContext,
    MarketContext,
    PositionEventType,
    PositionLifecycleEvent,
    PositionPerformance,
    PositionSnapshot,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.intelligence.lifecycle")

#: Events that are only meaningful once a real MFE/profit high-water has moved.
_EXPECTATION_CONFIRMED_FLOOR_R: float = 0.25
#: MFE high-water event requires a favourable excursion beyond this R.
_MFE_FLOOR_R: float = 0.40
#: A retained profit giveback above this fraction of peak profit is notable.
_GIVEBACK_NOTICE_PCT: float = 0.35
#: Deep adverse excursion (R) which, while still held, flags degradation.
_DEGRADING_MAE_R: float = 0.55

_INSERT_EVENT_SQL = """
    INSERT INTO position_lifecycle_events
    (event_key, ticket, trade_id, experience_id, symbol, timeframe, event_type,
     sequence, event_timestamp, market_context, position_snapshot, payload)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(event_key) DO NOTHING;
"""

# Closed-trade enrichments: rescue the realized PnL / exit mechanism from the
# already-persisted authoritative ledger row so the EXITED event is complete.
_SELECT_LEDGER_CLOSED = """
    SELECT ticket, exit_price, net_pnl_usd, gross_pnl_usd, exit_mechanism,
           duration_seconds, MFE_usd, MAE_usd
    FROM audit_ledger WHERE ticket = ?;
"""


class PositionLifecycleTracker:
    """
    Immutable position-timeline recorder fed by the live engine.

    Responsibilities:
        * initialize a position timeline on first observation (CREATED/OPENED)
        * emit state-change events as the position evolves (MOVING, MFE,
          GIVEBACK, DEGRADING, RECOVERY, MODIFIED)
        * finalize the timeline on exit, enriched with realized PnL
        * make the timeline queryable for autopsy and observability

    The tracker never mutates an emitted event: a new observation appends a new
    event. Replay of the same observation is a no-op at the database level.
    """

    def __init__(
        self,
        audit_repo: AuditRepository,
        expectation_confirmed_floor_r: float = _EXPECTATION_CONFIRMED_FLOOR_R,
        mfe_floor_r: float = _MFE_FLOOR_R,
        giveback_notice_pct: float = _GIVEBACK_NOTICE_PCT,
        degrading_mae_r: float = _DEGRADING_MAE_R,
    ) -> None:
        self.audit_repo = audit_repo
        self.expectation_confirmed_floor_r = expectation_confirmed_floor_r
        self.mfe_floor_r = mfe_floor_r
        self.giveback_notice_pct = giveback_notice_pct
        self.degrading_mae_r = degrading_mae_r

        #: In-memory per-ticket high-water marks. Derived, restartable: the
        #: persisted events are the source of truth, this cache is just a fast
        #: view of the latest created/opened state.
        self._ticket_meta: dict[str, dict[str, Any]] = {}
        #: Monotonic sequence per ticket (restart-safe: keyed ctor/emit counters).
        self._sequence: dict[str, int] = {}
        #: Fast "already emitted this exact observation" guard. Tuple layout:
        #: (event_type, last_price, last_ts, last_sl, last_tp); legacy 2-tuples
        #: from before BUG-054 are tolerated at read time.
        self._last_emitted: dict[str, tuple[PositionEventType, float, float, float, float]] = {}

        self.event_count: int = 0

    # ------------------------------------------------------------------
    # Public feed API (called from LiveEngine, not from the tick hot path for
    # heavy work - emit() only queues a DB write and updates a counter).
    # ------------------------------------------------------------------

    def observe_position(
        self,
        ticket: int,
        snapshot: PositionSnapshot,
        performance: PositionPerformance | None = None,
        market: MarketContext | None = None,
        decision: DecisionContext | None = None,
        trade_id: str = "",
        experience_id: str = "",
        at: datetime | None = None,
    ) -> None:
        """
        Registers an observation of an open position.

        Derives which lifecycle events this observation implies and emits them.
        This is a pure classification + queued-write; it never executes anything.
        """
        if ticket is None or ticket < 0:
            return
        ticket_key = str(ticket)
        now = at or datetime.now(UTC)
        perf = performance or PositionPerformance()
        mctx = market or MarketContext()
        dctx = decision or DecisionContext()

        # Initialize the timeline on first sighting.
        if ticket_key not in self._ticket_meta:
            self._ticket_meta[ticket_key] = {
                "entry_price": snapshot.entry_price,
                "created_at": now,
                "trade_id": trade_id,
                "experience_id": experience_id,
                "peaked_profit": 0.0,
                "peaked_loss": 0.0,
            }
            self._sequence[ticket_key] = 0
            self.emit(
                PositionEventType.POSITION_CREATED,
                ticket=ticket_key,
                snapshot=snapshot,
                performance=perf,
                market=mctx,
                decision=dctx,
                trade_id=trade_id,
                experience_id=experience_id,
                at=now,
                detail="position first observed on live path",
            )
            if snapshot.entry_price > 0.0:
                self.emit(
                    PositionEventType.POSITION_OPENED,
                    ticket=ticket_key,
                    snapshot=snapshot,
                    performance=perf,
                    market=mctx,
                    decision=dctx,
                    trade_id=trade_id,
                    experience_id=experience_id,
                    at=now,
                    detail=f"filled at {snapshot.entry_price:.5f}",
                )
            self._last_emitted[ticket_key] = (
                PositionEventType.POSITION_CREATED,
                0.0,
                0.0,
                0.0,
                0.0,
            )
            return

        self._update_high_water(ticket_key, snapshot)
        base = self._ticket_meta[ticket_key]
        entry = base["entry_price"]

        # Expressed in R multiple vs the stop distance (or ATR fallback).
        planned_risk = abs(entry - snapshot.stop_loss) if snapshot.stop_loss > 0.0 else 0.0

        # MFE reached: a real favourable excursion is now at its best yet.
        if perf.mfe >= self.mfe_floor_r and (perf.mfe > float(base.get("mfe_seen", 0.0))):
            self._ticket_meta[ticket_key]["mfe_seen"] = perf.mfe
            self.emit(
                PositionEventType.POSITION_MFE_REACHED,
                ticket=ticket_key,
                snapshot=snapshot,
                performance=perf,
                market=mctx,
                decision=dctx,
                at=now,
                detail=f"maximum favourable excursion {perf.mfe:.2f}R",
            )

        # Expectation confirmed: the thesis started to work with real edge.
        if perf.mfe >= self.expectation_confirmed_floor_r and not base.get("exp_confirmed", False):
            self._ticket_meta[ticket_key]["exp_confirmed"] = True
            self.emit(
                PositionEventType.POSITION_EXPECTATION_CONFIRMED,
                ticket=ticket_key,
                snapshot=snapshot,
                performance=perf,
                market=mctx,
                decision=dctx,
                at=now,
                detail=f"thesis confirmed at {perf.mfe:.2f}R favourable excursion",
            )

        # Proft giveback: peak profit was reached and a material fraction lost.
        peak = base["peaked_profit"]
        if (
            peak > 0.0
            and snapshot.floating_pnl >= 0.0
            and perf.profit_giveback_pct >= self.giveback_notice_pct
            and not base.get("gave_back", False)
        ):
            self._ticket_meta[ticket_key]["gave_back"] = True
            self.emit(
                PositionEventType.POSITION_PROFIT_GIVEBACK,
                ticket=ticket_key,
                snapshot=snapshot,
                performance=perf,
                market=mctx,
                decision=dctx,
                at=now,
                detail=(
                    f"{perf.profit_giveback_pct * 100.0:.0f}% of peak profit "
                    f"given back (peak {peak:.2f})"
                ),
            )

        # Degrading: deep adverse excursion still being held.
        if perf.mae >= self.degrading_mae_r and not base.get("degrading", False):
            self._ticket_meta[ticket_key]["degrading"] = True
            self.emit(
                PositionEventType.POSITION_DEGRADING,
                ticket=ticket_key,
                snapshot=snapshot,
                performance=perf,
                market=mctx,
                decision=dctx,
                at=now,
                detail=f"adverse excursion reached {perf.mae:.2f}R while still open",
            )

        # Recovery attempt: previously degrading but now improving.
        if (
            base.get("degrading", False)
            and snapshot.floating_pnl > 0.0
            and not base.get("recovered", False)
        ):
            self._ticket_meta[ticket_key]["recovered"] = True
            self.emit(
                PositionEventType.POSITION_RECOVERY_ATTEMPT,
                ticket=ticket_key,
                snapshot=snapshot,
                performance=perf,
                market=mctx,
                decision=dctx,
                at=now,
                detail="position recovered into profit after adverse excursion",
            )

        # Routine movement / modification - throttled to avoid per-tick spam.
        # BUG-054: persist POSITION_MOVING only when meaningful:
        #   1. >= 60s since the last persisted MOVING event, OR
        #   2. stop-loss changed, OR
        #   3. take-profit changed, OR
        #   4. risk/lifecycle-relevant state changed (event-type or sequence
        #      boundary, e.g. MFE/MAE threshold crossed -> state changed)
        #   5. position is closing/closed (handled by finalize_exit)
        # The drift guard is kept as a cheap first filter but the 60s window
        # and SL/TP deltas are what actually bound storage.
        last_entry = self._last_emitted.get(ticket_key)
        if last_entry is None:
            last_type, last_val, last_ts = None, 0.0, 0.0
            last_sl, last_tp = 0.0, 0.0
        elif len(last_entry) < 3:
            # Backward-compatible unpack for older in-memory tuples.
            last_type, last_val = last_entry
            last_ts = 0.0
            last_sl, last_tp = 0.0, 0.0
        elif len(last_entry) == 3:
            last_type, last_val, last_ts = last_entry
            last_sl, last_tp = 0.0, 0.0
        else:
            last_type, last_val, last_ts, last_sl, last_tp = last_entry
        _ = last_type  # event-type boundary check is implicit in emit()
        now_ts = now.timestamp()
        time_since_last = now_ts - (last_ts if last_ts else 0.0)
        sl_changed = bool(
            snapshot.stop_loss and abs(snapshot.stop_loss - (last_sl if last_sl else 0.0)) > 1e-9
        )
        tp_changed = bool(
            snapshot.take_profit
            and abs(snapshot.take_profit - (last_tp if last_tp else 0.0)) > 1e-9
        )
        price_drift = abs(snapshot.current_price - last_val)
        meaningful = (
            time_since_last >= 60.0
            or sl_changed
            or tp_changed
            or price_drift >= 0.15 * (planned_risk or 1.0)
        )
        if not meaningful:
            return
        # Remember what we persisted so the next check can compare SL/TP too.
        self._last_emitted[ticket_key] = (
            PositionEventType.POSITION_MOVING,
            snapshot.current_price,
            now_ts,
            snapshot.stop_loss or 0.0,
            snapshot.take_profit or 0.0,
        )
        self.emit(
            PositionEventType.POSITION_MOVING,
            ticket=ticket_key,
            snapshot=snapshot,
            performance=perf,
            market=mctx,
            decision=dctx,
            at=now,
            detail=f"price moved to {snapshot.current_price:.5f}",
        )

    def emit(
        self,
        event_type: PositionEventType,
        ticket: str,
        snapshot: PositionSnapshot,
        performance: PositionPerformance,
        market: MarketContext,
        decision: DecisionContext,
        at: datetime,
        trade_id: str = "",
        experience_id: str = "",
        detail: str = "",
    ) -> None:
        """Classifies and persists one immutable lifecycle event."""
        if not self.audit_repo._is_sqlite:
            return
        seq = self._sequence.get(ticket, 0)
        seq += 1
        self._sequence[ticket] = seq
        event_key = self._build_event_key(ticket=ticket, seq=seq, event_type=event_type.value)
        event = PositionLifecycleEvent(
            event_key=event_key,
            ticket=ticket,
            trade_id=trade_id,
            experience_id=experience_id,
            symbol=market.symbol or "",
            timeframe=market.timeframe,
            event_type=event_type,
            sequence=seq,
            event_timestamp=at,
            market_context=market,
            position=snapshot,
            performance=performance,
            decision=decision,
            detail=detail,
        )
        payload = {
            "event_key": event_key,
            "detail": detail,
            "decision": decision.model_dump(),
            "snapshot": snapshot.model_dump(),
            "performance": performance.model_dump(),
            "market": market.model_dump(),
        }
        try:
            self.audit_repo._queue.put_nowait(
                (
                    _INSERT_EVENT_SQL,
                    (
                        event_key,
                        ticket,
                        trade_id,
                        experience_id,
                        event.symbol,
                        event.timeframe,
                        event_type.value,
                        seq,
                        at.isoformat(),
                        json.dumps(market.model_dump()),
                        json.dumps(snapshot.model_dump()),
                        json.dumps(payload),
                    ),
                )
            )
            self.event_count += 1
            logger.debug(
                "[POSITION_TRACK]",
                ticket=ticket,
                state=event_type.value,
                seq=seq,
            )
        except Exception as e:
            logger.error("[POSITION_TRACK] emit failed (isolated)", ticket=ticket, error=str(e))

    def finalize_exit(
        self,
        ticket: int,
        snapshot: PositionSnapshot | None = None,
        performance: PositionPerformance | None = None,
        market: MarketContext | None = None,
        decision: DecisionContext | None = None,
        realized_pnl_usd: float = 0.0,
        realized_r: float = 0.0,
        exit_mechanism: str = "",
        at: datetime | None = None,
    ) -> None:
        """
        Emits the terminal POSITION_EXITED event for a closed ticket.

        Enrichment from the authoritative ledger (realized PnL, exit mechanism)
        makes the final event the complete forensic close of the timeline.
        """
        ticket_key = str(ticket)
        now = at or datetime.now(UTC)
        base = self._ticket_meta.get(ticket_key, {})
        perf = performance or PositionPerformance()
        mctx = market or MarketContext(symbol=base.get("symbol", ""))

        # Enrich from the ledger when the caller did not supply realized numbers.
        if realized_pnl_usd == 0.0 and realized_r == 0.0:
            realized_pnl_usd, exit_mechanism, realized_r = self._read_closed_ledger(
                ticket_key, perf
            )

        closed_snapshot = snapshot or PositionSnapshot(
            entry_price=base.get("entry_price", 0.0),
            current_price=snapshot.current_price if snapshot else base.get("entry_price", 0.0),
            floating_pnl=0.0,
            realized_pnl=realized_pnl_usd,
        )
        self.emit(
            PositionEventType.POSITION_EXITED,
            ticket=ticket_key,
            snapshot=closed_snapshot,
            performance=perf,
            market=mctx,
            decision=decision or DecisionContext(),
            at=now,
            trade_id=base.get("trade_id", ""),
            experience_id=base.get("experience_id", ""),
            detail=f"exited {exit_mechanism or 'UNKNOWN'} realized {realized_pnl_usd:.2f} ({realized_r:.2f}R)",
        )
        # Finalize in-memory high-water so re-observation after cleanup is fresh.
        self._ticket_meta.pop(ticket_key, None)
        self._sequence.pop(ticket_key, None)
        self._last_emitted.pop(ticket_key, None)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _update_high_water(self, ticket_key: str, snapshot: PositionSnapshot) -> None:
        """Tracks monotonic peak profit / peak loss for giveback measurement."""
        base = self._ticket_meta[ticket_key]
        base["peaked_profit"] = max(base["peaked_profit"], snapshot.floating_pnl)
        base["peaked_loss"] = min(base["peaked_loss"], snapshot.floating_pnl)

    def _read_closed_ledger(
        self, ticket_key: str, perf: PositionPerformance
    ) -> tuple[float, str, float]:
        """Rescues realized PnL / exit mechanism from the authoritative ledger."""
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(_SELECT_LEDGER_CLOSED, (int(ticket_key),)).fetchone()
            finally:
                conn.close()
            if row is None:
                return 0.0, "", 0.0
            net = float(row["net_pnl_usd"] or 0.0) if row["net_pnl_usd"] is not None else 0.0
            mech = str(row["exit_mechanism"] or "")
            # Approximate R: net / planned risk. planned risk is not on the row
            # directly; fall back to MFE/MAE R already in performance.
            r_mult = perf.mfe if perf.mfe > perf.mae else -perf.mae
            return net, mech, float(r_mult)
        except Exception as e:
            logger.error("[POSITION_TRACK] ledger read failed", ticket=ticket_key, error=str(e))
            return 0.0, "", 0.0

    @staticmethod
    def _build_event_key(ticket: str, seq: int, event_type: str) -> str:
        """Deterministic dedup key for one timeline event."""
        raw = f"{ticket}|{seq}|{event_type}"
        return f"lev_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"

    # ------------------------------------------------------------------
    # Query / rebuild support
    # ------------------------------------------------------------------

    def list_events_for_ticket(self, ticket: int, limit: int = 500) -> list[PositionLifecycleEvent]:
        """Returns the ordered, immutable timeline for one position."""
        if not self.audit_repo._is_sqlite:
            return []
        from nexus_scalp.intelligence.store import load_lifecycle_events

        return load_lifecycle_events(self.audit_repo, ticket=str(ticket), limit=limit)
