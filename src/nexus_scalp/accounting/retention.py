"""Retention analytics — evidence-based winner-profit-retention metrics.

BUG-081 (Defect #3): winners give back almost all of their MFE without the
system measuring it. These functions quantify MFE capture / giveback per
trade and across a cohort so any future retention-policy change is driven by
measured evidence, never by hardcoded thresholds.

Statistical honesty rule (mirrors `accounting/aggregation.py`): every metric
is None when the sample cannot support it. MFE <= 0 explicitly yields None
capture (a trade can never "capture" a fraction of a non-positive excursion).
"""

from __future__ import annotations

from collections.abc import Sequence

_EPS: float = 1e-9


def mfe_capture_ratio(*, realized_profit: float, mfe: float) -> float | None:
    """realized_profit / max(MFE, eps) for a profitable excursion.

    None when MFE <= 0 (there is nothing to capture — never a synthetic 0.0).
    """
    if mfe <= 0.0:
        return None
    return realized_profit / max(mfe, _EPS)


def giveback(*, mfe: float, realized_profit: float) -> float | None:
    """MFE - realized_profit (how much peak profit was given back)."""
    if mfe <= 0.0:
        return None
    return mfe - realized_profit


def giveback_ratio(*, mfe: float, realized_profit: float) -> float | None:
    """(MFE - realized) / max(MFE, eps); None when MFE <= 0."""
    if mfe <= 0.0:
        return None
    return (mfe - realized_profit) / max(mfe, _EPS)


def cohort_capture_report(
    records: Sequence[tuple[float, float]],
) -> dict[str, float | int | None]:
    """Aggregates (realized_profit, mfe) pairs into a cohort retention report.

    Args:
        records: sequence of (realized_profit, mfe) per completed trade.

    Returns:
        {
            sample_trades, profitable_trades, avg_capture_ratio,
            median_capture_ratio (via sorted middle), avg_giveback,
            avg_giveback_ratio, total_mfe, total_realized, worst_capture_ratio
        }
    """
    out: dict[str, float | int | None] = {
        "sample_trades": len(records),
        "profitable_trades": 0,
        "avg_capture_ratio": None,
        "median_capture_ratio": None,
        "avg_giveback": None,
        "avg_giveback_ratio": None,
        "total_mfe": None,
        "total_realized": None,
        "worst_capture_ratio": None,
    }
    ratios = [
        r
        for (realized, mfe) in records
        if (r := mfe_capture_ratio(realized_profit=realized, mfe=mfe)) is not None
    ]
    givebacks = [
        g
        for (realized, mfe) in records
        if (g := giveback(mfe=mfe, realized_profit=realized)) is not None
    ]
    gb_ratios = [
        g
        for (realized, mfe) in records
        if (g := giveback_ratio(mfe=mfe, realized_profit=realized)) is not None
    ]
    out["profitable_trades"] = sum(1 for realized, _ in records if realized > 0.0)
    total_mfe = sum(mfe for _, mfe in records if mfe > 0.0)
    total_realized = sum(realized for realized, _ in records)
    if ratios:
        out["avg_capture_ratio"] = round(sum(ratios) / len(ratios), 4)
        out["worst_capture_ratio"] = round(min(ratios), 4)
        ordered = sorted(ratios)
        mid = len(ordered) // 2
        out["median_capture_ratio"] = round(
            ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0, 4
        )
    if givebacks:
        out["avg_giveback"] = round(sum(givebacks) / len(givebacks), 2)
    if gb_ratios:
        out["avg_giveback_ratio"] = round(sum(gb_ratios) / len(gb_ratios), 4)
    if total_mfe > 0.0:
        out["total_mfe"] = round(total_mfe, 2)
        out["total_realized"] = round(total_realized, 2)
    return out
