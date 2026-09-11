"""BUG-260 regression battery (Agent-15 — Risk & Capital Protection Core).

PROVEN DEFECT: RiskConfig.max_margin_usage_pct was stored on the engine and
hot-reloaded from the runtime snapshot, but NEVER ENFORCED — the sizing
margin clamp used a hard-coded 20% of free margin. An operator setting the
UI value to 1% believed 1% was enforced; the engine happily used up to 20%.

FIX under test: the effective clamp is min(0.20 hard safety clamp,
max_margin_usage_pct / 100). The hard clamp stays the upper safety boundary;
an invalid configured value (non-finite / <=0 / >100 / wrong type) is
refused and the hard clamp governs — a broken config can only tighten or
preserve the boundary, never loosen it.
"""

from __future__ import annotations

import pytest

from nexus_scalp.configuration.config import RiskConfig
from nexus_scalp.domain.models import AccountInfo, SymbolInfo
from nexus_scalp.risk.risk_engine import RiskEngine


def _account(margin_free: float = 100_000.0) -> AccountInfo:
    return AccountInfo(
        login=1,
        trade_mode=0,
        leverage=100,
        balance=100_000.0,
        equity=100_000.0,
        margin=0.0,
        margin_free=margin_free,
        currency="USD",
    )


def _symbol_info() -> SymbolInfo:
    return SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=10,
        freeze_level=0,
        trade_contract_size=100.0,
    )


def _sized_volume(engine: RiskEngine) -> float:
    # 1% of 100k equity risked over a 0.5 SL distance demands ~20 lots —
    # far above every margin clamp, so the MARGIN CLAMP is the binding
    # constraint and the sized volume directly reveals the clamp fraction.
    vol, _reason = engine.calculate_dynamic_volume(
        entry=2000.0,
        sl=1999.5,
        account=_account(),
        symbol_info=_symbol_info(),
        risk_pct=1.0,
    )
    return vol


def _margin_used_pct(volume: float) -> float:
    return (100 * 2000.0 * volume) / 100 / 100_000.0 * 100.0


class TestMarginUsageEnforcement:
    def test_configured_1pct_is_enforced(self) -> None:
        engine = RiskEngine(RiskConfig(max_margin_usage_pct=1.0), max_margin_usage_pct=1.0)
        vol = _sized_volume(engine)
        assert _margin_used_pct(vol) <= 1.0 + 1e-9
        assert vol == pytest.approx(0.5)  # 1% of 100k free margin @1:100

    def test_configured_10pct_default_is_enforced(self) -> None:
        engine = RiskEngine(RiskConfig(max_margin_usage_pct=10.0), max_margin_usage_pct=10.0)
        vol = _sized_volume(engine)
        assert _margin_used_pct(vol) <= 10.0 + 1e-9
        assert vol == pytest.approx(5.0)

    def test_hard_clamp_remains_upper_boundary(self) -> None:
        """A configured 50% may NOT exceed the 20% hard safety clamp."""
        engine = RiskEngine(RiskConfig(max_margin_usage_pct=50.0), max_margin_usage_pct=50.0)
        vol = _sized_volume(engine)
        assert _margin_used_pct(vol) <= 20.0 + 1e-9
        assert vol == pytest.approx(10.0)

    @pytest.mark.parametrize("bad", [0.0, -5.0, 150.0, float("nan"), float("inf")])
    def test_invalid_config_falls_back_to_hard_clamp(self, bad: float) -> None:
        """An invalid configured value is refused: the hard clamp governs.
        A broken config can never loosen the boundary."""
        engine = RiskEngine(RiskConfig(max_margin_usage_pct=10.0), max_margin_usage_pct=bad)
        vol = _sized_volume(engine)
        assert _margin_used_pct(vol) <= 20.0 + 1e-9
        assert vol == pytest.approx(10.0)

    def test_hot_reloaded_value_takes_effect(self) -> None:
        """The runtime-config sync writes engine.max_margin_usage_pct — the
        clamp must honor the NEW value on the next sizing call."""
        engine = RiskEngine(RiskConfig(max_margin_usage_pct=10.0), max_margin_usage_pct=10.0)
        assert _sized_volume(engine) == pytest.approx(5.0)
        engine.max_margin_usage_pct = 2.0  # hot-reload path
        vol = _sized_volume(engine)
        assert _margin_used_pct(vol) <= 2.0 + 1e-9
        assert vol == pytest.approx(1.0)
