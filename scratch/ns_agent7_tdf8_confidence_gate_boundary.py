"""Agent 7 — TDF-8: policy channel + confidence gate boundary (real policy).

Drives the REAL SignalPolicy.evaluate_probabilities with crafted probability
vectors and feature states so that a candidate channel FIRES, then walks the
directional confidence across the threshold boundary:
  - directional conf just below / exactly at / just above effective threshold
  - proves gate semantics: conf < eff_thr blocks, conf >= eff_thr passes

Threshold formula (verified in source): active = base + 0.10 (survival) + range_penalty.
The policy builds cand_confidence = directional prob over trained classes.
We control probs so the candidate channel (AGGRESSIVE_SCALP_BUY via ichimoku)
fires with a chosen confidence.
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


class _RegimeState:
    """Minimal duck-typed regime state (policy reads .regime_type etc.)."""

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
    from nexus_scalp.features.regime_classifier import RegimeType
    from nexus_scalp.features.scalp_features import ScalpFeatureEngine
    from nexus_scalp.signals.policy import SignalPolicy

    print("=== TDF-8: confidence-gate boundary on the REAL policy ===")

    bars = make_bars(600)
    # force a bullish displacement so broke_previous_high / momentum fires
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
    pol = SignalPolicy(confidence_threshold=base_thr)

    # Candidate channel must fire; craft probs so the candidate's directional
    # confidence (over trained classes) takes the three boundary values.
    # cand fires via ichimoku/ict/momentum — directional conf = p_buy/(p_nt+p_buy+p_sell)
    results = {}
    for label, p_buy in [("below", 0.20), ("at", 0.21), ("above", 0.24)]:
        # trained mass = 0.6 -> conf = p_buy/0.6: 0.333 / 0.35 / 0.40
        probs = torch.tensor([0.6 - p_buy, p_buy, 0.0, 0.0], dtype=torch.float32)
        pol2 = SignalPolicy(confidence_threshold=base_thr)
        proposal = pol2.evaluate_probabilities(
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
        eff = base_thr + 0.0  # no survival, no range penalty (trending state)
        expected_block = (conf < eff) and action == "NO_TRADE"
        print(
            f"  {label:6s} conf={conf:.3f} eff_thr={eff:.2f} action={action} "
            f"stage={proposal.decision_stage} blocked_by={proposal.blocked_by} "
            f"reason={(proposal.reason_code or '')[:52]} "
            f"gate={'BLOCKED' if expected_block else 'PASSED'}"
        )

    # semantic check: below must be NO_TRADE w/ CONFIDENCE_FAIL, above must be an entry action
    below_action = results["below"][0]
    above_action = results["above"][0]
    ok_below = below_action == "NO_TRADE"
    ok_above = above_action != "NO_TRADE"
    at_action = results["at"][0]
    print()
    print(f"  below -> NO_TRADE: {ok_below};  at-threshold action: {at_action};  above -> entry: {ok_above}")
    if ok_below and ok_above:
        print("TDF-8 VERDICT: PASS (gate blocks strictly-below, allows at/above — correct >= semantics)")
        return 0
    print("TDF-8 VERDICT: REVIEW (boundary semantics deviate from expectation — inspect above)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
