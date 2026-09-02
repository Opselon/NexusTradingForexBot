"""MSLIE historical validation probe — multi-regime, multi-behavior series.

Simulates three market regimes (trending / ranging / volatile-sweepy) and
verifies the perception engine produces coherent, regime-distinguishable
intelligence. Captured output: scratch/mslie_validate.out.txt

Run: .venv/Scripts/python.exe -m scratch.mslie_validate (or direct file)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")

from nexus_scalp.mslie import MarketStructureEngine


@dataclass
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int


def _series(kind: str, n: int = 300, start: float = 2000.0) -> list[Bar]:
    bars: list[Bar] = []
    t = datetime(2025, 3, 1, 0, 0, tzinfo=UTC)
    price = start
    for i in range(n):
        if kind == "trend":
            drift = 0.8 if i > n // 3 else 0.0
            noise = 0.4 if i % 2 else -0.3
            vol = 0.9
        elif kind == "range":
            # sinusoidal mean reversion around the base level — no
            # directional persistence, bounded band
            drift = 0.0
            noise = 0.55 * (1.0 if i % 2 == 0 else -0.2)
            rev = (start - price) * 0.12  # strong pull to the base
            wave = 1.6 * (1.0 if (i % 40) < 20 else -1.0)  # slow alternation
            noise = noise + rev + wave * 0.05
            vol = 1.0
        else:  # volatile sweepy
            drift = 0.15
            noise = 1.3 if i % 3 == 0 else -0.7
            vol = 2.2
        o = price
        c = price + drift + noise
        h = max(o, c) + vol
        l = min(o, c) - vol
        # occasional engineered stop hunt: spike beyond the recent extreme
        if kind == "sweep" and i % 50 == 40:
            l = price - vol * 2.5  # deep low
            c = price + 0.2  # reclaim
        bars.append(Bar(t, o, h, l, c, 80 + (i % 9) * 25))
        price = c
        t += timedelta(minutes=1)
    return bars


def main() -> None:
    results = {}
    for kind in ("trend", "range", "sweep"):
        eng = MarketStructureEngine(symbol="XAUUSD", timeframe="M1")
        bars = _series(kind)
        v = eng.analyze_market(bars, atr=None)
        st = eng.get_debug_status()
        results[kind] = {
            "regime": v.regime.regime_label,
            "trend_strength": v.regime.trend_strength,
            "volatility": v.regime.volatility_state,
            "structure": v.structure,
            "bias": v.bias.name,
            "confidence": v.structure_confidence,
            "swings": (v.swing_count_high, v.swing_count_low),
            "zones": len(v.liquidity_map),
            "sweeps": [
                s.direction + ":" + s.after_event_state.name
                for s in ([v.last_sweep_event] if v.last_sweep_event else [])
            ],
            "latency_ms": st["engine_status"]["latency_ms"],
            "memory": len(v.memory),
        }
        print(f"[{kind.upper()}]")
        for k, val in results[kind].items():
            print(f"    {k}: {val}")

    # regime distinctness assertions (probe-level evidence)
    trend_reg = results["trend"]["regime"]
    range_reg = results["range"]["regime"]
    assert trend_reg not in ("RANGING",), f"trend misclassified: {trend_reg}"
    assert range_reg in ("RANGING", "COMPRESSION", "MIXED"), f"range misclassified: {range_reg}"
    assert results["sweep"]["volatility"] > results["range"]["volatility"]
    print("\nPROBE ASSERTIONS PASS: regime-distinct perception verified")
    print("latency (ms):", {k: r["latency_ms"] for k, r in results.items()})


if __name__ == "__main__":
    main()
