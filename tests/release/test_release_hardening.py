"""Release-system hardening tests (spec sections 12-18, 44-45).

All tests in this directory run against REAL artifacts when present under
``release/``; otherwise they exercise the same code paths on synthetic
fixtures so the suite remains deterministic on dev machines without a build.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus_scalp.release import packaging as pkg
from nexus_scalp.release import verify as ver

REPO_ROOT = Path(__file__).resolve().parents[2]


def _inspect_release_root() -> Path | None:
    """Locate the most recent built release root (release/vX.Y.Z/windows/x64)."""
    rel = REPO_ROOT / "release"
    if not rel.is_dir():
        return None
    versions = sorted(
        (d for d in rel.iterdir() if d.is_dir() and d.name.startswith("v")),
        reverse=True,
    )
    for v in versions:
        candidate = v / "windows" / "x64"
        if (candidate / "portable").is_dir():
            return candidate
    return None


# ---------------------------------------------------------------------------
# 1. CHECKSUMS — cross-location path resolution
# ---------------------------------------------------------------------------
def test_checksums_verify_from_release_root(tmp_path: Path) -> None:
    root = _make_release_fixture(tmp_path)
    rel_artifacts = [
        root / "portable" / "NexusScalpEngine.exe",
        root / "cli" / "NexusScalpEngine-CLI.exe",
    ]
    pkg.checksums_file(rel_artifacts, root / "checksums" / "SHA256SUMS.txt", base_dir=root)
    res = pkg.verify_checksums_file(root / "checksums" / "SHA256SUMS.txt", root)
    assert res["valid"] is True


def test_checksums_verify_from_portable_dir(tmp_path: Path) -> None:
    """verify_release from the portable dir must resolve sums against the
    release root (paths in SHA256SUMS.txt are root-relative)."""
    root = _make_release_fixture(tmp_path)
    rel_artifacts = [
        root / "portable" / "NexusScalpEngine.exe",
        root / "cli" / "NexusScalpEngine-CLI.exe",
    ]
    pkg.generate_manifest(
        rel_artifacts, root / "manifests" / "release-manifest.json", base_dir=root
    )
    pkg.checksums_file(rel_artifacts, root / "checksums" / "SHA256SUMS.txt", base_dir=root)
    result = ver.verify_release(root / "portable", include_launch=False)
    checksums = next(c for c in result["checks"] if c["check"] == "Checksums/manifest")
    assert checksums["status"] == "PASS", checksums["detail"]


def test_checksums_missing_file_detected(tmp_path: Path) -> None:
    root = _make_release_fixture(tmp_path)
    sums = root / "checksums" / "SHA256SUMS.txt"
    sums.write_text("0" * 64 + "  portable/NexusScalpEngine.exe\n", encoding="utf-8")
    res = pkg.verify_checksums_file(sums, root)
    assert res["valid"] is False
    assert any(f.get("status") == "MISMATCH" for f in res["files"])


# ---------------------------------------------------------------------------
# 2. MANIFEST — tamper detection
# ---------------------------------------------------------------------------
def test_manifest_tamper_artifact_detected(tmp_path: Path) -> None:
    root = _make_release_fixture(tmp_path)
    art = root / "portable" / "NexusScalpEngine.exe"
    manifest = root / "manifests" / "release-manifest.json"
    pkg.generate_manifest([art], manifest, base_dir=root)
    art.write_bytes(b"tampered-binary")
    res = pkg.verify_manifest(manifest, root)
    assert res["valid"] is False
    assert any(f["status"] == "MISMATCH" for f in res["files"])


def test_manifest_missing_artifact_detected(tmp_path: Path) -> None:
    root = _make_release_fixture(tmp_path)
    art = root / "portable" / "NexusScalpEngine.exe"
    manifest = root / "manifests" / "release-manifest.json"
    pkg.generate_manifest([art], manifest, base_dir=root)
    art.unlink()
    res = pkg.verify_manifest(manifest, root)
    assert res["valid"] is False
    assert any(f["status"] == "MISSING" for f in res["files"])


def test_manifest_identity_fields(tmp_path: Path) -> None:
    root = _make_release_fixture(tmp_path)
    art = root / "portable" / "NexusScalpEngine.exe"
    manifest = root / "manifests" / "release-manifest.json"
    pkg.generate_manifest([art], manifest, base_dir=root, channel="stable")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["version"]
    assert data["architecture"]
    assert data["channel"] == "stable"
    assert data["artifacts"][0]["sha256"]
    assert data["artifacts"][0]["relative_path"].startswith("portable/")


# ---------------------------------------------------------------------------
# 3. ARCHITECTURE — explicit support/unsupported
# ---------------------------------------------------------------------------
def test_architecture_support_matrix_is_explicit() -> None:
    from nexus_scalp.release import environment as renv
    from nexus_scalp.release import evaluate as reval

    for arch, expected in (
        ("x64", "PASS"),
        ("AMD64", "PASS"),
        ("ARM64", "BLOCKED"),
        ("aarch64", "BLOCKED"),
    ):
        fake = renv.EnvironmentInfo(os_name="Windows", architecture=arch, process_architecture=arch)
        res = reval.evaluate_requirements(fake)
        arch_res = next(r for r in res if r.name == "Architecture")
        assert arch_res.verdict == expected, f"{arch} -> {arch_res.verdict}"


# ---------------------------------------------------------------------------
# 4. SECRETS SCAN — REAL vs PLACEHOLDER vs NORMAL source
# ---------------------------------------------------------------------------
def _scan_dir(tmp_path: Path, content: str) -> ver.VerifyResult:
    (tmp_path / "probe.txt").write_text(content, encoding="utf-8")
    for sub in ("Web", "configs", "docs", "licenses"):
        (tmp_path / sub).mkdir(exist_ok=True)
    (tmp_path / "README.txt").write_text("r", encoding="utf-8")
    (tmp_path / "build-info.json").write_text('{"version":"9.0.0"}', encoding="utf-8")
    return ver.ReleaseVerifier(root=tmp_path, exe_name="NexusScalpEngine.exe")._secrets_scan()


def test_secrets_scan_flags_real_bot_token(tmp_path: Path) -> None:
    res = _scan_dir(tmp_path, 'bot_token = "7233738325:AAGuH2WLVRy8KW7M6abcdefghijklmnop"')
    assert res.status == "FAIL"


def test_secrets_scan_flags_real_api_key(tmp_path: Path) -> None:
    res = _scan_dir(tmp_path, 'api_key = "sk-1234567890abcdefghij"')
    assert res.status == "FAIL"


def test_secrets_scan_passes_placeholders(tmp_path: Path) -> None:
    res = _scan_dir(tmp_path, 'bot_token = "TOKEN"\napi_key = ""\npassword = "changeme"')
    assert res.status == "PASS"


def test_secrets_scan_passes_normal_source(tmp_path: Path) -> None:
    res = _scan_dir(
        tmp_path,
        'password = None\npassword = "none"\n# api_key is a parameter not a value\n',
    )
    assert res.status == "PASS"


def test_secrets_scan_flags_jwt(tmp_path: Path) -> None:
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    res = _scan_dir(tmp_path, f'token = "{jwt}"')
    assert res.status == "FAIL"


# ---------------------------------------------------------------------------
# 5. VERIFIER — failure modes
# ---------------------------------------------------------------------------
def test_verifier_fails_on_tampered_release(tmp_path: Path) -> None:
    root = _make_release_fixture(tmp_path)
    result = ver.verify_release(root / "portable", include_launch=False)
    assert result["valid"] is True
    (root / "portable" / "NexusScalpEngine.exe").write_bytes(b"X" * 100)
    result = ver.verify_release(root / "portable", include_launch=False)
    assert result["valid"] is False
    assert any(c["status"] == "FAIL" for c in result["checks"])


def test_verifier_fails_on_missing_manifest(tmp_path: Path) -> None:
    root = _make_release_fixture(tmp_path)
    (root / "manifests" / "release-manifest.json").unlink()
    result = ver.verify_release(root / "portable", include_launch=False)
    assert result["valid"] is False
    checksums = next(c for c in result["checks"] if c["check"] == "Checksums/manifest")
    assert "release-manifest.json missing" in checksums["detail"]


def test_verifier_identity_mismatch_fails(tmp_path: Path) -> None:
    root = _make_release_fixture(tmp_path)
    bi = root / "portable" / "build-info.json"
    data = json.loads(bi.read_text(encoding="utf-8"))
    data["version"] = "9.9.9"
    bi.write_text(json.dumps(data), encoding="utf-8")
    result = ver.verify_release(root / "portable", include_launch=False)
    identity = next(c for c in result["checks"] if c["check"].startswith("Identity"))
    assert identity["status"] == "FAIL"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _make_release_fixture(base: Path) -> Path:
    """Create a minimal valid release tree satisfying the verifier checks.

    Includes a generated release-manifest.json + SHA256SUMS.txt so callers
    start from a COMPLETE, verifier-PASSING release.
    """
    root = base / "release" / "v9.0.0" / "windows" / "x64"
    for sub in (
        "portable/Web",
        "portable/configs",
        "portable/docs",
        "portable/licenses",
        "cli",
        "checksums",
        "manifests",
        "sbom",
    ):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "portable" / "NexusScalpEngine.exe").write_bytes(b"EXE" * 100)
    (root / "portable" / "Web" / "index.html").write_text("<html>", encoding="utf-8")
    (root / "portable" / "Web" / "app.js").write_text("//", encoding="utf-8")
    (root / "portable" / "Web" / "styles.css").write_text("/* */", encoding="utf-8")
    (root / "portable" / "configs" / "base.yaml").write_text(
        "execution:\n  mode: PAPER\n", encoding="utf-8"
    )
    (root / "portable" / "README.txt").write_text("README", encoding="utf-8")
    (root / "portable" / "build-info.json").write_text(
        json.dumps(
            {
                "version": "9.0.0",
                "architecture": "x64",
                "channel": "stable",
                "git_commit": "abc1234",
            }
        ),
        encoding="utf-8",
    )
    (root / "cli" / "NexusScalpEngine-CLI.exe").write_bytes(b"CLI" * 100)
    artifacts = [
        root / "portable" / "NexusScalpEngine.exe",
        root / "cli" / "NexusScalpEngine-CLI.exe",
    ]
    pkg.generate_manifest(
        artifacts, root / "manifests" / "release-manifest.json", base_dir=root, channel="stable"
    )
    pkg.checksums_file(artifacts, root / "checksums" / "SHA256SUMS.txt", base_dir=root)
    return root


# ---------------------------------------------------------------------------
# 6. REAL ARTIFACT TAMPER TEST (runs only when a build exists)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(_inspect_release_root() is None, reason="no built release dir")
def test_real_release_artifacts_verify() -> None:
    """The actual built release must pass the full verifier (no launch)."""
    root = _inspect_release_root()
    assert root is not None
    result = ver.verify_release(root / "portable", include_launch=False)
    assert result["valid"] is True, result["checks"]
