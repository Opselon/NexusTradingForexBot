"""
Context Analysis — Multi-dimensional Performance & Matrix Slicing
================================================================
PHASE 25 (2026-08-25).
Computes session, hourly, weekday, and regime performance matrices from
ResearchSample objects.
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.research.models import ResearchSample


def _compute_metrics(samples: list[ResearchSample]) -> dict[str, Any]:
    count = len(samples)
    if count == 0:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "expectancy_r": 0.0,
            "avg_r": 0.0,
            "max_dd_r": 0.0,
            "sample_count": 0,
        }
    wins = sum(1 for s in samples if s.realized_r > 0.0)
    losses = sum(1 for s in samples if s.realized_r < 0.0)
    total_r = sum(s.realized_r for s in samples)
    expectancy_r = total_r / count
    win_rate = wins / count if count > 0 else 0.0
    avg_r = expectancy_r

    peak = 0.0
    current_eq = 0.0
    max_dd = 0.0
    for s in samples:
        current_eq += s.realized_r
        peak = max(peak, current_eq)
        dd = peak - current_eq
        max_dd = max(max_dd, dd)

    return {
        "trades": count,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 4),
        "expectancy_r": round(expectancy_r, 4),
        "avg_r": round(avg_r, 4),
        "max_dd_r": round(max_dd, 4),
        "sample_count": count,
    }


def compute_context_matrices(samples: list[ResearchSample]) -> dict[str, Any]:
    """Computes session, hourly, weekday, and regime performance matrices."""
    session_buckets: dict[str, list[ResearchSample]] = {}
    hourly_buckets: dict[int, list[ResearchSample]] = {h: [] for h in range(24)}
    weekday_buckets: dict[int, list[ResearchSample]] = {w: [] for w in range(7)}
    regime_buckets: dict[tuple[str, str, str], list[ResearchSample]] = {}

    for s in samples:
        sess = str(getattr(s, "session", "ALL") or "ALL")
        session_buckets.setdefault(sess, []).append(s)

        dt = s.decision_timestamp
        hour = dt.hour
        hourly_buckets[hour].append(s)

        weekday = dt.weekday()
        weekday_buckets[weekday].append(s)

        trend = str(getattr(s, "trend_state", "NEUTRAL") or "NEUTRAL")
        vol = str(getattr(s, "volatility_regime", "NORMAL") or "NORMAL")
        liq = str(getattr(s, "liquidity_regime", "") or getattr(s, "regime", "NORMAL") or "NORMAL")
        regime_key = (trend, vol, liq)
        regime_buckets.setdefault(regime_key, []).append(s)

    session_matrix = {k: _compute_metrics(v) for k, v in session_buckets.items()}
    hourly_matrix = {str(h): _compute_metrics(v) for h, v in hourly_buckets.items()}
    weekday_matrix = {str(w): _compute_metrics(v) for w, v in weekday_buckets.items()}

    regime_matrix = {
        f"{t}|{v}|{l}": _compute_metrics(v_samples)
        for (t, v, l), v_samples in regime_buckets.items()
    }

    return {
        "session_matrix": session_matrix,
        "hourly_matrix": hourly_matrix,
        "weekday_matrix": weekday_matrix,
        "regime_matrix": regime_matrix,
    }
