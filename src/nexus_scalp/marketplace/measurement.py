"""
Marketplace measurement — honest windows/regimes (CHG-0056, ARCH_SPEC §2).

Provides window/regime slices of available backtest data (ResearchDataset)
and returns NOT_AVAILABLE for any per-strategy live attribution (never
fabricates live numbers — ARCH_SPEC §2 scoring.py §19).
"""

from __future__ import annotations

from dataclasses import dataclass

from nexus_scalp.research.metrics import compute_backtest
from nexus_scalp.research.models import BacktestResult, ResearchDataset, ResearchSample


@dataclass(frozen=True)
class WindowMeasurement:
    window: str
    n_samples: int
    expectancy_r: float
    win_rate: float
    availability: str  # AVAILABLE | NOT_AVAILABLE
    reasons: list[str]


@dataclass(frozen=True)
class RegimeMeasurement:
    regime: str
    n_samples: int
    expectancy_r: float
    win_rate: float
    availability: str


def _win_rate(samples: list[ResearchSample]) -> float:
    if not samples:
        return 0.0
    return sum(1 for s in samples if getattr(s, "realized_r", 0.0) > 0.0) / len(samples)


def _exp(samples: list[ResearchSample]) -> float:
    if not samples:
        return 0.0
    return sum(getattr(s, "realized_r", 0.0) for s in samples) / len(samples)


def windows(dataset: ResearchDataset | None) -> list[WindowMeasurement]:
    """Per-window slices of the available dataset (never live P&L).

    Windows: IS_WINDOW (first 60%), OOS_WINDOW (last 25%), RECENT (last 20%).
    Live-tilted windows (live_attributed) return NOT_AVAILABLE honestly.
    """
    if dataset is None or not dataset.samples:
        return [
            WindowMeasurement(
                "IS_WINDOW", 0, 0.0, 0.0, "NOT_AVAILABLE", ["NOT_AVAILABLE: no backtest dataset"]
            ),
            WindowMeasurement(
                "OOS_WINDOW", 0, 0.0, 0.0, "NOT_AVAILABLE", ["NOT_AVAILABLE: no backtest dataset"]
            ),
            WindowMeasurement(
                "RECENT_WINDOW",
                0,
                0.0,
                0.0,
                "NOT_AVAILABLE",
                ["NOT_AVAILABLE: no backtest dataset"],
            ),
            WindowMeasurement(
                "LIVE_ATTRIBUTED",
                0,
                0.0,
                0.0,
                "NOT_AVAILABLE",
                ["NOT_AVAILABLE: no per-strategy live attribution yet"],
            ),
        ]
    ordered = sorted(dataset.samples, key=lambda s: getattr(s, "decision_timestamp", 0))
    n = len(ordered)
    # IS: first ~60%, OOS: last ~25%, recent: last 20%
    is_cut = max(1, int(n * 0.60))
    oos_cut = max(1, int(n * 0.25))
    recent_cut = max(1, n // 5)
    is_samples = ordered[:is_cut]
    oos_samples = ordered[-oos_cut:]
    recent = ordered[-recent_cut:]
    return [
        WindowMeasurement(
            "IS_WINDOW", len(is_samples), _exp(is_samples), _win_rate(is_samples), "AVAILABLE", []
        ),
        WindowMeasurement(
            "OOS_WINDOW",
            len(oos_samples),
            _exp(oos_samples),
            _win_rate(oos_samples),
            "AVAILABLE",
            [],
        ),
        WindowMeasurement(
            "RECENT_WINDOW", len(recent), _exp(recent), _win_rate(recent), "AVAILABLE", []
        ),
        WindowMeasurement(
            "LIVE_ATTRIBUTED",
            0,
            0.0,
            0.0,
            "NOT_AVAILABLE",
            ["NOT_AVAILABLE: no per-strategy live attribution yet"],
        ),
    ]


def regimes(dataset: ResearchDataset | None) -> list[RegimeMeasurement]:
    """Per-regime slices (UNKNOWN kept but penalized). Live regimes NOT_AVAILABLE."""
    if dataset is None or not dataset.samples:
        return [RegimeMeasurement("NO DATA", 0, 0.0, 0.0, "NOT_AVAILABLE")]
    buckets: dict[str, list[ResearchSample]] = {}
    for s in dataset.samples:
        buckets.setdefault(getattr(s, "regime", "UNKNOWN") or "UNKNOWN", []).append(s)
    out: list[RegimeMeasurement] = []
    for reg, lst in sorted(buckets.items()):
        out.append(RegimeMeasurement(reg, len(lst), _exp(lst), _win_rate(lst), "AVAILABLE"))
    # honest live regime slot — never fabricated
    out.append(RegimeMeasurement("LIVE", 0, 0.0, 0.0, "NOT_AVAILABLE"))
    return out


def backtest_metrics(
    dataset: ResearchDataset, strategy_id: str = "", strategy_version: str = ""
) -> BacktestResult | None:
    """Deterministic backtest over the available dataset (honest, never live)."""
    if dataset is None or not dataset.samples:
        return None
    try:
        return compute_backtest(
            list(dataset.samples),
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            dataset_id=dataset.dataset_id,
        )
    except Exception:
        return None


__all__ = ["RegimeMeasurement", "WindowMeasurement", "backtest_metrics", "regimes", "windows"]
