"""ModelBundleStore — serving-artifact loading, verification and persistence.

P1 seam L9 (god-file decomposition, extraction 9 of the live-engine wave).
The model-bundle load/verify/persist cluster leaves
``application/live_engine.py`` verbatim (behavior-preserving extraction):

    _load_or_create_bundle            : bundle acquisition + caching entry
    _artifact_meta_coherence          : meta/model class-count coherence gate
    _expected_num_features_for_artifact
    _load_or_initialize_model_weights : safe torch load (or cold init)
    _load_scaler_artifacts            : scaler mean/std sidecar load
    _save_model_weights_atomic        : temp-file + os.replace persistence

State ownership: ``_bundle`` / ``_bundle_lock`` remain at the composition
root (LiveEngine) — they are true cross-service coordination state read by
inference/hot-swap/retrain. The methods are called UNBOUND with the engine
as the state surface (``ModelBundleStore._x(engine, ...)``) so the repo's
established harness/patch contracts (instance monkeypatching in
hot_swap_governance / debug_snapshot tests) keep working unchanged.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import torch

from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.observability.logging import get_logger

if TYPE_CHECKING:  # import-cycle breaker: value imports stay inside _engine_types()
    from nexus_scalp.application.live_engine import ModelBundle, ScalerBundle

logger = get_logger("nexus_scalp.application.live.model_bundle_store")


def _engine_types():
    """Late-bind engine value types (import-cycle breaker)."""
    from nexus_scalp.application.live_engine import ModelBundle, ScalerBundle

    return ModelBundle, ScalerBundle


class ModelBundleStore:
    """Bundle load/verify/persist operations (composition root: LiveEngine).

    UNBOUND-DELEGATION CONTRACT: methods are invoked as
    ``ModelBundleStore.method(engine, ...)`` — ``self`` IS the LiveEngine.
    Structural declaration below (mypy only).
    """

    if TYPE_CHECKING:
        FEATURE_DIM: int
        _declared_contract_dim_for_path: Any
        _declared_head_classes_for_path: Any
        _bundle_lock: Any

    def __init__(self, om: Any) -> None:
        # Composition root pattern: the engine IS the state surface; the
        # methods are also invoked UNBOUND with the engine as the first
        # argument (see module docstring), so no per-instance state here.
        self.om = om

    def _load_or_create_bundle(self, model_path: Path, force_fresh: bool) -> ModelBundle:
        # P1 ARTIFACT TRUST: verify the EXACT on-disk artifact against its
        # integrity metadata BEFORE the weights become the serving model.
        # force_fresh cold-start skips this (nothing on disk to verify yet).
        if not force_fresh:
            from nexus_scalp.model_lifecycle.load_integrity import (
                ArtifactIntegrityError,
                verify_artifact_integrity,
            )

            allow_legacy = bool(getattr(self, "allow_legacy_unverified_artifacts", False))
            try:
                verify_artifact_integrity(model_path, allow_legacy_unverified=allow_legacy)
            except ArtifactIntegrityError as integ_err:
                logger.critical(
                    "[MODEL_LOAD_REJECTED] event=ARTIFACT_INTEGRITY_FAIL "
                    "status=%s reason=%s artifact=%s",
                    integ_err.verdict.status.value,
                    integ_err.verdict.reason,
                    integ_err.verdict.artifact,
                )
                raise
        model = self._load_or_initialize_model_weights(
            model_path=model_path, force_fresh=force_fresh
        )
        scaler = self._load_scaler_artifacts(model_path=model_path)
        _MB, _ = _engine_types()
        return _MB(model=model, scaler=scaler, artifact_path=model_path)

    @staticmethod
    def _artifact_meta_coherence(model_path: Path) -> dict[str, Any]:
        """AGENT-10: metadata/tensor coherence + schema-identity verdict.

        Reads model.meta.json (when present) and the serialized tensors and
        verifies:
          * artifact head width == meta num_classes/model_head_classes
            (the 4-head + 3-meta P0 incoherence class is rejected here);
          * artifact input width == meta feature dimension (BUG-141 class);
          * meta feature_schema_id is a REGISTERED schema id — dimension
            equality alone is not identity (family semantics, 50/60/70D).
        Returns {"ok": bool, "reason": str, ...diagnostic fields}. Missing
        meta is NOT an error here (cold-start bundles carry no meta; the
        width gate downstream still applies) — coherence is enforced only
        on the fields that EXIST.
        """
        import json as _json

        verdict: dict[str, Any] = {
            "ok": True,
            "reason": "",
            "path": str(model_path),
        }
        try:
            meta_path = Path(model_path).with_suffix(".meta.json")
            if not meta_path.exists():
                verdict["reason"] = "NO_META"
                return verdict
            meta = _json.loads(meta_path.read_text(encoding="utf-8"))
            state = torch.load(model_path, map_location="cpu", weights_only=True)
            if not isinstance(state, dict):
                verdict.update(ok=False, reason="STATE_DICT_UNREADABLE")
                return verdict
            ip = state.get("input_projection.weight")
            cls = state.get("classifier.weight")
            if ip is None or cls is None or not hasattr(ip, "shape") or not hasattr(cls, "shape"):
                verdict.update(ok=False, reason="MISSING_CORE_TENSORS")
                return verdict
            artifact_head = int(cls.shape[0])
            artifact_dim = int(ip.shape[1])
            meta_head = meta.get("model_head_classes", meta.get("num_classes"))
            meta_dim = meta.get("feature_schema_dimension", meta.get("num_features"))
            verdict.update(
                artifact_head=artifact_head,
                artifact_dim=artifact_dim,
                meta_head=meta_head,
                meta_dim=meta_dim,
            )
            if meta_head is not None and int(meta_head) != artifact_head:
                verdict.update(ok=False, reason="HEAD_META_CLASS_MISMATCH")
                return verdict
            if meta_dim is not None and int(meta_dim) != artifact_dim:
                verdict.update(ok=False, reason="DIMENSION_META_MISMATCH")
                return verdict
            schema_id = str(meta.get("feature_schema_id", "") or "")
            if schema_id:
                from nexus_scalp.features.schema import FEATURE_SCHEMAS

                if not FEATURE_SCHEMAS.is_registered(schema_id):
                    verdict.update(ok=False, reason="UNREGISTERED_SCHEMA_ID")
                    return verdict
                resolved = FEATURE_SCHEMAS.resolve(schema_id)
                if resolved.dimension != artifact_dim:
                    verdict.update(ok=False, reason="SCHEMA_DIMENSION_MISMATCH")
                    return verdict
            verdict["reason"] = "COHERENT"
            return verdict
        except Exception as exc:  # unreadable artifact => refuse loudly
            verdict.update(ok=False, reason="COHERENCE_PROBE_FAILED", detail=str(exc))
            return verdict

    def _expected_num_features_for_artifact(self, model_path: Path) -> int:
        """Infer expected input width from the on-disk artifact, falling back to class default.

        When the checkpoint exists, its ``input_projection.weight.shape[1]`` is
        the source of truth (covers 50D + 70D). On cold-start (no file) the
        class ``FEATURE_DIM`` is kept so first-time users still bootstrap 50D.
        """
        with contextlib.suppress(Exception):
            if model_path.exists():
                probe = torch.load(model_path, map_location="cpu", weights_only=True)
                w = probe.get("input_projection.weight") if isinstance(probe, dict) else None
                if w is not None and hasattr(w, "shape") and len(w.shape) == 2:
                    return int(w.shape[1])
        # BUG-125 regression: tests call via LiveEngine._expected_num_features_for_artifact(None, path)
        # (unbound with self=None on macOS). Handle None gracefully.
        if self is None:
            from nexus_scalp.application.live_engine import LiveEngine

            return int(LiveEngine.FEATURE_DIM)
        return int(self.__class__.FEATURE_DIM)

    def _load_or_initialize_model_weights(self, model_path: Path, force_fresh: bool) -> ScalpNet:
        """Loads model.pt if present, validating against the artifact's own declared width.

        BUG-125: the width gate now validates against the checkpoint's own
        declared tensor width (artifact-driven contract selection) instead of
        the process-wide 50D default.
        """
        if force_fresh:
            # BUG-141: seed the width the PATH's declared contract demands
            # (meta/scaler/checkpoint), not the process-wide class default -
            # force_fresh must never mint a 50D file into a declared-70D path.
            expected_dim = self._declared_contract_dim_for_path(model_path) or int(
                self.__class__.FEATURE_DIM
            )
        else:
            expected_dim = self._expected_num_features_for_artifact(model_path)
        # BUG-243: mint at the bundle's DECLARED head width, not hardcoded 4.
        declared_head = self._declared_head_classes_for_path(model_path.with_suffix(".meta.json"))
        model = ScalpNet(num_features=expected_dim, num_classes=declared_head)
        model.eval()

        if model_path.exists() and not force_fresh:
            # P2 torch.load guard: the SERVING load path must never
            # deserialize arbitrary pickle objects (hard invariant).
            state_dict = torch.load(model_path, map_location="cpu", weights_only=True)

            expected = model.input_projection.weight.shape
            loaded = state_dict.get("input_projection.weight", torch.empty(0)).shape
            if loaded != expected:
                backup_path = model_path.with_suffix(".pt.corrupt")
                logger.critical(
                    "Checkpoint dimension mismatch; quarantining",
                    expected=str(expected),
                    loaded=str(loaded),
                    backup=str(backup_path),
                )
                with contextlib.suppress(Exception):
                    model_path.rename(backup_path)
                raise RuntimeError(
                    f"Checkpoint dimension mismatch: expected {expected}, got {loaded}"
                )

            model.load_state_dict(state_dict)
            logger.info("Loaded model weights", path=str(model_path), expected_dim=expected_dim)
            # P0-2 ARTIFACT TRUST ANCHOR (2026-09-09): the bundle's own
            # manifest is self-referential — it proves the WEIGHTS match the
            # SIDE-CAR, not that they are the GOVERNED champion. Cross-check
            # the artifact fingerprint against the lifecycle registry's
            # CHAMPION row; a mismatch or unreadable fingerprint fails
            # closed (never serves silently-drifted bytes — the Appendix-R
            # defect class). No operator override here: promotion/recovery
            # is the governance path that updates the registry.
            _anch = getattr(self, "_verify_champion_registry_binding", None)
            if _anch is None:
                # Minimal state surface (direct ModelBundleStore construction):
                # run the helper directly with this object as the state surface.
                ModelBundleStore._verify_champion_registry_binding(
                    self, model_path, actual_bytes_hash=None
                )
            else:
                _anch(model_path, actual_bytes_hash=None)
            return model

        logger.info(
            "Initializing fresh model weights", path=str(model_path), expected_dim=expected_dim
        )
        self._save_model_weights_atomic(model, model_path)
        return model

    def _verify_champion_registry_binding(
        self, model_path: Path, actual_bytes_hash: str | None
    ) -> None:
        """P0-2 ARTIFACT TRUST ANCHOR: cross-check the serving artifact against
        the lifecycle registry's governed CHAMPION row.

        Behavior:
          * registry row exists + carries a fingerprint -> the on-disk artifact
            sha256 (16-hex prefix, same scheme as fingerprint_artifact) MUST
            match; mismatch => ArtifactIntegrityError (fail closed, CRITICAL).
          * no champion row / empty fingerprint / registry unavailable => the
            check is INERT (logged) so cold-start and non-champion artifact
            paths keep working; the self-referential bundle verification above
            still applies. This is NOT an override — governed promotion is the
            only writer of CHAMPION rows.
        """
        import hashlib as _hashlib
        import sqlite3 as _sqlite3

        from nexus_scalp.model_lifecycle.load_integrity import (
            ArtifactIntegrityError,
            ArtifactIntegrityStatus,
            IntegrityVerdict,
        )

        try:
            from nexus_scalp.model_lifecycle.models import ModelStatus
        except Exception:
            return
        try:
            om = getattr(self, "om", None)
            audit = getattr(om, "audit", None) if om is not None else None
            if audit is None:
                audit = getattr(self, "audit", None)
            if audit is None or not getattr(audit, "_is_sqlite", False):
                logger.info("[TRUST_ANCHOR] event=REGISTRY_CHECK_INERT reason=no_sqlite_audit")
                return
            db_path = getattr(audit, "_db_path", None)
            if not db_path:
                logger.info("[TRUST_ANCHOR] event=REGISTRY_CHECK_INERT reason=no_db_path")
                return
            conn = _sqlite3.connect(db_path, timeout=5.0)
            try:
                row = conn.execute(
                    "SELECT model_id, artifact_fingerprint FROM experience_model_registry "
                    "WHERE lifecycle_status=? ORDER BY registered_at DESC LIMIT 1;",
                    (ModelStatus.CHAMPION.value,),
                ).fetchone()
            finally:
                conn.close()
            if not row:
                logger.info("[TRUST_ANCHOR] event=REGISTRY_CHECK_INERT reason=no_champion_row")
                return
            champion_id = str(row[0] or "")
            governed_fp = str(row[1] or "").strip().lower()
            if not governed_fp:
                logger.info(
                    "[TRUST_ANCHOR] event=REGISTRY_CHECK_INERT reason=champion_row_has_no_fingerprint"
                )
                return
            h = _hashlib.sha256()
            with open(model_path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            actual_fp = h.hexdigest()[:16]
            if actual_fp != governed_fp:
                logger.critical(
                    "[TRUST_ANCHOR] event=CHAMPION_BINDING_MISMATCH "
                    "serving_sha16=%s governed_sha16=%s champion_row=%s "
                    "(on-disk artifact is NOT the governed champion; refusing load)",
                    actual_fp,
                    governed_fp,
                    champion_id,
                )
                raise ArtifactIntegrityError(
                    IntegrityVerdict(
                        status=ArtifactIntegrityStatus.HASH_MISMATCH,
                        reason=(
                            f"serving artifact sha16 {actual_fp} != governed CHAMPION "
                            f"{governed_fp} (registry row {champion_id})"
                        ),
                        artifact=model_path.name,
                        expected_sha256=governed_fp,
                        actual_sha256=actual_fp,
                    )
                )
            logger.info(
                "[TRUST_ANCHOR] event=CHAMPION_BINDING_VERIFIED champion_row=%s sha16=%s",
                champion_id,
                actual_fp,
            )
        except ArtifactIntegrityError:
            raise
        except Exception as exc:  # inert on registry/infra failure (not artifact failure)
            logger.info(
                "[TRUST_ANCHOR] event=REGISTRY_CHECK_INERT reason=registry_error detail=%s",
                str(exc)[:200],
            )

    def _load_scaler_artifacts(self, model_path: Path) -> ScalerBundle:
        scaler_path = model_path.with_suffix(".scaler.npz")
        if not scaler_path.exists():
            logger.info("Scaler artifact missing (cold-start acceptable)", path=str(scaler_path))
            _, _SB = _engine_types()
            return _SB(mean=None, std=None)
        try:
            data = np.load(scaler_path)
            mean = np.asarray(data["mean"], dtype=np.float32).reshape(-1)
            std = np.asarray(data["std"], dtype=np.float32).reshape(-1)

            # BUG-125: scaler width must match the MODEL's declared width
            expected_dim = self._expected_num_features_for_artifact(model_path)
            if mean.shape[0] != expected_dim or std.shape[0] != expected_dim:
                raise RuntimeError(
                    f"Scaler dim invalid: mean{mean.shape} std{std.shape} "
                    f"expected ({expected_dim},) for artifact {model_path.name}"
                )

            logger.info(
                "Loaded scaler artifacts successfully",
                path=str(scaler_path),
                mean_shape=mean.shape,
                std_shape=std.shape,
            )
            # OBS-PERF-RESILIENCE: a degenerate std (zero/negative/non-finite)
            # makes the bundle NOT-ready (transform passes features through
            # unchanged instead of dividing by zero). Surface it loudly at
            # load time — the degraded state must be visible, never silent.
            degenerate = int(np.sum(~(np.isfinite(std) & (std > 0.0))))
            if degenerate:
                logger.warning(
                    "[SCALER_DEGRADED] event=DEGENERATE_STD scaler_not_ready_features_passthrough",
                    path=str(scaler_path),
                    degenerate_columns=degenerate,
                    total_columns=int(std.shape[0]),
                )
            _, _SB = _engine_types()
            return _SB(mean=mean, std=std)

        except Exception as err:
            # RUNTIME RESILIENCE (Agent-7 failure injection): a scaler
            # sidecar that EXISTS but fails to load/validate is CORRUPT —
            # that is a declared-artifact defect, not a cold start. The
            # bundle is stamped corrupt=True so the inference path can
            # refuse to serve raw unscaled features (T24 fail-closed).
            logger.error(
                "[SCALER_CORRUPT] event=SCALER_LOAD_FAILED sidecar_exists_but_unreadable "
                "inference_will_be_blocked_for_this_bundle",
                error=str(err),
                path=str(scaler_path),
            )
            _, _SB = _engine_types()
            return _SB(mean=None, std=None, corrupt=True)

    def _save_model_weights_atomic(self, model: ScalpNet, model_path: Path) -> bool:
        """Saves current PyTorch model weights state_dict atomically to disk with thread lock and logging.

        BUG-141 guard: refuses to persist weights whose input width contradicts
        the target path's DECLARED contract (meta/scaler/checkpoint). A
        desynced runtime state must never silently overwrite a bundle with a
        mismatched-dimension artifact (the 2026-08-27 70d_liquidity clobber
        class). Mismatch -> CRITICAL log + no write (artifact preserved).

        Returns True on successful persist, False on BUG-141 refusal or I/O
        failure so callers can refuse the END-TO-END persist (no bundle swap,
        no provenance, explicit ASYNC_RETRAIN_REFUSED) instead of diverging
        memory==disk identity.
        """
        try:
            model_width = int(model.input_projection.weight.shape[1])
            declared = self._declared_contract_dim_for_path(model_path)
            if declared is not None and declared != model_width:
                logger.critical(
                    "[BUG141_GUARD] event=ARTIFACT_WIDTH_CONTRACT_REFUSED",
                    path=str(model_path),
                    model_width=model_width,
                    declared_dim=declared,
                )
                return False
        except Exception as guard_err:  # never block the save on guard failure
            logger.warning(
                "[BUG141_GUARD] contract probe failed (save proceeds)",
                error=str(guard_err),
                path=str(model_path),
            )
        with self._bundle_lock:
            try:
                model_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = model_path.with_suffix(".pt.tmp")

                # Detach state dict to CPU before saving for HFT thread safety
                cpu_state = {k: v.detach().cpu() for k, v in model.state_dict().items()}
                torch.save(cpu_state, tmp)
                tmp.replace(model_path)

                logger.info(
                    "Saved PyTorch model weights artifact atomically to disk",
                    path=str(model_path),
                    tensor_layers=len(cpu_state),
                )
            except Exception as err:
                logger.error(
                    "Failed to save atomic model weights to disk",
                    error=str(err),
                    path=str(model_path),
                )
                return False
        return True
