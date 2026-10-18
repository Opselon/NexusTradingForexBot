"""Canonical Label Quality Audit (MODEL FACTORY — stage 3).

WHY THIS EXISTS
---------------
The Triple Barrier labeler (``labeling/triple_barrier``) generates correct
labels, but nothing ever AUDITED them before training. ``model_lifecycle/
gates.gate_label_integrity`` checks the aggregate distribution at promotion
time only, and ``labeling/sample_weights`` reweights imbalance after the
fact. Neither answers the brief's requirement: detect collapsed classes,
fold imbalance, regime-specific collapse, suspicious temporal concentration
and unexpected label changes after a dataset regeneration — BEFORE a model
is trained on the labels.

This module is that audit. It runs on a labeled dataset frame and produces
``label_quality_report.json``. Training fails loudly when the audit verdict
is FAIL.

CONTRACT (Section 12)
---------------------
* class distribution overall + by fold + by regime + by session + over time
* label density (labeled rows / rows) and NO_TRADE percentage
* detection of: collapsed classes, fold imbalance, regime-specific label
  collapse, suspicious temporal concentration
* the audit never modifies labels; it reports

TRAIN-ONLY DISCIPLINE
---------------------
The audit is descriptive over the whole dataset frame; it is not a fitted
statistic and is not used to transform features. Fold-level distributions
use only the frame's own ``_split`` markers (which are positional and
chronological — no RNG, no target leakage).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.label_quality")

__all__ = [
    "LabelQualityAudit",
    "LabelQualityReport",
    "LabelStatus",
    "audit_label_quality",
    "load_label_quality_report",
]

#: Canonical 3-class contract (model_lifecycle/gates + triple_barrier).
CLASS_NAMES: dict[int, str] = {0: "NO_TRADE", 1: "BUY", 2: "SELL"}

#: Minimum share each non-NO_TRADE class must occupy to not be collapsed.
_MIN_CLASS_RATIO: float = 0.05
#: Maximum NO_TRADE share before the dataset is considered collapsed to pass.
_MAX_NO_TRADE_RATIO: float = 0.96
#: Maximum fold-to-fold class-share spread before fold imbalance is flagged.
_MAX_FOLD_SPREAD: float = 0.25
#: Maximum share of any class inside a single regime before regime collapse.
_MAX_REGIME_DOMINANCE: float = 0.98
#: Maximum fraction of all BUY+SELL labels in one decile of time.
_MAX_TIME_DECILE_CONCENTRATION: float = 0.40

_LABEL_NAME_TO_INT: dict[str, int] = {
    "0": 0,
    "1": 1,
    "2": 2,
    "NO_TRADE": 0,
    "WAIT": 0,
    "BUY": 1,
    "BUY_MARKET": 1,
    "SELL": 2,
    "SELL_MARKET": 2,
}


class LabelStatus:
    PASS: str = "PASS"
    WARN: str = "WARN"
    FAIL: str = "FAIL"


@dataclass
class LabelQualityReport:
    dataset_id: str
    label_schema_id: str
    label_version: str
    rows: int
    labeled_rows: int
    label_density: float
    class_distribution: dict[str, int]
    class_ratios: dict[str, float]
    no_trade_percentage: float
    balance_ratio: float
    distribution_by_fold: dict[str, dict[str, int]]
    distribution_by_regime: dict[str, dict[str, int]]
    distribution_by_session: dict[str, dict[str, int]]
    distribution_over_time: dict[str, dict[str, int]]
    collapsed_classes: list[str]
    fold_imbalance: list[str]
    regime_collapse: list[str]
    temporal_concentration: list[str]
    quality_status: str
    generated_at: str
    config: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_label_series(series: pl.Series) -> np.ndarray:
    """Normalizes string or numeric labels to integer codes 0, 1, 2 (or -1 for invalid/missing)."""
    dtype = series.dtype
    if dtype in (pl.String, pl.Categorical):
        vals = series.to_list()
        out = np.empty(len(vals), dtype=np.int64)
        for i, v in enumerate(vals):
            if v is None:
                out[i] = -1
            else:
                s = str(v).strip().upper()
                out[i] = _LABEL_NAME_TO_INT.get(s, -1)
        return out
    else:
        arr = series.cast(pl.Int64, strict=False).to_numpy()
        return np.where(np.isfinite(arr), arr, -1).astype(np.int64)


def _dist(labels: np.ndarray) -> dict[str, int]:
    out: dict[str, int] = {}
    valid = labels[labels >= 0]
    for c in np.unique(valid):
        name = CLASS_NAMES.get(int(c), f"CLASS_{int(c)}")
        out[name] = int((valid == c).sum())
    return out


class LabelQualityAudit:
    """Audits a labeled dataset frame BEFORE training."""

    def __init__(
        self,
        *,
        label_schema_id: str = "triple_barrier_v3",
        label_version: str = "3.6",
        min_class_ratio: float = _MIN_CLASS_RATIO,
        max_no_trade_ratio: float = _MAX_NO_TRADE_RATIO,
        max_fold_spread: float = _MAX_FOLD_SPREAD,
        max_regime_dominance: float = _MAX_REGIME_DOMINANCE,
        max_time_decile_concentration: float = _MAX_TIME_DECILE_CONCENTRATION,
    ) -> None:
        self.label_schema_id = label_schema_id
        self.label_version = label_version
        self.min_class_ratio = float(min_class_ratio)
        self.max_no_trade_ratio = float(max_no_trade_ratio)
        self.max_fold_spread = float(max_fold_spread)
        self.max_regime_dominance = float(max_regime_dominance)
        self.max_time_decile_concentration = float(max_time_decile_concentration)

    def audit(self, frame: pl.DataFrame, *, dataset_id: str = "") -> LabelQualityReport:
        """Audits a labeled frame. Requires a ``label`` column."""
        if frame is None or frame.is_empty():
            raise ValueError("LabelQualityAudit: empty frame — nothing to audit")
        if "label" not in frame.columns:
            raise ValueError(
                "LabelQualityAudit: frame has no 'label' column; the Triple "
                "Barrier labeler must run first"
            )

        labels_all = _normalize_label_series(frame["label"])
        n = len(labels_all)
        valid_mask = labels_all >= 0
        labeled = int(valid_mask.sum())
        labels = labels_all[valid_mask]

        dist = _dist(labels)
        ratios = {k: (v / len(labels) if labels.size else 0.0) for k, v in dist.items()}
        no_trade_pct = float(ratios.get("NO_TRADE", 0.0))
        buy = dist.get("BUY", 0)
        sell = dist.get("SELL", 0)
        balance_ratio = min(buy, sell) / max(buy, sell) if max(buy, sell) > 0 else 0.0

        # ---- by fold -------------------------------------------------------
        by_fold: dict[str, dict[str, int]] = {}
        fold_key = "_split" if "_split" in frame.columns else "split"
        if fold_key in frame.columns:
            for s in ("train", "val", "test", "purged"):
                idx = [i for i, v in enumerate(frame[fold_key].to_list()) if v == s]
                if idx:
                    by_fold[s] = _dist(labels_all[idx])

        # ---- by regime -----------------------------------------------------
        by_regime: dict[str, dict[str, int]] = {}
        if "regime" in frame.columns:
            for r in sorted(set(map(str, frame["regime"].to_list()))):
                idx = [i for i, v in enumerate(frame["regime"].to_list()) if str(v) == r]
                if idx:
                    by_regime[str(r)] = _dist(labels_all[idx])

        # ---- by session ----------------------------------------------------
        by_session: dict[str, dict[str, int]] = {}
        if "session" in frame.columns:
            for s in sorted(set(map(str, frame["session"].to_list()))):
                idx = [i for i, v in enumerate(frame["session"].to_list()) if str(v) == s]
                if idx:
                    by_session[str(s)] = _dist(labels_all[idx])

        # ---- over time (deciles of chronological position) ------------------
        over_time: dict[str, dict[str, int]] = {}
        if "timestamp" in frame.columns or "time" in frame.columns:
            tcol = "timestamp" if "timestamp" in frame.columns else "time"
            ts = frame[tcol].to_list()
            order = np.argsort(ts, kind="stable")
            decile = int(n // 10) or 1
            for d in range(10):
                d_idx = order[d * decile : (d + 1) * decile if d < 9 else n]
                if len(d_idx):
                    over_time[f"decile_{d + 1}"] = _dist(labels_all[d_idx])

        # ---- detections -----------------------------------------------------
        collapsed: list[str] = []
        for cls in ("BUY", "SELL"):
            share = ratios.get(cls, 0.0)
            if share < self.min_class_ratio:
                collapsed.append(f"{cls} below minimum ratio ({share:.2%})")
        if no_trade_pct > self.max_no_trade_ratio:
            collapsed.append(f"NO_TRADE dominance {no_trade_pct:.2%}")

        fold_imb: list[str] = []
        scored = [k for k in ("train", "val", "test") if k in by_fold]
        for cls in ("BUY", "SELL"):
            shares = []
            for k in scored:
                total = sum(by_fold[k].values())
                if total:
                    shares.append(by_fold[k].get(cls, 0) / total)
            if shares and (max(shares) - min(shares)) > self.max_fold_spread:
                fold_imb.append(
                    f"{cls} fold spread {max(shares) - min(shares):.2%} "
                    f"(folds: {[round(s, 3) for s in shares]})"
                )

        regime_collapse: list[str] = []
        for r, reg_dist in by_regime.items():
            total = sum(reg_dist.values())
            if not total:
                continue
            for cls, cnt in reg_dist.items():
                if cnt / total >= self.max_regime_dominance and cls != "NO_TRADE":
                    regime_collapse.append(f"regime {r}: {cls} at {cnt / total:.2%}")

        temporal_conc: list[str] = []
        signal_total = buy + sell
        if signal_total and over_time:
            shares = []
            for _, time_dist in over_time.items():
                shares.append((time_dist.get("BUY", 0) + time_dist.get("SELL", 0)) / signal_total)
            if max(shares) > self.max_time_decile_concentration:
                temporal_conc.append(
                    f"{max(shares):.2%} of all BUY+SELL labels fall in one time decile"
                )

        # ---- verdict --------------------------------------------------------
        warnings: list[str] = []
        if collapsed:
            warnings.append("class collapse detected")
        if fold_imb:
            warnings.append("fold imbalance detected")
        if regime_collapse:
            warnings.append("regime-specific label collapse detected")
        if temporal_conc:
            warnings.append("suspicious temporal concentration detected")
        if not (buy and sell):
            collapsed.append("one of BUY/SELL entirely absent")
            warnings.append("BUY or SELL entirely absent")

        if collapsed:
            status = LabelStatus.FAIL
        elif warnings:
            status = LabelStatus.WARN
        else:
            status = LabelStatus.PASS

        report = LabelQualityReport(
            dataset_id=dataset_id,
            label_schema_id=self.label_schema_id,
            label_version=self.label_version,
            rows=n,
            labeled_rows=labeled,
            label_density=(labeled / n if n else 0.0),
            class_distribution=dist,
            class_ratios={k: round(v, 6) for k, v in ratios.items()},
            no_trade_percentage=round(no_trade_pct, 6),
            balance_ratio=round(balance_ratio, 6),
            distribution_by_fold=by_fold,
            distribution_by_regime=by_regime,
            distribution_by_session=by_session,
            distribution_over_time=over_time,
            collapsed_classes=collapsed,
            fold_imbalance=fold_imb,
            regime_collapse=regime_collapse,
            temporal_concentration=temporal_conc,
            quality_status=status,
            generated_at=datetime.now(UTC).isoformat(),
            config={
                "min_class_ratio": self.min_class_ratio,
                "max_no_trade_ratio": self.max_no_trade_ratio,
                "max_fold_spread": self.max_fold_spread,
                "max_regime_dominance": self.max_regime_dominance,
                "max_time_decile_concentration": self.max_time_decile_concentration,
            },
            warnings=warnings,
        )
        logger.info(
            "[LABEL_QUALITY] event=AUDITED rows=%d density=%.3f status=%s",
            n,
            report.label_density,
            status,
        )
        return report


def audit_label_quality(
    frame: pl.DataFrame,
    *,
    dataset_id: str = "",
    label_schema_id: str = "triple_barrier_v3",
    out_path: str | Path | None = None,
) -> LabelQualityReport:
    """Canonical entry: audit a labeled frame and (optionally) persist."""
    audit = LabelQualityAudit(label_schema_id=label_schema_id)
    report = audit.audit(frame, dataset_id=dataset_id)
    if out_path is not None:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
    return report


def load_label_quality_report(path: str | Path) -> LabelQualityReport | None:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    payload["config"] = payload.get("config") or {}
    return LabelQualityReport(**payload)


def _report_hash(report: LabelQualityReport) -> str:
    return hashlib.sha256(
        json.dumps(report.to_dict(), sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
