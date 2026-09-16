"""BUG-293 release-contract pins: the packaged first-start must be gated.

The v9.0.12 release shipped a Windows EXE that hard-crashed on a clean
install's first start (missing model bundle -> trust gates abort -> PYI
unhandled exception). CI stayed green because NO gate ever launched the
built EXE in a START shape: the smoke steps run `version` and `health`
(non-engine commands), and clean_install_test.ps1 — which exists — was
never wired into the release workflow.

These pins are the same class as the BUG-166 guards: a future release.yml
edit that removes the blocking first-start gate fails here, in-repo,
before the next broken release can ship.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_YML = REPO_ROOT / ".github" / "workflows" / "release.yml"
BUILD_PS1 = REPO_ROOT / "scripts" / "build" / "build_release.ps1"
START_SMOKE = REPO_ROOT / "scripts" / "build" / "start_smoke.ps1"
CLEAN_INSTALL = REPO_ROOT / "scripts" / "build" / "clean_install_test.ps1"

SMOKE_GUARD_ID = "BUG293_START_SMOKE_FAILED"
INSTALL_GUARD_ID = "BUG293_CLEAN_INSTALL_FAILED"


def test_start_smoke_script_exists_and_pins_crash_markers() -> None:
    assert START_SMOKE.exists(), "start_smoke.ps1 missing (BUG-293 gate producer)"
    src = START_SMOKE.read_text(encoding="utf-8")
    # Bare launch (user double-click parity), not an explicit subcommand:
    assert "Start-Process -FilePath $ExePath" in src
    # Every observed failure signature of the v9.0.12 crash must be caught:
    for marker in (
        "ArtifactIntegrityError",
        "MODEL_LOAD_REJECTED",
        "Failed to execute script",
        "[PYI-",
        "Traceback (most recent call last)",
    ):
        assert marker in src, f"crash marker not covered by the start smoke: {marker}"


def test_release_yml_blocks_on_packaged_first_start() -> None:
    src = RELEASE_YML.read_text(encoding="utf-8")
    assert SMOKE_GUARD_ID in src, "blocking packaged START smoke removed from release.yml"
    assert INSTALL_GUARD_ID in src, "blocking clean-install lifecycle step removed from release.yml"
    # Both new steps must live in the WINDOWS BUILD job (the only job that
    # can execute the EXE) — a step parked in a non-gating job would let a
    # broken first-start publish anyway.
    build_job = src.split("build-windows-x64:", 1)[1].split("\n  arm64-report:", 1)[0]
    assert SMOKE_GUARD_ID in build_job
    assert INSTALL_GUARD_ID in build_job
    assert "start_smoke.ps1" in build_job
    # The publish job still gates on the build job (transitively blocking).
    assert "needs: [validate, gates, build-windows-x64]" in src


def test_clean_install_test_wires_start_smoke() -> None:
    src = CLEAN_INSTALL.read_text(encoding="utf-8")
    assert "$StartSmoke" in src and "start_smoke.ps1" in src
    # Local orchestrator runs the smoke too (the release build script must
    # exercise the same user story the CI gate does):
    bsrc = BUILD_PS1.read_text(encoding="utf-8")
    assert "-StartSmoke" in bsrc


def test_release_yml_remains_parseable() -> None:
    """Guards live in YAML land — keep the document loadable (no indentation
    accidents from the inserted steps)."""
    import yaml

    doc = yaml.safe_load(RELEASE_YML.read_text(encoding="utf-8"))
    steps = doc["jobs"]["build-windows-x64"]["steps"]
    names = [s.get("name", "") for s in steps]
    smoke = [n for n in names if "START smoke" in n]
    install = [n for n in names if "Clean-install" in n]
    assert smoke and install, names
    # Order matters: the packaged smoke runs right after EXE smoke tests and
    # BEFORE the installer/checksum steps so a broken payload fails early.
    idx = {n: i for i, n in enumerate(names)}
    assert idx[smoke[0]] > idx["EXE smoke tests"]
    assert idx[install[0]] > idx["Installer (Inno Setup)"]
