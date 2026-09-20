"""ML-FEAT-002 — Feature Importance, Collinearity Clustering & Redundancy Pruning.

WHY THIS EXISTS
---------------
The live ``scalp_v1`` contract feeds 50 features to ScalpNet on every bar.  Several
families are constructed from the same underlying quantities (three EMA
distances, three lag returns, four HTF trend columns, two Ichimoku distance
columns) and are therefore plausible collinearity candidates.  Redundant
inputs inflate the parameter count of a small scalping model and promote
overfitting on a sample size that is already constrained by the triple-barrier
labeler's purge/embargo stride.

This module computes the empirical evidence the repo previously had no
artifact for: which features carry out-of-sample signal, and which are
statistically redundant neighbours of a stronger feature.

DESIGN CONTRACT (see ``docs/ml-system/tasks/ML-FEAT-002.md``)
-------------------------------------------------------------
* **Pure NumPy.** The analysis layer must run in the slim Linux verification
  venv, which has numpy + polars but no scipy/sklearn/torch.  Spearman rank
  correlation, mutual information and the surrogate model are therefore
  implemented locally rather than imported from scikit-learn.
* **Read-only on the schema.** ``scalp_v1`` / 50D is the ACTIVE live contract
  (``features/schema.py``).  Nothing here deletes, reorders or renames a
  feature — NON_GOAL of the task.  The output is an *audit* that a future
  schema version would cite, not a mutation of the live vector.
* **No free lunch in the loss.** Permutation Feature Importance requires a
  fitted model.  When no trained checkpoint is supplied, the analysis fits a
  deterministic ridge-regularised linear surrogate (closed-form, seed-pinned)
  on the training split only, then measures the *validation* loss increase
  when each feature column is shuffled.  A PFI computed on the same data the
  model was fitted on is optimistic; the train/val split enforced here is
  what makes the "out-of-sample" claim in the task objective true.
* **Deterministic.** Every shuffle uses a seeded generator; repeated runs on
  the same frame produce byte-identical rankings, so the audit artifact is
  reproducible and citable in a governance record.

INVARIANTS
----------
1. ``compute_correlation_matrix`` is symmetric with a unit diagonal and is
   computed on rank-transformed (Spearman) values so monotonic non-linear
   relationships are captured, not just linear ones.
2. ``cluster_collinear_features`` returns disjoint clusters — a feature joins
   the cluster of the *strongest* correlated earlier feature, so a redundant
   column is never the cluster representative.
3. ``compute_permutation_importance`` never permutes a constant column
   (permuting it is a no-op that would report a spurious zero-variance
   reading) and reports it as NaN so the audit can name it explicitly.
4. Analysis functions take the feature matrix by name; they do not import the
   live feature engine, so an audit cannot accidentally touch the tick path.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from nexus_scalp.features.scalp_features import FEATURE_NAMES
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.features.importance")

#: Task SCOPE threshold: pairs above this absolute Spearman correlation are
#: "collinear".  The task body asks for > 0.85 clustering; the WHY_IT_EXISTS
#: paragraph cites > 0.90 as the overfitting risk band.  0.85 is the stricter
#: contract, so it is the default.
COLLINEARITY_THRESHOLD: float = 0.85

#: Default ridge penalty for the closed-form linear PFI surrogate.  Small and
#: fixed: the surrogate only has to rank features, not predict well.
_RIDGE_ALPHA: float = 1e-3

#: Default permutation repeats.  More repeats narrow the PFI confidence band
#: at linear cost; the task benchmark uses 5.
DEFAULT_PERMUTATION_REPEATS: int = 5


@dataclass(frozen=True)
class CollinearityCluster:
    """One group of statistically redundant features.

    ``representative`` is the member that survived the prune — the first
    member of the group in *importance order*, not index order, so the
    retained feature is the one with the strongest measured signal.
    """

    cluster_id: int
    representative: str
    members: tuple[str, ...]
    max_abs_correlation: float

    @property
    def redundant_members(self) -> tuple[str, ...]:
        """Members other than the representative — the pruning candidates."""
        return tuple(m for m in self.members if m != self.representative)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "representative": self.representative,
            "members": list(self.members),
            "redundant_members": list(self.redundant_members),
            "max_abs_correlation": round(float(self.max_abs_correlation), 6),
        }


@dataclass(frozen=True)
class CollinearPair:
    """A single feature pair whose absolute Spearman rho exceeds the threshold."""

    feature_a: str
    feature_b: str
    spearman_rho: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_a": self.feature_a,
            "feature_b": self.feature_b,
            "abs_spearman_rho": round(float(abs(self.spearman_rho)), 6),
            "spearman_rho": round(float(self.spearman_rho), 6),
        }


@dataclass(frozen=True)
class ImportanceReport:
    """Complete ML-FEAT-002 analysis result over one evaluation frame."""

    feature_names: tuple[str, ...]
    spearman_matrix: np.ndarray
    mutual_information: np.ndarray
    permutation_importance: np.ndarray
    permutation_std: np.ndarray
    clusters: tuple[CollinearityCluster, ...]
    collinear_pairs: tuple[CollinearPair, ...]
    constant_features: tuple[str, ...]
    correlation_threshold: float
    n_samples: int
    model_description: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def n_features(self) -> int:
        return len(self.feature_names)

    def importance_ranking(self) -> list[dict[str, Any]]:
        """Features sorted by mean permutation importance, descending.

        NaN (constant-column) readings sort last so the audit table always
        names them, never silently drops them.
        """
        rows: list[dict[str, Any]] = []
        for idx, name in enumerate(self.feature_names):
            score = float(self.permutation_importance[idx])
            rows.append(
                {
                    "rank": 0,
                    "feature": name,
                    "feature_index": idx,
                    "mean_permutation_importance": score,
                    "std_permutation_importance": float(self.permutation_std[idx]),
                    "mutual_information": float(self.mutual_information[idx]),
                }
            )
        rows.sort(
            key=lambda r: (
                math.isnan(r["mean_permutation_importance"]),
                -r["mean_permutation_importance"],
            )
        )
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
        return rows

    def prune_recommendation(self) -> list[dict[str, Any]]:
        """One row per redundant feature: what to drop and what it duplicates."""
        out: list[dict[str, Any]] = []
        for cluster in self.clusters:
            for redundant in cluster.redundant_members:
                idx = self.feature_names.index(redundant)
                out.append(
                    {
                        "drop_feature": redundant,
                        "feature_index": idx,
                        "keep_feature": cluster.representative,
                        "cluster_max_abs_correlation": round(float(cluster.max_abs_correlation), 6),
                        "mean_permutation_importance": float(self.permutation_importance[idx]),
                    }
                )
        out.sort(key=lambda r: -float(r["cluster_max_abs_correlation"]))
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": "ML-FEAT-002",
            "feature_schema_id": self.metadata.get("feature_schema_id", "scalp_v1"),
            "n_features": self.n_features,
            "n_samples": self.n_samples,
            "correlation_threshold": self.correlation_threshold,
            "model": self.model_description,
            "constant_features": list(self.constant_features),
            "top_10_alpha_drivers": [
                {k: v for k, v in row.items() if k != "rank"}
                for row in self.importance_ranking()[:10]
            ],
            "collinear_pairs": [p.to_dict() for p in self.collinear_pairs],
            "clusters": [c.to_dict() for c in self.clusters],
            "prune_recommendation": self.prune_recommendation(),
            "full_ranking": self.importance_ranking(),
            "metadata": self.metadata,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str, sort_keys=False)


# ==============================================================================
# Correlation
# ==============================================================================


def _rank_columns(matrix: np.ndarray) -> np.ndarray:
    """Average-rank transform of each column (the Spearman pre-transform).

    Ties get the average of the ranks they span, matching the standard
    Spearman definition.  A constant column maps to an all-ones rank column,
    which is the value that makes it report zero correlation against
    everything rather than NaN-ing the whole matrix.
    """
    out = np.empty_like(matrix, dtype=np.float64)
    n = matrix.shape[0]
    for j in range(matrix.shape[1]):
        col = matrix[:, j]
        order = np.argsort(col, kind="stable")
        sorted_vals = col[order]
        ranks_sorted = np.arange(n, dtype=np.float64) + 1.0
        # Average the ranks spanned by each tied run, in sorted order.
        run_start = 0
        while run_start < n:
            run_end = run_start
            while run_end + 1 < n and sorted_vals[run_end + 1] == sorted_vals[run_start]:
                run_end += 1
            if run_end > run_start:
                ranks_sorted[run_start : run_end + 1] = float(
                    np.mean(ranks_sorted[run_start : run_end + 1])
                )
            run_start = run_end + 1
        column_ranks = np.empty(n, dtype=np.float64)
        column_ranks[order] = ranks_sorted
        out[:, j] = column_ranks
    return out


def _pearson(matrix: np.ndarray) -> np.ndarray:
    """Pearson correlation of already-centred-ish columns, with NaN guards."""
    n = matrix.shape[0]
    if n < 2:
        return np.zeros((matrix.shape[1], matrix.shape[1]))
    centred = matrix - matrix.mean(axis=0, keepdims=True)
    cov = (centred.T @ centred) / float(n - 1)
    std = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    denom = np.outer(std, std)
    corr = np.where(denom > 0, cov / np.where(denom > 0, denom, 1.0), 0.0)
    np.fill_diagonal(corr, 1.0)
    # Numerical clamp: rho must stay in [-1, 1].
    return np.clip(corr, -1.0, 1.0)


def compute_correlation_matrix(
    features: np.ndarray,
    feature_names: tuple[str, ...] | list[str] | None = None,
    method: str = "spearman",
) -> np.ndarray:
    """Spearman (default) or Pearson correlation matrix across all features.

    Spearman is the default because the task's INVESTIGATION_PLAN explicitly
    asks for relationships "that Pearson correlation misses": rank correlation
    captures any monotonic non-linear mapping, which is the shape of most
    indicator-to-price relationships (ratios, distances, sigmoid-like flags).

    Args:
        features: ``(n_samples, n_features)`` float matrix.
        feature_names: optional, used only for the shape assertion message.
        method: ``"spearman"`` (rank transform first) or ``"pearson"``.

    Returns:
        Symmetric ``(n_features, n_features)`` matrix, unit diagonal, clamped
        to ``[-1, 1]``.  Constant columns correlate 0 with everything else.
    """
    if features.ndim != 2:
        raise ValueError(f"features must be 2-D, got shape {features.shape}")
    if features.shape[0] < 2:
        raise ValueError(f"need at least 2 samples to correlate, got {features.shape[0]}")
    if feature_names is not None and len(feature_names) != features.shape[1]:
        raise ValueError(
            f"feature_names length {len(feature_names)} != feature matrix width {features.shape[1]}"
        )
    if method not in ("spearman", "pearson"):
        raise ValueError(f"method must be 'spearman' or 'pearson', got {method!r}")

    clean = np.nan_to_num(features.astype(np.float64, copy=False), nan=0.0, posinf=0.0, neginf=0.0)
    if method == "spearman":
        clean = _rank_columns(clean)
    return _pearson(clean)


# ==============================================================================
# Mutual information (numpy-only, no scikit-learn)
# ==============================================================================


def _discretize(values: np.ndarray, bins: int) -> np.ndarray:
    """Equal-width binning to integers, the standard MI quantisation for reals."""
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return np.zeros(len(values), dtype=np.int64)
    lo = float(finite.min())
    hi = float(finite.max())
    if hi <= lo:
        return np.zeros(len(values), dtype=np.int64)
    scaled = (values - lo) / (hi - lo)
    scaled = np.clip(np.nan_to_num(scaled, nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0)
    idx = np.floor(scaled * bins).astype(np.int64)
    return np.clip(idx, 0, bins - 1)


def _entropy(counts: np.ndarray, total: float) -> float:
    nz = counts[counts > 0]
    if nz.size == 0:
        return 0.0
    p = nz / total
    return float(-np.sum(p * np.log(p)))


def compute_mutual_information(
    features: np.ndarray,
    labels: np.ndarray,
    bins: int = 16,
) -> np.ndarray:
    """Mutual information I(feature_j; label), numpy-only.

    Real-valued features are equal-width binned (``bins`` per feature) and
    MI is computed from the joint count table against the discrete label.
    This is the same estimator scikit-learn's ``mutual_info_classif`` uses
    under the hood, reimplemented here because the slim verification venv
    carries no scikit-learn.

    Constant columns yield MI = 0 exactly (a degenerate variable carries no
    information about anything), which is the property the audit relies on
    when naming dead features.
    """
    if features.ndim != 2:
        raise ValueError(f"features must be 2-D, got shape {features.shape}")
    if features.shape[0] != labels.shape[0]:
        raise ValueError(
            f"sample mismatch: features {features.shape[0]} vs labels {labels.shape[0]}"
        )
    if bins < 2:
        raise ValueError(f"bins must be >= 2, got {bins}")

    n = features.shape[0]
    y = np.asarray(labels)
    classes = np.unique(y)
    h_y = _entropy(np.bincount(np.searchsorted(classes, y), minlength=len(classes)), float(n))

    mi = np.zeros(features.shape[1], dtype=np.float64)
    for j in range(features.shape[1]):
        col = features[:, j].astype(np.float64, copy=False)
        if not np.all(np.isfinite(col)):
            col = np.nan_to_num(col, nan=0.0, posinf=0.0, neginf=0.0)
        std = col.std()
        if std == 0:
            mi[j] = 0.0  # constant column: no information
            continue
        x_binned = _discretize(col, bins)
        y_binned = np.searchsorted(classes, y).astype(np.int64)
        joint = np.bincount(x_binned * len(classes) + y_binned, minlength=bins * len(classes))
        joint = joint.reshape(bins, len(classes))
        h_xy = _entropy(joint.ravel(), float(n))
        h_x = _entropy(joint.sum(axis=1), float(n))
        # I(X;Y) = H(X) + H(Y) - H(X,Y); floored at 0 (estimator noise).
        mi[j] = max(h_x + h_y - h_xy, 0.0)
    return mi


# ==============================================================================
# Collinearity clustering
# ==============================================================================


def find_collinear_pairs(
    correlation_matrix: np.ndarray,
    feature_names: tuple[str, ...] | list[str],
    threshold: float = COLLINEARITY_THRESHOLD,
) -> list[CollinearPair]:
    """Every unordered pair whose ``|rho|`` exceeds ``threshold``.

    Returns pairs sorted by descending ``|rho|`` — the worst redundancy first.
    """
    if correlation_matrix.ndim != 2 or correlation_matrix.shape[0] != correlation_matrix.shape[1]:
        raise ValueError(f"correlation_matrix must be square, got {correlation_matrix.shape}")
    if len(feature_names) != correlation_matrix.shape[0]:
        raise ValueError(
            f"feature_names length {len(feature_names)} != matrix width {correlation_matrix.shape[0]}"
        )
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be in [0, 1], got {threshold}")

    pairs: list[CollinearPair] = []
    n = correlation_matrix.shape[0]
    for i in range(n):
        for j in range(i + 1, n):
            rho = float(correlation_matrix[i, j])
            if abs(rho) > threshold:
                pairs.append(
                    CollinearPair(
                        feature_a=feature_names[i],
                        feature_b=feature_names[j],
                        spearman_rho=rho,
                    )
                )
    pairs.sort(key=lambda p: -abs(p.spearman_rho))
    return pairs


def cluster_collinear_features(
    correlation_matrix: np.ndarray,
    feature_names: tuple[str, ...] | list[str],
    importance: np.ndarray | None = None,
    threshold: float = COLLINEARITY_THRESHOLD,
) -> list[CollinearityCluster]:
    """Hierarchical single-linkage clustering of collinear features.

    Walks the correlated pairs in descending ``|rho|`` order and merges their
    features union-find style.  Single linkage is the correct semantics here:
    a chain ``A--B--C`` where each link clears the threshold is genuinely one
    redundant family (an EMA-distance ladder is exactly this shape), and
    complete linkage would split it into incoherent fragments.

    ``importance`` (any higher-is-better score, typically PFI) selects the
    cluster representative: the member with the highest importance keeps its
    slot and the rest become pruning candidates.  Without it the
    representative is the lowest-index member, which would keep the weaker
    feature whenever the redundant family was added later in the schema's
    life.  Clusters are returned in descending ``max_abs_correlation`` order.
    """
    if correlation_matrix.ndim != 2 or correlation_matrix.shape[0] != correlation_matrix.shape[1]:
        raise ValueError(f"correlation_matrix must be square, got {correlation_matrix.shape}")
    if len(feature_names) != correlation_matrix.shape[0]:
        raise ValueError(
            f"feature_names length {len(feature_names)} != matrix width {correlation_matrix.shape[0]}"
        )
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be in [0, 1], got {threshold}")
    if importance is not None and len(importance) != len(feature_names):
        raise ValueError(
            f"importance length {len(importance)} != feature_names length {len(feature_names)}"
        )

    pairs = find_collinear_pairs(correlation_matrix, feature_names, threshold)
    parent = list(range(len(feature_names)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for pair in pairs:
        ra, rb = (
            find(feature_names.index(pair.feature_a)),
            find(feature_names.index(pair.feature_b)),
        )
        if ra != rb:
            parent[rb] = ra

    groups: dict[int, list[int]] = {}
    for idx in range(len(feature_names)):
        groups.setdefault(find(idx), []).append(idx)

    clusters: list[CollinearityCluster] = []
    for cluster_id, (_, indices) in enumerate(sorted(groups.items()), start=1):
        if len(indices) < 2:
            continue  # singleton: no redundancy to report
        if importance is not None:
            ranked = sorted(indices, key=lambda i: (np.isnan(importance[i]), -importance[i]))
        else:
            ranked = sorted(indices)
        representative_idx = ranked[0]
        sub = correlation_matrix[np.ix_(indices, indices)]
        # Max off-diagonal |rho| inside the group = the group's redundancy grade.
        off = sub.copy()
        np.fill_diagonal(off, 0.0)
        max_rho = float(np.max(np.abs(off))) if sub.size > 1 else 0.0
        clusters.append(
            CollinearityCluster(
                cluster_id=cluster_id,
                representative=feature_names[representative_idx],
                members=tuple(feature_names[i] for i in ranked),
                max_abs_correlation=max_rho,
            )
        )
    clusters.sort(key=lambda c: -c.max_abs_correlation)
    # Re-number after sorting so cluster ids read in severity order.
    return [
        CollinearityCluster(
            cluster_id=i,
            representative=c.representative,
            members=c.members,
            max_abs_correlation=c.max_abs_correlation,
        )
        for i, c in enumerate(clusters, start=1)
    ]


# ==============================================================================
# Permutation feature importance
# ==============================================================================


def _train_val_split(n: int, val_fraction: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic chronological split: the tail of the series is validation.

    Chronological, not random, because the underlying samples are a time
    series: a random split would leak the autocorrelated neighbours on both
    sides of the validation rows into the surrogate's fit and the measured
    "out-of-sample" loss drop would no longer be out-of-sample.
    """
    if not 0.0 < val_fraction < 1.0:
        raise ValueError(f"val_fraction must be in (0, 1), got {val_fraction}")
    n_val = max(1, round(n * val_fraction))
    n_val = min(n_val, n - 1)
    return np.arange(0, n - n_val), np.arange(n - n_val, n)


def _fit_ridge(x: np.ndarray, y: np.ndarray, alpha: float = _RIDGE_ALPHA) -> np.ndarray:
    """Closed-form ridge regression weights (no stochastic step, fully deterministic)."""
    n_feat = x.shape[1]
    a = x.T @ x + alpha * np.eye(n_feat)
    b = x.T @ y
    try:
        return np.linalg.solve(a, b)
    except np.linalg.LinAlgError:
        return np.linalg.lstsq(a, b, rcond=None)[0]


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max(axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / np.sum(exp, axis=1, keepdims=True)


def _surrogate_predict(x: np.ndarray, weights: np.ndarray, n_classes: int) -> np.ndarray:
    """One-vs-rest softmax over per-class ridge columns -> class probabilities."""
    if weights.ndim == 1:
        # Binary/multiclass stored as (n_features, n_classes).
        logits = x @ weights
    else:
        logits = x @ weights
    if logits.ndim == 1:
        logits = logits.reshape(-1, 1)
    if n_classes > 2:
        return _softmax(logits)
    # Binary: sigmoid on the single column.
    p = 1.0 / (1.0 + np.exp(-np.clip(logits[:, 0], -30, 30)))
    return np.column_stack([1.0 - p, p])


def _cross_entropy(probs: np.ndarray, y: np.ndarray, n_classes: int) -> float:
    idx = np.clip(y.astype(np.int64), 0, max(n_classes - 1, 0))
    picked = probs[np.arange(len(y)), idx]
    return float(-np.mean(np.log(np.clip(picked, 1e-12, None))))


def compute_permutation_importance(
    features: np.ndarray,
    labels: np.ndarray,
    feature_names: tuple[str, ...] | list[str] | None = None,
    model: Any = None,
    model_predict: Any = None,
    val_fraction: float = 0.3,
    repeats: int = DEFAULT_PERMUTATION_REPEATS,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """Out-of-sample permutation feature importance.

    Fits ``model`` (or the deterministic ridge surrogate when ``model`` is
    ``None``) on the training split, records the baseline validation loss,
    then shuffles each feature column *in the validation split only* and
    measures the loss increase.  A feature whose shuffling does not hurt
    validation performance carries no signal the model used.

    Args:
        features: ``(n_samples, n_features)`` matrix.
        labels: integer class labels, ``(n_samples,)``.
        feature_names: optional, for the width assertion message only.
        model: optional pre-fitted model.  When given, ``model_predict`` must
            map ``(matrix, n_classes) -> probabilities`` and the fit step is
            skipped (the caller asserts it was fitted on training data only).
        model_predict: callable used with ``model``.
        val_fraction: chronological validation share.
        repeats: permutation repeats per feature (mean+std reported).
        seed: shuffle seed — same seed, same result.

    Returns:
        ``(mean_importance, std_importance)`` per feature.  A constant column
        yields NaN for both: permuting it is a mathematical no-op, so a 0.0
        would be indistinguishable from "real zero importance" in the audit.
    """
    if features.ndim != 2:
        raise ValueError(f"features must be 2-D, got shape {features.shape}")
    if features.shape[0] != labels.shape[0]:
        raise ValueError(f"sample mismatch: {features.shape[0]} vs {labels.shape[0]}")
    if feature_names is not None and len(feature_names) != features.shape[1]:
        raise ValueError(
            f"feature_names length {len(feature_names)} != matrix width {features.shape[1]}"
        )
    if repeats < 1:
        raise ValueError(f"repeats must be >= 1, got {repeats}")

    x = np.nan_to_num(features.astype(np.float64, copy=False), nan=0.0, posinf=0.0, neginf=0.0)
    y = np.asarray(labels).astype(np.int64, copy=False)
    n_classes = int(np.max(y)) + 1
    if n_classes < 2:
        # A single-class frame cannot express a loss difference; report NaN
        # rather than a fabricate-all-zeros ranking.
        return np.full(features.shape[1], np.nan), np.full(features.shape[1], np.nan)

    train_idx, val_idx = _train_val_split(len(y), val_fraction, seed)
    x_train, y_train = x[train_idx], y[train_idx]
    x_val, y_val = x[val_idx], y[val_idx]

    # Standardise on TRAIN statistics only — fitting the scaler on the full
    # frame would leak validation distribution into the surrogate.
    mu = x_train.mean(axis=0, keepdims=True)
    sd = x_train.std(axis=0, keepdims=True)
    sd_safe = np.where(sd > 0, sd, 1.0)
    x_train_s = (x_train - mu) / sd_safe
    x_val_s = (x_val - mu) / sd_safe

    if model is None:
        # One-vs-rest ridge columns, fitted on the training split only.
        cols = []
        for cls in range(n_classes):
            target = (y_train == cls).astype(np.float64)
            cols.append(_fit_ridge(x_train_s, target))
        weights = np.column_stack(cols)
        model_description = (
            "deterministic one-vs-rest ridge surrogate (closed-form, alpha=1e-3, "
            "train-fitted, chronological 70/30 split, seed-pinned)"
        )
        probs = _surrogate_predict(x_val_s, weights, n_classes)
    else:
        if model_predict is None:
            raise ValueError("model_predict is required when model is supplied")
        probs = np.asarray(model_predict(x_val, n_classes), dtype=np.float64)
        model_description = "caller-supplied model"

    baseline = _cross_entropy(probs, y_val, n_classes)

    rng = np.random.default_rng(seed)
    n_feat = x.shape[1]
    std_col = x_val_s.std(axis=0)
    constant_mask = std_col == 0.0

    importance = np.full(n_feat, np.nan)
    importance_std = np.full(n_feat, np.nan)
    for j in range(n_feat):
        if constant_mask[j]:
            continue  # permuting a constant is a no-op -> NaN, not 0.0
        deltas = np.empty(repeats, dtype=np.float64)
        for rep in range(repeats):
            permuted = x_val_s.copy()
            permuted[:, j] = permuted[rng.permutation(len(permuted)), j]
            if model is None:
                p = _surrogate_predict(permuted, weights, n_classes)
            else:
                p = np.asarray(model_predict(permuted, n_classes), dtype=np.float64)
            deltas[rep] = _cross_entropy(p, y_val, n_classes) - baseline
        importance[j] = float(np.mean(deltas))
        importance_std[j] = float(np.std(deltas))

    logger.debug(
        "PFI complete",
        n_features=n_feat,
        n_train=len(train_idx),
        n_val=len(val_idx),
        baseline_loss=round(baseline, 6),
        n_constant=int(np.sum(constant_mask)),
        model=model_description,
    )
    # ``model_description`` is surfaced via the caller's ImportanceReport.
    compute_permutation_importance._last_model_description = model_description  # type: ignore[attr-defined]
    return importance, importance_std


# ==============================================================================
# Top-level analysis
# ==============================================================================


def analyze_features(
    features: np.ndarray,
    labels: np.ndarray,
    feature_names: tuple[str, ...] | list[str] | None = None,
    correlation_threshold: float = COLLINEARITY_THRESHOLD,
    model: Any = None,
    model_predict: Any = None,
    val_fraction: float = 0.3,
    permutation_repeats: int = DEFAULT_PERMUTATION_REPEATS,
    mi_bins: int = 16,
    seed: int = 42,
    metadata: dict[str, Any] | None = None,
) -> ImportanceReport:
    """Run the full ML-FEAT-002 battery over one evaluation frame.

    One pass computes the Spearman matrix, mutual information vs the label,
    out-of-sample permutation importance, and the collinearity cluster map.
    """
    if features.ndim != 2:
        raise ValueError(f"features must be 2-D, got shape {features.shape}")
    if feature_names is None:
        if features.shape[1] != len(FEATURE_NAMES):
            raise ValueError(
                f"matrix has {features.shape[1]} columns but no feature_names given and the "
                f"scalp_v1 contract declares {len(FEATURE_NAMES)}"
            )
        feature_names = FEATURE_NAMES
    if len(feature_names) != features.shape[1]:
        raise ValueError(
            f"feature_names length {len(feature_names)} != matrix width {features.shape[1]}"
        )
    names = tuple(feature_names)

    spearman = compute_correlation_matrix(features, names, method="spearman")
    mi = compute_mutual_information(features, labels, bins=mi_bins)
    importance, importance_std = compute_permutation_importance(
        features,
        labels,
        feature_names=names,
        model=model,
        model_predict=model_predict,
        val_fraction=val_fraction,
        repeats=permutation_repeats,
        seed=seed,
    )
    model_description = getattr(
        compute_permutation_importance, "_last_model_description", "deterministic ridge surrogate"
    )
    clusters = cluster_collinear_features(spearman, names, importance, correlation_threshold)
    pairs = find_collinear_pairs(spearman, names, correlation_threshold)

    clean = np.nan_to_num(features.astype(np.float64, copy=False), nan=0.0, posinf=0.0, neginf=0.0)
    constant = tuple(names[j] for j in range(len(names)) if float(clean[:, j].std()) == 0.0)

    return ImportanceReport(
        feature_names=names,
        spearman_matrix=spearman,
        mutual_information=mi,
        permutation_importance=importance,
        permutation_std=importance_std,
        clusters=tuple(clusters),
        collinear_pairs=tuple(pairs),
        constant_features=constant,
        correlation_threshold=correlation_threshold,
        n_samples=int(features.shape[0]),
        model_description=model_description,
        metadata=metadata or {},
    )


def save_report(
    report: ImportanceReport, json_path: Path | str, npz_path: Path | str | None = None
) -> None:
    """Persist the audit: JSON for humans/governance, optional NPZ for matrices.

    The NPZ carries the dense Spearman / MI / PFI arrays, which are the
    "correlation matrix artifact in artifacts/research/" the task's
    EVIDENCE_REQUIRED block asks for.
    """
    json_path = Path(json_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(report.to_json(), encoding="utf-8")
    if npz_path is not None:
        npz_path = Path(npz_path)
        npz_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            npz_path,
            feature_names=np.array(list(report.feature_names), dtype=object),
            spearman_matrix=report.spearman_matrix,
            mutual_information=report.mutual_information,
            permutation_importance=report.permutation_importance,
            permutation_importance_std=report.permutation_std,
            correlation_threshold=report.correlation_threshold,
        )
    logger.info("feature-importance audit saved", json=str(json_path), npz=str(npz_path))
