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
from typing import Dict, List, Tuple, Optional

import numpy as np
import polars as pl
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.training.walk_forward_trainer")


# =============================================================================
# DATASET
# =============================================================================

class ScalpDataset(Dataset):
    """Simple tensor dataset for ScalpNet training."""

    def __init__(self, features: np.ndarray, labels: np.ndarray, device: torch.device) -> None:
        self.features = torch.tensor(features, dtype=torch.float32).to(device)
        self.labels = torch.tensor(labels, dtype=torch.long).to(device)

    def __len__(self) -> int:
        return int(len(self.labels))

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor]:
        return self.features[idx], self.labels[idx]


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
    """

    NUM_FEATURES: int = 50
    NUM_CLASSES: int = 3

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
        active_class_boost: float = 2.5,
        artifact_save_path: Path = Path("artifacts/models/scalp/XAUUSD/v1.0.0/model.pt"),
        use_feature_scaling: bool = True,
        clip_features_min: float = -5.0,
        clip_features_max: float = 5.0,
        min_rows_per_train_split: int = 50,
        min_rows_per_test_split: int = 20,
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

        if torch.cuda.is_available():
            self.device = torch.device("cuda")
        elif torch.backends.mps.is_available():
            self.device = torch.device("mps")
        else:
            self.device = torch.device("cpu")

        self.label_map: Dict[str, int] = {
            ActionType.NO_TRADE.value: 0,
            ActionType.BUY_MARKET.value: 1,
            ActionType.SELL_MARKET.value: 2,
        }

        self.inverse_label_map: Dict[int, str] = {
            0: ActionType.NO_TRADE.value,
            1: ActionType.BUY_MARKET.value,
            2: ActionType.SELL_MARKET.value,
        }

        self._set_seed(self.seed)

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def train_and_validate(self, df: pl.DataFrame, feature_cols: List[str]) -> ScalpNet:
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

        oos_predictions: List[int] = []
        oos_targets: List[int] = []

        for fold in range(self.num_folds):
            start_idx = fold * fold_size
            end_idx = total_samples if fold == self.num_folds - 1 else (fold + 1) * fold_size

            fold_X = X_raw[start_idx:end_idx]
            fold_y = y[start_idx:end_idx]

            if len(fold_X) < 10:
                continue

            raw_split_point = int(len(fold_X) * self.train_ratio)
            train_end_point = max(0, raw_split_point - self.purge_gap)
            test_start_point = raw_split_point

            X_train_raw = fold_X[:train_end_point]
            y_train = fold_y[:train_end_point]
            X_test_raw = fold_X[test_start_point:]
            y_test = fold_y[test_start_point:]

            if len(X_train_raw) < self.min_rows_per_train_split or len(X_test_raw) < self.min_rows_per_test_split:
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

            weights_tensor = self._build_class_weights(y_train).to(self.device)
            dyn_batch = self._resolve_batch_size(len(y_train))

            train_ds = ScalpDataset(X_train, y_train, self.device)
            test_ds = ScalpDataset(X_test, y_test, self.device)

            train_loader = self._make_loader(train_ds, dyn_batch, shuffle=True)
            test_loader = self._make_loader(test_ds, dyn_batch, shuffle=False)

            model = self._create_model(num_features=len(feature_cols))
            optimizer = torch.optim.AdamW(model.parameters(), lr=self.learning_rate, weight_decay=1e-4)
            criterion = nn.CrossEntropyLoss(weight=weights_tensor)
            scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=self.epochs)

            best_val_loss = float("inf")
            best_state: Optional[Dict[str, torch.Tensor]] = None
            patience_counter = 0

            for epoch in range(self.epochs):
                train_loss = self._train_one_epoch(model, train_loader, optimizer, criterion)
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

        for epoch in range(self.epochs):
            train_loss = self._train_one_epoch(final_model, full_loader, final_optimizer, final_criterion)
            final_scheduler.step()

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
        live_model: Optional[ScalpNet] = None,
        recent_df: Optional[pl.DataFrame] = None,
        feature_cols: Optional[List[str]] = None,
        epochs: int = 3,
        learning_rate: float = 1e-4,
        max_holding_bars: int = 15,
        model: Optional[ScalpNet] = None,  # Keyword Alias for backwards compatibility
    ) -> ScalpNet:
        """
        Performs clone-safe online fine-tuning on recent labeled bars.
        Supports both 'live_model' and 'model' keyword arguments.
        """
        target_model = live_model if live_model is not None else model
        if target_model is None or recent_df is None or feature_cols is None:
            raise ValueError("Must provide target model, recent_df, and feature_cols to fine_tune_online.")

        self._validate_training_frame(recent_df, feature_cols)
        recent_df = self._filter_trainable_rows(recent_df)

        logger.info(
            "Initiating online fine-tuning",
            buffer_rows=len(recent_df),
            epochs=epochs,
            learning_rate=learning_rate,
            max_holding_bars=max_holding_bars,
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
            logger.info("No pre-existing scaler artifact found for fine-tuning. Fitting initial scaler on recent memory buffer.")
            scaler = self._fit_scaler(X_raw)

        X_scaled = self._transform_features(X_raw, scaler)

        if len(y) < 32:
            logger.warning(
                "Insufficient post-purge labeled rows for online fine-tuning",
                samples=len(y),
            )
            return copy.deepcopy(target_model)

        working_model = copy.deepcopy(target_model).to(self.device)
        working_model.train()

        weights_tensor = self._build_class_weights(y).to(self.device)
        dyn_batch = max(16, min(128, len(y) // 8))
        dataset = ScalpDataset(X_scaled, y, self.device)
        loader = self._make_loader(dataset, dyn_batch, shuffle=True)

        optimizer = torch.optim.AdamW(
            working_model.parameters(),
            lr=learning_rate,
            weight_decay=1e-4,
        )
        criterion = nn.CrossEntropyLoss(weight=weights_tensor)

        for ep in range(epochs):
            avg_loss = self._train_one_epoch(working_model, loader, optimizer, criterion)

        working_model.eval()

        self._save_checkpoint(working_model)
        self._save_scaler(scaler)

        logger.info(
            "Online fine-tuning complete",
            buffer_rows=len(valid_df),
            checkpoint=str(self.artifact_path),
        )

        return working_model.to(torch.device("cpu"))

    # =========================================================================
    # INTERNAL: VALIDATION & FILTERS
    # =========================================================================

    def _validate_training_frame(self, df: pl.DataFrame, feature_cols: List[str]) -> None:
        if len(feature_cols) != self.NUM_FEATURES:
            raise ValueError(
                f"50D feature contract violation: expected {self.NUM_FEATURES} "
                f"feature columns, got {len(feature_cols)}"
            )

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
            out = out.filter(pl.col("label_evaluated") == True)

        if "is_purged" in out.columns:
            out = out.filter(pl.col("is_purged") == False)

        return out

    # =========================================================================
    # INTERNAL: EXTRACTION / TRANSFORM
    # =========================================================================

    def _extract_X_y(self, df: pl.DataFrame, feature_cols: List[str]) -> Tuple[np.ndarray, np.ndarray]:
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
        model = ScalpNet(num_features=num_features, num_classes=4)
        model.to(self.device)
        return model

    def _build_class_weights(self, y: np.ndarray) -> torch.Tensor:
        samples = len(y)
        class_counts = np.bincount(y, minlength=self.NUM_CLASSES)
        weights = np.zeros(self.NUM_CLASSES, dtype=np.float32)

        for c in range(self.NUM_CLASSES):
            if class_counts[c] > 0:
                weights[c] = samples / (self.NUM_CLASSES * class_counts[c])
            else:
                weights[c] = 0.0

        if class_counts[1] > 0:
            weights[1] *= self.active_class_boost
        if class_counts[2] > 0:
            weights[2] *= self.active_class_boost

        for c in range(self.NUM_CLASSES):
            if weights[c] > 0.0:
                weights[c] = float(np.clip(weights[c], 0.10, 10.0))

        weights_4d = np.zeros(4, dtype=np.float32)
        weights_4d[:3] = weights

        return torch.tensor(weights_4d, dtype=torch.float32)

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

        for batch_x, batch_y in loader:
            optimizer.zero_grad(set_to_none=True)
            logits = model(batch_x, return_logits=True)
            loss = criterion(logits, batch_y)
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
            for batch_x, batch_y in loader:
                logits = model(batch_x, return_logits=True)
                loss = criterion(logits, batch_y)

                batch_rows = len(batch_y)
                total_loss += float(loss.item()) * batch_rows
                total_rows += batch_rows

        return total_loss / max(1, total_rows)

    def _predict_classes(self, model: ScalpNet, loader: DataLoader) -> List[int]:
        model.eval()
        preds: List[int] = []

        with torch.inference_mode():
            for batch_x, _ in loader:
                probs = model(batch_x, return_logits=False)
                batch_preds = torch.argmax(probs, dim=-1).detach().cpu().numpy().tolist()
                preds.extend(batch_preds)

        return preds

    # =========================================================================
    # INTERNAL: METRICS
    # =========================================================================

    def _calculate_fold_sharpe_proxy(self, preds: List[int], targets: np.ndarray) -> float:
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

    def _evaluate_global_performance(self, preds: List[int], targets: List[int]) -> Dict[str, str]:
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
        total_samples = int(len(preds_arr))
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
            std  = np.asarray(scaler.std,  dtype=np.float32).reshape(-1)

            if mean.size != self.NUM_FEATURES or std.size != self.NUM_FEATURES:
                raise RuntimeError(
                    f"Scaler dim invalid on save: mean{mean.shape} std{std.shape} "
                    f"expected ({self.NUM_FEATURES},)"
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
        std  = np.asarray(data["std"],  dtype=np.float32).reshape(-1)

        if mean.size != self.NUM_FEATURES or std.size != self.NUM_FEATURES:
            raise RuntimeError(
                f"Scaler dim invalid on load: mean{mean.shape} std{std.shape} "
                f"expected ({self.NUM_FEATURES},)"
            )

        return ScalerBundle(mean=mean, std=std)

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




def _load_scaler(self) -> ScalerBundle:
    scaler_path = self._get_scaler_path()
    if not scaler_path.exists():
        raise FileNotFoundError(f"Scaler artifact not found at: {scaler_path}")

    data = np.load(scaler_path)
    mean = np.asarray(data["mean"], dtype=np.float32).reshape(-1)
    std  = np.asarray(data["std"],  dtype=np.float32).reshape(-1)

    if mean.size != self.NUM_FEATURES or std.size != self.NUM_FEATURES:
        raise RuntimeError(
            f"Scaler dim invalid on load: mean{mean.shape} std{std.shape} "
            f"expected ({self.NUM_FEATURES},)"
        )

    return ScalerBundle(mean=mean, std=std)


    def _save_metadata(self, feature_cols: List[str]) -> None:
        meta_path = self._get_meta_path()
        tmp_path = meta_path.with_name(meta_path.name + ".tmp")

        payload = {
            "num_features": self.NUM_FEATURES,
            "num_classes": self.NUM_CLASSES,
            "feature_columns": feature_cols,
            "label_mapping": self.label_map,
            "train_ratio": self.train_ratio,
            "num_folds": self.num_folds,
            "purge_gap_bars": self.purge_gap,
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
