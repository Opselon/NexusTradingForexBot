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


# GO-API-SHIP regression: the .iss asserts _internal\go-api\nexus-api.exe exists
# (contract assertion, not a skipifsourcedoesntexist), so the release workflow
# MUST bake the binary. v9.0.15 died at ISCC because the workflow never built
# the Go plane — build_release.ps1 did, release.yml did not.
def test_release_yml_bakes_the_go_api_plane() -> None:
    """The workflow must build nexus-api.exe and add it to the onedir payload."""
    src = RELEASE_YML.read_text(encoding="utf-8")

    build_idx = src.find("Build Go API plane")
    assert build_idx > 0, "the Go API build step was removed from release.yml"

    pyinstaller_idx = src.find("name: PyInstaller onedir")
    assert pyinstaller_idx > build_idx, "the Go build must run before PyInstaller"

    adddata = src[pyinstaller_idx : src.find("name: PyInstaller onefile CLI")]
    assert "go-api" in adddata, (
        "the onedir build no longer add-data's the go-api ship dir — ISCC would "
        "then fail on the .iss go-api contract entry"
    )


def test_release_yml_go_build_uses_the_ship_dir_the_adddata_references() -> None:
    """The build step's output dir must be exactly the dir add-data consumes."""
    src = RELEASE_YML.read_text(encoding="utf-8")
    build_step = src[src.find("Build Go API plane") :]
    # The build step runs with working-directory: go-api, so a $PWD-relative
    # ship dir lands under go-api/... while PyInstaller (rooted at the repo
    # root) consumes $PWD\release\build\... — the paths must be reconciled
    # through GITHUB_WORKSPACE, not $PWD.
    has_wd = "working-directory: go-api" in build_step
    # the anchor must be a repo-rooted variable ($env:GITHUB_WORKSPACE or an
    # explicit $RepoRoot over it) — never bare $PWD under that working dir
    ship = re.search(r'Join-Path (\$\S+) "([^"]*go-api)"', build_step)
    assert ship, "could not locate the go-api ship dir in the build step"
    anchor, ship_dir = ship.group(1), ship.group(2)
    onedir = src[src.find("name: PyInstaller onedir") :]
    consume = re.search(r"--add-data [`\"]?\$PWD\\([^;`\" ]*go-api)", onedir)
    assert consume, "could not locate the go-api add-data consumer in the onedir build"
    if has_wd:
        # under working-directory: go-api the ship dir MUST be anchored at the
        # workspace root: $PWD there is the go-api module dir, so the same
        # relative path lands under go-api/ and PyInstaller never sees it.
        # An indirection ($RepoRoot = $env:GITHUB_WORKSPACE) is fine as long
        # as the assignment is itself rooted.
        if "GITHUB_WORKSPACE" not in anchor:
            assignment = re.search(r"(\$\S+)\s*=\s*(.*)", build_step.split("Join-Path")[0])
            assert assignment and "GITHUB_WORKSPACE" in assignment.group(2), (
                f"build runs under working-directory: go-api but anchors the ship "
                f"dir at {anchor!r} — that resolves to go-api/... and PyInstaller "
                f"never finds the dir it add-data's"
            )
    assert ship_dir.replace("\\", "/") == consume.group(1).replace("\\", "/"), (
        f"build writes to {ship_dir!r} but PyInstaller consumes {consume.group(1)!r}"
    )


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


# ---------------------------------------------------------------------------
# DevOps hardening (v9.0.15 cycle): the two silent-pass gates fixed in this PR.
#
# 1. clean_install_test.ps1 used to `exit 0` when the setup exe was absent,
#    which contradicted the workflow's own BUG293_CLEAN_INSTALL_FAILED gate:
#    a producer step that silently failed to emit the installer would publish
#    a release whose lifecycle test never ran at all.
# 2. The post-publish verify step only checked that the release EXISTED and had
#    at least one asset. A short or tampered asset list passed.
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[2]


def _read_clean_install_script() -> str:
    p = _REPO / "scripts" / "build" / "clean_install_test.ps1"
    assert p.is_file(), f"clean_install_test.ps1 missing at {p}"
    return p.read_text(encoding="utf-8")


def test_clean_install_test_does_not_silently_skip_a_missing_setup_exe() -> None:
    """BUG-293-QUIET: absence of the setup exe must FAIL the release, not skip."""
    text = _read_clean_install_script()
    guard = text.split("Resolve-Path", 1)[0]
    # The old silent-skip guard is gone ...
    assert re.search(r"if\s*\(\s*-not\s+\$SetupExe\s+-or\s+-not\s*\(Test-Path", guard) is None
    # ... and both absence branches now exit non-zero.
    assert re.search(r"if\s*\(\s*-not\s+\$SetupExe\s*\)\s*{[^}]*exit\s+2", guard, re.S)
    assert re.search(r"if\s*\(\s*-not\s*\(Test-Path\s+\$SetupExe\)\)\s*{[^}]*exit\s+3", guard, re.S)
    # No executed `exit 0` remains before the script starts real work. Only the
    # code lines matter — the comment block may quote the removed behaviour.
    code_lines = [ln for ln in guard.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert not any(re.search(r"exit\s+0", ln) for ln in code_lines), code_lines


def test_release_yml_post_publish_verify_checks_asset_contract() -> None:
    """The post-publish check must assert the asset CONTRACT, not just existence."""
    yml = (_REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    step_idx = yml.find("Verify published release")
    assert step_idx != -1
    step = yml[step_idx : yml.find("env:", step_idx)]
    assert "TAG MISMATCH" in step
    assert "ASSET COUNT" in step
    assert "MISSING ASSET" in step
    assert "ZERO-SIZE ASSET" in step
    assert "DIGEST MISMATCH" in step
    # The 7 contract assets must be named explicitly.
    for name in (
        "setup.exe",
        "win-x64.zip",
        "NexusScalpEngine-CLI.exe",
        "release-manifest.json",
        "sbom.spdx.json",
        "SHA256SUMS.txt",
        "update-manifest.signed.json",
    ):
        assert name in step, f"post-publish verify does not assert {name}"


# ---------------------------------------------------------------------------
# Round 2 of the v9.0.15 audit (three independent audit reports):
#
# F1 (user-visible): the portable zip shipped WITHOUT SHA256SUMS.txt, so a
#   user who extracted the zip and ran `verify-release` saw
#   "SHA256SUMS.txt missing". Only the manifest was embedded; the sums file
#   lived in checksums/ OUTSIDE the archive.
# F3: the post-publish check used `<` instead of `!=` (extra stale assets
#   from a previous run passed) and did not require every expected asset to
#   appear in SHA256SUMS.txt (a sums file carrying a previous version's
#   digests passed).
# F4 (dangerous): `Undraft release` ran `if: always()`, so a PARTIAL upload
#   failure still un-drafted (published) the incomplete release.
# ---------------------------------------------------------------------------


def test_embed_step_puts_the_checksums_file_inside_the_portable_tree() -> None:
    """F1: the portable bundle must carry SHA256SUMS.txt, not just the manifest.

    The contract is WRITTEN portable-scoped by the shared helper (not copied
    from the release root — see the F1/F2 regression test below) and the zip is
    rebuilt so the archive carries it.
    """
    yml = (_REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    idx = yml.find("Embed release contract in portable bundle")
    assert idx != -1, "embed step renamed/removed"
    step = yml[idx : yml.find("Pre-stage verification contract for installer", idx)]
    # The bundle contract is written by the shared helper, portable-rooted.
    assert "write_embedded_contract" in step, step
    # ... and the zip is rebuilt, because the archive built at the Stage step
    # predates both contract files.
    assert "Compress-Archive" in step
    assert "PORTABLE_ZIP_REBUILD_FAILED" in step


def test_embed_step_runs_before_anything_hashes_the_zip() -> None:
    """F1 ORDERING: the zip must be final before its digest is recorded.

    Regression: the embed+rezip used to run AFTER the Checksums step, so
    SHA256SUMS.txt recorded the digest of a zip that had already been replaced
    -> the post-publish DIGEST contract failed on every release.
    """
    yml = (_REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    stage_idx = yml.find("name: Stage release tree")
    embed_idx = yml.find("Embed release contract in portable bundle")
    sums_idx = yml.find("name: Checksums + manifest + SBOM")
    assert 0 < stage_idx < embed_idx < sums_idx, (
        "the embed+rezip step must run after Staging and BEFORE the checksums step"
    )
    assert "Compress-Archive" not in yml[sums_idx:], (
        "nothing may rebuild the zip after the checksums step"
    )


def test_embedded_contract_is_portable_scoped_not_the_release_root_copy() -> None:
    """F2/F4: the bundle contract must list only files the bundle contains.

    Regression: the release-root contract (cli/..., *.zip, *-setup.exe) was
    copied into the portable tree, so `verify-release` on an extracted zip — and
    on the installed tree, which IS that payload — reported
    "checksum: ... MISSING" / "manifest file missing: ...".
    """
    yml = (_REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    idx = yml.find("Pre-stage verification contract for installer")
    pre = yml[idx : yml.find("name: Installer (Inno Setup)", idx)]
    # The pre-stage step must RELOCATE the bundle contract, never author a
    # release-root one.
    assert "Copy-Item $PortableSums" in pre and "Copy-Item $PortableManifest" in pre, pre
    assert "$Subset" not in pre, "the release-root 3-artifact subset must be gone"
    # Release-root sums are still produced for publication, but only by the
    # Checksums step (4 artifacts, incl. setup.exe).
    assert '"$Out\\NexusScalpEngine-$V-win-x64-setup.exe"' in yml


def test_published_sums_covers_the_metadata_assets() -> None:
    """F3: the strict coverage rule needs the metadata assets in SHA256SUMS.

    Regression: the gate requires every published asset to appear in
    SHA256SUMS.txt, but the sums was generated over the 4 payload binaries only
    — release-manifest.json / sbom.spdx.json / update-manifest.signed.json were
    never covered, so the gate could not pass.
    """
    yml = (_REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    idx = yml.find("Finalize SHA256SUMS coverage")
    assert idx != -1, "the SHA256SUMS coverage step is missing"
    step = yml[idx : yml.find("verify-release (full self-check", idx)]
    for asset in ("release-manifest.json", "sbom.spdx.json", "update-manifest.signed.json"):
        assert asset in step, f"{asset} is not covered by the sums step"
    assert "Add-Content" in step
    # It must run AFTER signing (that file does not exist before) and BEFORE the
    # tree self-check / upload.
    sign_idx = yml.find("Sign update manifest (Ed25519 trust root)")
    verify_idx = yml.find("verify-release (full self-check")
    assert 0 < sign_idx < idx < verify_idx, "coverage step ordering changed"


def test_release_yml_undraft_only_runs_when_publish_actually_failed() -> None:
    """F4: undraft must not publish a release whose upload failed."""
    yml = (_REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    idx = yml.find("Undraft release")
    assert idx != -1
    step = yml[idx : yml.find("shell:", idx)]
    # `if: always()` would run even on a successful publish; the step must be
    # gated on the publish step having failed.
    assert "always()" not in step, step
    assert "steps.publish.outcome == 'failure'" in step
    # and the publish step must carry the id the guard references
    assert re.search(r"name: Publish release\s*\n\s*id: publish", yml)


def test_post_publish_verify_requires_exact_asset_count() -> None:
    """F3a: `!=` not `<` — stale assets from a previous run must fail."""
    yml = (_REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    idx = yml.find("Verify published release")
    step = yml[idx : yml.find("env:", idx)]
    assert "len(published) != len(expected)" in step
    assert "UNCHECKED ASSET" in step
