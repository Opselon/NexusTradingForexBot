"""SEC-2/SEC-3: static-serving + auth surface hardening for the /alt console.

Lane: security-static-surface. This file is READ-ONLY outside itself: it never
patches server.py or scripts/serve_alt_ui.py — it pins the CURRENT on-disk
behavior of the /alt StaticFiles mount, the WEB-AUTH-P0 middleware allowlist,
path-traversal handling, directory-listening suppression, cache policy, legacy
'/' route shadowing, and SSE auth consistency.

Design rules
------------
* The dist is a CONTROLLED tmp tree (via NEXUS_ALT_UI_DIR, which
  server._resolve_alt_ui_dir() honors FIRST), so sibling rebuilds of
  frontend/dist cannot change hashes under this suite. A separate test mirrors
  the REAL repo dist when it exists and skips with the mount's own reason when
  it does not.
* NSE_WEB_AUTH_DISABLE is left UNSET (and actively cleared) so the real token
  middleware is installed; the token comes from NSE_WEB_AUTH_TOKEN only.
* Deep-link/SPA-fallback and cache-header expectations that depend on the
  concurrent server.py edit are non-strict xfail with a finding id, so the
  suite stays honest whether or not the fallback lands.

Run (worktree root):
  <venv>/Scripts/python.exe -m pytest tests/unit/test_alt_ui_static_auth.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Import isolation. The dev venv's __editable__ .pth pins nexus_scalp to the
# MAIN checkout's src/ (same trap conftest.py documents for .worktrees). This
# lane must test THIS worktree's server.py (the sibling edits land here), so
# force-resolve and purge any pre-imported copy — but only when the already
# imported module would come from a different tree (keeps full-suite runs on
# the main checkout untouched).
# ---------------------------------------------------------------------------
_WT_SRC = str(REPO_ROOT / "src")
if (_WT_SRC not in sys.path) or (
    "nexus_scalp.web.server" in sys.modules
    and not str(Path(sys.modules["nexus_scalp.web.server"].__file__)).startswith(str(REPO_ROOT))
):
    sys.path.insert(0, _WT_SRC)
    for _m in [_k for _k in list(sys.modules) if _k.startswith("nexus_scalp")]:
        del sys.modules[_m]

from fastapi.testclient import TestClient  # noqa: E402
from starlette.routing import Mount  # noqa: E402

from nexus_scalp.web import server as web_server  # noqa: E402
from nexus_scalp.web.auth import is_public_path  # noqa: E402

SERVER_UNDER_TEST = Path(web_server.__file__)
TOKEN = "sec2-static-auth-token"
ALT_CANARY = "<<ALT-STATIC-TEST-SHELL>>"
SECRET_CANARY = "DPAPI-SECRET-STORE-CANARY-MUST-NEVER-BE-SERVED"
LEGACY_MARKER = "Control Center"

#: Traversal battery against a file that lives OUTSIDE the dist root (the
#: sibling canary) plus classic absolute/encoded targets. Every entry is
#: requested both authenticated and not.
_TRAVERSAL_PATHS = [
    "/alt/../secrets.enc",
    "/alt/%2e%2e/secrets.enc",
    "/alt/%2E%2E%2Fsecrets.enc",
    "/alt/%2e%2e%2fsecrets.enc",
    "/alt/..%2f..%2fsecrets.enc",
    "/alt/..\\secrets.enc",
    "/alt/..%5c..%5csecrets.enc",
    "/alt/assets/../../../secrets.enc",
    "/alt/assets/%2e%2e%2f%2e%2e%2f%2e%2e%2fsecrets.enc",
    "/alt/%2e%2e%2f%2e%2e%2fsecrets.enc",
    "/alt/..%2fWeb/index.html",
    "/alt/../Web/index.html",
    "/alt/./assets/../../secrets.enc",
    "/alt/%2e%2e/%2e%2e/%2e%2e/%2e%2e/windows/win.ini",
    "/alt/assets/..%2f..%2f..%2f..%2fsecrets.enc",
]


def _write_dist(root: Path) -> Path:
    """A deterministic built-dist-shaped tree: index + hashed assets + a bare
    directory (no index) to probe directory listing."""
    dist = root / "dist"
    (dist / "assets" / "bare").mkdir(parents=True)
    (dist / "index.html").write_text(
        f"<html><head><title>alt</title></head><body>{ALT_CANARY}</body></html>",
        encoding="utf-8",
    )
    (dist / "assets" / "index-TESTHASH1.js").write_text("export const alt = 1;\n", encoding="utf-8")
    (dist / "assets" / "index-TESTHASH2.css").write_text(":root{--bg:#000}\n", encoding="utf-8")
    # Secret one level above the served root: the traversal target.
    (root / "secrets.enc").write_text(SECRET_CANARY, encoding="utf-8")
    return dist


def _make_app(tmp_path: Path):
    os.environ["NSE_WEB_AUTH_TOKEN"] = TOKEN
    os.environ.pop("NSE_WEB_AUTH_DISABLE", None)  # real auth: never disabled here
    os.environ["NEXUS_ALT_UI_DIR"] = str(_write_dist(tmp_path))
    os.environ["NSE_WEB_AUTH_COOKIE_DISABLE"] = "1"  # header-only: pin token transport
    return web_server.create_app(engine_ref=None)


@pytest.fixture()
def alt_app(tmp_path):
    app = _make_app(tmp_path)
    mounts = [r for r in app.routes if isinstance(r, Mount) and r.path == "/alt"]
    if not mounts:
        pytest.skip(
            "/alt mount not created — same condition the server itself uses "
            "(no built dist resolvable via NEXUS_ALT_UI_DIR)"
        )
    try:
        yield app
    finally:
        os.environ.pop("NEXUS_ALT_UI_DIR", None)
        os.environ.pop("NSE_WEB_AUTH_TOKEN", None)
        os.environ.pop("NSE_WEB_AUTH_COOKIE_DISABLE", None)


@pytest.fixture()
def alt_client(alt_app):
    """Tokenless client (separate object: never shares auth headers)."""
    with TestClient(alt_app) as c:
        yield c


@pytest.fixture()
def auth(alt_app):
    """Authenticated client carrying the Bearer credential."""
    with TestClient(alt_app) as c:
        c.headers.update({"Authorization": f"Bearer {TOKEN}"})
        yield c


def _no_leak(response, url: str) -> None:
    """A probe may 400/401/403/404/200-index — it may NEVER return out-of-root
    file contents or echo secret material."""
    assert response.status_code in (400, 401, 403, 404, 200), url
    body = response.text
    assert SECRET_CANARY not in body, f"SEC-2 TRAVERSAL LEAK via {url}"
    assert "[fonts]" not in body and "DPAPI" not in body, f"SEC-2 leak via {url}"
    if response.status_code == 200:
        # The only content an /alt probe may ever return is the console shell
        # itself, never another tree's file (legacy Web/index.html is ~300KB).
        assert ALT_CANARY in body or response.headers["content-type"].startswith(
            "application/json"
        ), f"SEC-2: 200 body from outside dist for {url}: {body[:120]!r}"


# ---------------------------------------------------------------------------
# 1. Auth consistency between the /alt mount and the API (WEB-AUTH-P0)
# ---------------------------------------------------------------------------


def test_api_status_unauthenticated_is_401(alt_client) -> None:
    """WEB-AUTH-P0 intact: the canonical snapshot never answers tokenless."""
    r = alt_client.get("/api/status")
    assert r.status_code == 401, "SEC-2: /api/status must fail closed without a token"
    assert r.json()["error"]["code"] == "UNAUTHORIZED"
    assert r.headers.get("www-authenticate") == "Bearer"


def test_alt_static_is_not_public_and_never_leaks_api(alt_client) -> None:
    """OBSERVED: the WHOLE /alt mount sits behind the token (401, not 200).

    That is stricter than the legacy static allowlist and is pinned so a
    future 'make /alt public' decision must be an explicit edit, not an
    accident (finding ALT-STATIC-AUTHPOLICY in docs/alt-ui-static-security.md).
    """
    for p in ("/alt", "/alt/", "/alt/index.html", "/alt/assets/index-TESTHASH1.js"):
        r = alt_client.get(p)
        assert r.status_code == 401, f"SEC-2: {p} served without auth"
        assert ALT_CANARY not in r.text
        assert "export const alt" not in r.text


def test_alt_static_reachable_with_token(auth) -> None:
    for p, marker in (
        ("/alt/", ALT_CANARY),
        ("/alt/index.html", ALT_CANARY),
        ("/alt/assets/index-TESTHASH1.js", "export const alt"),
        ("/alt/assets/index-TESTHASH2.css", "--bg"),
    ):
        r = auth.get(p)
        assert r.status_code == 200, f"SEC-2: {p} not served (mount regression)"
        assert marker in r.text


def test_allowlist_refuses_traversal_upstream(auth) -> None:
    """Single source of truth: a path carrying literal .. or \\ is never public.

    FINDING SEC-2/A2 (pinned, defense-in-depth): the refusal is a TEXT check
    on the path string — percent-encoded traversal ("%2e%2e") passes it. The
    live probes are the real guard: uvicorn (ASGI spec) hands the middleware
    the DECODED scope["path"], so encoded variants arrive as ".." and 401
    (pinned by the battery below). A front-end that forwards raw percent
    encoding without decoding would widen this allowlist — flagged for the
    8088 integrators checklist.
    """
    assert is_public_path("/assets/app.js") is True
    for bad in ("/assets/../../secrets.enc", "/alt/..\\secrets.enc", "/assets/../x"):
        assert is_public_path(bad) is False, bad
    assert is_public_path("/assets/%2e%2e/secrets.enc") is True  # pinned current behavior


# ---------------------------------------------------------------------------
# 2. Path traversal — never serve outside dist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", _TRAVERSAL_PATHS)
def test_traversal_never_discloses_files(auth, alt_client, url) -> None:
    _no_leak(auth.get(url), "auth " + url)
    _no_leak(alt_client.get(url), "noauth " + url)


def test_outside_dist_target_actually_exists(auth, tmp_path) -> None:
    """Guard the guard: the canary file we probe for is really there, so a
    passing traversal test means refusal, not a missing fixture."""
    assert (tmp_path / "secrets.enc").read_text(encoding="utf-8") == SECRET_CANARY


# ---------------------------------------------------------------------------
# 3. Directory listing
# ---------------------------------------------------------------------------


def test_no_directory_listing_on_indexless_dir(auth) -> None:
    """/alt/assets/bare/ has no index.html: Starlette StaticFiles must never
    enumerate it. Observed: it falls through to the SPA shell (or plain 404 if
    the sibling's fallback is absent) — both are listing-free."""
    r = auth.get("/alt/assets/bare/")
    assert r.status_code in (403, 404, 200), r.status_code
    body = r.text.lower()
    assert "directory listing" not in body, "SEC-2: directory listing exposed"
    assert "index-testhash1.js" not in body, "SEC-2: directory contents enumerated"


# ---------------------------------------------------------------------------
# 4. Cache policy: shell must not go stale; hashed assets may be immutable
# ---------------------------------------------------------------------------


# SEC-2/C1 CLOSED (ALT-UI-PRO integration): _AltSpaStaticFiles.get_response now pins
# no-store on the REAL-file HTML shell path and immutable on hashed /alt/assets/*.
# The assertion below is a plain (non-xfail) regression pin.
def test_index_shell_cache_control_prevents_stale_shell(auth) -> None:
    r = auth.get("/alt/index.html")
    assert r.status_code == 200
    assert "no-store" in (r.headers.get("cache-control") or ""), (
        f"observed Cache-Control={r.headers.get('cache-control')!r}"
    )


def test_spa_fallback_shell_cache_policy_is_pinned(auth) -> None:
    """Pin the CURRENT fallback behavior (no-store) without requiring it: the
    assertion is conditional on a 200 html fallback, which is the fallback's
    own contract (deep-link behavior — see findings doc)."""
    r = auth.get("/alt/audit/123")
    if r.status_code == 200 and r.headers.get("content-type", "").startswith("text/html"):
        assert "no-store" in (r.headers.get("cache-control") or "")
    else:
        # Fallback not landed/removed: honest 404 is acceptable, index bytes are not.
        assert r.status_code in (404, 405, 307), r.status_code


def test_hashed_asset_content_type_sanity(auth) -> None:
    js = auth.get("/alt/assets/index-TESTHASH1.js")
    css = auth.get("/alt/assets/index-TESTHASH2.css")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    # Missing hashed assets must 404 loudly, never be masked by the shell.
    missing = auth.get("/alt/assets/index-NOPE9999.js")
    assert missing.status_code == 404
    assert ALT_CANARY not in missing.text


# ---------------------------------------------------------------------------
# 5. Mount must not overmount legacy routes or the API
# ---------------------------------------------------------------------------


def test_root_serves_legacy_dashboard_not_alt(auth) -> None:
    legacy = (web_server.WEB_DIR / "index.html").read_bytes()
    r = auth.get("/")
    assert r.status_code == 200
    assert r.content == legacy, "SEC-2: /alt mount shadowed the legacy dashboard at /"
    assert ALT_CANARY not in r.text
    assert LEGACY_MARKER.encode() in r.content or b"<!DOCTYPE" in r.content[:40]


def test_api_404_is_not_swallowed_by_spa_fallback(auth) -> None:
    """The fallback is scoped to the /alt mount: unknown /api/* stays a JSON
    404 envelope, never HTML shell bytes (defense against overmount drift)."""
    r = auth.get("/api/no-such-endpoint")
    assert r.status_code == 404
    assert ALT_CANARY not in r.text
    assert r.headers["content-type"].startswith("application/json")


# ---------------------------------------------------------------------------
# 6. SSE auth consistency (same policy on the streaming surface)
# ---------------------------------------------------------------------------


async def _stream_head(app, path: str, headers: dict) -> dict:
    seen: dict = {}

    async def receive():
        return {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.start" and "status" not in seen:
            seen["status"] = msg["status"]
            seen["ct"] = (dict(msg.get("headers", [])).get(b"content-type") or b"").decode(
                "latin-1"
            )

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "client": ("127.0.0.1", 51234),
        "server": ("testserver", 80),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    try:
        await asyncio.wait_for(app(scope, receive, send), timeout=10)
    except TimeoutError:
        seen["timed_out"] = True
    return seen


def test_sse_stream_requires_the_same_token(alt_client) -> None:
    app = alt_client.app
    head = asyncio.run(_stream_head(app, "/api/ticks/stream", {"accept": "text/event-stream"}))
    assert head.get("status") == 401, f"SEC-2: SSE stream answered tokenless: {head}"
    assert "event-stream" not in (head.get("ct") or "")


# ---------------------------------------------------------------------------
# 7. Mirror of the real repo dist (skip exactly like the mount does)
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo_dist_client():
    dist = REPO_ROOT / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        pytest.skip(
            "no built frontend/dist — /alt is not mounted by the server itself "
            "(same disable-by-default contract, ALT-UI mount policy)"
        )
    os.environ["NSE_WEB_AUTH_TOKEN"] = TOKEN
    os.environ.pop("NSE_WEB_AUTH_DISABLE", None)
    os.environ.pop("NEXUS_ALT_UI_DIR", None)  # exercise the real resolver path
    app = web_server.create_app(engine_ref=None)
    mounts = [r for r in app.routes if isinstance(r, Mount) and r.path == "/alt"]
    if not mounts:
        pytest.skip("/alt not mounted by create_app (dist unresolvable)")
    with TestClient(app) as c:
        yield c
    os.environ.pop("NSE_WEB_AUTH_TOKEN", None)


def test_repo_dist_index_and_bundle_serve_with_expected_types(repo_dist_client) -> None:
    """The real shell must reference assets that really resolve (no stale
    hash drift between dist/index.html and dist/assets)."""
    import re

    idx = repo_dist_client.get("/alt/index.html", headers={"Authorization": f"Bearer {TOKEN}"})
    assert idx.status_code == 200
    refs = re.findall(r'(?:src|href)="(/alt/assets/[^"]+)"', idx.text)
    assert refs, "dist/index.html references no /alt/assets/* — build drift"
    for ref in refs:
        r = repo_dist_client.get(ref, headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200, f"SEC-2: referenced asset 404s: {ref}"
        assert SECRET_CANARY not in r.text


def test_repo_dist_traversal_battery_stays_closed(repo_dist_client) -> None:
    for url in _TRAVERSAL_PATHS[:8]:
        for headers in ({}, {"Authorization": f"Bearer {TOKEN}"}):
            r = repo_dist_client.get(url, headers=headers)
            assert r.status_code in (400, 401, 403, 404, 200), url
            assert "DPAPI" not in r.text and "root:" not in r.text
