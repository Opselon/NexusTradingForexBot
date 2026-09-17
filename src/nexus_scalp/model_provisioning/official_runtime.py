"""Real staging verification. Acquisition is not governance approval."""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from packaging.specifiers import SpecifierSet

from nexus_scalp.model_provisioning.official_contract import require
from nexus_scalp.model_provisioning.official_staging import read_json, verify_file


def verify_runtime(bundle_dir: Path) -> None:
    from nexus_scalp.model_provisioning.official import OfficialBundleError, verify_bundle_manifest

    manifest = verify_bundle_manifest(read_json(bundle_dir / "manifest.json"))
    for name, entry in manifest["files"].items():
        verify_file(bundle_dir / name, entry)
    try:
        import torch
    except ImportError as exc:
        raise OfficialBundleError(
            "VERIFICATION_PENDING", "PyTorch unavailable; staging not verified"
        ) from exc
    consumer = manifest["consumer"]
    current_platform = f"{sys.platform}-{platform.machine().lower()}"
    require(
        platform.python_version() in SpecifierSet(consumer["python"])
        and str(torch.__version__) in SpecifierSet(consumer["pytorch"])
        and current_platform in consumer["platforms"],
        "RUNTIME_UNSUPPORTED",
        "current Python/PyTorch/platform not in tested consumer constraints",
    )
    try:
        import numpy as np

        from nexus_scalp.application.live.model_bundle_store import ModelBundleStore
        from nexus_scalp.model_lifecycle.integrity import (
            check_model_behavioral_health,
            detect_untrained_fresh_init,
        )
        from nexus_scalp.model_lifecycle.load_integrity import verify_artifact_integrity

        meta = read_json(bundle_dir / "model.meta.json")
        expected = {
            k: manifest[k]
            for k in (
                "architecture",
                "architecture_version",
                "architecture_parameters",
                "input_layout",
                "class_labels",
                "feature_schema_id",
                "feature_schema_hash",
                "symbol",
                "timeframe",
            )
        }
        expected.update(
            feature_schema_dimension=manifest["dimension"], num_classes=manifest["class_count"]
        )
        for key, value in expected.items():
            require(
                meta.get(key) == value and type(meta.get(key)) is type(value),
                "ARTIFACT_INTEGRITY_FAILED",
                f"metadata {key} differs from signed contract",
            )
        model_path = bundle_dir / "model.pt"
        verify_artifact_integrity(model_path, allow_legacy_unverified=False)
        with np.load(bundle_dir / "model.scaler.npz", allow_pickle=False) as scaler:
            for key in ("mean", "std"):
                value = scaler[key]
                require(
                    value.shape == (manifest["dimension"],) and bool(np.isfinite(value).all()),
                    "ARTIFACT_INTEGRITY_FAILED",
                    f"scaler {key} shape/nonfinite",
                )
            require(
                bool((scaler["std"] > 0).all()),
                "ARTIFACT_INTEGRITY_FAILED",
                "scaler std must be positive",
            )

        class StagingLoader(ModelBundleStore):
            @staticmethod
            def _declared_head_classes_for_path(path: Path) -> int:
                return int(read_json(path)["num_classes"])

        # Use the actual runtime weights loader, including strict state_dict and
        # its registry hook. No registry is created/promoted by acquisition.
        # The live engine repeats its real champion binding at activation.
        with torch.random.fork_rng(devices=[]):
            model = StagingLoader(None)._load_or_initialize_model_weights(
                model_path, force_fresh=False
            )
            for value in model.state_dict().values():
                require(
                    bool(torch.isfinite(value).all()),
                    "ARTIFACT_INTEGRITY_FAILED",
                    "nonfinite weights",
                )
            fresh, detail = detect_untrained_fresh_init(model_path, manifest["dimension"])
            require(
                not fresh and not detail.startswith("CANARY_ERROR"),
                "ARTIFACT_INTEGRITY_FAILED",
                f"fresh-init gate: {detail}",
            )
            healthy, detail, _ = check_model_behavioral_health(model_path, manifest["dimension"])
            require(healthy, "ARTIFACT_INTEGRITY_FAILED", f"behavioral gate: {detail}")
    except OfficialBundleError:
        raise
    except ImportError as exc:
        raise OfficialBundleError(
            "VERIFICATION_PENDING", f"runtime verification dependency absent: {exc}"
        ) from exc
    except Exception as exc:
        raise OfficialBundleError("ARTIFACT_INTEGRITY_FAILED", str(exc)[:300]) from exc
