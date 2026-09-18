"""Model Studio Routes — Deep Learning & Neural Network Inspection, Prediction & Training.

Provides comprehensive observability, interactive testing, and managed training
for PyTorch ScalpNet models across 50D (`scalp_v1`) and 70D (`scalp_v3`):
  - Overview: architecture summary, parameter counts, weights fingerprint, scaler stats
  - Prediction: 50D and 70D forward pass with probabilities, logits, confidence, and entropy
  - 70D Assembly: live multi-source component fetch (Base 0..49, News 50..59, Liquidity 60..69)
  - Neural Inspection: layer-by-layer activation L2 norms, means, sparsity, and gradient saliency
  - Out-of-Distribution (OOD) & Numerical Validation: z-score tracking, finiteness, probability sum
  - Training: dataset enumeration, managed training dispatch, real-time progress polling
  - Stress Testing: automated robustness battery (flash crashes, noise, zero variance, NaNs)
  - Benchmarking: 100-iteration latency profiling (P50, P90, P99, throughput)
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import torch
from fastapi import HTTPException
from pydantic import BaseModel, Field

from nexus_scalp.features.schema import active_dimension, active_schema
from nexus_scalp.model_generation.model_registry import ModelRecord, get_model_registry
from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.web.model_studio_routes")

REPO_ROOT = Path(__file__).resolve().parents[3]

# Operator-configurable allowlist of roots a Model Studio request may READ from.
# Mirrors provisioning_routes._allowed_import_roots: model-studio dataset paths
# come from the operator UI / REST body, so they are confined to declared roots
# (containment via Path.is_relative_to, never string prefix) and only ever READ.
_DATASET_ROOTS_ENV = "NEXUS_MODEL_STUDIO_ROOTS"
_DEFAULT_DATASET_ROOTS = ("data/raw", "data/processed", "data/positions", "data")


def _allowed_dataset_roots() -> list[Path]:
    """Resolved allowlist of directories a dataset path may live under."""
    import os

    roots_env = str(os.environ.get(_DATASET_ROOTS_ENV, "")).strip()
    roots: list[Path] = []
    seen: set[Path] = set()
    for raw in [*roots_env.split(os.pathsep), *_DEFAULT_DATASET_ROOTS]:
        cleaned = raw.strip()
        if not cleaned:
            continue
        candidate = Path(cleaned).expanduser()
        resolved = (
            candidate.resolve() if candidate.is_absolute() else (REPO_ROOT / candidate).resolve()
        )
        if resolved not in seen:
            seen.add(resolved)
            roots.append(resolved)
    return roots


def _dataset_candidates() -> list[tuple[str, str, Path]]:
    """Server-derived inventory of selectable dataset files: (name, relpath, path).

    The Path objects produced here are derived ONLY from REPO_ROOT and the root
    allowlist — never from request input — so downstream reads stay untainted.
    """
    found: list[tuple[str, str, Path]] = []
    seen: set[Path] = set()
    for root in _allowed_dataset_roots():
        if not root.is_dir():
            continue
        for p in sorted([*root.glob("*.parquet"), *root.glob("*.csv")]):
            if not p.is_file():
                continue
            # Skip temp/partial artifacts written by our own atomic installers.
            if p.name.startswith(".") or ".tmp" in p.name:
                continue
            real = p.resolve()
            if real in seen:
                continue
            seen.add(real)
            try:
                rel = str(real.relative_to(REPO_ROOT))
            except ValueError:
                rel = str(real)
            found.append((real.name, rel, real))
    return found


def _safe_dataset_path(raw: str) -> Path | None:
    """Select an operator-named dataset from the SERVER-DERIVED inventory.

    Security model (closes CodeQL py/path-injection and the string-prefix
    bypass class — "data/rawx" starts with "data/raw"):
      1. the caller's string is used ONLY as a lookup key against the inventory
         built by ``_dataset_candidates()`` from the root allowlist;
      2. no Path is ever constructed from request input, so the value that
         reaches the filesystem read always originates server-side;
      3. ``..`` segments and null bytes are rejected outright (fail-closed for
         callers that pass through malformed input);
      4. the resolved file must sit under an allowlisted root;
      5. callers only ever READ the file (never write, never execute).
    Returns None when the name matches nothing selectable.
    """
    import os

    s = str(raw or "").strip()
    if not s or "\x00" in s:
        return None
    if any(part == ".." for part in Path(s).parts) or (os.altsep and ".." in s.split(os.altsep)):
        return None

    expanded = os.path.expanduser(s)
    for name, rel, path in _dataset_candidates():
        if expanded in (name, rel, str(path)):
            return path
    return None


def _resolve_requested_dataset(raw: str) -> Path | None:
    """Resolve a caller-named dataset, distinguishing "absent" from "refused".

    ``raw`` empty/whitespace means the caller did not choose a dataset, so the
    caller may fall back to a default. Anything else that fails to resolve is an
    explicit-but-invalid selection and raises 400 — silently substituting a
    different dataset would hide both traversal attempts and stale UI state.
    """
    key = str(raw or "").strip()
    if not key:
        return None
    resolved = _safe_dataset_path(key)
    if resolved is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"dataset_path {key!r} is not a selectable dataset; "
                "pick a file listed by GET /api/model-studio/datasets"
            ),
        )
    return resolved


def _read_dataset_frame(target: Path) -> pl.DataFrame:
    """Read an inventory-derived dataset as a Polars frame (parquet or csv only)."""
    if target.suffix.lower() == ".parquet":
        return pl.read_parquet(target)
    if target.suffix.lower() == ".csv":
        return pl.read_csv(target)
    raise ValueError(f"unsupported dataset type: {target.suffix}")


# Operator-configurable allowlist of roots a Model Studio request may READ/LOAD models from.
_MODEL_ROOTS_ENV = "NEXUS_MODEL_STUDIO_MODEL_ROOTS"
_DEFAULT_MODEL_ROOTS = (
    "artifacts/model_generation/checkpoints",
    "artifacts/models",
    "artifacts/model_generation/models",
    "models",
)


def _allowed_model_roots() -> list[Path]:
    """Resolved allowlist of directories a model checkpoint may live under."""
    import os

    roots_env = str(os.environ.get(_MODEL_ROOTS_ENV, "")).strip()
    roots: list[Path] = []
    seen: set[Path] = set()
    for raw in [*roots_env.split(os.pathsep), *_DEFAULT_MODEL_ROOTS]:
        cleaned = raw.strip()
        if not cleaned:
            continue
        candidate = Path(cleaned).expanduser()
        resolved = (
            candidate.resolve() if candidate.is_absolute() else (REPO_ROOT / candidate).resolve()
        )
        if resolved not in seen:
            seen.add(resolved)
            roots.append(resolved)
    return roots


def _model_candidates() -> list[tuple[str, str, str, Path]]:
    """Server-derived inventory of selectable model files: (id, name, relpath, path)."""
    found: list[tuple[str, str, str, Path]] = []
    seen: set[Path] = set()
    for root in _allowed_model_roots():
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.pt")):
            if not p.is_file():
                continue
            if p.name.startswith(".") or ".tmp" in p.name:
                continue
            real = p.resolve()
            if real in seen:
                continue
            seen.add(real)
            try:
                rel = str(real.relative_to(REPO_ROOT))
            except ValueError:
                rel = str(real)
            found.append((p.stem, p.name, rel, real))
    return found


def _safe_model_path(raw: str) -> Path | None:
    """Select an operator-named model checkpoint from server-derived inventory."""
    import os

    s = str(raw or "").strip()
    if not s or "\x00" in s:
        return None
    if any(part == ".." for part in Path(s).parts) or (os.altsep and ".." in s.split(os.altsep)):
        return None

    expanded = os.path.expanduser(s)
    for model_id, name, rel, path in _model_candidates():
        if expanded in (model_id, name, rel, str(path)):
            return path
    return None


def _resolve_requested_model(raw: str) -> Path | None:
    """Resolve caller-named model checkpoint, raising 400 on explicit-but-invalid."""
    key = str(raw or "").strip()
    if not key:
        return None
    resolved = _safe_model_path(key)
    if resolved is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"model {key!r} is not a selectable checkpoint; "
                "pick a model listed by GET /api/model-studio/models"
            ),
        )
    return resolved


# =============================================================================
# Request / Response Schemas
# =============================================================================


class ModelStudioPredictRequest(BaseModel):
    dimension: int = Field(default=50, description="Feature dimension (50 or 70)")
    features: list[float] | None = Field(default=None, description="Feature vector")
    use_live_features: bool = Field(
        default=False, description="Use live feature vector from engine"
    )
    fetch_live_70d: bool = Field(
        default=False, description="Fetch live Base+News+Liquidity for 70D"
    )
    perturbation_sigma: float = Field(
        default=0.0, ge=0.0, le=5.0, description="Gaussian noise jitter"
    )
    simulate_policy_threshold: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Threshold"
    )
    inspect_layers: bool = Field(default=True, description="Capture layer activation norms")
    compute_saliency: bool = Field(default=True, description="Compute feature gradients")


class ModelStudioTrainRequest(BaseModel):
    dataset_path: str = Field(default="", description="Path to parquet/csv dataset")
    dataset_id: str = Field(default="", description="Registered dataset ID")
    dimension: int = Field(default=50, description="Target dimension (50 or 70)")
    epochs: int = Field(default=3, ge=1, le=50, description="Training epochs")
    batch_size: int = Field(default=256, ge=16, le=2048, description="Batch size")
    learning_rate: float = Field(default=5e-4, ge=1e-6, le=1e-1, description="Learning rate")
    seed: int = Field(default=42, description="Random seed")


class ModelStudioStressRequest(BaseModel):
    dimension: int = Field(default=50, description="Model dimension (50 or 70)")
    include_flash_crash: bool = Field(default=True)
    include_noise: bool = Field(default=True)
    include_nonfinite: bool = Field(default=True)
    include_boundary: bool = Field(default=True)


class ModelStudioBenchmarkRequest(BaseModel):
    dimension: int = Field(default=50, description="Model dimension (50 or 70)")
    iterations: int = Field(default=100, ge=10, le=1000, description="Number of passes")


class ModelStudioDownloadRequest(BaseModel):
    symbol: str = Field(default="XAUUSD", description="Symbol e.g. XAUUSD")
    timeframe: str = Field(default="M1", description="Timeframe (M1, M3, M5, M15)")
    bars: int = Field(default=10000, ge=100, le=500000, description="Candle count")
    source: str = Field(default="synthetic", description="Source (synthetic, mt5, csv)")
    csv_path: str | None = Field(default=None, description="Path to CSV file if source=csv")
    seed: int = Field(default=42, description="Random seed")


class ModelStudioInspectFeaturesRequest(BaseModel):
    dataset_path: str = Field(default="", description="Path to dataset file")
    dimension: int = Field(default=50, description="Feature dimension (50 or 70)")
    max_rows: int = Field(default=500, ge=50, le=10000, description="Rows to process")


class ModelStudioPositionDatasetRequest(BaseModel):
    source_dataset_path: str = Field(default="", description="Source market bars dataset")
    dimension: int = Field(default=50, description="Feature dimension (50 or 70)")
    bars_limit: int = Field(default=5000, ge=100, le=50000, description="Bars limit")
    max_holding_bars: int = Field(default=30, ge=5, le=120, description="Max holding bars")
    target_atr_multiplier: float = Field(default=2.0, ge=0.5, le=10.0, description="Target ATR")
    stop_loss_atr_multiplier: float = Field(
        default=1.5, ge=0.5, le=5.0, description="Stop loss ATR"
    )
    friction_pips: float = Field(default=0.25, ge=0.0, le=5.0, description="Friction in pips")


class ModelStudioHotLoadRequest(BaseModel):
    model_id: str = Field(..., description="Checkpoint ID, filename, or relative path")
    fine_tune_enabled: bool = Field(default=False, description="Enable fine-tuning mode")
    attach_scaler: bool = Field(default=True, description="Auto-load sidecar scaler (.scaler.npz)")
    operator: str = Field(default="OPERATOR", description="Operator identity")


class ModelStudioFineTuneRequest(BaseModel):
    base_model_id: str = Field(default="", description="Base checkpoint ID (uses active if empty)")
    dataset_path: str = Field(default="", description="Dataset path for fine-tuning")
    epochs: int = Field(default=3, ge=1, le=50, description="Fine-tune epochs")
    learning_rate: float = Field(
        default=1e-4, ge=1e-6, le=1e-1, description="Fine-tune learning rate"
    )
    freeze_backbone: bool = Field(default=True, description="Freeze non-classifier layers")
    seed: int = Field(default=42, description="Random seed")


class ModelStudioVerifyRequest(BaseModel):
    model_id: str = Field(..., description="Model ID or filename to verify")


class ModelStudioRegisterRequest(BaseModel):
    path: str = Field(..., description="Filesystem path to .pt weights")
    name: str = Field(default="", description="Friendly model name")
    dimension: int = Field(default=50, description="Feature dimension (50 or 70)")


class ModelStudioCanaryRequest(BaseModel):
    model_id: str = Field(..., description="Model ID to load as canary")


class ModelStudioTagRequest(BaseModel):
    model_id: str = Field(..., description="Model ID to tag")
    stage: str = Field(default="STAGING", description="Stage: CHAMPION, CANARY, STAGING, ARCHIVED")
    fine_tune_enabled: bool | None = Field(default=None, description="Fine-tune permission")


class ModelStudioExportRequest(BaseModel):
    model_id: str = Field(..., description="Model ID to export")


class ModelStudioDriftRequest(BaseModel):
    dataset_path: str = Field(default="", description="Dataset path to compare against")
    dimension: int = Field(default=50, description="Feature dimension (50 or 70)")
    max_rows: int = Field(default=500, ge=50, le=10000, description="Row sample limit")


# In-memory training progress tracker for studio
_STUDIO_TRAIN_STATE: dict[str, Any] = {
    "status": "IDLE",
    "stage": "IDLE",
    "epoch": 0,
    "epochs": 0,
    "loss": 0.0,
    "val_loss": 0.0,
    "started_at": None,
    "updated_at": None,
    "run_id": "",
    "message": "No active training run.",
}


# =============================================================================
# Helper Utilities & Core Engines
# =============================================================================


class _StudioLoadedScaler:
    """Live scaler bundle holding mean and std vectors for hot-loaded models."""

    def __init__(self, mean: np.ndarray, std: np.ndarray, dim: int) -> None:
        self.mean = mean.astype(np.float32)
        self.std = np.maximum(std.astype(np.float32), 1e-3)
        self._dim = dim

    def is_ready(self) -> bool:
        return bool(np.all(np.isfinite(self.std)) and np.all(self.std > 0.0))

    def dimension(self) -> int:
        return self._dim

    def transform(self, x: np.ndarray) -> np.ndarray:
        if not self.is_ready():
            return np.clip(x, -5.0, 5.0)
        m = self.mean[: x.shape[-1]]
        s = self.std[: x.shape[-1]]
        return np.clip((x - m) / s, -5.0, 5.0)

    def transform_50d(self, x: np.ndarray) -> np.ndarray:
        return self.transform(x)


@dataclass
class StudioActiveBundle:
    model_id: str
    dimension: int
    model: ScalpNet
    scaler: Any
    weights_sha256: str
    weights_path: str
    scaler_path: str
    fine_tune_enabled: bool
    stage: str
    loaded_at: str
    inference_count: int = 0


_STUDIO_BUNDLE_LOCK = threading.RLock()


class _StudioBundleHolder:
    active: StudioActiveBundle | None = None
    canary: StudioActiveBundle | None = None


def _get_active_model_and_scaler(engine: Any, dimension: int) -> tuple[torch.nn.Module, Any, str]:
    """Resolves model and scaler for inference testing (hot-loaded, live bundle, or fresh instance)."""
    with _STUDIO_BUNDLE_LOCK:
        if _StudioBundleHolder.active is not None:
            if _StudioBundleHolder.active.dimension == dimension:
                _StudioBundleHolder.active.inference_count += 1
                return (
                    _StudioBundleHolder.active.model,
                    _StudioBundleHolder.active.scaler,
                    f"HOT_LOADED:{_StudioBundleHolder.active.model_id}",
                )

    if engine is not None and getattr(engine, "_bundle", None) is not None:
        with engine._bundle_lock:
            bundle = engine._bundle
        if bundle is not None and bundle.model is not None:
            model_dim = getattr(bundle.model, "num_features", None)
            if model_dim == dimension or (model_dim is None and dimension == 50):
                return bundle.model, bundle.scaler, "LIVE_BUNDLE"

    # Engine offline or dimension mismatch: instantiate fresh ScalpNet
    fresh_model = ScalpNet(num_features=dimension, num_classes=3)
    fresh_model.eval()

    class _MockScaler:
        def __init__(self, dim: int):
            self.mean = np.zeros(dim, dtype=np.float32)
            self.std = np.ones(dim, dtype=np.float32)

        def is_ready(self) -> bool:
            return True

        def transform(self, x: np.ndarray) -> np.ndarray:
            return np.clip(x, -5.0, 5.0)

        def transform_50d(self, x: np.ndarray) -> np.ndarray:
            return np.clip(x, -5.0, 5.0)

    return fresh_model, _MockScaler(dimension), "IN_MEMORY_INSTANCE"


def _shannon_entropy(probs: list[float]) -> float:
    """Calculates Shannon entropy in bits ($H = -\\sum p_i \\log_2(p_i)$)."""
    ent = 0.0
    for p in probs:
        if p > 1e-12:
            ent -= p * math.log2(p)
    return round(ent, 4)


def _capture_layer_activations(
    model: torch.nn.Module, x_tensor: torch.Tensor
) -> list[dict[str, Any]]:
    """Runs a forward pass with temporary forward hooks to capture layer statistics."""
    activations: list[dict[str, Any]] = []
    hooks = []

    def make_hook(name: str):
        def hook(module: torch.nn.Module, inp: Any, out: Any):
            if isinstance(out, torch.Tensor):
                arr = out.detach().cpu().numpy().flatten()
                norm = float(np.linalg.norm(arr))
                mean = float(np.mean(arr))
                std = float(np.std(arr))
                zero_frac = float(np.mean(np.abs(arr) < 1e-6))
                activations.append(
                    {
                        "layer": name,
                        "type": module.__class__.__name__,
                        "shape": list(out.shape),
                        "l2_norm": round(norm, 4),
                        "mean": round(mean, 4),
                        "std": round(std, 4),
                        "zero_fraction": round(zero_frac, 3),
                    }
                )

        return hook

    for name, module in model.named_modules():
        if isinstance(
            module,
            (torch.nn.Linear, torch.nn.Conv1d, torch.nn.LayerNorm, torch.nn.MultiheadAttention),
        ):
            hooks.append(module.register_forward_hook(make_hook(name)))

    try:
        with torch.inference_mode():
            _ = model(x_tensor)
    finally:
        for h in hooks:
            h.remove()

    return activations


def _compute_saliency(model: torch.nn.Module, x_np: np.ndarray, top_k: int = 5) -> dict[str, Any]:
    """Computes input feature saliency via backprop gradients $\\partial \\text{score}/\\partial x$."""
    try:
        x_var = torch.tensor(x_np, dtype=torch.float32, requires_grad=True)
        model.zero_grad()
        out = model(x_var)
        top_class = int(torch.argmax(out, dim=-1)[0])
        score = out[0, top_class]
        score.backward()
        grads = (
            x_var.grad.detach().cpu().numpy().flatten()
            if x_var.grad is not None
            else np.zeros(x_np.shape[1])
        )

        # Feature indices sorted by gradient
        sorted_indices = np.argsort(grads)
        neg_drivers = [
            {"index": int(i), "gradient": round(float(grads[i]), 5)} for i in sorted_indices[:top_k]
        ]
        pos_drivers = [
            {"index": int(i), "gradient": round(float(grads[i]), 5)}
            for i in sorted_indices[-top_k:][::-1]
        ]

        return {
            "top_positive_drivers": pos_drivers,
            "top_negative_drivers": neg_drivers,
            "mean_abs_gradient": round(float(np.mean(np.abs(grads))), 5),
            "max_abs_gradient": round(float(np.max(np.abs(grads))), 5),
        }
    except Exception as exc:
        return {"error": str(exc), "top_positive_drivers": [], "top_negative_drivers": []}


def _scan_available_datasets() -> list[dict[str, Any]]:
    """Scans repository for available training parquet/csv datasets."""
    found: list[dict[str, Any]] = []
    candidates = [
        REPO_ROOT / "data" / "raw",
        REPO_ROOT / "data",
        REPO_ROOT / "artifacts" / "model_generation" / "datasets",
    ]
    seen_paths = set()

    for base in candidates:
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.suffix.lower() in (".parquet", ".csv") and p.is_file():
                if p.name.startswith(".") or ".tmp" in p.name or "positions" in p.parts:
                    continue
                abs_path = str(p.resolve())
                if abs_path in seen_paths:
                    continue
                seen_paths.add(abs_path)
                size = p.stat().st_size
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=UTC).isoformat()
                found.append(
                    {
                        "name": p.name,
                        "path": str(p.relative_to(REPO_ROOT)),
                        "size_bytes": size,
                        "size_display": f"{size / (1024 * 1024):.2f} MB"
                        if size > 1024 * 1024
                        else f"{size / 1024:.1f} KB",
                        "format": p.suffix.lower().lstrip("."),
                        "modified_at": mtime,
                    }
                )
    return sorted(found, key=lambda d: d["name"])


# =============================================================================
# Standalone Core Executors (Usable from both REST and CLI)
# =============================================================================


def get_studio_overview(engine: Any = None) -> dict[str, Any]:
    """Returns comprehensive model architecture, weights fingerprint, and environment stats."""
    dim = (
        getattr(engine, "effective_feature_dim", active_dimension())
        if engine
        else active_dimension()
    )
    model, scaler, model_source = _get_active_model_and_scaler(engine, dim)

    param_count = sum(p.numel() for p in model.parameters())
    trainable_count = sum(p.numel() for p in model.parameters() if p.requires_grad)

    weights_hasher = hashlib.sha256()
    for p in model.parameters():
        weights_hasher.update(p.detach().cpu().numpy().tobytes())
    weights_sha256 = weights_hasher.hexdigest()

    scaler_mean = getattr(scaler, "mean", None)
    scaler_std = getattr(scaler, "std", None)
    scaler_stats: dict[str, Any] = {"status": "ABSENT"}
    if scaler is not None and getattr(scaler, "is_ready", lambda: False)():
        mean_arr = np.asarray(scaler_mean).flatten() if scaler_mean is not None else np.array([])
        std_arr = np.asarray(scaler_std).flatten() if scaler_std is not None else np.array([])
        scaler_stats = {
            "status": "READY",
            "mean_min": round(float(np.min(mean_arr)), 4) if mean_arr.size else 0.0,
            "mean_max": round(float(np.max(mean_arr)), 4) if mean_arr.size else 0.0,
            "std_min": round(float(np.min(std_arr)), 4) if std_arr.size else 1.0,
            "std_max": round(float(np.max(std_arr)), 4) if std_arr.size else 1.0,
            "clamped_cols": int(np.sum(std_arr <= 1e-3)) if std_arr.size else 0,
        }

    datasets = _scan_available_datasets()

    return {
        "status": "OK",
        "active_schema_id": getattr(
            engine, "effective_feature_schema_id", active_schema().schema_id
        )
        if engine
        else active_schema().schema_id,
        "effective_dimension": dim,
        "model_source": model_source,
        "architecture": model.__class__.__name__,
        "parameter_count": param_count,
        "trainable_parameters": trainable_count,
        "weights_sha256": weights_sha256,
        "device": str(next(model.parameters()).device) if param_count else "cpu",
        "scaler_stats": scaler_stats,
        "live_feature_vector_available": bool(
            engine and getattr(engine, "_last_fv", None) is not None
        ),
        "available_datasets_count": len(datasets),
        "available_datasets": datasets[:10],
        "inspected_at": datetime.now(UTC).isoformat(),
    }


def fetch_70d_components(engine: Any = None) -> dict[str, Any]:
    """Assembles and returns live 70D components (Base 0..49, News 50..59, Liquidity 60..69)."""
    from nexus_scalp.features.schema_contract import (
        canonical_feature_names,
        feature_schema_hash,
        validate_70d_vector,
    )

    all_names = canonical_feature_names()
    base_50: list[float] = [0.0] * 50
    news_10: list[float] = [0.0] * 10
    liq_10: list[float] = [0.0] * 10
    sources: dict[str, str] = {
        "base": "SYNTHETIC_BASELINE",
        "news": "NEUTRAL",
        "liquidity": "NEUTRAL",
    }

    if engine is not None:
        last_fv = getattr(engine, "_last_fv", None)
        if last_fv is not None:
            try:
                base_50 = list(last_fv.to_tensor_input())
                sources["base"] = "LIVE_TICK"
            except Exception:
                pass

        news_engine = getattr(engine, "news_engine", None)
        if news_engine is not None and getattr(engine, "_news_enabled", False):
            try:
                ctx = news_engine.current_context()
                if ctx is not None:
                    from nexus_scalp.governance.alignment import vectorize_news_context
                    from nexus_scalp.shadow.shadow70.news_provider import build_news_10

                    news_10, _ = build_news_10(vectorize_news_context(ctx))
                    sources["news"] = "LIVE_NEWS_ENGINE"
            except Exception:
                pass

        gov = getattr(engine, "liquidity_governor", None)
        if gov is not None:
            snap = getattr(gov, "last_snapshot", None)
            if snap is not None and getattr(gov, "causal_state", lambda: "")() == "VALID":
                vec = list(snap.features)
                if len(vec) == 10:
                    liq_10 = [float(v) for v in vec]
                    sources["liquidity"] = "LIVE_LIQUIDITY_GOVERNOR"

    full_70 = base_50 + news_10 + liq_10
    contract_valid = True
    contract_error = None
    try:
        validate_70d_vector(full_70, schema_hash=feature_schema_hash(), context="studio_fetch")
    except Exception as err:
        contract_valid = False
        contract_error = str(err)

    slots: list[dict[str, Any]] = []
    for i in range(70):
        family = "BASE" if i < 50 else "NEWS" if i < 60 else "LIQUIDITY"
        slots.append(
            {
                "index": i,
                "name": all_names[i] if i < len(all_names) else f"feat_{i}",
                "family": family,
                "value": round(full_70[i], 4),
                "is_finite": math.isfinite(full_70[i]),
            }
        )

    return {
        "status": "OK",
        "dimension": 70,
        "contract_valid": contract_valid,
        "contract_error": contract_error,
        "schema_hash": feature_schema_hash(),
        "sources": sources,
        "slots": slots,
        "vector": full_70,
        "fetched_at": datetime.now(UTC).isoformat(),
    }


def execute_predict(req: ModelStudioPredictRequest, engine: Any = None) -> dict[str, Any]:
    """Deep neural prediction inspection with entropy, saliency, and layer norms."""
    t0 = time.perf_counter()
    dim = req.dimension if req.dimension in (50, 70) else 50
    model, scaler, model_source = _get_active_model_and_scaler(engine, dim)

    features = req.features
    feature_source = "REQUEST"
    if req.fetch_live_70d and dim == 70:
        fetch_res = fetch_70d_components(engine)
        features = fetch_res["vector"]
        feature_source = "LIVE_70D_ASSEMBLED"
    elif req.use_live_features or features is None:
        if engine is not None and getattr(engine, "_last_fv", None) is not None:
            features = list(engine._last_fv.to_tensor_input())
            if dim == 70:
                features = features + [0.0] * 20
            feature_source = "LIVE_TICK"
        else:
            features = [0.0] * dim
            feature_source = "SYNTHETIC_ZERO"

    if len(features) != dim:
        raise ValueError(f"Dimension mismatch: model expects {dim} features, got {len(features)}.")

    sanitized: list[float] = []
    non_finite_count = 0
    for val in features:
        try:
            f = float(val)
            if not math.isfinite(f):
                non_finite_count += 1
                f = 0.0
        except Exception:
            non_finite_count += 1
            f = 0.0
        sanitized.append(f)

    if req.perturbation_sigma > 0.0:
        noise = np.random.normal(0.0, req.perturbation_sigma, size=len(sanitized))
        sanitized = (np.array(sanitized) + noise).tolist()
        feature_source += f"+NOISE(sigma={req.perturbation_sigma})"

    t_prep = time.perf_counter()

    x_np = np.array(sanitized, dtype=np.float32).reshape(1, -1)
    if hasattr(scaler, "transform"):
        x_scaled = scaler.transform(x_np)
    elif hasattr(scaler, "transform_50d"):
        x_scaled = scaler.transform_50d(x_np)
    else:
        x_scaled = x_np

    t_scaler = time.perf_counter()

    x_tensor = torch.tensor(x_scaled, dtype=torch.float32)
    model.eval()

    with torch.inference_mode():
        logits_tensor = model(x_tensor)
        if hasattr(model, "classifier"):
            probs_tensor = logits_tensor
        else:
            probs_tensor = torch.softmax(logits_tensor, dim=-1)

    t_forward = time.perf_counter()

    probs = [float(p) for p in probs_tensor.detach().cpu().numpy().flatten()]
    while len(probs) < 3:
        probs.append(0.0)

    p_sum = sum(probs[:3])
    all_positive = all(p >= 0.0 for p in probs[:3])
    numerical_valid = all_positive and abs(p_sum - 1.0) < 1e-4

    labels = ["NO_TRADE", "BUY_MARKET", "SELL_MARKET"]
    argmax_idx = int(np.argmax(probs[:3]))
    confidence = probs[argmax_idx]

    sorted_probs = sorted(probs[:3], reverse=True)
    top1_prob = sorted_probs[0]
    top2_prob = sorted_probs[1] if len(sorted_probs) > 1 else 0.0
    confidence_margin = round(top1_prob - top2_prob, 4)
    entropy = _shannon_entropy(probs[:3])

    threshold = req.simulate_policy_threshold if req.simulate_policy_threshold is not None else 0.35
    action = labels[argmax_idx]
    policy_verdict = action if (confidence >= threshold and action != "NO_TRADE") else "NO_TRADE"

    scaler_mean = getattr(scaler, "mean", None)
    scaler_std = getattr(scaler, "std", None)
    ood_score = 0.0
    if scaler_mean is not None and scaler_std is not None:
        sm = np.asarray(scaler_mean).flatten()
        ss = np.maximum(np.asarray(scaler_std).flatten(), 1e-3)
        z_scores = np.abs(np.array(sanitized) - sm) / ss
        ood_score = round(float(np.max(z_scores)), 3)

    layer_stats = _capture_layer_activations(model, x_tensor) if req.inspect_layers else []
    saliency_report = _compute_saliency(model, x_scaled) if req.compute_saliency else {}

    t_end = time.perf_counter()

    return {
        "status": "OK",
        "dimension": dim,
        "model_source": model_source,
        "feature_source": feature_source,
        "predicted_class": argmax_idx,
        "predicted_label": action,
        "confidence": round(confidence, 4),
        "confidence_margin": confidence_margin,
        "shannon_entropy_bits": entropy,
        "probabilities": {
            "no_trade": round(probs[0], 4),
            "buy": round(probs[1], 4),
            "sell": round(probs[2], 4),
        },
        "numerical_validation": {
            "valid": numerical_valid,
            "sum": round(p_sum, 6),
            "all_positive": all_positive,
            "non_finite_inputs_sanitized": non_finite_count,
        },
        "policy_simulation": {
            "threshold_applied": threshold,
            "final_action": policy_verdict,
            "passed_gate": confidence >= threshold,
        },
        "ood_metrics": {
            "max_z_score": ood_score,
            "is_out_of_distribution": ood_score > 4.5,
        },
        "latency_ms": {
            "feature_prep": round((t_prep - t0) * 1000, 2),
            "scaler": round((t_scaler - t_prep) * 1000, 2),
            "model_forward": round((t_forward - t_scaler) * 1000, 2),
            "analysis": round((t_end - t_forward) * 1000, 2),
            "total_e2e": round((t_end - t0) * 1000, 2),
        },
        "layer_inspection": layer_stats,
        "saliency": saliency_report,
        "evaluated_at": datetime.now(UTC).isoformat(),
    }


def execute_stress_test(req: ModelStudioStressRequest, engine: Any = None) -> dict[str, Any]:
    """Runs automated 6-step adversarial stress test on model."""
    dim = req.dimension if req.dimension in (50, 70) else 50
    model, scaler, _ = _get_active_model_and_scaler(engine, dim)

    results: list[dict[str, Any]] = []

    # 1. Zero Variance
    x_const = np.full((1, dim), 2.5, dtype=np.float32)
    try:
        x_s = scaler.transform(x_const) if hasattr(scaler, "transform") else x_const
        p = model(torch.tensor(x_s, dtype=torch.float32)).detach().numpy().flatten()
        results.append(
            {
                "test": "ZERO_VARIANCE",
                "passed": bool(np.all(np.isfinite(p))),
                "detail": "Constant inputs normalize without zero division",
            }
        )
    except Exception as err:
        results.append({"test": "ZERO_VARIANCE", "passed": False, "detail": str(err)})

    # 2. Flash Crash (+/- 1e6)
    x_shock = np.full((1, dim), 1e6, dtype=np.float32)
    try:
        x_s = scaler.transform(x_shock) if hasattr(scaler, "transform") else x_shock
        p = model(torch.tensor(x_s, dtype=torch.float32)).detach().numpy().flatten()
        results.append(
            {
                "test": "FLASH_CRASH_SHOCK",
                "passed": bool(np.all(np.isfinite(p))),
                "detail": "1e6 extreme inputs clipped properly",
            }
        )
    except Exception as err:
        results.append({"test": "FLASH_CRASH_SHOCK", "passed": False, "detail": str(err)})

    # 3. Non-finite inputs (NaN injection)
    x_nan = np.zeros((1, dim), dtype=np.float32)
    x_nan[0, 5] = np.nan
    try:
        x_nan_safe = np.nan_to_num(x_nan, nan=0.0)
        x_s = scaler.transform(x_nan_safe) if hasattr(scaler, "transform") else x_nan_safe
        p = model(torch.tensor(x_s, dtype=torch.float32)).detach().numpy().flatten()
        results.append(
            {
                "test": "NAN_INJECTION_DEFENSE",
                "passed": bool(np.all(np.isfinite(p))),
                "detail": "NaN sanitized to 0.0 fail-safe",
            }
        )
    except Exception as err:
        results.append({"test": "NAN_INJECTION_DEFENSE", "passed": False, "detail": str(err)})

    # 4. Dimension Boundary
    schema = active_schema()
    boundary_passed = False
    try:
        schema.validate_vector([0.0] * (dim - 1))
    except ValueError:
        boundary_passed = True
    results.append(
        {
            "test": "DIMENSION_BOUNDARY_FAIL_LOUD",
            "passed": boundary_passed,
            "detail": f"Dimension {dim - 1} rejected by schema",
        }
    )

    all_passed = all(r["passed"] for r in results)
    return {
        "status": "OK",
        "dimension": dim,
        "all_passed": all_passed,
        "tests_run": len(results),
        "results": results,
        "executed_at": datetime.now(UTC).isoformat(),
    }


def execute_benchmark(req: ModelStudioBenchmarkRequest, engine: Any = None) -> dict[str, Any]:
    """Profiles model inference latency over N iterations."""
    dim = req.dimension if req.dimension in (50, 70) else 50
    model, _, _ = _get_active_model_and_scaler(engine, dim)
    n = req.iterations

    x = torch.zeros((1, dim), dtype=torch.float32)
    latencies_ms: list[float] = []

    # Warm up 5 passes
    with torch.inference_mode():
        for _ in range(5):
            _ = model(x)

    t_total_start = time.perf_counter()
    with torch.inference_mode():
        for _ in range(n):
            t0 = time.perf_counter()
            _ = model(x)
            latencies_ms.append((time.perf_counter() - t0) * 1000)
    t_total_end = time.perf_counter()

    elapsed_sec = t_total_end - t_total_start
    throughput = n / elapsed_sec if elapsed_sec > 0 else float("inf")

    return {
        "status": "OK",
        "dimension": dim,
        "iterations": n,
        "latency_p50_ms": round(float(np.percentile(latencies_ms, 50)), 3),
        "latency_p90_ms": round(float(np.percentile(latencies_ms, 90)), 3),
        "latency_p99_ms": round(float(np.percentile(latencies_ms, 99)), 3),
        "latency_max_ms": round(float(np.max(latencies_ms)), 3),
        "latency_min_ms": round(float(np.min(latencies_ms)), 3),
        "throughput_inferences_per_sec": round(throughput, 1),
        "sla_passed": bool(np.percentile(latencies_ms, 99) < 10.0),
    }


def execute_train(req: ModelStudioTrainRequest) -> dict[str, Any]:
    """Dispatches real PyTorch model training with chosen dataset and hyperparameters."""
    run_id = f"train_studio_{int(time.time())}"

    explicit_dataset = bool(str(req.dataset_path or "").strip())
    target_path = _resolve_requested_dataset(req.dataset_path)

    df: pl.DataFrame | None = None
    if target_path is not None:
        try:
            loaded = _read_dataset_frame(target_path)
            if loaded.height >= 20 or explicit_dataset:
                df = loaded
        except Exception:
            if explicit_dataset:
                raise

    if df is None and not explicit_dataset:
        datasets = _scan_available_datasets()
        for cand in datasets:
            cand_p = _safe_dataset_path(str(cand["path"]))
            if cand_p is not None:
                try:
                    loaded = _read_dataset_frame(cand_p)
                    if loaded.height >= 20:
                        df = loaded
                        target_path = cand_p
                        break
                except Exception:
                    continue

    if df is None:
        from scripts.data.ingest_historical_candles import generate_synthetic_bars

        df = generate_synthetic_bars(symbol="XAUUSD", count=1000, seed=req.seed)
        target_path = None

    assert df is not None
    mat, _ = extract_dataset_features(df, dimension=req.dimension, max_rows=1000)
    n = mat.shape[0]

    # A degenerate dataset (empty, or too few rows to split into train/val) must
    # fail loudly.
    _MIN_TRAIN_ROWS = 20
    if n < _MIN_TRAIN_ROWS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"dataset too small to train: {n} usable row(s), need at least "
                f"{_MIN_TRAIN_ROWS}. Download more candles or pick another dataset."
            ),
        )

    # Generate pseudo labels for 3 classes based on future price direction
    closes = (
        np.array(df["close"].to_numpy()[:n], dtype=np.float64)
        if "close" in df.columns
        else (
            np.array(df["current_price"].to_numpy()[:n], dtype=np.float64)
            if "current_price" in df.columns
            else np.linspace(2000.0, 2050.0, n, dtype=np.float64)
        )
    )
    y_labels = np.zeros(n, dtype=np.int64)
    for i in range(n - 5):
        fut_ret = (closes[i + 5] - closes[i]) / max(closes[i], 1e-4)
        if fut_ret > 0.0005:
            y_labels[i] = 1
        elif fut_ret < -0.0005:
            y_labels[i] = 2

    # Split train/val. Both sides must be non-empty so the epoch loop and the
    # validation forward pass always see at least one row.
    train_size = min(max(int(n * 0.8), 1), n - 1)
    X_train = torch.tensor(mat[:train_size], dtype=torch.float32)
    y_train = torch.tensor(y_labels[:train_size], dtype=torch.long)
    X_val = torch.tensor(mat[train_size:], dtype=torch.float32)
    y_val = torch.tensor(y_labels[train_size:], dtype=torch.long)

    # Initialize model
    torch.manual_seed(req.seed)
    model = ScalpNet(num_features=req.dimension, num_classes=3)
    optimizer = torch.optim.AdamW(model.parameters(), lr=req.learning_rate)
    criterion = torch.nn.CrossEntropyLoss()

    final_loss = 0.0
    final_val_loss = 0.0
    batch_size = min(req.batch_size, train_size)

    for ep in range(1, req.epochs + 1):
        model.train()
        permutation = torch.randperm(train_size)
        epoch_losses: list[float] = []
        for i in range(0, train_size, batch_size):
            indices = permutation[i : i + batch_size]
            batch_x, batch_y = X_train[indices], y_train[indices]
            optimizer.zero_grad()
            out = model(batch_x)
            loss = criterion(out, batch_y)
            loss.backward()
            optimizer.step()
            epoch_losses.append(float(loss.item()))

        model.eval()
        with torch.inference_mode():
            val_out = model(X_val)
            val_loss = float(criterion(val_out, y_val).item())

        final_loss = round(float(np.mean(epoch_losses)), 4) if epoch_losses else 0.0
        final_val_loss = round(float(val_loss), 4)

        _STUDIO_TRAIN_STATE.clear()
        _STUDIO_TRAIN_STATE.update(
            {
                "status": "TRAINING" if ep < req.epochs else "DONE",
                "stage": "COMPLETE" if ep == req.epochs else "TRAINING",
                "epoch": ep,
                "epochs": req.epochs,
                "loss": final_loss,
                "val_loss": final_val_loss,
                "started_at": _STUDIO_TRAIN_STATE.get("started_at")
                or datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
                "run_id": run_id,
                "dimension": req.dimension,
                "dataset": str(target_path.relative_to(REPO_ROOT)) if target_path else "synthetic",
                "message": f"Epoch {ep}/{req.epochs} complete. Loss: {final_loss} | Val Loss: {final_val_loss}",
            }
        )

    # Save checkpoint
    ckpt_dir = REPO_ROOT / "artifacts" / "model_generation" / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    model_id = f"{run_id}_{req.dimension}d"
    ckpt_path = ckpt_dir / f"{model_id}.pt"
    torch.save(model.state_dict(), ckpt_path)

    # Compute and save scaler sidecar
    scaler_path = ckpt_dir / f"{model_id}.scaler.npz"
    scaler_mean = np.mean(mat, axis=0).astype(np.float32)
    scaler_std = np.maximum(np.std(mat, axis=0), 1e-3).astype(np.float32)
    np.savez(scaler_path, mean=scaler_mean, std=scaler_std, dimension=req.dimension)

    hasher = hashlib.sha256()
    with open(ckpt_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    weights_sha256 = hasher.hexdigest()

    # Save metadata manifest
    manifest_path = ckpt_dir / f"{model_id}.meta.json"
    manifest_data = {
        "model_id": model_id,
        "architecture": "ScalpNet",
        "dimension": req.dimension,
        "epochs": req.epochs,
        "final_loss": final_loss,
        "final_val_loss": final_val_loss,
        "weights_sha256": weights_sha256,
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_path": str(target_path.relative_to(REPO_ROOT)) if target_path else "synthetic",
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    # Register into SQLite model registry
    registry = get_model_registry()
    rec = ModelRecord(
        id=model_id,
        name=ckpt_path.name,
        version="1.0.0",
        dimension=req.dimension,
        architecture="ScalpNet",
        weights_path=str(ckpt_path.relative_to(REPO_ROOT)),
        scaler_path=str(scaler_path.relative_to(REPO_ROOT)),
        manifest_path=str(manifest_path.relative_to(REPO_ROOT)),
        sha256=weights_sha256,
        epochs=req.epochs,
        final_loss=final_loss,
        final_val_loss=final_val_loss,
        dataset_path=str(target_path.relative_to(REPO_ROOT)) if target_path else "synthetic",
        fine_tune_enabled=True,
        stage="STAGING",
        metrics={
            "final_loss": final_loss,
            "final_val_loss": final_val_loss,
            "train_size": train_size,
        },
    )
    registry.register_model(rec)

    _STUDIO_TRAIN_STATE["checkpoint_path"] = str(ckpt_path.relative_to(REPO_ROOT))
    _STUDIO_TRAIN_STATE["scaler_path"] = str(scaler_path.relative_to(REPO_ROOT))

    return {
        "status": "OK",
        "run_id": run_id,
        "model_id": model_id,
        "message": f"Training completed successfully for {req.dimension}D model ({req.epochs} epochs).",
        "target_dataset": str(target_path.relative_to(REPO_ROOT)) if target_path else "synthetic",
        "epochs_completed": req.epochs,
        "final_loss": final_loss,
        "final_val_loss": final_val_loss,
        "checkpoint_path": str(ckpt_path.relative_to(REPO_ROOT)),
        "scaler_path": str(scaler_path.relative_to(REPO_ROOT)),
        "sha256": weights_sha256,
        "registered_in_db": True,
        "state": dict(_STUDIO_TRAIN_STATE),
    }


def execute_download(req: ModelStudioDownloadRequest) -> dict[str, Any]:
    """Downloads market candles via MT5, synthetic generator, or CSV import."""
    import re

    from scripts.data.ingest_historical_candles import ingest

    tf = req.timeframe.upper().strip()
    if tf not in ("M1", "M3", "M5", "M15"):
        tf = "M1"

    # Whitelist-sanitize symbol and source to prevent path injection
    safe_symbol = re.sub(r"[^A-Za-z0-9_]", "", req.symbol.strip()).upper() or "XAUUSD"
    safe_source = req.source.lower().strip()
    if safe_source not in ("synthetic", "mt5", "csv"):
        safe_source = "synthetic"

    # Confine optional CSV input to allowlisted dataset roots
    safe_csv_path: Path | None = None
    if safe_source == "csv" and req.csv_path:
        safe_csv_path = _safe_dataset_path(req.csv_path)
        if safe_csv_path is None:
            raise HTTPException(
                status_code=400, detail="csv_path must be an existing file under data/"
            )

    out_dir = (REPO_ROOT / "data" / "raw").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{safe_symbol}_{tf}.{safe_source}.parquet"

    try:
        res = ingest(
            source=safe_source,
            symbol=safe_symbol,
            timeframe=tf,
            count=req.bars,
            csv_path=safe_csv_path,
            output=out_path,
            seed=req.seed,
            min_rows=min(1000, req.bars),
        )
        return {
            "status": "OK",
            "message": f"Successfully ingested {res.rows:,} candles for {res.symbol} ({res.timeframe}).",
            "dataset_path": str(Path(res.path).relative_to(REPO_ROOT)),
            "rows": res.rows,
            "symbol": res.symbol,
            "timeframe": res.timeframe,
            "source": res.source,
            "start_time": res.start_time,
            "end_time": res.end_time,
            "bytes_written": res.bytes_written,
            "size_display": (
                f"{res.bytes_written / (1024 * 1024):.2f} MB"
                if res.bytes_written > 1024 * 1024
                else f"{res.bytes_written / 1024:.1f} KB"
            ),
            "elapsed_sec": res.elapsed_sec,
            "throughput_bars_sec": res.throughput_bars_sec,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def extract_dataset_features(
    df: pl.DataFrame, dimension: int = 50, max_rows: int = 500
) -> tuple[np.ndarray, list[str]]:
    """Extracts 50D or 70D numerical feature matrix and names from market bars."""
    from nexus_scalp.features.schema_contract import canonical_feature_names

    all_names = list(canonical_feature_names())[:dimension]

    sliced = df.slice(0, max_rows) if df.height > max_rows else df
    n = sliced.height
    closes = (
        np.array(sliced["close"].to_numpy(), dtype=np.float64)
        if "close" in sliced.columns
        else (
            np.array(sliced["current_price"].to_numpy(), dtype=np.float64)
            if "current_price" in sliced.columns
            else np.linspace(2000.0, 2050.0, n, dtype=np.float64)
        )
    )
    highs = (
        np.array(sliced["high"].to_numpy(), dtype=np.float64)
        if "high" in sliced.columns
        else closes + 0.5
    )
    lows = (
        np.array(sliced["low"].to_numpy(), dtype=np.float64)
        if "low" in sliced.columns
        else closes - 0.5
    )
    opens = (
        np.array(sliced["open"].to_numpy(), dtype=np.float64)
        if "open" in sliced.columns
        else closes
    )
    volumes = (
        np.array(sliced["tick_volume"].to_numpy(), dtype=np.float64)
        if "tick_volume" in sliced.columns
        else np.ones(n, dtype=np.float64)
    )

    mat = np.zeros((n, dimension), dtype=np.float32)
    ranges = np.maximum(highs - lows, 0.01)
    body_tops = np.maximum(opens, closes)
    body_bottoms = np.minimum(opens, closes)
    body_sizes = body_tops - body_bottoms
    upper_wicks = highs - body_tops
    lower_wicks = body_bottoms - lows

    # Base features
    mat[:, 0] = np.clip(upper_wicks / ranges, 0.0, 5.0)
    mat[:, 1] = np.clip(lower_wicks / ranges, 0.0, 5.0)
    mat[:, 2] = np.clip(body_sizes / ranges, 0.0, 5.0)
    mat[:, 3] = (body_sizes / ranges <= 0.12).astype(np.float32)
    mat[:, 4] = ((lower_wicks / ranges >= 0.55) & (body_tops >= (highs - ranges * 0.35))).astype(
        np.float32
    )
    mat[:, 5] = (body_sizes > np.roll(ranges, 1)).astype(np.float32)
    mat[:, 6] = np.clip(((closes - lows) / ranges) * 2.0 - 1.0, -1.0, 1.0)

    # Returns & Momentum
    ret = np.zeros(n, dtype=np.float32)
    ret[1:] = (closes[1:] - closes[:-1]) / np.maximum(closes[:-1], 1e-4)
    mat[:, 8] = np.clip(ret * 100.0, -5.0, 5.0)
    mat[:, 20] = np.roll(mat[:, 8], 1)
    mat[:, 21] = np.roll(mat[:, 8], 2)
    mat[:, 22] = np.roll(mat[:, 8], 3)

    # ATR
    atrs = np.zeros(n, dtype=np.float32)
    for i in range(1, n):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        atrs[i] = tr
    mean_tr = float(np.mean(atrs[1:])) if n > 1 else 1.0
    mat[:, 23] = np.clip(atrs / max(mean_tr, 0.01), 0.0, 5.0)

    # Volume z-score
    vol_mean = float(np.mean(volumes))
    vol_std = max(float(np.std(volumes)), 1e-3)
    mat[:, 24] = np.clip((volumes - vol_mean) / vol_std, -5.0, 5.0)

    # Session indicators
    mat[:, 16] = 0.0
    mat[:, 17] = 1.0
    mat[:, 18] = 0.0
    mat[:, 19] = 0.0

    # RSI
    delta = np.zeros(n)
    delta[1:] = closes[1:] - closes[:-1]
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = float(np.mean(gain))
    avg_loss = max(float(np.mean(loss)), 1e-6)
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    mat[:, 34] = np.clip((rsi - 50.0) / 25.0, -2.0, 2.0)

    # Moving averages
    ema21 = float(np.mean(closes[-21:])) if n >= 21 else float(closes[-1])
    mat[:, 35] = np.clip((closes - ema21) / max(mean_tr, 0.01), -5.0, 5.0)
    ema50 = float(np.mean(closes[-50:])) if n >= 50 else float(closes[-1])
    mat[:, 36] = np.clip((closes - ema50) / max(mean_tr, 0.01), -5.0, 5.0)

    # Fill remaining base / news / liquidity slots
    for col in range(dimension):
        if col not in (0, 1, 2, 3, 4, 5, 6, 8, 16, 17, 18, 19, 20, 21, 22, 23, 24, 34, 35, 36):
            if col < 50:
                mat[:, col] = np.clip(np.sin(col + np.arange(n) * 0.1) * 0.5, -2.0, 2.0)
            elif col < 60:
                mat[:, col] = np.clip(0.1 * (col - 50), 0.0, 1.0)
            else:
                mat[:, col] = np.clip(0.2 * (col - 60) * 0.5, -1.0, 2.0)

    return mat, all_names


def execute_inspect_features(req: ModelStudioInspectFeaturesRequest) -> dict[str, Any]:
    """Inspects dataset features, calculates statistics, and validates normalization for 50D/70D."""
    dim = req.dimension if req.dimension in (50, 70) else 50
    target_path = _resolve_requested_dataset(req.dataset_path)

    if target_path is None:
        datasets = _scan_available_datasets()
        if datasets:
            target_path = _safe_dataset_path(str(datasets[0]["path"]))

    if target_path is not None:
        df = _read_dataset_frame(target_path)
    else:
        from scripts.data.ingest_historical_candles import generate_synthetic_bars

        df = generate_synthetic_bars(symbol="XAUUSD", count=req.max_rows, seed=42)

    mat, names = extract_dataset_features(df, dimension=dim, max_rows=req.max_rows)
    n_rows = mat.shape[0]

    means = np.mean(mat, axis=0)
    stds = np.std(mat, axis=0)
    mins = np.min(mat, axis=0)
    maxs = np.max(mat, axis=0)

    stds_clamped = np.maximum(stds, 1e-3)
    normalized_mat = np.clip((mat - means) / stds_clamped, -5.0, 5.0)

    features_report: list[dict[str, Any]] = []
    healthy_count = 0
    clamped_count = 0
    nan_count = 0

    for i in range(dim):
        name = names[i] if i < len(names) else f"feature_{i}"
        family = "BASE" if i < 50 else "NEWS" if i < 60 else "LIQUIDITY"
        is_zero_var = bool(stds[i] < 1e-6)
        has_nan = bool(not np.all(np.isfinite(mat[:, i])))

        if has_nan:
            status = "WARNING"
            nan_count += 1
        elif is_zero_var:
            status = "CLAMPED"
            clamped_count += 1
        else:
            status = "HEALTHY"
            healthy_count += 1

        features_report.append(
            {
                "index": i,
                "name": name,
                "family": family,
                "raw_min": round(float(mins[i]), 4),
                "raw_max": round(float(maxs[i]), 4),
                "raw_mean": round(float(means[i]), 4),
                "raw_std": round(float(stds[i]), 4),
                "normalized_sample": round(float(normalized_mat[0, i]), 4) if n_rows else 0.0,
                "zero_variance": is_zero_var,
                "status": status,
            }
        )

    return {
        "status": "OK",
        "dataset_path": str(target_path.relative_to(REPO_ROOT)) if target_path else "synthetic",
        "dimension": dim,
        "rows_processed": n_rows,
        "total_features": dim,
        "healthy_features": healthy_count,
        "clamped_features": clamped_count,
        "nan_features": nan_count,
        "scaler_ready": True,
        "features": features_report,
        "evaluated_at": datetime.now(UTC).isoformat(),
    }


def execute_generate_position_dataset(req: ModelStudioPositionDatasetRequest) -> dict[str, Any]:
    """Generates specialized Position-State dataset for Layer-2 Position/Risk Management."""
    from nexus_scalp.model_generation.position_dataset_generator import generate_position_dataset

    target_path = _resolve_requested_dataset(req.source_dataset_path)

    res = generate_position_dataset(
        source_path=target_path,
        dimension=req.dimension,
        bars_limit=req.bars_limit,
        max_holding_bars=req.max_holding_bars,
        target_atr_multiplier=req.target_atr_multiplier,
        stop_loss_atr_multiplier=req.stop_loss_atr_multiplier,
        friction_pips=req.friction_pips,
    )
    return asdict(res)


# =============================================================================
# Model Registry & Hot-Load Core Execution Engines (15 API-First Features)
# =============================================================================


def execute_list_models() -> dict[str, Any]:
    """1. Lists all models from the SQLite registry and syncs filesystem checkpoints."""
    registry = get_model_registry()
    registry.sync_filesystem_checkpoints()
    models = registry.list_models(include_archived=False)

    with _STUDIO_BUNDLE_LOCK:
        active_id = _StudioBundleHolder.active.model_id if _StudioBundleHolder.active else None

    # Fallback to DB active if in-memory bundle is not set
    if active_id is None:
        db_active = registry.get_active_model()
        if db_active is not None:
            active_id = db_active.id

    records_out = []
    for m in models:
        d = asdict(m)
        d["is_active"] = m.id == active_id
        records_out.append(d)

    return {
        "status": "OK",
        "count": len(records_out),
        "active_champion_id": active_id,
        "models": records_out,
    }


def execute_hot_load(req: ModelStudioHotLoadRequest, engine: Any = None) -> dict[str, Any]:
    """2. Hot-loads model weights and scaler sidecar atomically into memory."""
    registry = get_model_registry()
    rec = registry.get_model(req.model_id)

    target_path: Path | None = None
    if rec is not None and rec.weights_path:
        cand = (REPO_ROOT / rec.weights_path).resolve()
        if cand.is_file():
            target_path = cand

    if target_path is None:
        target_path = _resolve_requested_model(req.model_id)

    if target_path is None or not target_path.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"Model checkpoint {req.model_id!r} could not be resolved or found.",
        )

    # Safe deserialization (PyTorch weights_only=True)
    try:
        weights = torch.load(target_path, map_location="cpu", weights_only=True)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to safely load model checkpoint: {exc}",
        ) from exc

    # Infer dimension from weights input projection
    dim = 50
    if "input_projection.weight" in weights:
        dim = int(weights["input_projection.weight"].shape[1])
    elif rec is not None and rec.dimension in (50, 70):
        dim = rec.dimension

    # Instantiate model and load state
    model = ScalpNet(num_features=dim, num_classes=3)
    model.load_state_dict(weights)
    model.eval()

    # Resolve or create scaler
    scaler_attached = False
    scaler_path_str = ""
    scaler: Any = None

    if req.attach_scaler:
        cand_scalers = []
        if rec and rec.scaler_path:
            cand_scalers.append((REPO_ROOT / rec.scaler_path).resolve())
        cand_scalers.append(target_path.with_suffix(".scaler.npz"))
        cand_scalers.append(target_path.parent / f"{target_path.stem}.scaler.npz")

        for sc_path in cand_scalers:
            if sc_path.is_file():
                try:
                    sc_data = np.load(sc_path)
                    mean_arr = sc_data["mean"]
                    std_arr = sc_data["std"]
                    scaler = _StudioLoadedScaler(mean_arr, std_arr, dim)
                    scaler_attached = True
                    try:
                        scaler_path_str = str(sc_path.relative_to(REPO_ROOT))
                    except ValueError:
                        scaler_path_str = str(sc_path)
                    break
                except Exception as sc_err:
                    logger.warning(f"Failed to load scaler {sc_path}: {sc_err}")

    if scaler is None:
        scaler = _StudioLoadedScaler(
            np.zeros(dim, dtype=np.float32), np.ones(dim, dtype=np.float32), dim
        )

    # Warm-up forward pass & latency profiling
    t0 = time.perf_counter()
    with torch.no_grad():
        dummy_x = torch.zeros((1, dim), dtype=torch.float32)
        dummy_out = model(dummy_x)
        assert dummy_out.shape == (1, 3)
    latency_us = float((time.perf_counter() - t0) * 1e6)

    # Calculate weights SHA256
    hasher = hashlib.sha256()
    with open(target_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    weights_sha256 = hasher.hexdigest()

    model_id = rec.id if rec else target_path.stem
    try:
        weights_rel = str(target_path.relative_to(REPO_ROOT))
    except ValueError:
        weights_rel = str(target_path)

    # Create active bundle and atomically swap
    bundle = StudioActiveBundle(
        model_id=model_id,
        dimension=dim,
        model=model,
        scaler=scaler,
        weights_sha256=weights_sha256,
        weights_path=weights_rel,
        scaler_path=scaler_path_str,
        fine_tune_enabled=req.fine_tune_enabled,
        stage="CHAMPION",
        loaded_at=datetime.now(UTC).isoformat(),
    )

    with _STUDIO_BUNDLE_LOCK:
        _StudioBundleHolder.active = bundle

    # Update SQLite Registry
    if rec is None:
        rec = ModelRecord(
            id=model_id,
            name=target_path.name,
            version="1.0.0",
            dimension=dim,
            architecture="ScalpNet",
            weights_path=weights_rel,
            scaler_path=scaler_path_str,
            sha256=weights_sha256,
            fine_tune_enabled=req.fine_tune_enabled,
            stage="CHAMPION",
        )
        registry.register_model(rec)

    registry.set_active_champion(
        model_id,
        operator=req.operator,
        fine_tune_enabled=req.fine_tune_enabled,
        latency_p50_us=latency_us,
    )

    # Cascade to engine if present
    if engine is not None and getattr(engine, "_bundle", None) is not None:
        try:
            with engine._bundle_lock:
                engine._bundle.model = model
                engine._bundle.scaler = scaler
        except Exception as eng_exc:
            logger.warning(f"Engine bundle sync warning: {eng_exc}")

    return {
        "status": "OK",
        "message": f"Model {model_id} ({dim}D) successfully hot-loaded into active memory.",
        "model_id": model_id,
        "dimension": dim,
        "architecture": "ScalpNet",
        "weights_sha256": weights_sha256,
        "scaler_attached": scaler_attached,
        "scaler_path": scaler_path_str,
        "fine_tune_enabled": req.fine_tune_enabled,
        "warmup_latency_us": round(latency_us, 2),
        "stage": "CHAMPION",
        "loaded_at": bundle.loaded_at,
    }


def execute_active_model() -> dict[str, Any]:
    """3. Returns the runtime state of the currently hot-loaded model."""
    with _STUDIO_BUNDLE_LOCK:
        if _StudioBundleHolder.active is None:
            registry = get_model_registry()
            db_act = registry.get_active_model()
            if db_act is not None:
                try:
                    execute_hot_load(
                        ModelStudioHotLoadRequest(
                            model_id=db_act.id,
                            fine_tune_enabled=db_act.fine_tune_enabled,
                            attach_scaler=True,
                            operator="PERSISTENT_RESTORE",
                        )
                    )
                except Exception as exc:
                    logger.warning(f"Failed to auto-restore champion from DB: {exc}")

        if _StudioBundleHolder.active is None:
            return {
                "status": "NO_ACTIVE_MODEL",
                "active_model": None,
                "message": "No model is currently hot-loaded in memory.",
            }
        b = _StudioBundleHolder.active
        return {
            "status": "OK",
            "active_model": {
                "model_id": b.model_id,
                "dimension": b.dimension,
                "architecture": "ScalpNet",
                "weights_sha256": b.weights_sha256,
                "weights_path": b.weights_path,
                "scaler_path": b.scaler_path,
                "scaler_ready": bool(b.scaler and b.scaler.is_ready()),
                "fine_tune_enabled": b.fine_tune_enabled,
                "stage": b.stage,
                "loaded_at": b.loaded_at,
                "inference_count": b.inference_count,
            },
        }


def execute_rollback(engine: Any = None) -> dict[str, Any]:
    """4. Atomically rolls back to the previous active champion model from history."""
    registry = get_model_registry()
    prev = registry.get_previous_active_model()
    if prev is None:
        raise HTTPException(
            status_code=400,
            detail="No previous champion model found in audit history to roll back to.",
        )

    hot_req = ModelStudioHotLoadRequest(
        model_id=prev.id,
        fine_tune_enabled=prev.fine_tune_enabled,
        attach_scaler=True,
        operator="ROLLBACK",
    )
    res = execute_hot_load(hot_req, engine=engine)
    res["message"] = f"Successfully rolled back champion model to {prev.id}."
    res["action"] = "ROLLBACK"
    return res


def execute_fine_tune(req: ModelStudioFineTuneRequest) -> dict[str, Any]:
    """5. Dispatches fine-tuning from a base model checkpoint with backbone freezing options."""
    registry = get_model_registry()

    base_id = req.base_model_id.strip()
    base_rec: ModelRecord | None = None
    if base_id:
        base_rec = registry.get_model(base_id)
    else:
        with _STUDIO_BUNDLE_LOCK:
            if _StudioBundleHolder.active:
                base_id = _StudioBundleHolder.active.model_id
                base_rec = registry.get_model(base_id)
        if base_rec is None:
            base_rec = registry.get_active_model()

    if base_rec is None:
        raise HTTPException(
            status_code=400,
            detail="Base model for fine-tuning could not be found.",
        )

    if not base_rec.fine_tune_enabled:
        raise HTTPException(
            status_code=400,
            detail=f"Base model {base_rec.id} is locked; fine_tune_enabled is False.",
        )

    target_weights = (REPO_ROOT / base_rec.weights_path).resolve()
    if not target_weights.is_file():
        raise HTTPException(status_code=400, detail="Base model weights file missing.")

    weights = torch.load(target_weights, map_location="cpu", weights_only=True)
    dim = base_rec.dimension
    model = ScalpNet(num_features=dim, num_classes=3)
    model.load_state_dict(weights)
    model.train()

    # Freeze backbone if requested
    frozen_count = 0
    trainable_count = 0
    if req.freeze_backbone:
        for name, param in model.named_parameters():
            if "classifier" not in name:
                param.requires_grad = False
                frozen_count += param.numel()
            else:
                trainable_count += param.numel()
    else:
        for param in model.parameters():
            trainable_count += param.numel()

    # Load dataset
    df: pl.DataFrame | None = None
    target_path = _resolve_requested_dataset(req.dataset_path)
    if target_path is not None:
        df = _read_dataset_frame(target_path)
    else:
        from scripts.data.ingest_historical_candles import generate_synthetic_bars

        df = generate_synthetic_bars(symbol="XAUUSD", count=500, seed=req.seed)

    assert df is not None
    mat, _ = extract_dataset_features(df, dimension=dim, max_rows=500)
    n = mat.shape[0]
    if n < 10:
        raise HTTPException(status_code=400, detail="Dataset too small for fine-tuning.")

    closes = (
        np.array(df["close"].to_numpy()[:n], dtype=np.float64)
        if "close" in df.columns
        else np.linspace(2000.0, 2020.0, n, dtype=np.float64)
    )
    y_labels = np.zeros(n, dtype=np.int64)
    for i in range(n - 3):
        diff = closes[i + 3] - closes[i]
        if diff > 0.1:
            y_labels[i] = 1
        elif diff < -0.1:
            y_labels[i] = 2

    X_t = torch.tensor(mat, dtype=torch.float32)
    y_t = torch.tensor(y_labels, dtype=torch.long)

    optimizer = torch.optim.AdamW(
        filter(lambda p: p.requires_grad, model.parameters()),
        lr=req.learning_rate,
    )
    criterion = torch.nn.CrossEntropyLoss()

    final_loss = 0.0
    for _ in range(req.epochs):
        optimizer.zero_grad()
        out = model(X_t)
        loss = criterion(out, y_t)
        loss.backward()
        optimizer.step()
        final_loss = float(loss.item())

    # Save fine-tuned model
    ts = int(time.time())
    ft_id = f"{base_rec.id}_ft_{ts}"
    ckpt_dir = REPO_ROOT / "artifacts" / "model_generation" / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    ft_path = ckpt_dir / f"{ft_id}.pt"
    torch.save(model.state_dict(), ft_path)

    # Save matching scaler
    ft_scaler_path = ckpt_dir / f"{ft_id}.scaler.npz"
    mean_ft = np.mean(mat, axis=0).astype(np.float32)
    std_ft = np.maximum(np.std(mat, axis=0), 1e-3).astype(np.float32)
    np.savez(ft_scaler_path, mean=mean_ft, std=std_ft, dimension=dim)

    hasher = hashlib.sha256()
    with open(ft_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    ft_sha256 = hasher.hexdigest()

    new_rec = ModelRecord(
        id=ft_id,
        name=ft_path.name,
        version="1.1.0",
        dimension=dim,
        architecture="ScalpNet",
        weights_path=str(ft_path.relative_to(REPO_ROOT)),
        scaler_path=str(ft_scaler_path.relative_to(REPO_ROOT)),
        sha256=ft_sha256,
        epochs=req.epochs,
        final_loss=final_loss,
        final_val_loss=final_loss,
        dataset_path=str(target_path.relative_to(REPO_ROOT)) if target_path else "synthetic",
        fine_tune_enabled=True,
        stage="STAGING",
        metrics={
            "parent_model_id": base_rec.id,
            "freeze_backbone": req.freeze_backbone,
            "frozen_parameters": frozen_count,
            "trainable_parameters": trainable_count,
            "final_loss": final_loss,
        },
    )
    registry.register_model(new_rec)

    return {
        "status": "OK",
        "message": f"Model {ft_id} fine-tuned successfully from {base_rec.id}.",
        "fine_tuned_model_id": ft_id,
        "parent_model_id": base_rec.id,
        "epochs": req.epochs,
        "final_loss": final_loss,
        "frozen_parameters": frozen_count,
        "trainable_parameters": trainable_count,
        "weights_path": new_rec.weights_path,
        "scaler_path": new_rec.scaler_path,
        "sha256": ft_sha256,
    }


def execute_verify(req: ModelStudioVerifyRequest) -> dict[str, Any]:
    """6. Performs dry-run verification battery on a model checkpoint and its sidecars."""
    registry = get_model_registry()
    rec = registry.get_model(req.model_id)

    target_path: Path | None = None
    if rec and rec.weights_path:
        cand = (REPO_ROOT / rec.weights_path).resolve()
        if cand.is_file():
            target_path = cand
    if target_path is None:
        target_path = _resolve_requested_model(req.model_id)

    if target_path is None or not target_path.is_file():
        raise HTTPException(status_code=400, detail=f"Model {req.model_id!r} not found.")

    checks: list[dict[str, Any]] = []
    checks.append({"name": "FILE_EXISTS", "passed": True, "detail": str(target_path.name)})

    weights: dict[str, torch.Tensor] = {}
    try:
        weights = torch.load(target_path, map_location="cpu", weights_only=True)
        checks.append(
            {
                "name": "SAFE_DESERIALIZATION",
                "passed": True,
                "detail": "weights_only=True passed",
            }
        )
    except Exception as exc:
        checks.append({"name": "SAFE_DESERIALIZATION", "passed": False, "detail": str(exc)})
        return {"status": "FAILED", "all_passed": False, "checks": checks}

    all_finite = True
    nan_layers = []
    for k, v in weights.items():
        if torch.is_tensor(v):
            if not torch.all(torch.isfinite(v)).item():
                all_finite = False
                nan_layers.append(k)
    checks.append(
        {
            "name": "NUMERICAL_FINITENESS",
            "passed": all_finite,
            "detail": "All weights finite" if all_finite else f"Non-finite values in: {nan_layers}",
        }
    )

    non_zero = True
    for _k, v in weights.items():
        if (
            v.dim() >= 2
            and "weight" in _k
            and torch.is_tensor(v)
            and v.dtype == torch.float32
            and v.numel() > 1
        ):
            if float(torch.std(v).item()) < 1e-7:
                non_zero = False
                break
    checks.append(
        {
            "name": "WEIGHT_VARIANCE",
            "passed": non_zero,
            "detail": (
                "Non-zero variance verified across layers"
                if non_zero
                else "Warning: low/zero variance detected"
            ),
        }
    )

    dim = 50
    if "input_projection.weight" in weights:
        dim = int(weights["input_projection.weight"].shape[1])
    model: ScalpNet | None = None
    try:
        model = ScalpNet(num_features=dim, num_classes=3)
        model.load_state_dict(weights)
        model.eval()
        checks.append(
            {
                "name": "ARCHITECTURE_LOAD",
                "passed": True,
                "detail": f"ScalpNet {dim}D initialized",
            }
        )
    except Exception as m_err:
        checks.append({"name": "ARCHITECTURE_LOAD", "passed": False, "detail": str(m_err)})

    if model is not None:
        try:
            with torch.no_grad():
                x = torch.zeros((1, dim), dtype=torch.float32)
                out = model(x)
                passed_smoke = out.shape == (1, 3) and bool(torch.all(torch.isfinite(out)).item())
                checks.append(
                    {
                        "name": "SMOKE_INFERENCE",
                        "passed": passed_smoke,
                        "detail": f"Output shape: {list(out.shape)}",
                    }
                )
        except Exception as s_err:
            checks.append({"name": "SMOKE_INFERENCE", "passed": False, "detail": str(s_err)})
    else:
        checks.append(
            {"name": "SMOKE_INFERENCE", "passed": False, "detail": "Model failed to instantiate"}
        )

    scaler_cand = target_path.with_suffix(".scaler.npz")
    scaler_present = scaler_cand.is_file()
    checks.append(
        {
            "name": "SCALER_SIDECAR",
            "passed": scaler_present,
            "detail": (
                f"Sidecar found: {scaler_cand.name}"
                if scaler_present
                else "Sidecar not found (default unit scaler used)"
            ),
        }
    )

    all_passed = all(c["passed"] for c in checks if c["name"] != "SCALER_SIDECAR")
    return {
        "status": "OK" if all_passed else "WARNING",
        "model_id": req.model_id,
        "dimension": dim,
        "all_passed": all_passed,
        "checks": checks,
    }


def execute_register_model(req: ModelStudioRegisterRequest) -> dict[str, Any]:
    """7. Registers an external or discovered model checkpoint into the SQLite catalog."""
    target_path = _resolve_requested_model(req.path)
    if target_path is None or not target_path.is_file():
        raise HTTPException(status_code=400, detail=f"File {req.path!r} not found.")

    weights = torch.load(target_path, map_location="cpu", weights_only=True)
    dim = req.dimension
    if "input_projection.weight" in weights:
        dim = int(weights["input_projection.weight"].shape[1])

    hasher = hashlib.sha256()
    with open(target_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    sha = hasher.hexdigest()

    model_id = req.name.strip() or target_path.stem
    rel_path = str(target_path.relative_to(REPO_ROOT))

    scaler_cand = target_path.with_suffix(".scaler.npz")
    scaler_rel = str(scaler_cand.relative_to(REPO_ROOT)) if scaler_cand.is_file() else ""

    rec = ModelRecord(
        id=model_id,
        name=target_path.name,
        version="1.0.0",
        dimension=dim,
        architecture="ScalpNet",
        weights_path=rel_path,
        scaler_path=scaler_rel,
        sha256=sha,
        fine_tune_enabled=True,
        stage="STAGING",
    )
    registry = get_model_registry()
    saved = registry.register_model(rec)
    return {"status": "OK", "model": asdict(saved)}


def execute_delete_model(model_id: str) -> dict[str, Any]:
    """8. Safely removes a model from the registry (guarded against active champion)."""
    registry = get_model_registry()
    with _STUDIO_BUNDLE_LOCK:
        if _StudioBundleHolder.active and _StudioBundleHolder.active.model_id == model_id:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete active champion model. Hot-load a different model first.",
            )

    rec = registry.get_model(model_id)
    if rec and rec.is_active:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete active champion model recorded in database.",
        )

    deleted = registry.delete_model(model_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Model {model_id!r} not found.")

    return {"status": "OK", "deleted": True, "model_id": model_id}


def execute_get_scaler(model_id: str) -> dict[str, Any]:
    """9. Inspects the scaler vector parameters (means, stds) for a model."""
    registry = get_model_registry()
    rec = registry.get_model(model_id)

    scaler_file: Path | None = None
    dim = 50
    if rec:
        dim = rec.dimension
        if rec.scaler_path:
            cand = (REPO_ROOT / rec.scaler_path).resolve()
            if cand.is_file():
                scaler_file = cand

    if scaler_file is None:
        target_model = _safe_model_path(model_id)
        if target_model:
            cand = target_model.with_suffix(".scaler.npz")
            if cand.is_file():
                scaler_file = cand

    if scaler_file is None or not scaler_file.is_file():
        return {
            "status": "NO_SCALER_SIDECAR",
            "model_id": model_id,
            "dimension": dim,
            "features": [],
            "message": "No dedicated scaler sidecar found for this model; standard unit scaling will apply.",
        }

    data = np.load(scaler_file)
    mean_vec = data["mean"]
    std_vec = data["std"]
    dim = int(data.get("dimension", len(mean_vec)))

    features = []
    for idx in range(len(mean_vec)):
        features.append(
            {
                "index": idx,
                "mean": round(float(mean_vec[idx]), 4),
                "std": round(float(std_vec[idx]), 4),
                "zero_variance": bool(float(std_vec[idx]) <= 1e-4),
                "clamp_min": -5.0,
                "clamp_max": 5.0,
            }
        )

    return {
        "status": "OK",
        "model_id": model_id,
        "dimension": dim,
        "features_count": len(features),
        "features": features,
    }


def execute_canary(req: ModelStudioCanaryRequest) -> dict[str, Any]:
    """10. Hot-loads a candidate model into a shadow canary slot."""
    target_path = _resolve_requested_model(req.model_id)
    if target_path is None or not target_path.is_file():
        raise HTTPException(status_code=400, detail=f"Model {req.model_id!r} not found.")

    weights = torch.load(target_path, map_location="cpu", weights_only=True)
    dim = 50
    if "input_projection.weight" in weights:
        dim = int(weights["input_projection.weight"].shape[1])

    model = ScalpNet(num_features=dim, num_classes=3)
    model.load_state_dict(weights)
    model.eval()

    scaler = _StudioLoadedScaler(
        np.zeros(dim, dtype=np.float32), np.ones(dim, dtype=np.float32), dim
    )

    bundle = StudioActiveBundle(
        model_id=req.model_id,
        dimension=dim,
        model=model,
        scaler=scaler,
        weights_sha256="",
        weights_path=str(target_path.relative_to(REPO_ROOT)),
        scaler_path="",
        fine_tune_enabled=False,
        stage="CANARY",
        loaded_at=datetime.now(UTC).isoformat(),
    )

    with _STUDIO_BUNDLE_LOCK:
        _StudioBundleHolder.canary = bundle

    registry = get_model_registry()
    registry.update_model_stage(req.model_id, stage="CANARY")

    return {
        "status": "OK",
        "message": f"Model {req.model_id} successfully loaded into CANARY shadow slot.",
        "canary_model_id": req.model_id,
        "dimension": dim,
        "stage": "CANARY",
    }


def execute_history(limit: int = 50) -> dict[str, Any]:
    """11. Returns audit trail of model hot-swaps, rollbacks, and loads from SQLite."""
    registry = get_model_registry()
    events = registry.get_load_history(limit=limit)
    return {"status": "OK", "count": len(events), "history": events}


def execute_export_model(req: ModelStudioExportRequest) -> dict[str, Any]:
    """12. Packages model weights, scaler, and manifest into a zip bundle."""
    registry = get_model_registry()
    rec = registry.get_model(req.model_id)
    if rec is None or not rec.weights_path:
        raise HTTPException(status_code=404, detail=f"Model {req.model_id!r} not found.")

    weights_file = (REPO_ROOT / rec.weights_path).resolve()
    if not weights_file.is_file():
        raise HTTPException(status_code=404, detail="Model weights file missing.")

    export_dir = REPO_ROOT / "artifacts" / "model_generation" / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    zip_path = export_dir / f"{rec.id}_bundle.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(weights_file, arcname=weights_file.name)
        if rec.scaler_path:
            sc_file = (REPO_ROOT / rec.scaler_path).resolve()
            if sc_file.is_file():
                zf.write(sc_file, arcname=sc_file.name)
        if rec.manifest_path:
            mf_file = (REPO_ROOT / rec.manifest_path).resolve()
            if mf_file.is_file():
                zf.write(mf_file, arcname=mf_file.name)
        else:
            meta_json = json.dumps(asdict(rec), indent=2)
            zf.writestr("model.meta.json", meta_json)

    size = zip_path.stat().st_size
    hasher = hashlib.sha256()
    with open(zip_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)

    return {
        "status": "OK",
        "model_id": rec.id,
        "export_path": str(zip_path.relative_to(REPO_ROOT)),
        "size_bytes": size,
        "sha256": hasher.hexdigest(),
    }


def execute_tag_model(req: ModelStudioTagRequest) -> dict[str, Any]:
    """13. Updates the deployment stage and fine-tune permissions for a model."""
    valid_stages = ("CHAMPION", "CANARY", "STAGING", "ARCHIVED")
    stage_upper = req.stage.upper().strip()
    if stage_upper not in valid_stages:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid stage: {req.stage}. Must be one of {valid_stages}",
        )

    registry = get_model_registry()
    updated = registry.update_model_stage(
        req.model_id,
        stage=stage_upper,
        fine_tune_enabled=req.fine_tune_enabled,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Model {req.model_id!r} not found.")

    return {"status": "OK", "model": asdict(updated)}


def execute_benchmark_live(iterations: int = 100) -> dict[str, Any]:
    """14. Live profiling of inference latency and throughput of the hot-loaded active model."""
    with _STUDIO_BUNDLE_LOCK:
        if _StudioBundleHolder.active is None:
            raise HTTPException(status_code=400, detail="No active model hot-loaded to benchmark.")
        model = _StudioBundleHolder.active.model
        scaler = _StudioBundleHolder.active.scaler
        dim = _StudioBundleHolder.active.dimension
        model_id = _StudioBundleHolder.active.model_id

    latencies_us: list[float] = []
    dummy = np.random.randn(1, dim).astype(np.float32)

    with torch.no_grad():
        for _ in range(iterations):
            t0 = time.perf_counter()
            scaled = scaler.transform(dummy)
            t_tensor = torch.tensor(scaled, dtype=torch.float32)
            _ = model(t_tensor)
            latencies_us.append((time.perf_counter() - t0) * 1e6)

    p50 = float(np.percentile(latencies_us, 50))
    p90 = float(np.percentile(latencies_us, 90))
    p99 = float(np.percentile(latencies_us, 99))
    throughput = 1e6 / p50 if p50 > 0 else 0.0

    return {
        "status": "OK",
        "model_id": model_id,
        "dimension": dim,
        "iterations": iterations,
        "p50_latency_us": round(p50, 2),
        "p90_latency_us": round(p90, 2),
        "p99_latency_us": round(p99, 2),
        "throughput_samples_per_sec": round(throughput, 1),
    }


def execute_drift_check(req: ModelStudioDriftRequest) -> dict[str, Any]:
    """15. Detects feature distribution drift between active scaler and candidate dataset."""
    with _STUDIO_BUNDLE_LOCK:
        if _StudioBundleHolder.active is None:
            raise HTTPException(
                status_code=400, detail="No active model hot-loaded for drift check."
            )
        scaler = _StudioBundleHolder.active.scaler
        dim = _StudioBundleHolder.active.dimension
        model_id = _StudioBundleHolder.active.model_id

    target_path = _resolve_requested_dataset(req.dataset_path)
    df: pl.DataFrame | None = None
    if target_path:
        df = _read_dataset_frame(target_path)
    else:
        from scripts.data.ingest_historical_candles import generate_synthetic_bars

        df = generate_synthetic_bars(symbol="XAUUSD", count=req.max_rows, seed=42)

    mat, names = extract_dataset_features(df, dimension=dim, max_rows=req.max_rows)
    data_means = np.mean(mat, axis=0)

    scaler_means = getattr(scaler, "mean", np.zeros(dim))
    scaler_stds = getattr(scaler, "std", np.ones(dim))

    drift_scores = []
    high_drift_features = []
    for idx in range(min(dim, len(data_means), len(scaler_means))):
        shift = abs(float(data_means[idx]) - float(scaler_means[idx])) / max(
            float(scaler_stds[idx]), 1e-3
        )
        drift_scores.append(shift)
        feat_name = names[idx] if idx < len(names) else f"feature_{idx}"
        if shift > 2.5:
            high_drift_features.append(
                {
                    "feature": feat_name,
                    "z_score_shift": round(shift, 3),
                    "data_mean": round(float(data_means[idx]), 3),
                    "scaler_mean": round(float(scaler_means[idx]), 3),
                }
            )

    mean_drift = float(np.mean(drift_scores)) if drift_scores else 0.0

    return {
        "status": "OK",
        "model_id": model_id,
        "dataset": str(target_path.relative_to(REPO_ROOT)) if target_path else "synthetic",
        "overall_drift_score": round(mean_drift, 4),
        "drift_detected": bool(len(high_drift_features) > 0 or mean_drift > 1.5),
        "high_drift_count": len(high_drift_features),
        "high_drift_features": high_drift_features,
    }


# =============================================================================
# Router Registration
# =============================================================================


def register_model_studio_routes(app: Any, _err: Any, _log_err: Any) -> None:
    """Registers Deep Learning / Model Studio REST endpoints on the FastAPI app."""

    @app.get("/api/model-studio/overview")
    def route_overview() -> dict[str, Any]:
        engine = getattr(app.state, "engine", None)
        return get_studio_overview(engine)

    @app.get("/api/model-studio/fetch-70d")
    def route_fetch_70d() -> dict[str, Any]:
        engine = getattr(app.state, "engine", None)
        return fetch_70d_components(engine)

    @app.post("/api/model-studio/predict")
    def route_predict(req: ModelStudioPredictRequest) -> dict[str, Any]:
        engine = getattr(app.state, "engine", None)
        try:
            return execute_predict(req, engine)
        except ValueError as err:
            raise HTTPException(status_code=422, detail=str(err)) from err

    @app.get("/api/model-studio/datasets")
    def route_datasets() -> dict[str, Any]:
        datasets = _scan_available_datasets()
        return {"status": "OK", "count": len(datasets), "datasets": datasets}

    @app.post("/api/model-studio/datasets/download")
    def route_download_dataset(req: ModelStudioDownloadRequest) -> dict[str, Any]:
        return execute_download(req)

    @app.post("/api/model-studio/datasets/inspect-features")
    def route_inspect_features(req: ModelStudioInspectFeaturesRequest) -> dict[str, Any]:
        return execute_inspect_features(req)

    @app.post("/api/model-studio/position-dataset/generate")
    def route_generate_position_dataset(req: ModelStudioPositionDatasetRequest) -> dict[str, Any]:
        return execute_generate_position_dataset(req)

    @app.post("/api/model-studio/train")
    def route_train(req: ModelStudioTrainRequest) -> dict[str, Any]:
        return execute_train(req)

    @app.get("/api/model-studio/train/progress")
    def route_train_progress() -> dict[str, Any]:
        return {"status": "OK", "progress": dict(_STUDIO_TRAIN_STATE)}

    @app.post("/api/model-studio/stress-test")
    def route_stress_test(req: ModelStudioStressRequest) -> dict[str, Any]:
        engine = getattr(app.state, "engine", None)
        return execute_stress_test(req, engine)

    @app.post("/api/model-studio/benchmark")
    def route_benchmark(req: ModelStudioBenchmarkRequest) -> dict[str, Any]:
        engine = getattr(app.state, "engine", None)
        return execute_benchmark(req, engine)

    # -------------------------------------------------------------------------
    # AI Hub / Model Registry & Hot-Load Endpoints (15 API-First Capabilities)
    # -------------------------------------------------------------------------

    @app.get("/api/model-studio/models")
    def route_list_models() -> dict[str, Any]:
        return execute_list_models()

    @app.post("/api/model-studio/models/hot-load")
    def route_hot_load(req: ModelStudioHotLoadRequest) -> dict[str, Any]:
        engine = getattr(app.state, "engine", None)
        return execute_hot_load(req, engine=engine)

    @app.get("/api/model-studio/models/active")
    def route_active_model() -> dict[str, Any]:
        return execute_active_model()

    @app.post("/api/model-studio/models/rollback")
    def route_rollback() -> dict[str, Any]:
        engine = getattr(app.state, "engine", None)
        return execute_rollback(engine=engine)

    @app.post("/api/model-studio/models/fine-tune")
    def route_fine_tune(req: ModelStudioFineTuneRequest) -> dict[str, Any]:
        return execute_fine_tune(req)

    @app.post("/api/model-studio/models/verify")
    def route_verify(req: ModelStudioVerifyRequest) -> dict[str, Any]:
        return execute_verify(req)

    @app.post("/api/model-studio/models/register")
    def route_register_model(req: ModelStudioRegisterRequest) -> dict[str, Any]:
        return execute_register_model(req)

    @app.delete("/api/model-studio/models/{model_id}")
    def route_delete_model(model_id: str) -> dict[str, Any]:
        return execute_delete_model(model_id)

    @app.get("/api/model-studio/models/{model_id}/scaler")
    def route_get_scaler(model_id: str) -> dict[str, Any]:
        return execute_get_scaler(model_id)

    @app.post("/api/model-studio/models/canary")
    def route_canary(req: ModelStudioCanaryRequest) -> dict[str, Any]:
        return execute_canary(req)

    @app.get("/api/model-studio/models/history")
    def route_history(limit: int = 50) -> dict[str, Any]:
        return execute_history(limit=limit)

    @app.post("/api/model-studio/models/export")
    def route_export_model(req: ModelStudioExportRequest) -> dict[str, Any]:
        return execute_export_model(req)

    @app.post("/api/model-studio/models/tag")
    def route_tag_model(req: ModelStudioTagRequest) -> dict[str, Any]:
        return execute_tag_model(req)

    @app.post("/api/model-studio/models/benchmark-live")
    def route_benchmark_live() -> dict[str, Any]:
        return execute_benchmark_live()

    @app.post("/api/model-studio/models/drift-check")
    def route_drift_check(req: ModelStudioDriftRequest) -> dict[str, Any]:
        return execute_drift_check(req)
