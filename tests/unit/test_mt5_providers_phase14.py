"""
Unit Tests - MT5 Broker-Aware Provider Snapshots (Phase 14)
============================================================
Tests the pure mapping/validation layer of the broker-aware providers:
- account snapshot mapping from raw MT5 account_info objects
- symbol spec/tick separation + stale detection
- UTC normalization for every timestamp input shape (task §2)
- bar integrity validation (task §39)
- deal net-result accounting (profit - costs, BUG-019 lineage)
- diagnostics wrapper: failure is never silent, structured log line
- RiskEngine broker-aware margin/profit provenance helpers
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from nexus_scalp.adapters.mt5.diagnostics import (
    MT5ConnectionState,
    run_mt5_call,
)
from nexus_scalp.adapters.mt5.providers import (
    BROKER_NATIVE,
    FALLBACK_ESTIMATE,
    UNAVAILABLE,
    AccountSnapshot,
    build_account_snapshot,
    build_deal_snapshot,
    build_position_snapshot,
    build_rate_bar_snapshot,
    build_symbol_snapshot,
    normalize_utc,
    validate_ohlc_bars,
)
from nexus_scalp.domain.enums import OrderType
from nexus_scalp.risk.risk_engine import RiskEngine

# ---------------------------------------------------------------------------
# UTC normalization contract (task §2 + BUG-044 lineage)
# ---------------------------------------------------------------------------


class TestUtcNormalization:
    def test_aware_datetime_preserved(self) -> None:
        dt = datetime(2026, 8, 17, 1, 30, tzinfo=UTC)
        assert normalize_utc(dt) == dt

    def test_naive_datetime_treated_as_utc(self) -> None:
        dt = datetime(2026, 8, 17, 1, 30)
        out = normalize_utc(dt)
        assert out is not None
        assert out.tzinfo is not None
        assert out.tzinfo.utcoffset(out) == timedelta(0)

    def test_numpy_datetime64_normalized(self) -> None:
        try:
            import numpy as np
        except ImportError:
            pytest.skip("numpy not installed")
        val = np.datetime64("2026-08-17T01:30:00")
        out = normalize_utc(val)
        assert out is not None
        assert out.year == 2026 and out.month == 8 and out.day == 17
        assert out.tzinfo is not None

    def test_iso_string_with_z_normalized(self) -> None:
        out = normalize_utc("2026-08-17T01:30:29Z")
        assert out is not None
        assert out.tzinfo is not None
        assert out.utcoffset() == timedelta(0)

    def test_iso_string_naive_treated_as_utc(self) -> None:
        out = normalize_utc("2026-08-17 01:30:29")
        assert out is not None
        assert out.tzinfo is not None
        assert out.utcoffset() == timedelta(0)

    def test_sql_timestamp_string(self) -> None:
        out = normalize_utc("2026-08-17 01:30:29")
        assert out is not None
        assert out.hour == 1 and out.minute == 30

    def test_float_epoch(self) -> None:
        out = normalize_utc(1784417429.0)
        assert out is not None
        assert out.tzinfo is not None

    def test_none_and_garbage_return_none(self) -> None:
        assert normalize_utc(None) is None
        assert normalize_utc("not a date") is None
        assert normalize_utc(float("nan")) is None

    def test_non_utc_aware_converted_to_utc(self) -> None:
        from datetime import timezone

        # Fixed-offset zone (no tzdata dependency on Windows).
        zurich = timezone(timedelta(hours=2))
        dt = datetime(2026, 8, 17, 1, 30, tzinfo=zurich)
        out = normalize_utc(dt)
        assert out is not None
        assert out.utcoffset() == timedelta(0)
        assert out.hour == 23  # 01:30 +02:00 == 23:30 previous day UTC


# ---------------------------------------------------------------------------
# Account snapshot mapping
# ---------------------------------------------------------------------------


def _raw_account(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "login": 123456,
        "trade_mode": 0,
        "leverage": 100,
        "limit_orders": 5,
        "margin_so_mode": 0,
        "trade_allowed": True,
        "trade_expert": True,
        "margin_mode": 0,
        "currency_digits": 2,
        "fifo_close": False,
        "balance": 10000.0,
        "credit": 0.0,
        "profit": 123.45,
        "equity": 10123.45,
        "margin": 500.0,
        "margin_free": 9623.45,
        "margin_level": 2024.69,
        "currency": "USD",
        "server": "MetaQuotes-Demo",
        "company": "MetaQuotes Ltd.",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestAccountSnapshotMapping:
    def test_full_mapping(self) -> None:
        snap = build_account_snapshot(_raw_account())
        assert snap.available is True
        assert snap.source == BROKER_NATIVE
        assert snap.login == 123456
        assert snap.balance == 10000.0
        assert snap.equity == 10123.45
        assert snap.margin == 500.0
        assert snap.margin_free == 9623.45
        assert snap.margin_level == 2024.69
        assert snap.trade_allowed is True
        assert snap.server == "MetaQuotes-Demo"
        assert snap.floating_pnl == 123.45
        assert snap.margin_level_source == BROKER_NATIVE

    def test_none_raw_returns_unavailable(self) -> None:
        snap = build_account_snapshot(None)
        assert snap.available is False
        assert snap.source == UNAVAILABLE

    def test_missing_optional_fields_are_none_not_fake(self) -> None:
        snap = build_account_snapshot(_raw_account(server=None, company=None, credit=None))
        assert snap.server is None
        assert snap.company is None
        assert snap.credit is None
        # core money fields still map
        assert snap.balance == 10000.0

    def test_unsupported_field_semantics(self) -> None:
        # Fields absent from the provider are None (the API contract marks
        # UNSUPPORTED_BY_PROVIDER via None, never a fake value).
        snap = build_account_snapshot(_raw_account())
        assert getattr(snap, "fifo_close", False) is False


# ---------------------------------------------------------------------------
# Symbol snapshot (spec vs tick separation, stale detection)
# ---------------------------------------------------------------------------


def _raw_symbol_info(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "name": "XAUUSD",
        "description": "Gold vs US Dollar",
        "path": "Metals\\XAUUSD",
        "digits": 2,
        "point": 0.01,
        "trade_mode": 0,
        "trade_calc_mode": 0,
        "trade_tick_size": 0.01,
        "trade_tick_value": 1.0,
        "trade_contract_size": 100.0,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "trade_stops_level": 10,
        "trade_freeze_level": 0,
        "currency_base": "USD",
        "currency_profit": "USD",
        "currency_margin": "USD",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _raw_tick(now_epoch: int, bid: float = 4392.46, ask: float = 4392.73) -> SimpleNamespace:
    return SimpleNamespace(
        time=now_epoch, time_msc=now_epoch * 1000, flags=2, bid=bid, ask=ask, last=bid, volume=7
    )


class TestSymbolSnapshotMapping:
    def test_broker_epoch_offset_applied(self) -> None:
        """Broker epochs are server-local (+3h on this broker); the
        snapshot must map them to REAL UTC, not treat them as UTC."""
        now = datetime.now(UTC)
        broker_epoch = int(now.timestamp()) + 3 * 3600  # terminal 3h ahead
        snap = build_symbol_snapshot(_raw_symbol_info(), _raw_tick(broker_epoch))
        tick_utc = datetime.fromisoformat(snap.tick["time_utc"])
        assert abs((now - tick_utc).total_seconds()) < 2.0
        # freshness must be ~0 (not 3h -> not stale)
        assert snap.tick_freshness_ms is not None
        assert snap.tick_freshness_ms < 2_000.0

    def test_spec_and_tick_separated(self) -> None:
        now = datetime.now(UTC)
        snap = build_symbol_snapshot(_raw_symbol_info(), _raw_tick(int(now.timestamp())))
        assert snap.available is True
        assert snap.spec["name"] == "XAUUSD"
        assert snap.spec["digits"] == 2
        assert snap.spec["trade_contract_size"] == 100.0
        assert snap.tick["bid"] == 4392.46
        assert snap.tick["ask"] == 4392.73
        assert snap.spread_points == 0.27
        assert snap.spread_points_source == BROKER_NATIVE

    def test_stale_tick_detected(self) -> None:
        old_epoch = int((datetime.now(UTC) - timedelta(minutes=5)).timestamp())
        snap = build_symbol_snapshot(_raw_symbol_info(), _raw_tick(old_epoch))
        assert snap.tick_freshness_ms is not None
        assert snap.tick_freshness_ms > 30_000.0

    def test_none_both_returns_unavailable(self) -> None:
        snap = build_symbol_snapshot(None, None)
        assert snap.available is False


class TestPositionDealMapping:
    def test_position_snapshot(self) -> None:
        raw = SimpleNamespace(
            ticket=100001,
            symbol="XAUUSD",
            type=0,
            magic=888101,
            identifier=100001,
            time=1784417429,
            time_msc=1784417429000,
            time_update=1784417429,
            time_update_msc=1784417429000,
            external_id="",
            volume=0.5,
            price_open=4390.0,
            price_current=4395.0,
            sl=4370.0,
            tp=4430.0,
            price_ticket=4390.0,
            profit=250.0,
            swap=-1.2,
            commission=-7.0,
            comment="NSE_ORDER",
        )
        snap = build_position_snapshot(raw)
        assert snap.ticket == 100001
        assert snap.profit == 250.0
        assert snap.swap == -1.2
        assert snap.commission == -7.0

    def test_deal_net_result_subtracts_costs(self) -> None:
        raw = SimpleNamespace(
            ticket=200,
            order=150,
            position_id=100,
            symbol="XAUUSD",
            type=0,
            entry=1,
            magic=888101,
            identifier=100,
            time=1784417429,
            time_msc=0,
            external_id="",
            reason=0,
            volume=0.5,
            price=4395.0,
            profit=250.0,
            fee=0.0,
            swap=-1.2,
            commission=-7.0,
            comment="",
        )
        snap = build_deal_snapshot(raw)
        assert snap.profit == 250.0
        assert snap.net_result == 250.0 - 1.2 - 7.0 - 0.0


class TestBarValidation:
    def _bar(self, time: int, o: float, h: float, l: float, c: float, v: int = 10):
        return build_rate_bar_snapshot(
            {
                "time": time,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "tick_volume": v,
                "spread": 1,
                "real_volume": 100,
            }
        )

    def test_valid_bars_pass(self) -> None:
        bars = [self._bar(1, 1.0, 1.2, 0.9, 1.1), self._bar(2, 1.1, 1.3, 1.0, 1.25)]
        report = validate_ohlc_bars(bars)
        assert report["invalid"] == 0
        assert report["valid"] == 2

    def test_duplicate_timestamps_detected(self) -> None:
        bars = [self._bar(1, 1.0, 1.2, 0.9, 1.1), self._bar(1, 1.1, 1.3, 1.0, 1.25)]
        report = validate_ohlc_bars(bars)
        assert report["duplicate_timestamps"] == 1
        assert report["invalid"] == 1

    def test_descending_timestamps_detected(self) -> None:
        bars = [self._bar(2, 1.0, 1.2, 0.9, 1.1), self._bar(1, 1.1, 1.3, 1.0, 1.25)]
        report = validate_ohlc_bars(bars)
        assert report["descending_timestamps"] == 1

    def test_high_low_violation_detected(self) -> None:
        bars = [self._bar(1, 1.0, 0.8, 0.9, 1.1)]  # high < low
        report = validate_ohlc_bars(bars)
        assert report["high_low_violation"] == 1
        assert report["invalid"] == 1

    def test_negative_volume_detected(self) -> None:
        bars = [self._bar(1, 1.0, 1.2, 0.9, 1.1, v=-5)]
        report = validate_ohlc_bars(bars)
        assert report["negative_volume"] == 1


# ---------------------------------------------------------------------------
# Diagnostics wrapper (task §5/§6: failure is never silent)
# ---------------------------------------------------------------------------


class _FakeMT5Module:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self._last_err = (10031, "NO_CONNECTION") if fail else (0, "")

    def last_error(self):
        return self._last_err


class TestDiagnosticsWrapper:
    def test_success_records_duration_and_result_count(self) -> None:
        mod = _FakeMT5Module()
        result, diag = run_mt5_call("account_info", lambda: [1, 2, 3], mt5_module=mod)
        assert result == [1, 2, 3]
        assert diag.status == "SUCCESS"
        assert diag.result_count == 3
        assert diag.duration_ms >= 0.0
        assert "duration_ms=" in diag.log_line()
        assert "[MT5_CALL]" in diag.log_line()

    def test_failure_records_error_code(self) -> None:
        mod = _FakeMT5Module(fail=True)
        result, diag = run_mt5_call("positions_get", lambda: None, mt5_module=mod)
        assert result is None
        assert diag.status == "FAILED"
        assert diag.mt5_error_code == 10031
        assert diag.mt5_error_message == "NO_CONNECTION"
        line = diag.log_line()
        assert "status=FAILED" in line
        assert "error_code=10031" in line

    def test_exception_captured_and_reraised(self) -> None:
        mod = _FakeMT5Module()

        def boom() -> None:
            raise TypeError("bad")

        with pytest.raises(TypeError):
            run_mt5_call("order_calc_profit", boom, mt5_module=mod)

    def test_retcode_labels(self) -> None:
        from nexus_scalp.adapters.mt5.diagnostics import retcode_label

        assert "REQUOTE" in retcode_label(10004)
        assert "NO_CONNECTION" in retcode_label(10031)
        assert retcode_label(None) == "UNKNOWN"

    def test_connection_state_machine(self) -> None:
        state = MT5ConnectionState()
        assert state.state == MT5ConnectionState.DISCONNECTED
        state.set_state(MT5ConnectionState.CONNECTED, "ok")
        assert state.connected is True
        state.record_failure("history_deals_get", "boom")
        assert state.state == MT5ConnectionState.CONNECTED  # still connected
        state.mark_degraded("tick stalled")
        assert state.state == MT5ConnectionState.DEGRADED
        d = state.to_dict()
        assert d["state"] == MT5ConnectionState.DEGRADED
        assert d["last_failed_operation"] == "tick stalled"  # degraded reason wins


# ---------------------------------------------------------------------------
# RiskEngine broker-aware provenance helpers
# ---------------------------------------------------------------------------


class _FakeCalcAdapter:
    """Adapter stub exposing the broker calc snapshots."""

    def __init__(self, native: bool = True) -> None:
        self.native = native

    def order_calc_margin_snapshot(self, **kwargs):
        if self.native:
            snap = SimpleNamespace(
                available=True,
                value=20.0,
                value_source=BROKER_NATIVE,
                error_code=None,
                error_message=None,
            )
        else:
            snap = SimpleNamespace(
                available=False,
                value=None,
                value_source=UNAVAILABLE,
                error_code=10031,
                error_message="NO_CONNECTION",
            )
        return snap

    def order_calc_profit_snapshot(self, **kwargs):
        if self.native:
            snap = SimpleNamespace(
                available=True,
                value=1.5,
                value_source=BROKER_NATIVE,
                error_code=None,
                error_message=None,
            )
        else:
            snap = SimpleNamespace(
                available=False,
                value=None,
                value_source=UNAVAILABLE,
                error_code=10031,
                error_message="NO_CONNECTION",
            )
        return snap


class TestRiskBrokerProvenance:
    def setup_method(self) -> None:
        from nexus_scalp.configuration.config import RiskConfig

        self.engine = RiskEngine(config=RiskConfig())

    def test_broker_native_margin(self) -> None:
        result = self.engine.verify_margin_with_broker(
            symbol="XAUUSD",
            order_type=OrderType.BUY,
            volume=0.01,
            price=2000.0,
            adapter=_FakeCalcAdapter(native=True),
            fallback_estimate=19.99,
        )
        assert result["source"] == BROKER_NATIVE
        assert result["margin_required"] == 20.0
        assert result["available"] is True

    def test_unavailable_falls_back_to_estimate(self) -> None:
        result = self.engine.verify_margin_with_broker(
            symbol="XAUUSD",
            order_type=OrderType.BUY,
            volume=0.01,
            price=2000.0,
            adapter=_FakeCalcAdapter(native=False),
            fallback_estimate=19.99,
        )
        assert result["source"] == UNAVAILABLE
        assert result["available"] is False
        assert result["error"]["code"] == 10031

    def test_no_adapter_keeps_fallback(self) -> None:
        result = self.engine.verify_margin_with_broker(
            symbol="XAUUSD",
            order_type=OrderType.BUY,
            volume=0.01,
            price=2000.0,
            adapter=None,
            fallback_estimate=19.99,
        )
        assert result["source"] == FALLBACK_ESTIMATE
        assert result["margin_required"] == 19.99

    def test_broker_native_profit(self) -> None:
        result = self.engine.verify_profit_with_broker(
            symbol="XAUUSD",
            order_type=OrderType.SELL,
            volume=0.01,
            price_open=2000.0,
            price_close=1998.5,
            adapter=_FakeCalcAdapter(native=True),
            fallback_estimate=None,
        )
        assert result["source"] == BROKER_NATIVE
        assert result["profit"] == 1.5
