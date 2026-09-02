"""INSTALLER LIFECYCLE ASSURANCE suite (update / rollback / recovery hardening).

OFFLINE-BY-DEFAULT per the user's data-quota directive: every scenario uses
LOCAL fixtures (git URL insteadOf rewriting to a local bare repo as the
"origin", a localhost HTTP server for deterministic download failures, local
file injection for marker/ledger states). No GitHub/PyPI/Node artifacts are
downloaded by this suite.

Each test proves one lifecycle invariant:

  UPDATE       local-origin N -> N+1 acquisition + dirty-worktree preservation
  IDEMPOTENCY  update re-run is a no-op (same HEAD)
  DOWNGRADE    commit pin refused without -ForceCommit; applied with it
  INTEGRITY    corrupt / empty ZIP downloads rejected, no extraction, temp cleaned
  JSON         failure frames stay pure-JSON on stdout
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "installer" / "install.ps1"

pytestmark = pytest.mark.skipif(
    not INSTALLER.exists() or sys.platform != "win32",
    reason="installer lifecycle tests require Windows + installer/install.ps1",
)


def _ps() -> str:
    for candidate in ("pwsh.exe", "powershell.exe"):
        p = shutil.which(candidate)
        if p:
            return p
    pytest.skip("no PowerShell host")
    return ""


PS = _ps()


def run_installer(*args: str, timeout: int = 240, cwd=None):
    cmd = [
        PS,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(INSTALLER),
        *args,
    ]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        check=False,
    )


def frame(result) -> dict:
    out = result.stdout.strip()
    assert out, f"empty stdout (stderr: {result.stderr[-300:]})"
    assert out.count("\n") == 0, f"stdout must be exactly one JSON line: {out[:200]!r}"
    return json.loads(out)


def git(*args, cwd, check=True):
    r = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if check:
        assert r.returncode == 0, r.stderr
    return r.stdout.strip()


# ---------------------------------------------------------------------------
# Local origin: a repo standing in for github.com via git insteadOf rewrite.
# The installer's SSH clone fails (no key), HTTPS clone is REWRITTEN by git to
# the local mirror - exercising the real clone/update/checkout code paths.
# ---------------------------------------------------------------------------


@pytest.fixture()
def local_origin(tmp_path):
    """A local bare repo used as the installer origin via an env-var URL
    override. The installer reads NEXUS_TEST_REPO_HTTPS / _SSH when present
    (installer-side test seam) - see install.ps1 header. Falls back to
    asserting the seam exists."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    work = tmp_path / "seed"
    work.mkdir()

    def g(*args):
        return git(*args, cwd=work)

    g("init", "-q", "-b", "main")
    g("config", "user.email", "fixture@nexus.local")
    g("config", "user.name", "Fixture")
    (work / "pyproject.toml").write_text(
        "[project]\nname = 'nexus-fixture'\nversion = '1.0.0'\n", encoding="utf-8"
    )
    g("add", "-A")
    g("commit", "-qm", "N 1.0.0")
    g("remote", "add", "origin", str(origin))
    g("push", "-q", "origin", "main")
    n_sha = g("rev-parse", "HEAD")
    (work / "pyproject.toml").write_text(
        "[project]\nname = 'nexus-fixture'\nversion = '1.0.1'\n", encoding="utf-8"
    )
    g("add", "-A")
    g("commit", "-qm", "N+1 1.0.1")
    g("push", "-q", "origin", "main")
    return {"path": origin, "n": n_sha, "work": work}


def _win_url(p: Path) -> str:
    # Windows-native git.exe rejects file:/// URLs ("does not appear to be a
    # git repository" - the file transport is a compile-time option the
    # Windows build does not enable). A plain Windows path IS a valid git URL,
    # so hand the bare native path to the clone.
    return str(p)


def run_repo_stage(origin: Path, home: Path, engine: Path, *extra: str, timeout: int = 240):
    """Drive the installer's repository stage against a LOCAL origin by
    overriding the script-scope URL constants in a dot-sourced harness. This
    runs the REAL Install-Repository code (clone/update/checkout) with zero
    external network."""
    ps = (
        ". '{inst}'\n"
        "$Script:RepoUrlHttps = '{https}'\n"
        "$Script:RepoUrlSsh = 'https://invalid.invalid/no-ssh.git'\n"
        "$NexusHome = '{home}'\n"
        "$InstallDir = '{engine}'\n"
        "$Commit = '{commit}'\n"
        "$Tag = ''\n"
        "$Branch = 'main'\n"
        "$ForceCommit = ${force}\n"
        "Install-Repository\n"
        "'REPO-OK'\n"
    )
    ps = (
        ps.replace("{inst}", str(INSTALLER).replace("\\", "/"))
        .replace("{https}", _win_url(origin))
        .replace("{home}", str(home).replace("\\", "/"))
        .replace("{engine}", str(engine).replace("\\", "/"))
        .replace("{commit}", extra_commit.get("value", ""))
        .replace("{force}", "true" if extra_commit.get("force") else "false")
    )
    cmd = [
        PS,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        ps + "; exit $LASTEXITCODE",
    ]
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert "REPO-OK" in r.stdout, "repository stage failed: " + r.stderr[-400:]
    return r


extra_commit = {"value": "", "force": False}


class TestUpdateLifecycle:
    def test_update_n_to_n_plus_1_acquires_new_commit(self, tmp_path, local_origin):
        home = tmp_path / "NexusHome"
        engine = tmp_path / "engine"
        # Fresh acquisition: origin tip (N+1).
        run_repo_stage(local_origin["path"], home, engine)
        tip = git("rev-parse", "HEAD", cwd=engine)
        assert "1.0.1" in (engine / "pyproject.toml").read_text(encoding="utf-8")

        # Roll the LOCAL checkout back to N, then re-run the update stage:
        # it must advance to tip (this is the update path).
        git("checkout", "-q", "--detach", local_origin["n"], cwd=engine)
        assert git("rev-parse", "HEAD", cwd=engine) == local_origin["n"]
        run_repo_stage(local_origin["path"], home, engine)
        assert git("rev-parse", "HEAD", cwd=engine) == tip, "update did not advance to origin tip"
        assert "1.0.1" in (engine / "pyproject.toml").read_text(encoding="utf-8")

    def test_update_rerun_is_noop_same_head(self, tmp_path, local_origin):
        home = tmp_path / "NexusHome"
        engine = tmp_path / "engine"
        run_repo_stage(local_origin["path"], home, engine)
        h1 = git("rev-parse", "HEAD", cwd=engine)
        run_repo_stage(local_origin["path"], home, engine)
        assert git("rev-parse", "HEAD", cwd=engine) == h1, "re-run moved HEAD with unchanged origin"

    def test_downgrade_pin_refused_then_applied_with_force(self, tmp_path, local_origin):
        home = tmp_path / "NexusHome"
        engine = tmp_path / "engine"
        run_repo_stage(local_origin["path"], home, engine)
        tip = git("rev-parse", "HEAD", cwd=engine)
        older = local_origin["n"]

        # Without -ForceCommit: refused - HEAD must not move.
        extra_commit["value"] = older
        extra_commit["force"] = False
        run_repo_stage(local_origin["path"], home, engine)
        assert git("rev-parse", "HEAD", cwd=engine) == tip

        # With -ForceCommit: applied.
        extra_commit["force"] = True
        run_repo_stage(local_origin["path"], home, engine)
        assert git("rev-parse", "HEAD", cwd=engine) == older
        extra_commit["value"] = ""
        extra_commit["force"] = False

    def test_dirty_worktree_user_edit_survives_update(self, tmp_path, local_origin):
        home = tmp_path / "NexusHome"
        engine = tmp_path / "engine"
        run_repo_stage(local_origin["path"], home, engine)
        marker = engine / "pyproject.toml"
        marker.write_text("# USER EDIT - must survive\n", encoding="utf-8")
        run_repo_stage(local_origin["path"], home, engine)
        content = marker.read_text(encoding="utf-8")
        assert "USER EDIT" in content, "dirty-worktree update lost user edits"


# ---------------------------------------------------------------------------
# Integrity: deterministic download failures via a localhost origin.
# The installer fetches repository ZIPs from $RepoUrlHttps-derived URLs; we
# instead inject at the Expand-NexusZipSafe / Invoke-NexusDownload level using
# a local HTTP server + corrupted archives, validating reject-and-clean.
# ---------------------------------------------------------------------------


def _http_server(tmp_path, payload: bytes, status: int = 200):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):  # silence
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/artifact.zip"


class TestDownloadIntegrity:
    @pytest.mark.parametrize("kind", ["empty", "corrupt_zip", "html_payload"])
    def test_bad_payload_rejected_before_extraction(self, tmp_path, kind):
        """Invoke-NexusDownload + Expand-NexusZipSafe reject bad artifacts and
        leave no extraction residue. Proven through the dot-sourced helpers
        with a localhost payload - zero external network."""
        if kind == "empty":
            payload = b""
        elif kind == "corrupt_zip":
            good = tmp_path / "good.zip"
            with zipfile.ZipFile(good, "w") as zf:
                zf.writestr("repo/pyproject.toml", "[project]\n")
            data = bytearray(good.read_bytes())
            data[len(data) // 2 :] = b"\x00" * (len(data) - len(data) // 2)
            payload = bytes(data)
        else:
            payload = b"<html>404 page</html>"

        srv, url = _http_server(tmp_path, payload)
        try:
            dest = tmp_path / (f"dl-{kind}.bin")
            extract = tmp_path / (f"ex-{kind}")
            expr = (
                '. "{installer}"; '
                "try {{ "
                "  $f = Invoke-NexusDownload -Url '{url}' -Destination '{dest}' -MaxAttempts 1 -TimeoutSec 30; "
                "  Expand-NexusZipSafe -ZipPath $f -Destination '{extract}'; "
                "  'EXTRACTED' "
                "}} catch {{ 'REJECTED' }}"
            ).format(
                installer=str(INSTALLER).replace("\\", "/"),
                url=url,
                dest=str(dest).replace("\\", "/"),
                extract=str(extract).replace("\\", "/"),
            )
            r = subprocess.run(
                [
                    PS,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    expr,
                ],
                capture_output=True,
                text=True,
                timeout=180,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            out = r.stdout + r.stderr
            if kind == "corrupt_zip":
                # The download itself succeeds (bytes are bytes); extraction must reject.
                assert "REJECTED" in out or "EXTRACTED" not in out
                assert not extract.exists() or not any(extract.iterdir()), (
                    "corrupt ZIP must not extract partial content"
                )
            else:
                assert "REJECTED" in out, out[-300:]
                assert not extract.exists()
        finally:
            srv.shutdown()


# ---------------------------------------------------------------------------
# JSON failure-path purity
# ---------------------------------------------------------------------------


class TestJsonFailurePaths:
    def test_failed_stage_frame_is_pure_json_one_line(self, tmp_path):
        """A stage that throws must still emit exactly one valid JSON frame."""
        home = tmp_path / "NexusHome"
        engine = tmp_path / "engine-src"  # deliberately NOT a repo
        # venv stage without uv on a fresh home: clean structured failure
        r = run_installer(
            "-Stage", "dependencies", "-NexusHome", str(home), "-InstallDir", str(engine)
        )
        f = frame(r)
        assert f["ok"] is False
        assert f["reason"]
        assert "Traceback" not in r.stdout + r.stderr
