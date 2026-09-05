"""Agent-5 regression suite: BUG-249 (spread/ATR gate), BUG-251 (reversal confidence semantics), BUG-252 (peak_equity drawdown)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
import torch

from nexus_scalp.configuration.config import RiskConfig
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import AccountInfo, SymbolInfo, TickData, TradeProposal
from nexus_scalp.features.regime_classifier import (
    MarketRegimeState,
    RecommendedExecutionType,
    RegimeReason,
    RegimeType,
)
from nexus_scalp.risk.risk_engine import RiskEngine
from nexus_scalp.signals.policy import SignalPolicy
from tests.unit.test_policy import _make_feature_vector, _make_tick

_TICK_SEQ = [0]


def _fresh_policy(threshold: float = 0.20) -> SignalPolicy:
    p = SignalPolicy(confidence_threshold=threshold)
    p._last_signal_time = None
    p._dedup_last_time = None
    p._dedup_last_bid = 0.0
    p._dedup_last_ask = 0.0
    return p


def _fresh_tick(spread: float = 0.20, price: float = 2000.0) -> TickData:
    _TICK_SEQ[0] += 1
    base = _make_tick()
    return base.model_copy(
        update={
            "timestamp": base.timestamp + timedelta(seconds=_TICK_SEQ[0]),
            "bid": price,
            "ask": price + spread,
        }
    )


def _regime() -> MarketRegimeState:
    return MarketRegimeState(
        symbol="XAUUSD",
        timestamp_utc=datetime.now(UTC).isoformat(),
        regime_type=RegimeType.TRENDING_MOMENTUM,
        regime_probability=0.85,
        recommended_execution_type=RecommendedExecutionType.HYBRID_LIMIT_STOP,
        order_flow_imbalance=0.10,
        tick_velocity_per_sec=2.0,
        current_spread_usd=0.20,
        realized_volatility_5m=0.01,
        reason=RegimeReason.OFI_TREND_ALIGN,
        is_macro_news_active=False,
    )


def _buy_candidate_fv(atr: float = 1.5) -> object:
    """A BUY candidate that passes every other gate (sweep + choch + large swings)."""
    return _make_feature_vector().model_copy(
        update={
            "is_above_kumo": True,
            "tenkan_sen": 2005.0,
            "kijun_sen": 2003.0,
            "live_tick_displacement": 0.9,
            "choch_bullish": True,
            "liquidity_sweep_signal": 1,
            "dist_to_swing_low_20": 5.0,
            "dist_to_swing_high_20": 5.0,
            "atr_m1": atr,
        }
    )


_PROBS_STRONG_BUY = [0.20, 0.55, 0.15, 0.05]


class TestBug249SpreadAtrGate:
    def test_wide_spread_blocks_candidate(self) -> None:
        """spread 0.90 vs ATR 1.5 -> ratio 0.60 > 0.18 -> SPREAD_ATR_GATE block."""
        policy = _fresh_policy()
        wide = _fresh_tick(spread=0.90)
        proposal = policy.evaluate_probabilities(
            torch.tensor([_PROBS_STRONG_BUY], dtype=torch.float32),
            wide,
            _buy_candidate_fv(),
            regime_state=_regime(),
        )
        assert proposal.action == ActionType.NO_TRADE
        assert proposal.decision_stage == "SPREAD_ATR_GATE"
        assert proposal.blocked_by == "SPREAD_ATR_RATIO"
        assert "SPREAD_ATR_RATIO_EXCEEDED" in proposal.reason_code
        ratio = proposal.risk_checks["spread_atr_ratio"]
        assert ratio == pytest.approx(0.60, abs=0.01)

    def test_narrow_spread_passes_gate(self) -> None:
        """spread 0.20 vs ATR 1.5 -> ratio 0.133 <= 0.18 -> candidate proceeds."""
        policy = _fresh_policy()
        narrow = _fresh_tick(spread=0.20)
        proposal = policy.evaluate_probabilities(
            torch.tensor([_PROBS_STRONG_BUY], dtype=torch.float32),
            narrow,
            _buy_candidate_fv(),
            regime_state=_regime(),
        )
        assert proposal.decision_stage == "FINAL_DECISION"
        assert proposal.action == ActionType.BUY_MARKET

    def test_zero_spread_never_blocks(self) -> None:
        """Degenerate zero spread never hits the SPREAD_ATR_GATE itself: the
        zero-spread candidate may still be blocked by the downstream
        asymmetric-RR gate (entry==ask with flat swing geometry), which is
        independent of the BUG-249 guard under test."""
        policy = _fresh_policy()
        flat = _fresh_tick(spread=0.0)
        proposal = policy.evaluate_probabilities(
            torch.tensor([_PROBS_STRONG_BUY], dtype=torch.float32),
            flat,
            _buy_candidate_fv(),
            regime_state=_regime(),
        )
        # The BUG-249 gate itself must not fire: reason must not be
        # SPREAD_ATR_RATIO_EXCEEDED and blocked_by must not be SPREAD_ATR_RATIO.
        assert proposal.blocked_by != "SPREAD_ATR_RATIO"
        assert "SPREAD_ATR_RATIO_EXCEEDED" not in proposal.reason_code


class TestBug251ReversalConfidenceSemantics:
    def test_large_no_trade_mass_suppresses_false_reversal(self) -> None:
        """sell=0.30 / buy=0.20 / no_trade=0.40: trained bias 0.333 < 0.60 -> no
        reversal (old raw-bias 0.60 would have fired spuriously)."""
        policy = _fresh_policy()
        fv = _make_feature_vector().model_copy(
            update={"is_below_kumo": True, "choch_bearish": True}
        )
        proposal = policy._evaluate_ai_reversal(
            current_tick=_fresh_tick(),
            feature_vector=fv,
            held_position_dirs={1: "BUY"},
            prob_buy=0.20,
            prob_sell=0.30,
            no_trade_prob=0.40,
            atr=1.5,
            regime_str="UNKNOWN",
            regime_conf=0.5,
        )
        assert proposal is None

    def test_genuine_conviction_still_reverses_normalized(self) -> None:
        """sell=0.55 / buy=0.10 / no_trade=0.10: trained bias 0.733 >= 0.60 ->
        reversal fires with NORMALIZED confidence 0.733 (not raw 0.55)."""
        policy = _fresh_policy()
        fv = _make_feature_vector().model_copy(
            update={"is_below_kumo": True, "choch_bearish": True}
        )
        proposal = policy._evaluate_ai_reversal(
            current_tick=_fresh_tick(),
            feature_vector=fv,
            held_position_dirs={1: "BUY"},
            prob_buy=0.10,
            prob_sell=0.55,
            no_trade_prob=0.10,
            atr=1.5,
            regime_str="UNKNOWN",
            regime_conf=0.5,
        )
        assert proposal is not None
        assert proposal.action == ActionType.CLOSE_POSITION
        assert proposal.confidence == pytest.approx(0.55 / 0.75, abs=0.01)

    def test_degenerate_mass_falls_back_to_raw_share(self) -> None:
        """All-zero mass: fall back to raw directional share, never crash."""
        policy = _fresh_policy()
        fv = _make_feature_vector().model_copy(
            update={"is_below_kumo": True, "choch_bearish": True}
        )
        proposal = policy._evaluate_ai_reversal(
            current_tick=_fresh_tick(),
            feature_vector=fv,
            held_position_dirs={1: "BUY"},
            prob_buy=0.0,
            prob_sell=0.0,
            no_trade_prob=0.0,
            atr=1.5,
        )
        # No conviction either way, must not crash.
        assert proposal is None


class TestBug252PeakEquityDrawdownPenalty:
    def _account(self) -> AccountInfo:
        return AccountInfo(
            login=1,
            trade_mode=0,
            leverage=100,
            balance=10000,
            equity=8000,
            margin=0,
            margin_free=8000,
        )

    def _symbol(self) -> SymbolInfo:
        return SymbolInfo(
            symbol="XAUUSD",
            digits=2,
            point=0.01,
            tick_size=0.01,
            tick_value=1.0,
            volume_min=0.01,
            volume_max=10,
            volume_step=0.01,
            stops_level=10,
            freeze_level=0,
            trade_contract_size=100,
        )

    def _proposal(self) -> TradeProposal:
        return TradeProposal(
            request_id="r",
            symbol="XAUUSD",
            generated_at=datetime.now(UTC),
            action=ActionType.BUY_MARKET,
            confidence=0.6,
            proposed_entry=2000.0,
            stop_loss=1998.0,
            take_profit=2004.0,
            risk_reward_ratio=2.0,
        )

    def _tick(self) -> TickData:
        return TickData(
            symbol="XAUUSD",
            timestamp=datetime.now(UTC),
            bid=2000.0,
            ask=2000.20,
            volume=1.0,
        )

    def test_no_peak_no_penalty(self) -> None:
        """peak_equity=None -> honest fallback (peak == equity), no DD cut."""
        engine = RiskEngine(RiskConfig(risk_per_trade_pct=0.5))
        order = engine.evaluate_proposal(
            self._proposal(),
            self._account(),
            self._symbol(),
            [],
            self._tick(),
            pending_orders=[],
            peak_equity=None,
        )
        assert order is not None
        baseline_volume = order.volume

        engine2 = RiskEngine(RiskConfig(risk_per_trade_pct=0.5))
        order_dd = engine2.evaluate_proposal(
            self._proposal(),
            self._account(),
            self._symbol(),
            [],
            self._tick(),
            pending_orders=[],
            peak_equity=12000.0,  # 33.3% drawdown -> penalty max(0.2, 1-6.67)=0.2
        )
        assert order_dd is not None
        # 33% DD cuts risk 80% (penalty 0.2) -> volume should be ~0.2x baseline.
        assert order_dd.volume < baseline_volume * 0.35
        assert baseline_volume > 0.0
