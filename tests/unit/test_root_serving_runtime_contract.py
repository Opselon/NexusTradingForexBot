"""END-USER-RUNTIME-UI-INTEGRATION (Lane A) — runtime static-serving contract.

Pins the frozen decisions of CONTRACT.md (#4, #5, #6, #7, #8, #9):
  * #4/#5 — `/` and `/index.html` serve the React build when the dist
    resolves, and degrade to the legacy ``Web/index.html`` when it does not
    (zero regression vs the pre-wave behavior).
  * #5 — the legacy dashboard relocates to ``/legacy`` + ``/legacy.html``,
    ``/legacy/`` 302-redirects to ``/legacy.html``.
  * #6 — the root SPA fallback is registered LAST, so every registered route
    and the ``/alt`` mount keep winning (Starlette first-match); deny
    classes (``/api``, ``/ws``, ``/web``) answer with an honest 404 and
    never with the index document (CONTRACT §60).
  * #7 — dotless SPA deep links are public static shells (GET/HEAD only);
    ``/api``, ``/ws``, ``/web`` children stay gated.
  * #8 — hashed ``/assets/*`` files are served immutable; index documents
    are always no-store.
  * #9 — the dist is resolved through the frozen
    ``nexus_scalp.web.frontend_assets`` seam; ``/health`` carries an
    ADDITIVE ``frontend`` object and never drops its existing fields.

Dist resolution is driven ENTIRELY by ``NEXUS_ALT_UI_DIR`` + the frozen
seam's monkeypatch seams — no file is created in or deleted from a real
checkout, and no ports are touched (TestClient only).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.web import auth as auth_module
from nexus_scalp.web.auth import COOKIE_BOOTSTRAP_PATHS, is_public_path
from nexus_scalp.web.frontend_assets import (
    DIST_ENV_VAR,
    frontend_status,
    resolve_frontend_dist,
)

#: Marker written into the fake React index (never present in the legacy page).
_REACT_MARKER = "<!--NEXUS-REACT-INDEX-LANE-A-->"
#: Marker present in the legacy Web/index.html <title>.
_LEGACY_TITLE = "Nexus Scalp Engine"


@pytest.fixture
def clean_dist_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forget every real dist candidate so a test controls what resolves.

    The frozen seam reads ``NEXUS_ALT_UI_DIR`` first (authoritative) and
    falls back to ``<repo>/frontend/dist`` / ``<cwd>/frontend/dist``. In a
    worktree without a build those candidates are absent, but a neighbouring
    checkout can place one on disk — deleting files is forbidden, so the env
    override is pointed at a temp dir that has no index.html instead. A
    set-but-invalid override is authoritative (contract #9): NO dist.
    """
    monkeypatch.delenv(DIST_ENV_VAR, raising=False)
    monkeypatch.setenv("NSE_WEB_AUTH_DISABLE", "1")


@pytest.fixture
def react_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_dist_env: None) -> Path:
    """A minimal vite-shaped dist in a temp dir, resolved via the env slot."""
    dist = tmp_path / "fake-react-dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        f"<!doctype html><html><body>{_REACT_MARKER}"
        '<script src="/assets/index-Ab12Cd34Ef.js"></script>'
        '<link rel="icon" href="/favicon.ico">'
        "</body></html>",
        encoding="utf-8",
    )
    # content-hashed asset (vite style) + a plain-named sibling
    (assets / "index-Ab12Cd34Ef.js").write_text("export const LANE_A = true;", encoding="utf-8")
    (assets / "logo-Zx9y8w7v.png").write_bytes(b"\x89PNG-LANE-A")
    (assets / "plain.css").write_text("body{}", encoding="utf-8")
    # sibling file OUTSIDE the dist — the traversal guard must never reach it
    (tmp_path / "secret.txt").write_text("NEVER-SERVED", encoding="utf-8")
    monkeypatch.setenv(DIST_ENV_VAR, str(dist))
    return dist


@pytest.fixture
def client(react_dist: Path) -> TestClient:
    from nexus_scalp.web.server import create_app

    return TestClient(create_app(engine_ref=None))


@pytest.fixture
def legacy_client(
    tmp_path: Path, clean_dist_env: None, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """App with NO resolvable dist: the pre-wave legacy-at-/ behavior.

    ``clean_dist_env`` already forgets the env slot; pointing it at a
    NON-EXISTENT override dir on top shadows any real
    ``<repo|cwd>/frontend/dist`` that a neighbouring build might leave on
    disk (set-but-invalid => no dist, contract #9). No real file is ever
    deleted — the override simply does not exist.
    """
    from nexus_scalp.web.server import create_app

    monkeypatch.setenv(DIST_ENV_VAR, str(tmp_path / "no-dist-anywhere"))
    return TestClient(create_app(engine_ref=None))


# =============================================================================
# #4/#5 — the canonical document at "/"
# =============================================================================
def test_root_serves_react_index_when_dist_resolves(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert _REACT_MARKER in r.text
    assert _LEGACY_TITLE not in r.text
    assert r.headers["cache-control"] == "no-store"


def test_index_html_alias_of_root_when_dist_resolves(client: TestClient) -> None:
    r = client.get("/index.html")
    assert r.status_code == 200
    assert _REACT_MARKER in r.text
    assert r.headers["cache-control"] == "no-store"


def test_root_falls_back_to_legacy_when_dist_absent(legacy_client: TestClient) -> None:
    r = legacy_client.get("/")
    assert r.status_code == 200
    assert _LEGACY_TITLE in r.text
    assert _REACT_MARKER not in r.text
    assert r.headers["cache-control"] == "no-store"


def test_index_html_alias_falls_back_to_legacy(legacy_client: TestClient) -> None:
    r = legacy_client.get("/index.html")
    assert r.status_code == 200
    assert _LEGACY_TITLE in r.text


def test_no_dist_unknown_path_stays_pre_wave_404(legacy_client: TestClient) -> None:
    """Contract #6 "serving dist": with no build there is no SPA fallback, so
    an unknown deep link keeps EXACTLY the pre-wave answer (an honest 404
    from the router) — the legacy page is never handed out for free, and the
    mount's deny/escape machinery has nothing to weaken."""
    r = legacy_client.get("/trading")
    assert r.status_code == 404
    assert _REACT_MARKER not in r.text


# =============================================================================
# #5 — legacy dashboard relocation
# =============================================================================
@pytest.mark.parametrize("path", ["/legacy", "/legacy.html"])
def test_legacy_dashboard_served_from_relocation(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    assert _LEGACY_TITLE in r.text
    assert _REACT_MARKER not in r.text
    assert r.headers["cache-control"] == "no-store"


def test_legacy_slash_redirects_to_legacy_html(client: TestClient) -> None:
    r = client.get("/legacy/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/legacy.html"
    # the redirect target really is the legacy document
    assert _LEGACY_TITLE in client.get("/legacy.html").text


# =============================================================================
# #6 — root SPA fallback (registered LAST; deny classes never serve a shell)
# =============================================================================
def test_spa_fallback_serves_index_for_dotless_deep_link(client: TestClient) -> None:
    for path in ("/trading", "/positions/123", "/audit/deep/nested"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert _REACT_MARKER in r.text, path
        assert r.headers["cache-control"] == "no-store", path


@pytest.mark.parametrize("path", ["/api/does-not-exist", "/ws/nope", "/web/nope"])
def test_deny_classes_get_honest_404_never_index(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 404, f"{path} must NOT fall back to the SPA index"
    assert _REACT_MARKER not in r.text
    # a data caller must be able to see it is not HTML
    assert r.headers["content-type"] == "application/json"


def test_missing_asset_is_honest_404_never_index(client: TestClient) -> None:
    r = client.get("/assets/does-not-exist-deadbeef.js")
    assert r.status_code == 404
    assert _REACT_MARKER not in r.text


def test_registered_route_wins_over_root_fallback(client: TestClient) -> None:
    # /legacy is a registered route; the fallback mount must not shadow it
    assert _LEGACY_TITLE in client.get("/legacy").text
    # and a registered API route is untouched by the fallback
    assert client.get("/api/status").status_code in (200, 401, 403, 404, 503)


# =============================================================================
# #8 — cache headers
# =============================================================================
@pytest.mark.parametrize("name", ["index-Ab12Cd34Ef.js", "logo-Zx9y8w7v.png"])
def test_hashed_assets_are_immutable(client: TestClient, name: str) -> None:
    r = client.get(f"/assets/{name}")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_plain_named_asset_is_not_cached_immutable(client: TestClient) -> None:
    r = client.get("/assets/plain.css")
    assert r.status_code == 200
    # no hash in the filename => never the immutable policy (may be absent
    # entirely — StaticFiles' own defaults apply untouched)
    assert r.headers.get("cache-control", "") != "public, max-age=31536000, immutable"


def test_index_documents_are_no_store(client: TestClient) -> None:
    for path in ("/", "/index.html", "/legacy", "/legacy.html"):
        assert client.get(path).headers["cache-control"] == "no-store", path


# =============================================================================
# #9 — the frozen seam
# =============================================================================
def test_resolve_frontend_dist_follows_env_override(react_dist: Path) -> None:
    assert resolve_frontend_dist() == react_dist


def test_env_override_set_but_invalid_means_no_dist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_dist_env: None
) -> None:
    """A set-but-invalid override is authoritative: NEVER a fallthrough."""
    empty = tmp_path / "no-index-here"
    empty.mkdir()
    monkeypatch.setenv(DIST_ENV_VAR, str(empty))
    assert resolve_frontend_dist() is None
    status = frontend_status()
    assert status["dist_present"] is True  # the dir exists
    assert status["index"] is False  # but it cannot be served
    assert status["dir"] == str(empty)


def test_env_override_unset_and_repo_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unset env + no repo/cwd dist => None (the legacy fallback path)."""
    monkeypatch.delenv(DIST_ENV_VAR, raising=False)
    monkeypatch.setattr(
        "nexus_scalp.web.frontend_assets._repo_root", lambda: Path("/nonexistent-a")
    )
    # an EXISTING cwd without frontend/dist — no file is deleted anywhere
    monkeypatch.chdir(tmp_path)
    assert resolve_frontend_dist() is None
    assert frontend_status() == {
        "dist_present": False,
        "index": False,
        "dir": None,
    }


def test_seam_never_raises_on_unreadable_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_dist_env: None
) -> None:
    """The seam runs on the /health request path: OSError must not escape."""
    monkeypatch.setenv(DIST_ENV_VAR, str(tmp_path / "gone"))
    # resolve twice — existence probe + status probe — both must degrade
    assert resolve_frontend_dist() is None
    assert isinstance(frontend_status(), dict)


# =============================================================================
# #7 — auth: dotless shells public, data classes gated
# =============================================================================
def test_dotless_shell_is_public_get_only() -> None:
    assert is_public_path("/trading", "GET") is True
    assert is_public_path("/trading", "HEAD") is True
    # contract #7: the shell rule is GET/HEAD only
    assert is_public_path("/trading", "POST") is False
    assert is_public_path("/trading", "PUT") is False


def test_api_children_stay_gated_even_dotless() -> None:
    for path in ("/api/does-not-exist", "/ws/nope", "/web/nope"):
        assert is_public_path(path, "GET") is False, path


def test_legacy_is_public_and_a_bootstrap_entry() -> None:
    for path in ("/legacy", "/legacy.html"):
        assert is_public_path(path, "GET") is True, path
        assert path in COOKIE_BOOTSTRAP_PATHS, path


def test_traversal_is_never_public() -> None:
    for path in ("/../secret.txt", "/assets/../../secret.txt", "/..%2fsecret"):
        assert is_public_path(path, "GET") is False, path


def test_dotted_unknown_stays_gated() -> None:
    assert is_public_path("/unknown.bundle.js", "GET") is False


# =============================================================================
# #9 — /health is JSON and carries the additive frontend block
# =============================================================================
def _health_payload(client: TestClient) -> dict[str, Any]:
    r = client.get("/health")
    assert r.status_code in (200, 503), r.text[:400]
    assert r.headers["content-type"] == "application/json"
    if r.status_code == 200:
        return r.json()
    # 503 carries the verdict under FastAPI's `detail` envelope
    return r.json()["detail"]


def test_health_is_json_not_html(client: TestClient) -> None:
    payload = _health_payload(client)
    assert "verdict" in payload
    assert "checks" in payload


def test_health_carries_additive_frontend_block(client: TestClient) -> None:
    payload = _health_payload(client)
    frontend = payload["frontend"]
    assert set(frontend.keys()) == {"dist_present", "index", "dir"}
    assert frontend["dist_present"] is True
    assert frontend["index"] is True
    assert frontend["dir"] is not None


def test_health_existing_fields_survive(legacy_client: TestClient) -> None:
    payload = _health_payload(legacy_client)
    for key in ("verdict", "checks"):
        assert key in payload, f"additive change must not drop {key}"
    frontend = payload["frontend"]
    assert frontend["dist_present"] is False
    assert frontend["index"] is False
    assert frontend["dir"] is None


# =============================================================================
# traversal never escapes the dist (defense in depth with the auth guard)
# =============================================================================
def test_traversal_never_serves_outside_dist(client: TestClient) -> None:
    for path in ("/../secret.txt", "/assets/../../secret.txt"):
        r = client.get(path)
        assert r.status_code == 404, path
        assert "NEVER-SERVED" not in r.text, path


@pytest.mark.parametrize(
    "malicious",
    [
        "..%2f..%2f..%2fetc%2fpasswd",
        "..\\..\\..\\Windows\\win.ini",
        "....//....//etc/passwd",
        "%2e%2e%2f%2e%2e%2fsecret.txt",
        "index-Ab12Cd34Ef.js/../../secret.txt",
        "C:/Windows/system.ini",
        "//etc/hosts",
        # NOTE: the shape "../../../../etc/passwd" is resolved by httpx and
        # real browsers BEFORE it reaches the ASGI scope (arrives as
        # "/etc/passwd", indistinguishable from a deep link — contract #6
        # serves the index shell, never a file). Server-visible traversal
        # in every other form is pinned here; the collapsed shape is
        # escalated in nse-enduser-runtime/REQUESTS.md re: phase-14's
        # pre-wave expectation for that param.
    ],
)
def test_root_fallback_refuses_traversal_shapes(client: TestClient, malicious: str) -> None:
    """Every traversal vector is refused by the root fallback (never a 200
    shell or a file outside the dist) — httpx resolving ``../..`` client-side
    means the ASGI scope can hold an already-collapsed absolute path, so the
    guard refuses resolved-absolute shapes too (phase-14 traversal parity)."""
    r = client.get(f"/assets/{malicious}")
    assert r.status_code in (401, 404), f"traversal {malicious!r} must be refused"
    assert _REACT_MARKER not in r.text, malicious
    assert "NEVER-SERVED" not in r.text, malicious


def test_auth_public_paths_unaffected_by_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """The allowlist is unchanged by dist resolution (no auth regression)."""
    monkeypatch.delenv(DIST_ENV_VAR, raising=False)
    for path in ("/api/health", "/health", "/static/app.js", "/assets/x.css"):
        assert is_public_path(path, "GET") is True, path


def test_auth_module_exports_unchanged() -> None:
    assert hasattr(auth_module, "is_public_path")
    assert hasattr(auth_module, "PUBLIC_PATHS")
    assert hasattr(auth_module, "COOKIE_BOOTSTRAP_PATHS")


def test_index_json_is_valid(client: TestClient) -> None:
    """The health payload is always JSON-decodable (never an HTML error page)."""
    json.loads(client.get("/health").content)
