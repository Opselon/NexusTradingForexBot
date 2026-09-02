"""
STRATEGY FACTORY — Hunter Registry Self-Consistency Invariant (ROL regression)
==============================================================================

Regression for the dead-RR-floor bug (repo HEAD 315cb36f):

_evaluate_one derives tp_dist/stop_dist from the registry entry itself
(tp_dist = stop_dist * atr_tp_mult/atr_stop_mult), so the RR_BELOW_FLOOR gate
compares a CONSTANT of the entry against that same entry's rr_floor:

  - If derived RR >= rr_floor the gate is inert (always passes).
  - If derived RR <  rr_floor the gate fires for EVERY setup -> the strategy
    can never return GO -> sample_maker (filters decision == 'GO')
    systematically excludes all of that strategy's training samples.

Two registry entries shipped in exactly that broken state:

  hunter_trend_v1: 1.2x stop / 2.0x tp -> derived 1.667 < rr_floor 1.8
  hunter_range_v1: 0.9x stop / 1.6x tp -> derived 1.778 < rr_floor 1.8

Fix: atr_tp_mult trend_v1 2.0 -> 2.4 (derived 2.0) and range_v1 1.6 -> 1.8
(derived 2.0). These tests pin the invariant for the WHOLE registry so future
edits cannot re-introduce a self-inconsistent (dead) strategy, and pin the
behavioral GO for both previously dead setups.
"""

from __future__ import annotations

import pytest

from nexus_scalp.model_generation.setup_detector import SetupDetection
from nexus_scalp.model_generation.strategy_factory import (
    HUNTER_STRATEGIES,
    StrategyFactory,
)


def _derived_rr(strategy_id: str) -> float:
    """RR the factory will actually enforce for a registry entry."""
    strat = HUNTER_STRATEGIES[strategy_id]
    return strat.atr_tp_mult / strat.atr_stop_mult


class TestRegistrySelfConsistency:
    """Every hunter entry's derived RR must satisfy its own rr_floor."""

    def test_trend_v1_registry_self_consistent(self) -> None:
        strat = HUNTER_STRATEGIES["hunter_trend_v1"]
        assert strat.atr_stop_mult == pytest.approx(1.2)
        assert strat.atr_tp_mult == pytest.approx(2.4)
        assert _derived_rr("hunter_trend_v1") >= strat.rr_floor

    def test_range_v1_registry_self_consistent(self) -> None:
        strat = HUNTER_STRATEGIES["hunter_range_v1"]
        assert strat.atr_stop_mult == pytest.approx(0.9)
        assert strat.atr_tp_mult == pytest.approx(1.8)
        assert _derived_rr("hunter_range_v1") >= strat.rr_floor

    def test_all_hunter_strategies_self_consistent(self) -> None:
        broken = [
            (sid, strat.atr_tp_mult / strat.atr_stop_mult, strat.rr_floor)
            for sid, strat in HUNTER_STRATEGIES.items()
            if (strat.atr_tp_mult / strat.atr_stop_mult) < strat.rr_floor
        ]
        assert broken == [], f"self-inconsistent (dead) hunter strategies: {broken}"


class TestDeadStrategiesNowFireGO:
    """The two previously dead strategies must return GO on clean setups."""

    def test_trend_continuation_gets_go(self) -> None:
        setup = SetupDetection(
            setup_id="s",
            setup_type="TREND_CONTINUATION",
            quality=0.95,
            factors={"direction": 1},
        )
        row = {"atr_m1": 0.5, "spread": 0.02, "regime": "TRENDING"}
        decision = StrategyFactory().evaluate(setup, row)
        assert decision.decision == "GO", f"reasons={decision.reasons}"

    def test_ranging_fade_gets_go(self) -> None:
        setup = SetupDetection(
            setup_id="s",
            setup_type="RANGING_FADE",
            quality=0.95,
            factors={"direction": -1},
        )
        row = {"atr_m1": 0.5, "spread": 0.02, "regime": "RANGING"}
        decision = StrategyFactory().evaluate(setup, row)
        assert decision.decision == "GO", f"reasons={decision.reasons}"
