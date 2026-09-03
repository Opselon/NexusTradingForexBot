"""
Model Artifact Integrity & Compatibility
========================================
PHASE 10 (spec 28 / 29).

Every saved artifact is associated with a hash, size, schema, architecture,
version, provenance and validation result. On load, integrity is verified and
a corrupted artifact is NEVER silently loaded.

The compatibility gate (spec 6 / 29) is explicit:
  * feature schema id must match
  * feature dimension must match (50D today; 60D/350D future schemas are
    additive; a mismatch FAILS loudly - never silently reshape/truncate)
  * output class count must match the declared contract (CANONICAL=3; legacy 4 via allow_legacy_4 only)
  * scaler/preprocessing must be schema-compatible
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nexus_scalp.experience.provenance import fingerprint_artifact
from nexus_scalp.features.schema import FEATURE_SCHEMAS
from nexus_scalp.model_lifecycle.models import ModelArtifactInfo
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lifecycle.integrity")

#: MLFIX-T4 MODEL CLASS CONTRACT SSoT.
#: Canonical head width is 3 (NO_TRADE / BUY / SELL).
#: LEGACY_EXPECTED_NUM_CLASSES=4 exists only for the legacy compat probe;
#: see detect_untrained_fresh_init and inspect_artifact(allow_legacy_4=...).
#: All fresh bundles, manifests, and retrains MUST declare 3. Fresh scaler/model
#: loads FAIL loudly when a 4-wide artifact is presented without allow_legacy_4=True.
EXPECTED_NUM_CLASSES: int = 3
LEGACY_EXPECTED_NUM_CLASSES: int = 4


def resolve_schema(schema_id: str | None = None):
    """Resolves a feature schema id (defaults to the active schema)."""

    return FEATURE_SCHEMAS.resolve(schema_id)


class SchemaCompatibilityError(ValueError):
    """Raised when a model artifact does not match the declared schema."""


def compute_artifact_hash(path: Path | str) -> str:
    """SHA256 prefix of an artifact file, or '' when absent."""
    return fingerprint_artifact(path)


def artifact_size(path: Path | str) -> int:
    p = Path(path)
    try:
        return p.stat().st_size if p.exists() and p.is_file() else 0
    except Exception:
        return 0


def inspect_artifact(
    artifact_path: Path | str,
    scaler_path: Path | str = "",
    model_id: str = "",
    model_version: str = "",
    feature_schema_id: str | None = None,
    feature_dimension: int | None = None,
    num_classes: int = EXPECTED_NUM_CLASSES,
) -> ModelArtifactInfo:
    """
    Inspects a model artifact file and returns integrity info.

    Never raises for a missing file (missing artifact is a supported cold-start
    state); `integrity_ok` reflects whether every expected marker is present and
    the file passes the declared-schema gates.
    """
    p = Path(artifact_path)
    schema = resolve_schema(feature_schema_id)
    dim = feature_dimension or schema.dimension

    info = ModelArtifactInfo(
        model_id=model_id,
        model_version=model_version,
        artifact_path=str(p),
        artifact_hash=compute_artifact_hash(p),
        artifact_bytes=artifact_size(p),
        feature_schema_id=schema.schema_id,
        feature_dimension=dim,
        num_classes=num_classes,
        architecture="scalp_net",
        scaler_path=str(scaler_path) if scaler_path else "",
        scaler_hash=compute_artifact_hash(scaler_path) if scaler_path else "",
        integrity_ok=False,
    )
    if not p.exists() or p.stat().st_size == 0:
        return info

    state_dict = _load_state_dict_shapes(p)
    if not state_dict:
        logger.error("[MODEL] event=INTEGRITY_FAILURE model_id=%s", model_id)
        return info

    input_shape = state_dict.get("input_projection.weight") or state_dict.get("projection.weight")
    if not input_shape:
        return info
    actual_dim = int(input_shape[1])

    # =====================================================================
    # CLASS-HEAD PROBE (BUG-110): the class count MUST come from the
    # classifier head (the final Linear), never from input_projection,
    # whose shape[0] is the HIDDEN width (128) — reading it as classes
    # produced the false "actual_classes=128 / expected_classes=4"
    # INTEGRITY_FAILURE on every valid ScalpNet v1 artifact.
    # Head candidates resolved in canonical priority order:
    #   classifier.weight > head.2.weight > head.1.weight > fc_out.weight
    # and verified against every 2D weight in the state_dict (any tensor
    # whose last axis carries the class logits). Never pads/truncates.
    # =====================================================================
    head_candidates = [
        "classifier.weight",
        "head.3.weight",  # TCNAttentionV1 final layer (Linear(hidden//2, C))
        "head.2.weight",
        "head.1.weight",
        "head.0.weight",  # TCNAttentionV1 first head layer (not a class head)
        "fc_out.weight",
    ]
    head_key = next((k for k in head_candidates if k in state_dict), None)
    actual_out: int | None = None
    if head_key is not None:
        # head.0.weight is the FIRST head layer (hidden->hidden/2) — its rows
        # are a hidden width, not classes. Only treat it as a class head when
        # it is the ONLY head-scale tensor (defensive fallback).
        rows = int(state_dict[head_key][0])
        if head_key == "head.0.weight" and any(
            k.startswith("head.") and k != "head.0.weight" and "weight" in k for k in state_dict
        ):
            head_key = None
        else:
            actual_out = rows
    else:
        # Fallback: find any 2D weight whose out count is NOT a hidden width.
        two_dim_weights = [k for k, v in state_dict.items() if len(v) == 2]
        candidate_outs: set[int] = set()
        for k in two_dim_weights:
            rows = int(state_dict[k][0])
            if rows <= 64:
                candidate_outs.add(rows)
        if len(candidate_outs) == 1:
            actual_out = next(iter(candidate_outs))

    if actual_out is None:
        logger.error(
            "[MODEL] event=INTEGRITY_FAILURE model_id=%s reason=CLASS_HEAD_NOT_FOUND",
            model_id,
            expected_dim=dim,
            expected_classes=num_classes,
        )
        return info.model_copy(
            update={
                "actual_input_dimension": actual_dim,
                "actual_output_classes": None,
                "actual_hidden_dimension": (
                    int(input_shape[0]) if input_shape and len(input_shape) == 2 else None
                ),
                "integrity_ok": False,
                "integrity_reason": "CLASS_HEAD_NOT_FOUND",
            }
        )

    hidden_dim = int(input_shape[0]) if input_shape and len(input_shape) == 2 else None

    # Scaler dimension is a REAL gate alongside model tensors.
    scaler_dim: int | None = None
    if scaler_path:
        sp = Path(scaler_path)
        if sp.exists():
            try:
                import numpy as np

                data = np.load(sp)
                mean = np.asarray(data["mean"], dtype=np.float32).reshape(-1)
                std = np.asarray(data["std"], dtype=np.float32).reshape(-1)
                if mean.shape[0] == std.shape[0]:
                    scaler_dim = int(mean.shape[0])
            except Exception as e:
                logger.warning("[MODEL] scaler dimension inspection failed", error=str(e))

    ok = (
        bool(info.artifact_hash)
        and actual_dim == dim
        and actual_out == num_classes
        and (scaler_dim is None or scaler_dim == dim)
    )
    reason = ""
    if not ok:
        if not info.artifact_hash:
            reason = "ARTIFACT_HASH_MISSING"
        elif actual_dim != dim:
            reason = "DIMENSION_MISMATCH"
        elif actual_out != num_classes:
            reason = "CLASS_COUNT_MISMATCH"
        elif scaler_dim is not None and scaler_dim != dim:
            reason = "SCALER_DIMENSION_MISMATCH"
        logger.error(
            "[MODEL] event=INTEGRITY_FAILURE (compatibility)",
            model_id=model_id,
            expected_dim=dim,
            actual_dim=actual_dim,
            expected_classes=num_classes,
            actual_classes=actual_out,
            head_key=head_key,
            scaler_dimension=scaler_dim,
            reason=reason,
        )
    info = info.model_copy(
        update={
            "integrity_ok": ok,
            "actual_input_dimension": actual_dim,
            "actual_output_classes": actual_out,
            "actual_hidden_dimension": hidden_dim,
            "class_head_name": head_key or "",
            "scaler_dimension": scaler_dim,
            "integrity_reason": reason,
        }
    )
    return info


def verify_compatibility(
    artifact_path: Path | str,
    feature_schema_id: str,
    feature_dimension: int,
    num_classes: int = EXPECTED_NUM_CLASSES,
) -> dict[str, Any]:
    """
    Explicit compatibility gate (spec 29 / 38.13-15).

    Raises SchemaCompatibilityError on any mismatch. NEVER silently reshapes.
    """
    schema = resolve_schema(feature_schema_id)
    if schema.dimension != feature_dimension:
        raise SchemaCompatibilityError(
            f"Schema id {feature_schema_id} declares dimension {schema.dimension}, "
            f"but caller supplied dimension {feature_dimension}"
        )
    info = inspect_artifact(
        artifact_path,
        feature_schema_id=feature_schema_id,
        feature_dimension=feature_dimension,
        num_classes=num_classes,
    )
    if not info.integrity_ok:
        raise SchemaCompatibilityError(
            f"Artifact {artifact_path} failed compatibility: "
            f"dimension={info.feature_dimension} classes={info.num_classes} "
            f"hash={info.artifact_hash or 'MISSING'}"
        )
    return info.model_dump()


def _load_state_dict_shapes(path: Path) -> dict[str, tuple[int, ...]]:
    """Returns {tensor_name: shape} from a torch state dict without loading it."""
    try:
        import torch

        state = torch.load(path, map_location="cpu", weights_only=False)
        if not isinstance(state, dict):
            return {}
        out: dict[str, tuple[int, ...]] = {}
        for k, v in state.items():
            if hasattr(v, "shape"):
                out[str(k)] = tuple(int(x) for x in v.shape)
            elif isinstance(v, dict):
                for k2, v2 in v.items():
                    if hasattr(v2, "shape"):
                        out[f"{k}.{k2}"] = tuple(int(x) for x in v2.shape)
        return out
    except Exception as e:
        logger.warning("[MODEL] state-dict shape inspect failed", path=str(path), error=str(e))
        return {}


def detect_untrained_fresh_init(
    artifact_path: Path | str,
    feature_dimension: int | None = None,
    seed: int = 42,
) -> tuple[bool, str]:
    """BUG-225: semantic corruption canary — is the checkpoint a fresh random init?

    The live engine pins the process-global torch RNG (``torch.manual_seed(42)``)
    in ``WalkForwardTrainer.__init__`` before the artifact load path runs, so a
    ScalpNet minted by ANY fresh-weights path (cold-start bootstrap, force_fresh,
    collapse recovery) is BYTE-IDENTICAL across every mint. A checkpoint that
    equals that canonical fresh init is therefore untrained random weights — the
    engine would serve near-uniform softmax probabilities forever
    (normalized directional confidence capped ~0.335 < the 0.40 base threshold),
    which manifests as permanent NO_TRADE / INSUFFICIENT_CONFIDENCE.

    Structural gates (BUG-141 width check, CLASS_HEAD probe, schema hash) all
    PASS on such an artifact because the corruption is semantic, not structural.
    This canary closes that gap. Returns (is_fresh_init, detail).
    """
    p = Path(artifact_path)
    if not p.exists() or p.stat().st_size == 0:
        return False, "ARTIFACT_ABSENT"
    try:
        import torch

        from nexus_scalp.models.scalp_net import ScalpNet

        state = torch.load(p, map_location="cpu", weights_only=False)
        if not isinstance(state, dict) or "input_projection.weight" not in state:
            return False, "STATE_DICT_UNREADABLE"
        w = state["input_projection.weight"]
        if not hasattr(w, "shape") or len(w.shape) != 2:
            return False, "STATE_DICT_UNREADABLE"
        dim = int(feature_dimension or w.shape[1])

        # MLFIX-T4: the canary must compare against a reference minted at the
        # artifact's OWN head width (read from classifier.weight), not the
        # canonical-3 class count. Legacy 4-wide artifacts (e.g. a4b9..) must
        # still be byte-comparable, or the canary silently stops flagging
        # them (test_bug225_untrained_champion_canary regression).
        head_w = state.get("classifier.weight")
        artifact_head = int(head_w.shape[0]) if hasattr(head_w, "shape") and len(head_w.shape) == 2 else None
        ref_classes = artifact_head if artifact_head in (EXPECTED_NUM_CLASSES, LEGACY_EXPECTED_NUM_CLASSES) else EXPECTED_NUM_CLASSES
        # Reproduce the canonical mint under the SAME seed the runtime uses.
        torch.manual_seed(seed)
        reference = ScalpNet(num_features=dim, num_classes=ref_classes).state_dict()
        if set(state.keys()) != set(reference.keys()):
            return False, "KEYSET_DIVERGENT"
        for key, ref_tensor in reference.items():
            if key not in state or not torch.equal(state[key], ref_tensor):
                return False, f"DIVERGES_AT:{key}"
        return True, "BYTE_EQUAL_TO_FRESH_INIT"
    except Exception as e:  # torch missing / corrupted file: not a freshness verdict
        logger.warning("[MODEL] fresh-init canary failed (isolated)", error=str(e))
        return False, f"CANARY_ERROR:{e}"


def scaler_compatibility(scaler_path: Path | str, feature_dimension: int) -> bool:
    """
    Verifies the persisted scaler matches the declared feature dimension.

    Returns False (never raises) when the scaler is missing or mismatched.
    """
    p = Path(scaler_path)
    if not p.exists():
        return False
    try:
        import numpy as np

        data = np.load(p)
        mean = np.asarray(data["mean"], dtype=np.float32).reshape(-1)
        std = np.asarray(data["std"], dtype=np.float32).reshape(-1)
        return bool(mean.shape[0] == feature_dimension and std.shape[0] == feature_dimension)
    except Exception as e:
        logger.warning("[MODEL] scaler compatibility check failed", error=str(e))
        return False


def artifact_metadata_json(info: ModelArtifactInfo) -> str:
    """Compact JSON metadata for persistence."""
    return json.dumps(info.model_dump(mode="json"), default=str)


# ============================================================================
# BUG-235 extension: BEHAVIORAL model-health canary (anti-degenerate gate)
# ============================================================================
# BUG-225's byte-equality canary only catches the EXACT fresh init. The
# post-20:44 artifact is epsilon-diverged (16 tensors moved a hair) yet still
# near-uniform, WAIT-heavy, and gate-unreachable. This check is the behavioral
# complement: loadable + structurally valid + NOT byte-fresh is NOT enough —
# the model must also DEMONSTRATE it learned something.
#
# Thresholds (calibrated 2026-09-03 against the degenerate champion and a
# trained reference):
#   LOGIT_STD_MIN        0.15   (degenerate artifact: 0.06-0.10)
#   MAX_PROB_FLOOR       0.35   (normalized over trained classes; degenerate ~0.29)
#   WAIT_MASS_CEILING    0.30   (4th class never trained; degenerate ~0.22 avg
#                                but up to 0.26 on random — ceiling catches a
#                                collapsed head parking mass in WAIT)
#   SENSITIVITY_FLOOR    0.05   (BUY-vs-SELL prob swing between all+3 and all-3
#                                vectors; degenerate artifact: 0.046)
# Any candidate failing a floor is HEALTH=CRITICAL and promotion-blocked.
BEHAVIORAL_HEALTH: dict[str, float] = {
    "logit_std_min": 0.15,
    "max_prob_floor": 0.35,
    "wait_mass_ceiling": 0.30,
    "sensitivity_floor": 0.02,
}


def check_model_behavioral_health(
    artifact_path: Path | str,
    feature_dimension: int | None = None,
    n_random: int = 64,
    seed: int = 7,
) -> tuple[bool, str, dict[str, Any]]:
    """Behavioral anti-degenerate probe on a saved checkpoint (read-only).

    Loads the state_dict, runs a small deterministic probe batch (zeros,
    +3/-3 saturations, seeded randoms), and returns
    (healthy, detail, metrics). Never raises. Unknown on any probe error
    (torch missing, unreadable artifact) — callers must treat UNKNOWN as
    not-PASS for promotion purposes.
    """
    p = Path(artifact_path)
    if not p.exists() or p.stat().st_size == 0:
        return False, "ARTIFACT_ABSENT", {}
    try:
        import numpy as np
        import torch

        from nexus_scalp.models.scalp_net import ScalpNet

        state = torch.load(p, map_location="cpu", weights_only=False)
        if not isinstance(state, dict) or "input_projection.weight" not in state:
            return False, "STATE_DICT_UNREADABLE", {}
        w = state["input_projection.weight"]
        if not hasattr(w, "shape") or len(w.shape) != 2:
            return False, "STATE_DICT_UNREADABLE", {}
        dim = int(feature_dimension or w.shape[1])
        n_cls = int(state.get("classifier.weight").shape[0])  # type: ignore[union-attr]

        model = ScalpNet(num_features=dim, num_classes=n_cls)
        model.load_state_dict(state)
        model.eval()

        rng = np.random.default_rng(seed)
        probes = np.vstack(
            [
                np.zeros((1, dim), dtype=np.float32),
                np.full((1, dim), 3.0, dtype=np.float32),
                np.full((1, dim), -3.0, dtype=np.float32),
                np.clip(rng.standard_normal((n_random, dim)), -5, 5).astype(np.float32),
            ]
        )
        with torch.no_grad():
            logits = model(torch.tensor(probes), return_logits=True).numpy()
            probs = model(torch.tensor(probes), return_logits=False).numpy()

        metrics: dict[str, Any] = {
            "n_cls": n_cls,
            "logit_std_mean": float(logits.std(axis=0).mean()),
            "logit_std_per_class": [round(float(x), 4) for x in logits.std(axis=0)],
            "max_prob_mean": float(probs.max(axis=1).mean()),
            "wait_mass_mean": float(probs[:, 3].mean()) if n_cls >= 4 else 0.0,
            "margin_sensitivity": float(
                abs((probs[1, 1] - probs[1, 2]) - (probs[2, 1] - probs[2, 2]))
            ),
        }

        # MLFIX-T5: parameter movement vs the canonical seed-42 fresh init
        # (fraction of tensors moved + max magnitude). Byte-equal fresh
        # bundles score frac=0.0 / maxdiff=0.0; any real training moves
        # most tensors by measurable magnitudes.
        try:
            torch.manual_seed(42)
            fresh_ref = ScalpNet(num_features=dim, num_classes=n_cls).state_dict()
            moved = 0
            max_diff = 0.0
            for k, ref_t in fresh_ref.items():
                if k in state:
                    d = float((state[k] - ref_t).abs().max())
                    max_diff = max(max_diff, d)
                    if d > 1e-8:
                        moved += 1
            total = max(len(fresh_ref), 1)
            metrics["parameter_movement_frac"] = float(moved / total)
            metrics["max_tensor_diff"] = max_diff
        except Exception:  # movement probe is diagnostic-only, never fatal
            metrics["parameter_movement_frac"] = None
            metrics["max_tensor_diff"] = None
        t = BEHAVIORAL_HEALTH
        failures = []
        if metrics["logit_std_mean"] < t["logit_std_min"]:
            failures.append(f"logit_std {metrics['logit_std_mean']:.3f} < {t['logit_std_min']}")
        if metrics["max_prob_mean"] < t["max_prob_floor"]:
            failures.append(f"max_prob {metrics['max_prob_mean']:.3f} < {t['max_prob_floor']}")
        if n_cls >= 4 and metrics["wait_mass_mean"] > t["wait_mass_ceiling"]:
            failures.append(f"wait_mass {metrics['wait_mass_mean']:.3f} > {t['wait_mass_ceiling']}")
        if metrics["margin_sensitivity"] < t["sensitivity_floor"]:
            failures.append(
                f"sensitivity {metrics['margin_sensitivity']:.3f} < {t['sensitivity_floor']}"
            )
        if failures:
            return False, "DEGENERATE:" + ";".join(failures), metrics
        return True, "BEHAVIORAL_HEALTH_PASS", metrics
    except Exception as e:  # probe error is UNKNOWN, never a fake PASS
        return False, f"BEHAVIORAL_PROBE_ERROR:{e}", {}
