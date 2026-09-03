"""Profile the LIQUIDITY engine internals to find the O(H^2) hot spot."""
import sys
import time

sys.path.insert(0, r"C:/Users/Capsizer/source/repos/NexusTradingForexBot")

import nexus_scalp.features.liquidity_engine as le


def make_bars(n: int) -> list:
    from datetime import UTC, datetime

    from nexus_scalp.market_data.bar_aggregator import BarData

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
    vis = [b for b in bars if b.timestamp <= decision]
    print("visible bars:", len(vis))

    for fn_name in (
        "detect_confirmed_swings",
        "session_high_low_pools",
        "daily_price_pools",
        "update_pool_states",
        "htf_liquidity_score",
        "internal_external_distances",
        "liquidity_confluence",
        "detect_reactive_sweep",
    ):
        fn = getattr(le, fn_name, None)
        if fn is None:
            print(f"{fn_name}: MISSING")
            continue
        t0 = time.perf_counter()
        try:
            if fn_name == "detect_confirmed_swings":
                fn(vis, window=le.SWING_CONFIRM_BARS)
            elif fn_name in ("session_high_low_pools", "daily_price_pools", "update_pool_states"):
                pools = le.detect_confirmed_swings(vis, window=le.SWING_CONFIRM_BARS)
                pools = list(pools[0]) + list(pools[1])
                if fn_name == "update_pool_states":
                    fn(pools, vis, 1.5, now=decision)
                else:
                    fn(vis, now=decision)
            elif fn_name == "htf_liquidity_score":
                fn(vis, 1.5, decision_at=decision)
            elif fn_name == "internal_external_distances":
                pools = []
                for sh, sl in [le.detect_confirmed_swings(vis, window=le.SWING_CONFIRM_BARS)]:
                    pools = list(sh) + list(sl)
                fn(pools, 3000.0, 1.5, decision_at=decision)
            elif fn_name == "liquidity_confluence":
                pools = [p for sh, sl in [le.detect_confirmed_swings(vis, window=le.SWING_CONFIRM_BARS)] for p in list(sh) + list(sl)]
                fn(pools, decision_at=decision, atr=1.5)
            else:
                fn(vis, 1.5, decision_at=decision)
            dt = time.perf_counter() - t0
            print(f"{fn_name}: {dt*1000:.1f} ms")
        except Exception as e:
            print(f"{fn_name}: ERR {type(e).__name__} {str(e)[:120]}")


if __name__ == "__main__":
    main()