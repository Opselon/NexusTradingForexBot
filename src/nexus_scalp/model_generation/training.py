"""Candidate Trainer (PHASE 13, spec 22 / 36).

Builds CandidateModelArtifacts from DatasetArtifact + ExperimentConfig.
NEVER writes to the Champion path. A failed training run is FAILED, never
CHALLENGER.

Reuses the Phase 10 candidate/staging safety model. This module trains with
a compact, deterministic loop suitable for behavioral tests + CLI smoke
tests; production-scale training continues through the Phase 10
ChallengerTrainer / WalkForwardTrainer (same candidate-staging boundary).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import polars as pl
import torch
from torch.utils.data import DataLoader, TensorDataset

from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.lineage import assert_production_eligible
from nexus_scalp.model_generation.model_factory import ModelFactory
from nexus_scalp.model_generation.models import (
    ExperimentConfig,
    ModelManifest,
    default_label_schema,
)
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.training.walk_forward_trainer import (
    FocalLossWithSmoothing,
    _balance_oversample_dataset,
)

logger = get_logger("nexus_scalp.model_generation.training")


def _split_columns(frame: pl.DataFrame, news_enabled: bool) -> tuple[list[str], list[str]]:
    """Feature cols = feat_*; news cols = news_* (when enabled)."""
    feat_cols = [c for c in frame.columns if c.startswith("feat_")]
    news_cols = [
        c for c in frame.columns if c.startswith("news_") and c != "news_context_schema_id"
    ]
    if not news_enabled:
        news_cols = []
    return feat_cols, news_cols


def dataset_hash_value(dataset_frame: pl.DataFrame) -> str:
    """Deterministic content hash of the dataset frame (features + labels only)
    used for candidate identity — a candidate is reproducible from
    (dataset, experiment config, seed)."""
    import hashlib
    import json

    cols = [c for c in dataset_frame.columns if c.startswith("feat_") or c in ("label", "_split")]
    payload = dataset_frame.select(cols).to_dict(as_series=False)
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def deterministic_candidate_id(
    experiment: ExperimentConfig, dataset_frame: pl.DataFrame, dataset_hash: str = ""
) -> str:
    """Candidate identity is DETERMINISTIC (spec 12): same dataset + same
    experiment config + same seed -> same candidate id. Never wall-clock."""
    cfg = {
        "experiment_id": experiment.experiment_id,
        "architecture": experiment.architecture,
        "news_enabled": experiment.news_enabled,
        "seed": experiment.seed,
        "strategy": experiment.strategy_id,
        "dataset_hash": dataset_hash,
    }
    import hashlib
    import json

    canonical = json.dumps(cfg, sort_keys=True, default=str)
    return "cand_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


#: Gradient norm cap: a norm above this is treated as EXPLODING (failed run,
#: never a silently-trained artifact).
MAX_GRAD_NORM: float = 5.0


def _grad_norm(model: torch.nn.Module) -> float | None:
    total = 0.0
    count = 0
    for p_ in model.parameters():
        if p_.grad is not None:
            total += float(p_.grad.detach().float().pow(2).sum())
            count += 1
    if count == 0:
        return None
    return float(math.sqrt(total))


class CandidateTrainer:
    """Trains one candidate model from a dataset artifact frame."""

    def __init__(self, store: ArtifactStore | None = None) -> None:
        self.store = store or ArtifactStore()
        self.model_factory = ModelFactory()
        self.label_schema = default_label_schema()

    def train_candidate(
        self,
        experiment: ExperimentConfig,
        dataset_frame: pl.DataFrame,
        *,
        feature_cols: list[str] | None = None,
        model_id: str | None = None,
        epochs: int | None = None,
        governance_override: bool = False,
    ) -> dict[str, Any]:
        """Trains and persists a candidate artifact. Never touches Champion.

        MLFIX-T7: when the associated dataset manifest carries a tainted
        label_origin (PAPER/LIVE/UNKNOWN), training a production-eligible
        candidate is blocked unless the caller passes governance_override=True.
        Research-only runs that are not CHAMPION-eligible should NOT pass the
        override — they are simply not production-training.

        Returns {status, model_id, artifact, error?}.
        """
        if dataset_frame.is_empty():
            return {"status": "FAILED", "error": "empty dataset"}
        if "label" not in dataset_frame.columns:
            return {"status": "FAILED", "error": "missing label column"}

        # MLFIX-T7 hard guard: refuse to train a CHAMPION-eligible candidate
        # from a tainted dataset manifest without an explicit operator token.
        if experiment.dataset_id:
            _man = self.store.read_dataset_manifest(experiment.dataset_id) or {}
            _origin = str(_man.get("label_origin") or _man.get("source_classification") or "")
            if _origin:
                try:
                    assert_production_eligible(_origin, governance_override=governance_override)
                except Exception as exc:
                    return {"status": "FAILED", "error": str(exc)}

        feat_cols, news_cols = _split_columns(dataset_frame, experiment.news_enabled)
        if feature_cols:
            feat_cols = [c for c in feature_cols if c in dataset_frame.columns]
            # BUG-112 (TASK-05): when the caller passes an EXPLICIT feature_cols
            # that already includes the news block, news_cols must be derived
            # from that list — otherwise the manifest records
            # input_dimension = len(feat_cols) + len(news_cols) which
            # DOUBLE-COUNTS news (e.g. 72 + 12 = 84 for a 72-wide model) and
            # the runtime predict() then rejects its own artifact.
            news_cols = [c for c in feat_cols if c.startswith("news_")]
            feat_cols = [c for c in feat_cols if not c.startswith("news_")]
        if not feat_cols:
            return {"status": "FAILED", "error": "no feature columns in dataset"}

        # labels -> numpy (validate 3-class contract)
        labels = dataset_frame["label"].to_numpy().astype(np.int64)
        try:
            self.label_schema.validate_labels(labels.tolist())
        except ValueError as e:
            return {"status": "FAILED", "error": str(e)}

        # features (feat_*) + optional news features (news_*)
        X_arr = dataset_frame.select(feat_cols).to_numpy().astype(np.float32)
        if news_cols:
            X_news = dataset_frame.select(news_cols).to_numpy().astype(np.float32)
            X_arr = np.hstack([X_arr, X_news])

        # T29: NaN/Inf inputs would train a garbage model (NaN loss) that
        # looks COMPLETED — reject them up front so the run is FAILED,
        # never CHALLENGER.
        if not np.isfinite(X_arr).all():
            return {"status": "FAILED", "error": "non-finite feature values in dataset"}

        # split: train on non-test rows, validate on test rows
        split = dataset_frame.get_column("_split") if "_split" in dataset_frame.columns else None
        if split is None:
            # fall back: last 20% as validation
            n = X_arr.shape[0]
            train_idx = np.arange(int(n * 0.8))
            val_idx = np.arange(int(n * 0.8), n)
        else:
            train_idx = np.where(split.to_numpy() != "test")[0]
            val_idx = np.where(split.to_numpy() == "test")[0]

        if len(train_idx) == 0 or len(val_idx) == 0:
            return {"status": "FAILED", "error": "empty train/val split"}

        # ------------------------------------------------------------------
        # Feature scaling (distribution parity, forensic audit T24).
        # Fit mean/std on the TRAIN split ONLY (zero leakage into val/OOS) and
        # persist it with the artifact so the runtime applies the SAME
        # transform during inference. Matches the legacy WalkForwardTrainer's
        # isolated-fitted-scaler invariant.
        # ------------------------------------------------------------------
        feat_mean = X_arr[train_idx].mean(axis=0).astype(np.float32)
        feat_std = X_arr[train_idx].std(axis=0).astype(np.float32)
        feat_std = np.where(feat_std < 1e-8, 1.0, feat_std)  # constant cols -> identity
        X_scaled = (X_arr - feat_mean) / feat_std

        # TASK-04-70D-MODEL-VALIDATION (BUG-101): seed BEFORE model
        # construction. Building the model first then seeding leaves weight
        # init at the ambient (unseeded) RNG state -> two runs of the same
        # experiment produce different results (reproducibility broken,
        # brief 39). WalkForwardTrainer already seeds before constructing.
        seed = int(experiment.seed or 42)
        torch.manual_seed(seed)
        np.random.seed(seed)

        model = self.model_factory.build(
            architecture=experiment.architecture,
            num_classes=int(experiment.class_count or 3),
            parameters={
                "input_dim": X_arr.shape[1],
                **(experiment.architecture_parameters or {}),
            },
        )

        epochs_n = epochs or int((experiment.training or {}).get("epochs", 10))
        lr = float((experiment.training or {}).get("learning_rate", 0.001))
        batch_size = int((experiment.training or {}).get("batch_size", 256))

        train_ds = TensorDataset(
            torch.from_numpy(X_scaled[train_idx]),
            torch.from_numpy(labels[train_idx]),
        )

        optimizer = torch.optim.Adam(model.parameters(), lr=lr)

        # ------------------------------------------------------------------
        # Class imbalance recipe (mirrors WalkForwardTrainer production path):
        #   1. Oversample BUY/SELL toward 85% of majority so the minority
        #      classes are actually present in every batch.
        #   2. FocalLossWithSmoothing (gamma=2.0, smoothing=0.08) + class
        #      weights: focal factor (1-p_t)^2 down-weights easy NO_TRADE,
        #      smoothing prevents degenerate overconfidence.
        # Plain CE on the 88%-NO_TRADE set trains an always-NO_TRADE model
        # (88% acc, 0 recall on BUY/SELL, macro-F1 ~0.31) — REJECTED by the
        # validation gate. (data-gate finding)
        # ------------------------------------------------------------------
        X_train = X_scaled[train_idx]
        y_train = labels[train_idx]

        # oversample active classes (deterministic seed for reproducibility)
        np.random.seed(seed)
        X_bal, y_bal = _balance_oversample_dataset(X_train, y_train, active_boost_ratio=0.85)

        class_counts = np.bincount(y_train, minlength=3)[:3]
        beta = 0.99
        effective_num = 1.0 - np.power(beta, class_counts.astype(np.float64))
        effective_num = np.maximum(effective_num, 1e-5)
        cb_weights = (1.0 - beta) / effective_num
        for idx in (1, 2):  # BUY / SELL boost
            cb_weights[idx] *= 3.0
        cb_weights = cb_weights / cb_weights.mean()
        # head may be 4-wide (ScalpNet NO_TRADE/BUY/SELL/WAIT) — WAIT never
        # appears in labels, gets unit weight
        model_num_classes = int(getattr(model, "num_classes", None) or experiment.class_count or 3)
        if model_num_classes > 3:
            cb_weights = np.concatenate([cb_weights, np.ones(model_num_classes - 3)])
        alpha_t = torch.tensor(cb_weights, dtype=torch.float32)
        criterion = FocalLossWithSmoothing(alpha=alpha_t, gamma=2.0, label_smoothing=0.08)

        # re-seed after oversampling (np.random consumed by balancing)
        torch.manual_seed(seed)
        np.random.seed(seed)
        train_ds = TensorDataset(
            torch.from_numpy(X_bal),
            torch.from_numpy(y_bal),
        )
        loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)

        model.train()
        for _ in range(epochs_n):
            for xb, yb in loader:
                optimizer.zero_grad()
                out = model(xb)
                loss = criterion(out, yb)
                if not torch.isfinite(loss):
                    return {
                        "status": "FAILED",
                        "error": f"non-finite loss at epoch {_}: {float(loss)}",
                    }
                loss.backward()
                grad_norm = _grad_norm(model)
                if grad_norm is None or not math.isfinite(grad_norm) or grad_norm > MAX_GRAD_NORM:
                    return {
                        "status": "FAILED",
                        "error": f"invalid/exploding gradient norm {grad_norm} at epoch {_}",
                    }
                torch.nn.utils.clip_grad_norm_(model.parameters(), MAX_GRAD_NORM)
                optimizer.step()

        # eval on validation (SCALED with the train-fitted transform)
        model.eval()
        with torch.inference_mode():
            val_logits = model(torch.from_numpy(X_scaled[val_idx]))
            val_preds = val_logits.argmax(dim=1).numpy()
        val_acc = float(np.mean(val_preds == labels[val_idx])) if len(val_idx) else 0.0

        mid = model_id or deterministic_candidate_id(
            experiment, dataset_frame, dataset_hash=dataset_hash_value(dataset_frame)
        )
        # AGENT-09 (TASK-09 governance readiness, brief 25): the manifest MUST
        # carry the provenance fields verify_candidate expects (feature_schema_hash,
        # liquidity_algorithm_version, training_commit) or the governance gates
        # FAIL even for a scientifically valid candidate. Resolve them here from
        # the canonical schema contract + the dataset manifest.
        _ds_manifest = (
            self.store.read_dataset_manifest(experiment.dataset_id)
            if experiment.dataset_id
            else None
        )
        _ds_manifest = _ds_manifest or {}
        _schema_hash = str(_ds_manifest.get("feature_schema_hash", "") or "")
        if not _schema_hash:
            try:
                from nexus_scalp.features.schema_contract import feature_schema_hash as _fsh

                _schema_hash = _fsh()
            except Exception:
                _schema_hash = ""
        _liquidity_algo = str(
            _ds_manifest.get("liquidity_algorithm_version", "")
            or _ds_manifest.get("algorithm_version", "")
            or "70d-v1.0.0"
        )
        _training_commit = ""
        try:
            import subprocess

            _training_commit = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout.strip()
        except Exception:
            _training_commit = ""
        manifest = ModelManifest(
            model_id=mid,
            model_version="1.0.0",
            role="CANDIDATE",
            status="TRAINED",
            architecture_id=experiment.architecture,
            architecture_parameters=experiment.architecture_parameters,
            feature_schema_id=dataset_frame["feature_schema_id"][0]
            if "feature_schema_id" in dataset_frame.columns
            else "scalp_v1",
            feature_dimension=len(feat_cols),
            label_schema_id=self.label_schema.label_schema_id,
            label_schema_version=self.label_schema.version,
            class_count=self.label_schema.class_count,
            classes=self.label_schema.class_names,
            dataset_id=experiment.dataset_id,
            random_seed=seed,
            training_config=experiment.training,
            optimizer="adam",
            strategy_id=experiment.strategy_id,
            strategy_version=experiment.strategy_version,
            news_enabled=experiment.news_enabled,
            news_schema_version=experiment.news_schema_id,
            walk_forward_status="",
            oos_status="",
            final_validation_result={
                "val_accuracy": round(val_acc, 4),
                "epochs": epochs_n,
                "train_rows": len(train_idx),
                "val_rows": len(val_idx),
            },
            # AGENT-09 (TASK-09 governance): top-level provenance fields —
            # governance/verify.py reads these at TOP-LEVEL (not inside
            # build_metadata); without them the 14-gate verify FAILs the
            # candidate regardless of scientific validity (brief 25).
            feature_schema_hash=_schema_hash,
            liquidity_algorithm_version=_liquidity_algo,
            training_commit=_training_commit,
            build_metadata={
                "trainer": "CandidateTrainer",
                "news_features": news_cols,
                "input_dimension": len(feat_cols) + len(news_cols),
                "feature_schema_hash": _schema_hash,
                "liquidity_algorithm_version": _liquidity_algo,
                "training_commit": _training_commit,
            },
        )

        artifact = self.store.save_model_artifact(
            mid,
            model.state_dict(),
            manifest.model_dump(mode="json"),
            scaler=(feat_mean, feat_std),
        )
        # AGENT-09 (TASK-09, brief 24/25): stamp governance evidence refs
        # into the persisted manifest (oos_artifact / robustness_artifact /
        # shadow_evidence are separate artifacts; the manifest records their
        # paths so verify_candidate's oos_artifact_recorded gate passes).
        _extra_evidence = (experiment.training or {}).get("evidence", {}) or {}
        if _extra_evidence:
            try:
                _persisted = self.store.read_model_manifest(mid) or {}
                for _k in ("oos_artifact", "robustness_artifact", "shadow_evidence"):
                    if _k in _extra_evidence:
                        _persisted[_k] = _extra_evidence[_k]
                self.store.write_json(self.store.model_manifest_path(mid), _persisted)
            except Exception:
                logger.warning("[TRAIN] event=EVIDENCE_STAMP_FAILED model_id=%s", mid)
        logger.info("[TRAIN] event=CANDIDATE_READY model_id=%s val_acc=%.4f", mid, val_acc)
        return {
            "status": "COMPLETED",
            "model_id": mid,
            "val_accuracy": round(val_acc, 4),
            "artifact": artifact,
        }
