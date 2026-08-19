"""TASK-10 forensic probe: causality audit at the feature-engine level.

Injects "future" data (future highs/lows/swings/HTF closes) into the bar
window AFTER a decision timestamp T and re-computes the liquidity 10D
block at decision_at=T. Expected: the liquidity features at T are
IDENTICAL before and after the injection (no future leakage), because
the engine is causal (only bars with close <= T are usable).

Independent check: also verifies the HTF forming-bucket exclusion
(incomplete H1/H4/D1 must not be consumed).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from nexus_scalp.features.liquidity_engine import compute_liquidity_features  # noqa: E402

LIQ_NAMES = (
    "bsl_distance_atr",
    "ssl_distance_atr",
    "eqh_strength",
    "eql_strength",
    "htf_liquidity_score",
    "internal_liquidity_distance",
    "external_liquidity_distance",
    "liquidity_confluence",
    "liquidity_sweep_state",
    "post_sweep_displacement",
)


def make_bars(n: int = 300, seed: int = 11, start: datetime | None = None) -> list[dict]:
    rng = np.random.default_rng(seed)
    price = 2000.0
    start = start or datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    out = []
    for i in range(n):
        price += float(rng.normal(0.0, 0.5))
        o = price
        h = o + abs(float(rng.normal(0.3, 0.25)))
        l = o - abs(float(rng.normal(0.3, 0.25)))
        c = o + float(rng.normal(0.0, 0.45))
        c = min(max(c, l + 0.005), h - 0.005)
        out.append(
            {
                "timestamp": datetime.fromtimestamp(start.timestamp() + i * 60, tz=UTC),
                "open": round(o, 5),
                "high": round(h, 5),
                "low": round(l, 5),
                "close": round(c, 5),
                "tick_volume": int(rng.integers(50, 400)),
            }
        )
    return out


def main() -> int:
    bars = make_bars()
    # choose T = the 200th bar's close (well past warm-up)
    t_idx = 200
    T = bars[t_idx]["timestamp"]
    window = bars[: t_idx + 1]

    def to_objs(window_: list[dict]) -> list[SimpleNamespace]:
        return [SimpleNamespace(**dict(b)) for b in window_]

    def feats_at(window_: list[dict]) -> list[float]:
        # Do NOT pass mid_price: the engine must derive the decision price
        # from the causal window only (bars <= T). Feeding the future
        # close as mid_price would itself be a leak vector.
        f = compute_liquidity_features(to_objs(window_), decision_at=T)
        return [float(x) for x in f.as_vector()]

    v_before = feats_at(window)

    # Inject EXTREME future data AFTER T: huge highs/lows, sweeps, HTF closes.
    future = []
    rng = np.random.default_rng(999)
    fprice = float(bars[t_idx]["close"])
    for i in range(1, 60):
        fprice += float(rng.normal(0.0, 2.0))
        future.append(
            {
                "timestamp": datetime.fromtimestamp(T.timestamp() + i * 60, tz=UTC),
                "open": round(fprice, 5),
                "high": round(fprice + 5.0, 5),  # extreme
                "low": round(fprice - 5.0, 5),
                "close": round(fprice + (1.0 if i % 2 else -1.0), 5),
                "tick_volume": 99999,
            }
        )
    window_future = window + future
    v_after = feats_at(window_future)

    print("T:", T.isoformat())
    print("v_before:", [round(x, 6) for x in v_before])
    print("v_after :", [round(x, 6) for x in v_after])
    diffs = [abs(a - b) for a, b in zip(v_before, v_after)]
    print("max abs diff:", max(diffs))
    for i, (nm, d) in enumerate(zip(LIQ_NAMES, diffs)):
        flag = "  <-- LEAK" if d > 1e-9 else ""
        print(f"  {60 + i} {nm}: diff={d:.9f}{flag}")

    if max(diffs) > 1e-9:
        print("\nRESULT: FUTURE DATA LEAKED INTO DECISION T (RELEASE_BLOCKED_MTF_LEAKAGE)")
        return 1
    print("\nRESULT: CAUSAL — future data does not change features at T")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())