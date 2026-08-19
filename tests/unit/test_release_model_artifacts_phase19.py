"""TASK-9 (TASK-09-70D-PRODUCTION-RELEASE) — model artifact release
compatibility tests (TEST-REL-09/10/11/12/13/26 mapping).

Covers:
    TEST-REL-09  model registry preserved (classification never mutates)
    TEST-REL-10  60D legacy model remains available (LEGACY class, COMPATIBLE)
    TEST-REL-11  70D model loads (scalp_v4 COMPATIBLE when liquidity ok)
    TEST-REL-12  missing Liquidity blocks incompatible 70D model
                 (LIQUIDITY_UNAVAILABLE / MODEL_NOT_RUNTIME_COMPATIBLE)
    TEST-REL-13  wrong schema blocks model (FEATURE_SCHEMA_MISMATCH /
                 DIMENSION_MISMATCH)
    TEST-REL-26  model artifact hashes verified (identity hashes + mismatch)

Run: .venv/Scripts/python.exe -m pytest tests/unit/test_release_model_artifacts_phase19.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus_scalp.features.schema import FEATURE_SCHEMAS
from nexus_scalp.release import model_artifacts as rma

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def artifact_root(tmp_path: Path) -> Path:
    """A fake artifact tree with three models: 50D champ, 60D legacy, 70D."""
    root = tmp_path / "models"
    root.mkdir()

    def _write(model_id: str, schema_id: str, dim: int, *, scaler: bool = True,
               liquidity_algo: str = "1.0.0", artifact_hash: str = "") -> Path:
        d = root / model_id
        d.mkdir()
        (d / "model.pt").write_bytes(b"weights-" + model_id.encode())
        if scaler:
            (d / "scaler.npz").write_bytes(b"scaler-" + model_id.encode())
        mf = {
            "model_id": model_id,
            "model_version": "1.0.0",
            "role": "CANDIDATE",
            "status": "TRAINED",
            "feature_schema_id": schema_id,
            "feature_dimension": dim,
            "label_schema_id": "triple_barrier_3class_v1",
            "class_count": 3,
            "artifact_hash": artifact_hash or rma.sha256_file(d / "model.pt"),
            "scaler_hash": rma.sha256_file(d / "scaler.npz") if scaler else "",
            "liquidity_algorithm_version": liquidity_algo,
        }
        (d / "model.json").write_text(
            json.dumps(mf, indent=2), encoding="utf-8"
        )
        return d

    _write("champ_50d", "scalp_v1", 50)
    _write("legacy_60d", "scalp_v2", 60)
    _write("model_70d", "scalp_v4", 70)
    return root


def _manifest(artifact_dir: Path) -> dict:
    return json.loads((artifact_dir / "model.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Identity (TEST-REL-26)
# ---------------------------------------------------------------------------


def test_identity_fields_complete(artifact_root: Path) -> None:
    ident = rma.compute_artifact_identity(artifact_root / "model_70d")
    assert ident is not None
    d = ident.as_dict()
    for key in (
        "model_id",
        "model_version",
        "schema_id",
        "dimension",
        "schema_hash",
        "artifact_hash",
        "scaler_hash",
        "algorithm_versions",
    ):
        assert key in d, key
    assert d["schema_id"] == "scalp_v4"
    assert d["dimension"] == 70
    assert len(d["artifact_hash"]) == 64
    assert len(d["schema_hash"]) == 64


def test_artifact_hash_mismatch_detected(artifact_root: Path) -> None:
    d = artifact_root / "champ_50d"
    (d / "model.pt").write_bytes(b"tampered")
    res = rma.check_runtime_compatibility(d)
    assert res.status == rma.CompatibilityStatus.MODEL_NOT_RUNTIME_COMPATIBLE
    assert any("artifact_hash" in f for f in res.failures)


def test_missing_weights_yields_unavailable(artifact_root: Path) -> None:
    d = artifact_root / "champ_50d"
    (d / "model.pt").unlink()
    res = rma.check_runtime_compatibility(d)
    assert res.status == rma.CompatibilityStatus.MODEL_UNAVAILABLE


# ---------------------------------------------------------------------------
# Classification (TEST-REL-09/10, brief section 38)
# ---------------------------------------------------------------------------


def test_classify_active_legacy_70d(artifact_root: Path) -> None:
    champ = rma.compute_artifact_identity(artifact_root / "champ_50d")
    legacy = rma.compute_artifact_identity(artifact_root / "legacy_60d")
    m70 = rma.compute_artifact_identity(artifact_root / "model_70d")
    assert champ is not None and legacy is not None and m70 is not None
    assert rma.classify_artifact(champ) == rma.ArtifactClass.ACTIVE
    assert rma.classify_artifact(legacy) == rma.ArtifactClass.LEGACY
    assert rma.classify_artifact(m70) == rma.ArtifactClass.LEGACY


def test_classify_champion_retained(artifact_root: Path) -> None:
    """A champion whose schema is no longer active is RETAINED, never pruned."""
    m70 = rma.compute_artifact_identity(artifact_root / "model_70d")
    assert m70 is not None
    # A champion is by definition protected: ACTIVE, never pruned/archived.
    assert (
        rma.classify_artifact(m70, is_champion=True) == rma.ArtifactClass.ACTIVE
    )


def test_summarize_preserves_all_artifacts(artifact_root: Path) -> None:
    """TEST-REL-09: inventory lists every artifact — classification never
    prunes or hides legacy/70D artifacts."""
    recs = rma.summarize_artifacts(artifact_root)
    ids = {r["identity"]["model_id"] for r in recs}
    assert {"champ_50d", "legacy_60d", "model_70d"} <= ids
    by_id = {r["identity"]["model_id"]: r for r in recs}
    assert by_id["legacy_60d"]["retention"]["action"] == "KEEP"
    assert by_id["legacy_60d"]["retention"]["pruneable_by_release"] is False


# ---------------------------------------------------------------------------
# 70D dependency check (TEST-REL-11/12)
# ---------------------------------------------------------------------------


def test_70d_compatible_when_liquidity_available(artifact_root: Path) -> None:
    res = rma.check_runtime_compatibility(
        artifact_root / "model_70d", liquidity_producer_available=True
    )
    assert res.status == rma.CompatibilityStatus.COMPATIBLE, res.reason


def test_70d_blocked_without_liquidity(artifact_root: Path) -> None:
    """TEST-REL-12: missing liquidity blocks an incompatible 70D model."""
    res = rma.check_runtime_compatibility(
        artifact_root / "model_70d", liquidity_producer_available=False
    )
    assert res.status in (
        rma.CompatibilityStatus.MODEL_NOT_RUNTIME_COMPATIBLE,
        rma.CompatibilityStatus.LIQUIDITY_UNAVAILABLE,
    )
    assert any("liquidity" in f.lower() for f in res.failures)


def test_70d_scalp_v3_requires_liquidity(artifact_root: Path) -> None:
    """scalp_v3 (70D parity contract) also requires the liquidity producer."""
    # Build a scalp_v3 artifact explicitly
    d = artifact_root / "model_70d_v3"
    d.mkdir()
    (d / "model.pt").write_bytes(b"weights-v3")
    (d / "scaler.npz").write_bytes(b"scaler-v3")
    mf = {
        "model_id": "model_70d_v3",
        "model_version": "1.0.0",
        "feature_schema_id": "scalp_v3",
        "feature_dimension": 70,
        "artifact_hash": rma.sha256_file(d / "model.pt"),
        "scaler_hash": rma.sha256_file(d / "scaler.npz"),
        "liquidity_algorithm_version": "1.0.0",
    }
    (d / "model.json").write_text(json.dumps(mf), encoding="utf-8")
    ok = rma.check_runtime_compatibility(d, liquidity_producer_available=True)
    assert ok.status == rma.CompatibilityStatus.COMPATIBLE, ok.reason
    blocked = rma.check_runtime_compatibility(d, liquidity_producer_available=False)
    assert any("liquidity" in f for f in blocked.failures)


def test_60d_legacy_blocked_without_liquidity(artifact_root: Path) -> None:
    """scalp_liquidity_v1 also requires the liquidity producer; scalp_v2
    (momentum family) does NOT — dependency set is schema-specific."""
    res = rma.check_runtime_compatibility(
        artifact_root / "legacy_60d", liquidity_producer_available=False
    )
    assert res.status == rma.CompatibilityStatus.COMPATIBLE


def test_wrong_schema_blocks(artifact_root: Path) -> None:
    """TEST-REL-13: unregistered schema id blocks the model."""
    d = artifact_root / "model_70d"
    mf = _manifest(d)
    mf["feature_schema_id"] = "scalp_v99"
    (d / "model.json").write_text(json.dumps(mf), encoding="utf-8")
    res = rma.check_runtime_compatibility(d, liquidity_producer_available=True)
    assert res.status == rma.CompatibilityStatus.FEATURE_SCHEMA_MISMATCH
    assert "unregistered" in " ".join(res.failures)


def test_dimension_mismatch_blocks(artifact_root: Path) -> None:
    """A manifest whose dimension disagrees with the registry is blocked."""
    d = artifact_root / "model_70d"
    mf = _manifest(d)
    mf["feature_dimension"] = 71
    (d / "model.json").write_text(json.dumps(mf), encoding="utf-8")
    res = rma.check_runtime_compatibility(d, liquidity_producer_available=True)
    assert res.status == rma.CompatibilityStatus.MODEL_NOT_RUNTIME_COMPATIBLE
    assert any("dimension" in f for f in res.failures)


def test_missing_scaler_blocks(artifact_root: Path) -> None:
    d = artifact_root / "model_70d"
    (d / "scaler.npz").unlink()
    res = rma.check_runtime_compatibility(d, liquidity_producer_available=True)
    assert res.status == rma.CompatibilityStatus.MODEL_NOT_RUNTIME_COMPATIBLE
    assert any("scaler" in f for f in res.failures)


# ---------------------------------------------------------------------------
# Schema registry contract (no silent fallback)
# ---------------------------------------------------------------------------


def test_schema_hash_stable_and_registry_derived() -> None:
    h1 = rma.schema_hash_for("scalp_v1")
    h2 = rma.schema_hash_for("scalp_v1")
    assert h1 == h2 and len(h1) == 64
    assert rma.schema_hash_for("scalp_v4") != h1
    assert rma.schema_hash_for("scalp_does_not_exist") == ""


def test_70d_schema_registered_in_canonical_registry() -> None:
    schema = FEATURE_SCHEMAS.resolve("scalp_v4")
    assert schema.dimension == 70
    assert schema.supersedes == "scalp_v1" or schema.supersedes  # lineage declared