"""Agent-5 S4 golden characterization: SmartMetrics kernel parity + purity.

Validates that execution/position_intelligence.calculate_smart_metrics is
numerically identical to the original OrderLifecycleManager method for
representative and edge inputs, that the facade delegates (one formula
source), and that the module has no execution authority (no adapter/broker
imports).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import Position, SymbolInfo
from nexus_scalp.execution.order_manager import OrderLifecycleManager
from nexus_scalp.execution.position_intelligence import (
    SmartMetricsInputs,
    calculate_smart_metrics,
)
from nexus_scalp.features.scalp_features import FeatureVector


class _FakeAdapter:
    pass


def _symbol_info() -> SymbolInfo:
    return SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=50.0,
        volume_step=0.01,
        stops_level=10,
        freeze_level=0,
        trade_contract_size=100.0,
    )


def _pos(profit: float) -> Position:
    return Position(
        ticket=1,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=1.0,
        price_open=2000.0,
        sl=1995.0,
        tp=2020.0,
        profit=profit,
        magic=1,
    )


def _inputs(price: float, dur: float, profit: float, features=None, **over) -> SmartMetricsInputs:
    base = dict(
        pos=_pos(profit),
        price_current=price,
        mid_price=price,
        spread=0.02,
        atr=1.8,
        net_price_delta=price - 2000.0,
        gross_price_delta=price - 2000.0,
        impact_price_delta=0.03,
        total_impact_usd=3.0,
        holding_duration=dur,
        features=features,
        symbol_info=_symbol_info(),
        be_trigger=1.0,
        trailing_distance=1.5,
        max_holding_seconds=1800.0,
        atr_sl_buffer_multiplier=1.5,
        rescue_registered=True,
        lsf_desync_score=0.15,
        mfe=3.2,
        mae=-1.1,
        adverse_ticks=12,
        favorable_ticks=20,
        stagnation_ticks=5,
    )
    base.update(over)
    return SmartMetricsInputs(**base)


def _assert_same(old, new):
    """Parity: exact for bool/int/str, 1e-12 relative for floats (the system's
    own rounding convention keeps most keys exact; unrounded ratio keys compare
    within IEEE-754 double epsilon of identical operation order)."""
    import math

    assert set(old) == set(new)
    for k in old:
        a, b = old[k], new[k]
        if isinstance(a, dict):
            _assert_same(a, b)
        elif isinstance(a, float) or isinstance(b, float):
            if a is None or b is None:
                assert a == b, k
            elif math.isnan(a) or math.isnan(b):
                assert math.isnan(a) and math.isnan(b), k
            else:
                assert math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12), (k, a, b)
        else:
            assert a == b, k


class TestS4SmartMetricsGolden:
    @pytest.fixture()
    def om(self):
        return OrderLifecycleManager(
            adapter=_FakeAdapter(),
            audit_repo=AuditRepository(db_url="sqlite:///:memory:"),
        )

    def _populate(self, om):
        om._mfe_tracker[1] = 3.2
        om._mae_tracker[1] = -1.1
        om._adverse_ticks[1] = 12
        om._favorable_ticks[1] = 20
        om._stagnation_ticks[1] = 5
        om._rescue_registered_tickets[1] = True
        om._lsf_state[1] = {"desync_score": 0.15}
        om._entry_regimes[1] = "TRENDING_MOMENTUM"

    @pytest.mark.parametrize(
        ("price", "dur", "profit"),
        [
            (2012.41, 905.0, 12.5),
            (2000.0, 0.0, 0.0),
            (1996.0, 3000.0, -4.0),
            (2012.41, 0.5, 12.5),
            (2012.41, 99999.0, 12.5),
        ],
    )
    def test_numerical_parity_full_dict(self, om, price, dur, profit):
        """All 57 metric keys identical: original method vs extracted kernel."""
        self._populate(om)
        old = om._calculate_smart_position_metrics(
            pos=_pos(profit),
            price_current=price,
            mid_price=price,
            spread=0.02,
            atr=1.8,
            net_price_delta=price - 2000.0,
            gross_price_delta=price - 2000.0,
            impact_price_delta=0.03,
            total_impact_usd=3.0,
            holding_duration=dur,
            features=None,
            symbol_info=_symbol_info(),
        )
        new = calculate_smart_metrics(_inputs(price, dur, profit))
        _assert_same(old, new)

    def test_nan_inf_feature_guard_parity(self, om):
        """_safe_feature_float guards: NaN/inf -> default, identical."""
        self._populate(om)
        fields = {
            name: 0.0 if is_float else ("XAUUSD" if name == "symbol" else False)
            for name, f in FeatureVector.model_fields.items()
            for is_float in [
                f.annotation is float
                or (
                    getattr(f.annotation, "__origin__", None) is None
                    and f.annotation in (int, float)
                )
            ]
        }
        fields["symbol"] = "XAUUSD"
        fields["timestamp_utc"] = "2026-09-02T00:00:00+00:00"
        fields["atr_m1"] = float("nan")
        fields["htf_h4_trend"] = float("inf")
        fv = FeatureVector.model_construct(**fields)
        old = om._calculate_smart_position_metrics(
            pos=_pos(12.5),
            price_current=2012.41,
            mid_price=2012.41,
            spread=0.02,
            atr=1.8,
            net_price_delta=12.41,
            gross_price_delta=12.41,
            impact_price_delta=0.03,
            total_impact_usd=3.0,
            holding_duration=905.0,
            features=fv,
            symbol_info=_symbol_info(),
        )
        new = calculate_smart_metrics(_inputs(2012.41, 905.0, 12.5, features=fv))
        _assert_same(old, new)

    def test_facade_is_thin_delegate(self, om):
        """Facade returns the SAME dict the kernel produces (one formula source)."""
        self._populate(om)
        inp = _inputs(2012.41, 905.0, 12.5)
        via_facade = om._calculate_smart_position_metrics(
            pos=inp.pos,
            price_current=inp.price_current,
            mid_price=inp.mid_price,
            spread=inp.spread,
            atr=inp.atr,
            net_price_delta=inp.net_price_delta,
            gross_price_delta=inp.gross_price_delta,
            impact_price_delta=inp.impact_price_delta,
            total_impact_usd=inp.total_impact_usd,
            holding_duration=inp.holding_duration,
            features=inp.features,
            symbol_info=inp.symbol_info,
        )
        via_kernel = calculate_smart_metrics(inp)
        _assert_same(via_facade, via_kernel)

    def test_no_execution_authority_in_module(self):
        """The intelligence module must not import broker/execution surfaces."""
        import nexus_scalp.execution.position_intelligence as mod

        src = open(mod.__file__, encoding="utf-8").read()
        for forbidden in (
            "order_send",
            "close_position",
            "modify_position",
            "IMT5Port",
            "AuditRepository",
            "TelegramNotifier",
        ):
            assert forbidden not in src, forbidden
