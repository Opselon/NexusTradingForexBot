"""Economic fold metric tests (research/training-parity P1).

The walk-forward fold evaluation must carry a genuine money-side metric:
net expectancy in R (gross expectancy minus friction), computed from the
SAME triple-barrier outcomes the classification metrics use.

Contract:
  * net R uses the configured friction consistently (gross - friction).
  * accuracy cannot masquerade as economics: a fold with high trade
    accuracy and an all-NO_TRADE (zero-trade) fold are distinguished, and
    a 100%-accuracy-but-no-trades model scores 0.0 economic expectancy.
  * the proxy 'sharpe' is derived from the same proxy returns and is
    reported with its friction assumption — never presented as measured.
  * economics travel on the run's convergence metadata + manifest extra.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from nexus_scalp.features.scalp_features import FEATURE_NAMES
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer


def _frame(n: int = 600, seed: int = 13) -> pl.DataFrame:
    rng = np.random.RandomState(seed)
    data = {name: rng.randn(n).tolist() for name in FEATURE_NAMES}
    labels = ["NO_TRADE", "BUY_MARKET", "SELL_MARKET"]
    data["label"] = [labels[i % 3] for i in range(n)]
    data["label_evaluated"] = [True] * n
    data["is_purged"] = [False] * n
    return pl.DataFrame(data)


def test_economics_net_r_applies_friction_consistently() -> None:
    tr = WalkForwardTrainer(friction_r=0.15, reward_r=1.2)
    # 4 trades: 2 correct (+1.2R each), 2 wrong (-1R each), friction 0.15R.
    preds = [1, 2, 1, 2, 0, 0, 0, 0]
    targets = np.array([1, 2, 2, 1, 0, 0, 0, 0], dtype=np.int64)
    econ = tr._calculate_fold_economics(preds, targets)
    assert econ["trades"] == 4
    assert econ["win_rate"] == pytest.approx(0.5)
    # Gross: (2*1.2 - 2*1.0)/4 = 0.10R. Net: 0.10 - 0.15 = -0.05R — a
    # positive-accuracy-looking fold can be economically negative.
    assert econ["gross_expectancy_r"] == pytest.approx(0.10)
    assert econ["net_expectancy_r"] == pytest.approx(0.10 - 0.15)
    assert econ["friction_r"] == 0.15
    assert econ["no_trade_rate"] == pytest.approx(0.5)


def test_zero_trade_fold_scores_zero_economics() -> None:
    """A model that never trades has NO economic evidence — 0.0 expectancy,
    NOT a free pass, and no fabricated trades."""
    tr = WalkForwardTrainer()
    preds = [0] * 50
    targets = np.array([1, 2] * 25, dtype=np.int64)
    econ = tr._calculate_fold_economics(preds, targets)
    assert econ["trades"] == 0
    assert econ["net_expectancy_r"] == 0.0
    assert econ["no_trade_rate"] == 1.0


def test_accuracy_cannot_masquerade_as_economics() -> None:
    """Perfect classification accuracy on an all-NO_TRADE fold yields exactly
    zero economic expectancy — accuracy and money are different measures."""
    tr = WalkForwardTrainer()
    perfect_no_trade = [0] * 100
    targets = np.array([0] * 100, dtype=np.int64)
    acc = float(np.mean(np.array(perfect_no_trade) == targets))  # 100%
    econ = tr._calculate_fold_economics(perfect_no_trade, targets)
    assert acc == 1.0
    assert econ["net_expectancy_r"] == 0.0
    assert econ["trades"] == 0


def test_legacy_sharpe_proxy_name_returns_proxy_only() -> None:
    """The deprecated name keeps working (callers/tests) and equals the
    proxy_sharpe_ratio field of the economics block."""
    tr = WalkForwardTrainer()
    preds = [1, 2, 1, 0]
    targets = np.array([1, 2, 2, 0], dtype=np.int64)
    legacy = tr._calculate_fold_sharpe_proxy(preds, targets)
    econ = tr._calculate_fold_economics(preds, targets)
    assert legacy == pytest.approx(econ["proxy_sharpe_ratio"])


def test_economics_travel_on_run_metadata(tmp_path) -> None:
    tr = WalkForwardTrainer(
        num_folds=3,
        epochs_per_fold=1,
        min_rows_per_train_split=50,
        min_rows_per_test_split=20,
        artifact_save_path=tmp_path / "econ" / "m.pt",
        smoke=True,
        governance_override=True,
        friction_r=0.2,
    )
    tr.train_and_validate(_frame(), FEATURE_NAMES)
    meta = tr.last_convergence_metadata
    assert meta["friction_r"] == 0.2
    folds = meta["fold_economics"]
    assert len(folds) == 3
    for f in folds:
        # Per-fold: net == gross - friction (both sign-consistent).
        assert f["net_expectancy_r"] == pytest.approx(f["gross_expectancy_r"] - f["friction_r"])
        assert 0.0 <= f["win_rate"] <= 1.0
        assert f["max_drawdown_r"] >= 0.0
    assert meta["net_expectancy_r"] == pytest.approx(
        float(np.mean([f["net_expectancy_r"] for f in folds]))
    )
    assert meta["max_fold_drawdown_r"] >= 0.0


def test_friction_assumption_is_configurable() -> None:
    cheap = WalkForwardTrainer(friction_r=0.05)
    pricey = WalkForwardTrainer(friction_r=0.45)
    preds = [1, 2, 1, 2]
    targets = np.array([1, 2, 1, 2], dtype=np.int64)
    e_cheap = cheap._calculate_fold_economics(preds, targets)
    e_pricey = pricey._calculate_fold_economics(preds, targets)
    assert e_cheap["net_expectancy_r"] > e_pricey["net_expectancy_r"]
    assert e_cheap["gross_expectancy_r"] == e_pricey["gross_expectancy_r"]
