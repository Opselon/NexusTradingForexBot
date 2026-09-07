"""HotSwapService — atomic serving-artifact swap (safe hot swap).

P1 seam L4 (god-file decomposition, extraction 4 of the live-engine wave):
``hot_swap_model`` leaves ``application/live_engine.py`` verbatim
(behavior-preserving extraction). Governance contract preserved:
approved-root path allow-list, manifest hash verification,
production_eligible field, dimension gate, warm-up forward pass,
bundle-lock swap, trainer rebind, active-model registration.

State ownership: ``_bundle`` / ``_bundle_lock`` stay at the composition root
(LiveEngine), reached through ``self.om``; this module owns the SWAP LOGIC.
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.application.live.hot_swap")


class HotSwapService:
    """Atomic model-artifact hot swap (composition root: LiveEngine)."""

    def __init__(self, om: Any) -> None:
        self.om = om

    async def swap_model(self, new_artifact_path: str, *, source: str = "WEB_UI") -> dict:
            """Atomically swap the serving model artifact (safe hot swap).
    
            Loads + validates + warms the NEW bundle FIRST; only on success the
            current bundle is released and the new one becomes authoritative.
            In-flight inference completes against the old bundle under the
            bundle lock. Never replaces a healthy model with an invalid artifact.
    
            P0-2026-09-04 SECURITY HARDENING (hot-swap governance):
              * path allow-list — the swap target must resolve inside the
                approved artifact roots (traversal / symlink escape / arbitrary
                external files rejected before any load);
              * bundle coherence — a manifest.json next to the artifact is
                verified against the actual bytes when present (stale sidecar /
                hash mismatch rejected);
              * safe load — weights_only state_dict deserialization with
                declared-width verification (no arbitrary pickle objects);
              * metadata/head coherence — model.meta.json class count must equal
                the actual tensor head; a rejected candidate (REJECTED lifecycle
                or production_eligible=False when the field exists) cannot be
                activated through the swap path.
            """
            from nexus_scalp.training.safe_loader import load_state_dict_safe
    
            new_path = Path(new_artifact_path)
            old_path = Path(self.om.config.model.model_artifact_path)
            if not new_path.exists():
                logger.error(
                    "[MODEL_HOT_SWAP] event=MODEL_HOT_SWAP_FAILED reason=ARTIFACT_MISSING path=%s",
                    new_artifact_path,
                )
                return {
                    "success": False,
                    "reason": "ARTIFACT_MISSING",
                    "runtime_applied": False,
                }
            # P0 hardening: allow-list BEFORE any load (never trust the caller).
            try:
                from nexus_scalp.training.champion_guard import resolve_under
    
                resolved = resolve_under(new_path)
            except Exception as path_err:
                logger.error(
                    "[MODEL_HOT_SWAP] event=MODEL_HOT_SWAP_FAILED reason=PATH_REJECTED detail=%s",
                    path_err,
                )
                return {
                    "success": False,
                    "reason": "PATH_REJECTED",
                    "detail": str(path_err),
                    "runtime_applied": False,
                }
            del resolved
            try:
                # Governance coherence: verify the bundle manifest when present.
                manifest_path = new_path.parent / "manifest.json"
                meta_path = new_path.with_suffix(".meta.json")
                if manifest_path.exists():
                    import hashlib
                    import json as _json
    
                    manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
                    h = hashlib.sha256(new_path.read_bytes()).hexdigest()
                    if str(manifest.get("model_sha256", "")) and h != manifest["model_sha256"]:
                        return {
                            "success": False,
                            "reason": "BUNDLE_HASH_MISMATCH",
                            "runtime_applied": False,
                        }
                    if manifest.get("production_eligible") is False:
                        return {
                            "success": False,
                            "reason": "CANDIDATE_NOT_PRODUCTION_ELIGIBLE",
                            "runtime_applied": False,
                        }
                if meta_path.exists():
                    import json as _json
    
                    meta = _json.loads(meta_path.read_text(encoding="utf-8"))
                    if meta.get("production_eligible") is False:
                        return {
                            "success": False,
                            "reason": "CANDIDATE_NOT_PRODUCTION_ELIGIBLE",
                            "runtime_applied": False,
                        }
                # AGENT-10 (TASK-AGENT10-MODEL-PIPELINE): metadata/tensor
                # coherence + schema-identity gate BEFORE any attach. The P0
                # docstring always claimed head==meta class coherence; the check
                # is now real: artifact head vs meta num_classes, artifact width
                # vs meta declared dimension, and the meta schema id must be
                # REGISTERED (dimension equality alone is not identity).
                coherence = self.om._artifact_meta_coherence(new_path)
                if not coherence["ok"]:
                    logger.error(
                        "[MODEL_HOT_SWAP] event=MODEL_HOT_SWAP_FAILED reason=%s detail=%s",
                        coherence["reason"],
                        coherence,
                    )
                    return {
                        "success": False,
                        "reason": coherence["reason"],
                        "detail": coherence,
                        "runtime_applied": False,
                    }
                # Safe deserialization + declared-width verification (dimension
                # gate: the artifact's own declared contract, as before).
                expected_dim = self.om._expected_num_features_for_artifact(new_path)
    
                def _safe_state():
                    return load_state_dict_safe(
                        new_path, expected_input_dim=expected_dim, check_approved_root=False
                    )
    
                await asyncio.to_thread(_safe_state)
                # Load + validate the NEW bundle in isolation (never touching
                # the serving bundle). _load_or_create_bundle raises on dimension
                # mismatch and quarantines corrupt checkpoints.
                new_bundle = await asyncio.to_thread(
                    self.om._load_or_create_bundle, model_path=new_path, force_fresh=False
                )
    
                def _warmup_and_hash():
                    # Warm-up: one forward pass validates the artifact end-to-end.
                    import hashlib
    
                    import numpy as np
                    import torch
    
                    warm = np.zeros((1, int(new_bundle.model.num_features)), dtype=np.float32)
                    warm = new_bundle.scaler.transform(warm)
                    with torch.inference_mode():
                        new_bundle.model(torch.tensor(warm, dtype=torch.float32))
    
                    # Compute artifact hash for traceability (model version/hash)
                    h = hashlib.sha256()
                    with open(new_path, "rb") as f:
                        for chunk in iter(lambda: f.read(65536), b""):
                            h.update(chunk)
                    return h.hexdigest()[:16]
    
                artifact_hash = await asyncio.to_thread(_warmup_and_hash)
    
                # ATOMIC SWAP under the bundle lock: new bundle replaces old.
                with self.om._bundle_lock:
                    self.om._bundle = new_bundle
                # BUG-185: the new artifact may declare a different contract
                # width - rebind the online trainer before anything retrains.
                self.om._rebind_trainer_to_bundle()
                self.om.config.model.model_artifact_path = new_artifact_path
                self.om._register_active_model(model_path=new_path, replaced=True)
                # Surface model version/hash on the runtime snapshot
                self.om.runtime_config.apply(
                    {"model.model_artifact_path": new_artifact_path},
                    source=f"MODEL_HOT_SWAP::{source}",
                )
                logger.info(
                    "[MODEL_HOT_SWAP] event=MODEL_HOT_SWAP_COMPLETED source=%s "
                    "artifact_hash=%s old=%s new=%s",
                    source,
                    artifact_hash,
                    old_path.name,
                    new_path.name,
                )
                return {
                    "success": True,
                    "runtime_applied": True,
                    "artifact_hash": artifact_hash,
                    "artifact_path": new_artifact_path,
                    "configuration_version": self.om.runtime_config.get_version(),
                }
            except Exception as exc:
                logger.error(
                    "[MODEL_HOT_SWAP] event=MODEL_HOT_SWAP_FAILED source=%s error=%s",
                    source,
                    exc,
                )
                return {
                    "success": False,
                    "reason": str(exc),
                    "runtime_applied": False,
                    "current_model_unchanged": True,
                }
    
