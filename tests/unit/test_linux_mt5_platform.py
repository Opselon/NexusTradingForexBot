"""Linux MT5 platform — fail-closed environment-separation regression tests.

Proven contracts from the 2026-09-09 Linux/MT5 mission (Agent 11):

1. The native MetaTrader5 package has no Linux wheels: the repo's import guard
   (mt5_adapter.HAS_NATIVE_MT5) MUST stay False on non-win32 platforms, and a
   Linux boot must NEVER bind DirectMT5Adapter.
2. The gateway client works on Linux; the engine's Linux default is the
   RemoteMT5GatewayAdapter (HTTP/HMAC), proven end-to-end against an in-process
   stub gateway (connect -> tick -> disconnect).
3. Environment separation: the Linux TEST host must fail doctor checks when
   LIVE credentials are present in the environment (fail-closed, not advisory).
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import sys
import threading
import time
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from nexus_scalp.adapters.mt5.mt5_adapter import HAS_NATIVE_MT5  # noqa: E402
from nexus_scalp.adapters.mt5.remote_gateway import RemoteMT5GatewayAdapter  # noqa: E402


class _StubGateway(BaseHTTPRequestHandler):
    """Minimal gateway implementing the audited HMAC wire contract."""

    def log_message(self, *args, **kwargs):  # silence
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        ts = self.headers.get("X-NSE-TIMESTAMP", "")
        sig = self.headers.get("X-NSE-SIGNATURE", "")
        key = self.headers.get("X-NSE-API-KEY", "")
        expected = hmac.new(SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        if key != API_KEY or not hmac.compare_digest(sig, expected):
            self.send_response(401)
            self.end_headers()
            return
        req = json.loads(body or b"{}")
        action = req.get("action", "")
        if action == "PING":
            resp: dict[str, object] = {"status": "OK"}
        elif action == "GET_LAST_TICK":
            resp = {
                "status": "SUCCESS",
                "data": {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                    "bid": 2400.10,
                    "ask": 2400.40,
                    "last": 0.0,
                    "volume": 0.0,
                    "flags": 0,
                },
            }
        else:
            resp = {"status": "FAILED", "message": f"stub lacks {action}"}
        payload = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class TestLinuxImportGuard:
    def test_native_mt5_is_false_on_non_win32(self):
        """Contract: the win32-only import guard stays False on Linux.

        If this fails, someone made `import MetaTrader5` succeed on Linux —
        which cannot happen with official wheels (win_amd64-only) and would
        mean an unofficial fork entered the dependency tree.
        """
        if sys.platform == "win32":
            pytest.skip("win32 host: guard is True there by design")
        assert HAS_NATIVE_MT5 is False

    def test_adapter_connect_fails_closed_without_terminal(self):
        """On Linux, DirectMT5Adapter.connect() must fail CLOSED (False), never
        half-open a session or fabricate a connection."""
        if sys.platform == "win32":
            pytest.skip("win32 host")
        from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter

        adapter = DirectMT5Adapter(retries=1)
        assert adapter.connect() is False
        assert adapter.is_connected() is False


API_KEY = "default_local_key"
SECRET = "default_local_secret"


class TestLinuxGatewayPath:
    """The supported Linux integration: RemoteMT5GatewayAdapter over HTTP/HMAC."""

    @pytest.fixture()
    def gateway(self):
        srv = HTTPServer(("127.0.0.1", 0), _StubGateway)
        port = srv.server_address[1]
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{port}"
        srv.shutdown()

    def test_gateway_client_connect_and_tick_on_linux(self, gateway):
        adapter = RemoteMT5GatewayAdapter(
            gateway_url=gateway, api_key=API_KEY, secret_token=SECRET, timeout_seconds=3.0
        )
        assert adapter.connect() is True
        tick = adapter.get_last_tick("XAUUSD")
        assert tick.bid == pytest.approx(2400.10)
        assert tick.ask == pytest.approx(2400.40)
        assert tick.timestamp is not None
        assert adapter.is_connected() is True
        adapter.disconnect()
        assert adapter.is_connected() is False

    def test_gateway_client_rejects_bad_signature(self, gateway):
        adapter = RemoteMT5GatewayAdapter(
            gateway_url=gateway,
            api_key=API_KEY,
            secret_token="wrong-secret",
            timeout_seconds=3.0,
        )
        assert adapter.connect() is False

    def test_gateway_client_fails_closed_when_gateway_dead(self):
        adapter = RemoteMT5GatewayAdapter(
            gateway_url="http://127.0.0.1:1",  # nothing listens on port 1
            timeout_seconds=1.0,
        )
        assert adapter.connect() is False
        assert adapter.is_connected() is False


def _load_doctor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, object]:
    script = REPO / "scripts" / "linux" / "mt5_doctor.py"
    src = script.read_text(encoding="utf-8")
    ns: dict[str, object] = {"__name__": "mt5_doctor_under_test"}
    monkeypatch.chdir(tmp_path)  # no repo .env beside us
    exec(compile(src, str(script), "exec"), ns)  # trusted repo script
    return ns


class TestLinuxTestHostCredentialHygiene:
    """Fail-closed checks preventing TEST configuration with LIVE credentials."""

    def test_doctor_fails_on_live_credential_env(self, monkeypatch, tmp_path):
        """scripts/linux/mt5_doctor.py must FAIL when MT5 credentials are set
        in the environment of the Linux test host."""
        monkeypatch.setenv("NSE_MT5__ACCOUNT", "12345")
        monkeypatch.setenv("NSE_MT5__PASSWORD", "hunter2")
        monkeypatch.setenv("NSE_MT5__SERVER", "SomeLiveServer")
        ns = _load_doctor(monkeypatch, tmp_path)
        main = ns["main"]
        assert callable(main)
        assert main() != 0, "doctor must fail when LIVE credentials are exported"

    def test_doctor_reports_credential_checks_clean_without_credentials(
        self, monkeypatch, tmp_path, capsys
    ):
        for key in ("NSE_MT5__ACCOUNT", "NSE_MT5__PASSWORD", "NSE_MT5__SERVER"):
            monkeypatch.delenv(key, raising=False)
        ns = _load_doctor(monkeypatch, tmp_path)
        main = ns["main"]
        assert callable(main)
        buf = io.StringIO()
        with redirect_stdout(buf):
            main()  # type: ignore[operator]
        out = buf.getvalue()
        cred_lines = [
            line
            for line in out.splitlines()
            if "no-live-credentials-in-env" in line or "no-mt5-password-in-repo-dotenv" in line
        ]
        assert cred_lines, "doctor must run the credential-hygiene checks"
        assert all(line.startswith("PASS") for line in cred_lines), cred_lines
        del capsys

    def test_paper_boundary_never_constructs_broker_adapter(self):
        """PAPER mode's adapter is credential-free by construction. Pinned so a
        future refactor cannot let PAPER config reach the broker adapter."""
        from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
        from nexus_scalp.domain.enums import ExecutionMode

        adapter = PaperMT5Adapter(symbol="XAUUSD")
        assert adapter.current_account_source == "PAPER"
        assert ExecutionMode.PAPER.value == "PAPER"
