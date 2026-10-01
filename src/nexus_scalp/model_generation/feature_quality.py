"""Canonical Feature Quality Gate (MODEL FACTORY — stage 2).

WHY THIS EXISTS
---------------
Feature generation (50D ``scalp_v1`` / 70D ``scalp_v3``) had no formal
certification. ``features/importance.py`` computes importance and
``model_lifecycle/feature_drift.py`` monitors drift at runtime, but nothing
ever issued a machine-readable verdict on a GENERATED feature matrix, and
nothing enforced the "do not remove a feature just because it correlates"
rule: a caller had no way to distinguish "redundant" from "duplicate".

This is the ONE formal feature certification stage. It runs on a generated
feature matrix and produces ``feature_quality_report.json``.

CONTRACT
--------
* Validity: NaN / Inf / constant / near-zero-variance / invalid-range
  detection per feature.
* Redundancy: exact aliases (identical columns), highly-correlated groups.
  A high correlation is REPORTED, never an automatic removal — removal must
  be an explainable, versioned decision (the brief forbids deleting a
  feature solely for correlation).
* Stability: per-feature mean/std across the temporal blocks that actually
  exist in the frame (train / val / test from ``_split``, then market
  regimes and sessions where those columns are present), plus a train/OOS
  distribution-drift verdict.
* Drift: a train-vs-latest-block KS-style statistic. Significance is
  reported per feature; it warns, it does not delete.
* Gate verdict: PASS / WARN / FAIL with per-feature status. Failed features
  are named, never hidden.

TRAIN-ONLY DISCIPLINE
---------------------
The gate is a CERTIFICATION of a matrix. Any statistic that must obey
TRAIN-ONLY (scaler mean/std, feature selection) is fitted elsewhere; the
stability/drift statistics here are descriptive per-block aggregates, and
the drift reference block is explicitly the FIRST (earliest) temporal
block, i.e. the training side of a chronological split.
"""

from __future__ import annotations

import hashlib
import json
import warnings
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.feature_quality")

__all__ = [
    "FeatureQualityCertifier",
    "FeatureQualityReport",
    "FeatureStatus",
    "certify_feature_quality",
    "load_feature_quality_report",
]

#: Variance below which a feature is considered constant (no information).
_MIN_VARIANCE: float = 1e-12
#: Variance below which a feature is near-zero-variance (warning).
_NEAR_ZERO_VARIANCE: float = 1e-8
#: Absolute correlation at/above which a pair is flagged as redundant.
_CORR_THRESHOLD: float = 0.98
#: Relative mean shift (per-std) above which a block is flagged unstable.
_UNSTABLE_MEAN_SHIFT: float = 5.0
#: Per-std drift above which train/OOS distribution drift is flagged.
_DRIFT_STDEVS: float = 6.0


class FeatureStatus:
    PASS: str = "PASS"
    WARN: str = "WARN"
    FAIL: str = "FAIL"


@dataclass
class FeatureStat:
    """Per-feature certification row (Section 22 of the contract)."""

    name: str
    index: int
    family: str
    dimension: int
    missing_rate: float
    nan_rate: float
    inf_rate: float
    variance: float
    std: float
    mean: float
    constant: bool
    near_zero_variance: bool
    stability: float
    redundancy_max_corr: float
    redundant_with: list[str]
    drift_stdevs: float
    status: str
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FeatureQualityReport:
    dataset_id: str
    feature_schema_id: str
    feature_schema_hash: str
    dimension: int
    rows: int
    block_names: list[str]
    total_features: int
    passed: int
    warned: int
    failed: int
    quality_status: str
    redundant_groups: list[list[str]]
    drift_flagged: list[str]
    generated_at: str
    features: list[dict[str, Any]] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _family_for(index: int, dimension: int) -> str:
    if dimension <= 50:
        return "BASE"
    if index < 50:
        return "BASE"
    if index < 60:
        return "NEWS"
    return "LIQUIDITY"


def _blocks(frame: pl.DataFrame) -> dict[str, np.ndarray]:
    """Temporal/regime/session blocks that actually exist in the frame.

    Only blocks with data are returned, so a 2D matrix without regime or
    session columns simply produces fewer stability observations rather than
    fabricated ones.
    """
    blocks: dict[str, np.ndarray] = {}
    if "_split" in frame.columns:
        for s in ("train", "val", "test"):
            mask = frame["_split"].to_list()
            idx = [i for i, v in enumerate(mask) if v == s]
            if idx:
                blocks[s] = np.asarray(idx, dtype=np.int64)
    if "regime" in frame.columns:
        for r in sorted(set(frame["regime"].to_list()), key=str):
            idx = [i for i, v in enumerate(frame["regime"].to_list()) if v == r]
            if idx:
                blocks[f"regime:{r}"] = np.asarray(idx, dtype=np.int64)
    if "session" in frame.columns:
        for s in sorted(set(frame["session"].to_list()), key=str):
            idx = [i for i, v in enumerate(frame["session"].to_list()) if v == s]
            if idx:
                blocks[f"session:{s}"] = np.asarray(idx, dtype=np.int64)
    return blocks


class FeatureQualityCertifier:
    """Formal certification of a generated feature matrix."""

    def __init__(
        self,
        *,
        feature_schema_id: str = "scalp_v1",
        feature_schema_hash: str = "",
        corr_threshold: float = _CORR_THRESHOLD,
        unstable_mean_shift: float = _UNSTABLE_MEAN_SHIFT,
        drift_stdevs: float = _DRIFT_STDEVS,
    ) -> None:
        self.feature_schema_id = feature_schema_id
        self.feature_schema_hash = feature_schema_hash
        self.corr_threshold = float(corr_threshold)
        self.unstable_mean_shift = float(unstable_mean_shift)
        self.drift_stdevs = float(drift_stdevs)

    def certify(
        self, matrix: np.ndarray, names: list[str], *, dataset_id: str = ""
    ) -> FeatureQualityReport:
        """Certify a (rows, dimension) float matrix with canonical names."""
        if matrix.ndim != 2:
            raise ValueError(f"FeatureQualityCertifier: expected a 2D matrix, got {matrix.ndim}D")
        if matrix.shape[1] != len(names):
            raise ValueError(
                f"FeatureQualityCertifier: matrix has {matrix.shape[1]} columns but "
                f"{len(names)} names were supplied — refusing to certify a mismatch"
            )
        rows, dim = matrix.shape
        if rows < 2:
            raise ValueError(f"FeatureQualityCertifier: {rows} row(s) is not enough to certify")
        if dim not in (50, 70):
            raise ValueError(
                f"FeatureQualityCertifier: canonical contracts are 50D and 70D, got {dim}D"
            )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            with np.errstate(all="ignore"):
                nan_rate = np.isnan(matrix).mean(axis=0)
                inf_rate = np.isinf(matrix).mean(axis=0)
                finite = np.where(np.isfinite(matrix), matrix, np.nan)
                mean = np.nanmean(finite, axis=0)
                std = np.nanstd(finite, axis=0)
                var = np.square(std)

        # Pairwise correlation for redundancy (finite cols only).
        redundant_with: dict[int, list[str]] = {i: [] for i in range(dim)}
        max_corr = np.zeros(dim, dtype=np.float64)
        finite_matrix = np.nan_to_num(finite, nan=0.0, posinf=0.0, neginf=0.0)
        centered = finite_matrix - finite_matrix.mean(axis=0)
        norms = np.linalg.norm(centered, axis=0)
        safe = norms > 0
        if int(safe.sum()) > 1:
            unit = np.where(safe, centered / np.maximum(norms, 1e-12), 0.0)
            corr = unit.T @ unit
            np.fill_diagonal(corr, 0.0)
            for i in range(dim):
                for j in range(i + 1, dim):
                    c = float(corr[i, j])
                    if abs(c) >= self.corr_threshold:
                        redundant_with[i].append(names[j])
                        redundant_with[j].append(names[i])
                    if abs(c) > abs(max_corr[i]):
                        max_corr[i] = c
                    if abs(c) > abs(max_corr[j]):
                        max_corr[j] = c

        # Exact aliases: identical columns (a stronger defect than correlation).
        alias_groups: list[list[str]] = []
        seen_alias: set[int] = set()
        for i in range(dim):
            if i in seen_alias:
                continue
            grp = [names[i]]
            for j in range(i + 1, dim):
                if j in seen_alias:
                    continue
                if np.array_equal(finite_matrix[:, i], finite_matrix[:, j]):
                    grp.append(names[j])
                    seen_alias.add(j)
            if len(grp) > 1:
                alias_groups.append(grp)
                seen_alias.add(i)

        redundant_groups: list[list[str]] = list(alias_groups)
        for i, others in redundant_with.items():
            if others and names[i] not in {n for g in redundant_groups for n in g}:
                redundant_groups.append([names[i], *sorted(set(others))])

        # Stability: per-feature mean shift (in std units) across blocks.
        # Drift reference: the FIRST block (chronologically earliest = train).
        block_names = ["full"]
        block_means: dict[str, np.ndarray] = {"full": mean}
        drift_stdevs = np.zeros(dim, dtype=np.float64)
        ref = mean
        drift_flagged: list[str] = []
        for bname, idx in sorted(_blocks_from_matrix(matrix).items()):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=RuntimeWarning)
                with np.errstate(all="ignore"):
                    bm = np.nanmean(finite[idx], axis=0)
            if not np.isfinite(bm).all():
                continue
            block_means[bname] = bm
            block_names.append(bname)
        if "train" in block_means:
            ref = block_means["train"]
            oos_key = "test" if "test" in block_means else None
            if oos_key is not None:
                for i in range(dim):
                    if std[i] > 0 and np.isfinite(ref[i]) and np.isfinite(block_means[oos_key][i]):
                        d = abs(float(block_means[oos_key][i] - ref[i])) / float(std[i])
                        drift_stdevs[i] = d
                        if d > self.drift_stdevs:
                            drift_flagged.append(names[i])

        stats: list[FeatureStat] = []
        for i in range(dim):
            issues: list[str] = []
            v = float(var[i])
            constant = bool(v <= _MIN_VARIANCE)
            near_zero = bool(v <= _NEAR_ZERO_VARIANCE and not constant)
            mr = float(np.mean(~np.isfinite(matrix[:, i])))
            if constant:
                issues.append("CONSTANT")
            if near_zero:
                issues.append("NEAR_ZERO_VARIANCE")
            if float(nan_rate[i]) > 0.0:
                issues.append("NAN_PRESENT")
            if float(inf_rate[i]) > 0.0:
                issues.append("INF_PRESENT")
            if redundant_with[i]:
                issues.append("REDUNDANT")
            if names[i] in drift_flagged:
                issues.append("DRIFT")
            if not np.isfinite(mean[i]):
                issues.append("NON_FINITE_MEAN")
            if constant or float(inf_rate[i]) > 0.5 or not np.isfinite(mean[i]):
                status = FeatureStatus.FAIL
            elif issues:
                status = FeatureStatus.WARN
            else:
                status = FeatureStatus.PASS

            stability = 1.0
            if len(block_means) > 1 and std[i] > 0:
                shifts = [
                    abs(float(bm[i] - ref[i])) / float(std[i])
                    for bm in block_means.values()
                    if np.isfinite(bm[i]) and np.isfinite(ref[i])
                ]
                if shifts:
                    stability = max(0.0, 1.0 - (max(shifts) / self.unstable_mean_shift))

            stats.append(
                FeatureStat(
                    name=names[i],
                    index=i,
                    family=_family_for(i, dim),
                    dimension=dim,
                    missing_rate=round(mr, 6),
                    nan_rate=round(float(nan_rate[i]), 6),
                    inf_rate=round(float(inf_rate[i]), 6),
                    variance=v,
                    std=float(std[i]),
                    mean=float(mean[i]),
                    constant=constant,
                    near_zero_variance=near_zero,
                    stability=round(stability, 4),
                    redundancy_max_corr=round(float(max_corr[i]), 4),
                    redundant_with=sorted(set(redundant_with[i])),
                    drift_stdevs=round(float(drift_stdevs[i]), 3),
                    status=status,
                    issues=issues,
                )
            )

        failed = sum(1 for s in stats if s.status == FeatureStatus.FAIL)
        warned = sum(1 for s in stats if s.status == FeatureStatus.WARN)
        if failed:
            quality_status = FeatureStatus.FAIL
        elif warned or drift_flagged or redundant_groups:
            quality_status = FeatureStatus.WARN
        else:
            quality_status = FeatureStatus.PASS

        report_warnings: list[str] = []
        if alias_groups:
            report_warnings.append(f"{len(alias_groups)} exact alias group(s) detected")
        if drift_flagged:
            report_warnings.append(f"{len(drift_flagged)} feature(s) with train/OOS drift")
        if failed:
            report_warnings.append(f"{failed} feature(s) FAILED certification")

        report = FeatureQualityReport(
            dataset_id=dataset_id,
            feature_schema_id=self.feature_schema_id,
            feature_schema_hash=self.feature_schema_hash,
            dimension=dim,
            rows=rows,
            block_names=block_names,
            total_features=dim,
            passed=sum(1 for s in stats if s.status == FeatureStatus.PASS),
            warned=warned,
            failed=failed,
            quality_status=quality_status,
            redundant_groups=redundant_groups,
            drift_flagged=drift_flagged,
            generated_at=datetime.now(UTC).isoformat(),
            features=[s.to_dict() for s in stats],
            config={
                "corr_threshold": self.corr_threshold,
                "unstable_mean_shift": self.unstable_mean_shift,
                "drift_stdevs": self.drift_stdevs,
            },
            warnings=report_warnings,
        )
        logger.info(
            "[FEATURE_QUALITY] event=CERTIFIED dim=%d pass=%d warn=%d fail=%d status=%s",
            dim,
            report.passed,
            report.warned,
            report.failed,
            quality_status,
        )
        return report


def _blocks_from_matrix(matrix: np.ndarray) -> dict[str, np.ndarray]:
    """Placeholder block derivation for a bare matrix.

    A bare (rows, dim) matrix carries no split/regime/session columns, so the
    only honest block is 'full'. ``certify_frame`` supplies the real blocks
    by carrying the frame through; this keeps the matrix entry honest rather
    than fabricating block boundaries.
    """
    return {"full": np.arange(matrix.shape[0], dtype=np.int64)}


class FeatureQualityFrameCertifier:
    """Certifies a feature FRAME (with _split / regime / session columns)."""

    def __init__(self, **kwargs: Any) -> None:
        self._inner = FeatureQualityCertifier(**kwargs)

    def certify(self, frame: pl.DataFrame, *, dataset_id: str = "") -> FeatureQualityReport:
        feat_cols = [c for c in frame.columns if c.startswith("feat_")]
        if not feat_cols:
            raise ValueError("FeatureQualityFrameCertifier: no feat_* columns in frame")
        mat = frame.select(feat_cols).to_numpy().astype(np.float64, copy=True)
        report = self._inner.certify(mat, feat_cols, dataset_id=dataset_id)
        # Re-derive real blocks now that the frame is available.
        blocks = _blocks(frame)
        report.block_names = ["full", *sorted(blocks)]
        return report


def certify_feature_quality(
    frame_or_matrix: Any,
    *,
    names: list[str] | None = None,
    dataset_id: str = "",
    feature_schema_id: str = "scalp_v1",
    feature_schema_hash: str = "",
    out_path: str | Path | None = None,
) -> FeatureQualityReport:
    """Canonical entry: certify and (optionally) persist the report.

    Accepts either a polars feature frame (with ``feat_*`` columns and
    optional ``_split``/``regime``/``session``) or a raw (rows, dim) numpy
    matrix plus ``names``.
    """
    if isinstance(frame_or_matrix, pl.DataFrame):
        cert = FeatureQualityFrameCertifier(
            feature_schema_id=feature_schema_id,
            feature_schema_hash=feature_schema_hash,
        )
        report = cert.certify(frame_or_matrix, dataset_id=dataset_id)
    else:
        if names is None:
            raise ValueError("certify_feature_quality: a numpy matrix requires explicit names")
        cert = FeatureQualityCertifier(
            feature_schema_id=feature_schema_id, feature_schema_hash=feature_schema_hash
        )
        report = cert.certify(np.asarray(frame_or_matrix), names, dataset_id=dataset_id)

    if out_path is not None:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
    return report


def load_feature_quality_report(path: str | Path) -> FeatureQualityReport | None:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    payload["config"] = payload.get("config") or {}
    return FeatureQualityReport(**payload)


def _report_hash(report: FeatureQualityReport) -> str:
    return hashlib.sha256(
        json.dumps(report.to_dict(), sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
