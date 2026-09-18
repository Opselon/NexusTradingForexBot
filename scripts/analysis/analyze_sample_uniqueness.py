"""ML-LABEL-002: Empirical Sample Uniqueness & Concurrency Distribution Analysis.

Evaluates Marcos Lopez de Prado's Sample Uniqueness Weighting algorithm on Gold (XAUUSD)
M1 market data labeled with cost-aware Triple Barrier horizons.

Produces:
  - Quantitative concurrency metrics & overlap distribution
  - Effective sample size (Kish formula) and redundancy ratio
  - ASCII empirical distribution histogram
  - Structured JSON artifact for CI and governance audit
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nexus_scalp.labeling.sample_weights import (
    compute_concurrency_events,
    compute_sample_uniqueness,
    compute_uniqueness_metrics,
)
from nexus_scalp.labeling.triple_barrier import TripleBarrierConfig, TripleBarrierLabeler
from scripts.data.ingest_historical_candles import generate_synthetic_bars


def render_ascii_histogram(data: np.ndarray, bins: int = 10, title: str = "Distribution") -> str:
    """Renders a text-based histogram for terminal and markdown display."""
    if len(data) == 0:
        return f"{title}: [Empty data]"

    counts, bin_edges = np.histogram(data, bins=bins)
    max_count = max(counts) if max(counts) > 0 else 1
    max_bar_len = 30

    lines = [f"--- {title} (N={len(data):,}) ---"]
    for i in range(len(counts)):
        bar_len = int((counts[i] / max_count) * max_bar_len)
        bar_str = "#" * bar_len
        pct = (counts[i] / len(data)) * 100.0
        lines.append(
            f"[{bin_edges[i]:.2f} - {bin_edges[i + 1]:.2f}]: {bar_str:<{max_bar_len}} {counts[i]:>6,} ({pct:>5.1f}%)"
        )
    return "\n".join(lines)


def run_empirical_uniqueness_study(
    bar_count: int = 10_000,
    max_holding_bars: int = 15,
    take_profit_atr_mult: float = 1.1,
    stop_loss_atr_mult: float = 1.0,
    friction_usd: float = 0.35,
    seed: int = 42,
) -> dict[str, Any]:
    """Runs empirical sample uniqueness analysis across simulated Gold M1 candles."""
    t0 = time.perf_counter()

    # 1. Generate market bars
    bars = generate_synthetic_bars(symbol="XAUUSD", count=bar_count, seed=seed)

    # 2. Add realistic ATR
    closes = bars["close"].to_numpy().astype(np.float64)
    highs = bars["high"].to_numpy().astype(np.float64)
    lows = bars["low"].to_numpy().astype(np.float64)

    tr = np.maximum(highs[1:] - lows[1:], np.abs(highs[1:] - closes[:-1]))
    tr = np.maximum(tr, np.abs(lows[1:] - closes[:-1]))
    atr_vals = np.zeros(len(bars), dtype=np.float64)
    atr_vals[0] = 1.5
    for i in range(1, len(bars)):
        atr_vals[i] = 0.9 * atr_vals[i - 1] + 0.1 * tr[i - 1]

    bars = bars.with_columns(
        [
            pl.Series("atr_m1", atr_vals),
            pl.Series("atr", atr_vals),
        ]
    )

    # 3. Apply Triple Barrier Labeler with diagnostics
    config = TripleBarrierConfig(
        take_profit_atr_mult=take_profit_atr_mult,
        stop_loss_atr_mult=stop_loss_atr_mult,
        max_holding_bars=max_holding_bars,
        friction_usd=friction_usd,
        include_diagnostics=True,
    )
    labeler = TripleBarrierLabeler(config=config)
    labeled_df = labeler.label_dataframe(bars)

    eval_mask = labeled_df["is_eval_sample"].to_numpy().astype(bool)
    n_eval = int(np.sum(eval_mask))

    # 4. Compute Sample Uniqueness
    raw_u = compute_sample_uniqueness(df=labeled_df, normalize=False)

    eval_u = raw_u[eval_mask]

    # Concurrency events
    eval_indices = np.where(eval_mask)[0]
    holding = labeled_df["holding_bars"].to_numpy()[eval_mask].astype(np.int64)
    ends = np.minimum(eval_indices + holding, len(bars) - 1)
    concurrency = compute_concurrency_events(eval_indices, ends, total_bars=len(bars))
    active_c = concurrency[concurrency > 0]

    # Metrics
    metrics = compute_uniqueness_metrics(
        weights=raw_u,
        start_indices=eval_indices,
        end_indices=ends,
        total_bars=len(bars),
    )

    # Distribution quantiles
    quantiles = {
        "p05": float(np.percentile(eval_u, 5)),
        "p10": float(np.percentile(eval_u, 10)),
        "p25": float(np.percentile(eval_u, 25)),
        "p50": float(np.percentile(eval_u, 50)),
        "p75": float(np.percentile(eval_u, 75)),
        "p90": float(np.percentile(eval_u, 90)),
        "p95": float(np.percentile(eval_u, 95)),
    }

    elapsed = round(time.perf_counter() - t0, 3)

    return {
        "dataset_bars": bar_count,
        "evaluated_samples": n_eval,
        "metrics": metrics.to_dict(),
        "quantiles": quantiles,
        "raw_uniqueness_histogram": render_ascii_histogram(
            eval_u, bins=10, title="Raw Sample Uniqueness (u_i)"
        ),
        "concurrency_histogram": render_ascii_histogram(
            active_c, bins=10, title="Concurrency (c_t)"
        ),
        "elapsed_sec": elapsed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Empirical Sample Uniqueness Analysis (ML-LABEL-002)"
    )
    parser.add_argument("--bars", type=int, default=10000, help="Total synthetic bars to evaluate")
    parser.add_argument("--holding", type=int, default=15, help="Max holding bars horizon")
    parser.add_argument("--export-md", type=str, default="", help="Export markdown report path")
    parser.add_argument("--export-json", type=str, default="", help="Export JSON results path")

    args = parser.parse_args()

    results = run_empirical_uniqueness_study(
        bar_count=args.bars,
        max_holding_bars=args.holding,
    )

    print(results["raw_uniqueness_histogram"])
    print()
    print(results["concurrency_histogram"])
    print()
    print(f"Evaluated Samples:    {results['evaluated_samples']:,}")
    print(f"Mean Uniqueness:      {results['metrics']['mean_uniqueness']:.4f}")
    print(f"Effective Samples:    {results['metrics']['effective_sample_size']:,.1f}")
    print(f"Redundancy Ratio:     {results['metrics']['redundancy_ratio'] * 100:.2f}%")
    print(f"Max Concurrency:      {results['metrics']['max_concurrency']}")
    print(f"Execution Time:       {results['elapsed_sec']:.3f}s")

    if args.export_json:
        p = Path(args.export_json)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"Exported JSON: {p}")

    if args.export_md:
        p = Path(args.export_md)
        p.parent.mkdir(parents=True, exist_ok=True)
        md_content = f"""# ML-LABEL-002: Empirical Sample Uniqueness & Concurrency Study

## Executive Summary
Evaluation of Marcos Lopez de Prado's Sample Uniqueness Weighting algorithm on Gold M1 bars.
- **Dataset Size:** {results["dataset_bars"]:,} bars
- **Evaluated Samples:** {results["evaluated_samples"]:,} samples
- **Mean Sample Uniqueness ($u_i$):** {results["metrics"]["mean_uniqueness"]:.4f}
- **Kish Effective Sample Size ($N_{{eff}}$):** {results["metrics"]["effective_sample_size"]:,.1f}
- **Sample Redundancy Ratio:** {results["metrics"]["redundancy_ratio"] * 100:.2f}%
- **Max Concurrency ($c_t$):** {results["metrics"]["max_concurrency"]} concurrent active labels
- **Mean Concurrency ($c_t$):** {results["metrics"]["mean_concurrency"]:.2f}

## Quantiles of Sample Uniqueness
| Quantile | Value |
|---|---|
| p05 | {results["quantiles"]["p05"]:.4f} |
| p10 | {results["quantiles"]["p10"]:.4f} |
| p25 | {results["quantiles"]["p25"]:.4f} |
| Median (p50) | {results["quantiles"]["p50"]:.4f} |
| p75 | {results["quantiles"]["p75"]:.4f} |
| p90 | {results["quantiles"]["p90"]:.4f} |
| p95 | {results["quantiles"]["p95"]:.4f} |

## Empirical Distributions
```
{results["raw_uniqueness_histogram"]}
```

```
{results["concurrency_histogram"]}
```
"""
        with open(p, "w", encoding="utf-8") as f:
            f.write(md_content)
        print(f"Exported Markdown: {p}")


if __name__ == "__main__":
    main()
