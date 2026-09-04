"""SAFE LOADER + PATH ALLOW-LIST for production model artifacts (P0 security).

Every production model loader must go through load_state_dict_safe():

    * weights_only=True deserialization (no arbitrary pickle objects)
    * pure state_dict enforcement (dict of tensors only)
    * expected-key + shape verification against the canonical contract
    * canonical head/class-count verification (3) and input width
    * path resolved inside the approved artifact roots (champion_guard)
    * symlink/traversal safe (realpath-based)

Legacy 4-wide heads stay loadable ONLY for explicitly legacy paths that pass
expected_classes=4 — the canonical serving path always asks for the
CANONICAL class count.
"""

from __future__ import annotations

from pathlib import Path

import torch

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.training.champion_guard import resolve_under

logger = get_logger("nexus_scalp.training.safe_loader")

CANONICAL_INPUT_DIM = 70
CANONICAL_CLASSES = 3


class SafeLoadError(RuntimeError):
    """Raised when an artifact fails safe loading / contract inspection."""


def load_state_dict_safe(
    path: Path | str,
    *,
    expected_input_dim: int | None = None,
    expected_classes: int | None = None,
    enforce_canonical: bool = False,
    check_approved_root: bool = True,
) -> dict[str, torch.Tensor]:
    """Safe, contract-checked state_dict load.

    enforce_canonical=True additionally requires the canonical 70D/3-class
    geometry (the canonical 70D scalp_v3 production contract).
    """
    p = Path(path)
    if check_approved_root:
        p = resolve_under(p)  # traversal / symlink / champion-loader guard
    if not p.exists():
        raise SafeLoadError(f"SAFE_LOAD_ABORT: artifact missing: {p}")
    try:
        state = torch.load(p, map_location="cpu", weights_only=True)
    except Exception as err:
        raise SafeLoadError(
            f"SAFE_LOAD_ABORT: weights_only load failed for {p.name}: {err}"
        ) from err
    if not isinstance(state, dict) or not state:
        raise SafeLoadError(f"SAFE_LOAD_ABORT: {p.name} is not a state_dict (unexpected object)")
    for k, v in state.items():
        if not isinstance(k, str) or not hasattr(v, "shape"):
            raise SafeLoadError(f"SAFE_LOAD_ABORT: {p.name} contains non-tensor entry {k!r}")

    ip = state.get("input_projection.weight")
    cls = state.get("classifier.weight")
    if ip is None or not hasattr(ip, "shape") or ip.ndim != 2:
        raise SafeLoadError(f"SAFE_LOAD_ABORT: {p.name} missing input_projection.weight")
    if cls is None or not hasattr(cls, "shape") or cls.ndim != 2:
        raise SafeLoadError(f"SAFE_LOAD_ABORT: {p.name} missing classifier.weight")
    dim = int(ip.shape[1])
    head = int(cls.shape[0])
    if enforce_canonical:
        if dim != CANONICAL_INPUT_DIM or head != CANONICAL_CLASSES:
            raise SafeLoadError(
                f"SAFE_LOAD_ABORT: {p.name} violates canonical contract "
                f"(input={dim}, head={head}; expected 70/3)"
            )
    if expected_input_dim is not None and dim != int(expected_input_dim):
        raise SafeLoadError(
            f"SAFE_LOAD_ABORT: {p.name} input width {dim} != expected {expected_input_dim}"
        )
    if expected_classes is not None and head != int(expected_classes):
        raise SafeLoadError(
            f"SAFE_LOAD_ABORT: {p.name} head {head} != expected classes {expected_classes}"
        )
    logger.info("SAFE_LOAD_OK path=%s input=%d head=%d", p.name, dim, head)
    return state  # type: ignore[return-value]


def sidecar_paths_for(model_path: Path | str) -> dict[str, Path]:
    """Canonical sibling sidecar paths (scaler/meta/manifest) for a model."""
    m = Path(model_path)
    return {
        "scaler": m.with_name("model.scaler.npz"),
        "meta": m.with_name("model.meta.json"),
        "manifest": m.parent / "manifest.json",
    }
