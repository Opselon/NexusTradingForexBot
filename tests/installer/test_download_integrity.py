"""DOWNLOAD/INSTALL INTEGRITY VERIFICATION suite (INSTALL-VERIFY hardening).

Proves the download->install integrity gate end-to-end with LOCAL fixtures
(localhost HTTP origin, real PowerShell) - zero external network:

  H1  SHA256 match     -> download verified, artifact lands at destination
  H2  SHA256 mismatch  -> BLOCKED (throw), no artifact at destination
  H3  malformed digest -> BLOCKED before any network use of the value
  H4  truncated payload + MinBytes floor -> BLOCKED as truncated
  H5  empty payload    -> BLOCKED (empty check retained)
  H6  HTTP 404 payload -> retried then BLOCKED, no destination file
  H7  unverified path still records telemetry with sha256 of the artifact
  H8  .partial residue never left behind on failure
  H9  state/install.json carries install_state + download_telemetry fields

Failure of any of these means the installer can mistake a corrupt download
for a successful one - the exact defect class this hardening closes.
"""

from __future__ import annotations

import hashlib
import json
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
    reason="installer integrity tests require Windows + installer/install.ps1",
)


def _ps() -> str:
    import shutil

    for candidate in ("pwsh.exe", "powershell.exe"):
        p = shutil.which(candidate)
        if p:
            return p
    pytest.skip("no PowerShell host")
    return ""


PS = _ps()


# ---------------------------------------------------------------------------
# Local HTTP origin serving one deterministic payload
# ---------------------------------------------------------------------------


class _PayloadHandler(BaseHTTPRequestHandler):
    payload = b""
    status = 200

    def do_GET(self):
        self.send_response(self.status)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *a):  # silence
        pass


def _serve(payload: bytes, status: int = 200):
    handler = type("H", (_PayloadHandler,), {"payload": payload, "status": status})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/artifact.bin"


def run_download(tmp_path: Path, url: str, dest: Path, *extra: str, max_attempts: int = 2):
    """Drive the REAL Invoke-NexusDownload through a dot-sourced installer."""
    expr = (
        '. "{inst}"; '
        "try {{ "
        "  $f = Invoke-NexusDownload -Url '{url}' -Destination '{dest}' "
        "        -MaxAttempts {max_attempts} -TimeoutSec 60 {extra}; "
        "  'DOWNLOADED=' + $f; "
        "  $t = $Script:DownloadTelemetry | Select-Object -Last 1; "
        "  'TELEMETRY=' + ($t | ConvertTo-Json -Compress) "
        "}} catch {{ "
        "  'BLOCKED=' + $_.Exception.Message; "
        "  $t = $Script:DownloadTelemetry | Select-Object -Last 1; "
        "  if ($t) {{ 'TELEMETRY=' + ($t | ConvertTo-Json -Compress) }} "
        "}}"
    ).format(
        inst=str(INSTALLER).replace("\\", "/"),
        url=url,
        dest=str(dest).replace("\\", "/"),
        max_attempts=max_attempts,
        extra=" ".join(extra),
    )
    return subprocess.run(
        [PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", expr],
        capture_output=True,
        text=True,
        timeout=240,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


class TestSha256Verification:
    def test_matching_digest_downloads_and_verifies(self, tmp_path):
        payload = b"NEXUS integrity fixture" * 100
        digest = hashlib.sha256(payload).hexdigest()
        srv, url = _serve(payload)
        try:
            dest = tmp_path / "ok.bin"
            r = run_download(tmp_path, url, dest, "-ExpectedSha256", digest)
            out = r.stdout
            assert "DOWNLOADED=" in out, out + r.stderr[-400:]
            assert dest.read_bytes() == payload
            # Telemetry must record the verified outcome with the digest.
            tel_line = next((ln for ln in out.splitlines() if ln.startswith("TELEMETRY=")), "")
            assert tel_line, out
            tel = json.loads(tel_line[len("TELEMETRY=") :])
            assert tel["outcome"] == "ok"
            assert tel["verification"] == "sha256"
            assert tel["expected_sha"] == digest
            assert tel["actual_sha"] == digest
        finally:
            srv.shutdown()

    def test_mismatched_digest_is_blocked_with_no_artifact(self, tmp_path):
        payload = b"NEXUS integrity fixture" * 100
        wrong_digest = "0" * 64
        srv, url = _serve(payload)
        try:
            dest = tmp_path / "tampered.bin"
            r = run_download(tmp_path, url, dest, "-ExpectedSha256", wrong_digest, max_attempts=1)
            out = r.stdout
            assert "BLOCKED=" in out, out + r.stderr[-400:]
            assert "MISMATCH" in out, out
            assert not dest.exists(), (
                "a digest-mismatched artifact must never land at the destination"
            )
            tel_line = next((ln for ln in out.splitlines() if ln.startswith("TELEMETRY=")), "")
            if tel_line:
                tel = json.loads(tel_line[len("TELEMETRY=") :])
                assert tel["outcome"] == "blocked"
        finally:
            srv.shutdown()

    def test_malformed_digest_is_blocked(self, tmp_path):
        payload = b"data"
        srv, url = _serve(payload)
        try:
            dest = tmp_path / "bad-digest.bin"
            r = run_download(tmp_path, url, dest, "-ExpectedSha256", "deadbeef", max_attempts=1)
            assert "BLOCKED=" in r.stdout, r.stdout + r.stderr[-400:]
            assert "64-hex" in r.stdout
            assert not dest.exists()
        finally:
            srv.shutdown()

    def test_truncated_payload_blocked_by_min_bytes_floor(self, tmp_path):
        payload = b"short"
        srv, url = _serve(payload)
        try:
            dest = tmp_path / "truncated.bin"
            r = run_download(tmp_path, url, dest, "-MinBytes", "10000", max_attempts=1)
            out = r.stdout
            assert "BLOCKED=" in out, out + r.stderr[-400:]
            assert "minimum" in out or "truncated" in out
            assert not dest.exists()
        finally:
            srv.shutdown()

    def test_empty_payload_still_blocked(self, tmp_path):
        srv, url = _serve(b"")
        try:
            dest = tmp_path / "empty.bin"
            r = run_download(tmp_path, url, dest, max_attempts=1)
            assert "BLOCKED=" in r.stdout, r.stdout + r.stderr[-400:]
            assert not dest.exists()
        finally:
            srv.shutdown()

    def test_http_error_status_blocked_and_retried(self, tmp_path):
        srv, url = _serve(b"not found", status=404)
        try:
            dest = tmp_path / "http404.bin"
            r = run_download(tmp_path, url, dest, max_attempts=2)
            assert "BLOCKED=" in r.stdout, r.stdout + r.stderr[-400:]
            assert not dest.exists()
            # .partial residue must be cleaned on every failure path.
            partials = list(tmp_path.glob("http404.bin.partial-*"))
            assert not partials, f"partial residue left behind: {partials}"
        finally:
            srv.shutdown()

    def test_unverified_download_records_telemetry_with_actual_sha(self, tmp_path):
        payload = b"unverified-but-recorded" * 10
        digest = hashlib.sha256(payload).hexdigest()
        srv, url = _serve(payload)
        try:
            dest = tmp_path / "unverified.bin"
            r = run_download(tmp_path, url, dest)
            assert "DOWNLOADED=" in r.stdout, r.stdout + r.stderr[-400:]
            tel_line = next((ln for ln in r.stdout.splitlines() if ln.startswith("TELEMETRY=")), "")
            assert tel_line
            tel = json.loads(tel_line[len("TELEMETRY=") :])
            assert tel["outcome"] == "ok"
            assert tel["verification"] in ("empty-check", "size")
            assert tel["actual_sha"] == digest, (
                "telemetry must record the artifact digest even when unpinned"
            )
        finally:
            srv.shutdown()


class TestStateTelemetryFields:
    def test_state_ledger_carries_install_state_and_telemetry_fields(self, tmp_path):
        """The state stage's install.json must carry the truth-table summary
        field (install_state) and the download telemetry array (possibly
        empty) - the machine-readable contract for AVAILABLE/DEGRADED/BLOCKED
        reporting and per-download digests."""
        home = tmp_path / "NexusHome"
        engine = tmp_path / "engine-src"
        expr = (
            '. "{inst}"; '
            "$NexusHome = '{home}'; "
            "$InstallDir = '{engine}'; "
            "Write-InstallState -LastStage 'state'; "
            "'STATE-WRITTEN'"
        ).format(
            inst=str(INSTALLER).replace("\\", "/"),
            home=str(home).replace("\\", "/"),
            engine=str(engine).replace("\\", "/"),
        )
        r = subprocess.run(
            [PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", expr],
            capture_output=True,
            text=True,
            timeout=180,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        assert "STATE-WRITTEN" in r.stdout, r.stdout + r.stderr[-400:]
        state = json.loads((home / "state" / "install.json").read_text(encoding="utf-8"))
        # No stages ran in this session => honest UNKNOWN, never a fake AVAILABLE.
        assert state.get("install_state") == "UNKNOWN"
        assert hasattr(state, "get") and "download_telemetry" in state
        assert isinstance(state["download_telemetry"], list)


class TestChecksumManifestProbe:
    """Manual checksum probe contract: the helper must agree with an
    independent Python-side SHA256 of the same file (the 'manual probe' the
    verification plan calls for)."""

    def test_get_file_sha256_matches_python_hashlib(self, tmp_path):
        payload = bytes(range(256)) * 512  # 128 KiB deterministic blob
        target = tmp_path / "probe.bin"
        target.write_bytes(payload)
        expected = hashlib.sha256(payload).hexdigest()
        expr = (". \"{inst}\"; $h = Get-FileSha256 -Path '{p}'; 'HASH=' + $h").format(
            inst=str(INSTALLER).replace("\\", "/"), p=str(target).replace("\\", "/")
        )
        r = subprocess.run(
            [PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", expr],
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        out = r.stdout.strip()
        assert out.startswith("HASH="), r.stdout + r.stderr[-300:]
        actual = out[len("HASH=") :].strip().lower()
        assert actual == expected, f"installer hash {actual} != python hash {expected}"

    def test_zip_artifact_with_pinned_digest_roundtrip(self, tmp_path):
        """A ZIP artifact verified against a pre-computed digest then
        extracted: the full download->verify->extract chain in one flow."""
        good = tmp_path / "src.zip"
        with zipfile.ZipFile(good, "w") as zf:
            zf.writestr("repo/pyproject.toml", "[project]\nname = 'nexus'\n")
        payload = good.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        srv, url = _serve(payload)
        try:
            dest = tmp_path / "roundtrip.zip"
            extract = tmp_path / "roundtrip-extract"
            expr = (
                '. "{inst}"; '
                "try {{ "
                "  $f = Invoke-NexusDownload -Url '{url}' -Destination '{dest}' "
                "        -MaxAttempts 1 -TimeoutSec 60 -ExpectedSha256 '{digest}'; "
                "  Expand-NexusZipSafe -ZipPath $f -Destination '{extract}'; "
                "  'CHAIN-OK'"
                "}} catch {{ 'CHAIN-BLOCKED=' + $_.Exception.Message }}"
            ).format(
                inst=str(INSTALLER).replace("\\", "/"),
                url=url,
                dest=str(dest).replace("\\", "/"),
                digest=digest,
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
            assert "CHAIN-OK" in r.stdout, r.stdout + r.stderr[-400:]
            assert (extract / "repo" / "pyproject.toml").exists()
        finally:
            srv.shutdown()
