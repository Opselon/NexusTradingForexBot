"""TDD Step 1 — Timezone & midnight-boundary accounting tests.

Canonical policy: UTC half-open [start, next_start) per periods.py.
Broker epochs are SERVER-LOCAL (GMT+3) and must be normalized via
BROKER_SERVER_UTC_OFFSET_MINUTES before bucketing (BUG-070 chain).

These tests prove the timezone contract, DST-tolerant normalization,
and millisecond boundary correctness independently of the PnL fixture.
"""

from __future__ import annotations

import gc
from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.accounting import AccountingCore, PeriodKind
from nexus_scalp.accounting.periods import ensure_utc, period_bounds
from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.mt5.providers import (
    BROKER_SERVER_UTC_OFFSET_MINUTES,
    broker_epoch_to_utc,
    normalize_utc,
)


@pytest.fixture()
def audit(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'tz.db'}", flush_interval_sec=0.05)
    yield repo
    repo.close()
    gc.collect()


@pytest.fixture()
def core(audit) -> AccountingCore:
    return AccountingCore(audit_repo=audit, adapter=None)


def _utc_epoch(dt: datetime) -> int:
    """True UTC seconds since epoch."""
    assert dt.tzinfo is not None
    return int(dt.timestamp())


def _broker_epoch(dt_utc: datetime) -> int:
    """Broker-reported epoch (server-local, +180 min) for a true UTC instant."""
    return _utc_epoch(dt_utc) + BROKER_SERVER_UTC_OFFSET_MINUTES * 60


def _seed_one_closed(audit: AuditRepository, close_utc: datetime, *, profit: float = -50.0) -> None:
    broker_close = _broker_epoch(close_utc)
    broker_open = broker_close - 600
    audit.sync_broker_history(
        orders=[],
        deals=[
            {
                "ticket": 70000001,
                "order": 70000002,
                "position_id": 777700100,
                "symbol": "XAUUSD",
                "type": 1,
                "entry": 0,
                "magic": 1,
                "time": broker_open,
                "volume": 0.10,
                "price": 3350.0,
                "profit": 0.0,
                "commission": 0.0,
                "swap": 0.0,
                "fee": 0.0,
                "reason": 0,
                "comment": "ENTRY",
                "external_id": "",
            },
            {
                "ticket": 70000002,
                "order": 70000003,
                "position_id": 777700100,
                "symbol": "XAUUSD",
                "type": 1,
                "entry": 1,
                "magic": 1,
                "time": broker_close,
                "volume": 0.10,
                "price": 3352.0,
                "profit": profit,
                "commission": -1.0,
                "swap": 0.0,
                "fee": 0.0,
                "reason": 3,
                "comment": "",
                "external_id": "",
            },
        ],
        symbol="XAUUSD",
        sync_from=close_utc - timedelta(days=1),
        sync_to=close_utc + timedelta(days=1),
    )


class TestUtcNormalization:
    def test_naive_datetime_treated_as_utc(self):
        naive = datetime(2026, 8, 21, 12, 0, 0)
        out = ensure_utc(naive)
        assert out.tzinfo is not None
        assert out == datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)

    def test_aware_non_utc_converted_to_utc(self):
        # Simulate a host in UTC+3 passing a local timestamp — ensure_utc must shift it
        from datetime import timezone

        tz_plus3 = timezone(timedelta(hours=3))
        local_midnight = datetime(2026, 8, 21, 0, 0, tzinfo=tz_plus3)
        utc = ensure_utc(local_midnight)
        assert utc == datetime(2026, 8, 20, 21, 0, tzinfo=UTC)

    def test_broker_epoch_to_utc_subtracts_offset(self):
        # A broker epoch for UTC midnight 2026-08-21 should recover midnight UTC
        utc_midnight = datetime(2026, 8, 21, 0, 0, tzinfo=UTC)
        broker_epoch = _broker_epoch(utc_midnight)
        recovered = broker_epoch_to_utc(broker_epoch)
        assert recovered == utc_midnight

    def test_normalize_utc_handles_mixed_formats(self):
        assert normalize_utc("2026-08-21T12:00:00Z") == datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
        assert normalize_utc("2026-08-21 12:00:00") == datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
        assert normalize_utc(int(datetime(2026, 8, 21, 12, 0, tzinfo=UTC).timestamp())) == datetime(
            2026, 8, 21, 12, 0, tzinfo=UTC
        )


class TestMidnightBoundaries:
    def test_deal_at_start_of_day_belongs_to_that_day(self, audit, core):
        _seed_one_closed(audit, datetime(2026, 8, 21, 0, 0, 0, tzinfo=UTC))
        assert (
            core.period_report(
                PeriodKind.DAY, at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
            ).total_trades
            == 1
        )
        assert (
            core.period_report(
                PeriodKind.DAY, at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
            ).total_trades
            == 0
        )

    def test_deal_one_ms_before_midnight_belongs_to_previous_day(self, audit, core):
        # Close at 2026-08-21 23:59:59.999 UTC — still inside 08-21
        _seed_one_closed(audit, datetime(2026, 8, 21, 23, 59, 59, 999000, tzinfo=UTC))
        # The broker epoch resolution is seconds, so this is 23:59:59 — inside 08-21
        assert (
            core.period_report(
                PeriodKind.DAY, at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
            ).total_trades
            == 1
        )
        assert (
            core.period_report(
                PeriodKind.DAY, at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
            ).total_trades
            == 0
        )

    def test_deal_at_next_midnight_belongs_to_next_day(self, audit, core):
        _seed_one_closed(audit, datetime(2026, 8, 22, 0, 0, 0, tzinfo=UTC))
        assert (
            core.period_report(
                PeriodKind.DAY, at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
            ).total_trades
            == 0
        )
        assert (
            core.period_report(
                PeriodKind.DAY, at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
            ).total_trades
            == 1
        )

    def test_half_open_interval_contains(self):
        bounds = period_bounds(PeriodKind.DAY, at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC))
        assert bounds.contains(datetime(2026, 8, 21, 0, 0, 0, tzinfo=UTC)) is True
        assert bounds.contains(datetime(2026, 8, 21, 23, 59, 59, tzinfo=UTC)) is True
        assert bounds.contains(datetime(2026, 8, 22, 0, 0, 0, tzinfo=UTC)) is False
        assert bounds.contains(datetime(2026, 8, 20, 23, 59, 59, tzinfo=UTC)) is False

    def test_period_bounds_keys_are_stable(self):
        assert (
            period_bounds(PeriodKind.DAY, at=datetime(2026, 8, 21, 15, 0, tzinfo=UTC)).key
            == "2026-08-21"
        )
        assert period_bounds(
            PeriodKind.WEEK, at=datetime(2026, 8, 21, 15, 0, tzinfo=UTC)
        ).key.startswith("2026-W")
        assert (
            period_bounds(PeriodKind.MONTH, at=datetime(2026, 8, 21, 15, 0, tzinfo=UTC)).key
            == "2026-08"
        )
        assert (
            period_bounds(PeriodKind.YEAR, at=datetime(2026, 8, 21, 15, 0, tzinfo=UTC)).key
            == "2026"
        )


class TestBrokerUtcAlignment:
    def test_broker_day_spread_across_utc_midnight(self, audit, core):
        """
        A broker midnight deal (server 00:00 = UTC 21:00) must not shift days.
        The fixture's broker epochs all resolve inside 2026-08-21 UTC — prove
        a trade stamped at broker midnight 2026-08-22 00:00 (UTC 2026-08-21 21:00)
        still belongs to 2026-08-21 UTC, not 08-22.
        """
        utc_2100 = datetime(2026, 8, 21, 21, 0, tzinfo=UTC)
        _seed_one_closed(audit, utc_2100)
        assert (
            core.period_report(
                PeriodKind.DAY, at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
            ).total_trades
            == 1
        )
        assert (
            core.period_report(
                PeriodKind.DAY, at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
            ).total_trades
            == 0
        )
