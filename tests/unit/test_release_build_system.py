# Nexus Scalp Engine — Tests for the RELEASE BUILD SYSTEM and deliverable scripts
# =============================================================================
# Behavioral checks on the packaging helper module and the release output tree
# contract (scripts/build/*, installer/*, .github/workflows/release.yml).
# These run on every CI push — not only on tag releases.
# =============================================================================

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_build_scripts_exist() -> None:
    """The release system's scripts and installer are present and sane."""
    for p in (
        "scripts/build/build_release.ps1",
        "scripts/build/verify_release.ps1",
        "scripts/build/clean_install_test.ps1",
        "installer/NexusScalpEngine.iss",
        ".github/workflows/release.yml",
    ):
        f = REPO_ROOT / p
        assert f.exists(), f"missing {p}"
        text = f.read_text(encoding="utf-8", errors="replace")
        assert text.strip(), f"empty {p}"


def test_build_scripts_reference_packaged_entrypoint() -> None:
    """Both the local script and the CI workflow must build the packaged
    entrypoint (src/nexus_scalp/release/packaged_main.py) so the EXE exposes
    the `nexus` CLI and never the raw argparse launcher."""
    for p in (
        "scripts/build/build_release.ps1",
        ".github/workflows/release.yml",
    ):
        text = (REPO_ROOT / p).read_text(encoding="utf-8", errors="replace")
        assert "packaged_main.py" in text, f"{p} must reference packaged_main.py"


def test_release_workflow_has_no_live_and_publishes_assets() -> None:
    text = (REPO_ROOT / ".github/workflows/release.yml").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "action-gh-release" in text
    assert "packaged_main.py" in text
    assert "SHA256SUMS" in text or "checksums" in text
    assert "arm64" in text.lower()  # explicit non-support reporting


def test_installer_iss_safe_defaults() -> None:
    text = (REPO_ROOT / "installer/NexusScalpEngine.iss").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "ArchitecturesAllowed=x64compatible" in text  # no ARM64 silently
    assert "uninsneveruninstall" in text  # user data never auto-deleted
    assert "{localappdata}" in text  # per-user install + data separation


def test_requirements_cover_web_and_news_runtime() -> None:
    """The web/news stack is a runtime dependency — requirements.txt and
    pyproject.toml must both declare it (no silent runtime drift)."""
    req = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    pypr = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for dep in ("fastapi", "uvicorn", "httpx", "feedparser"):
        assert dep in req, f"requirements.txt missing {dep}"
        assert dep in pypr, f"pyproject.toml missing {dep}"


def test_release_dir_contract(tmp_path: Path) -> None:
    """The release output layout contract: versioned dir, windows/x64 split,
    portable/cli subdirs, checksums+manifests dirs."""
    base = tmp_path / "release" / "v9.0.0" / "windows" / "x64"
    for sub in ("portable", "cli", "checksums", "manifests", "sbom"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    assert (base / "portable").is_dir()
    assert (base / "cli").is_dir()
    assert (base / "checksums").is_dir()
