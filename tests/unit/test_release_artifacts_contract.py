"""Regression tests for the 2026-08-30 release-artifacts + manifest repair.

Covers:
1. packaging.generate_manifest self-creates its destination directory
   (root cause of the v9.0.2 release FileNotFoundError — clean CI checkouts
   have no release/vX/windows/x64/manifests tree).
2. packaging.generate_sbom / checksums_file self-create destinations.
3. nexus_scalp.release.artifacts canonical naming model: deterministic,
   version-derived names for the release root, portable zip, installer,
   manifest, checksums and SBOM paths.
"""

from __future__ import annotations

from pathlib import Path

from nexus_scalp.release import artifacts
from nexus_scalp.release import packaging as p


def test_generate_manifest_creates_missing_parent_dirs(tmp_path: Path) -> None:
    """v9.0.2 release failure: manifests/ did not exist in a clean checkout.
    generate_manifest must create the full destination path itself."""
    root = tmp_path / "release" / "v9.0.2" / "windows" / "x64"
    out = root / "manifests" / "release-manifest.json"
    assert not root.exists(), "precondition: clean tree (no release dirs)"

    result = p.generate_manifest([], out, channel="stable", base_dir=root)

    assert result == out
    assert out.exists(), "manifest written"
    assert out.parent.is_dir()


def test_generate_manifest_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path
    out = root / "manifests" / "release-manifest.json"
    p.generate_manifest([], out, base_dir=root)
    first = out.read_text(encoding="utf-8")
    p.generate_manifest([], out, base_dir=root)
    assert out.read_text(encoding="utf-8") == first


def test_generate_sbom_creates_missing_parent_dirs(tmp_path: Path) -> None:
    out = tmp_path / "deep" / "nested" / "sbom.spdx.json"
    p.generate_sbom(dependencies={"torch": "2.0"}, out=out)
    assert out.exists()


def test_artifact_naming_is_deterministic_and_version_derived(tmp_path: Path) -> None:
    root = artifacts.release_root(tmp_path, "9.0.3")
    assert root == tmp_path / "release" / "v9.0.3" / "windows" / "x64"

    zip_path = artifacts.portable_zip_path(root, "9.0.3")
    setup_path = artifacts.installer_path(root, "9.0.3")
    assert zip_path.name == "NexusScalpEngine-9.0.3-win-x64.zip"
    assert setup_path.name == "NexusScalpEngine-9.0.3-win-x64-setup.exe"

    # Determinism: same inputs -> identical paths
    assert artifacts.portable_zip_path(root, "9.0.3") == zip_path
    assert artifacts.installer_path(root, "9.0.3") == setup_path

    # v-prefix tolerance
    assert artifacts.portable_zip_path(root, "v9.0.3") == zip_path


def test_artifact_contract_paths_are_consistent(tmp_path: Path) -> None:
    root = artifacts.release_root(tmp_path, "1.2.3")
    assert artifacts.release_manifest_path(root) == root / "manifests" / "release-manifest.json"
    assert artifacts.embedded_manifest_path(root) == root / "portable" / "release-manifest.json"
    assert artifacts.sha256_sums_path(root) == root / "checksums" / "SHA256SUMS.txt"
    assert artifacts.sbom_path(root) == root / "sbom" / "sbom.spdx.json"
    # Embedded manifest lives inside the portable bundle shipped to users.
    assert artifacts.embedded_manifest_path(root).parent == artifacts.portable_dir(root)


def test_manifest_artifacts_list_only_existing(tmp_path: Path) -> None:
    root = artifacts.release_root(tmp_path, "9.0.4")
    # Nothing built yet -> empty list, no error.
    assert artifacts.manifest_artifacts_for(root, "9.0.4") == []

    exe = artifacts.portable_dir(root) / "NexusScalpEngine.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    listed = artifacts.manifest_artifacts_for(root, "9.0.4")
    assert listed == [exe]
