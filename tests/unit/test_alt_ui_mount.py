"""MOUNT-DEEP-LINK: /alt SPA history-fallback contract (backend lane, additive mount).

Pins the REAL behavior of the /alt mount in ``nexus_scalp.web.server.create_app``:

* ``GET /alt/``            -> 200 index.html (StaticFiles html=True, unchanged)
* ``GET /alt/<route>``     -> 200 + the SAME index.html body when Accept wants HTML
                              (deep link / reload of a client-side React route)
* ``GET /alt/<route>``     -> plain JSON 404 when Accept does NOT want HTML
                              (an API/XHR probe must never receive the shell)
* ``GET /alt/assets/<file>`` -> served verbatim, immutable cache policy; a missing
                              hashed asset stays 404 (never masquerades as HTML)
* legacy ``/`` and ``/api/status`` are byte-compatible with before the change
* WEB-AUTH is UNCHANGED: the shell stays behind the token (401 without it) and
  only ``/alt/assets/`` remains public per the 089fb10b allowlist — asserted,
  never weakened.

Offline-only: TestClient over ``create_app`` (same boot pattern as
tests/unit/test_frontend_assets_phase14.py / test_live_state_contract.py).
Skips cleanly when no built frontend/dist exists.

Run:
  ./.venv/Scripts/python.exe -m pytest tests/unit/test_alt_ui_mount.py -p no:cacheprovider -q
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.web.server import WEB_DIR, create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
DIST = REPO_ROOT / "frontend" / "dist"
TOKEN = "alt-mount-test-token"

BROWSER_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
)

pytestmark = pytest.mark.skipif(
    not (DIST / "index.html").is_file(),
    reason="no built frontend/dist (npm run build) — /alt mount not active",
)


def _one_asset() -> str:
    """First real hashed asset in dist/assets (name-agnostic: rebuild-safe)."""
    assets = sorted((DIST / "assets").iterdir())
    assert assets, "frontend/dist/assets is empty — rebuild the bundle"
    return assets[0].name


@pytest.fixture()
def alt_client(monkeypatch) -> TestClient:
    """App with WEB-AUTH ACTIVE (token via env) so auth behavior is real.

    The token is only attached to the requests that need it, which lets the
    same fixture pin the public/protected split instead of hiding it.
    ``NEXUS_ALT_UI_DIR`` is pinned to the repo bundle so a stray ambient
    override in someone's shell cannot make the mount (and these tests) drift.
    """
    monkeypatch.delenv("NSE_WEB_AUTH_DISABLE", raising=False)
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("NEXUS_ALT_UI_DIR", str(DIST))
    client = TestClient(create_app(engine_ref=None))
    client.headers["Accept-Language"] = "en"  # deterministic, no effect on routing
    return client


def _auth(client: TestClient) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


# ---------------------------------------------------------------------------
# 1. deep-link fallback (the actual fix)
# ---------------------------------------------------------------------------


class TestDeepLinkFallback:
    def test_index_serves_html(self, alt_client: TestClient) -> None:
        r = alt_client.get("/alt/", headers={**_auth(alt_client), "Accept": BROWSER_ACCEPT})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert b'<div id="root">' in r.content

    @pytest.mark.parametrize("route", ["positions", "trading", "risk", "ml", "audit"])
    def test_client_route_reload_returns_shell(self, alt_client: TestClient, route: str) -> None:
        """GET /alt/<route> with an HTML Accept = a browser reload of a SPA route."""
        shell = alt_client.get(
            "/alt/", headers={**_auth(alt_client), "Accept": BROWSER_ACCEPT}
        ).content
        r = alt_client.get(f"/alt/{route}", headers={**_auth(alt_client), "Accept": BROWSER_ACCEPT})
        assert r.status_code == 200, f"deep link /alt/{route} must serve the shell"
        assert r.headers["content-type"].startswith("text/html")
        assert r.content == shell, "fallback must return the SAME index.html bytes"
        # no-store: a rebuilt bundle lands on the very next navigation.
        assert r.headers["cache-control"] == "no-store"
        # the response depends on Accept -> shared caches must not reuse it
        # for a non-HTML probe of the same URL.
        assert "accept" in (r.headers.get("vary") or "").lower()

    def test_fallback_is_not_a_redirect(self, alt_client: TestClient) -> None:
        r = alt_client.get(
            "/alt/positions",
            headers={**_auth(alt_client), "Accept": BROWSER_ACCEPT},
            follow_redirects=False,
        )
        assert r.status_code == 200  # the URL stays /alt/positions

    def test_non_html_accept_stays_json_404(self, alt_client: TestClient) -> None:
        """An XHR/fetch probe must NOT be answered with the HTML shell."""
        r = alt_client.get("/alt/nope", headers={**_auth(alt_client), "Accept": "application/json"})
        assert r.status_code == 404
        assert "text/html" not in r.headers["content-type"]
        assert r.json()["detail"] == "Not Found"

    @pytest.mark.parametrize("accept", [None, "*/*", "text/*"])
    def test_non_html_accept_stays_404(self, alt_client: TestClient, accept: str | None) -> None:
        """Pinned (narrowest rule): only an *explicit* text/html|+xml Accept
        navigates. Wildcards and missing Accept are programmatic probes
        (curl/fetch) and keep the plain JSON 404 — unchanged from before."""
        headers = _auth(alt_client)
        if accept:
            headers["Accept"] = accept
        r = alt_client.get("/alt/nope", headers=headers)
        assert r.status_code == 404
        assert "text/html" not in r.headers["content-type"]

    def test_mutation_and_nested_paths_do_not_fall_back(self, alt_client: TestClient) -> None:
        """Only GET/HEAD navigations fall back; nothing else changes shape."""
        r = alt_client.post(
            "/alt/positions", headers={**_auth(alt_client), "Accept": BROWSER_ACCEPT}
        )
        assert r.status_code == 405  # StaticFiles method contract, unchanged
        r = alt_client.get(
            "/alt/assets/not-a-real-hash.js",
            headers={**_auth(alt_client), "Accept": BROWSER_ACCEPT},
        )
        assert r.status_code == 404, "missing hashed asset must never look like HTML"
        # a 404 must never carry a cache policy (an "immutable" 404 would pin
        # the missing file in the browser cache across rebuilds)
        assert "cache-control" not in r.headers


# ---------------------------------------------------------------------------
# 2. static assets still served (mount behavior preserved)
# ---------------------------------------------------------------------------


class TestStaticAssets:
    def test_real_asset_served_with_content_type(self, alt_client: TestClient) -> None:
        name = _one_asset()
        r = alt_client.get(f"/alt/assets/{name}", headers=_auth(alt_client))
        assert r.status_code == 200
        ct = r.headers["content-type"]
        assert ("javascript" in ct) or ("css" in ct) or ct.startswith("text/")
        assert r.content == (DIST / "assets" / name).read_bytes()
        # content-hashed => cacheable forever
        assert "max-age=31536000" in r.headers["cache-control"]
        assert "immutable" in r.headers["cache-control"]

    def test_shell_is_no_store(self, alt_client: TestClient) -> None:
        r = alt_client.get("/alt/", headers={**_auth(alt_client), "Accept": BROWSER_ACCEPT})
        assert r.headers["cache-control"] == "no-store"


# ---------------------------------------------------------------------------
# 3. legacy dashboard + API unaffected (regression guard)
# ---------------------------------------------------------------------------


class TestLegacyUnaffected:
    def test_legacy_root_serves_legacy_bundle(self, alt_client: TestClient) -> None:
        r = alt_client.get("/", headers={**_auth(alt_client), "Accept": BROWSER_ACCEPT})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        legacy = (WEB_DIR / "index.html").read_bytes()
        assert r.content == legacy, "legacy Web/ index must not change"
        assert r.content != (DIST / "index.html").read_bytes()

    def test_api_status_still_json(self, alt_client: TestClient) -> None:
        r = alt_client.get("/api/status", headers=_auth(alt_client))
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")
        body = r.json()
        for key in ("state_version", "engine_running", "health"):
            assert key in body

    def test_unknown_legacy_route_is_not_hijacked(self, alt_client: TestClient) -> None:
        """/alt fallback must be prefix-scoped: no SPA shell leaks elsewhere."""
        r = alt_client.get("/positions", headers={**_auth(alt_client), "Accept": BROWSER_ACCEPT})
        assert r.status_code == 404
        assert "text/html" not in r.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# 4. auth pinned as-is (WEB-AUTH untouched — assert current behavior, no change)
# ---------------------------------------------------------------------------


class TestAuthUnchanged:
    def test_shell_and_fallback_require_token(self, alt_client: TestClient) -> None:
        """Documented model (TASK-ALT-UI-HARDENING B2): HTML shell is protected."""
        for path in ("/alt/", "/alt/positions"):
            r = alt_client.get(path, headers={"Accept": BROWSER_ACCEPT})
            assert r.status_code == 401, f"{path} must stay behind WEB-AUTH"
            assert r.json()["error"]["code"] == "UNAUTHORIZED"

    def test_hashed_assets_are_public_per_allowlist(self, alt_client: TestClient) -> None:
        """Existing allowlist (089fb10b): /alt/assets/ is public static. Pinned."""
        r = alt_client.get(f"/alt/assets/{_one_asset()}")
        assert r.status_code == 200
        assert "cache-control" in r.headers

    def test_traversal_never_reaches_shell(self, alt_client: TestClient) -> None:
        r = alt_client.get(
            "/alt/../Web/index.html", headers={**_auth(alt_client), "Accept": BROWSER_ACCEPT}
        )
        # fail-closed however it is refused (client normalization, auth, or route)
        assert r.status_code in (400, 401, 404)
