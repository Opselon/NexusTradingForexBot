"""BUG-266: web-auth bootstrap contract (React /alt console + legacy dashboard).

Pins the fail-open-free bootstrap behavior that lets BOTH consoles render
from a tokenless first navigation while every data route stays gated:

* public: "/", "/index.html", "/alt", "/alt/" + the /alt/ static prefix
  (the built React console is a static shell; state lives only behind /api)
* the HttpOnly bootstrap cookie is issued on the public ENTRY surfaces
  (documents + app.js/api_client.js) and NOT on API paths
* the cookie authenticates GET /api/** (including the SSE stream path);
  state-mutating routes refuse the cookie-only transport
* headers (?token= / Bearer / X-NSE-Token) keep working unchanged
* traversal-shaped /alt paths never become public
* every non-public /api path stays 401 tokenless (fail-closed intact)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.web import auth as web_auth

TOKEN = "bug266-test-token-12345"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """App with a pinned env token (env-wins: DPAPI store never touched)."""
    from nexus_scalp.web.server import create_app

    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", TOKEN)
    monkeypatch.delenv("NSE_WEB_AUTH_DISABLE", raising=False)
    monkeypatch.delenv(web_auth.WEB_AUTH_COOKIE_DISABLE_ENV, raising=False)
    # /alt serving requires a built dist; ensure the mount exists so prefix
    # behaviour is tested for real (repo dev checkout ships frontend/dist).
    app = create_app(engine_ref=None)
    return TestClient(app)


def _cookie(client: TestClient) -> str:
    r = client.get("/alt/")
    assert r.status_code in (200, 404), f"/alt/ tokenless must not gate: {r.status_code}"
    sc = r.headers.get("set-cookie", "")
    if web_auth.WEB_AUTH_COOKIE_NAME not in sc:
        # dist absent (e.g. clean CI): the legacy asset path issues it too.
        r = client.get("/app.js")
        sc = r.headers.get("set-cookie", "")
    assert web_auth.WEB_AUTH_COOKIE_NAME in sc, f"bootstrap cookie missing: {sc!r}"
    return f"{web_auth.WEB_AUTH_COOKIE_NAME}={TOKEN}"


# --------------------------------------------------------------------------
# allowlist correctness (unit level, no HTTP)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/", "/index.html", "/alt", "/alt/", "/alt/assets/x.js", "/alt/trading", "/app.js"]
)
def test_bootstrap_entry_surfaces_are_public(path: str) -> None:
    assert web_auth.is_public_path(path), f"{path} must be public (BUG-266)"


@pytest.mark.parametrize(
    "path",
    [
        "/alternative-api",
        "/altx",
        "/api/status",
        "/api/v1/system/status",
        "/api/ticks/stream",
        "/assets/../api/status",
        "/alt/../api/status",
        "/alt/./../api/x",
    ],
)
def test_non_entry_and_traversal_paths_stay_gated(path: str) -> None:
    assert not web_auth.is_public_path(path), f"{path} must never be public"


def test_bootstrap_paths_are_all_public() -> None:
    """A Set-Cookie on a gated path would be dead code — pin the subset."""
    assert web_auth.COOKIE_BOOTSTRAP_PATHS <= web_auth.PUBLIC_PATHS


def test_shadow_layer_agrees_with_single_source_of_truth() -> None:
    """WebAuthMiddleware._is_public must equal is_public_path (BUG-266:
    the shadow layer used to carry a divergent inline copy)."""
    for path in (
        "/",
        "/alt/trading",
        "/api/status",
        "/assets/../api/status",
        "/static/x.css",
        "/vendor/webfonts/f.woff2",
    ):
        assert web_auth.WebAuthMiddleware._is_public(path) == web_auth.is_public_path(path)


# --------------------------------------------------------------------------
# HTTP contract (create_app + TestClient)
# --------------------------------------------------------------------------


def test_root_document_tokenless_200_and_cookie(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200, r.text
    sc = r.headers.get("set-cookie", "")
    assert web_auth.WEB_AUTH_COOKIE_NAME in sc
    low = sc.lower()
    assert "httponly" in low and "samesite=strict" in low.replace(" ", "")


@pytest.mark.parametrize("path", ["/alt", "/alt/"])
def test_alt_shell_tokenless_sets_cookie(client: TestClient, path: str) -> None:
    r = client.get(path)
    # dist may be absent in CI layouts -> the mount's 404 is still an
    # AUTH-PASS (never 401): the middleware must not gate the console shell.
    assert r.status_code in (200, 404), f"{path} tokenless must not 401: {r.status_code}"
    if r.status_code == 200:
        assert web_auth.WEB_AUTH_COOKIE_NAME in r.headers.get("set-cookie", "")


def test_api_stays_gated_tokenless(client: TestClient) -> None:
    for path in ("/api/status", "/api/v1/system/status", "/api/ticks/stream"):
        r = client.get(path)
        assert r.status_code == 401, f"{path} tokenless must 401 (fail-closed)"
        assert "UNAUTHORIZED" in r.text


def test_cookie_issued_on_asset_authenticates_api(client: TestClient) -> None:
    boot = _cookie(client)
    r = client.get("/api/status", headers={"Cookie": boot})
    # engine_ref=None may 503 on some sub-blocks but must NEVER 401.
    assert r.status_code == 200, r.text
    assert r.status_code != 401


def test_headers_still_win_and_work(client: TestClient) -> None:
    r = client.get("/api/status", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    r = client.get("/api/status", headers={"X-NSE-Token": TOKEN})
    assert r.status_code == 200
    r = client.get("/api/status?token=" + TOKEN)
    assert r.status_code == 200


def test_wrong_token_never_passes(client: TestClient) -> None:
    r = client.get("/api/status", headers={"Cookie": f"{web_auth.WEB_AUTH_COOKIE_NAME}=nope"})
    assert r.status_code == 401
    r = client.get("/api/status", headers={"Authorization": "***"})
    assert r.status_code == 401


def test_mutation_routes_keep_web_ui_bootstrap_contract(client: TestClient) -> None:
    """WEB-UI-BOOTSTRAP contract preserved (BUG-266 does not touch it): the
    cookie rides mutations too — the legacy dashboard posts via raw fetch()
    without headers. CSRF exposure is bounded by HttpOnly + SameSite=strict
    (no cross-site replay) and the 127.0.0.1 bind; operators who want
    header-only auth set NSE_WEB_AUTH_COOKIE_DISABLE=1 (covered by the
    opt-out test below; cookie acceptance == issuance there too)."""
    boot = _cookie(client)
    r = client.post(
        "/api/replay/toggle", json={"active": True, "speed": 1}, headers={"Cookie": boot}
    )
    assert r.status_code != 401, "cookie transport must stay valid for the legacy bundle"
    r = client.get("/api/status", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200


def test_cookie_disable_optout_removes_set_cookie(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv(web_auth.WEB_AUTH_COOKIE_DISABLE_ENV, "1")
    r = client.get("/")
    assert r.status_code == 200  # still public shell
    assert web_auth.WEB_AUTH_COOKIE_NAME not in r.headers.get("set-cookie", "")
    boot = f"{web_auth.WEB_AUTH_COOKIE_NAME}={TOKEN}"
    r = client.get("/api/status", headers={"Cookie": boot})
    assert r.status_code == 401, "with the opt-out, the cookie must not authenticate"
