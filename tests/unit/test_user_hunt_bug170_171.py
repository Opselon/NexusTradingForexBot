"""Regression net for the user-side bug hunt batch (BUG-170 / BUG-171).

BUG-170 — `nexus start` pidfile claim race (TOCTOU): two concurrent
_spawn_daemon() calls must spawn AT MOST ONE engine.

BUG-171 — SafeDownloader resume: a server/proxy that ignores the Range header
(200 + full body for a bytes=N- request) must NOT be appended onto the .part
prefix; the downloader restarts from zero so SHA verification converges.
"""

from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from nexus_scalp.release.updater import SafeDownloader


# ---------------------------------------------------------------------------
# BUG-171: Range-ignoring server must not corrupt resumed downloads
# ---------------------------------------------------------------------------
def test_bug171_range_ignoring_server_download_converges(tmp_path: Path) -> None:
    payload = (b"0123456789abcdef" * 200)[:4096]
    sha = hashlib.sha256(payload).hexdigest()

    class RangeIgnorer(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            # ALWAYS 200 + full body, even when Range is present (broken proxy).
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    srv = HTTPServer(("127.0.0.1", 0), RangeIgnorer)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        dl = SafeDownloader(tmp_path)
        part = tmp_path / "artifact.bin.part"
        part.write_bytes(payload[:1000])  # 1000 bytes from an interrupted try
        out = dl.download(
            f"http://127.0.0.1:{port}/artifact.bin", "artifact.bin", expected_sha256=sha
        )
        assert hashlib.sha256(out.read_bytes()).hexdigest() == sha
        assert out.read_bytes() == payload
    finally:
        srv.shutdown()
        thread.join(timeout=5)


def test_bug171_true_resume_still_appends_and_verifies(tmp_path: Path) -> None:
    """Guard the guard: a CORRECT 206 resume keeps the append path (BUG-122)."""
    payload = (b"abcdefgh" * 256)[:2048]
    sha = hashlib.sha256(payload).hexdigest()
    prefix = payload[:1024]

    class RangeHonorer(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            rng = self.headers.get("Range", "")
            start = int(rng.split("=")[1].split("-")[0]) if rng else 0
            if start > 0:
                body = payload[start:]
                self.send_response(206)
                self.send_header(
                    "Content-Range", f"bytes {start}-{len(payload) - 1}/{len(payload)}"
                )
            else:
                body = payload
                self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    srv = HTTPServer(("127.0.0.1", 0), RangeHonorer)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        dl = SafeDownloader(tmp_path)
        part = tmp_path / "artifact2.bin.part"
        part.write_bytes(prefix)
        out = dl.download(
            f"http://127.0.0.1:{port}/artifact2.bin", "artifact2.bin", expected_sha256=sha
        )
        assert hashlib.sha256(out.read_bytes()).hexdigest() == sha
        assert out.read_bytes() == payload
    finally:
        srv.shutdown()
        thread.join(timeout=5)


def test_bug171_corrupt_full_body_still_fails_sha(tmp_path: Path) -> None:
    """A 200 full body that does not match the expected hash must FAIL loudly
    (no silent accept) — the fix only changes resume semantics, not trust."""
    payload = b"WRONG-PAYLOAD" * 64

    class Plain200(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    srv = HTTPServer(("127.0.0.1", 0), Plain200)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        dl = SafeDownloader(tmp_path)
        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            dl.download(
                f"http://127.0.0.1:{port}/artifact3.bin",
                "artifact3.bin",
                expected_sha256=hashlib.sha256(b"expected").hexdigest(),
            )
    finally:
        srv.shutdown()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# BUG-170: concurrent _spawn_daemon claims exactly one pidfile
# ---------------------------------------------------------------------------
def test_bug170_concurrent_spawn_claims_single_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import nexus_scalp.cli.main as cmain
    import nexus_scalp.release.paths as rpaths

    monkeypatch.setattr(rpaths, "get_data_root", lambda: tmp_path)
    spawned: list[list[str]] = []

    def fake_popen(cmd: list[str], **kwargs: object) -> None:
        spawned.append(list(cmd))

    monkeypatch.setattr(cmain.subprocess, "Popen", fake_popen)

    barrier = threading.Barrier(2)

    def racer() -> None:
        barrier.wait()
        cmain._spawn_daemon(["python", "-c", "pass", "--mode", "paper", "x"])

    t1 = threading.Thread(target=racer)
    t2 = threading.Thread(target=racer)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(spawned) <= 1, f"BUG-170 race: {len(spawned)} engines spawned by concurrent starts"
    pidfile = tmp_path / "nexus.pid"
    assert pidfile.exists(), "winner must own the pidfile"


def test_bug170_second_start_after_claim_reports_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import nexus_scalp.cli.main as cmain
    import nexus_scalp.release.paths as rpaths

    monkeypatch.setattr(rpaths, "get_data_root", lambda: tmp_path)
    spawned: list[list[str]] = []
    monkeypatch.setattr(
        cmain.subprocess, "Popen", lambda cmd, **kw: spawned.append(list(cmd)) or None
    )

    # First start claims + spawns.
    cmain._spawn_daemon(["python", "-c", "pass", "--mode", "paper", "x"])
    assert len(spawned) == 1

    # Second start while the first pid is ALIVE (this process) must decline.
    import contextlib
    import io

    from typer.testing import CliRunner

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmain._spawn_daemon(["python", "-c", "pass", "--mode", "paper", "x"])
    assert len(spawned) == 1, "second start must NOT spawn a second engine"


def test_bug170_stale_pidfile_is_reclaimed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import nexus_scalp.cli.main as cmain
    import nexus_scalp.release.paths as rpaths

    monkeypatch.setattr(rpaths, "get_data_root", lambda: tmp_path)
    spawned: list[list[str]] = []
    monkeypatch.setattr(
        cmain.subprocess, "Popen", lambda cmd, **kw: spawned.append(list(cmd)) or None
    )

    pidfile = tmp_path / "nexus.pid"
    pidfile.write_text("999999", encoding="utf-8")  # dead pid
    cmain._spawn_daemon(["python", "-c", "pass", "--mode", "paper", "x"])
    assert len(spawned) == 1, "stale pidfile must be reclaimed, not block startup"
    assert pidfile.read_text(encoding="utf-8").strip() == str(cmain.os.getpid())
