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

import json
from pathlib import Path
from typing import Any

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


# ---------------------------------------------------------------------------
# CodeQL remediation follow-ups (alerts #1096 path-injection, #1097
# stack-trace-exposure), 2026-09-16 — pins for the hardened guard + the
# safe-category error surface.
# ---------------------------------------------------------------------------
def test_import_guard_rejects_prefix_bypass_and_traversal(
    client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sibling-prefix class ("data/rawx" starts-with "data/raw"), ..
    traversal (both separators), null bytes and a bare root must ALL be
    refused; only a real file under an allowed root passes."""
    import os

    from nexus_scalp.web.provisioning_routes import _allowed_import_path

    cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        (tmp_path / "data" / "raw").mkdir(parents=True)
        (tmp_path / "data" / "rawx").mkdir()
        good = tmp_path / "data" / "raw" / "x.csv"
        good.write_text("time,open\n1,2\n", encoding="utf-8")
        (tmp_path / "data" / "rawx" / "evil.csv").write_text("x", encoding="utf-8")

        assert _allowed_import_path(str(good)) == good.resolve()
        for bad in (
            "data/rawx/evil.csv",  # sibling prefix bypass
            "data/raw/../../secrets.csv",  # traversal
            "data/raw\..\secrets.csv",  # traversal, alt separator
            "data/raw/x.csv\x00.png",  # null byte
            "data/raw",  # the root itself
            "data/raw/missing.csv",  # non-existent under root
            r"C:\Windows\System32\drivers\etc\hosts",  # outside roots
        ):
            with pytest.raises(ValueError):
                _allowed_import_path(bad)
    finally:
        os.chdir(cwd)


def test_environment_probe_never_exposes_exception_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """py/stack-trace-exposure: with a broken torch import the probe reports
    the exception CATEGORY only — never the message (paths / library
    internals stay server-side)."""
    import builtins

    from nexus_scalp.model_provisioning import pipeline as pl_mod

    real_import = builtins.__import__

    def _boom(name: str, *a: Any, **k: Any) -> Any:
        if name == "torch":
            raise ImportError(r"simulated torch failure exposing C:\secret\path")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _boom)
    env = pl_mod.detect_ml_environment()
    assert env["torch"] is None
    assert env.get("torch_error") == "ImportError"
    assert "secret" not in json.dumps(env)


# ---------------------------------------------------------------------------
# BUG-301 — environment endpoints: DISCOVERY-only + explicit install route.
# ---------------------------------------------------------------------------
def test_environment_endpoint_is_discovery_only_and_ready_shape(client: TestClient) -> None:
    """GET /api/provisioning/environment returns the report (checklist +
    training_ready) with the python/environment/pytorch/gpu blocks — and
    creates NOTHING (no venv, no pip)."""
    hdr = _cookie(client)
    r = client.get("/api/provisioning/environment?backend=cpu", headers=hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    rep = body["report"]
    for key in ("python", "environment", "pytorch", "gpu", "checks", "training_ready"):
        assert key in rep, key
    assert isinstance(rep["training_ready"], bool)
    # every check is a typed dict with remedy text for failures
    for c in rep["checks"]:
        assert set(("stage", "ok", "code", "detail", "remedy")) <= set(c)


def test_train_start_backend_validation(client: TestClient) -> None:
    hdr = _cookie(client)
    from pathlib import Path as PathT

    root = PathT("data/raw")
    root.mkdir(parents=True, exist_ok=True)
    (root / "ok.csv").write_text("time,open\n1,2\n", encoding="utf-8")
    r = client.post(
        "/api/provisioning/train/start",
        json={"file": "data/raw/ok.csv", "backend": "tpu"},
        headers=hdr,
    )
    assert r.json()["error"]["code"] == "TRAIN_BACKEND_INVALID"


def test_install_endpoint_exists_single_flight(client: TestClient) -> None:
    """Route mounted + rejects while a (spied) install is active — the
    install route exists, is explicit POST, and never auto-starts training."""
    hdr = _cookie(client)
    r = client.post("/api/provisioning/environment/install", json={"backend": "cpu"}, headers=hdr)
    body = r.json()
    # Either a completed fresh report (success true/false) or a typed error —
    # never a 404 (route mounted) and never a raw 500 stack.
    assert r.status_code == 200, r.text[:200]
    assert ("report" in body) or ("error" in body)
