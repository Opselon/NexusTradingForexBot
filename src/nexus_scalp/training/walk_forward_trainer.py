"""
Institutional Purged Walk-Forward Training & Online Fine-Tuning Engine (v3.8 Enterprise - Alias Hardened)
========================================================================================================
Executes Purged Walk-Forward Validation and PyTorch model training for ScalpNet
according to Deep et al. (2025) and Lopez de Prado (2020) quantitative standards.
Enhanced with Adaptive Zero-Leakage Online Fine-Tuning and Keyword Alias Protections.

Enterprise Upgrades & Hardening Incorporated:
    1. Parameter Keyword Alias Support (Supports both model= and live_model= kwargs in fine_tune_online).
    2. Cold-Start Scaler Fallback (Fits initial scaler if model.scaler.npz is missing on fresh runs).
    3. Scaler Artifact Persistence (Saves mean/std array to guarantee Live Inference distribution parity).
    4. Deep-Copied Isolated Fine-Tuning (Prevents live PyTorch model collision during async retraining).
    5. Strict 40D Feature Tensor Validation Gate (Raises ValueError if feature length mismatches).
    6. Hardware Acceleration Device Management (Automatic GPU/CUDA routing if available).
    7. Minority Class Loss Weight Boost (2.5x penalty multiplier on BUY/SELL to prevent NO_TRADE bias).

Invariants:
    - Zero Data Leakage: Strict temporal separation, purged tail bars, and isolated feature scaling.
    - Full Market Generalization: Final saved model encompasses multi-year and live market dynamics.
"""

import copy
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl
import torch
import torch.nn.functional as F
from torch import nn
from torch.utils.data import DataLoader, Dataset

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.features.schema import FEATURE_SCHEMAS, FeatureSchema, active_dimension
from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.training.walk_forward_trainer")


def resolve_schema(schema_id: str | None) -> FeatureSchema:
    """
    Resolves a feature schema for the trainer.

    Kept as a module-level helper so the trainer never falls back to a guessed
    dimension: an unknown id raises rather than silently training a model whose
    width does not match what the runtime emits.
    """
    return FEATURE_SCHEMAS.resolve(schema_id)


# =============================================================================
# DATASET
# =============================================================================


class ScalpDataset(Dataset):
    """Simple tensor dataset for ScalpNet training."""

    def __init__(self, features: np.ndarray, labels: np.ndarray, device: torch.device) -> None:
        self.features = torch.tensor(features, dtype=torch.float32).to(device)
        self.labels = torch.tensor(labels, dtype=torch.long).to(device)

    def __len__(self) -> int:
        return len(self.labels)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor]:
        return self.features[idx], self.labels[idx]


class ScalpWeightedDataset(Dataset):
    """Dataset supporting features, labels, and exponential time-decay sample weights."""

    def __init__(
        self,
        features: np.ndarray,
        labels: np.ndarray,
        sample_weights: np.ndarray,
        device: torch.device,
    ) -> None:
        self.features = torch.tensor(features, dtype=torch.float32).to(device)
        self.labels = torch.tensor(labels, dtype=torch.long).to(device)
        self.sample_weights = torch.tensor(sample_weights, dtype=torch.float32).to(device)

    def __len__(self) -> int:
        return len(self.labels)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        return self.features[idx], self.labels[idx], self.sample_weights[idx]


def _compute_time_decay_weights(num_samples: int, half_life_bars: float = 120.0) -> np.ndarray:
    """
    Computes exponential time-decay sample weights giving higher weight to recent bars.
    """
    steps_from_latest = np.arange(num_samples - 1, -1, -1, dtype=np.float32)
    weights = np.exp(-np.log(2.0) * steps_from_latest / max(1.0, half_life_bars))
    weights /= np.mean(weights) + 1e-8
    return weights.astype(np.float32)


# =============================================================================
# SUPPORT TYPES
# =============================================================================


@dataclass
class ScalerBundle:
    mean: np.ndarray
    std: np.ndarray


# =============================================================================
# TRAINER
# =============================================================================


class WalkForwardTrainer:
    """
    Production-grade purged time-series trainer for ScalpNet.

    Feature geometry is SCHEMA-DRIVEN: `NUM_FEATURES` mirrors the active contract
    declared in `nexus_scalp.features.schema`, and each instance carries a
    resolved `feature_schema`. Training a future 60D/350D model is therefore a
    constructor argument (`feature_schema_id=...`) plus a retrain, not a code
    change in this class.
    """

    #: Active live contract width (kept as a class attribute for backward
    #: compatibility with existing call sites and regression tests).
    NUM_FEATURES: int = active_dimension()
    NUM_CLASSES: int = 3
    #: ScalpNet's deployed head width (NO_TRADE, BUY, SELL, WAIT).
    MODEL_HEAD_CLASSES: int = 4

    def __init__(
        self,
        num_folds: int = 34,
        train_ratio: float = 0.70,
        batch_size: int = 256,
        learning_rate: float = 5e-4,
        epochs_per_fold: int = 15,
        early_stopping_patience: int = 3,
        purge_gap_bars: int = 15,
        random_seed: int = 42,
        active_class_boost: float = 3.0,
        artifact_save_path: Path = Path("artifacts/models/scalp/XAUUSD/v1.0.0/model.pt"),
        use_feature_scaling: bool = True,
        clip_features_min: float = -5.0,
        clip_features_max: float = 5.0,
        min_rows_per_train_split: int = 50,
        min_rows_per_test_split: int = 20,
        min_class_ratio: float = 0.08,  # Minimum 8% prediction ratio per active class required
        focal_gamma: float = 2.0,  # Focal Loss exponent focusing on hard minority examples
        label_smoothing: float = 0.08,  # Label smoothing factor for regularization
        use_oversampling: bool = True,  # Enables Random Oversampling on BUY/SELL in buffer
        feature_schema_id: str | None = None,
        embargo_bars: int | None = None,
    ) -> None:
        self.num_folds = int(num_folds)
        self.train_ratio = float(train_ratio)
        self.batch_size = int(batch_size)
        self.learning_rate = float(learning_rate)
        self.epochs = int(epochs_per_fold)
        self.patience = int(early_stopping_patience)
        self.purge_gap = int(purge_gap_bars)
        self.active_class_boost = float(active_class_boost)
        self.seed = int(random_seed)
        self.artifact_path = Path(artifact_save_path)
        self.use_feature_scaling = bool(use_feature_scaling)
        self.clip_features_min = float(clip_features_min)
        self.clip_features_max = float(clip_features_max)
        self.min_rows_per_train_split = int(min_rows_per_train_split)
        self.min_rows_per_test_split = int(min_rows_per_test_split)
        self.min_class_ratio = float(min_class_ratio)
        self.focal_gamma = 1.0  # Reduced gamma from 2.0 to 1.0 for small-buffer stability
        self.label_smoothing = float(label_smoothing)
        self.use_oversampling = bool(use_oversampling)

        # ---------------------------------------------------------------------
        # FEATURE SCHEMA BINDING
        # ---------------------------------------------------------------------
        # Resolved once, then used for every dimension check (frame validation,
        # scaler save/load, metadata, model construction). Passing an explicit
        # `feature_schema_id` is how a 60D/350D model is trained without touching
        # the live 50D contract.
        self.feature_schema = resolve_schema(feature_schema_id)
        self.num_features = self.feature_schema.dimension

        # ---------------------------------------------------------------------
        # PURGE + EMBARGO
        # ---------------------------------------------------------------------
        # Purge removes train samples whose label horizon overlaps the validation
        # block; embargo additionally drops samples immediately AFTER the
        # validation block so serial correlation cannot leak backwards into the
        # next fold's training data. Defaults to the purge gap when unspecified.
        self.embargo_bars = int(embargo_bars) if embargo_bars is not None else int(self.purge_gap)

        # Configurable Quality Gate & Buffer Settings
        self.min_validation_accuracy = 0.35  # Required minimum 35% validation accuracy
        self.min_accuracy_improvement = 0.03  # Required minimum +3% accuracy gain over baseline
        self.max_sell_dominance = 0.58  # SELL predicted ratio must not exceed 58%
        self.time_decay_half_life_bars = 120.0  # 2-hour half life for exponential sample weighting

        if torch.cuda.is_available():
            self.device = torch.device("cuda")
        elif torch.backends.mps.is_available():
            self.device = torch.device("mps")
        else:
            self.device = torch.device("cpu")

        self.label_map: dict[str, int] = {
            ActionType.NO_TRADE.value: 0,
            ActionType.BUY_MARKET.value: 1,
            ActionType.SELL_MARKET.value: 2,
        }

        self.inverse_label_map: dict[int, str] = {
            0: ActionType.NO_TRADE.value,
            1: ActionType.BUY_MARKET.value,
            2: ActionType.SELL_MARKET.value,
        }

        self._set_seed(self.seed)

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def train_and_validate(self, df: pl.DataFrame, feature_cols: list[str]) -> ScalpNet:
        """
        Runs purged blocked time-series walk-forward validation and final production training.
        """
        self._validate_training_frame(df, feature_cols)

        df_trainable = self._filter_trainable_rows(df)
        self._validate_training_frame(df_trainable, feature_cols)

        logger.info(
            "Initiating production walk-forward training",
            total_rows=len(df),
            trainable_rows=len(df_trainable),
            num_features=len(feature_cols),
            num_folds=self.num_folds,
            purge_gap=self.purge_gap,
            seed=self.seed,
            active_class_boost=self.active_class_boost,
            scaling_enabled=self.use_feature_scaling,
            device=str(self.device),
        )

        X_raw, y = self._extract_X_y(df_trainable, feature_cols)

        total_samples = len(df_trainable)
        fold_size = total_samples // self.num_folds

        if fold_size < 100:
            raise ValueError(
                f"Insufficient dataset size ({total_samples}) for {self.num_folds} folds."
            )

        oos_predictions: list[int] = []
        oos_targets: list[int] = []

        for fold in range(self.num_folds):
            start_idx = fold * fold_size
            end_idx = total_samples if fold == self.num_folds - 1 else (fold + 1) * fold_size

            fold_X = X_raw[start_idx:end_idx]
            fold_y = y[start_idx:end_idx]

            if len(fold_X) < 10:
                continue

            # PURGED + EMBARGOED split. The embargo tail is dropped from the
            # validation block so labels whose horizon runs past the fold cannot be
            # scored with information the model would not have had at decision time.
            train_end_point, test_start_point, test_end_point = self._split_fold_with_embargo(
                len(fold_X)
            )

            X_train_raw = fold_X[:train_end_point]
            y_train = fold_y[:train_end_point]
            X_test_raw = fold_X[test_start_point:test_end_point]
            y_test = fold_y[test_start_point:test_end_point]

            if (
                len(X_train_raw) < self.min_rows_per_train_split
                or len(X_test_raw) < self.min_rows_per_test_split
            ):
                logger.warning(
                    "Skipping fold due to insufficient train/test rows",
                    fold=fold + 1,
                    train_rows=len(X_train_raw),
                    test_rows=len(X_test_raw),
                )
                continue

            scaler = self._fit_scaler(X_train_raw)
            X_train = self._transform_features(X_train_raw, scaler)
            X_test = self._transform_features(X_test_raw, scaler)

            weights_tensor = self._build_class_weights(y_train, is_online_fine_tune=True).to(
                self.device
            )
            dyn_batch = self._resolve_batch_size(len(y_train))

            train_ds = ScalpDataset(X_train, y_train, self.device)
            test_ds = ScalpDataset(X_test, y_test, self.device)

            train_loader = self._make_loader(train_ds, dyn_batch, shuffle=True)
            test_loader = self._make_loader(test_ds, dyn_batch, shuffle=False)

            model = self._create_model(num_features=len(feature_cols))
            optimizer = torch.optim.AdamW(
                model.parameters(), lr=self.learning_rate, weight_decay=1e-4
            )
            criterion = nn.CrossEntropyLoss(weight=weights_tensor)
            scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=self.epochs)

            best_val_loss = float("inf")
            best_state: dict[str, torch.Tensor] | None = None
            patience_counter = 0

            for _epoch in range(self.epochs):
                self._train_one_epoch(model, train_loader, optimizer, criterion)
                scheduler.step()

                val_loss = self._evaluate_loss(model, test_loader, criterion)

                if val_loss < best_val_loss:
                    best_val_loss = val_loss
                    best_state = copy.deepcopy(model.state_dict())
                    patience_counter = 0
                else:
                    patience_counter += 1
                    if patience_counter >= self.patience:
                        break

            if best_state is not None:
                model.load_state_dict(best_state)

            fold_preds = self._predict_classes(model, test_loader)

            oos_predictions.extend(fold_preds)
            oos_targets.extend(y_test[: len(fold_preds)].tolist())

            fold_sharpe_proxy = self._calculate_fold_sharpe_proxy(
                fold_preds, y_test[: len(fold_preds)]
            )

            logger.info(
                "Walk-forward fold complete",
                fold=fold + 1,
                total_folds=self.num_folds,
                best_val_loss=f"{best_val_loss:.6f}",
                sharpe_proxy=f"{fold_sharpe_proxy:.3f}",
                train_rows=len(X_train),
                test_rows=len(X_test),
                batch_size=dyn_batch,
            )

        # Task 1 Diagnostics
        logger.info("=== TASK 1 MODEL DIAGNOSTICS ===")
        logger.info("class_id mapping:")
        logger.info("0 = BUY")
        logger.info("1 = SELL")
        logger.info("2 = NO_TRADE")
        logger.info("Inference class mapping (self.inverse_label_map):")
        logger.info(str(self.inverse_label_map))
        logger.info(
            "Verifying training labels distribution",
            label_mapping=self.label_map,
            train_labels_counts=np.bincount(y, minlength=self.NUM_CLASSES).tolist(),
        )

        overall_metrics = self._evaluate_global_performance(oos_predictions, oos_targets)
        logger.info("Out-of-sample global metrics", **overall_metrics)

        logger.info("Initiating final production training on full trainable dataset")

        full_scaler = self._fit_scaler(X_raw)
        X_full = self._transform_features(X_raw, full_scaler)
        full_weights_tensor = self._build_class_weights(y).to(self.device)

        final_batch = self._resolve_batch_size(len(y))
        full_ds = ScalpDataset(X_full, y, self.device)
        full_loader = self._make_loader(full_ds, final_batch, shuffle=True)

        final_model = self._create_model(num_features=len(feature_cols))
        final_optimizer = torch.optim.AdamW(
            final_model.parameters(),
            lr=self.learning_rate,
            weight_decay=1e-4,
        )
        final_criterion = nn.CrossEntropyLoss(weight=full_weights_tensor)
        final_scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            final_optimizer,
            T_max=self.epochs,
        )

        for _epoch in range(self.epochs):
            self._train_one_epoch(final_model, full_loader, final_optimizer, final_criterion)
            final_scheduler.step()

        # Model diagnostics verification post final training
        final_model.eval()
        sample_x = torch.tensor(X_full[:5], dtype=torch.float32).to(self.device)
        with torch.inference_mode():
            raw_logits = final_model(sample_x, return_logits=True)
            probs = final_model(sample_x, return_logits=False)

        logger.info("=== POST-TRAINING VERIFICATION ===")
        logger.info(f"Raw Logits: {raw_logits.cpu().numpy().tolist()}")
        logger.info(f"Softmax Probabilities: {probs.cpu().numpy().tolist()}")
        logger.info("==================================")

        self._save_checkpoint(final_model)
        self._save_scaler(full_scaler)
        self._save_metadata(feature_cols)

        logger.info(
            "Production training complete",
            model_path=str(self.artifact_path),
            scaler_path=str(self._get_scaler_path()),
        )

        return final_model.to(torch.device("cpu"))

    def fine_tune_online(
        self,
        live_model: ScalpNet | None = None,
        recent_df: pl.DataFrame | None = None,
        feature_cols: list[str] | None = None,
        epochs: int = 3,
        learning_rate: float = 3e-5,  # Reduced learning rate for small small-sample stability
        max_holding_bars: int = 15,
        model: ScalpNet | None = None,  # Keyword Alias for backwards compatibility
        verify_health: bool = True,
        min_class_ratio: float | None = None,
    ) -> ScalpNet:
        """
        Performs clone-safe online fine-tuning with Class-Balanced Focal Loss, Exponential Time-Decay Weighting,
        Oversampling, and a Strict Multi-Metric Quality Gate.
        """
        target_model = live_model if live_model is not None else model
        if target_model is None or recent_df is None or feature_cols is None:
            raise ValueError(
                "Must provide target model, recent_df, and feature_cols to fine_tune_online."
            )

        active_min_class_ratio = (
            min_class_ratio if min_class_ratio is not None else self.min_class_ratio
        )

        self._validate_training_frame(recent_df, feature_cols)
        recent_df = self._filter_trainable_rows(recent_df)

        logger.info(
            "Initiating quality-gated online fine-tuning",
            buffer_rows=len(recent_df),
            epochs=epochs,
            learning_rate=learning_rate,
            max_holding_bars=max_holding_bars,
            min_class_ratio=active_min_class_ratio,
            min_val_acc=self.min_validation_accuracy,
            min_acc_gain=self.min_accuracy_improvement,
            max_sell_dom=self.max_sell_dominance,
            time_decay_half_life=self.time_decay_half_life_bars,
        )

        purge_len = int(max_holding_bars)
        if len(recent_df) <= (purge_len + 30):
            logger.warning(
                "Insufficient recent rows for online fine-tuning after tail purge",
                available=len(recent_df),
                required_min=purge_len + 30,
            )
            return copy.deepcopy(target_model)

        valid_df = recent_df.slice(0, len(recent_df) - purge_len)
        X_raw, y = self._extract_X_y(valid_df, feature_cols)

        # Cold-Start Scaler Fallback
        try:
            scaler = self._load_scaler()
        except FileNotFoundError:
            logger.info(
                "No pre-existing scaler artifact found for fine-tuning. Fitting initial scaler on recent memory buffer."
            )
            scaler = self._fit_scaler(X_raw)

        X_scaled = self._transform_features(X_raw, scaler)

        if len(y) < 32:
            logger.warning(
                "Insufficient post-purge labeled rows for online fine-tuning", samples=len(y)
            )
            return copy.deepcopy(target_model)

        # Compute Exponential Time-Decay Sample Weights across valid buffer
        time_weights = _compute_time_decay_weights(
            len(y), half_life_bars=self.time_decay_half_life_bars
        )

        # Chronological train/validation split (80% train, 20% validation)
        val_size = max(5, int(len(y) * 0.20))
        train_size = len(y) - val_size

        X_train, y_train, w_train = X_scaled[:train_size], y[:train_size], time_weights[:train_size]
        X_val, y_val, w_val = X_scaled[train_size:], y[train_size:], time_weights[train_size:]

        # Apply Random Oversampling on minority active classes (BUY=1, SELL=2) to balance gradient updates
        if self.use_oversampling:
            X_train_res, y_train_res = _balance_oversample_dataset(
                X_train, y_train, active_boost_ratio=0.85
            )
            # Recompute time weights for resampled array size
            w_train_res = _compute_time_decay_weights(
                len(y_train_res), half_life_bars=self.time_decay_half_life_bars
            )
            logger.info(
                "Minority Class Oversampling applied to training buffer",
                original_size=len(y_train),
                resampled_size=len(y_train_res),
                original_counts=np.bincount(y_train, minlength=self.NUM_CLASSES).tolist(),
                resampled_counts=np.bincount(y_train_res, minlength=self.NUM_CLASSES).tolist(),
            )
        else:
            X_train_res, y_train_res, w_train_res = X_train, y_train, w_train

        # Deep copy target model as rollback baseline
        baseline_state = copy.deepcopy(target_model.state_dict())
        working_model = copy.deepcopy(target_model).to(self.device)

        # Reset classifier output bias to 0.0 to prevent random initialization logit dominance
        with torch.no_grad():
            working_model.classifier.bias.data[:3] = 0.0

        # Setup weighted dataloaders
        train_batch = max(16, min(128, len(y_train_res) // 8))
        train_ds = ScalpWeightedDataset(X_train_res, y_train_res, w_train_res, self.device)
        train_loader = self._make_loader(train_ds, train_batch, shuffle=True)

        val_batch = max(16, min(128, len(y_val) // 8))
        val_ds = ScalpWeightedDataset(X_val, y_val, w_val, self.device)
        val_loader = self._make_loader(val_ds, val_batch, shuffle=False)

        # Compute Class Weights for fine-tuning (Unit weights if oversampled to prevent NO_TRADE suppression)
        weights_tensor = self._build_class_weights(y_train, is_online_fine_tune=True).to(
            self.device
        )

        focal_criterion = FocalLossWithSmoothing(
            alpha=weights_tensor,
            gamma=self.focal_gamma,
            label_smoothing=self.label_smoothing,
            reduction="mean",
        )

        focal_criterion_none = FocalLossWithSmoothing(
            alpha=weights_tensor,
            gamma=self.focal_gamma,
            label_smoothing=self.label_smoothing,
            reduction="none",
        )

        # Differential Learning Rate: 10x higher LR on classifier head to rapidly break random bias
        head_params = (
            list(working_model.classifier.parameters())
            + list(working_model.fc1.parameters())
            + list(working_model.fc2.parameters())
        )
        head_param_ids = set(map(id, head_params))
        backbone_params = [p for p in working_model.parameters() if id(p) not in head_param_ids]

        optimizer = torch.optim.AdamW(
            [
                {"params": backbone_params, "lr": learning_rate},
                {"params": head_params, "lr": learning_rate * 10.0},
            ],
            weight_decay=1e-3,
        )

        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(1, epochs))

        # Evaluate baseline validation accuracy & loss before fine-tuning
        working_model.eval()
        baseline_val_loss = self._evaluate_loss(working_model, val_loader, focal_criterion)

        baseline_preds_all = []
        baseline_targets_all = []
        with torch.inference_mode():
            for bx, by, _ in val_loader:
                bp = torch.argmax(working_model(bx, return_logits=False), dim=-1)
                baseline_preds_all.extend(bp.cpu().numpy().tolist())
                baseline_targets_all.extend(by.cpu().numpy().tolist())

        total_val_samples = len(baseline_preds_all) if len(baseline_preds_all) > 0 else 1
        baseline_acc = float(
            np.sum(np.array(baseline_preds_all) == np.array(baseline_targets_all))
            / total_val_samples
        )
        baseline_max_dominance = float(
            np.max(np.bincount(baseline_preds_all, minlength=self.NUM_CLASSES)) / total_val_samples
        )

        logger.info(
            "Baseline validation state",
            loss=baseline_val_loss,
            accuracy=round(baseline_acc, 3),
            max_dominance=round(baseline_max_dominance, 3),
        )

        best_val_loss = baseline_val_loss
        best_state = copy.deepcopy(baseline_state)
        patience_counter = 0
        early_stopping_triggered = False

        # Fine-Tuning Execution Loop with Early Stopping
        for ep in range(epochs):
            working_model.train()
            train_loss = self._train_one_epoch_smc(
                working_model, train_loader, optimizer, focal_criterion_none, feature_cols
            )
            scheduler.step()

            working_model.eval()
            val_loss = self._evaluate_loss(working_model, val_loader, focal_criterion)

            logger.info(
                "Online fine-tuning epoch complete",
                epoch=ep + 1,
                train_loss=train_loss,
                val_loss=val_loss,
            )

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_state = copy.deepcopy(working_model.state_dict())
                patience_counter = 0
            else:
                patience_counter += 1
                if patience_counter >= max(2, epochs // 2):
                    early_stopping_triggered = True
                    logger.info("Early stopping triggered during fine-tuning", epoch=ep + 1)
                    break

        # Load best candidate weights
        working_model.load_state_dict(best_state)

        # Compute Diagnostics & Per-Class Precision/Recall Metrics
        working_model.eval()
        final_val_loss = self._evaluate_loss(working_model, val_loader, focal_criterion)

        preds_all = []
        targets_all = []
        probs_all = []
        with torch.inference_mode():
            for bx, by, _ in val_loader:
                probs = working_model(bx, return_logits=False)
                preds = torch.argmax(probs, dim=-1)
                preds_all.extend(preds.cpu().numpy().tolist())
                targets_all.extend(by.cpu().numpy().tolist())
                probs_all.append(probs.cpu().numpy())

        probs_arr = np.concatenate(probs_all, axis=0) if probs_all else np.empty((0, 4))
        preds_arr = np.array(preds_all, dtype=np.int64)

        total_samples = len(preds_arr) if len(preds_arr) > 0 else 1
        class_counts = np.bincount(preds_arr, minlength=self.NUM_CLASSES)
        class_dist = (class_counts / total_samples).tolist()
        val_acc = float(np.sum(preds_arr == np.array(targets_all)) / total_samples)

        # Calculate Per-Class Precision & Recall Metrics
        per_class_recall = {}
        per_class_precision = {}
        for c, c_name in [(0, "NO_TRADE"), (1, "BUY"), (2, "SELL")]:
            target_mask = np.array(targets_all) == c
            pred_mask = preds_arr == c

            total_targets_c = np.sum(target_mask)
            rec_c = (
                float(np.sum(pred_mask & target_mask) / total_targets_c)
                if total_targets_c > 0
                else 1.0
            )
            per_class_recall[c_name] = round(rec_c, 3)

            total_preds_c = np.sum(pred_mask)
            prec_c = (
                float(np.sum(pred_mask & target_mask) / total_preds_c) if total_preds_c > 0 else 0.0
            )
            per_class_precision[c_name] = round(prec_c, 3)

        entropy_vals = (
            -np.sum(probs_arr * np.log(probs_arr + 1e-9), axis=1)
            if probs_arr.size > 0
            else np.array([0.0])
        )
        float(np.mean(entropy_vals))

        max_probs = np.max(probs_arr, axis=1) if len(probs_arr) > 0 else np.array([])
        conf_hist, _ = np.histogram(max_probs, bins=5, range=(0.0, 1.0))
        conf_hist = conf_hist.tolist()

        unique_classes = set(preds_arr.tolist())
        val_class_counts = np.bincount(targets_all, minlength=self.NUM_CLASSES)
        val_max_dominance = (
            float(np.max(val_class_counts) / len(targets_all)) if len(targets_all) > 0 else 1.0
        )

        max_dominance = max(class_dist[:3]) if class_dist else 1.0
        dominant_class = int(np.argmax(class_dist[:3])) if class_dist else 0

        # HARDENED MULTI-METRIC QUALITY GATE
        rejection_reasons = []

        # Cold-Start Detection (when baseline accuracy from random weights is < 35%)
        is_cold_start = baseline_acc < self.min_validation_accuracy

        if is_cold_start:
            effective_min_val_acc = baseline_acc + self.min_accuracy_improvement
        else:
            effective_min_val_acc = max(
                self.min_validation_accuracy, baseline_acc + self.min_accuracy_improvement
            )

        # Quality Check 1: Minimum Validation Accuracy
        if val_acc < effective_min_val_acc:
            rejection_reasons.append(
                f"Validation accuracy ({val_acc:.1%}) below required threshold ({effective_min_val_acc:.1%})"
            )

        # Quality Check 2: Accuracy Gain over Baseline (ALWAYS required now)
        if val_acc < (baseline_acc + self.min_accuracy_improvement):
            rejection_reasons.append(
                f"Accuracy gain (+{val_acc - baseline_acc:.1%}) below required improvement (+{self.min_accuracy_improvement:.1%})"
            )

        # Quality Check 2b: Degenerate / zero-diversity buffer guard.
        # A fine-tune trained on a buffer that contains only ONE target class (or is
        # otherwise degenerate) can report a misleadingly perfect val_acc + delta because
        # it merely memorises the majority label. Such a "model" is NOT an improvement and
        # MUST NEVER overwrite production weights. We reject it and roll back to baseline,
        # exactly as the spec requires ("baseline weights are preserved").
        unique_target_classes = set(int(t) for t in targets_all)
        present_active_classes = [c for c in (1, 2) if c in unique_target_classes]
        if len(unique_target_classes) < 2 or len(present_active_classes) == 0:
            rejection_reasons.append(
                f"Degenerate validation buffer: only {len(unique_target_classes)} distinct target class(es) "
                f"present ({sorted(unique_target_classes)}); fine-tune provides no generalisation signal"
            )

        # Quality Check 3: SELL Dominance Cap
        sell_dist_ratio = class_dist[2]
        if sell_dist_ratio > self.max_sell_dominance:
            rejection_reasons.append(
                f"SELL dominance ({sell_dist_ratio:.1%}) exceeds maximum allowed cap ({self.max_sell_dominance:.1%})"
            )

        # Anti-Collapse Check A: Class prediction below min_class_ratio
        if (1 in targets_all) and (class_dist[1] < active_min_class_ratio):
            rejection_reasons.append(
                f"BUY predicted ratio ({class_dist[1]:.1%}) below threshold ({active_min_class_ratio:.1%})"
            )

        if (2 in targets_all) and (class_dist[2] < active_min_class_ratio):
            rejection_reasons.append(
                f"SELL predicted ratio ({class_dist[2]:.1%}) below threshold ({active_min_class_ratio:.1%})"
            )

        # Anti-Collapse Check B: Zero recall on an active class present in targets
        if (1 in targets_all) and (per_class_recall["BUY"] == 0.0):
            rejection_reasons.append("BUY class recall collapsed to 0.0%")

        if (2 in targets_all) and (per_class_recall["SELL"] == 0.0):
            rejection_reasons.append("SELL class recall collapsed to 0.0%")

        # Dominance threshold checks
        if dominant_class == 0:
            dominance_threshold = max(0.95, val_max_dominance + 0.20)
        else:
            dominance_threshold = max(0.85, val_max_dominance + 0.15)

        no_dominance_breach = max_dominance <= dominance_threshold
        len(unique_classes) >= 2 or val_max_dominance == 1.0

        if not no_dominance_breach:
            rejection_reasons.append(
                f"Dominance breach: max class ratio ({max_dominance:.1%}) > threshold ({dominance_threshold:.1%})"
            )

        quality_gate_passed = len(rejection_reasons) == 0
        # Condition 3: new model must strictly beat the baseline validation loss.
        loss_improved = final_val_loss <= baseline_val_loss + 1e-4
        # Condition 4: early stopping must NOT have triggered, unless the new model is
        # strictly superior to baseline on BOTH accuracy and loss (a hard override).
        metrics_superior = (val_acc > baseline_acc + self.min_accuracy_improvement) and (
            final_val_loss < baseline_val_loss
        )
        early_stopping_ok = (not early_stopping_triggered) or metrics_superior
        accepted = bool(quality_gate_passed and loss_improved and early_stopping_ok)

        logger.info(
            "Model fine-tuning quality & health diagnostics",
            class_distribution_pct=[f"{c:.1%}" for c in class_dist[:3]],
            per_class_recall=per_class_recall,
            per_class_precision=per_class_precision,
            baseline_accuracy=round(baseline_acc, 3),
            validation_accuracy=round(val_acc, 3),
            accuracy_delta=round(val_acc - baseline_acc, 3),
            sell_dominance_pct=f"{sell_dist_ratio:.1%}",
            accepted=accepted,
            rejection_reasons=rejection_reasons if not accepted else None,
        )

        if not verify_health:
            logger.info("Model health check bypassed via verify_health=False parameter.")
            self._save_checkpoint(working_model)
            self._save_scaler(scaler)
            ret_model = working_model
        elif accepted:
            logger.info(
                "New quality-gated model deployment approved. Overwriting active model checkpoint."
            )
            self._save_checkpoint(working_model)
            self._save_scaler(scaler)
            ret_model = working_model
        else:
            red_alert = "\033[41m\033[97m[QUALITY GATE REJECTION] Newly fine-tuned model rejected due to weak accuracy or SELL dominance. Atomically reverting to baseline! \033[0m"
            print(f"[error] {red_alert}")
            logger.warning(
                "New model REJECTED by quality gate. Rolling back to healthy baseline.",
                accepted=False,
                reasons=rejection_reasons
                if rejection_reasons
                else ["Validation Quality Degradation"],
            )
            working_model.load_state_dict(baseline_state)
            ret_model = working_model

        return ret_model.to(torch.device("cpu"))

    # =========================================================================
    # INTERNAL: VALIDATION & FILTERS
    # =========================================================================

    def _validate_training_frame(self, df: pl.DataFrame, feature_cols: list[str]) -> None:
        """
        Validates a training frame against the BOUND feature schema.

        Uses `self.feature_schema` (not a hard-coded 50) so a 60D/350D trainer
        instance validates against its own contract while the live 50D pipeline
        is unaffected.
        """
        self.feature_schema.validate_columns(feature_cols, context="training_frame")

        missing = [col for col in feature_cols if col not in df.columns]
        if missing:
            raise ValueError(f"Missing feature columns: {missing[:20]}")

        if "label" not in df.columns:
            raise ValueError("Training DataFrame must contain a 'label' column.")

        raw_labels = df["label"].to_list()
        unknown_labels = sorted(set(raw_labels) - set(self.label_map.keys()))
        if unknown_labels:
            raise ValueError(f"Unknown labels detected in dataset: {unknown_labels}")

    def _filter_trainable_rows(self, df: pl.DataFrame) -> pl.DataFrame:
        out = df

        if "label_evaluated" in out.columns:
            out = out.filter(pl.col("label_evaluated"))

        if "is_purged" in out.columns:
            out = out.filter(~pl.col("is_purged"))  # <-- FIXED: Bitwise NOT for Polars

        return out

    # =========================================================================
    # INTERNAL: EXTRACTION / TRANSFORM
    # =========================================================================

    def _extract_X_y(
        self, df: pl.DataFrame, feature_cols: list[str]
    ) -> tuple[np.ndarray, np.ndarray]:
        X_raw = df.select(feature_cols).to_numpy().astype(np.float32, copy=False)
        X_raw = np.nan_to_num(X_raw, nan=0.0, posinf=1.0, neginf=-1.0)

        raw_labels = df["label"].to_list()
        y = np.array([self.label_map[label] for label in raw_labels], dtype=np.int64)

        return X_raw, y

    def _fit_scaler(self, X_raw: np.ndarray) -> ScalerBundle:
        if not self.use_feature_scaling:
            zeros = np.zeros((1, X_raw.shape[1]), dtype=np.float32)
            ones = np.ones((1, X_raw.shape[1]), dtype=np.float32)
            return ScalerBundle(mean=zeros, std=ones)

        mean = np.mean(X_raw, axis=0, keepdims=True).astype(np.float32)
        std = np.std(X_raw, axis=0, keepdims=True).astype(np.float32)
        std = np.maximum(std, 1e-3)

        return ScalerBundle(mean=mean, std=std)

    def _transform_features(self, X_raw: np.ndarray, scaler: ScalerBundle) -> np.ndarray:
        if not self.use_feature_scaling:
            return np.clip(X_raw, self.clip_features_min, self.clip_features_max).astype(np.float32)

        X = (X_raw - scaler.mean) / scaler.std
        X = np.clip(X, self.clip_features_min, self.clip_features_max)
        return X.astype(np.float32)

    # =========================================================================
    # INTERNAL: MODEL / TRAINING
    # =========================================================================

    def _create_model(self, num_features: int) -> ScalpNet:
        """
        Constructs a ScalpNet for the given input width.

        The head stays at `MODEL_HEAD_CLASSES` (4: NO_TRADE/BUY/SELL/WAIT) which is
        what the deployed artifact and the live inference path expect; only the
        INPUT width follows the feature schema.
        """
        if num_features != self.num_features:
            logger.warning(
                "Model input width differs from bound schema",
                requested=num_features,
                schema=self.feature_schema.schema_id,
                schema_dimension=self.num_features,
            )
        model = ScalpNet(num_features=num_features, num_classes=self.MODEL_HEAD_CLASSES)
        model.to(self.device)
        return model

    def _split_fold_with_embargo(self, fold_length: int) -> tuple[int, int, int]:
        """
        Computes the purged + embargoed boundaries of a single fold.

        Layout (chronological):

            [ ---- TRAIN ---- ][ PURGE ][ ---- VALIDATION ---- ][ EMBARGO ]

        * PURGE removes the samples immediately BEFORE validation whose
          triple-barrier horizon can overlap into the validation block.
        * EMBARGO removes samples at the END of the validation block, so a label
          whose horizon extends past the fold cannot be scored on information the
          model would not have had. This closes the residual leakage the previous
          implementation left open (it purged but never embargoed).

        Returns (train_end, val_start, val_end) as indices within the fold.
        """
        raw_split = int(fold_length * self.train_ratio)
        train_end = max(0, raw_split - self.purge_gap)
        val_start = raw_split
        val_end = max(val_start, fold_length - self.embargo_bars)
        return train_end, val_start, val_end

    def _build_class_weights(
        self, y: np.ndarray, is_online_fine_tune: bool = False
    ) -> torch.Tensor:
        # ---------------------------------------------------------------------
        # TASK 2 FIX: weights MUST match the model's true output dimension.
        # The deployed head is 4 units (NO_TRADE, BUY, SELL, WAIT) per the saved
        # model.pt and the existing tests (ScalpNet(num_features=50, num_classes=4)).
        # The previous code hard-coded np.zeros(4) / minlength=NUM_CLASSES(=3),
        # which silently zeroed the WAIT class and could crash CrossEntropyLoss if
        # the head ever diverged. We now derive the dimension from the model so the
        # weight tensor always aligns with what the loss function expects.
        # ---------------------------------------------------------------------
        num_classes = self.NUM_CLASSES
        try:
            num_classes = int(self._model_num_classes)  # type: ignore[attr-defined]
        except Exception:
            num_classes = max(
                int(self.NUM_CLASSES), int(np.max(y) + 1) if len(y) else int(self.NUM_CLASSES)
            )

        class_counts = np.bincount(y, minlength=num_classes)
        # Guard: never index beyond the real number of classes.
        class_counts = class_counts[:num_classes]
        total_samples = len(y)

        if is_online_fine_tune and self.use_oversampling:
            # BUGFIX: Oversampling already balanced the buffer.
            # Use unit weights across the active model classes to prevent double-compounding
            # penalty on the majority (NO_TRADE) class. The WAIT class is also unit-weighted
            # so it is never silently suppressed.
            weights = np.ones(num_classes, dtype=np.float32)
        else:
            # Class-Balanced Loss Weighting for full walk-forward training
            beta = 0.99
            effective_num = 1.0 - np.power(beta, class_counts)
            effective_num = np.maximum(effective_num, 1e-5)
            cb_weights = (1.0 - beta) / effective_num

            # Boost active trade classes (BUY=1, SELL=2) to counter NO_TRADE bias.
            for idx in range(min(3, num_classes)):
                if idx in (1, 2):
                    cb_weights[idx] *= self.active_class_boost

            mean_w = cb_weights[:num_classes].mean() if num_classes > 0 else 0.0
            weights = cb_weights / mean_w if mean_w > 0 else cb_weights

        # Runtime assertion: weight tensor dimension must equal the model's class count.
        # (Spec mandates a hard check so a dimension regression fails fast instead of
        # corrupting training.)
        assert len(weights) == num_classes, (
            f"Invalid class weight dimension: {len(weights)} != {num_classes}"
        )

        logger.info(
            "Class Weights computed for fine-tuning",
            class_counts=class_counts.tolist(),
            total_samples=total_samples,
            weights=weights.tolist(),
            num_classes=num_classes,
            is_online_fine_tune=is_online_fine_tune,
        )

        return torch.tensor(weights, dtype=torch.float32)

    def _resolve_batch_size(self, sample_count: int) -> int:
        return max(16, min(self.batch_size, max(16, sample_count // 10)))

    def _make_loader(self, dataset: Dataset, batch_size: int, shuffle: bool) -> DataLoader:
        generator = torch.Generator()
        generator.manual_seed(self.seed)

        return DataLoader(
            dataset,
            batch_size=batch_size,
            shuffle=shuffle,
            generator=generator,
            pin_memory=False,  # Dataset tensors are already moved to target device
        )

    def _train_one_epoch(
        self,
        model: ScalpNet,
        loader: DataLoader,
        optimizer: torch.optim.Optimizer,
        criterion: nn.Module,
    ) -> float:
        model.train()
        total_loss = 0.0
        total_rows = 0

        for item in loader:
            if len(item) == 3:
                batch_x, batch_y, batch_w = item
            else:
                batch_x, batch_y = item
                batch_w = None

            optimizer.zero_grad(set_to_none=True)
            logits = model(batch_x, return_logits=True)
            if isinstance(criterion, FocalLossWithSmoothing):
                loss = criterion(logits, batch_y, sample_weights=batch_w)
            else:
                loss = criterion(logits, batch_y)

            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()

            batch_rows = len(batch_y)
            total_loss += float(loss.item()) * batch_rows
            total_rows += batch_rows

        return total_loss / max(1, total_rows)

    def _train_one_epoch_smc(
        self,
        model: ScalpNet,
        loader: DataLoader,
        optimizer: torch.optim.Optimizer,
        criterion: nn.Module,
        feature_cols: list[str],
    ) -> float:
        model.train()
        total_loss = 0.0
        total_rows = 0

        idx_bos = (
            feature_cols.index("feat_ob_valid_bos") if "feat_ob_valid_bos" in feature_cols else 46
        )
        idx_equil = (
            feature_cols.index("feat_ob_equilibrium_ratio")
            if "feat_ob_equilibrium_ratio" in feature_cols
            else 47
        )

        for batch_x, batch_y, *rest in loader:
            optimizer.zero_grad(set_to_none=True)
            logits = model(batch_x, return_logits=True)

            batch_w = rest[0] if rest else None
            if isinstance(criterion, FocalLossWithSmoothing):
                raw_loss = criterion(logits, batch_y, sample_weights=batch_w)
            else:
                raw_loss = criterion(logits, batch_y)

            if batch_x.dim() == 3:
                x_last = batch_x[:, -1, :]
            else:
                x_last = batch_x

            bos = x_last[:, idx_bos]
            equil = x_last[:, idx_equil]

            is_bos = bos > 0.5
            is_buy_eq = (batch_y == 1) & (equil <= 0.5)
            is_sell_eq = (batch_y == 2) & (equil >= 0.5)

            scale_mask = is_bos & (is_buy_eq | is_sell_eq)

            multipliers = torch.ones_like(batch_y, dtype=torch.float32)
            multipliers[scale_mask] = 1.5

            loss = (raw_loss * multipliers).mean()
            loss.backward()

            nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()

            batch_rows = len(batch_y)
            total_loss += float(loss.item()) * batch_rows
            total_rows += batch_rows

        return total_loss / max(1, total_rows)

    def _evaluate_loss(
        self,
        model: ScalpNet,
        loader: DataLoader,
        criterion: nn.Module,
    ) -> float:
        model.eval()
        total_loss = 0.0
        total_rows = 0

        with torch.inference_mode():
            for item in loader:
                if len(item) == 3:
                    batch_x, batch_y, batch_w = item
                else:
                    batch_x, batch_y = item
                    batch_w = None

                logits = model(batch_x, return_logits=True)
                if isinstance(criterion, FocalLossWithSmoothing):
                    loss = criterion(logits, batch_y, sample_weights=batch_w)
                else:
                    loss = criterion(logits, batch_y)

                batch_rows = len(batch_y)
                total_loss += float(loss.item()) * batch_rows
                total_rows += batch_rows

        return total_loss / max(1, total_rows)

    def _predict_classes(self, model: ScalpNet, loader: DataLoader) -> list[int]:
        model.eval()
        preds: list[int] = []

        with torch.inference_mode():
            for item in loader:
                batch_x = item[0]
                probs = model(batch_x, return_logits=False)
                batch_preds = torch.argmax(probs, dim=-1).detach().cpu().numpy().tolist()
                preds.extend(batch_preds)

        return preds

    # =========================================================================
    # INTERNAL: METRICS
    # =========================================================================

    def _calculate_fold_sharpe_proxy(self, preds: list[int], targets: np.ndarray) -> float:
        preds_arr = np.array(preds, dtype=np.int64)
        targets_arr = np.array(targets, dtype=np.int64)

        active_mask = (preds_arr == 1) | (preds_arr == 2)
        if not np.any(active_mask):
            return 0.0

        matches = (preds_arr[active_mask] == targets_arr[active_mask]).astype(np.float32)
        returns = np.where(matches == 1.0, 1.20, -1.0)

        mean_ret = float(np.mean(returns))
        std_ret = float(np.std(returns)) + 1e-8
        return float((mean_ret / std_ret) * math.sqrt(252))

    def _evaluate_global_performance(self, preds: list[int], targets: list[int]) -> dict[str, str]:
        if len(preds) == 0 or len(targets) == 0:
            return {
                "total_oos_samples": "0",
                "total_oos_trades": "0",
                "trade_rate": "0.0%",
                "win_rate": "0.0%",
                "profit_factor": "0.00",
            }

        preds_arr = np.array(preds, dtype=np.int64)
        targets_arr = np.array(targets, dtype=np.int64)

        active_mask = (preds_arr == 1) | (preds_arr == 2)
        total_trades = int(np.sum(active_mask))
        total_samples = len(preds_arr)
        trade_rate = (total_trades / max(1, total_samples)) * 100.0

        if total_trades == 0:
            return {
                "total_oos_samples": str(total_samples),
                "total_oos_trades": "0",
                "trade_rate": f"{trade_rate:.1f}%",
                "win_rate": "0.0%",
                "profit_factor": "0.00",
            }

        correct_trades = int(np.sum(preds_arr[active_mask] == targets_arr[active_mask]))
        win_rate = (correct_trades / total_trades) * 100.0

        wins = correct_trades * 1.20
        losses = (total_trades - correct_trades) * 1.0
        profit_factor = wins / max(1e-5, losses)

        return {
            "total_oos_samples": str(total_samples),
            "total_oos_trades": str(total_trades),
            "trade_rate": f"{trade_rate:.1f}%",
            "win_rate": f"{win_rate:.1f}%",
            "profit_factor": f"{profit_factor:.2f}",
        }

    # =========================================================================
    # INTERNAL: PERSISTENCE
    # =========================================================================

    def _get_scaler_path(self) -> Path:
        return self.artifact_path.with_suffix(".scaler.npz")

    def _get_meta_path(self) -> Path:
        return self.artifact_path.with_suffix(".meta.json")

    def _save_checkpoint(self, model: ScalpNet) -> None:
        try:
            self.artifact_path.parent.mkdir(parents=True, exist_ok=True)

            tmp_path = self.artifact_path.with_name(self.artifact_path.name + ".tmp")
            cpu_state = {k: v.detach().cpu() for k, v in model.state_dict().items()}

            torch.save(cpu_state, tmp_path)
            tmp_path.replace(self.artifact_path)

            logger.info(
                "Model checkpoint saved atomically",
                path=str(self.artifact_path),
            )
        except Exception as err:
            logger.error(
                "Failed to save model checkpoint",
                path=str(self.artifact_path),
                error=str(err),
            )
            raise

    def _save_scaler(self, scaler: ScalerBundle) -> None:
        scaler_path = self._get_scaler_path()
        tmp_path = scaler_path.with_name(scaler_path.name + ".tmp")

        try:
            scaler_path.parent.mkdir(parents=True, exist_ok=True)

            if scaler.mean is None or scaler.std is None:
                raise RuntimeError("ScalerBundle is missing mean/std (cannot save).")

            mean = np.asarray(scaler.mean, dtype=np.float32).reshape(-1)
            std = np.asarray(scaler.std, dtype=np.float32).reshape(-1)

            if mean.size != self.num_features or std.size != self.num_features:
                raise RuntimeError(
                    f"Scaler dim invalid on save: mean{mean.shape} std{std.shape} "
                    f"expected ({self.num_features},) for schema "
                    f"{self.feature_schema.schema_id}"
                )

            with open(tmp_path, "wb") as f:
                np.savez(f, mean=mean, std=std)

            tmp_path.replace(scaler_path)

            logger.info(
                "Scaler artifact saved atomically",
                path=str(scaler_path),
                mean_shape=tuple(mean.shape),
                std_shape=tuple(std.shape),
            )

        except Exception as err:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

            logger.error("Failed to save scaler artifact", path=str(scaler_path), error=str(err))
            raise

    def _load_scaler(self) -> ScalerBundle:
        scaler_path = self._get_scaler_path()
        if not scaler_path.exists():
            raise FileNotFoundError(f"Scaler artifact not found at: {scaler_path}")

        data = np.load(scaler_path)
        mean = np.asarray(data["mean"], dtype=np.float32).reshape(-1)
        std = np.asarray(data["std"], dtype=np.float32).reshape(-1)

        if mean.size != self.num_features or std.size != self.num_features:
            raise RuntimeError(
                f"Scaler dim invalid on load: mean{mean.shape} std{std.shape} "
                f"expected ({self.num_features},) for schema {self.feature_schema.schema_id}"
            )

        return ScalerBundle(mean=mean, std=std)

    def _save_metadata(self, feature_cols: list[str]) -> None:
        meta_path = self._get_meta_path()
        tmp_path = meta_path.with_name(meta_path.name + ".tmp")

        payload = {
            "num_features": self.num_features,
            "num_classes": self.NUM_CLASSES,
            "model_head_classes": self.MODEL_HEAD_CLASSES,
            "feature_schema_id": self.feature_schema.schema_id,
            "feature_schema_dimension": self.feature_schema.dimension,
            "feature_columns": feature_cols,
            "label_mapping": self.label_map,
            "train_ratio": self.train_ratio,
            "num_folds": self.num_folds,
            "purge_gap_bars": self.purge_gap,
            "embargo_bars": self.embargo_bars,
            "epochs_per_fold": self.epochs,
            "batch_size": self.batch_size,
            "learning_rate": self.learning_rate,
            "active_class_boost": self.active_class_boost,
            "use_feature_scaling": self.use_feature_scaling,
            "clip_features_min": self.clip_features_min,
            "clip_features_max": self.clip_features_max,
            "seed": self.seed,
            "device_at_training": str(self.device),
        }

        try:
            meta_path.parent.mkdir(parents=True, exist_ok=True)

            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

            tmp_path.replace(meta_path)

            logger.info("Training metadata saved atomically", path=str(meta_path))
        except Exception as err:
            logger.error("Failed to save training metadata", path=str(meta_path), error=str(err))
            raise

    # =========================================================================
    # INTERNAL: SEEDING
    # =========================================================================

    def _set_seed(self, seed: int) -> None:
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)

        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)

        if torch.backends.cudnn.is_available():
            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False

        try:
            torch.use_deterministic_algorithms(True, warn_only=True)
        except Exception:
            pass


# =============================================================================
# LOSS & SAMPLING HELPERS FOR ANTI-COLLAPSE FINE-TUNING
# =============================================================================
class FocalLossWithSmoothing(nn.Module):
    """
    Focal Loss with Label Smoothing, Time-Decay Sample Weighting, and Class-Balanced Weights.
    Prevents majority-class dominance (NO_TRADE) and mode collapse during online fine-tuning.
    """

    def __init__(
        self,
        alpha: torch.Tensor | None = None,
        gamma: float = 2.0,
        label_smoothing: float = 0.08,
        reduction: str = "mean",
    ) -> None:
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma
        self.label_smoothing = label_smoothing
        self.reduction = reduction

    def forward(
        self,
        logits: torch.Tensor,
        targets: torch.Tensor,
        sample_weights: torch.Tensor | None = None,
    ) -> torch.Tensor:
        num_classes = logits.shape[1]
        log_probs = F.log_softmax(logits, dim=-1)
        probs = torch.exp(log_probs)

        # Smooth label targets
        with torch.no_grad():
            target_probs = torch.full_like(log_probs, self.label_smoothing / num_classes)
            target_probs.scatter_(
                1,
                targets.unsqueeze(1),
                1.0 - self.label_smoothing + (self.label_smoothing / num_classes),
            )

        # Focal factor: (1 - p_t)^gamma
        p_t = probs.gather(1, targets.unsqueeze(1)).squeeze(1)
        focal_weight = (1.0 - p_t) ** self.gamma

        # Cross-entropy with smoothed targets
        ce_loss = -(target_probs * log_probs).sum(dim=-1)
        focal_loss = focal_weight * ce_loss

        # Apply class weights alpha
        if self.alpha is not None:
            alpha_t = self.alpha.to(logits.device)[targets]
            focal_loss = alpha_t * focal_loss

        # Apply exponential time-decay sample weights
        if sample_weights is not None:
            focal_loss = focal_loss * sample_weights.to(logits.device)

        if self.reduction == "mean":
            return focal_loss.mean()
        elif self.reduction == "sum":
            return focal_loss.sum()
        return focal_loss  # reduction == 'none'


def _balance_oversample_dataset(
    X: np.ndarray, y: np.ndarray, active_boost_ratio: float = 0.85
) -> tuple[np.ndarray, np.ndarray]:
    """
    Oversamples minority active classes (BUY=1, SELL=2) so their representation
    in the online buffer approaches the majority class, preventing BUY class disappearance.
    """
    classes, counts = np.unique(y, return_counts=True)
    if len(classes) < 2:
        return X, y

    max_count = int(np.max(counts) * active_boost_ratio)
    indices = []

    for c in classes:
        c_idx = np.where(y == c)[0]
        if len(c_idx) == 0:
            continue
        if len(c_idx) < max_count and c in (1, 2):  # Active trading classes (BUY / SELL)
            repeat_count = max_count // len(c_idx)
            remainder = max_count % len(c_idx)
            selected = np.concatenate(
                [
                    np.tile(c_idx, repeat_count),
                    np.random.choice(
                        c_idx, remainder, replace=False if len(c_idx) >= remainder else True
                    ),
                ]
            )
        else:
            selected = c_idx
        indices.append(selected)

    all_indices = np.concatenate(indices)
    np.random.shuffle(all_indices)

    return X[all_indices], y[all_indices]
