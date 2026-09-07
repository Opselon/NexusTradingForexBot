"""Statistical promotion gate tests (money-path PHASE 7B).

Pins the paired moving-block bootstrap CI contract of
ChampionChallengerComparator: a challenger whose mean-R edge over the
champion is not statistically distinguishable from noise can never be
promotion-eligible on the comparison alone.
"""

from __future__ import annotations

import random

from nexus_scalp.model_lifecycle.comparison import (
    MIN_STAT_SAMPLES,
    ChampionChallengerComparator,
    _paired_bootstrap_mean_delta_ci,
)


def _challenger_dict(r_list: list[float], **extra) -> dict:
    base = {
        "model_id": "cand_test",
        "model_version": "1.0.0",
        "expectancy_r": 0.2,
        "max_drawdown_r": 2.0,
        "oos_expectancy_r": 0.15,
        "tail_loss_count": 0,
        "robustness_status": "PASS",
        "stability": 0.9,
    }
    base.update(extra)
    if r_list is not None:
        base["r_list"] = r_list
    return base


def _champion_dict(r_list: list[float], **extra) -> dict:
    base = {
        "model_id": "champ_test",
        "model_version": "1.0.0",
        "expectancy_r": 0.1,
        "max_drawdown_r": 2.0,
        "oos_expectancy_r": 0.1,
        "tail_loss_count": 0,
        "robustness_status": "PASS",
        "stability": 0.9,
    }
    base.update(extra)
    if r_list is not None:
        base["r_list"] = r_list
    return base


def test_bootstrap_ci_bounds_and_determinism() -> None:
    rng = random.Random(7)
    challenger = [rng.gauss(0.15, 1.0) for _ in range(400)]
    champion = [rng.gauss(0.05, 1.0) for _ in range(400)]
    a = _paired_bootstrap_mean_delta_ci(challenger, champion)
    b = _paired_bootstrap_mean_delta_ci(challenger, champion)
    assert a["sufficient"] == 1.0
    assert a["n"] == 400
    assert a["ci_low"] <= a["mean_delta"] <= a["ci_high"]
    assert a == b, "bootstrap must be deterministic (seeded)"


def test_insufficient_samples_marks_inconclusive() -> None:
    a = _paired_bootstrap_mean_delta_ci([0.1] * 10, [0.0] * 10)
    assert a["sufficient"] == 0.0


def test_noise_edge_is_rejected_by_statistical_gate() -> None:
    """Challenger ahead by a coin-flip margin on identical distributions ->
    CI includes 0 -> NOT eligible (a point estimate alone is not evidence)."""
    rng = random.Random(11)
    # Per-trade differentials are zero-mean noise; the challenger's apparent
    # +0.04R "edge" is the sample mean of pure noise, well inside its CI.
    noise = [rng.gauss(0.0, 1.0) for _ in range(300)]
    challenger = [rng.gauss(0.1, 1.0) for _ in range(300)]
    champion = [c - d for c, d in zip(challenger, noise, strict=True)]
    # mean(champion) = 0.1 - mean(noise) ≈ 0.1 - 0.04 → apparent edge ≈ +0.04R
    result = ChampionChallengerComparator().compare(
        champion=_champion_dict(champion),
        challenger=_challenger_dict(challenger),
        run_id="stat_test_1",
    )
    assert result.eligible is False
    assert any("statistical gate" in r for r in result.reasons)


def test_real_edge_passes_statistical_gate() -> None:
    """A challenger with a genuine large edge has CI strictly above 0."""
    rng = random.Random(5)
    champion = [rng.gauss(0.05, 0.5) for _ in range(300)]
    challenger = [x + 0.8 for x in champion]  # dominant real edge
    result = ChampionChallengerComparator().compare(
        champion=_champion_dict(champion),
        challenger=_challenger_dict(challenger),
        run_id="stat_test_2",
    )
    stat_reasons = [r for r in result.reasons if "statistical gate FAILED" in r]
    assert not stat_reasons
    assert result.eligible is True


def test_min_stat_samples_constant_is_sane() -> None:
    assert MIN_STAT_SAMPLES == 30
