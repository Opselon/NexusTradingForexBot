"""Feature-block importance harness (Phase 4A) — NEWS block permutation importance.

Reproducible, evidence-first evaluation over the canonical 70D dataset:
measures OOS predictive contribution of each feature BLOCK (base / news /
liquidity) via block-ablation (retrain-light: permutation of the block's
columns in the OOS split, model unchanged) rather than training-set
importance. No thresholds invented; results are MEASUREMENTS the mission
requires before any policy/gate change.

Design notes:
  * uses the canonical dataset artifact (artifacts/model_generation/datasets)
    and the champion bundle when available,
  * protected OOS: evaluates ONLY on the test slice per the dataset manifest
    (temporal end; purge/embargo already applied at build),
  * permutation nulls the BLOCK's information without changing vector width —
    the 70D contract (INV-009) is never violated, the model sees in-distribution
    marginals per column but destroyed joint structure,
  * outputs a JSON evidence artifact under artifacts/forensics/.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Feature-block geometry (canonical scalp_v3; features/schema_contract.py).
BLOCKS: dict[str, tuple[int, int]] = {
    "base": (0, 50),
    "news": (50, 60),
    "liquidity": (60, 70),
}

#: RNG seed for reproducible permutations (mission: repeatable evaluation).
PERMUTATION_SEED: int = 20260907


@dataclass(frozen=True)
class BlockImportanceResult:
    block: str
    columns: tuple[int, int]
    metric_baseline: float
    metric_permuted: float
    delta: float  # baseline - permuted (positive => block contributes)
    n_test_rows: int
    repeats: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "block": self.block,
            "columns": list(self.columns),
            "metric_baseline": round(self.metric_baseline, 6),
            "metric_permuted": round(self.metric_permuted, 6),
            "delta": round(self.delta, 6),
            "n_test_rows": self.n_test_rows,
            "repeats": self.repeats,
        }


def _load_dataset(dataset_dir: Path) -> tuple[Any, Any, list[str]]:
    """Loads (train_df, test_df, feature_columns) from a dataset artifact."""
    import polars as pl

    df = pl.read_parquet(dataset_dir / "dataset.parquet")
    with (dataset_dir / "dataset_manifest.json").open(encoding="utf-8") as fh:
        manifest = json.load(fh)
    counts = manifest.get("row_counts", {})
    train_n = int(counts.get("train", 0))
    val_n = int(counts.get("val", 0))
    # test = last slice (dataset is temporally ordered + purged at build)
    test_df = df.slice(train_n + val_n, df.height - train_n - val_n)
    train_df = df.head(train_n)
    feature_columns = [c for c in df.columns if c.startswith("feat_")]
    return train_df, test_df, feature_columns


def _fit_block_model(
    train_df: Any,
    feature_columns: list[str],
    label_column: str,
    seed: int = PERMUTATION_SEED,
) -> Any:
    """Fits a small deterministic classifier on the train slice for attribution.

    Deliberately NOT the production ScalpNet: the harness answers 'does the
    BLOCK carry predictive information the model can use' with a fast,
    deterministic, retrain-light probe. Uses a logistic-regression baseline
    implemented in numpy when sklearn is unavailable (the repo does not
    depend on sklearn — mission rule 11: no new dependencies for research
    tooling). Champion untouched (no promotion surface).
    """
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier

        X = train_df.select(feature_columns).to_numpy()
        y = train_df.select(label_column).to_numpy().ravel()
        return HistGradientBoostingClassifier(
            max_iter=150,
            max_depth=4,
            learning_rate=0.1,
            random_state=seed,
        ).fit(X, y)
    except ImportError:
        return _fit_logistic_numpy(train_df, feature_columns, label_column)


def _fit_logistic_numpy(train_df: Any, feature_columns: list[str], label_column: str) -> Any:
    """Deterministic class-balanced logistic regression (numpy-only).

    Inverse-frequency sample weights are REQUIRED: the triple-barrier label
    is ~88% NO_TRADE, so an unweighted probe collapses to the majority class
    and every block-ablation delta reads 0.0 (measured 2026-09-07). The
    balanced probe actually learns minority BUY/SELL structure, which is
    what an attribution harness must be sensitive to.
    """
    import numpy as np

    X = np.asarray(train_df.select(feature_columns).to_numpy(), dtype=np.float64)
    y = np.asarray(train_df.select(label_column).to_numpy().ravel())
    classes, counts = np.unique(y, return_counts=True)
    weight_map = {c: len(y) / (len(classes) * cnt) for c, cnt in zip(classes, counts)}
    sample_w = np.array([weight_map[c] for c in y], dtype=np.float64)
    sample_w /= sample_w.mean()
    k = len(classes)
    Y = np.zeros((len(y), k))
    for idx, c in enumerate(classes):
        Y[y == c, idx] = 1.0
    mu = X.mean(axis=0)
    sd = X.std(axis=0) + 1e-9
    Xs = (X - mu) / sd
    W = np.zeros((Xs.shape[1], k))
    b = np.zeros(k)
    lr = 1.0
    for _ in range(600):
        logits = Xs @ W + b
        logits -= logits.max(axis=1, keepdims=True)
        P = np.exp(logits)
        P /= P.sum(axis=1, keepdims=True)
        G = (P - Y) * sample_w[:, None] / len(y)
        W -= lr * (Xs.T @ G + 1e-4 * W)
        b -= lr * G.sum(axis=0)
    return ("numpy_logistic", W, b, mu, sd, classes)


def _accuracy(model: Any, X: Any, y: Any) -> float:
    import numpy as np

    if isinstance(model, tuple) and model and model[0] == "numpy_logistic":
        _, W, b, mu, sd, classes = model
        Xs = (np.asarray(X, dtype=np.float64) - mu) / sd
        logits = Xs @ W + b
        pred_idx = np.argmax(logits, axis=1)
        pred = classes[pred_idx]
        return float((pred == np.asarray(y)).mean())
    pred = np.asarray(model.predict(X))
    return float((pred == np.asarray(y)).mean())


def evaluate_block_importance(
    dataset_dir: str | Path,
    *,
    label_column: str = "label",
    repeats: int = 3,
    seed: int = PERMUTATION_SEED,
) -> dict[str, Any]:
    """Runs the protected-OOS block ablation; returns the evidence dict."""

    dataset_dir = Path(dataset_dir)
    train_df, test_df, feature_columns = _load_dataset(dataset_dir)
    rng = random.Random(seed)
    X_test = test_df.select(feature_columns).to_numpy()
    y_test = test_df.select(label_column).to_numpy().ravel()
    model = _fit_block_model(train_df, feature_columns, label_column, seed)
    baseline = _accuracy(model, X_test, y_test)

    results: list[BlockImportanceResult] = []
    for block, (lo, hi) in BLOCKS.items():
        if hi > len(feature_columns):
            continue  # block absent in this dataset width (e.g. 50D)
        permuted_scores: list[float] = []
        for _ in range(repeats):
            Xp = X_test.copy()
            for col in range(lo, hi):
                perm = list(range(Xp.shape[0]))
                rng.shuffle(perm)
                Xp[:, col] = Xp[perm, col]
            permuted_scores.append(_accuracy(model, Xp, y_test))
        permuted = sum(permuted_scores) / len(permuted_scores)
        results.append(
            BlockImportanceResult(
                block=block,
                columns=(lo, hi),
                metric_baseline=baseline,
                metric_permuted=permuted,
                delta=baseline - permuted,
                n_test_rows=X_test.shape[0],
                repeats=repeats,
            )
        )
    return {
        "harness_version": "block_importance_v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": dataset_dir.name,
        "metric": "accuracy (protected OOS test slice)",
        "method": "block permutation ablation (model fixed, block columns permuted)",
        "seed": seed,
        "baseline_accuracy": round(baseline, 6),
        "blocks": [r.to_dict() for r in results],
    }


def save_evidence(payload: dict[str, Any], out_dir: str | Path = "artifacts/forensics") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"news_block_importance_{datetime.now(UTC).strftime('%Y%m%d')}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
