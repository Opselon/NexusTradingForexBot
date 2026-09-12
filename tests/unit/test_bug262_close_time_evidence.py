"""BUG-262 regression suite: broker-evidenced close INSTANT on forensic paths.

The reconciliation close-loop, the vanished-ticket autopsy, and the BUG-046
outcome-repair job all reconstructed the broker outcome and ledger autopsy row
using the DETECTION time (tick/wall "now") as the trade's close time and
`datetime.now()` per repair, never the close-deal timestamp the broker history
already carried. Consequences:

  * accounting day/week buckets read `closed_at` (audit_ledger.close_time):
    every reconciled/repaired trade was relocated to the day it was DISCOVERED,
    not the day it closed (a restart at 00:05 re-bucketed the entire prior-day
    book into today);
  * holding duration was hard-zeroed (reconcile) or fabricated hours-to-days
    long (repair: close=now minus entry_time) — corrupting research
    avg_holding_duration and behavior forensics;
  * outcome_timestamp = detection-time poisoned close-vs-decision skew probes.

Fix: `broker_close_time()` derives the close from the deal evidence
(`closed_at`/`time`, normalized by `as_utc`), with a contamination guard —
evidence outside [entry/decision time, detection + tolerance] (e.g. a
server-local GMT+3 stamp, G1 class) is REFUSED and the previous detection-time
fallback applies. Never a fabricated instant.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta, timezone

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import Position, TickData
from nexus_scalp.execution.order_manager import OrderLifecycleManager
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    ExperienceOutcome,
    ExperienceRecord,
    FeatureSnapshot,
    StrategyContext,
)
from nexus_scalp.experience.outcome_recovery import (
    CLOSE_EVIDENCE_TOLERANCE_SEC,
    as_utc,
    broker_close_time,
)
from nexus_scalp.experience.outcome_repair import OutcomeRepairJob

# ---------------------------------------------------------------------------
# as_utc / broker_close_time units
# ---------------------------------------------------------------------------


class TestAsUtc:
    def test_aware_and_naive_datetime(self):
        aware = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        assert as_utc(aware) == aware
        assert as_utc(aware.astimezone(timezone(timedelta(hours=3)))) == aware
        naive = datetime(2026, 9, 1, 12, 0)
        assert as_utc(naive) == aware  # naive ASSUMED UTC (ensure_utc parity)

    def test_iso_strings(self):
        aware = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        assert as_utc("2026-09-01T12:00:00+00:00") == aware
        assert as_utc("2026-09-01 12:00:00") == aware
        assert as_utc("2026-09-01T12:00:00Z") == aware

    def test_epoch_numbers_and_digit_strings(self):
        epoch = int(datetime(2026, 9, 1, 12, 0, tzinfo=UTC).timestamp())
        assert as_utc(epoch) == datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        assert as_utc(str(epoch)) == datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

    def test_garbage_returns_none(self):
        for bad in (None, "", "not-a-date", {}, [], True, 0, 5, 1e18):
            assert as_utc(bad) is None


class TestBrokerCloseTime:
    def test_latest_of_multiple_close_deals(self):
        t0 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
        deals = [
            {"closed_at": (t0 + timedelta(minutes=5)).isoformat()},
            {"closed_at": t0},
            {"time": int((t0 + timedelta(minutes=2)).timestamp())},
        ]
        assert broker_close_time(deals) == t0 + timedelta(minutes=5)

    def test_contamination_guard_refuses_future_stamps(self):
        detect = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
        # Server-local (GMT+3) stamp: 3h ahead of detection -> REFUSED.
        deals = [{"closed_at": (detect + timedelta(hours=3)).isoformat()}]
        got = broker_close_time(
            deals,
            not_before=detect - timedelta(days=1),
            not_after=detect + timedelta(seconds=CLOSE_EVIDENCE_TOLERANCE_SEC),
        )
        assert got is None

    def test_pre_entry_stamps_refused(self):
        entry = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
        deals = [{"closed_at": (entry - timedelta(hours=1)).isoformat()}]
        assert broker_close_time(deals, not_before=entry) is None

    def test_mixed_garbage_skipped(self):
        t0 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
        deals = [{"closed_at": "junk"}, "not-a-dict", {"closed_at": None}, {"closed_at": t0}]
        assert broker_close_time(deals) == t0


# ---------------------------------------------------------------------------
# Reconciliation close-loop (restart gap): evidence-time ledger + outcome
# ---------------------------------------------------------------------------


def _make_om():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "bug262.db")
    audit = AuditRepository(db_url=f"sqlite:///{db_path}")

    class MockMT5Port:
        def __init__(self):
            self.deals: list[dict] = []
            self.positions: list[Position] = []

        def get_positions(self, symbol=None):
            return self.positions

        def close_position(self, ticket):
            return True

        def modify_position(self, ticket, stop_loss=None, take_profit=None):
            return True

        def get_pending_orders(self, symbol=None):
            return []

        def get_closed_deals_history(self, symbol: str, hours_back: int = 24):
            return self.deals

    mock = MockMT5Port()
    om = OrderLifecycleManager(adapter=mock, audit_repo=audit)
    return om, audit, mock


def _tick(bid: float = 1994.0) -> TickData:
    return TickData(
        symbol="XAUUSD",
        timestamp=datetime.now(UTC),
        bid=bid,
        ask=bid + 0.20,
        volume=1.0,
    )


def _seed_opened(audit: AuditRepository, ticket: int, open_dt: datetime) -> None:
    audit.log_ledger_opened(
        ticket=ticket,
        symbol="XAUUSD",
        direction="BUY",
        volume=0.1,
        entry_price=2000.0,
        timestamp_str=open_dt.isoformat(),
        order_id=f"req_{ticket}",
        entry_reason="PURE_AI",
        ai_confidence_at_open=0.6,
        market_regime_at_open="TRENDING",
        initial_sl_price=1995.0,
    )
    audit._queue.join()


def _close_deal(ticket: int, closed_at: datetime) -> dict:
    return {
        "ticket": 9000 + ticket,
        "order_ticket": 8000 + ticket,
        "position_ticket": ticket,
        "symbol": "XAUUSD",
        "price": 1995.0,
        "volume": 0.1,
        "profit": -50.0,
        "commission": 0.0,
        "swap": 0.0,
        "comment": "[sl 1995.0]",
        "closed_at": closed_at,
        "reason": 4,
        "entry_price": 2000.0,
        "direction": "BUY",
        "sl": 1995.0,
        "tp": 0.0,
    }


class _RecordingEngine:
    def __init__(self):
        self.calls: list[dict] = []

    def record_trade_outcome(self, **kw):
        self.calls.append(kw)


def test_reconcile_ledger_close_time_from_deal_evidence():
    """A close that happened HOURS before detection must keep its true instant."""
    om, audit, mock = _make_om()
    try:
        now = datetime.now(UTC)
        true_close = now - timedelta(hours=2)
        open_dt = true_close - timedelta(minutes=30)
        _seed_opened(audit, 7101, open_dt)
        mock.deals = [_close_deal(7101, true_close)]

        n = om.reconcile_missed_closes("XAUUSD", _tick(1994.0), hours_back=24)
        audit._queue.join()
        assert n == 1
        row = audit.get_ledger_row(7101)
        assert row is not None
        assert row["status"] == "RECONCILED"
        # THE FIX: close_time is the broker evidence instant, not tick-now.
        assert as_utc(row["close_time"]) == true_close
        assert as_utc(row["close_time"]) < now - timedelta(hours=1)
        # THE FIX: holding duration is real (30 min), not zeroed.
        assert abs(float(row["duration_sec"]) - 1800.0) < 2.0
    finally:
        audit.close()


def test_reconcile_experience_outcome_timestamp_evidenced():
    om, audit, mock = _make_om()
    try:
        now = datetime.now(UTC)
        true_close = now - timedelta(hours=3)
        _seed_opened(audit, 7102, true_close - timedelta(minutes=10))
        mock.deals = [_close_deal(7102, true_close)]
        eng = _RecordingEngine()
        om.experience_engine = eng

        n = om.reconcile_missed_closes("XAUUSD", _tick(1994.0), hours_back=24)
        audit._queue.join()
        assert n == 1
        assert len(eng.calls) == 1
        call = eng.calls[0]
        assert call["outcome_timestamp"] == true_close
        assert abs(float(call["holding_duration_seconds"]) - 600.0) < 2.0
        bo = call["broker_outcome"]
        assert bo is not None
        assert as_utc(bo["close_time"]) == true_close
        assert abs(float(bo["duration_sec"]) - 600.0) < 2.0
    finally:
        audit.close()


def test_reconcile_refuses_contaminated_server_local_stamps():
    """A +3h server-local close stamp is refused: fallback keeps old behavior."""
    om, audit, mock = _make_om()
    try:
        now = datetime.now(UTC)
        contaminated = now + timedelta(hours=3)  # GMT+3 server-local shape
        _seed_opened(audit, 7103, now - timedelta(minutes=45))
        mock.deals = [_close_deal(7103, contaminated)]

        n = om.reconcile_missed_closes("XAUUSD", _tick(1994.0), hours_back=24)
        audit._queue.join()
        assert n == 1
        row = audit.get_ledger_row(7103)
        assert row is not None
        # Refused -> fallback = detection tick (pre-fix instant), NOT the
        # future-dated contaminated stamp; the trade never leaves today's
        # accounting bucket because of clock-domain garbage.
        close = as_utc(row["close_time"])
        assert close <= now + timedelta(seconds=5)
    finally:
        audit.close()


def test_reconcile_missing_evidence_falls_back_to_detection_time():
    om, audit, mock = _make_om()
    try:
        now = datetime.now(UTC)
        _seed_opened(audit, 7104, now - timedelta(minutes=20))
        deal = _close_deal(7104, None)
        deal.pop("closed_at")
        mock.deals = [deal]

        n = om.reconcile_missed_closes("XAUUSD", _tick(1994.0), hours_back=24)
        audit._queue.join()
        assert n == 1
        row = audit.get_ledger_row(7104)
        assert row is not None
        close = as_utc(row["close_time"])
        assert close is not None and abs((close - now).total_seconds()) < 5.0
    finally:
        audit.close()


# ---------------------------------------------------------------------------
# Vanished-ticket autopsy: deal evidence wins over sweep-now
# ---------------------------------------------------------------------------


def test_autopsy_vanished_ticket_uses_deal_close_time():
    om, audit, mock = _make_om()
    try:
        now = datetime.now(UTC)
        true_close = now - timedelta(minutes=50)
        entry_time = true_close - timedelta(minutes=25)
        ticket = 7105
        # Engine-tracked state, then the position VANISHED (not in positions).
        om._entry_timestamps[ticket] = entry_time
        om._entry_prices[ticket] = 2000.0
        om._entry_sls[ticket] = 1995.0
        om._entry_tps[ticket] = 2010.0
        om._entry_directions[ticket] = "BUY"
        om._last_known_volume[ticket] = 0.1
        mock.deals = [_close_deal(ticket, true_close)]

        from types import SimpleNamespace

        om._sweep_dead_tickets(
            "XAUUSD",
            [],  # no live positions -> everything tracked is dead
            SimpleNamespace(bid=1994.0, ask=1994.2, symbol="XAUUSD"),
            now,
            None,
            0.8,
        )
        audit._queue.join()
        row = audit.get_ledger_row(ticket)
        assert row is not None
        close = as_utc(row["close_time"])
        assert close is not None
        # Evidenced close ~50 min before the sweep instant.
        assert (now - close).total_seconds() > 2900.0
        assert abs(float(row["duration_sec"]) - 1500.0) < 2.0
    finally:
        audit.close()


# ---------------------------------------------------------------------------
# BUG-046 outcome repair: historical close instant preserved
# ---------------------------------------------------------------------------


def _seed_experience_pair(ledger: ExperienceLedger, key: str, decision_ts: datetime) -> None:
    rec = ExperienceRecord(
        experience_id=f"exp_{key}",
        request_id=key,
        idempotency_key=key,
        symbol="XAUUSD",
        timeframe="M1",
        decision_timestamp=decision_ts,
        strategy_id="strat_fam",
        strategy_version="1.0.0",
        context=StrategyContext(
            strategy_id="strat_fam",
            symbol="XAUUSD",
            session="LONDON",
            regime="TRENDING",
            volatility_regime="NORMAL",
            trend_state="BULLISH",
        ),
        feature_snapshot=FeatureSnapshot(
            feature_schema_id="scalp_v1", feature_dimension=50, values=[0.0] * 50
        ),
        action="BUY_MARKET",
        entry_reason="SMC",
        model_probability=0.6,
        signal_confidence=0.6,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
        approved_volume=0.1,
    )
    ledger.record_experience(rec)
    ledger.audit_repo._queue.join()


def _seed_zero_outcome(repo: AuditRepository, key: str, ticket: int, recorded_at: datetime) -> None:
    payload = ExperienceOutcome(
        idempotency_key=key,
        execution_id=str(ticket),
        outcome_timestamp=recorded_at,
        is_executed=True,
        is_closed=True,
        exit_reason="UNKNOWN",
        realized_pnl_usd=0.0,
        realized_r_multiple=0.0,
        approved_volume=0.1,
    ).model_dump_json()
    conn = sqlite3.connect(repo._db_path)
    conn.execute(
        """INSERT INTO audit_experience_outcomes
           (idempotency_key, execution_id, outcome_timestamp, is_executed, is_closed,
            exit_reason, realized_pnl_usd, realized_r_multiple, approved_volume,
            holding_duration_seconds, payload)
           VALUES (?, ?, ?, 1, 1, 'UNKNOWN', 0.0, 0.0, 0.1, 0.0, ?)""",
        (key, str(ticket), recorded_at.isoformat(), payload),
    )
    conn.commit()
    conn.close()


def test_outcome_repair_preserves_historical_close_instant():
    temp_dir = tempfile.mkdtemp()
    repo = AuditRepository(db_url=f"sqlite:///{temp_dir}/repair.db")
    try:
        ledger = ExperienceLedger(repo)
        now = datetime.now(UTC)
        decision_ts = now - timedelta(days=7, hours=6)
        true_close = now - timedelta(days=7)  # closed 7 days ago
        key = "bug262-repair"
        ticket = 5150
        _seed_experience_pair(ledger, key, decision_ts)
        _seed_zero_outcome(repo, key, ticket, decision_ts + timedelta(seconds=5))

        deals = [
            {
                "ticket": ticket + 1,
                "order_ticket": ticket,
                "position_ticket": ticket,
                "symbol": "XAUUSD",
                "price": 1990.0,
                "volume": 0.1,
                "profit": -80.0,
                "commission": -2.0,
                "swap": 0.0,
                "comment": "[sl 1990.0]",
                "closed_at": true_close.isoformat(),  # broker evidence (ISO str)
                "reason": 4,
            }
        ]
        job = OutcomeRepairJob(ledger=ledger, broker_deals_fn=lambda t, h: deals)
        result = job.run()
        assert result.repaired == 1

        conn = sqlite3.connect(repo._db_path)
        conn.row_factory = sqlite3.Row
        import json

        row = conn.execute(
            "SELECT outcome_timestamp, holding_duration_seconds, payload "
            "FROM audit_experience_outcomes WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        conn.close()
        assert row is not None
        # THE FIX: the repaired outcome closes at the BROKER deal time, not
        # at repair-now (pre-fix: datetime.now(UTC) relocated a 7-day-old
        # trade into today's accounting bucket).
        assert as_utc(row["outcome_timestamp"]) == true_close
        payload = json.loads(row["payload"])
        bo = payload["broker_outcome"]
        assert as_utc(bo["close_time"]) == true_close
        assert abs(float(bo["duration_sec"]) - (true_close - decision_ts).total_seconds()) < 2.0
        assert (
            float(row["holding_duration_seconds"]) > 0.0
            or abs(
                float(payload["behavior"]["duration_sec"])
                - (true_close - decision_ts).total_seconds()
            )
            < 2.0
        )
    finally:
        repo.close()


def test_outcome_repair_refuses_contaminated_evidence_and_falls_back():
    """Server-local (future) deal stamps keep the old repair-now behavior."""
    temp_dir = tempfile.mkdtemp()
    repo = AuditRepository(db_url=f"sqlite:///{temp_dir}/repair2.db")
    try:
        ledger = ExperienceLedger(repo)
        now = datetime.now(UTC)
        decision_ts = now - timedelta(days=2)
        key = "bug262-contam"
        ticket = 5151
        _seed_experience_pair(ledger, key, decision_ts)
        _seed_zero_outcome(repo, key, ticket, decision_ts + timedelta(seconds=5))

        deals = [
            {
                "ticket": ticket + 1,
                "order_ticket": ticket,
                "position_ticket": ticket,
                "symbol": "XAUUSD",
                "price": 1990.0,
                "volume": 0.1,
                "profit": -80.0,
                "commission": -2.0,
                "swap": 0.0,
                "comment": "[sl 1990.0]",
                # 3h ahead of wall-now: impossible close (server-local shape).
                "closed_at": (now + timedelta(hours=3)).isoformat(),
                "reason": 4,
            }
        ]
        job = OutcomeRepairJob(ledger=ledger, broker_deals_fn=lambda t, h: deals)
        result = job.run()
        assert result.repaired == 1

        conn = sqlite3.connect(repo._db_path)
        row = conn.execute(
            "SELECT outcome_timestamp FROM audit_experience_outcomes WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        conn.close()
        stamped = as_utc(row[0])
        assert stamped is not None
        # Contaminated evidence refused: repair-time fallback, never the
        # future-dated stamp.
        assert stamped <= now + timedelta(seconds=5)
    finally:
        repo.close()


def test_outcome_repair_honors_epoch_int_evidence():
    """Durable-deal shape (int epoch) is accepted as close evidence."""
    temp_dir = tempfile.mkdtemp()
    repo = AuditRepository(db_url=f"sqlite:///{temp_dir}/repair3.db")
    try:
        ledger = ExperienceLedger(repo)
        now = datetime.now(UTC)
        decision_ts = now - timedelta(days=3)
        # Epoch evidence is second-granular: floor the expected close to match.
        true_close = (decision_ts + timedelta(minutes=15)).replace(microsecond=0)
        key = "bug262-epoch"
        ticket = 5152
        _seed_experience_pair(ledger, key, decision_ts)
        _seed_zero_outcome(repo, key, ticket, decision_ts + timedelta(seconds=5))

        deals = [
            {
                "ticket": ticket + 1,
                "order_ticket": ticket,
                "position_ticket": ticket,
                "symbol": "XAUUSD",
                "price": 2020.0,
                "volume": 0.1,
                "profit": 100.0,
                "commission": -2.0,
                "swap": 0.0,
                "comment": "[tp 2020.0]",
                "closed_at": int(true_close.timestamp()),  # durable epoch shape
                "reason": 5,
            }
        ]
        job = OutcomeRepairJob(ledger=ledger, broker_deals_fn=lambda t, h: deals)
        result = job.run()
        assert result.repaired == 1
        conn = sqlite3.connect(repo._db_path)
        row = conn.execute(
            "SELECT outcome_timestamp FROM audit_experience_outcomes WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        conn.close()
        assert as_utc(row[0]) == true_close
    finally:
        repo.close()
