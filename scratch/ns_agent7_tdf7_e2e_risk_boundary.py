"""Agent 7 — TDF-7: E2E with a trading-allowed regime (guardian gate bypassed).

TDF-6 hit the REGIME_GUARDIAN (HIGH_SPREAD_CHOP) which correctly fail-closed.
To trace the FULL chain into risk/execution, rerun with a trending regime
state so the policy reaches the risk boundary and (when every gate passes)
produces a sized TradeOrder — still adapter-free (no broker).
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")

import numpy as np
import torch


def make_bars(n: int, start: float = 2400.0, seed: int = 21) -> list:
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


def main() -> int:
    from nexus_scalp.configuration.config import RiskConfig
    from nexus_scalp.domain.enums import ActionType
    from nexus_scalp.domain.models import TickData
    from nexus_scalp.features.regime_classifier import (
        MarketRegimeClassifier,
        MarketRegimeState,
        RegimeReason,
        RegimeType,
        RecommendedExecutionType,
    )
    from nexus_scalp.features.scalp_features import ScalpFeatureEngine
    from nexus_scalp.models.scalp_net import ScalpNet
    from nexus_scalp.risk.risk_engine import RiskEngine
    from nexus_scalp.signals.policy import SignalPolicy

    bars = make_bars(600)
    tick = TickData(
        symbol="XAUUSD",
        timestamp=bars[-1].timestamp + timedelta(seconds=23),
        bid=bars[-1].close - 0.03,
        ask=bars[-1].close + 0.03,
        volume=4.0,
    )
    eng = ScalpFeatureEngine(symbol="XAUUSD")
    fv = eng.compute_from_bars(completed_bars=bars, current_tick=tick)
    x50 = fv.to_tensor_input()

    hist = np.array(
        [
            ScalpFeatureEngine(symbol="XAUUSD")
            .compute_from_bars(completed_bars=make_bars(600, seed=s), current_tick=tick)
            .to_tensor_input()
            for s in range(5, 9)
        ],
        dtype=np.float32,
    )
    mean, std = hist.mean(axis=0), hist.std(axis=0)
    std[std == 0.0] = 1.0
    x_scaled = (np.array([x50], dtype=np.float32) - mean) / std

    torch.manual_seed(5)
    net = ScalpNet(num_features=50, num_classes=4)
    net.eval()
    with torch.inference_mode():
        logits = net(torch.tensor(x_scaled, dtype=torch.float32), return_logits=True)
    from nexus_scalp.model_lifecycle.model_class_contract import masked_softmax

    probs = masked_softmax(logits)[0]

    # Real classifier for context, then OVERRIDE with an explicit TRENDING
    # regime (deterministic fixture) so the guardian gate is not the story —
    # we are tracing policy->risk boundary behavior.
    rc = MarketRegimeClassifier(symbol="XAUUSD")
    state = None
    for i in range(40, 0, -1):
        past = TickData(
            symbol="XAUUSD",
            timestamp=tick.timestamp - timedelta(seconds=i * 3),
            bid=tick.bid - 0.02,
            ask=tick.ask - 0.02,
            volume=2.0,
        )
        state = rc.classify_tick(current_tick=past)
    trending = MarketRegimeState(
        symbol=tick.symbol,
        timestamp_utc=tick.timestamp.isoformat(),
        regime_type=RegimeType.TRENDING_MOMENTUM,
        regime_probability=0.9,
        order_flow_imbalance=0.4,
        realized_volatility_5m=0.0002,
        tick_velocity_per_sec=2.0,
        current_spread_usd=round(tick.ask - tick.bid, 2),
        is_macro_news_active=False,
        recommended_execution_type=RecommendedExecutionType.IOC_MARKET,
        reason=RegimeReason.OFI_TREND_ALIGN,
    )
    _ = state  # context only

    pol = SignalPolicy(confidence_threshold=0.20)
    proposal = pol.evaluate_probabilities(
        probabilities=probs,
        current_tick=tick,
        feature_vector=fv,
        regime_state=trending,
        survival_mode=False,
        force_log=False,
        order_manager=None,
    )
    print("=== TDF-7: policy->risk boundary on trading-allowed regime ===")
    print(
        f"  [POLICY] action={proposal.action.value} conf={proposal.confidence:.3f} "
        f"stage={proposal.decision_stage} blocked_by={proposal.blocked_by} "
        f"rr={proposal.risk_reward_ratio:.2f}"
    )

    risk = RiskEngine(config=RiskConfig(risk_per_trade_pct=0.5))
    if proposal.action not in (ActionType.NO_TRADE, ActionType.WAIT):
        order = risk.evaluate_proposal(
            proposal=proposal,
            account=_Acc(),  # type: ignore[arg-type]
            symbol_info=_Sym(),  # type: ignore[arg-type]
            active_positions=[],
            current_tick=tick,
            regime_state=trending,
            atr=max(fv.atr_m1, 0.5),
        )
        if order is not None:
            print(
                f"  [RISK] SIZED TradeOrder: volume={order.volume} type={order.order_type.value} "
                f"id={order.order_id[:18]}... -> dispatch_order would be the ONLY next authority"
            )
            # HARD_MAX_LOTS / clamp proof with an absurd volume
            clamped = risk.get_clamped_position_size(
                volume=999.0, account=_Acc(), symbol_info=_Sym()  # type: ignore[arg-type]
            )
            print(f"  [RISK] clamp(999 lots) -> {clamped} (tier ceiling 1.0 @ 5k equity)")
            return 0
        print("  [RISK] proposal REJECTED by risk gates (fail-closed, correct)")
        return 0
    print("  [POLICY] NO_TRADE (no risk consult) — inspect reason:", proposal.reason_code[:80])
    return 0


if __name__ == "__main__":
    sys.exit(main())
