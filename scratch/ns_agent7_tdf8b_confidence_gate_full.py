"""Agent 7 — TDF-8b: full channel pass-through with zone-quality satisfied.

TDF-8b reruns the boundary walk with ai_zone_confidence_threshold=0.30 so the
ZONE_QUALITY_GATE does not intercept the boundary cases. This isolates the
CONFIDENCE_GATE itself (base 0.35) — the documented 0.35 gate.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")

import torch


def make_bars(n: int, start: float = 2400.0, seed: int = 42) -> list:
    from nexus_scalp.market_data.bar_aggregator import BarData

    bars = []
    px = start
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    for i in range(n):
        seed = (seed * 1103515245 + 12345) % (2**31)
        delta = ((seed % 1000) - 500) / 5000.0
        o = px
        c = px + delta
        bars.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=t0 + timedelta(minutes=i),
                open=o,
                high=max(o, c) + abs(delta) * 0.6 + 0.05,
                low=min(o, c) - abs(delta) * 0.6 - 0.05,
                close=c,
                tick_volume=100 + seed % 40,
                is_complete=True,
            )
        )
        px = c
    return bars


class _Acc:
    equity = 5000.0
    margin_free = 4000.0
    leverage = 100
    balance = 5000.0
    peak_equity = 5000.0


class _Sym:
    symbol = "XAUUSD"
    digits = 2
    point = 0.01
    tick_size = 0.01
    tick_value = 1.0
    volume_min = 0.01
    volume_max = 100.0
    volume_step = 0.01
    stops_level = 0
    freeze_level = 0
    trade_contract_size = 100.0


class _AlgoCfg:
    """Duck-typed algo config with the zone threshold relaxed."""

    def __init__(self):
        from nexus_scalp.configuration.config import AlgoConfig

        real = AlgoConfig()
        for f in real.model_fields:
            try:
                setattr(self, f, getattr(real, f))
            except Exception:
                pass
        self.ai_zone_confidence_threshold = 0.30


class _RegimeState:
    def __init__(self, ts):
        from nexus_scalp.features.regime_classifier import (
            MarketRegimeState,
            RegimeReason,
            RegimeType,
            RecommendedExecutionType,
        )

        self._s = MarketRegimeState(
            symbol="XAUUSD",
            timestamp_utc=ts.isoformat(),
            regime_type=RegimeType.TRENDING_MOMENTUM,
            regime_probability=0.9,
            order_flow_imbalance=0.3,
            realized_volatility_5m=0.0002,
            tick_velocity_per_sec=2.0,
            current_spread_usd=0.06,
            is_macro_news_active=False,
            recommended_execution_type=RecommendedExecutionType.IOC_MARKET,
            reason=RegimeReason.OFI_TREND_ALIGN,
        )

    def __getattr__(self, name):
        return getattr(self._s, name)


def main() -> int:
    from nexus_scalp.domain.models import TickData
    from nexus_scalp.features.scalp_features import ScalpFeatureEngine
    from nexus_scalp.signals.policy import SignalPolicy

    print("=== TDF-8b: CONFIDENCE_GATE boundary (zone gate relaxed) ===")
    bars = make_bars(600)
    tick = TickData(
        symbol="XAUUSD",
        timestamp=bars[-1].timestamp + timedelta(seconds=23),
        bid=bars[-1].high + 0.6,
        ask=bars[-1].high + 0.66,
        volume=4.0,
    )
    eng = ScalpFeatureEngine(symbol="XAUUSD")
    fv = eng.compute_from_bars(completed_bars=bars, current_tick=tick)
    regime = _RegimeState(tick.timestamp)

    base_thr = 0.35
    results = {}
    for label, p_buy in [("below", 0.20), ("at", 0.21), ("above", 0.24), ("strong", 0.42)]:
        probs = torch.tensor([0.6 - p_buy, p_buy, 0.0, 0.0], dtype=torch.float32)
        pol = SignalPolicy(
            confidence_threshold=base_thr,
            algo_config=_AlgoCfg(),
        )
        pol.algo_config = _AlgoCfg()
        proposal = pol.evaluate_probabilities(
            probabilities=probs,
            current_tick=tick,
            feature_vector=fv,
            regime_state=regime,
            survival_mode=False,
            force_log=False,
            order_manager=None,
        )
        action = proposal.action.value
        conf = float(proposal.confidence)
        results[label] = (action, conf, proposal.blocked_by, proposal.decision_stage)
        print(
            f"  {label:6s} conf={conf:.3f} action={action} stage={proposal.decision_stage} "
            f"blocked_by={proposal.blocked_by} reason={(proposal.reason_code or '')[:56]}"
        )

    below = results["below"][0] == "NO_TRADE"
    above = results["above"][0] != "NO_TRADE"
    strong = results["strong"][0] != "NO_TRADE"
    at = results["at"][0] != "NO_TRADE"  # >= semantics: exactly at passes
    print()
    print(f"  below->NO_TRADE: {below}; at->entry: {at}; above->entry: {above}; strong->entry: {strong}")
    if below and at and above and strong:
        print("TDF-8b VERDICT: PASS — CONFIDENCE_GATE semantics confirmed (>= threshold passes)")
        return 0
    print("TDF-8b VERDICT: REVIEW")
    return 1


if __name__ == "__main__":
    sys.exit(main())
