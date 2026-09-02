"""Check H-sensitivity of liquidity features: does H=1000/2000 change values
vs H=4000 on the SAME decision row? If features are identical, smaller H is
semantics-preserving for this data (M5 XAUUSD)."""
import sys
import time

sys.path.insert(0, r"C:/Users/Capsizer/source/repos/NexusTradingForexBot")

from datetime import UTC, datetime

from nexus_scalp.features.liquidity_engine import compute_liquidity_features
from nexus_scalp.market_data.bar_aggregator import BarData


def make_bars(n: int) -> list[BarData]:
    out = []
    ts = datetime(2025, 3, 1, tzinfo=UTC)
    px = 3000.0
    for i in range(n):
        o = px
        c = px + (0.4 if i % 3 else -0.3)
        out.append(
            BarData(
                symbol="XAUUSD", timeframe="M5", timestamp=ts,
                open=o, high=max(o, c) + 0.2, low=min(o, c) - 0.2,
                close=c, tick_volume=100 + (i % 50), is_complete=True,
            )
        )
        px = c
        ts = ts.replace(minute=(ts.minute + 5) % 60, hour=(ts.hour + (1 if ts.minute >= 55 else 0)) % 24)
        if i and i % 288 == 287:
            ts = ts.replace(day=ts.day + 1)
    return out


def main() -> None:
    bars = make_bars(4000)
    decision = bars[-1].timestamp
    base = compute_liquidity_features(bars[:], decision_at=decision, mid_price=bars[-1].close, atr=1.5)
    base_vec = base.as_vector()
    print("H=4000:", [round(v, 4) for v in base_vec])
    for h in (3000, 2000, 1000, 500):
        sub = bars[-h:]
        t0 = time.perf_counter()
        f = compute_liquidity_features(sub, decision_at=decision, mid_price=bars[-1].close, atr=1.5)
        dt = (time.perf_counter() - t0) * 1000
        vec = f.as_vector()
        deltas = [abs(a - b) for a, b in zip(base_vec, vec, strict=False)]
        print(f"H={h:4d}: {dt:7.1f} ms/row  max_delta={max(deltas):.6f}  nz={sum(1 for d in deltas if d > 1e-9)}/10")


if __name__ == "__main__":
    main()