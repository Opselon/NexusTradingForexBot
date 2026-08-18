"""PHASE 15D — Hunter Setup/Strategy/Sample Maker unit tests."""

from __future__ import annotations

import polars as pl
import pytest

from nexus_scalp.model_generation.sample_maker import (
    HunterSampleMaker,
    attach_hunter_metadata,
    quality_tier,
)
from nexus_scalp.model_generation.setup_detector import (
    HUNTER_MIN_QUALITY,
    SETUP_TYPES,
    SetupDetection,
    SetupDetector,
    validate_setup_type,
)
from nexus_scalp.model_generation.strategy_factory import (
    DEFAULT_HUNTER_STRATEGY,
    HUNTER_STRATEGIES,
    EntryDecision,
    HunterStrategy,
    StrategyFactory,
    best_strategy_for,
    get_strategy,
)


def _row(**over) -> dict:
    row = {
        "timestamp": "2026-08-17T10:00:00+00:00",
        "close": 4400.0,
        "high": 4402.0,
        "low": 4398.0,
        "atr_m1": 1.5,
        "spread": 0.20,
        "regime": "TRENDING",
        "session_london": 0.0,
        "session_ny": 0.0,
        "feat_ob_liquidity_swept": 0.0,
        "liquidity_sweep_signal": 0.0,
        "stop_hunt_depth": 0.0,
        "feat_ob_valid_bos": 0.0,
        "breakout_sig": 0.0,
        "norm_displacement": 0.0,
        "fvg_sig": 0.0,
        "choch_sig": 0.0,
        "order_block_type": 0.0,
        "feat_ob_equilibrium_ratio": 0.0,
        "feat_ob_fib_50_60_alignment": 0.0,
        "htf_h4_trend": 0.5,
        "htf_h1_momentum": 0.5,
        "consecutive_momentum_count": 0.0,
        "dist_to_ema_21": 0.1,
        "price_compression_flag_ratio": 0.0,
        "close_location_value": 0.0,
        "norm_rsi": 0.0,
        "lower_wick_ratio": 0.0,
        "pinbar_sig": 0.0,
        "lag_1_volume_z": 0.0,
        "dist_to_swing_high_20": 0.2,
    }
    row.update(over)
    return row


class TestSetupDetector:
    def test_no_setup_on_flat_row(self):
        dets = SetupDetector().detect(_row())
        assert dets == [] or all(d.quality < HUNTER_MIN_QUALITY for d in dets)

    def test_liquidity_sweep_detected(self):
        dets = SetupDetector().detect(
            _row(
                feat_ob_liquidity_swept=1.0,
                liquidity_sweep_signal=0.8,
                stop_hunt_depth=1.2,
            )
        )
        assert any(d.setup_type == "LIQUIDITY_SWEEP" for d in dets)

    def test_order_block_detected(self):
        dets = SetupDetector().detect(
            _row(order_block_type=1.0, feat_ob_valid_bos=0.9, feat_ob_equilibrium_ratio=0.5)
        )
        assert any(d.setup_type == "ORDER_BLOCK" for d in dets)

    def test_fvg_detected(self):
        dets = SetupDetector().detect(_row(fvg_sig=1.0, close_location_value=0.7))
        assert any(d.setup_type == "FVG" for d in dets)

    def test_london_breakout_session_gated(self):
        dets = SetupDetector().detect(
            _row(session_london=1.0, breakout_sig=1.0, norm_displacement=0.9)
        )
        assert any(d.setup_type == "LONDON_BREAKOUT" for d in dets)
        # no London session -> no London setup
        dets2 = SetupDetector().detect(_row(breakout_sig=1.0, norm_displacement=0.9))
        assert not any(d.setup_type == "LONDON_BREAKOUT" for d in dets2)

    def test_quality_bounds(self):
        dets = SetupDetector().detect(
            _row(
                feat_ob_liquidity_swept=2.0,
                liquidity_sweep_signal=-0.9,
                stop_hunt_depth=3.0,
            )
        )
        for d in dets:
            assert 0.0 <= d.quality <= 1.0

    def test_setup_type_registry(self):
        assert len(SETUP_TYPES) >= 12
        assert all(validate_setup_type(t) for t in SETUP_TYPES)

    def test_deterministic_ids(self):
        r1 = _row(feat_ob_valid_bos=1.0, norm_displacement=0.8)
        d1 = SetupDetector().detect(r1)
        d2 = SetupDetector().detect(r1)
        assert [s.setup_id for s in d1] == [s.setup_id for s in d2]


class TestStrategyFactory:
    def test_go_on_qualified_sweep(self):
        row = _row(
            feat_ob_liquidity_swept=1.0,
            liquidity_sweep_signal=0.8,
            stop_hunt_depth=1.2,
        )
        dets = SetupDetector().detect(row)
        sweep = next(d for d in dets if d.setup_type == "LIQUIDITY_SWEEP")
        dec = StrategyFactory().evaluate(sweep, row, "hunter_sweep_v1")
        assert dec.decision == "GO"
        assert dec.stop_distance is not None and dec.stop_distance > 0
        assert dec.tp_distance is not None and dec.tp_distance > dec.stop_distance

    def test_no_go_on_spread_wide(self):
        row = _row(
            feat_ob_liquidity_swept=1.0,
            liquidity_sweep_signal=0.8,
            stop_hunt_depth=1.2,
            spread=1.0,  # spread/atr = 0.67 > 0.30 cap
        )
        dets = SetupDetector().detect(row)
        sweep = next(d for d in dets if d.setup_type == "LIQUIDITY_SWEEP")
        dec = StrategyFactory().evaluate(sweep, row, "hunter_sweep_v1")
        assert dec.decision == "NO_GO"
        assert any("SPREAD_TOO_WIDE" in r for r in dec.reasons)

    def test_no_go_on_regime(self):
        row = _row(
            feat_ob_liquidity_swept=1.0,
            liquidity_sweep_signal=0.8,
            stop_hunt_depth=1.2,
            regime="RANGING",
        )
        dets = SetupDetector().detect(row)
        sweep = next(d for d in dets if d.setup_type == "LIQUIDITY_SWEEP")
        dec = StrategyFactory().evaluate(sweep, row, "hunter_range_v1")  # wrong strategy
        assert dec.decision == "NO_GO"

    def test_registry_integrity(self):
        assert len(HUNTER_STRATEGIES) >= 12
        for _sid, strat in HUNTER_STRATEGIES.items():
            assert strat.setup_types
            assert strat.min_quality >= 0.5
            assert strat.rr_floor >= 1.5
            assert strat.atr_tp_mult > strat.atr_stop_mult

    def test_get_and_best(self):
        assert get_strategy("hunter_smc_v1").strategy_id == "hunter_smc_v1"
        with pytest.raises(KeyError):
            get_strategy("nope")
        dets = SetupDetector().detect(
            _row(feat_ob_valid_bos=1.0, norm_displacement=0.9, breakout_sig=0.8)
        )
        if dets:
            s = best_strategy_for(dets[0])
            assert s in HUNTER_STRATEGIES

    def test_decision_contract(self):
        d = EntryDecision(
            strategy_id="x",
            setup=SetupDetection(setup_id="s", setup_type="T", quality=0.7),
            decision="GO",
        )
        c = d.to_contract()
        assert c["decision"] == "GO"
        assert c["strategy_id"] == "x"


class TestHunterSampleMaker:
    def test_tier_mapping(self):
        assert quality_tier(0.85) == "TIER_A"
        assert quality_tier(0.75) == "TIER_B"
        assert quality_tier(0.60) == "TIER_C"
        assert quality_tier(0.30) == "NO_TRADE"

    def test_relative_tier_mapping(self):
        # percentile: 0 = worst, 1 = elite -> top 10% = TIER_A, next 25% = TIER_B
        assert quality_tier(0.9, percentile=0.95) == "TIER_A"
        assert quality_tier(0.9, percentile=0.80) == "TIER_B"
        assert quality_tier(0.9, percentile=0.20) == "TIER_C"
        assert quality_tier(0.9, percentile=0.0) == "TIER_C"

    def test_analyze_row_relative_reference(self):
        # build a reference distribution, then a top-quality row should be TIER_A
        ref = [0.56, 0.57, 0.58, 0.60, 0.62, 0.64, 0.66, 0.68, 0.70, 0.72, 0.75, 0.78]
        h = HunterSampleMaker().analyze_row(
            _row(
                feat_ob_liquidity_swept=1.0,
                liquidity_sweep_signal=0.95,
                stop_hunt_depth=2.0,
            ),
            quality_reference=ref,
        )
        assert h["tier"] in ("TIER_A", "TIER_B")

    def test_analyze_row_no_setup(self):
        h = HunterSampleMaker().analyze_row(_row())
        assert h["tier"] == "NO_TRADE"
        assert h["decision"] == "NO_GO"

    def test_analyze_row_qualified(self):
        h = HunterSampleMaker().analyze_row(
            _row(
                feat_ob_liquidity_swept=1.0,
                liquidity_sweep_signal=0.9,
                stop_hunt_depth=1.4,
            )
        )
        assert h["setup_type"] == "LIQUIDITY_SWEEP"
        assert h["tier"] in ("TIER_A", "TIER_B", "TIER_C")
        assert h["decision"] in ("GO", "NO_GO")

    def test_attach_metadata(self):
        meta = attach_hunter_metadata(
            {"metadata": {}},
            {
                "setup_type": "FVG",
                "quality": 0.8,
                "tier": "TIER_A",
                "strategy_id": "hunter_fvg_v1",
                "decision": "GO",
                "direction": "BUY",
                "stop_distance": 1.0,
                "tp_distance": 2.0,
                "reasons": ("HUNTER_QUALIFIED",),
            },
        )
        assert meta["setup_type"] == "FVG"
        assert meta["setup_tier"] == "TIER_A"
        assert meta["entry_decision"] == "GO"

    def test_build_hunter_frame(self):
        df = pl.DataFrame(
            [
                {
                    **_row(
                        **{
                            "feat_ob_liquidity_swept": 1.0,
                            "liquidity_sweep_signal": 0.8,
                            "stop_hunt_depth": 1.2,
                        }
                    )
                },
                {**_row()},
            ]
        )
        hf = HunterSampleMaker().build_hunter_frame(df)
        assert hf.height == 2
        assert "setup_tier" in hf.columns
        assert "entry_decision" in hf.columns
        assert "setup_quality" in hf.columns

    def test_hunter_gate(self):
        df = pl.DataFrame(
            [
                {
                    **_row(
                        **{
                            "feat_ob_liquidity_swept": 1.0,
                            "liquidity_sweep_signal": 0.9,
                            "stop_hunt_depth": 1.5,
                        }
                    )
                },
                {**_row()},
            ]
        )
        hf = HunterSampleMaker().build_hunter_frame(df)
        gated = HunterSampleMaker().hunter_gate_frame(hf)
        assert gated.height <= hf.height
        assert all(r["entry_decision"] == "GO" for r in gated.to_dicts()) or gated.height == 0
