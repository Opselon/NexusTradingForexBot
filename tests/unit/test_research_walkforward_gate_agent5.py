"""Regression tests — Agent 5 forensic pass (research gates).

D1 (BUG-244 part A): walk_forward_folds accepted purge/embargo defaults of
   0.0 — a caller relying on library defaults silently ran an unguarded
   walk-forward (the BUG-183 class reappearing one layer deeper: the four
   production gates were wired, but the shared fold producer still leaked by
   default).
D2 (BUG-244 part B): WalkForwardEngine.validate stamped fold status=PASS from
   VALIDATION expectancy only; a fold whose recorded OOS expectancy is
   NEGATIVE was reported PASS, and scoring honored walkforward.passed — so
   VALIDATED verdicts could rest on folds whose own out-of-sample window lost
   money (observed on all three real VALIDATED registry rows, 2026-09-05).
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

from nexus_scalp.research.models import ResearchDataset, ResearchSample
from nexus_scalp.research.splitting import (
    DEFAULT_EMBARGO_SECONDS,
    DEFAULT_PURGE_SECONDS,
    walk_forward_folds,
)
from nexus_scalp.research.walkforward import WalkForwardEngine

T0 = datetime(2026, 8, 1, tzinfo=UTC)


def _sig_default(fn, name: str) -> float:
    return float(inspect.signature(fn).parameters[name].default)


def _mk_sample(i: int, r: float, strategy_id: str = "probe") -> ResearchSample:
    dec = T0 + timedelta(minutes=i)
    return ResearchSample(
        sample_id=f"s{i}",
        experience_id=f"e{i}",
        idempotency_key=f"k{i}",
        decision_timestamp=dec,
        outcome_timestamp=dec + timedelta(minutes=5),
        symbol="XAUUSD",
        timeframe="M1",
        strategy_id=strategy_id,
        strategy_version="1",
        feature_schema_id="scalp_v1",
        feature_dimension=50,
        regime="TRENDING",
        session="LDN",
        volatility_regime="NORMAL",
        trend_state="UP",
        feature_hash="h",
        context_fingerprint="cf",
        entry_price=3300.0,
        stop_loss=3299.0,
        take_profit=3302.0,
        direction="BUY",
        realized_r=r,
        realized_pnl_usd=r * 10.0,
        risk_distance=1.0,
        holding_duration_sec=60,
        mae_r=0.0,
        mfe_r=0.0,
        exit_reason="TP" if r >= 0 else "SL",
    )


# ---------------------------------------------------------------------------
# D1: the shared fold producer must default to the SSOT leakage constants.
# ---------------------------------------------------------------------------


def test_walk_forward_folds_default_to_splitting_leakage_constants() -> None:
    assert _sig_default(walk_forward_folds, "purge_seconds") == DEFAULT_PURGE_SECONDS
    assert _sig_default(walk_forward_folds, "embargo_seconds") == DEFAULT_EMBARGO_SECONDS


def test_walk_forward_folds_default_purge_removes_boundary_crossing_horizon() -> None:
    # 90 samples, block = 90 // 5 = 18. Fold 1: train = 0..18, val = 18..36.
    # A train sample deciding just before the boundary with a 5-minute outcome
    # horizon CROSSES it -> with purge on (default) it must be purged from train.
    samples = [_mk_sample(i, 0.1) for i in range(90)]
    ds = ResearchDataset(dataset_id="purge_ds", samples=samples)
    folds = walk_forward_folds(ds, n_splits=3)
    assert folds, "dataset must produce folds"
    f1 = folds[0]
    boundary = f1.val_start
    crossing = [
        s for s in f1.train if s.decision_timestamp <= boundary <= s.outcome_timestamp
    ]
    assert crossing == [], (
        "boundary-crossing horizons must be purged from train by default"
    )


# ---------------------------------------------------------------------------
# D2: a fold with a NEGATIVE OOS expectancy must never be stamped PASS.
# ---------------------------------------------------------------------------


def _dataset_with_negative_fold_oos() -> ResearchDataset:
    # n=120, block=24. Layout:
    #   fold1: train 0..24   val 24..48  oos 48..72
    #   fold2: train 0..48   val 48..72  oos 72..96
    #   fold3: train 0..72   val 72..96  oos 96..120
    # Validation windows positive (so folds would pass under the OLD
    # val-only rule); the fold-3-only OOS window strongly negative.
    samples = []
    for i in range(120):
        in_val = 24 <= i < 96
        in_oos3 = 96 <= i < 120
        if in_val:
            r = +1.0
        elif in_oos3:
            r = -1.2
        else:
            r = +0.2
        samples.append(_mk_sample(i, r))
    return ResearchDataset(dataset_id="neg_oos_ds", samples=samples)


def test_negative_oos_fold_is_never_stamped_pass() -> None:
    ds = _dataset_with_negative_fold_oos()
    res = WalkForwardEngine().validate(ds, "neg_oos", "1")
    neg_pass = [f for f in res.folds if f.oos_expectancy_r < 0 and f.status == "PASS"]
    assert neg_pass == [], (
        "a fold whose own OOS expectancy is negative must be FAIL, "
        f"got PASS on folds {[f.fold for f in neg_pass]}"
    )


def test_walkforward_result_cannot_pass_when_majority_of_oos_windows_negative() -> None:
    ds = _dataset_with_negative_fold_oos()
    res = WalkForwardEngine().validate(ds, "neg_oos", "1")
    neg_oos_folds = [f for f in res.folds if f.oos_expectancy_r < 0]
    if len(neg_oos_folds) * 2 > len(res.folds):
        assert not res.passed, (
            "a majority-negative OOS fold set must fail the walk-forward gate"
        )


def test_positive_folds_still_pass_normally() -> None:
    # Guard the fix against over-tightening: healthy folds (val>0, oos>=0)
    # must still be PASS and the engine must report passed=True.
    samples = [_mk_sample(i, 0.8) for i in range(120)]
    ds = ResearchDataset(dataset_id="pos_ds", samples=samples)
    res = WalkForwardEngine().validate(ds, "pos", "1")
    assert res.passed
    assert all(f.status == "PASS" for f in res.folds)
