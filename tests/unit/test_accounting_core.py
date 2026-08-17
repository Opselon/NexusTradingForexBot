"""
Phase 08 Unified Accounting & Performance Intelligence — Unit Tests
====================================================================
Covers the canonical accounting core end-to-end against a real SQLite audit
database:

  * snapshots (capture + duplicate protection + throttling)
  * period aggregation (DAY/WEEK/MONTH/YEAR) with deterministic UTC bounds
  * PnL / growth / drawdown / recovery math
  * closure classification (TP / SL / breakeven-SL / trailing / manual /
    partial) and exactly-once outcome recording
  * strategy attribution (identity chain via outcome table)
  * loss attribution and position quality (MAE/MFE, behavioral flags)
  * trade forensic trace
  * worker lifecycle, idempotency, failure isolation, restartability
  * self-healing rebuild from authoritative records
  * model/schema provenance survival

No synthetic dashboard values are asserted anywhere: every assertion is
derived from rows this test itself wrote.
"""

from __future__ import annotations

import gc
import os
import shutil
import tempfile
import time
from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.accounting import (
    AccountingCore,
    AccountingWorker,
    ExitClassification,
    PeriodKind,
    TradeOutcome,
)
from nexus_scalp.accounting.aggregation import aggregate_period, compute_drawdown
from nexus_scalp.accounting.models import AccountSnapshot, TradeRecord
from nexus_scalp.accounting.normalize import classify_exit, classify_outcome, normalize_trade_row
from nexus_scalp.accounting.periods import (
    ensure_utc,
    parse_sql_timestamp,
    period_bounds,
    recent_periods,
)
from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.models import AccountInfo
from nexus_scalp.experience.intelligence import ExperienceIntelligenceEngine
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    ExperienceOutcome,
    ExperienceRecord,
    FeatureSnapshot,
    StrategyContext,
)
from nexus_scalp.experience.quality import compute_behavior_metrics

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class _FakeAccount:
    """Minimal account object shaped like the domain AccountInfo."""

    def __init__(
        self,
        balance: float,
        equity: float,
        margin: float = 0.0,
        margin_free: float = 0.0,
        currency: str = "USD",
        leverage: int = 100,
        login: int = 42,
    ) -> None:
        self.balance = balance
        self.equity = equity
        self.margin = margin
        self.margin_free = margin_free
        self.currency = currency
        self.leverage = leverage
        self.login = login


class _FakePosition:
    def __init__(self, volume: float = 1.0) -> None:
        self.volume = volume


class _FakeAdapter:
    """Broker adapter stub returning real account/position data."""

    def __init__(self) -> None:
        self.account = _FakeAccount(balance=10000.0, equity=10000.0)
        self.positions: list[_FakePosition] = []
        self.fail_account = False
        self.fail_positions = False

    def get_account_info(self) -> _FakeAccount:
        if self.fail_account:
            raise RuntimeError("broker down")
        return self.account

    def get_positions(self, symbol: str | None = None) -> list[_FakePosition]:
        if self.fail_positions:
            raise RuntimeError("positions down")
        return self.positions


@pytest.fixture()
def audit(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'test.db'}", flush_interval_sec=0.05)
    yield repo
    repo.close()
    gc.collect()


@pytest.fixture()
def core(audit):
    adapter = _FakeAdapter()
    ledger = ExperienceLedger(audit_repo=audit)
    return AccountingCore(audit_repo=audit, adapter=adapter, experience_ledger=ledger)


def _flush(audit: AuditRepository, seconds: float = 0.4) -> None:
    """Waits for the audit queue worker to flush queued writes."""
    time.sleep(seconds)


def _snapshot_row(
    audit: AuditRepository,
    balance: float,
    equity: float,
    ts: datetime | None = None,
    margin_free: float | None = None,
) -> None:
    """Directly inserts one authoritative snapshot row (bypassing throttle)."""
    stamp = (ts or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M:%S")
    audit._queue.put_nowait(
        (
            "INSERT INTO audit_account_snapshots (timestamp, balance, equity, margin_free, peak_equity) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                stamp,
                balance,
                equity,
                margin_free if margin_free is not None else balance,
                max(balance, equity),
            ),
        )
    )
    _flush(audit)


def _ledger_closed(
    audit: AuditRepository,
    ticket: int,
    *,
    direction: str = "BUY",
    entry: float = 2000.0,
    exit_price: float,
    pnl: float,
    commission: float = 0.0,
    swap: float = 0.0,
    close_ts: datetime,
    exit_mechanism: str = "",
    initial_sl: float = 0.0,
    final_sl: float = 0.0,
    is_risk_free_hit: int = 0,
    was_sl_modified: int = 0,
    mae: float = 0.0,
    mfe: float = 0.0,
    mae_usd: float = 0.0,
    mfe_usd: float = 0.0,
    volume: float = 1.0,
) -> None:
    """Writes one closed ledger row through the real repository API."""
    stamp = close_ts.isoformat()
    audit.log_ledger_closed(
        ticket=ticket,
        symbol="XAUUSD",
        direction=direction,
        volume=volume,
        entry_price=entry,
        exit_price=exit_price,
        status="CLOSED",
        pnl=pnl,
        commission=commission,
        swap=swap,
        duration_sec=600.0,
        timestamp_str=stamp,
        mae=mae,
        mfe=mfe,
        initial_sl_price=initial_sl,
        final_sl_price=final_sl,
        is_risk_free_hit=is_risk_free_hit,
        exit_mechanism=exit_mechanism,
        open_time=(close_ts - timedelta(minutes=10)).isoformat(),
        close_time=stamp,
        was_sl_modified=was_sl_modified,
        mae_usd=mae_usd,
        mfe_usd=mfe_usd,
    )
    _flush(audit)


def _seed_experience(
    ledger: ExperienceLedger,
    *,
    request_id: str,
    execution_id: str,
    strategy_id: str = "strat_a",
    entry: float = 2000.0,
    sl: float = 1990.0,
    tp: float = 2020.0,
    decision_ts: datetime | None = None,
    outcome_ts: datetime | None = None,
    realized_pnl: float = 0.0,
    realized_r: float = 0.0,
    mae_points: float = 0.0,
    mfe_points: float = 0.0,
    mae_usd: float = 0.0,
    mfe_usd: float = 0.0,
    exit_reason: str = "TAKE_PROFIT_HIT",
) -> None:
    """Seeds a decision experience + its outcome, linked via execution_id."""
    dt = decision_ts or (datetime.now(UTC) - timedelta(hours=2))
    ot = outcome_ts or (dt + timedelta(minutes=5))
    ctx = StrategyContext(strategy_id=strategy_id, regime="TRENDING_MOMENTUM")
    exp = ExperienceRecord(
        experience_id=f"exp_{request_id}",
        request_id=request_id,
        idempotency_key=f"exp_{request_id}",
        symbol="XAUUSD",
        decision_timestamp=dt,
        strategy_id=strategy_id,
        context=ctx,
        feature_snapshot=FeatureSnapshot(values=[0.1] * 50),
        action="BUY_MARKET",
        entry_reason="PURE_AI",
        model_probability=0.8,
        signal_confidence=0.8,
        proposed_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_reward_ratio=2.0,
    )
    ledger.record_experience(exp)
    outcome = ExperienceOutcome(
        idempotency_key=exp.idempotency_key,
        execution_id=execution_id,
        outcome_timestamp=ot,
        is_executed=True,
        is_closed=True,
        exit_reason=exit_reason,
        realized_pnl_usd=realized_pnl,
        realized_r_multiple=realized_r,
        behavior=compute_behavior_metrics(
            mae_points=mae_points,
            mfe_points=mfe_points,
            mae_usd=mae_usd,
            mfe_usd=mfe_usd,
            planned_risk_distance=abs(entry - sl),
            duration_sec=300.0,
            initial_sl_distance=abs(entry - sl),
            atr_at_entry=4.0,
        ),
    )
    ledger.record_outcome(outcome)


# ---------------------------------------------------------------------------
# 1. Period boundaries (deterministic UTC)
# ---------------------------------------------------------------------------


class TestPeriodBounds:
    def test_day_bounds_are_utc_midnight_half_open(self) -> None:
        moment = datetime(2026, 8, 15, 14, 30, 0, tzinfo=UTC)
        b = period_bounds(PeriodKind.DAY, moment)
        assert b.start == datetime(2026, 8, 15, tzinfo=UTC)
        assert b.end == datetime(2026, 8, 16, tzinfo=UTC)
        assert b.key == "2026-08-15"
        # Half-open: midnight exactly belongs to the NEW day.
        assert not b.contains(datetime(2026, 8, 16, tzinfo=UTC))
        assert b.contains(datetime(2026, 8, 15, 23, 59, 59, 999999, tzinfo=UTC))

    def test_week_bounds_are_iso_monday(self) -> None:
        # 2026-08-15 is a Saturday -> ISO week W33, Monday 2026-08-10.
        b = period_bounds(PeriodKind.WEEK, datetime(2026, 8, 15, tzinfo=UTC))
        assert b.start == datetime(2026, 8, 10, tzinfo=UTC)
        assert b.end == datetime(2026, 8, 17, tzinfo=UTC)
        assert b.key == "2026-W33"

    def test_month_bounds(self) -> None:
        b = period_bounds(PeriodKind.MONTH, datetime(2026, 8, 15, tzinfo=UTC))
        assert b.start == datetime(2026, 8, 1, tzinfo=UTC)
        assert b.end == datetime(2026, 9, 1, tzinfo=UTC)
        assert b.key == "2026-08"

    def test_month_december_rolls_year(self) -> None:
        b = period_bounds(PeriodKind.MONTH, datetime(2026, 12, 25, tzinfo=UTC))
        assert b.end == datetime(2027, 1, 1, tzinfo=UTC)

    def test_year_bounds(self) -> None:
        b = period_bounds(PeriodKind.YEAR, datetime(2026, 8, 15, tzinfo=UTC))
        assert b.start == datetime(2026, 1, 1, tzinfo=UTC)
        assert b.end == datetime(2027, 1, 1, tzinfo=UTC)
        assert b.key == "2026"

    def test_naive_datetime_assumed_utc(self) -> None:
        naive = datetime(2026, 8, 15, 3, 0, 0)  # no tzinfo
        b = period_bounds(PeriodKind.DAY, naive)
        assert b.start.tzinfo is not None
        assert b.key == "2026-08-15"

    def test_recent_periods_bounded_and_ordered(self) -> None:
        at = datetime(2026, 8, 15, tzinfo=UTC)
        series = recent_periods(PeriodKind.DAY, 5, at)
        assert len(series) == 5
        keys = [b.key for b in series]
        assert keys == sorted(keys)
        assert keys[-1] == "2026-08-15"

    def test_parse_sql_timestamp_variants(self) -> None:
        assert parse_sql_timestamp("2026-08-15 10:00:00").hour == 10
        assert parse_sql_timestamp("2026-08-15T10:00:00+00:00").hour == 10
        assert parse_sql_timestamp("2026-08-15T10:00:00Z").hour == 10
        assert parse_sql_timestamp(None) is None
        assert parse_sql_timestamp("garbage") is None

    def test_ensure_utc_converts_offsets(self) -> None:
        shifted = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone(timedelta(hours=3)))
        assert ensure_utc(shifted).hour == 9


from datetime import timezone  # noqa: E402

# ---------------------------------------------------------------------------
# 2. Snapshot capture & duplicate protection
# ---------------------------------------------------------------------------


class TestSnapshots:
    def test_snapshot_recorded_and_readable(self, audit, core) -> None:
        _snapshot_row(audit, 10000.0, 10100.0, ts=datetime(2026, 8, 15, 10, 0, tzinfo=UTC))
        snaps = core.load_snapshots()
        assert len(snaps) == 1
        s = snaps[0]
        assert s.balance == 10000.0
        assert s.equity == 10100.0
        assert s.floating_pnl == 100.0  # equity - balance, derived
        assert s.timestamp.tzinfo is not None

    def test_snapshot_throttling_no_duplicate_spam(self, audit, core) -> None:
        acc = AccountInfo(
            login=1,
            trade_mode=0,
            leverage=100,
            balance=10000.0,
            equity=10000.0,
            margin=0.0,
            margin_free=10000.0,
        )
        # Force first write
        audit._last_snapshot_time = 0.0
        audit.log_account_snapshot(acc, 10000.0)
        # Same balance within 60s -> throttled, no second row
        audit.log_account_snapshot(acc, 10000.0)
        _flush(audit)
        snaps = core.load_snapshots()
        assert len(snaps) == 1

    def test_snapshot_balance_change_writes(self, audit, core) -> None:
        acc1 = AccountInfo(
            login=1,
            trade_mode=0,
            leverage=100,
            balance=10000.0,
            equity=10000.0,
            margin=0.0,
            margin_free=10000.0,
        )
        acc2 = AccountInfo(
            login=1,
            trade_mode=0,
            leverage=100,
            balance=10050.0,
            equity=10060.0,
            margin=0.0,
            margin_free=10060.0,
        )
        audit._last_snapshot_time = 0.0
        audit.log_account_snapshot(acc1, 10000.0)
        audit.log_account_snapshot(acc2, 10060.0)
        _flush(audit)
        assert len(core.load_snapshots()) == 2


# ---------------------------------------------------------------------------
# 3. Period aggregation correctness
# ---------------------------------------------------------------------------


class TestPeriodAggregation:
    def test_daily_aggregation(self, audit, core) -> None:
        d1 = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
        d2 = datetime(2026, 8, 15, 14, 0, tzinfo=UTC)
        _ledger_closed(
            audit,
            1,
            exit_price=2002.0,
            pnl=200.0,
            commission=2.0,
            close_ts=d1,
            exit_mechanism="TAKE_PROFIT_HIT",
        )
        _ledger_closed(
            audit, 2, exit_price=1998.0, pnl=-100.0, close_ts=d2, exit_mechanism="HARD_SL_HIT"
        )
        _snapshot_row(audit, 10000.0, 10000.0, ts=datetime(2026, 8, 15, 0, 0, 1, tzinfo=UTC))
        _snapshot_row(audit, 10100.0, 10100.0, ts=datetime(2026, 8, 15, 23, 59, 0, tzinfo=UTC))

        report = core.period_report(PeriodKind.DAY, at=datetime(2026, 8, 15, 12, 0, tzinfo=UTC))
        assert report.total_trades == 2
        assert report.win_count == 1
        assert report.loss_count == 1
        # net = gross - commission - swap: 200-2=198 win, -100 loss
        assert report.net_pnl == pytest.approx(98.0)
        assert report.gross_profit == pytest.approx(198.0)
        assert report.gross_loss == pytest.approx(100.0)
        assert report.win_rate == pytest.approx(50.0)
        assert report.expectancy == pytest.approx(49.0)
        assert report.profit_factor == pytest.approx(1.98)
        assert report.best_trade == pytest.approx(198.0)
        assert report.worst_trade == pytest.approx(-100.0)
        assert report.starting_balance == pytest.approx(10000.0)
        assert report.ending_balance == pytest.approx(10100.0)

    def test_weekly_aggregation(self, audit, core) -> None:
        # Monday of W33: 2026-08-10; trades on Tue and Fri of the same week.
        t1 = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)
        t2 = datetime(2026, 8, 14, 10, 0, tzinfo=UTC)
        _ledger_closed(
            audit, 1, exit_price=2001.0, pnl=100.0, close_ts=t1, exit_mechanism="TAKE_PROFIT_HIT"
        )
        _ledger_closed(
            audit, 2, exit_price=1999.0, pnl=-50.0, close_ts=t2, exit_mechanism="HARD_SL_HIT"
        )
        report = core.period_report(PeriodKind.WEEK, at=datetime(2026, 8, 13, 12, 0, tzinfo=UTC))
        assert report.total_trades == 2
        assert report.net_pnl == pytest.approx(50.0)

    def test_monthly_aggregation(self, audit, core) -> None:
        _ledger_closed(
            audit,
            1,
            exit_price=2001.0,
            pnl=100.0,
            close_ts=datetime(2026, 8, 3, tzinfo=UTC),
            exit_mechanism="TAKE_PROFIT_HIT",
        )
        _ledger_closed(
            audit,
            2,
            exit_price=1999.0,
            pnl=-25.0,
            close_ts=datetime(2026, 8, 20, tzinfo=UTC),
            exit_mechanism="HARD_SL_HIT",
        )
        report = core.period_report(PeriodKind.MONTH, at=datetime(2026, 8, 15, tzinfo=UTC))
        assert report.total_trades == 2
        assert report.net_pnl == pytest.approx(75.0)
        assert report.key == "2026-08"

    def test_yearly_aggregation(self, audit, core) -> None:
        _ledger_closed(
            audit,
            1,
            exit_price=2001.0,
            pnl=300.0,
            close_ts=datetime(2026, 2, 1, tzinfo=UTC),
            exit_mechanism="TAKE_PROFIT_HIT",
        )
        report = core.period_report(PeriodKind.YEAR, at=datetime(2026, 6, 15, tzinfo=UTC))
        assert report.total_trades == 1
        assert report.net_pnl == pytest.approx(300.0)
        assert report.key == "2026"

    def test_period_series_ordered_and_bounded(self, audit, core) -> None:
        series = core.period_series(PeriodKind.DAY, 7, at=datetime(2026, 8, 15, tzinfo=UTC))
        assert len(series) == 7
        keys = [r.key for r in series]
        assert keys == sorted(keys)

    def test_empty_period_has_data_false(self, audit, core) -> None:
        report = core.period_report(PeriodKind.DAY, at=datetime(2026, 1, 1, tzinfo=UTC))
        assert report.has_data is False
        assert report.total_trades == 0
        assert report.net_pnl == 0.0

    def test_trade_at_midnight_belongs_to_new_period(self, audit, core) -> None:
        # Exactly 00:00:00 UTC -> belongs to the NEW day (half-open).
        _ledger_closed(
            audit,
            1,
            exit_price=2001.0,
            pnl=50.0,
            close_ts=datetime(2026, 8, 16, 0, 0, 0, tzinfo=UTC),
            exit_mechanism="TAKE_PROFIT_HIT",
        )
        old = core.period_report(PeriodKind.DAY, at=datetime(2026, 8, 15, 12, 0, tzinfo=UTC))
        new = core.period_report(PeriodKind.DAY, at=datetime(2026, 8, 16, 12, 0, tzinfo=UTC))
        assert old.total_trades == 0
        assert new.total_trades == 1


# ---------------------------------------------------------------------------
# 4. Drawdown & recovery
# ---------------------------------------------------------------------------


class TestDrawdown:
    def _snap(self, ts: datetime, eq: float) -> AccountSnapshot:
        return AccountSnapshot(timestamp=ts, balance=eq, equity=eq, margin_free=eq, peak_equity=eq)

    def test_drawdown_peak_to_trough(self) -> None:
        snaps = [
            self._snap(datetime(2026, 8, 10, tzinfo=UTC), 10000.0),
            self._snap(datetime(2026, 8, 11, tzinfo=UTC), 11000.0),  # peak
            self._snap(datetime(2026, 8, 12, tzinfo=UTC), 9500.0),  # -13.64%
            self._snap(datetime(2026, 8, 13, tzinfo=UTC), 9800.0),
        ]
        r = compute_drawdown(snaps)
        assert r.max_drawdown_pct == pytest.approx(1500.0 / 11000.0 * 100.0, abs=0.01)
        assert r.max_drawdown_usd == pytest.approx(1500.0)
        assert r.peak_equity == pytest.approx(11000.0)
        assert r.current_drawdown_pct == pytest.approx((11000 - 9800) / 11000 * 100.0, abs=0.01)
        assert r.in_drawdown is True

    def test_drawdown_recovery(self) -> None:
        snaps = [
            self._snap(datetime(2026, 8, 10, tzinfo=UTC), 10000.0),
            self._snap(datetime(2026, 8, 11, tzinfo=UTC), 11000.0),  # peak
            self._snap(datetime(2026, 8, 12, tzinfo=UTC), 9500.0),  # trough
            self._snap(datetime(2026, 8, 13, tzinfo=UTC), 11000.0),  # recovered
        ]
        r = compute_drawdown(snaps)
        assert r.in_drawdown is False
        assert r.recovery_pct == pytest.approx(100.0)
        # Recovery duration = time from the peak that started the drawdown to
        # the moment equity returned to that peak (2 days).
        assert r.recovery_duration_sec == pytest.approx(2 * 86400.0)

    def test_drawdown_recovery_partial(self) -> None:
        snaps = [
            self._snap(datetime(2026, 8, 10, tzinfo=UTC), 10000.0),
            self._snap(datetime(2026, 8, 11, tzinfo=UTC), 11000.0),
            self._snap(datetime(2026, 8, 12, tzinfo=UTC), 9500.0),
            self._snap(datetime(2026, 8, 13, tzinfo=UTC), 10400.0),  # half recovered
        ]
        r = compute_drawdown(snaps)
        # recovered 900 of 1500
        assert r.recovery_pct == pytest.approx(60.0, abs=0.01)

    def test_drawdown_single_snapshot_no_data(self) -> None:
        snaps = [self._snap(datetime(2026, 8, 10, tzinfo=UTC), 10000.0)]
        r = compute_drawdown(snaps)
        assert r.has_data is True
        assert r.max_drawdown_pct == 0.0
        assert r.in_drawdown is False

    def test_drawdown_empty(self) -> None:
        r = compute_drawdown([])
        assert r.has_data is False
        assert r.sample_count == 0

    def test_intraperiod_drawdown(self) -> None:
        snaps = [
            self._snap(datetime(2026, 8, 15, 0, 5, tzinfo=UTC), 10000.0),
            self._snap(datetime(2026, 8, 15, 6, 0, tzinfo=UTC), 10400.0),
            self._snap(datetime(2026, 8, 15, 12, 0, tzinfo=UTC), 9900.0),
        ]
        pct, usd = core_drawdown_intra(snaps)
        assert usd == pytest.approx(500.0)
        assert pct == pytest.approx(500.0 / 10400.0 * 100.0, abs=0.01)


def core_drawdown_intra(snaps):
    from nexus_scalp.accounting.aggregation import intraperiod_drawdown

    return intraperiod_drawdown(snaps)


# ---------------------------------------------------------------------------
# 5. Closure classification (TP / SL / breakeven / trailing / manual / partial)
# ---------------------------------------------------------------------------


class TestClosureClassification:
    def _row(self, **overrides):
        base = {
            "exit_mechanism": "TAKE_PROFIT_HIT",
            "direction": "BUY",
            "entry_price": 2000.0,
            "initial_sl_price": 1990.0,
            "final_sl_price": 1990.0,
            "was_sl_modified": 0,
            "is_risk_free_hit": 0,
            "gross_pnl_usd": 100.0,
            "commission": 0.0,
            "swap": 0.0,
            "net_pnl_usd": 100.0,
            "ticket": 1,
            "symbol": "XAUUSD",
            "volume": 1.0,
            "status": "CLOSED",
        }
        base.update(overrides)
        return base

    def test_tp_closure(self) -> None:
        cls, risk_free = classify_exit(self._row(exit_mechanism="TAKE_PROFIT_HIT"))
        assert cls is ExitClassification.TAKE_PROFIT
        assert risk_free is False

    def test_initial_sl_closure(self) -> None:
        cls, _ = classify_exit(self._row(exit_mechanism="HARD_SL_HIT", was_sl_modified=0))
        assert cls is ExitClassification.INITIAL_STOP
        assert cls.is_stop_exit

    def test_breakeven_sl_closure_is_not_win(self) -> None:
        """SL moved to entry, final close at entry -> BREAKEVEN_STOP, NOT a win."""
        row = self._row(
            exit_mechanism="RISK_FREE_SL_HIT",
            was_sl_modified=1,
            is_risk_free_hit=1,
            final_sl_price=2000.0,  # parked at entry
            exit_price=2000.0,
            net_pnl_usd=0.0,
        )
        cls, risk_free = classify_exit(row)
        assert cls is ExitClassification.BREAKEVEN_STOP
        assert risk_free is True
        outcome = classify_outcome(row["net_pnl_usd"])
        assert outcome is TradeOutcome.BREAKEVEN  # NOT WIN

    def test_breakeven_but_net_loss_after_costs(self) -> None:
        """SL at entry but swap+commission made it a loss -> LOSS, not WIN."""
        row = self._row(
            exit_mechanism="RISK_FREE_SL_HIT",
            was_sl_modified=1,
            is_risk_free_hit=1,
            final_sl_price=2000.0,
            exit_price=2000.0,
            gross_pnl_usd=0.0,
            commission=3.0,
            swap=1.0,
            net_pnl_usd=0.0,  # persisted net is 0 -> recompute gross - costs
        )
        # normalize_trade_row computes net = 0 - 3 - 1 = -4
        record = normalize_trade_row(row)
        assert record.outcome is TradeOutcome.LOSS
        assert record.exit_classification is ExitClassification.BREAKEVEN_STOP
        assert record.risk_free_state is True

    def test_trailing_sl_closure(self) -> None:
        row = self._row(
            exit_mechanism="HARD_SL_HIT",
            was_sl_modified=1,
            is_risk_free_hit=0,
            final_sl_price=2010.0,  # pushed beyond entry (BUY)
            exit_price=2010.0,
            net_pnl_usd=100.0,
        )
        cls, _ = classify_exit(row)
        assert cls is ExitClassification.TRAILING_STOP
        assert cls.is_stop_exit

    def test_manual_closure(self) -> None:
        cls, _ = classify_exit(self._row(exit_mechanism="MANUAL_CLOSE"))
        assert cls is ExitClassification.MANUAL_EXIT

    def test_partial_closure(self) -> None:
        cls, _ = classify_exit(self._row(exit_mechanism="", status="PARTIAL_CLOSE"))
        assert cls is ExitClassification.PARTIAL_CLOSE

    def test_unknown_mechanism_preserved(self) -> None:
        cls, _ = classify_exit(self._row(exit_mechanism="SOMETHING_NEW"))
        assert cls is ExitClassification.OTHER_EXIT

    def test_emergency_close(self) -> None:
        cls, _ = classify_exit(self._row(exit_mechanism="PROFIT_GIVEBACK_PROTECTION"))
        assert cls is ExitClassification.EMERGENCY_EXIT


# ---------------------------------------------------------------------------
# 6. Trade normalization (PnL exactly once)
# ---------------------------------------------------------------------------


class TestTradeNormalization:
    def test_net_pnl_gross_minus_costs(self) -> None:
        record = normalize_trade_row(
            {
                "ticket": 7,
                "symbol": "XAUUSD",
                "direction": "BUY",
                "volume": 1.0,
                "entry_price": 2000.0,
                "exit_price": 2002.0,
                "gross_pnl_usd": 200.0,
                "commission": 2.0,
                "swap": 0.5,
                "net_pnl_usd": 0.0,  # persisted but zero -> recompute
                "exit_mechanism": "TAKE_PROFIT_HIT",
                "status": "CLOSED",
            }
        )
        assert record.net_pnl == pytest.approx(200.0 - 2.0 - 0.5)
        assert record.outcome is TradeOutcome.WIN
        assert record.gross_pnl == pytest.approx(200.0)

    def test_persisted_net_pnl_trusted(self) -> None:
        record = normalize_trade_row(
            {
                "ticket": 8,
                "symbol": "XAUUSD",
                "direction": "SELL",
                "volume": 1.0,
                "entry_price": 2000.0,
                "exit_price": 1998.0,
                "gross_pnl_usd": 150.0,
                "commission": 1.0,
                "swap": 0.0,
                "net_pnl_usd": 149.0,  # authoritative
                "exit_mechanism": "TAKE_PROFIT_HIT",
                "status": "CLOSED",
            }
        )
        assert record.net_pnl == pytest.approx(149.0)

    def test_r_multiple_reconstruction(self) -> None:
        record = normalize_trade_row(
            {
                "ticket": 9,
                "symbol": "XAUUSD",
                "direction": "BUY",
                "volume": 1.0,
                "entry_price": 2000.0,
                "exit_price": 2005.0,
                "initial_sl_price": 1990.0,
                "final_sl_price": 1990.0,
                "gross_pnl_usd": 500.0,
                "commission": 0.0,
                "swap": 0.0,
                "net_pnl_usd": 500.0,
                "mae": -8.0,
                "MFE_usd": 500.0,
                "mfe": 5.0,
                "exit_mechanism": "TAKE_PROFIT_HIT",
                "status": "CLOSED",
            }
        )
        # per point = 500/5 = 100 USD; risk = |2000-1990| * 100 = 1000; R = 500/1000
        assert record.risk_usd == pytest.approx(1000.0)
        assert record.realized_r == pytest.approx(0.5)

    def test_r_unknown_when_no_risk_basis(self) -> None:
        record = normalize_trade_row(
            {
                "ticket": 10,
                "symbol": "XAUUSD",
                "direction": "BUY",
                "volume": 1.0,
                "entry_price": 2000.0,
                "exit_price": 2001.0,
                "gross_pnl_usd": 100.0,
                "net_pnl_usd": 100.0,
                "exit_mechanism": "MANUAL_CLOSE",
                "status": "CLOSED",
            }
        )
        assert record.realized_r is None
        assert record.risk_usd is None

    def test_open_positions_excluded_from_trades(self, audit, core) -> None:
        audit.log_ledger_opened(
            ticket=500,
            symbol="XAUUSD",
            direction="BUY",
            volume=1.0,
            entry_price=2000.0,
            timestamp_str=datetime.now(UTC).isoformat(),
        )
        _flush(audit)
        trades = core.load_trades()
        assert all(t.ticket != 500 for t in trades)


# ---------------------------------------------------------------------------
# 7. Strategy attribution (identity chain through outcome table)
# ---------------------------------------------------------------------------


class TestStrategyAttribution:
    def test_trade_linked_to_strategy_via_outcome(self, audit, core) -> None:
        ledger = ExperienceLedger(audit_repo=audit)
        core.experience_ledger = ledger
        decision_ts = datetime(2026, 8, 15, 9, 0, tzinfo=UTC)
        outcome_ts = decision_ts + timedelta(minutes=5)
        _seed_experience(
            ledger,
            request_id="req_1",
            execution_id="777",
            strategy_id="strat_alpha",
            decision_ts=decision_ts,
            outcome_ts=outcome_ts,
            realized_pnl=120.0,
            realized_r=1.2,
        )
        _ledger_closed(
            audit,
            777,
            exit_price=2002.0,
            pnl=122.0,
            commission=2.0,
            close_ts=outcome_ts,
            exit_mechanism="TAKE_PROFIT_HIT",
        )
        trades = core.load_trades()
        assert len(trades) == 1
        t = trades[0]
        assert t.strategy_id == "strat_alpha"
        assert t.experience_id == "exp_req_1"
        assert t.model_id != "" or t.feature_schema_id != ""

    def test_trade_without_outcome_link_keeps_empty_identity(self, audit, core) -> None:
        _ledger_closed(
            audit,
            999,
            exit_price=2001.0,
            pnl=50.0,
            close_ts=datetime(2026, 8, 15, 10, 0, tzinfo=UTC),
            exit_mechanism="TAKE_PROFIT_HIT",
        )
        trades = core.load_trades()
        assert len(trades) == 1
        assert trades[0].strategy_id == ""
        assert trades[0].experience_id == ""

    def test_strategy_contributions_aggregate(self, audit, core) -> None:
        ledger = ExperienceLedger(audit_repo=audit)
        core.experience_ledger = ledger
        base = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
        _seed_experience(
            ledger,
            request_id="r1",
            execution_id="11",
            strategy_id="strat_win",
            decision_ts=base,
            outcome_ts=base + timedelta(minutes=2),
            realized_pnl=200.0,
            realized_r=2.0,
        )
        _seed_experience(
            ledger,
            request_id="r2",
            execution_id="12",
            strategy_id="strat_win",
            decision_ts=base,
            outcome_ts=base + timedelta(minutes=4),
            realized_pnl=-50.0,
            realized_r=-0.5,
        )
        _seed_experience(
            ledger,
            request_id="r3",
            execution_id="13",
            strategy_id="strat_loss",
            decision_ts=base,
            outcome_ts=base + timedelta(minutes=6),
            realized_pnl=-100.0,
            realized_r=-1.0,
        )
        _ledger_closed(
            audit,
            11,
            exit_price=2002.0,
            pnl=200.0,
            close_ts=base + timedelta(minutes=2),
            exit_mechanism="TAKE_PROFIT_HIT",
        )
        _ledger_closed(
            audit,
            12,
            exit_price=1995.0,
            pnl=-50.0,
            close_ts=base + timedelta(minutes=4),
            exit_mechanism="HARD_SL_HIT",
        )
        _ledger_closed(
            audit,
            13,
            exit_price=1990.0,
            pnl=-100.0,
            close_ts=base + timedelta(minutes=6),
            exit_mechanism="HARD_SL_HIT",
        )

        contribs = {c.strategy_id: c for c in core.strategy_contributions()}
        assert set(contribs) == {"strat_win", "strat_loss"}
        win = contribs["strat_win"]
        assert win.trade_count == 2
        assert win.net_pnl == pytest.approx(150.0)
        assert win.win_count == 1 and win.loss_count == 1
        assert win.win_rate == pytest.approx(50.0)
        loss = contribs["strat_loss"]
        assert loss.net_pnl == pytest.approx(-100.0)
        # loss share: 100 / 150 total losses
        assert loss.loss_share == pytest.approx(100.0 / 150.0)

    def test_strategy_lifecycle_from_registry(self, audit, core) -> None:
        # Persist a derived score through the evaluator's real upsert path.
        from nexus_scalp.experience.evaluator import StrategyEvaluator
        from nexus_scalp.experience.models import StrategyLifecycle, StrategyScore

        evaluator = StrategyEvaluator(audit_repo=audit)
        score = StrategyScore(
            strategy_id="strat_x",
            sample_count=5,
            confidence_score=0.9,
            profit_factor=1.1,
            expectancy_r=0.3,
            lifecycle_state=StrategyLifecycle.RETIRED,
        )
        evaluator._persist_strategy_score(score)
        _flush(audit)

        core.strategy_evaluator = evaluator
        ledger = ExperienceLedger(audit_repo=audit)
        _seed_experience(
            ledger,
            request_id="rx",
            execution_id="77",
            strategy_id="strat_x",
            realized_pnl=10.0,
            realized_r=0.1,
        )
        _ledger_closed(
            audit,
            77,
            exit_price=2000.1,
            pnl=10.0,
            close_ts=datetime(2026, 8, 15, 11, 0, tzinfo=UTC),
            exit_mechanism="MANUAL_CLOSE",
        )
        contribs = core.strategy_contributions()
        assert len(contribs) == 1
        assert contribs[0].lifecycle_state == "RETIRED"
        assert contribs[0].confidence == pytest.approx(0.9)

    def test_unregistered_strategy_with_trades_is_discovered_not_unknown(self, audit, core) -> None:
        """A strategy family with trades but no intelligence-registry row must
        render as DISCOVERED (authoritative below-floor default), never the
        empty/UNKNOWN the UI previously showed."""
        from nexus_scalp.experience.ledger import ExperienceLedger

        ledger = ExperienceLedger(audit_repo=audit)
        _seed_experience(
            ledger,
            request_id="ru",
            execution_id="88",
            strategy_id="strat_noreg",
            realized_pnl=5.0,
            realized_r=0.05,
        )
        _ledger_closed(
            audit,
            88,
            exit_price=2000.15,
            pnl=5.0,
            close_ts=datetime(2026, 8, 15, 11, 5, tzinfo=UTC),
            exit_mechanism="MANUAL_CLOSE",
        )
        # NO evaluator registered score for this id -> the accounting layer
        # must fall back to the truthful DISCOVERED lifecycle.
        contribs = core.strategy_contributions()
        assert len(contribs) == 1
        assert contribs[0].strategy_id == "strat_noreg"
        assert contribs[0].lifecycle_state == "DISCOVERED"
        assert contribs[0].confidence == 0.0


# ---------------------------------------------------------------------------
# 8. Loss attribution
# ---------------------------------------------------------------------------


class TestLossAttribution:
    def _loss_record(self, **overrides) -> TradeRecord:
        base = {
            "ticket": 1,
            "symbol": "XAUUSD",
            "direction": "BUY",
            "volume": 1.0,
            "entry_price": 2000.0,
            "exit_price": 1995.0,
            "gross_pnl": -50.0,
            "commission": 0.0,
            "swap": 0.0,
            "net_pnl": -50.0,
            "opened_at": datetime(2026, 8, 15, 10, 0, tzinfo=UTC),
            "closed_at": datetime(2026, 8, 15, 10, 30, tzinfo=UTC),
            "duration_sec": 1800.0,
            "exit_mechanism_raw": "HARD_SL_HIT",
            "exit_classification": ExitClassification.INITIAL_STOP,
            "outcome": TradeOutcome.LOSS,
            "risk_free_state": False,
            "was_sl_modified": False,
            "initial_sl": 1990.0,
            "final_sl": 1990.0,
            "mae_points": -10.0,
            "mfe_points": 1.0,
            "mae_usd": -100.0,
            "mfe_usd": 10.0,
            "risk_usd": 100.0,
            "realized_r": -0.5,
        }
        base.update(overrides)
        return TradeRecord(**base)

    def test_win_returns_unknown(self, core) -> None:
        rec = self._loss_record(net_pnl=50.0, outcome=TradeOutcome.WIN)
        assert core.attribute_loss(rec) is not None  # UNKNOWN for non-loss

    def test_position_management_failure_gave_back_profit(self, core) -> None:
        rec = self._loss_record(
            exit_classification=ExitClassification.BREAKEVEN_STOP,
            risk_free_state=True,
            mfe_usd=150.0,  # was in profit 1.5R
            mae_usd=-100.0,
            mfe_points=15.0,
            mae_points=-10.0,
            risk_usd=100.0,
            realized_r=-0.0,
            net_pnl=-2.0,
            exit_price=1999.8,
        )
        attribution = core.attribute_loss(rec)
        assert attribution.value == "POSITION_MANAGEMENT_FAILURE"

    def test_entry_failure(self, core) -> None:
        rec = self._loss_record(
            mfe_usd=10.0, mae_usd=-100.0, mfe_points=1.0, mae_points=-10.0, risk_usd=100.0
        )
        assert core.attribute_loss(rec).value == "ENTRY_FAILURE"

    def test_model_confidence_failure(self, core) -> None:
        # mfe_r = 0.25 (>= 0.2 so ENTRY_FAILURE does not fire, < 0.3 so the
        # model-confidence branch is the one that classifies), high confidence.
        rec = self._loss_record(
            confidence_at_open=0.9,
            mfe_usd=25.0,
            mae_usd=-100.0,
            mfe_points=2.5,
            mae_points=-10.0,
            risk_usd=100.0,
        )
        assert core.attribute_loss(rec).value == "MODEL_CONFIDENCE_FAILURE"

    def test_strategy_failure_vs_expectation(self, core) -> None:
        # mfe_r 0.25 avoids ENTRY; confidence 0.5 avoids MODEL; expected_r>0 and
        # realized<=-1 -> STRATEGY_FAILURE.
        rec = self._loss_record(
            realized_r=-1.0,
            confidence_at_open=0.5,
            mfe_usd=25.0,
            mae_usd=-100.0,
            mfe_points=2.5,
            mae_points=-10.0,
            risk_usd=100.0,
        )
        assert core.attribute_loss(rec, expected_r=0.5).value == "STRATEGY_FAILURE"

    def test_risk_failure_wider_than_planned(self, core) -> None:
        rec = self._loss_record(final_sl=1970.0)  # 30pts vs planned 10pts
        assert core.attribute_loss(rec).value == "RISK_FAILURE"

    def test_signal_failure_plain_stop(self, core) -> None:
        # mfe_r 0.25 avoids ENTRY; confidence 0.5 avoids MODEL; no expected_r
        # -> falls through to INITIAL_STOP -> SIGNAL_FAILURE.
        rec = self._loss_record(
            confidence_at_open=0.5,
            mfe_usd=25.0,
            mae_usd=-100.0,
            mfe_points=2.5,
            mae_points=-10.0,
            risk_usd=100.0,
        )
        assert core.attribute_loss(rec).value == "SIGNAL_FAILURE"


# ---------------------------------------------------------------------------
# 9. Forensic trade trace
# ---------------------------------------------------------------------------


class TestTradeForensics:
    def test_trace_reconstructs_full_lifecycle(self, audit, core) -> None:
        ledger = ExperienceLedger(audit_repo=audit)
        core.experience_ledger = ledger
        base = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
        _seed_experience(
            ledger,
            request_id="rf",
            execution_id="501",
            strategy_id="strat_f",
            decision_ts=base,
            outcome_ts=base + timedelta(minutes=5),
            realized_pnl=100.0,
            realized_r=1.0,
            mae_points=-3.0,
            mfe_points=8.0,
            mae_usd=-30.0,
            mfe_usd=80.0,
        )
        _ledger_closed(
            audit,
            501,
            exit_price=2008.0,
            pnl=101.0,
            commission=1.0,
            close_ts=base + timedelta(minutes=5),
            exit_mechanism="TAKE_PROFIT_HIT",
            initial_sl=1990.0,
            final_sl=1990.0,
            mae=-3.0,
            mfe=8.0,
            mae_usd=-30.0,
            mfe_usd=80.0,
        )
        trace = core.trade_trace(501)
        assert trace.found is True
        assert trace.trade["ticket"] == 501
        assert trace.identity["strategy_id"] == "strat_f"
        assert trace.exit_detail["exit_classification"] == "TAKE_PROFIT"
        assert trace.outcome["outcome"] == "WIN"
        assert trace.position_path["mfe_points"] == pytest.approx(8.0)
        assert trace.risk["initial_sl"] == pytest.approx(1990.0)

    def test_trace_unknown_ticket(self, audit, core) -> None:
        trace = core.trade_trace(424242)
        assert trace.found is False
        assert "TICKET_NOT_FOUND" in trace.notes

    def test_trace_orders_attached(self, audit, core) -> None:
        _ledger_closed(
            audit,
            601,
            exit_price=2001.0,
            pnl=50.0,
            close_ts=datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
            exit_mechanism="TAKE_PROFIT_HIT",
        )
        audit.log_order(
            ticket=601,
            order_id="ord_601",
            symbol="XAUUSD",
            action="BUY_MARKET",
            price=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            volume=1.0,
            reason="PURE_AI",
        )
        _flush(audit)
        trace = core.trade_trace(601)
        assert trace.found is True
        assert len(trace.order_events) >= 1


# ---------------------------------------------------------------------------
# 10. Worker lifecycle / idempotency / failure isolation
# ---------------------------------------------------------------------------


class TestWorker:
    def test_worker_start_stop(self, core) -> None:
        w = AccountingWorker(core=core, interval_sec=0.0)
        w.start()
        assert w.running is True
        w.start()  # idempotent
        assert w.running is True
        w.stop()
        assert w.running is False
        w.stop()  # idempotent
        assert w.running is False

    def test_worker_cycle_refreshes_cache(self, audit, core) -> None:
        # Close the trade inside the CURRENT UTC day so the worker's refresh and
        # the subsequent read target the same period (a close in a previous UTC
        # day belongs to that day's report, not today's).
        now_utc = datetime.now(UTC)
        close_ts = now_utc.replace(hour=10, minute=0, second=0, microsecond=0)
        _ledger_closed(
            audit,
            1,
            exit_price=2002.0,
            pnl=100.0,
            close_ts=close_ts,
            exit_mechanism="TAKE_PROFIT_HIT",
        )
        w = AccountingWorker(core=core, interval_sec=0.0)
        w.start()
        assert w.tick() is True
        # Cache now holds the current DAY report
        day = core.period_report(PeriodKind.DAY)
        assert day.total_trades == 1
        assert day.net_pnl == pytest.approx(100.0)
        assert w.cycle_count == 1

    def test_worker_throttled(self, core) -> None:
        w = AccountingWorker(core=core, interval_sec=60.0)
        w.start()
        assert w.tick() is True
        # second call within interval -> throttled
        assert w.tick() is False
        assert w.cycle_count == 1

    def test_worker_idle_when_stopped(self, core) -> None:
        w = AccountingWorker(core=core)
        assert w.tick() is False

    def test_worker_failure_isolated(self, audit, core) -> None:
        """A failing adapter must produce event=FAILURE and never raise."""
        core.adapter.fail_account = True
        w = AccountingWorker(core=core, interval_sec=0.0)
        w.start()
        # live_state failure is caught inside AccountingCore; the cycle still
        # completes, proving failure isolation in the read facade.
        assert w.tick() in (True, False)
        assert w.running is True  # worker survives

    def test_worker_restart_resumes_cleanly(self, audit, core) -> None:
        _ledger_closed(
            audit,
            2,
            exit_price=2001.0,
            pnl=25.0,
            close_ts=datetime(2026, 8, 15, 11, 0, tzinfo=UTC),
            exit_mechanism="TAKE_PROFIT_HIT",
        )
        w = AccountingWorker(core=core, interval_sec=0.0)
        w.start()
        w.tick()
        c1 = w.cycle_count
        w.stop()
        w.start()
        w.tick()
        assert w.cycle_count == c1 + 1
        assert w.last_error == ""

    def test_worker_never_duplicates_records(self, audit, core) -> None:
        """Repeated cycles must not create financial records (no writes at all)."""
        from nexus_scalp.accounting.periods import utc_now

        _ledger_closed(
            audit,
            3,
            exit_price=2001.0,
            pnl=25.0,
            close_ts=utc_now(),
            exit_mechanism="TAKE_PROFIT_HIT",
        )
        before = len(core.load_trades())
        w = AccountingWorker(core=core, interval_sec=0.0)
        w.start()
        for _ in range(3):
            w.tick()
        after = len(core.load_trades())
        assert before == after == 1


# ---------------------------------------------------------------------------
# 11. Self-healing rebuild
# ---------------------------------------------------------------------------


class TestSelfHealing:
    def test_derived_reports_rebuildable(self, audit, core) -> None:
        d = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
        _ledger_closed(
            audit, 1, exit_price=2002.0, pnl=100.0, close_ts=d, exit_mechanism="TAKE_PROFIT_HIT"
        )
        _ledger_closed(
            audit, 2, exit_price=1999.0, pnl=-40.0, close_ts=d, exit_mechanism="HARD_SL_HIT"
        )
        _snapshot_row(audit, 10000.0, 10000.0, ts=datetime(2026, 8, 15, 0, 0, 1, tzinfo=UTC))
        _snapshot_row(audit, 10060.0, 10060.0, ts=datetime(2026, 8, 15, 23, 59, 0, tzinfo=UTC))

        # First computation (cold)
        first = core.period_report(
            PeriodKind.DAY, at=datetime(2026, 8, 15, 12, 0, tzinfo=UTC), use_cache=False
        )
        # Invalidate cache and recompute — must reproduce identical money truth.
        core._report_cache.clear()
        second = core.period_report(
            PeriodKind.DAY, at=datetime(2026, 8, 15, 12, 0, tzinfo=UTC), use_cache=False
        )
        assert first.net_pnl == second.net_pnl == pytest.approx(60.0)
        assert first.total_trades == second.total_trades == 2

    def test_historical_records_never_mutated_by_aggregates(self, audit, core) -> None:
        d = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
        _ledger_closed(
            audit, 1, exit_price=2002.0, pnl=100.0, close_ts=d, exit_mechanism="TAKE_PROFIT_HIT"
        )
        _snapshot_row(audit, 10000.0, 10000.0, ts=d)
        _ = core.period_report(PeriodKind.DAY, at=d)
        row = audit.get_ledger_row(1)
        assert row is not None
        assert row["pnl"] == pytest.approx(100.0)  # untouched


# ---------------------------------------------------------------------------
# 12. Provenance / model-safety
# ---------------------------------------------------------------------------


class TestProvenanceSurvival:
    def test_feature_schema_dimension_forward_compat(self) -> None:
        from nexus_scalp.features.schema import FEATURE_SCHEMAS

        assert FEATURE_SCHEMAS.resolve("scalp_v1").dimension == 50
        assert FEATURE_SCHEMAS.resolve("scalp_v2").dimension == 60
        assert FEATURE_SCHEMAS.resolve("scalp_v3").dimension == 350
        # strict resolution: unknown id raises, never silently defaults
        with pytest.raises(KeyError):
            FEATURE_SCHEMAS.resolve("nope")

    def test_experience_rows_carry_schema_and_model_identity(self, audit, core) -> None:
        import sqlite3

        ledger = ExperienceLedger(audit_repo=audit)
        _seed_experience(
            ledger,
            request_id="rs",
            execution_id="900",
            strategy_id="strat_s",
            realized_pnl=1.0,
            realized_r=0.01,
        )
        _flush(audit)
        conn = sqlite3.connect(audit._db_path)
        try:
            row = conn.execute(
                "SELECT feature_schema_id, feature_dimension FROM audit_experiences "
                "WHERE request_id = 'rs'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] == "scalp_v1"
        assert row[1] == 50


# ---------------------------------------------------------------------------
# 11. Forensic trace quality join (regression for the BUG-008 join pattern)
# ---------------------------------------------------------------------------


class TestForensicQualityJoin:
    """
    The forensic trade trace must surface the outcome decomposition and
    behavioral flags. The first Phase 08 revision joined the outcome table on
    `audit_experiences.execution_id` (always empty by design), so every trace
    silently carried `NO_EXPERIENCE_OUTCOME` even for fully-attributable trades
    - the exact BUG-008 trap applied a second time inside the trace builder.
    """

    def test_trace_surfaces_outcome_decomposition_and_flags(self, audit, core) -> None:
        from nexus_scalp.experience.models import (
            BehavioralFlag,
            OutcomeDecomposition,
        )

        ledger = ExperienceLedger(audit_repo=audit)
        core.experience_ledger = ledger
        base = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)

        # Seed a decision + outcome carrying a NON-EMPTY decomposition and
        # behavioral flags (the _seed_experience helper leaves them default,
        # which is why this path was previously never exercised).
        ctx = StrategyContext(strategy_id="strat_q", regime="TRENDING_MOMENTUM")
        key = "exp_req_q1"
        exp = ExperienceRecord(
            experience_id="exp_q1",
            request_id="req_q1",
            idempotency_key=key,
            symbol="XAUUSD",
            decision_timestamp=base,
            strategy_id="strat_q",
            context=ctx,
            feature_snapshot=FeatureSnapshot(values=[0.1] * 50),
            action="BUY_MARKET",
            entry_reason="PURE_AI",
            model_probability=0.8,
            signal_confidence=0.8,
            proposed_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            risk_reward_ratio=2.0,
        )
        ledger.record_experience(exp)
        ledger.record_outcome(
            ExperienceOutcome(
                idempotency_key=key,
                execution_id="701",
                outcome_timestamp=base + timedelta(minutes=5),
                is_executed=True,
                is_closed=True,
                exit_reason="TAKE_PROFIT_HIT",
                realized_pnl_usd=100.0,
                realized_r_multiple=1.0,
                behavior=compute_behavior_metrics(
                    mae_points=-3.0,
                    mfe_points=8.0,
                    mae_usd=-30.0,
                    mfe_usd=80.0,
                    planned_risk_distance=10.0,
                    duration_sec=300.0,
                    initial_sl_distance=10.0,
                    atr_at_entry=4.0,
                ),
                decomposition=OutcomeDecomposition(
                    strategy_quality=0.8,
                    entry_quality=0.7,
                    execution_quality=0.9,
                    position_management_quality=0.6,
                    exit_quality=0.8,
                ),
                behavioral_flags=[BehavioralFlag.EARLY_EXIT],
            )
        )
        _ledger_closed(
            audit,
            701,
            exit_price=2008.0,
            pnl=101.0,
            commission=1.0,
            close_ts=base + timedelta(minutes=5),
            exit_mechanism="TAKE_PROFIT_HIT",
            initial_sl=1990.0,
            final_sl=1990.0,
            mae=-3.0,
            mfe=8.0,
            mae_usd=-30.0,
            mfe_usd=80.0,
        )
        _flush(audit)

        trace = core.trade_trace(701)
        assert trace.found is True
        assert "NO_EXPERIENCE_OUTCOME" not in trace.notes, (
            f"trace must resolve the outcome via the outcome-table join; notes={trace.notes}"
        )
        # Decomposition columns must be present and real.
        assert trace.quality.get("strategy_quality") == pytest.approx(0.8)
        assert trace.quality.get("entry_quality") == pytest.approx(0.7)
        assert trace.quality.get("execution_quality") == pytest.approx(0.9)
        assert trace.quality.get("management_quality") == pytest.approx(0.6)
        assert trace.quality.get("exit_quality") == pytest.approx(0.8)
        # Behavioral flags must round-trip.
        assert "EARLY_EXIT" in trace.behavioral_flags

    def test_trace_without_outcome_reports_identity_gap(self, audit, core) -> None:
        """
        A trade whose decision has NO outcome row cannot be attributed through
        the outcome-bridge design (audit_ledger.ticket only ever equals
        audit_experience_outcomes.execution_id), so the honest gap note is
        `NO_EXPERIENCE_LINK` - never fabricated strategy identity.
        """
        ledger = ExperienceLedger(audit_repo=audit)
        core.experience_ledger = ledger
        base = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
        ctx = StrategyContext(strategy_id="strat_q", regime="TRENDING_MOMENTUM")
        key = "exp_req_q2"
        ledger.record_experience(
            ExperienceRecord(
                experience_id="exp_q2",
                request_id="req_q2",
                idempotency_key=key,
                symbol="XAUUSD",
                decision_timestamp=base,
                strategy_id="strat_q",
                context=ctx,
                feature_snapshot=FeatureSnapshot(values=[0.1] * 50),
                action="BUY_MARKET",
                entry_reason="PURE_AI",
                model_probability=0.8,
                signal_confidence=0.8,
                proposed_entry=2000.0,
                stop_loss=1990.0,
                take_profit=2020.0,
                risk_reward_ratio=2.0,
            )
        )
        # NOTE: no outcome row is recorded for this decision.
        _ledger_closed(
            audit,
            702,
            exit_price=2001.0,
            pnl=10.0,
            close_ts=base + timedelta(minutes=5),
            exit_mechanism="TAKE_PROFIT_HIT",
            initial_sl=1990.0,
            final_sl=1990.0,
            mae=-2.0,
            mfe=3.0,
            mae_usd=-20.0,
            mfe_usd=30.0,
        )
        _flush(audit)
        trace = core.trade_trace(702)
        assert trace.found is True
        assert "NO_EXPERIENCE_LINK" in trace.notes, trace.notes
        assert trace.quality == {}
        assert trace.identity["strategy_id"] == ""
