"""Raw MT5 response mapping tests (REAL captured fixtures, no mocking).

Proves the mapping layer understands the EXACT structures the installed
MetaTrader5 package returned on the live terminal (2026-08-17): namedtuple
field names, types, nullability, and canonical net-result aggregation.
"""

from __future__ import annotations

import datetime

import pytest

from nexus_scalp.adapters.mt5.providers import (
    UNAVAILABLE,
    build_account_snapshot,
    build_deal_snapshot,
    build_history_order_snapshot,
    build_position_snapshot,
    build_rate_bar_snapshot,
)
from tests.helpers.mt5_fixtures import EXPECTED, fixture_object, fixture_objects


class _Raw:
    """Namedtuple-like stand-in shaped EXACTLY like the captured MT5 objects."""

    def __init__(self, mapping: dict) -> None:
        self.__dict__ = {k: v for k, v in mapping.items()}

    def __getitem__(self, key: str):
        return self.__dict__[key]


class TestAccountInfoMapping:
    def test_account_mapping_preserves_real_fields(self) -> None:
        raw_fields = fixture_object("account_info")
        snap = build_account_snapshot(_Raw(raw_fields))
        assert snap.available is True
        assert snap.source == "BROKER_NATIVE"
        assert snap.login == EXPECTED["account_login"]
        assert snap.balance == raw_fields["balance"]
        assert snap.equity == raw_fields["equity"]
        assert snap.currency == "USD"
        assert snap.leverage == 100
        assert snap.trade_mode == 0
        assert snap.trade_allowed is True
        assert snap.floating_pnl is not None  # equity - balance, MT5 definition

    def test_account_null_fields_stay_none(self) -> None:
        snap = build_account_snapshot(None)
        assert snap.available is False
        assert snap.source == UNAVAILABLE

    def test_account_floating_pnl_definition(self) -> None:
        raw_fields = fixture_object("account_info")
        snap = build_account_snapshot(_Raw(raw_fields))
        assert snap.floating_pnl == round(
            float(raw_fields["equity"]) - float(raw_fields["balance"]), 6
        )


class TestHistoryOrderMapping:
    def test_real_order_fields_mapped(self) -> None:
        raw = _Raw(fixture_objects("history_orders")[0])
        snap = build_history_order_snapshot(raw)
        assert snap.available is True
        assert snap.ticket == raw.ticket
        assert snap.volume_initial == raw.volume_initial
        assert snap.price_open == raw.price_open
        assert snap.sl == raw.sl
        assert snap.tp == raw.tp
        assert snap.state == raw.state
        assert snap.time_setup == raw.time_setup
        assert snap.time_done == raw.time_done
        assert snap.type == raw.type
        assert snap.comment == raw.comment

    def test_real_order_count_matches_fixture(self) -> None:
        assert len(fixture_objects("history_orders")) == EXPECTED["orders_count"]


class TestDealMapping:
    def test_real_deal_fields_mapped(self) -> None:
        raw = _Raw(fixture_objects("history_deals")[0])
        snap = build_deal_snapshot(raw)
        assert snap.available is True
        assert snap.ticket == raw["ticket"]
        assert snap.order == raw["order"]
        assert snap.position_id == raw["position_id"]
        assert snap.symbol == "XAUUSD"
        assert snap.entry == raw["entry"]
        assert snap.type == raw["type"]
        assert snap.volume == raw["volume"]
        assert snap.price == raw["price"]
        assert snap.profit == raw["profit"]
        assert snap.time == raw["time"]

    def test_deal_net_result_sign_convention(self) -> None:
        """commission/swap/fee are NEGATIVE costs in MT5: net = profit - |costs|."""
        snap = build_deal_snapshot(
            _Raw(
                {
                    "ticket": 1,
                    "order": 1,
                    "position_id": 1,
                    "symbol": "XAUUSD",
                    "type": 0,
                    "entry": 0,
                    "magic": 0,
                    "volume": 0.1,
                    "price": 100.0,
                    "profit": 50.0,
                    "fee": -1.0,
                    "swap": -2.0,
                    "commission": -3.0,
                    "comment": "",
                }
            )
        )
        assert snap.net_result == 50.0 - 3.0 - 2.0 - 1.0

    def test_real_deal_count_matches_fixture(self) -> None:
        assert len(fixture_objects("history_deals")) == EXPECTED["deals_count"]


class TestPositionAndRateMapping:
    def test_real_position_mapping(self) -> None:
        raw = _Raw(fixture_objects("positions")[0]) if fixture_objects("positions") else None
        if raw is None:
            pytest.skip("no open positions in fixture")
        snap = build_position_snapshot(raw)
        assert snap.available is True
        assert snap.ticket == raw.ticket
        assert snap.symbol == raw.symbol

    def test_real_rate_bar_mapping(self) -> None:
        raw = _Raw(fixture_objects("xauusd_m1_rates")[0])
        snap = build_rate_bar_snapshot(raw)
        assert snap.available is True
        assert snap.time_utc is not None
        assert snap.close == raw["close"]
        assert snap.open == raw["open"]

    def test_rate_bar_time_is_utc_epoch(self) -> None:
        raw = _Raw(fixture_objects("xauusd_m1_rates")[0])
        snap = build_rate_bar_snapshot(raw)
        # Broker epochs are server-local (GMT+3 on this terminal); the adapter
        # subtracts the verified offset to produce real UTC (Phase 14).
        from nexus_scalp.adapters.mt5.providers import broker_epoch_to_utc

        assert snap.time_utc == broker_epoch_to_utc(int(raw["time"]))
        assert snap.time_utc is not None
        # and the produced UTC must NOT be the raw server-local epoch interpreted
        # as UTC (the old 3h-shifted bug).
        assert snap.time_utc != datetime.datetime.fromtimestamp(int(raw["time"]), tz=datetime.UTC)
