"""EDGE ROUND-5 (2026-09-09): per-regime expectancy report (calibration data).

The scoring layer decomposes regime coverage into per-regime expectancy
(scoring._regime_expectancy_coverage). Calibration against REAL data needs
that same decomposition as a standalone, dumpable report over a dataset:

  * one row per traded regime: n, mean_r, win_rate, share_of_trades
  * consistent flag (mean_r > 0)
  * UNKNOWN provenance flagged

Deterministic, read-only, no I/O beyond the caller.
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.research.models import ResearchDataset


def per_regime_expectancy_report(dataset: ResearchDataset) -> dict[str, Any]:
    """Per-regime expectancy decomposition for calibration sweeps.

    Returns rows sorted by |mean_r| descending (the regimes that matter most
    first) plus a summary the scorer would compute (consistency x breadth).
    """
    by_regime: dict[str, list[float]] = {}
    for s in dataset.samples:
        by_regime.setdefault(str(s.regime or "UNKNOWN"), []).append(float(s.realized_r))
    n_total = len(dataset.samples)
    rows: list[dict[str, Any]] = []
    for name, rs in by_regime.items():
        n = len(rs)
        mean_r = sum(rs) / n if n else 0.0
        wins = sum(1 for r in rs if r > 0)
        rows.append(
            {
                "regime": name,
                "n": n,
                "mean_r": round(mean_r, 4),
                "win_rate": round(wins / n, 4) if n else 0.0,
                "share_of_trades": round(n / n_total, 4) if n_total else 0.0,
                "consistent": mean_r > 0.0,
                "unknown_provenance": name == "UNKNOWN",
            }
        )
    rows.sort(key=lambda r: -abs(r["mean_r"]))
    n_regimes = len(rows)
    negative = [r["regime"] for r in rows if not r["consistent"]]
    consistency = 1.0 - (len(negative) / n_regimes) if n_regimes else 0.0
    if any(r["unknown_provenance"] for r in rows):
        consistency *= 0.9
    breadth = min(1.0, n_regimes / 8.0)
    return {
        "n_samples": n_total,
        "n_regimes": n_regimes,
        "negative_regimes": negative,
        "regime_coverage": round(max(0.0, min(1.0, consistency * breadth)), 4),
        "rows": rows,
    }


def render_regime_report(dataset: ResearchDataset) -> str:
    """Compact human-readable rendering (no fake precision, no padding)."""
    rep = per_regime_expectancy_report(dataset)
    lines = [
        f"REGIME REPORT samples={rep['n_samples']} regimes={rep['n_regimes']} "
        f"coverage={rep['regime_coverage']}"
    ]
    for r in rep["rows"]:
        flag = "OK " if r["consistent"] else "NEG"
        if r["unknown_provenance"]:
            flag += "?"
        lines.append(
            f"  [{flag}] {r['regime']}: n={r['n']} mean_r={r['mean_r']} "
            f"win={r['win_rate']} share={r['share_of_trades']}"
        )
    return "\n".join(lines)


__all__: list[str] = [
    "per_regime_expectancy_report",
    "render_regime_report",
]
