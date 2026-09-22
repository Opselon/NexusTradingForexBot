"""ML-PHASE1 STEP-7 fail-closed validator + STEP-8 experience identity tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CHAMPION = REPO / "artifacts" / "models" / "scalp" / "XAUUSD" / "70d_liquidity" / "model.pt"


@pytest.fixture()
def tmp_artifact(tmp_path: Path) -> Path:
    """A MINIMAL but contract-coherent artifact: 70-wide tensors + meta."""
    import numpy as np
    import torch

    from nexus_scalp.features.schema_contract import (
        canonical_feature_names,
        feature_schema_hash,
    )
    from nexus_scalp.model_generation.temporal_contract import CANONICAL_MAX_GAP_US

    names = list(canonical_feature_names())
    meta = {
        "num_features": 70,
        "num_classes": 3,
        "model_head_classes": 3,
        "feature_schema_id": "scalp_v3",
        "feature_schema_dimension": 70,
        "feature_columns": [f"feat_{i}" for i in range(70)],
        "canonical_feature_names": names,
        "feature_schema_hash": feature_schema_hash("scalp_v3"),
        "seq_len": 32,
        "max_gap_us": CANONICAL_MAX_GAP_US,
        "temporal_contract": {
            "version": "1.0.0",
            "seq_len": 32,
            "max_gap_us": CANONICAL_MAX_GAP_US,
            "purge_gap_bars": 15,
            "embargo_bars": 15,
        },
    }
    p = tmp_path / "model.pt"
    torch.save(
        {
            "input_projection.weight": torch.zeros(64, 70),
            "classifier.weight": torch.zeros(3, 64),
        },
        p,
    )
    (tmp_path / "model.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    np.savez(
        tmp_path / "model.scaler.npz",
        mean=np.zeros(70, dtype=np.float64),
        std=np.ones(70, dtype=np.float64),
    )
    return p


class TestServingContractValidator:
    """STEP-7: ONE authoritative validator, fail-closed on every dimension."""

    def test_accepts_coherent_artifact(self, tmp_artifact: Path) -> None:
        from nexus_scalp.model_lifecycle.serving_contract import (
            validate_serving_contract,
        )

        v = validate_serving_contract(tmp_artifact)
        assert v.ok, v.as_dict()
        assert v.reason == "SERVING_CONTRACT_OK"
        assert v.checks["dimension_ok"]
        assert v.checks["class_head_ok"]
        assert v.checks["schema_hash_ok"]
        assert v.checks["feature_order_ok"]
        assert v.checks["temporal_contract_ok"]
        assert v.checks["scaler_dimension_ok"]

    def test_rejects_dimension_mismatch(self, tmp_artifact: Path) -> None:
        import torch

        from nexus_scalp.model_lifecycle.serving_contract import validate_serving_contract

        p = tmp_artifact
        torch.save(
            {
                "input_projection.weight": torch.zeros(64, 50),  # 50, not 70
                "classifier.weight": torch.zeros(3, 64),
            },
            p,
        )
        v = validate_serving_contract(p)
        assert not v.ok
        assert v.reason == "DIMENSION_MISMATCH"

    def test_rejects_class_head_mismatch(self, tmp_artifact: Path) -> None:
        import torch

        from nexus_scalp.model_lifecycle.serving_contract import validate_serving_contract

        p = tmp_artifact
        torch.save(
            {
                "input_projection.weight": torch.zeros(64, 70),
                "classifier.weight": torch.zeros(4, 64),  # 4 heads, meta says 3
            },
            p,
        )
        v = validate_serving_contract(p)
        assert not v.ok
        assert v.reason == "CLASS_HEAD_MISMATCH"

    def test_rejects_schema_hash_mismatch(self, tmp_artifact: Path) -> None:
        from nexus_scalp.model_lifecycle.serving_contract import validate_serving_contract

        p = tmp_artifact
        meta = json.loads((p.with_suffix(".meta.json")).read_text())
        meta["feature_schema_hash"] = "deadbeefdeadbeef"
        p.with_suffix(".meta.json").write_text(json.dumps(meta))
        v = validate_serving_contract(p)
        assert not v.ok
        assert v.reason == "SCHEMA_HASH_MISMATCH"

    def test_rejects_feature_order_mismatch(self, tmp_artifact: Path) -> None:
        from nexus_scalp.model_lifecycle.serving_contract import validate_serving_contract

        p = tmp_artifact
        meta = json.loads(p.with_suffix(".meta.json").read_text())
        # swap two canonical names -> order broken
        names = meta["canonical_feature_names"]
        names[0], names[1] = names[1], names[0]
        meta["canonical_feature_names"] = names
        p.with_suffix(".meta.json").write_text(json.dumps(meta))
        v = validate_serving_contract(p)
        assert not v.ok
        assert v.reason == "FEATURE_ORDER_MISMATCH"

    def test_rejects_temporal_contract_mismatch(self, tmp_artifact: Path) -> None:
        from nexus_scalp.model_lifecycle.serving_contract import validate_serving_contract

        p = tmp_artifact
        meta = json.loads(p.with_suffix(".meta.json").read_text())
        meta["temporal_contract"]["max_gap_us"] = 900_000_000  # divergent
        meta["max_gap_us"] = 900_000_000
        p.with_suffix(".meta.json").write_text(json.dumps(meta))
        v = validate_serving_contract(p)
        assert not v.ok
        assert v.reason == "TEMPORAL_CONTRACT_MISMATCH"

    def test_rejects_scaler_dimension_mismatch(self, tmp_artifact: Path) -> None:
        import numpy as np

        from nexus_scalp.model_lifecycle.serving_contract import validate_serving_contract

        p = tmp_artifact
        np.savez(
            p.parent / "model.scaler.npz",
            mean=np.zeros(50, dtype=np.float64),  # 50, not 70
            std=np.ones(50, dtype=np.float64),
        )
        v = validate_serving_contract(p)
        assert not v.ok
        assert v.reason == "SCALER_DIMENSION_MISMATCH"

    def test_rejects_schema_mismatch(self, tmp_artifact: Path) -> None:
        from nexus_scalp.model_lifecycle.serving_contract import validate_serving_contract

        p = tmp_artifact
        meta = json.loads(p.with_suffix(".meta.json").read_text())
        meta["feature_schema_id"] = "not_a_registered_schema"
        p.with_suffix(".meta.json").write_text(json.dumps(meta))
        v = validate_serving_contract(p)
        assert not v.ok
        assert v.reason == "SCHEMA_MISMATCH"

    def test_rejects_missing_artifact(self, tmp_path: Path) -> None:
        from nexus_scalp.model_lifecycle.serving_contract import validate_serving_contract

        v = validate_serving_contract(tmp_path / "nope.pt")
        assert not v.ok
        assert v.reason == "MISSING_ARTIFACT"

    def test_rejects_missing_metadata(self, tmp_path: Path) -> None:
        import torch

        from nexus_scalp.model_lifecycle.serving_contract import validate_serving_contract

        p = tmp_path / "model.pt"
        torch.save({"input_projection.weight": torch.zeros(64, 70)}, p)
        v = validate_serving_contract(p)
        assert not v.ok
        assert v.reason in ("MALFORMED_METADATA", "MISSING_COMPONENT_TENSORS")

    def test_rejects_registry_identity_mismatch(self, tmp_artifact: Path) -> None:
        from nexus_scalp.model_lifecycle.serving_contract import validate_serving_contract

        v = validate_serving_contract(
            tmp_artifact,
            registry_row={
                "feature_schema_id": "scalp_v1",  # artifact is scalp_v3/70
                "feature_dimension": 50,
                "artifact_fingerprint": "",
            },
        )
        assert not v.ok
        assert v.reason == "REGISTRY_IDENTITY_MISMATCH"
        assert v.diagnostics["registry_schema_id"] == "scalp_v1"

    def test_accepts_registry_identity_match(self, tmp_artifact: Path) -> None:
        import hashlib

        from nexus_scalp.model_lifecycle.serving_contract import validate_serving_contract

        fp = hashlib.sha256(tmp_artifact.read_bytes()).hexdigest()[:16]
        v = validate_serving_contract(
            tmp_artifact,
            registry_row={
                "feature_schema_id": "scalp_v3",
                "feature_dimension": 70,
                "artifact_fingerprint": fp,
            },
        )
        assert v.ok
        assert v.checks.get("registry_binding_ok") is True

    def test_require_raises_on_incompatible(self, tmp_artifact: Path) -> None:
        import torch

        from nexus_scalp.model_lifecycle.serving_contract import (
            IncompatibleArtifactError,
            require_serving_contract,
            validate_serving_contract,
        )

        p = tmp_artifact
        torch.save(
            {
                "input_projection.weight": torch.zeros(64, 50),
                "classifier.weight": torch.zeros(3, 64),
            },
            p,
        )
        with pytest.raises(IncompatibleArtifactError) as exc:
            require_serving_contract(p)
        assert exc.value.verdict.reason == "DIMENSION_MISMATCH"
        # the same verdict is available for logging without a second call
        assert validate_serving_contract(p).reason == "DIMENSION_MISMATCH"

    def test_champion_passes_the_contract(self) -> None:
        """The shipped champion is sha256-pinned and coherent: it must pass
        the new validator (proving the gate does not reject the good artifact
        while it rejects the incompatible one)."""
        if not CHAMPION.exists():
            pytest.skip("champion artifact not present")
        from nexus_scalp.model_lifecycle.serving_contract import (
            require_serving_contract,
        )

        v = require_serving_contract(CHAMPION)
        assert v.ok
        assert v.checks["dimension_ok"]
        assert v.checks["temporal_contract_ok"]


class TestFailClosedWiring:
    """STEP-7: the validator is wired at champion load + hot-load."""

    def test_validator_module_is_importable(self) -> None:
        from nexus_scalp.model_lifecycle.serving_contract import (
            ContractVerdict,
            IncompatibleArtifactError,
            require_serving_contract,
            validate_serving_contract,
        )

        assert callable(validate_serving_contract)
        assert callable(require_serving_contract)
        assert issubclass(IncompatibleArtifactError, RuntimeError)

    def test_bundle_store_calls_validator_on_load(self) -> None:
        """The champion load path must route through the contract validator."""
        import inspect

        from nexus_scalp.application.live import model_bundle_store
        from nexus_scalp.model_lifecycle.serving_contract import require_serving_contract

        src = inspect.getsource(
            model_bundle_store.ModelBundleStore._load_or_initialize_model_weights
        )
        assert "require_serving_contract" in src

    def test_hot_swap_calls_validator(self) -> None:
        import inspect

        from nexus_scalp.application.live import hot_swap
        from nexus_scalp.model_lifecycle.serving_contract import (
            validate_serving_contract,
        )

        src = inspect.getsource(hot_swap)
        assert "validate_serving_contract" in src
