"""TASK-9 (TASK-09-70D-PRODUCTION-RELEASE) — release manifest schema coverage
tests (TEST-REL-27 mapping).

Covers:
    TEST-REL-27  release manifest validated (schema coverage is complete and
                 derives from the canonical registry — never hardcoded)
    brief §37    manifest carries release_version / app_commit /
                 db_schema_version / web_bundle_version /
                 supported_model_schemas / required_migrations / hashes

Run: .venv/Scripts/python.exe -m pytest tests/unit/test_release_manifest_phase19.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus_scalp.release import packaging as pkg


@pytest.fixture()
def manifest_out(tmp_path: Path) -> dict:
    """Generate a real manifest into a temp dir with fake artifacts."""
    root = tmp_path / "release"
    root.mkdir()
    (root / "portable").mkdir()
    (root / "portable" / "NexusScalpEngine.exe").write_bytes(b"MZ-fake")
    arts = [root / "portable" / "NexusScalpEngine.exe"]
    out = root / "manifests" / "release-manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    pkg.generate_manifest(arts, out, base_dir=root)
    return json.loads(out.read_text(encoding="utf-8"))


def test_manifest_has_schema_coverage(manifest_out: dict) -> None:
    """TEST-REL-27 + brief §37: all contract keys present with real values."""
    for key in (
        "version",
        "git_commit",
        "db_schema_version",
        "web_bundle_version",
        "feature_schema",
        "feature_schema_dimension",
        "supported_model_schemas",
        "required_migrations",
        "model_compatibility",
        "artifacts",
    ):
        assert key in manifest_out, key
    assert manifest_out["feature_schema"] == "scalp_v1"
    assert manifest_out["feature_schema_dimension"] == 50
    assert "scalp_v1" in manifest_out["supported_model_schemas"]
    assert "scalp_v4" in manifest_out["supported_model_schemas"]
    assert manifest_out["db_schema_version"] >= 4  # audit=4 (2026-08-19)
    assert len(manifest_out["required_migrations"]) >= 5  # 3 audit + 1 news + 1 candle
    # every artifact has a sha256
    for a in manifest_out["artifacts"]:
        assert len(a["sha256"]) == 64


def test_manifest_feature_schema_registry_derived() -> None:
    """The manifest feature schema is never hardcoded — an unknown stamped
    value falls back to the registry's ACTIVE schema id."""
    assert pkg._manifest_feature_schema({"feature_schema": "scalp_v4"}) == "scalp_v4"
    assert pkg._manifest_feature_schema({"feature_schema": "does_not_exist"}) == "scalp_v1"
    assert pkg._manifest_feature_schema({}) == "scalp_v1"


def test_manifest_supported_schemas_include_70d() -> None:
    schemas = pkg._manifest_supported_model_schemas()
    assert "scalp_v2" in schemas  # 60D legacy
    assert "scalp_v4" in schemas  # 70D contract
    assert "scalp_liquidity_v1" in schemas  # 60D liquidity


def test_manifest_model_compatibility_all_schemas() -> None:
    line = pkg._manifest_model_compatibility({})
    assert "scalp_v1 (50D)" in line
    assert "scalp_v4 (70D)" in line


def test_manifest_roundtrip_verify(tmp_path: Path) -> None:
    """verify_manifest must accept the enriched manifest (additive keys)."""
    root = tmp_path / "release"
    root.mkdir()
    exe = root / "NexusScalpEngine.exe"
    exe.write_bytes(b"MZ-fake-2")
    out = root / "release-manifest.json"
    pkg.generate_manifest([exe], out, base_dir=root)
    res = pkg.verify_manifest(out, base_dir=root)
    assert res.get("valid") is True, res