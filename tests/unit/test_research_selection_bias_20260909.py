"""EDGE ROUND-2 (2026-09-09): economic OOS floor + selection-bias controls.

Contracts pinned here:

  1. The OOS gate DEFAULT floor is now MIN_ECONOMIC_OOS_EXPECTANCY_R = 0.02R
     (an edge below the friction-noise scale is not a tradable edge). The
     legacy constant MIN_OOS_EXPECTANCY_R = 0.0 is preserved; an EXPLICIT
     OOSGate(min_oos_expectancy_r=0.0) restores the old semantics.
  2. Deflated Sharpe Ratio (Bailey-de Prado): a strong edge survives heavy
     multiplicity; a weak edge mined from many trials is deflated away.
  3. White Reality Check (SPA): the best of a pure-luck family set is not a
     survivor; a family with a real edge is.
  4. Scoring verdict: VALIDATED requires the multiplicity controls to pass
     WHEN the gate attached them; legacy results without the fields keep the
     previous contract (additive, no fabricated metrics).
  5. The full pipeline threads n_trials into the run snapshot (provenance).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from nexus_scalp.research.metrics import (
    MIN_OOS_SIGNIFICANCE_SAMPLES,
    deflated_sharpe_ratio,
    oos_significance,
    sharpe_ratio_r,
    spa_family_pvalue,
)
from nexus_scalp.research.models import (
    BacktestResult,
    OOSResult,
    ResearchDataset,
    ResearchSample,
    RobustnessResult,
    WalkForwardResult,
)
from nexus_scalp.research.oos import (
    MIN_ECONOMIC_OOS_EXPECTANCY_R,
    MIN_OOS_EXPECTANCY_R,
    OOSGate,
)
from nexus_scalp.research.scoring import (
    DSR_CONFIDENCE_FLOOR,
    _oos_evidence_is_decisive,
    _selection_bias_control_passed,
    compute_strategy_score,
)


def _mk_samples(n: int, r_win: float = 0.6, frac: float = 0.6) -> list[ResearchSample]:
    base = datetime(2026, 8, 1, tzinfo=UTC)
    out: list[ResearchSample] = []
    for i in range(n):
        win = i % 10 < int(frac * 10)
        out.append(
            ResearchSample(
                sample_id=f"s{i}",
                experience_id=f"e{i}",
                idempotency_key=f"k{i}",
                decision_timestamp=base + timedelta(minutes=i * 10),
                outcome_timestamp=base + timedelta(minutes=i * 10 + 7),
                symbol="XAUUSD",
                strategy_id="S",
                strategy_version="1.0.0",
                regime="LONDON",
                entry_price=2000.0,
                stop_loss=1998.0,
                take_profit=2006.0,
                direction="BUY",
                realized_r=r_win if win else -0.9,
                realized_pnl_usd=12.0 if win else -18.0,
                risk_distance=2.0,
                holding_duration_sec=420.0,
                mae_r=0.2,
                mfe_r=0.8,
            )
        )
    return out


def _passing_backtest() -> BacktestResult:
    return BacktestResult(
        strategy_id="S",
        strategy_version="1.0.0",
        dataset_id="D",
        total_trades=50,
        wins=30,
        losses=20,
        expectancy_r=0.5,
    )


# ---------------------------------------------------------------------------
# 1. Economic floor: default tightened, legacy explicit path preserved
# ---------------------------------------------------------------------------


def test_economic_floor_is_positive_and_legacy_constant_preserved() -> None:
    assert MIN_OOS_EXPECTANCY_R == 0.0  # legacy public constant untouched
    assert MIN_ECONOMIC_OOS_EXPECTANCY_R == pytest.approx(0.02)
    assert MIN_ECONOMIC_OOS_EXPECTANCY_R > MIN_OOS_EXPECTANCY_R
    assert OOSGate().min_oos_expectancy_r == pytest.approx(MIN_ECONOMIC_OOS_EXPECTANCY_R)
    assert OOSGate(min_oos_expectancy_r=0.0).min_oos_expectancy_r == 0.0


def _sub_economic_dataset() -> ResearchDataset:
    """120 samples whose FINAL 20% (the OOS window) averages exactly +0.01R —
    positive, but below the 0.02R economic floor."""
    samples = _mk_samples(120, r_win=0.62, frac=0.545)
    oos_start = 96  # last 20% of 120
    for j, s in enumerate(samples[oos_start:]):
        # The gate measures FRICTION-ADJUSTED R (~0.025R consumed by the
        # default spread+slip at risk_distance 2.0); raw +0.645/-0.575
        # alternation leaves an adjusted mean of ~ +0.01R.
        r = 0.645 if j % 2 == 0 else -0.575
        samples[oos_start + j] = s.model_copy(
            update={"realized_r": r, "realized_pnl_usd": r * 20.0}
        )
    return ResearchDataset(dataset_id="D", samples=samples)


def test_default_gate_rejects_sub_economic_oos_edge() -> None:
    """An OOS edge BELOW the economic floor (0.01R) must FAIL by default —
    this is exactly the breakeven-noise case the old 0.0 floor let through."""
    ds = _sub_economic_dataset()
    res = OOSGate().evaluate(ds, "S", "1.0.0")
    assert 0.0 < res.oos_expectancy_r < MIN_ECONOMIC_OOS_EXPECTANCY_R
    assert res.status == "FAIL"


def test_explicit_legacy_gate_still_passes_same_edge() -> None:
    # Same dataset through the legacy explicit floor: unchanged verdict.
    ds = _sub_economic_dataset()
    res = OOSGate(min_oos_expectancy_r=0.0).evaluate(ds, "S", "1.0.0")
    assert res.status == "PASS"


def test_healthy_edge_passes_the_default_gate() -> None:
    ds = ResearchDataset(dataset_id="D", samples=_mk_samples(120, r_win=0.9, frac=0.75))
    res = OOSGate().evaluate(ds, "S", "1.0.0")
    assert res.status == "PASS"
    assert res.oos_expectancy_r >= MIN_ECONOMIC_OOS_EXPECTANCY_R


# ---------------------------------------------------------------------------
# 2. Deflated Sharpe Ratio
# ---------------------------------------------------------------------------


def test_dsr_strong_edge_survives_heavy_multiplicity() -> None:
    strong = [0.8] * 60 + [-0.5] * 20
    out = deflated_sharpe_ratio(strong, 200)
    assert out["dsr"] >= DSR_CONFIDENCE_FLOOR
    # Monotonic: more trials never increase the DSR.
    assert deflated_sharpe_ratio(strong, 1000)["dsr"] <= out["dsr"]


def test_dsr_weak_edge_deflated_away_by_multiplicity() -> None:
    weak = [0.05] * 50 + [-0.04] * 50
    few = deflated_sharpe_ratio(weak, 5)
    many = deflated_sharpe_ratio(weak, 200)
    assert many["dsr"] < few["dsr"]  # multiplicity hurts
    assert many["dsr"] < DSR_CONFIDENCE_FLOOR  # ...and kills the weak edge


def test_dsr_deterministic_and_bounded() -> None:
    vals = [0.3, -0.1, 0.5, 0.2, -0.3] * 20
    a = deflated_sharpe_ratio(vals, 50)
    b = deflated_sharpe_ratio(vals, 50)
    assert a == b
    assert 0.0 <= a["dsr"] <= 1.0


def test_sharpe_ratio_r_basic() -> None:
    assert sharpe_ratio_r([0.5] * 10) == 0.0  # zero variance -> 0, never inf
    pos = sharpe_ratio_r([0.8] * 60 + [-0.5] * 20)
    assert pos > 0


# ---------------------------------------------------------------------------
# 3. White Reality Check (SPA-style family test)
# ---------------------------------------------------------------------------


def test_spa_pure_luck_families_are_not_survivors() -> None:
    rng = np.random.default_rng(11)
    families = [list(rng.normal(0.0, 0.5, 100)) for _ in range(40)]
    out = spa_family_pvalue(families)
    assert out["n_families"] == 40
    assert out["survivor"] is False
    assert out["p_value"] > 0.05


def test_spa_detects_real_edge_buried_in_noise() -> None:
    rng = np.random.default_rng(7)
    noise = [list(rng.normal(0.0, 0.5, 120)) for _ in range(50)]
    real = list(rng.normal(0.35, 0.5, 120))
    out = spa_family_pvalue([*noise, real])
    assert out["survivor"] is True
    assert out["p_value"] <= 0.05


def test_spa_is_deterministic() -> None:
    rng = np.random.default_rng(3)
    families = [list(rng.normal(0.05, 0.4, 80)) for _ in range(10)]
    assert spa_family_pvalue(families) == spa_family_pvalue(families)


# ---------------------------------------------------------------------------
# 4. Gate attachment + verdict integration
# ---------------------------------------------------------------------------


def test_gate_attaches_dsr_when_trials_declared() -> None:
    ds = ResearchDataset(dataset_id="D", samples=_mk_samples(120, r_win=0.9, frac=0.75))
    res = OOSGate().evaluate(ds, "S", "1.0.0", n_trials=10)
    assert res.deflated_sharpe is not None
    assert res.deflated_sharpe["n_trials"] == 10
    # SPA absent when no family lists supplied (nothing fabricated).
    assert res.spa is None


def test_gate_skips_dsr_for_single_trial_runs() -> None:
    ds = ResearchDataset(dataset_id="D", samples=_mk_samples(120, r_win=0.9, frac=0.75))
    res = OOSGate().evaluate(ds, "S", "1.0.0")
    assert res.deflated_sharpe is None  # n_trials undeclared -> no deflation


def test_verdict_rejects_failing_dsr_but_keeps_legacy() -> None:
    ds = ResearchDataset(dataset_id="D", samples=_mk_samples(120))
    bt = _passing_backtest()
    wf_pass = WalkForwardResult(strategy_id="S", strategy_version="1", dataset_id="D", passed=True)
    rob_pass = RobustnessResult(strategy_id="S", strategy_version="1", status="PASS")

    # Legacy: no DSR/SPA fields at all -> old contract.
    legacy = OOSResult(
        strategy_id="S",
        strategy_version="1",
        dataset_id="D",
        status="PASS",
        oos_expectancy_r=0.4,
    )
    assert _selection_bias_control_passed(legacy) is True

    # Mined candidate with deflated-away edge -> blocked with a reason.
    mined_bad = OOSResult(
        strategy_id="S",
        strategy_version="1",
        dataset_id="D",
        status="PASS",
        oos_expectancy_r=0.3,
        oos_significance={
            "n": MIN_OOS_SIGNIFICANCE_SAMPLES + 5,
            "mean_r": 0.3,
            "ci_low": 0.1,
            "ci_high": 0.5,
            "decisive": True,
        },
        deflated_sharpe={"sr": 0.11, "dsr": 0.12, "n": 50, "n_trials": 200},
    )
    assert _selection_bias_control_passed(mined_bad) is False

    # Mined candidate whose edge survives deflation -> accepted.
    mined_good = OOSResult(
        strategy_id="S",
        strategy_version="1",
        dataset_id="D",
        status="PASS",
        oos_expectancy_r=0.4,
        oos_significance={"n": 50, "mean_r": 0.4, "ci_low": 0.2, "ci_high": 0.6, "decisive": True},
        deflated_sharpe={"sr": 0.84, "dsr": 0.997, "n": 80, "n_trials": 200},
    )
    assert _selection_bias_control_passed(mined_good) is True

    score = compute_strategy_score(ds, bt, wf_pass, mined_bad, rob_pass)
    assert score.verdict == "INCONCLUSIVE"
    assert any("selection-bias" in r for r in score.reasons)


def test_verdict_rejects_failing_spa() -> None:
    ds = ResearchDataset(dataset_id="D", samples=_mk_samples(120))
    bt = _passing_backtest()
    wf_pass = WalkForwardResult(strategy_id="S", strategy_version="1", dataset_id="D", passed=True)
    rob_pass = RobustnessResult(strategy_id="S", strategy_version="1", status="PASS")
    spa_fail = OOSResult(
        strategy_id="S",
        strategy_version="1",
        dataset_id="D",
        status="PASS",
        oos_expectancy_r=0.3,
        oos_significance={"n": 50, "mean_r": 0.3, "ci_low": 0.1, "ci_high": 0.5, "decisive": True},
        spa={
            "n_families": 120,
            "best_mean_r": 0.3,
            "p_value": 0.31,
            "alpha": 0.05,
            "survivor": False,
        },
    )
    assert _selection_bias_control_passed(spa_fail) is False
    score = compute_strategy_score(ds, bt, wf_pass, spa_fail, rob_pass)
    assert score.verdict == "INCONCLUSIVE"
    assert any("Reality Check" in r for r in score.reasons)


def test_oos_significance_still_decisive_baseline() -> None:
    # The round-1 significance contract is unchanged by round-2 additions.
    strong = oos_significance([0.8] * 60 + [-0.5] * 20)
    assert strong["decisive"] is True
    weak = oos_significance([0.05, -0.05] * 40)
    assert weak["decisive"] is False
