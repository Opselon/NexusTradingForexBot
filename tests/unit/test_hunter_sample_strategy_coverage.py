"""ROL2 regression — HunterSampleMaker default_strategy must not starve setups.

BUG (strategy): HunterSampleMaker defaulted ``default_strategy='hunter_smc_v1'``
and passed it as the StrategyFactory.evaluate() strategy_id filter, restricting
every row to the 5 SMC setup types. The 9 non-SMC setup types (CHOCH,
TREND_CONTINUATION, BREAKOUT_PULLBACK, IMPULSE, RANGING_FADE, OVERSOLD_BOUNCE,
COMPRESSION_BREAK, LONDON_BREAKOUT, NY_OPEN_SWEEP) always fell through to
NO_COMPATIBLE_STRATEGY -> NO_GO, so the GO-only sample filter systematically
excluded those training samples (strategy starvation by construction).

FIX under test: default_strategy=None makes evaluate(setup, row, None)
consider ALL compatible strategies per the documented evaluate() contract
(``strategy_id: str | None``); GO selection stays highest-RR among compatible
strategies. These tests pin that contract at the sample-maker boundary and
sweep all 14 setup types for strategy coverage.
"""

from nexus_scalp.model_generation.sample_maker import HunterSampleMaker
from nexus_scalp.model_generation.setup_detector import SETUP_TYPES, SetupDetection
from nexus_scalp.model_generation.strategy_factory import StrategyFactory

TS = "2026-09-03T10:00:00+00:00"


def test_london_breakout_not_starved() -> None:
    """End-to-end: a LONDON_BREAKOUT row reaches GO via hunter_london_v1."""
    maker = HunterSampleMaker()
    assert maker.default_strategy is None
    row = {
        "session_london": 1.0,
        "breakout_sig": 1.0,
        "norm_displacement": 1.0,
        "atr_m1": 0.5,
        "spread": 0.02,
        "regime": "RANGING",
    }
    res = maker.analyze_row(row, TS)
    assert res["decision"] == "GO"
    assert res["strategy_id"] == "hunter_london_v1"
    assert "NO_COMPATIBLE_STRATEGY" not in (res["reasons"] or ())


def test_ranging_fade_reaches_strategy_evaluation() -> None:
    """RANGING_FADE row reaches hunter_range_v1 evaluation (not NO_COMPATIBLE)."""
    maker = HunterSampleMaker()
    row = {
        "price_compression_flag_ratio": 1.0,
        "close_location_value": -1.0,
        "htf_h4_trend": 0.0,
        "atr_m1": 0.5,
        "spread": 0.02,
        "regime": "RANGING",
    }
    res = maker.analyze_row(row, TS)
    assert res["setup_type"] == "RANGING_FADE"
    assert res["decision"] == "GO"
    assert res["strategy_id"] == "hunter_range_v1"
    assert "NO_COMPATIBLE_STRATEGY" not in (res["reasons"] or ())


def test_all_14_setup_types_reach_a_compatible_strategy() -> None:
    """With strategy_id=None, no setup type may yield NO_COMPATIBLE_STRATEGY.

    Sweeps the full SETUP_TYPES registry with a minimal synthetic detection and
    asserts every type maps to at least one compatible strategy in the factory.
    """
    factory = StrategyFactory()
    row = {
        "atr_m1": 0.5,
        "spread": 0.02,
        "regime": "TRENDING",
        "session_london": 1.0,
    }
    assert len(SETUP_TYPES) == 14
    for setup_type in SETUP_TYPES:
        setup = SetupDetection(
            setup_id=f"test_{setup_type.lower()}",
            setup_type=setup_type,
            quality=0.95,
            factors={"direction": 1},
        )
        decision = factory.evaluate(setup, row, None)
        assert decision.decision in {"GO", "NO_GO"}
        assert "NO_COMPATIBLE_STRATEGY" not in (decision.reasons or ())
        # Regression hardening (BUG-226 review): a NO_GO for a clean
        # quality-0.95 setup under a REGIME-MATCHED row must be caused
        # ONLY by regime/session gates - never by RR/quality floors
        # (those would re-introduce dead strategies).
        forbidden = {
            r
            for r in (decision.reasons or ())
            if r.startswith("RR_BELOW_FLOOR")
            or r.startswith("QUALITY_BELOW_FLOOR")
            or r == "NO_DIRECTION_ALIGNMENT"
        }
        assert not forbidden, (setup_type, forbidden)
        assert decision.strategy_id != "hunter_smc_v1" or setup_type in (
            "ORDER_BLOCK",
            "FVG",
            "BREAK_OF_STRUCTURE",
            "LIQUIDITY_SWEEP",
            "OTE_PULLBACK",
        )
