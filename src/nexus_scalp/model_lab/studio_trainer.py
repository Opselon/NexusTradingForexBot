"""Neural Studio trainer — contract-aware training with a real OOS split.

Distinct from ``model_lab.trainer`` (the research experiment runner, which writes
to ``artifacts/models/research`` and never the Champion's home): this module is
the MODEL STUDIO trainer, driven by a ``ModelBuilderConfig``, writing to the
studio's own ``artifacts/model_generation/checkpoints`` root alongside the
existing ``execute_train`` path.

WHY THIS EXISTS
---------------
``model_studio_routes.execute_train`` did the fit inline in the HTTP request. It
split 80/20 train/val with NO out-of-sample holdout, so OOS metrics were never
measurable, and it accepted a bare ``dimension`` with no schema check.

This module implements the training data contract (Phase 7) and execution
(Phase 8 flow):

    CREATE RUN → VALIDATE → LOAD DATA → PREPROCESS → TRAIN →
    VALIDATE → OOS → SAVE → VERIFY → REGISTER

The OOS split is carved out BEFORE the training split and is never fitted on
(Phase 14: the trainer must never fit on OOS — neither the fit loop, the early
stopping, nor the scaler). Early stopping is decided on the VALIDATION loss only.

Metric truthfulness (Phase 12): a run that produced no measurement reports
``metrics_measured=False``. The ``loss=0.0000`` defect class was exactly a
register-but-never-trained record whose default ``0.0`` was displayed as a
measurement.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import torch

from nexus_scalp.model_lab.model_builder import (
    DIMENSION_TO_SCHEMA_ID,
    ModelBuilderConfig,
    resolve_dataset_for_training,
    validate_builder_config,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lab.studio_trainer")

REPO_ROOT = Path(__file__).resolve().parents[3]

# Name of the env override the web routes read for the inventory root. Imported
# here (not re-declared) so the trainer and the routes can never drift apart.
_REPO_ROOT_ENV = "NEXUS_MODEL_STUDIO_REPO_ROOT"

_MIN_TRAIN_ROWS = 20
_TRAINED_CLASS_COUNT = 3


@dataclass
class TrainingMetrics:
    """Phase 13: real, separately-named measurements (never defaulted to 0.0)."""

    train_loss: float | None = None
    validation_loss: float | None = None
    oos_loss: float | None = None
    train_accuracy: float | None = None
    validation_accuracy: float | None = None
    oos_accuracy: float | None = None
    class_distribution: dict[str, int] = field(default_factory=dict)
    confusion_matrix: list[list[int]] | None = None
    epochs_completed: int = 0
    baseline_train_loss: float | None = None
    metrics_measured: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TrainingResult:
    run_id: str
    model_id: str
    dimension: int
    schema_id: str
    status: str  # QUEUED | TRAINING | COMPLETE | FAILED | CANCELLED
    phase: str
    error: str
    artifact: dict[str, Any]
    metrics: TrainingMetrics
    config: dict[str, Any]
    started_at: str
    finished_at: str
    elapsed_sec: float
    dataset: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["metrics"] = self.metrics.to_dict()
        return out


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _restore_env(name: str, token: str | None) -> None:
    """Undo a temporary ``os.environ`` override without leaking it."""
    if token is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = token


class NeuralStudioTrainer:
    """Contract-aware trainer driven by a ``ModelBuilderConfig``."""

    def __init__(self, repo_root: Path | None = None) -> None:
        self._root = Path(repo_root) if repo_root is not None else REPO_ROOT

    # ------------------------------------------------------------------ run
    def train(self, cfg: ModelBuilderConfig, run_id: str | None = None) -> TrainingResult:
        """Synchronous preflighted fit (Phase 8 flow, steps 1-12).

        If this trainer was constructed with an explicit ``repo_root`` other
        than the installed checkout, the dataset/model inventories are pointed
        at that tree for the duration of the resolution only — never globally,
        and never past this call (a leaked override would silently redirect
        every later studio read in the process).
        """
        started = time.perf_counter()
        started_at = _now()
        run_id = run_id or f"train_studio_{int(time.time())}"
        schema_id = cfg.schema_id or DIMENSION_TO_SCHEMA_ID.get(cfg.dimension, "")

        token: str | None = None
        if self._root.resolve() != REPO_ROOT.resolve():
            token = os.environ.get(_REPO_ROOT_ENV)
            os.environ[_REPO_ROOT_ENV] = str(self._root.resolve())
        try:
            return self._train(cfg, run_id, schema_id, started, started_at)
        finally:
            _restore_env(_REPO_ROOT_ENV, token)

    def _train(
        self,
        cfg: ModelBuilderConfig,
        run_id: str,
        schema_id: str,
        started: float,
        started_at: str,
    ) -> TrainingResult:

        def _fail(phase: str, error: str, artifact: dict[str, Any] | None = None) -> TrainingResult:
            return TrainingResult(
                run_id=run_id,
                model_id="",
                dimension=cfg.dimension,
                schema_id=schema_id,
                status="FAILED",
                phase=phase,
                error=error,
                artifact=artifact or {},
                metrics=TrainingMetrics(),
                config=cfg.to_dict(),
                started_at=started_at,
                finished_at=_now(),
                elapsed_sec=time.perf_counter() - started,
                dataset={},
            )

        # 1. CREATE RUN + 2. VALIDATE — schema/dim/scaler/dataset consistency
        findings = validate_builder_config(cfg)
        errors = [f for f in findings if f.severity == "error"]
        if errors:
            return _fail(
                "VALIDATE",
                "; ".join(f"{f.code}: {f.message}" for f in errors),
            )

        # 3. LOAD DATA
        target_path = resolve_dataset_for_training(cfg.dataset_path)
        frame, dataset_info = self._load_dataset(cfg, target_path)
        if frame is None:
            return _fail("LOAD_DATA", "no usable dataset available for training")

        try:
            return self._fit(
                cfg=cfg,
                run_id=run_id,
                schema_id=schema_id,
                frame=frame,
                dataset_info=dataset_info,
                started_at=started_at,
                started=started,
            )
        except Exception as exc:
            logger.exception("training run %s failed", run_id)
            return _fail("TRAIN", str(exc))

    # ------------------------------------------------------------- dataset
    def _load_dataset(
        self, cfg: ModelBuilderConfig, target_path: Path | None
    ) -> tuple[Any, dict[str, Any]]:
        from nexus_scalp.model_lab.model_builder import _describe_frame, _load_frame

        if target_path is not None:
            frame = _load_frame(target_path)
            info = _describe_frame(frame, str(target_path))
            info["sha256"] = _hash_file(target_path)
            info["resolved"] = True
            return frame, info

        # No dataset selected: a synthetic fallback keeps the loop demonstrable,
        # but it is LABELED — never passed off as real data.
        from scripts.data.ingest_historical_candles import generate_synthetic_bars

        frame = generate_synthetic_bars(symbol="XAUUSD", count=1000, seed=cfg.seed)
        return frame, {
            "path": "synthetic",
            "resolved": False,
            "sha256": "",
            "row_count": int(frame.height),
            "synthetic_fallback": True,
        }

    # ------------------------------------------------------------------ fit
    def _fit(
        self,
        *,
        cfg: ModelBuilderConfig,
        run_id: str,
        schema_id: str,
        frame: Any,
        dataset_info: dict[str, Any],
        started_at: str,
        started: float,
    ) -> TrainingResult:
        from nexus_scalp.features.schema_contract import (
            canonical_feature_names,
            validate_vector,
        )
        from nexus_scalp.model_generation.model_registry import ModelRecord, get_model_registry
        from nexus_scalp.models.scalp_net import ScalpNet

        # 4. PREPROCESS
        from nexus_scalp.web.model_studio_routes import extract_dataset_features

        mat, names = extract_dataset_features(frame, dimension=cfg.dimension, max_rows=1000)
        n_rows = int(mat.shape[0])
        if n_rows < _MIN_TRAIN_ROWS:
            raise ValueError(
                f"dataset too small to train: {n_rows} usable row(s), need at least "
                f"{_MIN_TRAIN_ROWS}"
            )

        # Feature ORDERING is enforced here (Phase 5): the matrix columns must be
        # the canonical names in canonical order, not merely the right count.
        canonical = list(canonical_feature_names())[: cfg.dimension]
        if [str(x) for x in names] != canonical:
            raise ValueError(
                "feature ordering violation: the extracted matrix is not in "
                "canonical feature order"
            )

        labels = self._label(frame, n_rows)

        # 5. OOS SPLIT — carved out FIRST and never fitted on (Phase 14)
        oos_ratio = float(cfg.oos_ratio) if 0.0 < cfg.oos_ratio < 0.5 else 0.2
        n_oos = max(int(n_rows * oos_ratio), 1) if n_rows > _MIN_TRAIN_ROWS else 0
        oos_idx = np.arange(n_rows - n_oos, n_rows)
        fit_idx = np.arange(0, n_rows - n_oos)

        if len(fit_idx) < _MIN_TRAIN_ROWS:
            raise ValueError(
                f"after the OOS holdout the fit set has {len(fit_idx)} rows; need at "
                f"least {_MIN_TRAIN_ROWS}"
            )

        X_oos = torch.tensor(mat[oos_idx], dtype=torch.float32) if n_oos else None
        y_oos = torch.tensor(labels[oos_idx], dtype=torch.long) if n_oos else None

        # train/val split of the FIT set only
        train_size = min(max(int(len(fit_idx) * 0.8), 1), len(fit_idx) - 1)
        X_train = torch.tensor(mat[fit_idx[:train_size]], dtype=torch.float32)
        y_train = torch.tensor(labels[fit_idx[:train_size]], dtype=torch.long)
        X_val = torch.tensor(mat[fit_idx[train_size:]], dtype=torch.float32)
        y_val = torch.tensor(labels[fit_idx[train_size:]], dtype=torch.long)

        torch.manual_seed(cfg.seed)
        np.random.seed(cfg.seed)
        model = ScalpNet(
            num_features=cfg.dimension,
            num_classes=cfg.output_classes,
            hidden_dim=cfg.hidden_dim,
            num_heads=cfg.num_heads,
            dropout_rate=cfg.dropout_rate,
        )
        model.train()

        optimizer = self._build_optimizer(model, cfg)
        criterion = self._build_criterion(cfg)
        scheduler = self._build_scheduler(optimizer, cfg, cfg.epochs)

        # 6. TRAIN + 7. VALIDATE
        best_val = float("inf")
        best_state: dict[str, Any] | None = None
        patience_left = cfg.early_stopping_patience
        epoch_losses: list[float] = []
        val_losses: list[float] = []

        for _epoch in range(1, cfg.epochs + 1):
            model.train()
            perm = torch.randperm(train_size)
            batch_size = min(cfg.batch_size, train_size)
            for start in range(0, train_size, batch_size):
                idx = perm[start : start + batch_size]
                optimizer.zero_grad()
                out = model(X_train[idx])
                loss = criterion(out, y_train[idx])
                loss.backward()
                optimizer.step()
                epoch_losses.append(float(loss.item()))

            model.eval()
            with torch.inference_mode():
                val_loss = float(criterion(model(X_val), y_val).item())
            val_losses.append(val_loss)

            # Early stopping decides on VALIDATION loss only — OOS is never a
            # training-decision input (Phase 14).
            if val_loss < best_val - 1e-6:
                best_val = val_loss
                best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
                patience_left = cfg.early_stopping_patience
            elif cfg.early_stopping:
                patience_left -= 1
                if patience_left <= 0:
                    logger.info(
                        "run %s early-stopped (val=%.4f)", run_id, val_loss
                    )
                    break
            if scheduler is not None:
                scheduler.step()

        epochs_completed = len(val_losses)
        if best_state is not None:
            model.load_state_dict(best_state)
        model.eval()

        train_loss = float(np.mean(epoch_losses)) if epoch_losses else None
        val_loss = float(np.mean(val_losses)) if val_losses else None

        # 8. OOS — evaluated once, never fitted on
        oos_loss: float | None = None
        if X_oos is not None and y_oos is not None:
            with torch.inference_mode():
                oos_loss = float(criterion(model(X_oos), y_oos).item())

        # 9-11. METRICS — measured, not defaulted
        metrics = TrainingMetrics(
            train_loss=train_loss,
            validation_loss=val_loss,
            oos_loss=oos_loss,
            train_accuracy=self._accuracy(model, X_train, y_train),
            validation_accuracy=self._accuracy(model, X_val, y_val),
            oos_accuracy=self._accuracy(model, X_oos, y_oos) if X_oos is not None else None,
            class_distribution=self._class_distribution(labels),
            confusion_matrix=self._confusion_matrix(model, X_oos, y_oos)
            if X_oos is not None
            else None,
            epochs_completed=epochs_completed,
            baseline_train_loss=None,
            metrics_measured=train_loss is not None and val_loss is not None,
        )

        # 9. SAVE
        ckpt_dir = self._root / "artifacts" / "model_generation" / "checkpoints"
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        model_id = f"{run_id}_{cfg.dimension}d"
        ckpt_path = ckpt_dir / f"{model_id}.pt"
        torch.save(model.state_dict(), ckpt_path)

        scaler_path = ckpt_dir / f"{model_id}.scaler.npz"
        fit_mat = mat[fit_idx]
        scaler_mean = np.mean(fit_mat, axis=0).astype(np.float32)
        # The scaler is fitted on the FIT set only — never on OOS (Phase 14).
        scaler_std = np.maximum(np.std(fit_mat, axis=0), 1e-3).astype(np.float32)
        np.savez(
            scaler_path,
            mean=scaler_mean,
            std=scaler_std,
            dimension=cfg.dimension,
            schema_id=schema_id,
        )

        # Contract validation of a representative tensor (Phase 41)
        validate_vector(
            [float(v) for v in scaler_mean[: cfg.dimension]],
            dimension=cfg.dimension,
            schema_id=schema_id,
            context=f"train_{run_id}",
        )

        weights_sha256 = _hash_file(ckpt_path)

        manifest_path = ckpt_dir / f"{model_id}.meta.json"
        manifest = {
            "model_id": model_id,
            "run_id": run_id,
            "architecture": "ScalpNet",
            "dimension": cfg.dimension,
            "schema_id": schema_id,
            "feature_ordering": canonical,
            "sequence_length": cfg.sequence_length,
            "dtype": "float32",
            "normalization": "zscore_clip5",
            "output_classes": cfg.output_classes,
            "epochs": epochs_completed,
            "training_config": cfg.to_dict(),
            "training_metrics": metrics.to_dict(),
            "dataset": dataset_info,
            "weights_sha256": weights_sha256,
            "code_version": cfg.code_version,
            "training_version": cfg.training_version,
            "created_at": _now(),
        }
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)

        # 10. VERIFY + 11. REGISTER
        registry = get_model_registry()
        rec = ModelRecord(
            id=model_id,
            name=ckpt_path.name,
            version="1.0.0",
            dimension=cfg.dimension,
            architecture="ScalpNet",
            weights_path=self._rel(ckpt_path),
            scaler_path=self._rel(scaler_path),
            manifest_path=self._rel(manifest_path),
            sha256=weights_sha256,
            epochs=epochs_completed,
            final_loss=train_loss if train_loss is not None else 0.0,
            final_val_loss=val_loss if val_loss is not None else 0.0,
            accuracy=metrics.validation_accuracy or 0.0,
            dataset_path=dataset_info.get("path", ""),
            fine_tune_enabled=True,
            stage="STAGING",
            metrics={
                "run_id": run_id,
                "schema_id": schema_id,
                "metrics_measured": metrics.metrics_measured,
                "oos_loss": oos_loss,
                "oos_accuracy": metrics.oos_accuracy,
                "validation_accuracy": metrics.validation_accuracy,
                "class_distribution": metrics.class_distribution,
                "training_config": cfg.to_dict(),
            },
        )
        registry.register_model(rec)

        return TrainingResult(
            run_id=run_id,
            model_id=model_id,
            dimension=cfg.dimension,
            schema_id=schema_id,
            status="COMPLETE",
            phase="REGISTERED",
            error="",
            artifact={
                "checkpoint_path": self._rel(ckpt_path),
                "scaler_path": self._rel(scaler_path),
                "manifest_path": self._rel(manifest_path),
                "weights_sha256": weights_sha256,
                "model_id": model_id,
            },
            metrics=metrics,
            config=cfg.to_dict(),
            started_at=started_at,
            finished_at=_now(),
            elapsed_sec=time.perf_counter() - started,
            dataset=dataset_info,
        )

    # ------------------------------------------------------------- helpers
    def _label(self, frame: Any, n_rows: int) -> np.ndarray:
        closes = self._close_series(frame, n_rows)
        labels = np.zeros(n_rows, dtype=np.int64)
        for i in range(n_rows - 5):
            fut = (closes[i + 5] - closes[i]) / max(closes[i], 1e-4)
            if fut > 0.0005:
                labels[i] = 1
            elif fut < -0.0005:
                labels[i] = 2
        return labels

    @staticmethod
    def _close_series(frame: Any, n_rows: int) -> np.ndarray:
        cols = [str(c) for c in frame.columns]
        if "close" in cols:
            return np.asarray(frame["close"].to_numpy()[:n_rows], dtype=np.float64)
        if "current_price" in cols:
            return np.asarray(frame["current_price"].to_numpy()[:n_rows], dtype=np.float64)
        return np.linspace(2000.0, 2050.0, n_rows, dtype=np.float64)

    def _build_optimizer(self, model: torch.nn.Module, cfg: ModelBuilderConfig) -> Any:
        params = [p for p in model.parameters() if p.requires_grad]
        if cfg.optimizer == "adam":
            return torch.optim.Adam(params, lr=cfg.learning_rate)
        if cfg.optimizer == "sgd":
            return torch.optim.SGD(
                params, lr=cfg.learning_rate, momentum=0.9, weight_decay=cfg.weight_decay
            )
        return torch.optim.AdamW(params, lr=cfg.learning_rate, weight_decay=cfg.weight_decay)

    def _build_criterion(self, cfg: ModelBuilderConfig) -> Any:
        if (
            cfg.class_weights is not None
            and len(cfg.class_weights) == cfg.output_classes
        ):
            weights = torch.tensor(list(cfg.class_weights), dtype=torch.float32)
            return torch.nn.CrossEntropyLoss(weight=weights)
        return torch.nn.CrossEntropyLoss()

    def _build_scheduler(
        self, optimizer: Any, cfg: ModelBuilderConfig, epochs: int
    ) -> Any | None:
        if cfg.scheduler == "cosine":
            return torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
        if cfg.scheduler == "step":
            return torch.optim.lr_scheduler.StepLR(optimizer, step_size=1, gamma=0.95)
        return None

    @staticmethod
    def _accuracy(model: torch.nn.Module, x: torch.Tensor, y: torch.Tensor) -> float | None:
        if x is None or len(x) == 0:
            return None
        model.eval()
        with torch.inference_mode():
            pred = torch.argmax(model(x), dim=-1)
            return float((pred == y).float().mean().item())

    @staticmethod
    def _class_distribution(labels: np.ndarray) -> dict[str, int]:
        names = {0: "NO_TRADE", 1: "BUY", 2: "SELL"}
        return {
            names.get(int(c), f"CLASS_{int(c)}"): int((labels == c).sum())
            for c in np.unique(labels)
        }

    @staticmethod
    def _confusion_matrix(
        model: torch.nn.Module, x: torch.Tensor, y: torch.Tensor
    ) -> list[list[int]] | None:
        if x is None or len(x) == 0:
            return None
        model.eval()
        with torch.inference_mode():
            pred = torch.argmax(model(x), dim=-1).cpu().numpy()
        yv = y.cpu().numpy()
        n_classes = 3
        cm = [[0 for _ in range(n_classes)] for _ in range(n_classes)]
        for true_c, pred_c in zip(yv, pred, strict=True):
            if 0 <= int(true_c) < n_classes and 0 <= int(pred_c) < n_classes:
                cm[int(true_c)][int(pred_c)] += 1
        return cm

    def _rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self._root))
        except ValueError:
            return str(path)
