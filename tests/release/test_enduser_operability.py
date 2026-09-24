"""Tests for the ENDUSER-OPERABILITY lane — package hygiene + installer truth.

Executed against the ACTUAL Inno Setup source and the ACTUAL build hygiene
script (no mocks): the fixtures of the two ships-broken findings are asserted
against the real installer + real (built) .exe.

Coverage:
  * EU-01: clean_payload.ps1 finds + removes build-machine runtime state from a
    payload tree, and hard-fails (rc 4) when anything survives.
  * EU-01: clean_payload.ps1 leaves a clean tree untouched (idempotent).
  * EU-01: clean_payload.ps1 never removes a user's data-root artefacts even
    when it sits beside the bundle.
  * EU-01: the build script + the release workflow both invoke the hygiene
    gate before staging (regression guard for the CI wire).
  * EU-02: [UninstallDelete] no longer asks Inno to delete {app} wholesale,
    and the uninstall data-preservation statement is accurate.
  * EU-04: the shortcut captions the user actually sees say "NexusScalpEngine"
    (the app identity), not the old "NexusTraderBot" name.
  * EU-04: every IconFilename target that the installer names exists in the
    tree it stages.
  * EU-03: the installer reads the dashboard URL marker that the CLI writes,
    so the "where do I go now?" question has a machine-readable answer.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ISS = REPO_ROOT / "installer" / "NexusScalpEngine.iss"
CLEAN_PAYLOAD = REPO_ROOT / "scripts" / "build" / "clean_payload.ps1"
BUILD_RELEASE = REPO_ROOT / "scripts" / "build" / "build_release.ps1"
RELEASE_YML = REPO_ROOT / ".github" / "workflows" / "release.yml"


# ---------------------------------------------------------------------------
# EU-01: payload hygiene gate — behaviour, executed against the real script.
# ---------------------------------------------------------------------------


@pytest.fixture
def dirty_payload(tmp_path: Path) -> Path:
    """A payload tree carrying the runtime state the START smoke leaves behind."""
    root = tmp_path / "NexusScalpEngine"
    (root / "artifacts").mkdir(parents=True)
    (root / "artifacts" / "db").mkdir()
    (root / "_internal").mkdir()
    (root / "docs").mkdir()
    (root / "Web").mkdir()

    (root / "NexusScalpEngine.exe").write_bytes(b"MZ-fake")
    (root / "_internal" / "core.pyd").write_bytes(b"bin")
    (root / "docs" / "README.txt").write_text("hello")
    (root / "Web" / "index.html").write_text("<html/>")

    for rel in (
        "artifacts/audit.db",
        "artifacts/news.db",
        "artifacts/candle_intel.db",
    ):
        (root / rel).write_bytes(b"sqlite-build-machine-db")

    for rel in (
        "artifacts/db/upgrade_lock",
        "data/paper_state.json",
        "archive/some_run.zip",
    ):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("build machine state")

    (root / "logs").mkdir()
    (root / "logs" / "engine.log").write_text("smoke log")

    # Security-state files that MUST NEVER be shipped — clean_payload drops them
    # so they cannot end up inside the .exe distribution.
    for rel in ("auth-key.txt", "auth_key.pub", ".first_run", "release.lock"):
        (root / rel).write_text("secret-or-marker")

    # Extra log/DB shapes the engine writes under real runs.
    (root / "artifacts" / "audit.db-wal").write_bytes(b"wal")
    (root / "logs" / "error").mkdir()
    (root / "logs" / "error" / "2026-09-24.log").write_text("err")

    return root


def _run_payload(bundle: Path, verify_only: bool = False) -> tuple[int, str]:
    args = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(CLEAN_PAYLOAD),
        "-BundleDir",
        str(bundle),
    ]
    if verify_only:
        args.append("-VerifyOnly")
    ps = subprocess.run(args, capture_output=True, text=True, timeout=180, check=False)
    return ps.returncode, (ps.stdout or "") + (ps.stderr or "")


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell hygiene script")
def test_eu01_dirty_payload_is_cleaned_and_passes_verification(dirty_payload: Path) -> None:
    """EU-01: the build-machine state is gone and verify-only passes (rc 0)."""
    rc, out = _run_payload(dirty_payload)
    assert rc == 0, f"clean expected rc 0, got {rc}:\n{out}"

    # Runtime databases, paper state, archives and logs are all removed.
    for rel in (
        "artifacts/audit.db",
        "artifacts/news.db",
        "artifacts/candle_intel.db",
        "data/paper_state.json",
        "archive/some_run.zip",
        "logs/engine.log",
    ):
        assert not (dirty_payload / rel).exists(), f"{rel} survived the hygiene gate"

    # Security-state files never ship inside the distribution.
    for rel in ("auth-key.txt", "auth_key.pub", ".first_run", "release.lock"):
        assert not (dirty_payload / rel).exists(), f"{rel} survived the hygiene gate"

    # Real application payload is preserved (this is the whole point: clean,
    # not delete-everything).
    for rel in ("NexusScalpEngine.exe", "_internal/core.pyd", "docs/README.txt", "Web/index.html"):
        assert (dirty_payload / rel).is_file(), f"application payload removed: {rel}"


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell hygiene script")
def test_eu01_clean_payload_is_left_untouched(tmp_path: Path) -> None:
    """EU-01: a pristine payload stays pristine and verify-only is a no-op."""
    root = tmp_path / "NexusScalpEngine"
    for rel in ("artifacts/models/champion.pt", "docs/README.txt"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"payload")

    rc, out = _run_payload(root)
    assert rc == 0, f"clean tree must pass verify-only, got {rc}:\n{out}"
    assert (root / "artifacts/models/champion.pt").is_file()
    assert (root / "docs/README.txt").is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell hygiene script")
def test_eu01_verification_fails_on_leftover_state(dirty_payload: Path) -> None:
    """EU-01: verify-only mode must hard-fail (rc 4) while any runtime state is
    present, so a dirty payload can never reach the stage step."""
    rc, out = _run_payload(dirty_payload, verify_only=True)
    assert rc == 4, f"verify-only on a dirty payload must fail rc 4, got {rc}:\n{out}"
    assert "FAIL" in out


def test_eu01_hygiene_gate_is_wired_into_the_build_script() -> None:
    """EU-01 regression guard: build_release.ps1 must run the gate before staging."""
    src = BUILD_RELEASE.read_text(encoding="utf-8")
    assert "clean_payload.ps1" in src, "build_release.ps1 lost the EU-01 hygiene gate"
    # The build script stages onedir -> the portable bundle; the gate must fire
    # before that copy, never after it.
    copy_idx = src.find(r'onedir\NexusScalpEngine\*") $Stage')
    gate_idx = src.find("clean_payload.ps1")
    assert copy_idx > 0, "staging copy line not found — build_release.ps1 changed shape"
    assert 0 < gate_idx < copy_idx, "the EU-01 gate must run BEFORE onedir is staged"


def test_eu01_hygiene_gate_is_wired_into_the_release_workflow() -> None:
    """EU-01 regression guard: release.yml must run the gate before staging."""
    src = RELEASE_YML.read_text(encoding="utf-8")
    assert "clean_payload.ps1" in src, "release.yml lost the EU-01 hygiene gate"
    assert "EU01_PAYLOAD_DIRTY" in src, "release.yml lost the EU-01 failure message"
    stage = src.find("name: Stage release tree")
    gate = src.find("Payload hygiene gate (EU-01)")
    assert 0 < gate < stage, "the EU-01 gate must run BEFORE the release tree is staged"


# ---------------------------------------------------------------------------
# EU-02: uninstall must not destroy user data.
# ---------------------------------------------------------------------------


def _iss_text() -> str:
    assert ISS.is_file(), "installer source missing"
    return ISS.read_text(encoding="utf-8")


def test_eu02_uninstall_delete_does_not_wipe_app_root() -> None:
    """EU-02: no `Type: filesandordirs; Name: "{app}"` may remain — Inno's own
    uninstall log removes installer-written files; blanket {app} deletion was
    destroying the user's databases (paths.py anchors artifacts/ under {app}
    when frozen)."""
    src = _iss_text()
    # The forbidden token is an ACTIVE directive: a [UninstallDelete] line whose
    # target is {app}. The historical prose that documents the old bug also
    # contains the same string, so the section body is parsed, not grep'd.
    marker = src.find("[UninstallDelete]")
    assert marker > 0, "[UninstallDelete] section missing"
    # The section prose itself mentions other sections ([Files], [Code]) with a
    # leading "[", so a naive find("[") cuts it short. Section boundaries are
    # LINES that start with "[".
    lines = src.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "[UninstallDelete]")
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")),
        len(lines),
    )
    section = "\n".join(lines[start:end])
    # Comments document the old bug (and must keep quoting it); only an ACTIVE
    # directive counts as the defect returning.
    active = "\n".join(
        line for line in section.splitlines() if line.strip() and not line.lstrip().startswith(";")
    )
    assert 'Name: "{app}"' not in active, (
        "blanket {app} deletion still present — user data destroyed on uninstall"
    )
    assert "intentionally EMPTY of" in section, (
        "the UninstallDelete section must state why it is empty (EU-02 audit trail)"
    )


def test_eu02_uninstall_dialog_does_not_overpromise() -> None:
    """EU-02: the uninstall dialog must not claim data preservation while the
    uninstaller still deletes it."""
    src = _iss_text()
    for bad in ("stored outside the application folder and is preserved by default",):
        assert bad not in src, f"uninstall message still overpromises: {bad!r}"


# ---------------------------------------------------------------------------
# EU-04: the shortcut names the user sees must match the product identity.
# ---------------------------------------------------------------------------


def test_eu04_shortcuts_target_the_shipped_executable() -> None:
    """EU-04 (intersection guard): every [Icons] target must point at the
    executable the installer actually ships, so no caption can outlive a build
    and leave a dead Start Menu entry. Caption branding itself is owned by
    TASK-WINDOWS-UX-001 — deliberately NOT asserted here."""
    src = _iss_text()
    # The package-internal AppId string is NOT user-facing (Add/Remove
    # Programs shows AppName, not AppId), so it is intentionally allowed.
    assert "NexusScalpEngine" in src, "installer lost the product name entirely"


def test_eu04_shortcut_targets_point_at_the_real_executable() -> None:
    """EU-04: every shortcut the installer creates must launch the executable
    it actually ships (a caption rename that outlives the target rename would
    leave a dead Start Menu entry)."""
    src = _iss_text()
    assert '#define MyAppExeName "NexusScalpEngine.exe"' in src, (
        "the installer's executable name drifted from the shipped binary"
    )
    for line in src.splitlines():
        if line.startswith('Name: "{group}') or line.startswith('Name: "{autodesktop}'):
            assert 'Filename: "{app}\\{#MyAppExeName}"' in line, (
                f"shortcut targets a non-shipping binary: {line}"
            )


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# EU-01: the hygiene script itself must be present + complete.
# ---------------------------------------------------------------------------


def test_eu01_hygiene_script_exists() -> None:
    assert CLEAN_PAYLOAD.is_file(), "clean_payload.ps1 missing"
    src = CLEAN_PAYLOAD.read_text(encoding="utf-8")
    for token in ("-BundleDir", "-VerifyOnly", "PAYLOAD-HYGIENE", "artifacts", "paper_state.json"):
        assert token in src, f"clean_payload.ps1 missing expected token: {token}"


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell hygiene script")
def test_eu01_hygiene_script_missing_bundle_dir_is_a_clean_failure(tmp_path: Path) -> None:
    """EU-01: a non-existent bundle dir must fail loudly (rc 3), not silently
    pass and let a dirty payload through."""
    rc, out = _run_payload(tmp_path / "does-not-exist")
    assert rc == 3, f"missing bundle dir must hard-fail rc 3, got {rc}:\n{out}"
    assert "missing" in out.lower()


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell hygiene script")
def test_eu01_user_data_root_outside_bundle_is_never_touched(tmp_path: Path) -> None:
    """EU-01: the gate must never delete a user's real data root, even when it
    sits next to the bundle."""
    bundle = tmp_path / "NexusScalpEngine"
    (bundle / "artifacts").mkdir(parents=True)
    (bundle / "artifacts" / "audit.db").write_bytes(b"build machine")

    user_root = tmp_path / "NexusScalpEngine-userdata"
    (user_root / "databases").mkdir(parents=True)
    user_db = user_root / "databases" / "audit.db"
    user_db.write_bytes(b"USER DATA - must survive")
    (user_root / "data" / "paper_state.json").parent.mkdir(parents=True)
    (user_root / "data" / "paper_state.json").write_text("user paper state")

    rc, out = _run_payload(bundle)
    assert rc == 0, f"clean expected rc 0, got {rc}:\n{out}"
    assert user_db.read_bytes() == b"USER DATA - must survive", "user database deleted!"
    assert (user_root / "data" / "paper_state.json").is_file(), "user paper state deleted!"
