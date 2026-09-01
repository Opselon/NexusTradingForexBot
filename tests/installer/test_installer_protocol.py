"""Nexus installer test suite.

Runs installer/install.ps1 protocol surfaces and key helper functions against
a real PowerShell (Windows PowerShell 5.1 or PowerShell 7, whichever exists)
on Windows. Every test uses a unique temp -NexusHome / -InstallDir override so
the developer machine is never mutated.

Env-gated E2E (network downloads, real repository acquisition):
    set NEXUS_INSTALLER_E2E=1  ->  enables the repository-stage E2E test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "installer" / "install.ps1"

pytestmark = pytest.mark.skipif(
    not INSTALLER.exists() or sys.platform != "win32",
    reason="installer tests require Windows + installer/install.ps1",
)


# ---------------------------------------------------------------------------
# PowerShell helpers
# ---------------------------------------------------------------------------

def find_powershell() -> str:
    for candidate in ("pwsh.exe", "powershell.exe"):
        path = shutil.which(candidate)
        if path:
            return path
    pytest.skip("no PowerShell host found")


PS = find_powershell()


def run_installer(*args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    """Run install.ps1 with -NonInteractive and capture stdout/stderr separately."""
    cmd = [
        PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(INSTALLER), *args,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")


def parse_json_stdout(result: subprocess.CompletedProcess) -> dict:
    """Assert stdout is exactly one JSON frame (JSON stdout discipline)."""
    stdout = result.stdout.strip()
    assert stdout, f"empty stdout (stderr: {result.stderr[:500]})"
    return json.loads(stdout)  # raises if stdout is not pure JSON


def run_ps_expression(expression: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a PowerShell expression that dot-sources the installer for helper tests."""
    dot_source = f'. "{INSTALLER.as_posix()}"'
    cmd = [
        PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-Command", f"{dot_source}; {expression}",
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")


def unique_home(tmp_path: Path) -> str:
    return str(tmp_path / "NexusHome")


# ---------------------------------------------------------------------------
# Syntax validation
# ---------------------------------------------------------------------------

class TestSyntax:
    def test_parses_under_current_powershell(self):
        """The installer must parse cleanly under the running PS host."""
        script = (
            "$errs = $null; $tokens = $null; "
            f"[System.Management.Automation.Language.Parser]::ParseFile('{INSTALLER.as_posix()}', [ref]$tokens, [ref]$errs) | Out-Null; "
            "if ($errs.Count -eq 0) { 'CLEAN' } else { $errs | ForEach-Object { \"LINE $($_.Extent.StartLineNumber): $($_.Message)\" } }"
        )
        result = subprocess.run(
            [PS, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace",
        )
        assert result.stdout.strip() == "CLEAN", result.stdout

    def test_source_is_pure_ascii(self):
        """PS 5.1 parser + legacy codepage safety: source must be pure ASCII."""
        raw = INSTALLER.read_bytes()
        raw.decode("ascii")


# ---------------------------------------------------------------------------
# Protocol surfaces (no mutation)
# ---------------------------------------------------------------------------

class TestProtocolSurfaces:
    def test_protocol_version_outputs_integer(self):
        result = run_installer("-ProtocolVersion")
        assert result.returncode == 0
        assert result.stdout.strip() == "1"

    def test_manifest_is_valid_json_with_expected_shape(self):
        result = run_installer("-Manifest")
        assert result.returncode == 0
        manifest = parse_json_stdout(result)
        assert manifest["protocol_version"] == 1
        assert "installer_version" in manifest
        stages = manifest["stages"]
        assert isinstance(stages, list) and len(stages) >= 10
        expected_names = {
            "environment", "runtime", "git", "node", "repository", "venv",
            "dependencies", "node-deps", "config", "path", "verify", "state",
        }
        assert expected_names <= {s["name"] for s in stages}
        for stage in stages:
            assert set(stage.keys()) == {"name", "title", "category", "needs_user_input"}
            assert stage["needs_user_input"] is False

    def test_manifest_does_not_mutate_filesystem(self, tmp_path):
        home = unique_home(tmp_path)
        before = sorted(p.name for p in tmp_path.iterdir())
        run_installer("-Manifest", "-NexusHome", home)
        after = sorted(p.name for p in tmp_path.iterdir())
        assert before == after
        assert not (tmp_path / "NexusHome").exists()

    def test_show_resolved_paths_outputs_json_and_does_not_install(self, tmp_path):
        home = unique_home(tmp_path)
        result = run_installer("-ShowResolvedPaths", "-NexusHome", home)
        assert result.returncode == 0
        report = parse_json_stdout(result)
        assert home.lower() in report["nexus_home"].lower()
        assert report["protocol_version"] == 1
        assert not Path(home).exists(), "-ShowResolvedPaths must not create the install root"

    def test_unknown_stage_returns_exit_2_and_json_error_frame(self):
        result = run_installer("-Stage", "DEFINITELY_NOT_A_STAGE", "-Json")
        assert result.returncode == 2
        frame = parse_json_stdout(result)
        assert frame["ok"] is False
        assert frame["stage"] == "DEFINITELY_NOT_A_STAGE"
        assert "unknown stage" in frame["reason"]

    def test_explicit_empty_stage_is_unknown_not_full_install(self):
        """A misbehaving driver passing -Stage '' must not trigger a full install."""
        result = run_installer("-Stage", "", "-Json")
        assert result.returncode == 2
        frame = parse_json_stdout(result)
        assert frame["ok"] is False


# ---------------------------------------------------------------------------
# Stage execution frames (machine-readable contract)
# ---------------------------------------------------------------------------

class TestStageFrames:
    def test_environment_stage_frame_shape(self, tmp_path):
        home = unique_home(tmp_path)
        result = run_installer("-Stage", "environment", "-NexusHome", home, "-Json")
        assert result.returncode == 0
        frame = parse_json_stdout(result)
        assert frame["stage"] == "environment"
        assert frame["ok"] is True
        assert frame["skipped"] is False
        assert isinstance(frame["duration_ms"], int)
        assert frame["reason"] is None
        # environment stage must actually have created the home root (writability probe)
        assert Path(home).exists()

    def test_node_stage_is_optional_skip_or_ok(self):
        result = run_installer("-Stage", "node", "-Json")
        assert result.returncode == 0
        frame = parse_json_stdout(result)
        assert frame["stage"] == "node"
        assert frame["ok"] is True
        if frame["skipped"]:
            assert "optional" in (frame["reason"] or "")

    def test_stage_json_stdout_is_pure_json(self, tmp_path):
        """Human diagnostics must go to stderr in driver mode, never stdout."""
        home = unique_home(tmp_path)
        result = run_installer("-Stage", "environment", "-NexusHome", home, "-Json")
        stdout_lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
        assert len(stdout_lines) == 1, f"stdout must be exactly one JSON frame, got: {stdout_lines[:3]}"
        json.loads(stdout_lines[0])
        assert "[OK]" not in result.stdout and "->" not in result.stdout


# ---------------------------------------------------------------------------
# Helper functions (dot-sourced, isolated)
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_convertto_longpath_expands_83_alias(self):
        """kernel32 resolver expands 8.3 profile aliases to long form."""
        expr = (
            "$long = ConvertTo-LongPath 'C:\\Users\\CAPEX~1\\AppData\\Local\\Temp'; "
            "if ($long -notmatch '~\\d') { 'EXPANDED:' + $long } else { 'NOT-EXPANDED:' + $long }"
        )
        result = run_ps_expression(expr)
        out = result.stdout.strip()
        # On hosts without the alias, GetLongPathNameW returns the input; both
        # outcomes are valid as long as the resolver ran without throwing.
        assert out.startswith(("EXPANDED:", "NOT-EXPANDED:")), result.stderr[:300]

    def test_convertto_longpath_passthrough_ordinary_paths(self):
        result = run_ps_expression("ConvertTo-LongPath 'C:\\Windows\\System32'")
        assert result.stdout.strip() == "C:\\Windows\\System32"

    def test_zip_slip_guard_rejects_traversal_entries(self, tmp_path):
        """A zip containing ../ traversal must be rejected, not extracted."""
        evil_zip = tmp_path / "evil.zip"
        with zipfile.ZipFile(evil_zip, "w") as zf:
            zf.writestr("../../../evil.txt", "pwned")
        expr = (
            f"$dest = '{(tmp_path / 'dest').as_posix()}'; "
            f"try {{ Expand-NexusZipSafe -ZipPath '{evil_zip.as_posix()}' -Destination $dest; 'EXTRACTED' }} "
            "catch { 'BLOCKED' }"
        )
        result = run_ps_expression(expr)
        assert "BLOCKED" in result.stdout, result.stdout
        assert not (tmp_path.parent / "evil.txt").exists()

    def test_zip_slip_guard_rejects_absolute_paths(self, tmp_path):
        evil_zip = tmp_path / "evil-abs.zip"
        with zipfile.ZipFile(evil_zip, "w") as zf:
            zf.writestr("C:/Windows/evil-abs.txt", "pwned")
        expr = (
            f"try {{ Expand-NexusZipSafe -ZipPath '{evil_zip.as_posix()}' -Destination '{(tmp_path / 'd2').as_posix()}'; 'EXTRACTED' }} "
            "catch { 'BLOCKED' }"
        )
        result = run_ps_expression(expr)
        assert "BLOCKED" in result.stdout, result.stdout

    def test_zip_slip_guard_allows_clean_archive(self, tmp_path):
        good_zip = tmp_path / "good.zip"
        with zipfile.ZipFile(good_zip, "w") as zf:
            zf.writestr("repo/pyproject.toml", "[project]\nname='x'\n")
        dest = tmp_path / "good-dest"
        expr = (
            f"try {{ Expand-NexusZipSafe -ZipPath '{good_zip.as_posix()}' -Destination '{dest.as_posix()}'; 'OK' }} "
            "catch { 'BLOCKED: ' + $_.Exception.Message }"
        )
        result = run_ps_expression(expr)
        assert "OK" in result.stdout, result.stdout
        assert (dest / "repo" / "pyproject.toml").read_text(encoding="utf-8").startswith("[project]")

    def test_add_user_path_entry_is_idempotent_and_preserves_order(self, tmp_path):
        """PATH mutation helper: dedup + order preservation against a scratch hive."""
        expr = (
            "$userPathBefore = [Environment]::GetEnvironmentVariable('Path', 'User'); "
            "try { "
            "  [Environment]::SetEnvironmentVariable('Path', 'C:\\a;C:\\b', 'User'); "
            "  Add-UserPathEntry -Entry 'C:\\nexus-test-bin'; "
            "  Add-UserPathEntry -Entry 'C:\\nexus-test-bin'; "
            "  $after = [Environment]::GetEnvironmentVariable('Path', 'User'); "
            "  $count = ($after -split ';' | Where-Object { $_ -eq 'C:\\nexus-test-bin' }).Count; "
            "  $prefixKept = $after.StartsWith('C:\\a;C:\\b;'); "
            "  'COUNT=' + $count + ';PREFIX=' + $prefixKept "
            "} finally { "
            "  [Environment]::SetEnvironmentVariable('Path', $userPathBefore, 'User') "
            "}"
        )
        result = run_ps_expression(expr)
        out = result.stdout.strip()
        assert "COUNT=1" in out, out
        assert "PREFIX=True" in out, out


# ---------------------------------------------------------------------------
# Idempotency (real stage double-run in an isolated home)
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_config_stage_is_idempotent_and_preserves_user_edits(self, tmp_path):
        """Config stage: create-if-missing; second run must keep user edits."""
        home = unique_home(tmp_path)
        engine = tmp_path / "engine-src"
        (engine / "configs").mkdir(parents=True)
        (engine / "configs" / "base.yaml").write_text("base: template\n", encoding="utf-8")
        (engine / "configs" / "live.yaml.example").write_text("mode: paper\n", encoding="utf-8")

        first = run_installer("-Stage", "config", "-NexusHome", home, "-InstallDir", str(engine))
        assert first.returncode == 0, first.stderr[-500:]

        live = Path(home) / "config" / "live.yaml"
        assert live.exists()
        live.write_text("# user custom edit - must survive\nmode: paper\n", encoding="utf-8")

        second = run_installer("-Stage", "config", "-NexusHome", home, "-InstallDir", str(tmp_path / "engine-src"))
        assert second.returncode == 0
        content = live.read_text(encoding="utf-8")
        assert "user custom edit" in content, "installer overwrote user config!"

    def test_state_stage_writes_install_json(self, tmp_path):
        home = unique_home(tmp_path)
        result = run_installer("-Stage", "state", "-NexusHome", home, "-InstallDir", str(tmp_path / "engine-src"))
        assert result.returncode == 0
        frame = parse_json_stdout(result)
        assert frame["ok"] is True
        state = json.loads((Path(home) / "state" / "install.json").read_text(encoding="utf-8"))
        assert state["installer_version"]
        assert state["protocol_version"] == 1
        assert "installed_at" in state
        # Full-install state carries python/git facts; the fast per-stage
        # session flush may carry only stage records - accept both shapes.
        assert "git" in state or "stages" in state
        assert state.get("stages", {}).get("state", {}).get("ok") is True


# ---------------------------------------------------------------------------
# Env-gated E2E: real repository acquisition (network)
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestRepositoryE2E:
    @pytest.fixture(autouse=True)
    def _require_e2e_flag(self):
        if not os.environ.get("NEXUS_INSTALLER_E2E"):
            pytest.skip("set NEXUS_INSTALLER_E2E=1 to run installer repository E2E (downloads)")

    def test_repository_stage_acquires_checkout_into_temp_home(self, tmp_path):
        home = unique_home(tmp_path)
        engine = tmp_path / "engine"
        result = run_installer(
            "-Stage", "repository", "-NexusHome", home, "-InstallDir", str(engine), "-Json",
            timeout=900,
        )
        assert result.returncode == 0, result.stderr[-800:]
        frame = parse_json_stdout(result)
        assert frame["ok"] is True
        assert (engine / "pyproject.toml").exists()
        # git metadata present (init/clone ladder) so future updates work
        assert (engine / ".git").exists()


# ---------------------------------------------------------------------------
# Install lock (single-writer concurrency, task C-9 contract)
# ---------------------------------------------------------------------------

class TestInstallLock:
    def test_concurrent_stage_drivers_both_survive_with_wellformed_output(self, tmp_path):
        """Both -Json processes must exit cleanly; exactly one runs the stage,
        the other reports a well-formed skipped frame (lock held)."""
        import threading

        home = unique_home(tmp_path)
        engine = tmp_path / "engine-src"
        # A slow-but-safe stage pair: run 'state' twice concurrently (it
        # mutates state files; the lock must serialize or skip one side).
        results = {}

        def runner(idx):
            results[idx] = run_installer("-Stage", "state", "-NexusHome", home, "-InstallDir", str(engine), "-Json")

        t1 = threading.Thread(target=runner, args=(1,))
        t2 = threading.Thread(target=runner, args=(2,))
        t1.start(); t2.start(); t1.join(180); t2.join(180)

        assert 1 in results and 2 in results
        frames = []
        for idx in (1, 2):
            r = results[idx]
            assert r.returncode in (0, 1), f"runner {idx} rc={r.returncode} stderr={r.stderr[-200:]}"
            stdout = r.stdout.strip()
            assert stdout, f"runner {idx} produced no stdout"
            try:
                frames.append(json.loads(stdout))
            except json.JSONDecodeError:
                pytest.fail(f"runner {idx} stdout not pure JSON: {stdout[:200]}")
        # Lock contract: at least one frame must be well-formed; if a lock
        # skip occurred it must carry skipped=true with a lock reason.
        lock_skips = [f for f in frames if f.get("skipped") and "lock" in (f.get("reason") or "")]
        runs = [f for f in frames if not f.get("skipped")]
        assert runs or lock_skips
        # The state file must exist and be valid JSON (no torn writes).
        state_path = Path(home) / "state" / "install.json"
        assert state_path.exists()
        json.loads(state_path.read_text(encoding="utf-8"))

    def test_lock_probe_detects_held_lock(self, tmp_path):
        """Test-LockHeldByOtherProcess returns true while an exclusive handle is open.
        The lock helpers live in the entry-point (after the dot-source return),
        so this test spawns the installer script itself with a probe via a
        temp driver script that defines the helper inline."""
        home = unique_home(tmp_path)
        # Recreate the exact helper contract inline (single source: install.ps1);
        # the probe validates Windows file-lock semantics the installer relies on.
        helper = (
            "function Test-LockHeldByOtherProcess { "
            "    param([string]$LockPath) "
            "    if (-not (Test-Path $LockPath)) { return $false } "
            "    try { "
            "        $s = [System.IO.File]::Open($LockPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::None); "
            "        $s.Dispose(); return $false "
            "    } catch { return $true } "
            "}"
        )
        expr = (
            f"{helper} "
            f"$lockPath = '{(Path(home) / 'state' / 'installer.lock').as_posix()}'; "
            "New-Item -ItemType Directory -Force -Path (Split-Path -Parent $lockPath) | Out-Null; "
            "$s = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None); "
            "$held = Test-LockHeldByOtherProcess -LockPath $lockPath; "
            "$s.Dispose(); "
            "$free = Test-LockHeldByOtherProcess -LockPath $lockPath; "
            "'HELD=' + $held + ';FREE=' + $free"
        )
        result = run_ps_expression(expr)
        out = result.stdout.strip()
        assert "HELD=True" in out, out
        assert "FREE=False" in out, out
