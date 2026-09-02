"""CHG-0043 baseline probe: capture StreamingReplayEngine.run() hashes on
(a) a deterministic synthetic stream and (b) a real local M1 slice, BEFORE
the stepwise refactor. The post-refactor probe must reproduce these exactly.

Run: .venv/Scripts/python.exe scratch/chg0043_baseline_probe.py [--write]
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from nexus_scalp.research.event_source import BarEventSource, TickEventSource
from nexus_scalp.research.streaming_replay import (
    ReplayExecutionConfig,
    ReplaySessionConfig,
    StreamingReplayEngine,
)

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "scratch" / "chg0043_refactor_equivalence.json"

BUNDLE = REPO / "artifacts" / "models" / "scalp" / "XAUUSD" / "70d_liquidity" / "model.pt"

T0 = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)


def synthetic_ticks(minutes: int = 900) -> list[dict]:
    """Deterministic zig-zag + trend pattern (no RNG)."""
    out = []
    for m in range(minutes):
        base = 2650.0 + 3.0 * (m % 97) / 97.0 + 0.8 * ((m // 97) % 5)
        px = base + (1.2 if (m % 11) < 5 else -1.0)
        ts = T0 + timedelta(minutes=m)
        out.append(
            {
                "timestamp": ts,
                "bid": px,
                "ask": px + 0.2,
                "time_msc": int(ts.timestamp() * 1000),
                "last": 0.0,
                "flags": 0,
                "volume": 5.0,
                "symbol": "XAUUSD",
            }
        )
    return out


def real_bars(n: int = 1500) -> list[dict]:
    df = pl.read_parquet(REPO / "data" / "raw" / "XAUUSD_M1.parquet").head(n)
    out = []
    for r in df.iter_rows(named=True):
        ts = r["time_utc"].replace(tzinfo=UTC)
        out.append(
            {
                "timestamp": ts,
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "tick_volume": int(r["tick_volume"]),
                "spread": float(r["spread"]),
                "symbol": "XAUUSD",
                "timeframe": "M1",
            }
        )
    return out


def run_cfg(decide_on: str) -> ReplaySessionConfig:
    return ReplaySessionConfig(
        model_artifact_path=str(BUNDLE),
        policy_params={"confidence_threshold": 0.35},
        decide_on=decide_on,
        execution=ReplayExecutionConfig(),
        git_commit="chg0043-baseline",
    )


def main() -> int:
    write = "--write" in sys.argv
    ticks = synthetic_ticks()
    bars = real_bars()

    results = {}

    eng = StreamingReplayEngine(run_cfg("every_tick"))
    r = eng.run(TickEventSource(ticks, name="synthetic"), run_id="BASE-TICK")
    results["synthetic_every_tick"] = {
        "event_hash": r.event_hash,
        "ledger_hash": r.ledger_hash,
        "events_seen": r.events_seen,
        "decisions": r.decisions,
        "orders": len(r.orders),
        "trades": len(r.trades),
        "pnl": round(r.total_pnl_usd, 6),
    }

    eng2 = StreamingReplayEngine(run_cfg("bar_close"))
    r2 = eng2.run(BarEventSource(bars, name="real-m1"), run_id="BASE-BAR")
    results["real_m1_bar_close"] = {
        "event_hash": r2.event_hash,
        "ledger_hash": r2.ledger_hash,
        "events_seen": r2.events_seen,
        "decisions": r2.decisions,
        "orders": len(r2.orders),
        "trades": len(r2.trades),
        "pnl": round(r2.total_pnl_usd, 6),
        "first": r2.first_event,
        "last": r2.last_event,
    }

    blob = json.dumps(results, indent=2, sort_keys=True)
    print(blob)
    if write:
        OUT.write_text(blob, encoding="utf-8")
        print(f"WROTE {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
