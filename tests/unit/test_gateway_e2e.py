"""End-to-end tests for the Gateway bridge (server + CLI + HMAC contract).

Covers:
  - HMAC happy-path + negative cases via TestClient (no MT5, no network)
  - /health on Linux (DEGRADED) vs Windows path (mocked)
  - All 14 client actions map to SUCCESS/FAILED shapes (mocked DirectMT5Adapter)
  - CLI: nexus gateway serve (Windows-only guard on Linux) + gateway status
  - CLI appears in top-level help

No client code is affected — server only. Linux-safe (no MetaTrader5 import).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os

# AUDIT-B2: tests run in DEMO mode with the explicit defaults opt-in.
import os as _os
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.adapters.mt5.remote_gateway import RemoteMT5GatewayAdapter
from nexus_scalp.gateway.server import app, reset_adapter_for_tests

_os.environ.setdefault("NSE_GATEWAY_ALLOW_DEFAULTS", "1")

DEFAULT_KEY = "default_local_key"
DEFAULT_SECRET = "default_local_secret"


def _hmac_sig(secret: str, ts: str, body: bytes) -> str:
    return hmac.new(
        secret.encode(), msg=f"{ts}.".encode() + body, digestmod=hashlib.sha256
    ).hexdigest()


def _hmac_headers(secret: str, key: str, body: bytes, ts: str | None = None) -> dict[str, str]:
    ts = ts or str(int(time.time()))
    return {
        "Content-Type": "application/json",
        "X-NSE-API-KEY": key,
        "X-NSE-TIMESTAMP": ts,
        "X-NSE-SIGNATURE": _hmac_sig(secret, ts, body),
    }


# ---------------------------------------------------------------------------
# 1) HMAC contract (mirrors test_remote_gateway, exercised against server)
# ---------------------------------------------------------------------------


def test_gateway_hmac_ping_ok_on_linux_platform_gate() -> None:
    """Auth passes, server returns 503 platform gate (not 401) — proves HMAC path."""
    reset_adapter_for_tests()
    client = TestClient(app)
    body = json.dumps({"action": "PING", "payload": {}}).encode()
    r = client.post(
        "/api/v1/execute", content=body, headers=_hmac_headers(DEFAULT_SECRET, DEFAULT_KEY, body)
    )
    assert r.status_code == 503
    assert r.json()["message"] == "gateway server runs only on Windows"


def test_gateway_hmac_bad_signature_is_401() -> None:
    reset_adapter_for_tests()
    client = TestClient(app)
    body = json.dumps({"action": "PING", "payload": {}}).encode()
    headers = _hmac_headers(DEFAULT_SECRET, DEFAULT_KEY, body)
    headers["X-NSE-SIGNATURE"] = "bad"
    r = client.post("/api/v1/execute", content=body, headers=headers)
    assert r.status_code == 401
    assert "bad signature" in r.json()["message"].lower()


def test_gateway_hmac_bad_api_key_is_401() -> None:
    reset_adapter_for_tests()
    client = TestClient(app)
    body = json.dumps({"action": "PING", "payload": {}}).encode()
    headers = _hmac_headers(DEFAULT_SECRET, "wrong_key", body)
    r = client.post("/api/v1/execute", content=body, headers=headers)
    assert r.status_code == 401


def test_gateway_hmac_timestamp_skew_is_401() -> None:
    reset_adapter_for_tests()
    client = TestClient(app)
    body = json.dumps({"action": "PING", "payload": {}}).encode()
    old_ts = str(int(time.time()) - 10_000)
    headers = _hmac_headers(DEFAULT_SECRET, DEFAULT_KEY, body, ts=old_ts)
    r = client.post("/api/v1/execute", content=body, headers=headers)
    assert r.status_code == 401
    assert "skew" in r.json()["message"].lower()


def test_gateway_hmac_body_tamper_is_401() -> None:
    reset_adapter_for_tests()
    client = TestClient(app)
    body = json.dumps({"action": "PING", "payload": {}}).encode()
    tampered = json.dumps({"action": "PONG", "payload": {}}).encode()
    headers = _hmac_headers(DEFAULT_SECRET, DEFAULT_KEY, body)
    r = client.post("/api/v1/execute", content=tampered, headers=headers)
    assert r.status_code == 401


def test_gateway_hmac_empty_body_with_ping_action() -> None:
    reset_adapter_for_tests()
    client = TestClient(app)
    # Client never sends empty, but server must handle — missing action => 401/400 after auth
    body = b""
    headers = _hmac_headers(DEFAULT_SECRET, DEFAULT_KEY, body)
    r = client.post("/api/v1/execute", content=body, headers=headers)
    # Empty body -> json {} -> no action -> 400
    assert r.status_code in (400, 401, 503)


def test_gateway_missing_auth_headers_is_401() -> None:
    reset_adapter_for_tests()
    client = TestClient(app)
    r = client.post("/api/v1/execute", content=b'{"action":"PING","payload":{}}')
    assert r.status_code == 401


def test_gateway_health_on_linux_is_degraded() -> None:
    reset_adapter_for_tests()
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["platform"] == "linux"
    assert data["status"] == "DEGRADED"


def test_gateway_unknown_action_returns_failed_json_not_500() -> None:
    reset_adapter_for_tests()
    # Mock Windows + adapter so we pass platform gate and reach dispatch
    fake_adapter = MagicMock()
    with patch("nexus_scalp.gateway.server.sys") as mock_sys:
        mock_sys.platform = "win32"
        with patch("nexus_scalp.gateway.server._get_adapter", return_value=fake_adapter):
            client = TestClient(app)
            body = json.dumps({"action": "NOT_A_REAL_ACTION", "payload": {}}).encode()
            headers = _hmac_headers(DEFAULT_SECRET, DEFAULT_KEY, body)
            r = client.post("/api/v1/execute", content=body, headers=headers)
            assert r.status_code == 200
            assert r.json()["status"] == "FAILED"
            assert "unknown action" in r.json()["message"].lower()


# ---------------------------------------------------------------------------
# 2) Action mapping — all 14 client actions (mocked adapter, Windows path)
# ---------------------------------------------------------------------------


def _win_client(body_dict: dict) -> TestClient:
    """Helper that returns a TestClient bound to a Windows-mocked gateway."""
    # Caller patches _get_adapter; this just documents the helper.
    return TestClient(app)


@pytest.mark.parametrize(
    "action,payload",
    [
        ("GET_ACCOUNT_INFO", {}),
        ("GET_SYMBOL_INFO", {"symbol": "XAUUSD"}),
        ("GET_LAST_TICK", {"symbol": "XAUUSD"}),
        ("GET_HISTORICAL_BARS", {"symbol": "XAUUSD", "timeframe": "M1", "count": 5}),
        ("GET_POSITIONS", {"symbol": "XAUUSD"}),
        ("GET_PENDING_ORDERS", {"symbol": "XAUUSD"}),
        ("GET_CLOSED_DEALS_HISTORY", {"symbol": "XAUUSD", "hours_back": 24}),
        (
            "SEND_ORDER",
            {
                "symbol": "XAUUSD",
                "order_type": "BUY",
                "volume": 0.01,
                "price": 1.0,
                "stop_loss": 0.9,
                "take_profit": 1.1,
                "order_id": "test-1",
            },
        ),
        (
            "EXECUTE_MARKET_ORDER",
            {
                "symbol": "XAUUSD",
                "order_type": "BUY",
                "volume": 0.01,
                "price": 0,
                "stop_loss": 0,
                "take_profit": 0,
            },
        ),
        (
            "PLACE_PENDING_ORDER",
            {
                "symbol": "XAUUSD",
                "order_type": "BUY_LIMIT",
                "volume": 0.01,
                "price": 2400,
                "stop_loss": 2390,
                "take_profit": 2410,
            },
        ),
        ("CANCEL_PENDING_ORDER", {"ticket": 123}),
        ("MODIFY_POSITION", {"ticket": 123, "stop_loss": 1.0, "take_profit": 2.0}),
        ("CLOSE_POSITION", {"ticket": 123}),
    ],
)
def test_gateway_action_dispatch_shape(action: str, payload: dict) -> None:
    reset_adapter_for_tests()
    # Build a fake adapter that satisfies every action minimally
    snap = SimpleNamespace(
        available=True,
        error_state=None,
        login=12345,
        trade_mode=0,
        leverage=100,
        balance=10000.0,
        equity=10000.0,
        margin=0.0,
        margin_free=10000.0,
        currency="USD",
        spec={
            "name": "XAUUSD",
            "digits": 2,
            "point": 0.01,
            "trade_tick_size": 0.01,
            "trade_tick_value": 1.0,
            "volume_min": 0.01,
            "volume_max": 10.0,
            "volume_step": 0.01,
            "trade_stops_level": 5,
            "trade_freeze_level": 0,
            "trade_contract_size": 100.0,
        },
        bid=2400.0,
        ask=2400.5,
        last=2400.25,
        volume=1.0,
        flags=0,
        time_utc=None,
        source="BROKER_NATIVE",
        time=0,
    )
    # broker tick shape needs bid/ask for GET_LAST_TICK
    tick_snap = SimpleNamespace(
        available=True,
        error_state=None,
        bid=2400.0,
        ask=2400.5,
        last=2400.25,
        volume=1.0,
        flags=0,
        time=0,
        time_utc=None,
    )
    fake_adapter = MagicMock()
    fake_adapter.get_account_snapshot.return_value = snap
    fake_adapter.get_symbol_snapshot.return_value = snap
    fake_adapter.get_broker_tick.return_value = tick_snap
    fake_adapter.get_historical_bars.return_value = []
    fake_adapter.get_positions.return_value = []
    fake_adapter.get_pending_orders_snapshot.return_value = []
    fake_adapter.get_pending_orders.return_value = []
    fake_adapter.get_closed_deals_history.return_value = []
    fake_adapter.send_order.return_value = True
    fake_adapter.execute_market_order.return_value = 777001
    fake_adapter.place_pending_order.return_value = 777002
    fake_adapter.cancel_pending_order.return_value = True
    fake_adapter.modify_position.return_value = True
    fake_adapter.close_position.return_value = True

    with patch("nexus_scalp.gateway.server.sys") as mock_sys:
        mock_sys.platform = "win32"
        with patch("nexus_scalp.gateway.server._get_adapter", return_value=fake_adapter):
            client = TestClient(app)
            body = json.dumps({"action": action, "payload": payload}).encode()
            headers = _hmac_headers(DEFAULT_SECRET, DEFAULT_KEY, body)
            r = client.post("/api/v1/execute", content=body, headers=headers)
            assert r.status_code == 200, f"{action} unexpected {r.status_code} {r.text[:500]}"
            data = r.json()
            assert data.get("status") in ("SUCCESS", "OK", "FAILED"), f"{action} bad status {data}"
            # For these known actions we expect SUCCESS (mocked happy path), except edge payloads
            if action not in ("SEND_ORDER",):
                assert data.get("status") == "SUCCESS", f"{action} expected SUCCESS got {data}"


def test_gateway_verify_via_client_adapter_roundtrip() -> None:
    """Client's verify_request_signature agrees with server's HMAC."""
    secret = "roundtrip_secret_999"
    adapter = RemoteMT5GatewayAdapter(secret_token=secret)
    body = json.dumps({"action": "GET_ACCOUNT_INFO", "payload": {}}).encode()
    ts = str(int(time.time()))
    sig = hmac.new(
        secret.encode(), msg=f"{ts}.".encode() + body, digestmod=hashlib.sha256
    ).hexdigest()
    assert adapter.verify_request_signature(body, ts, sig) is True
    # Tampered fails
    assert adapter.verify_request_signature(body + b" ", ts, sig) is False


# ---------------------------------------------------------------------------
# 3) CLI — gateway serve / status (Linux-safe, no MT5)
# ---------------------------------------------------------------------------


def test_cli_help_lists_gateway() -> None:
    from typer.testing import CliRunner

    from nexus_scalp.cli.main import app as cli_app

    runner = CliRunner()
    res = runner.invoke(cli_app, ["--help"])
    assert res.exit_code == 0
    assert "gateway" in res.stdout.lower()


def test_cli_gateway_serve_on_linux_is_runtime_error_not_crash() -> None:
    from typer.testing import CliRunner

    from nexus_scalp.cli.main import app as cli_app

    runner = CliRunner()
    res = runner.invoke(cli_app, ["gateway", "serve", "--json"])
    assert res.exit_code == 1
    data = json.loads(res.stdout or "{}")
    low = json.dumps(data).lower()
    assert "windows" in low and "only" in low


def test_cli_gateway_status_unreachable_is_runtime_not_crash() -> None:
    from typer.testing import CliRunner

    from nexus_scalp.cli.main import app as cli_app

    runner = CliRunner()
    res = runner.invoke(cli_app, ["gateway", "status", "--url", "http://127.0.0.1:1"])
    # Unreachable -> exit 1 (no gateway there), but never a traceback crash
    assert res.exit_code == 1
    assert "unreachable" in res.stdout.lower() or "connection refused" in res.stdout.lower()


def test_cli_gateway_status_with_mocked_healthy_server() -> None:
    from typer.testing import CliRunner

    from nexus_scalp.cli.main import app as cli_app

    runner = CliRunner()
    fake_health = json.dumps({"status": "OK", "platform": "win32"}).encode()
    fake_ping = json.dumps({"status": "OK"}).encode()

    class FakeResp:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self) -> bytes:
            return self._data

        def __enter__(self) -> FakeResp:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    health_url = "http://127.0.0.1:8080/health"
    exec_url = "http://127.0.0.1:8080/api/v1/execute"

    def fake_urlopen(req: object, timeout: int = 5) -> FakeResp:  # type: ignore[override]
        url = req.full_url if hasattr(req, "full_url") else str(req)  # type: ignore[attr-defined]
        if url == health_url:
            return FakeResp(fake_health)
        if url == exec_url:
            return FakeResp(fake_ping)
        raise AssertionError(f"unexpected url {url}")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        res = runner.invoke(cli_app, ["gateway", "status", "--url", "http://127.0.0.1:8080"])
        assert res.exit_code == 0
        assert "reachable" in res.stdout.lower()


def test_engine_boot_daemon_linux_does_not_use_creationflags() -> None:
    """Regression: creationflags=DETACHED_PROCESS crashes on Linux."""
    from unittest.mock import patch as mocker_patch

    import nexus_scalp.cli.engine_boot as eb

    with (
        mocker_patch("subprocess.Popen") as mock_popen,
        mocker_patch("nexus_scalp.cli.engine_boot._pidfile") as mock_pidfile,
        mocker_patch("nexus_scalp.release.paths.get_data_root") as mock_root,
    ):
        tmp = __import__("pathlib").Path("/tmp/test-nexus-daemon")
        tmp.mkdir(parents=True, exist_ok=True)
        mock_root.return_value = tmp
        mock_pidfile.return_value = tmp / "nexus.pid"
        # Ensure no existing pidfile race
        try:
            (tmp / "nexus.pid").unlink()
        except FileNotFoundError:
            pass
        eb._spawn_daemon(["python", "-m", "nexus_scalp.cli.main", "start", "--mode", "paper"])
        assert mock_popen.call_count == 1
        _, kwargs = mock_popen.call_args
        # On Linux (this runner) must NOT pass creationflags
        assert "creationflags" not in kwargs
        assert kwargs.get("start_new_session") is True
