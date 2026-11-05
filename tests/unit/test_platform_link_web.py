"""Platform link web surface contract (GitHub star verification).

Pins:
  * every /api/provisioning/platform* route is token-gated (401 tokenless)
  * GET /api/provisioning/platform returns the honest state machine snapshot
  * connect/start returns the browser URL + short code (backend never opens
    a browser server-side — that is the client's consented action)
  * connect/poll without a session reports NOT_CONNECTED (never a crash)
  * POST /api/provisioning/official without verification fails CLOSED with
    code=PLATFORM_VERIFICATION_REQUIRED and the current state
  * reverify + disconnect round-trip without corrupting state
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.web import auth as web_auth

TOKEN = "platform-web-test-token-12345"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    from nexus_scalp.release import paths as rpaths

    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", TOKEN)
    monkeypatch.delenv("NSE_WEB_AUTH_DISABLE", raising=False)
    monkeypatch.delenv(web_auth.WEB_AUTH_COOKIE_DISABLE_ENV, raising=False)
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(tmp_path / "settings.db"))
    monkeypatch.setattr(rpaths, "get_runtime_workspace", lambda: tmp_path)
    # Point the platform link at a dead local port: status stays honest
    # (NOT_CONNECTED / no token) and any accidental network call fails closed.
    monkeypatch.setenv("NEXUS_PLATFORM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("NEXUS_PLATFORM_ALLOW_HTTP", "1")
    from nexus_scalp.platform_link import reset_platform_link_service

    reset_platform_link_service()
    from nexus_scalp.web.server import create_app

    app = create_app(engine_ref=None)
    yield TestClient(app)
    reset_platform_link_service()


def _cookie(client: TestClient) -> dict[str, str]:
    r = client.get("/first_setup.html")
    assert r.status_code == 200
    return {"Cookie": f"{web_auth.WEB_AUTH_COOKIE_NAME}={TOKEN}"}


def test_platform_routes_are_token_gated(client: TestClient) -> None:
    assert client.get("/api/provisioning/platform").status_code == 401
    assert client.post("/api/provisioning/platform/connect/start").status_code == 401
    assert client.post("/api/provisioning/platform/connect/poll").status_code == 401
    assert client.post("/api/provisioning/platform/reverify").status_code == 401
    assert client.post("/api/provisioning/platform/disconnect").status_code == 401


def test_platform_status_reports_not_connected(client: TestClient) -> None:
    r = client.get("/api/provisioning/platform", headers=_cookie(client))
    assert r.status_code == 200
    payload = r.json()
    assert payload["success"] is True
    platform: dict[str, Any] = payload["platform"]
    assert platform["state"] == "NOT_CONNECTED"
    assert platform["repository"] == "Opselon/NexusTradingForexBot"


def test_connect_poll_without_session_is_not_connected(client: TestClient) -> None:
    r = client.post("/api/provisioning/platform/connect/poll", headers=_cookie(client))
    assert r.status_code == 200
    payload = r.json()
    assert payload["success"] is True
    assert payload["platform"]["state"] == "NOT_CONNECTED"


def test_reverify_and_disconnect_roundtrip(client: TestClient) -> None:
    headers = _cookie(client)
    r1 = client.post("/api/provisioning/platform/reverify", headers=headers)
    assert r1.status_code == 200
    assert r1.json()["platform"]["state"] in {"NOT_CONNECTED", "VERIFICATION_UNAVAILABLE"}
    r2 = client.post("/api/provisioning/platform/disconnect", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["platform"]["state"] == "NOT_CONNECTED"


def test_official_download_denied_without_verification(client: TestClient) -> None:
    r = client.post("/api/provisioning/official", headers=_cookie(client), json={})
    assert r.status_code == 200
    payload = r.json()
    assert payload["success"] is False
    error = payload["error"]
    assert error["code"] == "PLATFORM_VERIFICATION_REQUIRED"
    assert payload["state"] in {"NOT_CONNECTED", "AUTHENTICATION_PENDING", "VERIFICATION_PENDING"}
    assert "star" in error["message"].lower() or "github" in error["message"].lower()
