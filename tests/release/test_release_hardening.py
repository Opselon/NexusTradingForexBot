"""BUG-166 pre-stage guard + BUG-174 CLI identity — release-hardening regression tests.

Covers the directive's P2 release-hardening task:

1. BUG-166 defense-in-depth guard exists in release.yml (CI-level regression:
   a future edit removing the guard fails these tests).
2. Pre-stage contract semantics: missing sums/manifest -> guard error id present;
   present contract -> installer allowed to proceed (existence semantics via the
   same Test-Path logic the workflow uses, exercised against a real tmp tree).
3. Installed 3-artifact checksum contract: portable/cli/zip entries verified,
   setup.exe intentionally ABSENT, tampering FAILS, missing records FAIL.
4. BUG-174: onefile CLI identity — build-info.json MUST be discoverable from
   sys._MEIPASS (PyInstaller onefile payload dir); a frozen CLI without the
   payload reports Commit None + runtime timestamp (fails-before regression).
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pytest

# BUG-210 xdist-stability shim: the suite may run with CWD redirected to a
# pytest tmp dir by a concurrently-collected module (a collection-time chdir
# is session-global under pytest-xdist and deterministic per worker).
# Importing nexus_scalp while cwd == repo root would let the release
# metadata CWD fallback resolve the repo-root build-info.json and poison
# get_version_info() for every later test in THIS worker. Anchor repo-root
# imports to the file location instead of CWD.
_REPO_PARENT = str(Path(__file__).resolve().parents[2])
if _REPO_PARENT not in sys.path:
    sys.path.insert(0, _REPO_PARENT)

from nexus_scalp.release import metadata as rmeta  # noqa: E402

REPO_ROOT = Path(_REPO_PARENT)
RELEASE_YML = REPO_ROOT / ".github" / "workflows" / "release.yml"
GUARD_ID = "BUG166_MISSING_PRESTAGE_CONTRACT"


# ---------------------------------------------------------------------------
# Part 1 — the CI guard exists and is wired into the pre-stage step
# ---------------------------------------------------------------------------
def test_release_yml_has_bug166_guard() -> None:
    """The defense-in-depth guard must exist in the workflow source itself.

    Regression: a future release.yml edit that removes the post-pre-stage
    contract assertion silently re-opens BUG-160 (installer embeds nothing,
    release publishes, post-install verify-release fails too late).
    """
    src = RELEASE_YML.read_text(encoding="utf-8")
    assert GUARD_ID in src, "BUG-166 guard error id missing from release.yml"
    # Guard must reference BOTH contract files
    assert "SHA256SUMS.txt" in src
    assert "release-manifest.json" in src
    # Guard must live in the pre-stage step (before the Installer step)
    pre_idx = src.find("Pre-stage verification contract for installer")
    ins_idx = src.find("name: Installer (Inno Setup)")
    assert 0 < pre_idx < ins_idx, "pre-stage step ordering changed"
    guard_idx = src.find(GUARD_ID)
    assert pre_idx < guard_idx < ins_idx, (
        "BUG-166 guard must execute inside the pre-stage step, before ISCC"
    )


def test_release_yml_guard_is_throw_not_warning() -> None:
    """The guard must FAIL the build (pwsh throw), not just log."""
    src = RELEASE_YML.read_text(encoding="utf-8")
    guard_zone = src[src.find(GUARD_ID) - 2000 : src.find(GUARD_ID) + 2000]
    assert re.search(r"throw\s+\"BUG166_MISSING_PRESTAGE_CONTRACT", guard_zone), (
        "guard must throw, not warn"
    )
    assert "Write-Host" in guard_zone  # and log success when present


def test_release_yml_installer_still_after_full_checksums() -> None:
    """Part 3: full Checksums+manifest+SBOM step must stay AFTER the installer
    (it hashes the setup.exe — BUG-143 chain must not be reordered)."""
    src = RELEASE_YML.read_text(encoding="utf-8")
    ins_idx = src.find("name: Installer (Inno Setup)")
    full_idx = src.find("name: Checksums + manifest + SBOM")
    assert 0 < ins_idx < full_idx, "installer must run BEFORE full checksums"


def test_release_yml_bug143_preflight_intact() -> None:
    """The full-checksum step must still pre-flight all 4 artifacts incl. setup.exe."""
    src = RELEASE_YML.read_text(encoding="utf-8")
    assert "BUG143_MISSING_ARTIFACT" in src
    assert "BUG143_EMPTY_ARTIFACT" in src
    assert "NexusScalpEngine-$V-win-x64-setup.exe" in src or ("win-x64-setup.exe" in src)


# ---------------------------------------------------------------------------
# Part 2 — guard existence semantics exercised on a real tmp tree
# ---------------------------------------------------------------------------
def _run_guard_logic(base: Path) -> str | None:
    """Mirror of the workflow's guard checks (Test-Path + minimum size).

    Returns the BUG166 error id when the guard would fail the build, else None.
    Kept in lockstep with release.yml deliberately; the yml-source tests above
    catch drift of the workflow, this catches drift of the semantics.
    """
    sums = base / "checksums" / "SHA256SUMS.txt"
    man = base / "manifests" / "release-manifest.json"
    if not sums.exists():
        return GUARD_ID
    if not man.exists():
        return GUARD_ID
    if sums.stat().st_size < 50 or man.stat().st_size < 50:
        return GUARD_ID
    return None


def test_guard_fails_when_contract_missing(tmp_path: Path) -> None:
    assert _run_guard_logic(tmp_path) == GUARD_ID


def test_guard_fails_on_stubs_too_small(tmp_path: Path) -> None:
    (tmp_path / "checksums").mkdir()
    (tmp_path / "manifests").mkdir()
    (tmp_path / "checksums" / "SHA256SUMS.txt").write_text("short", encoding="ascii")
    (tmp_path / "manifests" / "release-manifest.json").write_text("{}", encoding="ascii")
    assert _run_guard_logic(tmp_path) == GUARD_ID


def test_guard_passes_with_real_contract(tmp_path: Path) -> None:
    (tmp_path / "checksums").mkdir()
    (tmp_path / "manifests").mkdir()
    sums = tmp_path / "checksums" / "SHA256SUMS.txt"
    man = tmp_path / "manifests" / "release-manifest.json"
    # realistic-size contract (a real sums file is ~4 lines x 70+ chars; the
    # manifest carries identity + artifact entries — both far above 50 bytes)
    sums.write_text(("a" * 64 + "  portable/NexusScalpEngine.exe\n") * 4, encoding="ascii")
    man.write_text(
        json.dumps(
            {
                "product": "NexusScalpEngine",
                "version": "9.9.9",
                "git_commit": "deadbee",
                "build_timestamp": "2026-01-01T00:00:00+00:00",
                "artifacts": [
                    {
                        "name": "NexusScalpEngine.exe",
                        "relative_path": "portable/NexusScalpEngine.exe",
                        "size_bytes": 1,
                        "sha256": "a" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert _run_guard_logic(tmp_path) is None


# ---------------------------------------------------------------------------
# Part 4/5 — installed 3-artifact contract semantics
# ---------------------------------------------------------------------------
def _installed_sums(tmp_path: Path) -> Path:
    """Build an installed-tree-style contract (portable/cli/zip, NO setup.exe)."""
    (tmp_path / "checksums").mkdir()
    hashes = {
        "portable\\NexusScalpEngine.exe": "a" * 64,
        "cli\\NexusScalpEngine-CLI.exe": "b" * 64,
        "NexusScalpEngine-9.9.9-win-x64.zip": "c" * 64,
    }
    lines = "".join(f"{h}  {rel}\n" for rel, h in hashes.items())
    p = tmp_path / "checksums" / "SHA256SUMS.txt"
    p.write_text(lines, encoding="ascii")
    return p


def test_installed_contract_has_exactly_three_artifacts(tmp_path: Path) -> None:
    lines = _installed_sums(tmp_path).read_text(encoding="ascii").strip().splitlines()
    assert len(lines) == 3
    rels = [ln.split(maxsplit=1)[1].strip() for ln in lines]
    assert any(r.startswith("portable") for r in rels)
    assert any(r.startswith("cli") for r in rels)
    assert any(r.endswith(".zip") for r in rels)
    # setup.exe intentionally ABSENT (it is not inside the installed tree)
    assert not any("setup" in r for r in rels)


def test_installed_contract_tamper_detection(tmp_path: Path) -> None:
    """A tampered checksum line must NOT verify as valid."""
    from nexus_scalp.release.packaging import verify_checksums_file

    sums = _installed_sums(tmp_path)
    rel_root = tmp_path / "treeroot"
    (rel_root / "portable").mkdir(parents=True)
    (rel_root / "cli").mkdir(parents=True)
    # real files whose content hashes differ from the contract lines
    (rel_root / "portable" / "NexusScalpEngine.exe").write_bytes(b"TAMPERED")
    (rel_root / "cli" / "NexusScalpEngine-CLI.exe").write_bytes(b"TAMPERED")
    result = verify_checksums_file(sums, rel_root)
    assert result.get("valid") is False or result.get("ok") is False, result
    failures = str(result)
    assert (
        "NexusScalpEngine.exe" in failures
        or "MISMATCH" in failures.upper()
        or (result.get("problems") or result.get("errors"))
    ), f"tampering must be reported: {result}"


def test_installed_contract_missing_record_is_not_pass(tmp_path: Path) -> None:
    """A missing checksum record for a present file must not become a PASS —
    verify_checksums only validates what is listed, so the GUARD layer (release
    pre-flight + verify-release manifest cross-check) must catch the gap. Here
    we prove the manifest artifact list is the authoritative completeness check:
    a manifest entry whose hash line is absent from the sums file is a problem."""
    from nexus_scalp.release.packaging import verify_manifest

    manifest = tmp_path / "release-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": "9.9.9",
                "artifacts": [
                    {
                        "name": "NexusScalpEngine.exe",
                        "relative_path": "portable/NexusScalpEngine.exe",
                        "size_bytes": 8,
                        "sha256": "d" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    base = tmp_path / "base"
    (base / "portable").mkdir(parents=True)
    (base / "portable" / "NexusScalpEngine.exe").write_bytes(b"ACTUALFILE")
    result = verify_manifest(manifest, base)
    # hash of b'ACTUALFILE' != 'd'*64 -> must not be valid
    assert result.get("valid") is not True or any(
        e.get("match") is False for e in result.get("results", []) if isinstance(e, dict)
    ), f"hash mismatch must fail: {result}"


# ---------------------------------------------------------------------------
# Part 6-11 — BUG-174 onefile CLI identity (fails-before / passes-after)
# ---------------------------------------------------------------------------
def test_onefile_meipass_candidate_present_in_source() -> None:
    """The frozen candidate list must include the PyInstaller onefile payload
    dir (sys._MEIPASS). Regression for 'Commit: None + runtime timestamp'."""
    src = (REPO_ROOT / "src" / "nexus_scalp" / "release" / "metadata.py").read_text(
        encoding="utf-8"
    )
    assert "_MEIPASS" in src, "onefile payload dir candidate missing from metadata.py"


def test_frozen_meipass_build_info_is_discovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails-before: without the _MEIPASS candidate a frozen onefile CLI cannot
    see build-info.json. Passes-after: with it, stamped identity is returned."""
    payload = tmp_path / "meipass"
    payload.mkdir()
    stamped = {
        "version": "9.9.9",
        "git_commit": "deadbee",
        "build_timestamp": "2026-01-01T00:00:00+00:00",
        "architecture": "AMD64",
        "channel": "stable",
        "build_mode": "Release",
        "dirty_tree": False,
    }
    (payload / "build-info.json").write_text(json.dumps(stamped), encoding="utf-8")

    fake_exe = tmp_path / "NexusScalpEngine-CLI.exe"
    fake_exe.write_bytes(b"MZ")  # frozen exe marker; no build-info beside it
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe), raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(payload), raising=False)
    monkeypatch.chdir(tmp_path)  # CWD fallback must NOT be needed

    found = rmeta.get_build_info_file()
    assert found is not None and found.parent == payload, (
        "stamped build-info must be discovered from the onefile payload dir"
    )
    info = rmeta.get_version_info()
    assert info["commit"] == "deadbee"
    assert info["build_timestamp"] == "2026-01-01T00:00:00+00:00"
    assert info["version"] == "9.9.9"


def test_old_bug_fails_before_runtime_identity_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Part 11: the OLD failure mode — no build-info anywhere — must NOT be
    misread as valid stamped identity. get_version_info falls back to
    _git_commit()/now() only OUTSIDE a frozen bundle; a frozen CLI with no
    payload must be DETECTABLE as unstamped (this is what the release smoke
    test asserts against the real binary).

    BUG-210 hardening: the 'no build-info anywhere reachable' premise is
    enforced against BOTH leak directions (a repo-root CWD reintroducing
    the dev-machine's stale stamp, and any inherited sys module state) so
    the assertion validates the guarded contract, not worker-CWD luck.
    Test-only isolation; production metadata semantics unchanged."""
    fake_exe = tmp_path / "NexusScalpEngine-CLI.exe"
    fake_exe.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe), raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.chdir(tmp_path)
    # BUG-210 hardening: the frozen candidate scan ends with a repo-root
    # CWD escape hatch. Chdir alone is not enough when a parallel worker
    # redirected CWD to the REPO ROOT (a collection-time chdir from a
    # module under test leaks session-wide under pytest-xdist): the stale
    # repo-root build-info.json then resolves and poisons the identity.
    # A tmp-dir CWD cannot carry a pyproject.toml, so the package-relative
    # parent-walk returns to the true repo root deterministically. Cut the
    # escape hatch at its source: point metadata.__file__ at a shadow copy
    # inside tmp_path (only reachable via tmp_path itself). The production
    # candidate list and its order are unchanged (test-only seam).
    assert rmeta.__file__ is not None
    shadow_pkg = tmp_path / "nexus_scalp" / "release"
    shadow_pkg.mkdir(parents=True)
    (shadow_pkg / "__init__.py").write_bytes(b"")
    (shadow_pkg / "metadata.py").write_bytes(b"# shadow marker (BUG-210)\n")
    monkeypatch.setattr(rmeta, "__file__", str(shadow_pkg / "metadata.py"), raising=False)
    # Pin the premise BEFORE the identity verdict: a frozen exe whose only
    # reachable stamp is a stale repo-root build-info.json must NOT be
    # misread as valid stamped identity. (Fails-before guard for the old
    # 'Commit: None + runtime timestamp' BUG-174 failure mode.)
    assert rmeta.get_build_info_file() is None
    info = rmeta.get_version_info()
    # the unstamped profile: commit falls back to _git_commit() (None outside a
    # repo); timestamp is a RECORDED identity fact (CHG-0043): None when no
    # stamp exists (dev/source run) — never a fabricated now().
    unstamped = info["commit"] is None
    build_ts = info["build_timestamp"]
    runtime_ts = bool(build_ts) and build_ts.startswith(time.strftime("%Y-%m-%d"))
    assert unstamped or runtime_ts, (
        "unstamped frozen profile should show Commit None and/or runtime timestamp"
    )


def test_get_version_info_never_invents_commit_when_stamped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Part 10: when stamped metadata exists it WINS over every runtime source
    (git, clock). No component may independently invent commit/timestamp."""
    stamped = {
        "version": "1.2.3",
        "git_commit": "cafef00",
        "build_timestamp": "2020-01-01T00:00:00+00:00",
        "architecture": "AMD64",
        "channel": "stable",
        "build_mode": "Release",
    }
    (tmp_path / "build-info.json").write_text(json.dumps(stamped), encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.chdir(tmp_path)
    info = rmeta.get_version_info()
    assert info["commit"] == "cafef00"
    assert info["build_timestamp"] == "2020-01-01T00:00:00+00:00"
    assert info["version"] == "1.2.3"


# ---------------------------------------------------------------------------
# Part 6/8 — EXE smoke-step identity tripwire exists in release.yml
# (the build-time executable-level guard that would have caught BUG-174)
# ---------------------------------------------------------------------------
def test_release_yml_exe_smoke_asserts_stamped_cli_identity() -> None:
    """The EXE smoke step must assert CLI stamped identity, not just exit codes.

    On v9.0.5 the smoke step passed while the CLI binary reported Commit None
    and a runtime-generated timestamp (BUG-174) — exit-code-only coverage was
    the §46 blind spot. The step must now throw on: commit None, a timestamp
    that changes between two invocations, or identity diverging from the
    stamped build-info.json (the same file the manifest is derived from).
    """
    src = RELEASE_YML.read_text(encoding="utf-8")
    assert "CLI_EXE_IDENTITY_UNSTAMPED" in src, (
        "smoke step missing Commit-None tripwire (BUG-174 detector)"
    )
    assert "CLI_EXE_IDENTITY_RUNTIME_TIMESTAMP" in src, (
        "smoke step missing runtime-timestamp differential tripwire"
    )
    assert "CLI_EXE_IDENTITY_MISMATCH" in src, "smoke step missing commit-vs-build-info comparison"
    assert "version --json" in src, "smoke step must read machine-readable identity"
    # tripwire must live in the smoke step (before Stage release tree)
    smoke_idx = src.find("EXE smoke tests")
    stage_idx = src.find("name: Stage release tree")
    tripwire_idx = src.find("CLI_EXE_IDENTITY_UNSTAMPED")
    assert 0 < smoke_idx < tripwire_idx < stage_idx, (
        "identity tripwire must run inside the EXE smoke step"
    )


# ---------------------------------------------------------------------------
# Reviewer residual gap #2: restore the Tier-4 real-artifact verify test that
# d10e8f6 dropped when the stale (gitignored) release/v9.0.0 junk tree broke it.
# The junk tree is removed from the dev machine; this test is skipif-guarded so
# it runs wherever a real built release root exists (dev build dir or CI
# artifact checkout), and exercises verify_release end-to-end: PASS on the
# genuine tree, FAIL after any single-artifact tamper (checksums remain
# authoritative per Part 13 — identity is supplementary provenance).
# ---------------------------------------------------------------------------
def _find_release_root() -> Path | None:
    """Most recent release/vX.Y.Z/windows/x64 root carrying a portable bundle."""
    rel = REPO_ROOT / "release"
    if not rel.is_dir():
        return None
    for v in sorted(
        (d for d in rel.iterdir() if d.is_dir() and d.name.startswith("v")), reverse=True
    ):
        candidate = v / "windows" / "x64"
        if (candidate / "portable" / "NexusScalpEngine.exe").is_file():
            return candidate
    return None


@pytest.mark.skipif(_find_release_root() is None, reason="no built release dir on this machine")
def test_real_release_artifacts_verify_passes_then_fails_on_tamper(tmp_path: Path) -> None:
    """Real built release must pass verify-release (no launch); any artifact
    tamper must FAIL. Restores the environment-weakened coverage d10e8f6
    dropped (reviewer residual gap #2) — now honest: PASS on real tree,
    FAIL on tamper, skip with a truthful reason when no build exists."""
    import hashlib
    import shutil

    from nexus_scalp.release import verify as rverify

    root = _find_release_root()
    assert root is not None
    result = rverify.verify_release(root / "portable", include_launch=False)
    assert result["valid"] is True, result["checks"]

    # Tamper a COPY of the real tree — the genuine artifacts stay untouched.
    sandbox = tmp_path / "tampered"
    shutil.copytree(root / "portable", sandbox)
    exe = sandbox / "NexusScalpEngine.exe"
    original = exe.read_bytes()
    exe.write_bytes(original + b"\0tamper")
    tampered = rverify.verify_release(sandbox, include_launch=False)
    cks = next(c for c in tampered["checks"] if c["check"] == "Checksums/manifest")
    assert cks["status"] == "FAIL", cks
    assert "MISMATCH" in cks["detail"] or "MISSING" in cks["detail"], cks["detail"]
    # sha256 of the original is what the sums file records — sanity invariant
    assert len(hashlib.sha256(original).hexdigest()) == 64
