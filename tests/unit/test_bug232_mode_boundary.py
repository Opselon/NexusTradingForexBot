"""
BUG-232 regression tests: hard PAPER/LIVE boundary + mode persistence.

Production failure (2026-09-03 18:48 +03:30): the launcher bound
PaperMT5Adapter from the YAML-PAPER default while the engine re-bound
execution.mode to the persisted LIVE value from the settings DB. The old
align_adapter_to_boot_mode only corrected PAPER<-real boots, so the paper
simulator (seed 2000.00, login 9990001) ran under a LIVE badge, every UI
panel showed the stale seed price, and paper-geometry proposals reached the
real broker (BUG-231).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.domain.enums import ExecutionMode
from nexus_scalp.domain.models import AccountInfo

# ---------------------------------------------------------------------------
# FIX-3: paper seed plausibility
# ---------------------------------------------------------------------------


def test_paper_seed_xauusd_is_plausible_not_2000():
    adapter = PaperMT5Adapter(symbol="XAUUSD")
    assert adapter._current_price == pytest.approx(4400.00)


def test_paper_seed_env_override():
    with patch.dict("os.environ", {"NEXUS_PAPER_SEED_XAUUSD": "4430.55"}):
        adapter = PaperMT5Adapter(symbol="XAUUSD")
    assert adapter._current_price == pytest.approx(4430.55)


def test_paper_seed_unknown_instrument_falls_back():
    adapter = PaperMT5Adapter(symbol="XYZPAIR")
    assert adapter._current_price == pytest.approx(2000.00)


def test_paper_ticks_move_and_mean_revert():
    adapter = PaperMT5Adapter(symbol="XAUUSD")
    prices = [adapter.get_last_tick("XAUUSD").bid for _ in range(200)]
    assert len(set(prices)) > 10, "paper market is frozen (no movement)"
    seed = PaperMT5Adapter._SEED_BASELINES["XAUUSD"]
    assert all(p > seed * 0.90 for p in prices), "walk drifted unboundedly low"
    assert all(p < seed * 1.10 for p in prices), "walk drifted unboundedly high"


def test_paper_tick_spread_is_positive():
    adapter = PaperMT5Adapter(symbol="XAUUSD")
    for _ in range(20):
        t = adapter.get_last_tick("XAUUSD")
        assert t.ask > t.bid


# ---------------------------------------------------------------------------
# FIX-2: LIVE boot must never keep the paper adapter
# ---------------------------------------------------------------------------


class _FakeSettingsDb:
    def __init__(self, mapping: dict[str, object] | None = None) -> None:
        self._mapping = mapping or {}

    def get(self, key: str):
        if key not in self._mapping:
            return None

        class _Row:
            def __init__(self, value: object) -> None:
                self.value = value

        return _Row(self._mapping[key])


def _build_engine(persisted_mode: str, bound_paper: bool) -> LiveEngine:
    """Constructs a real LiveEngine with a paper adapter bound, then runs the
    BUG-232 alignment exactly as __init__ would (settings DB says LIVE)."""
    cfg_rows = {"execution.mode": persisted_mode}
    with (
        patch("nexus_scalp.application.live_engine.load_settings_service") as _lss,
        patch("nexus_scalp.adapters.mt5.mt5_adapter.DirectMT5Adapter") as _direct,
    ):
        _lss.return_value.db = _FakeSettingsDb(cfg_rows)
        # avoid importing MetaTrader5 in tests
        _direct.return_value = object()
        engine = LiveEngine.__new__(LiveEngine)
        # Minimal attributes required by align_adapter_to_boot_mode
        from nexus_scalp.configuration.config import AppConfig

        engine.config = AppConfig.model_validate(
            {
                "execution": {"symbol": "XAUUSD", "mode": "PAPER"},
                "model": {"model_artifact_path": "artifacts/m.pt"},
            }
        )
        paper = PaperMT5Adapter(symbol="XAUUSD")
        if bound_paper:
            result = engine.align_adapter_to_boot_mode(paper, ExecutionMode(persisted_mode))
        else:  # pragma: no cover - symmetric case helper
            result = paper
    return engine, result  # type: ignore[return-value]


def test_align_live_boot_replaces_paper_adapter():
    """THE BUG-232 regression: LIVE effective mode + paper adapter -> real adapter."""
    engine, result = _build_engine("LIVE", bound_paper=True)
    assert not isinstance(result, PaperMT5Adapter), (
        "LIVE boot kept the paper simulation adapter (BUG-232 unfixed)"
    )


def test_align_paper_boot_keeps_paper_adapter():
    engine, result = _build_engine("PAPER", bound_paper=True)
    assert isinstance(result, PaperMT5Adapter)


def test_session_generation_inits_at_zero():
    engine, _ = _build_engine("PAPER", bound_paper=True)
    assert getattr(engine, "_mode_session_generation", 0) == 0


def test_hot_swap_invalidates_cross_mode_state():
    """PAPER->LIVE hot-swap bumps the session generation and clears the
    signal-policy paper-geometry caches (BUG-231's upstream cause)."""
    engine, _ = _build_engine("PAPER", bound_paper=True)
    engine._mode_session_generation = 3

    class _Policy:
        last_order_price = 2000.08
        last_order_time = "paper-time"
        _last_active_direction = "SELL"
        _last_active_direction_time = "paper-time"
        _last_executed_price = 2000.08

    engine.signal_policy = _Policy()
    engine._last_tick_processed_time = 0.0

    # Seed the aggregator with a paper-geometry bar (the 2000-era poison the
    # BUG-231 incident proved crosses the boundary).
    from datetime import UTC, datetime, timedelta

    from nexus_scalp.market_data.bar_aggregator import BarAggregator, BarData

    engine.aggregator = BarAggregator(symbol="XAUUSD", timeframe_minutes=1)
    poison_bars = [
        BarData(
            symbol="XAUUSD",
            timeframe="M1",
            timestamp=datetime.now(UTC) - timedelta(minutes=n + 1),
            open=2000.0,
            high=2001.0,
            low=1999.0,
            close=2000.5,
            tick_volume=10,
            is_complete=True,
        )
        for n in range(5)
    ]
    engine.aggregator.reseed(poison_bars)
    assert len(engine.aggregator.get_completed_bars()) == 5
    engine.warmup_state = "READY"

    engine._invalidate_cross_mode_state(ExecutionMode.PAPER, ExecutionMode.LIVE)

    assert engine._mode_session_generation == 4
    assert engine.signal_policy.last_order_price is None
    assert engine.signal_policy._last_executed_price == 0.0
    assert engine._last_tick_processed_time > 0.0
    # BUG-231 continuation: paper bars + warmup state must NOT survive.
    assert engine.aggregator.get_completed_bars() == []
    assert engine.warmup_state == "WARMING_UP"


def test_hot_swap_purge_failure_is_isolated():
    """A broken aggregator must never break the swap (isolation contract)."""
    engine, _ = _build_engine("PAPER", bound_paper=True)

    class _Boom:
        def reseed(self, _bars):
            raise RuntimeError("simulated broker reseed failure")

    engine.aggregator = _Boom()
    engine.warmup_state = "READY"
    # Must not raise.
    engine._invalidate_cross_mode_state(ExecutionMode.PAPER, ExecutionMode.LIVE)
    assert engine._mode_session_generation == 1


def test_hot_swap_same_boundary_class_keeps_generation():
    """PAPER<->SHADOW swaps share the simulation boundary: no invalidation."""
    engine, _ = _build_engine("PAPER", bound_paper=True)
    engine._mode_session_generation = 7
    engine._invalidate_cross_mode_state(ExecutionMode.PAPER, ExecutionMode.SHADOW)
    assert engine._mode_session_generation == 7
