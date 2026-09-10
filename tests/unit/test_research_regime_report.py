"""EDGE ROUND-5 (2026-09-09): per-regime expectancy report (calibration data).

The report must agree with the scorer's decomposition (same consistency x
breadth math, same UNKNOWN discount), sort regimes by |mean_r| so the
regimes that matter most surface first, and flag losing regimes explicitly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.research.models import ResearchDataset, ResearchSample
from nexus_scalp.research.regime_report import (
    per_regime_expectancy_report,
    render_regime_report,
)
from nexus_scalp.research.scoring import _regime_expectancy_coverage


def _sample(idx: int, regime: str, r: float) -> ResearchSample:
    base = datetime(2026, 8, 1, tzinfo=UTC)
    return ResearchSample(
        sample_id=f"s{idx}",
        experience_id=f"e{idx}",
        idempotency_key=f"k{idx}",
        decision_timestamp=base + timedelta(minutes=idx * 10),
        outcome_timestamp=base + timedelta(minutes=idx * 10 + 7),
        symbol="XAUUSD",
        strategy_id="S",
        strategy_version="1.0.0",
        regime=regime,
        entry_price=2000.0,
        stop_loss=1998.0,
        direction="BUY",
        realized_r=r,
        realized_pnl_usd=r * 20.0,
        risk_distance=2.0,
        holding_duration_sec=300.0,
        mae_r=0.2,
        mfe_r=0.8,
    )


def _dataset(rows: list[tuple[str, float]]) -> ResearchDataset:
    samples = [_sample(i, reg, r) for i, (reg, r) in enumerate(rows)]
    return ResearchDataset(dataset_id="D", samples=samples)


def test_report_agrees_with_scorer_coverage() -> None:
    ds = _dataset([("LONDON", 0.5)] * 30 + [("NY", 0.2)] * 30)
    rep = per_regime_expectancy_report(ds)
    cov, _ = _regime_expectancy_coverage(ds)
    assert rep["regime_coverage"] == pytest.approx(cov)


def test_rows_sorted_by_absolute_mean_r() -> None:
    ds = _dataset([("WEAK", 0.1)] * 20 + [("STRONG", 0.9)] * 20)
    rep = per_regime_expectancy_report(ds)
    assert rep["rows"][0]["regime"] == "STRONG"


def test_losing_and_unknown_flags() -> None:
    ds = _dataset([("LONDON", 0.5)] * 20 + [("UNKNOWN", -0.3)] * 10)
    rep = per_regime_expectancy_report(ds)
    by = {r["regime"]: r for r in rep["rows"]}
    assert by["UNKNOWN"]["consistent"] is False
    assert by["UNKNOWN"]["unknown_provenance"] is True
    assert "UNKNOWN" in rep["negative_regimes"]
    assert by["LONDON"]["consistent"] is True


def test_win_rate_and_share_math() -> None:
    ds = _dataset([("LONDON", 0.5)] * 15 + [("LONDON", -0.5)] * 5 + [("NY", 0.2)] * 20)
    rep = per_regime_expectancy_report(ds)
    by = {r["regime"]: r for r in rep["rows"]}
    # London: 15 wins out of 20; share = 20/40
    assert by["LONDON"]["n"] == 20
    assert by["LONDON"]["win_rate"] == pytest.approx(0.75)
    assert by["LONDON"]["share_of_trades"] == pytest.approx(0.5)
    assert by["NY"]["share_of_trades"] == pytest.approx(0.5)


def test_render_contains_flags_and_regimes() -> None:
    ds = _dataset([("LONDON", 0.5)] * 10 + [("NY", -0.4)] * 10)
    text = render_regime_report(ds)
    assert "REGIME REPORT" in text
    assert "[OK ] LONDON" in text
    assert "[NEG] NY" in text
