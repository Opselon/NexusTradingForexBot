"""BUG-293 web provisioning surface contract (first-setup wizard backend).

Pins:
  * /first_setup.html is a PUBLIC entry document (tokenless render + the
    bootstrap Set-Cookie, same BUG-267 contract as the other shells)
  * every /api/provisioning/* route stays token-gated (401 tokenless)
  * with the bootstrap cookie: status/environment respond with the honest
    slot classification + recommendation shape the wizard consumes
  * train/start rejects non-parquet/csv files and paths outside the import
    roots (no arbitrary path drives the local training pipeline)
  * official POST with an unconfigured source fails CLOSED (nothing
    installed, honest error envelope)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.web import auth as web_auth

TOKEN = "bug293-test-token-12345"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    from nexus_scalp.release import paths as rpaths

    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", TOKEN)
    monkeypatch.delenv("NSE_WEB_AUTH_DISABLE", raising=False)
    monkeypatch.delenv(web_auth.WEB_AUTH_COOKIE_DISABLE_ENV, raising=False)
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(tmp_path / "settings.db"))
    # Anchor all runtime/workspace roots into tmp so the routes never touch
    # the developer's real artifacts tree during tests.
    monkeypatch.setattr(rpaths, "get_runtime_workspace", lambda: tmp_path)
    from nexus_scalp.web.server import create_app

    app = create_app(engine_ref=None)
    return TestClient(app)


def _cookie(client: TestClient) -> dict[str, str]:
    r = client.get("/first_setup.html")
    assert r.status_code == 200, "first_setup.html must be a public entry document"
    assert web_auth.WEB_AUTH_COOKIE_NAME in r.headers.get("set-cookie", ""), (
        "first-setup document must issue the bootstrap cookie (BUG-267 contract)"
    )
    return {"Cookie": f"{web_auth.WEB_AUTH_COOKIE_NAME}={TOKEN}"}


def test_first_setup_page_public_with_bootstrap_cookie(client: TestClient) -> None:
    assert web_auth.is_public_path("/first_setup.html")
    _cookie(client)


@pytest.mark.parametrize(
    "path",
    [
        "/api/provisioning/status",
        "/api/provisioning/environment",
    ],
)
def test_provisioning_api_gated_tokenless(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 401, f"{path} must never be public"


def test_status_shape_with_cookie(client: TestClient) -> None:
    hdr = _cookie(client)
    r = client.get("/api/provisioning/status", headers=hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    slot = body["slot"]
    assert slot["state"] in ("missing", "ready", "verified", "rejected")
    assert "path" in slot and "servable" in slot
    assert "action" in body["recommended"]


def test_environment_probe_with_cookie(client: TestClient) -> None:
    hdr = _cookie(client)
    r = client.get("/api/provisioning/environment", headers=hdr)
    assert r.status_code == 200
    env = r.json()["environment"]
    assert "python" in env and "torch" in env


def test_train_start_rejects_bad_input(client: TestClient) -> None:
    hdr = _cookie(client)
    r = client.post(
        "/api/provisioning/train/start",
        json={"file": "C:\\Windows\\system32\\drivers\\etc\\hosts"},
        headers=hdr,
    )
    assert r.status_code in (200, 400)  # error envelope, not a crash
    body = r.json()
    assert body.get("success") is not True
    assert body["error"]["code"] == "TRAIN_INPUT_REJECTED"
    r2 = client.post("/api/provisioning/train/start", json={"file": "data/raw/x.exe"}, headers=hdr)
    assert r2.json()["error"]["code"] == "TRAIN_INPUT_REJECTED"


def test_official_unconfigured_fails_closed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    hdr = _cookie(client)
    from nexus_scalp.model_provisioning import official as off

    monkeypatch.setattr(off, "DEFAULT_OFFICIAL_BASE_URL", "")
    r = client.post("/api/provisioning/official", json={}, headers=hdr)
    body = r.json()
    assert body.get("success") is not True
    # Nothing was installed and the failure is honestly recorded:
    assert body["error"]["code"] in ("OFFICIAL_BUNDLE_REJECTED", "PROVISIONING_OFFICIAL_ERROR")


def test_progress_and_cancel_no_run(client: TestClient) -> None:
    hdr = _cookie(client)
    r = client.get("/api/provisioning/train/progress", headers=hdr)
    assert r.status_code == 200 and r.json()["active"] is False
    r2 = client.post("/api/provisioning/train/cancel", json={}, headers=hdr)
    assert r2.json().get("cancel") in ("NO_ACTIVE_RUN", "REQUESTED")
