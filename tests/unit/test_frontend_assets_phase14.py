"""
Unit Tests - Frontend Asset & Boot Contract (Phase 14 completion)
==================================================================
Regression guards for the three Nexus-owned browser failures:

1. GET /api_client.js 404 -> `Uncaught ReferenceError: NX is not defined`
   at app.js:402. The server MUST serve api_client.js; api_client.js MUST
   define window.NX; index.html MUST load it BEFORE app.js.
2. Tailwind CDN runtime dependency - index.html MUST NOT reference
   cdn.tailwindcss.com; compiled tailwind.css MUST exist and be served.
3. Broken local asset references - every local <script src>/<link href>
   in index.html must resolve over the server with HTTP 200.
4. DOM contract - every getElementById() id used by app.js must exist in
   index.html (initApp() completes without null-deref).
5. Chart history contract - /api/chart/history returns diagnostics
   (source, requested, returned, first/last timestamps) and non-synthetic
   bars with the shape the chart library expects (time/open/high/low/close).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.web.server import WEB_DIR, create_app

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def client() -> TestClient:
    app = create_app(engine_ref=None)
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. NX namespace / api_client.js loading contract
# ---------------------------------------------------------------------------


class TestNxNamespaceContract:
    def test_api_client_served(self, client: TestClient) -> None:
        r = client.get("/api_client.js")
        assert r.status_code == 200
        assert (
            r.headers["content-type"].startswith("text/javascript")
            or "javascript" in r.headers["content-type"]
        )

    def test_api_client_defines_window_nx(self) -> None:
        content = (WEB_DIR / "api_client.js").read_text(encoding="utf-8")
        assert "window.NX" in content
        assert "NX.api" in content or ".api =" in content

    def test_index_script_order_nx_before_app(self) -> None:
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        idx_api = html.index("api_client.js")
        idx_app = html.index("app.js")
        assert idx_api < idx_app, "api_client.js must load BEFORE app.js"

    def test_app_js_uses_nx(self) -> None:
        app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        assert "NX.api" in app_js

    def test_no_fake_nx_namespace_in_app_js(self) -> None:
        """The fix is serving the real api_client.js, NOT faking NX in app.js."""
        app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        assert "window.NX = window.NX || {}" not in app_js
        assert not re.search(r"^const NX\s*=\s*\{\}", app_js, re.MULTILINE)


# ---------------------------------------------------------------------------
# 2. Tailwind production CDN removal
# ---------------------------------------------------------------------------


class TestTailwindLocalBuild:
    def test_no_tailwind_cdn(self) -> None:
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        assert "cdn.tailwindcss.com" not in html
        assert "https://cdn." not in html

    def test_compiled_tailwind_css_exists(self) -> None:
        css = WEB_DIR / "tailwind.css"
        assert css.exists()
        assert css.stat().st_size > 10_000  # compiled utilities, not a stub

    def test_compiled_tailwind_served(self, client: TestClient) -> None:
        r = client.get("/tailwind.css")
        assert r.status_code == 200
        assert "text/css" in r.headers["content-type"]

    def test_tailwind_css_contains_used_colors(self) -> None:
        """The compiled CSS must actually contain the custom theme colors."""
        css = (WEB_DIR / "tailwind.css").read_text(encoding="utf-8")
        # hex values are stored compressed; look for any of the theme colors
        assert any(
            hex_color in css for hex_color in ("#06b6d4", "#090d16", "#121826", "#eab308")
        ), "compiled tailwind.css must include the dashboard theme colors"


# ---------------------------------------------------------------------------
# 3. Local asset serving + no missing assets
# ---------------------------------------------------------------------------


class TestLocalAssetsServed:
    @pytest.mark.parametrize(
        "asset_path",
        [
            "/",
            "/styles.css",
            "/app.js",
            "/api_client.js",
            "/tailwind.css",
            "/vendor/fontawesome/all.min.css",
            "/vendor/webfonts/fa-solid-900.woff2",
            "/vendor/webfonts/fa-brands-400.woff2",
        ],
    )
    def test_local_asset_serves_200(self, client: TestClient, asset_path: str) -> None:
        r = client.get(asset_path)
        assert r.status_code == 200, f"local asset {asset_path} failed"

    def test_index_has_no_broken_local_refs(self, client: TestClient) -> None:
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        refs = re.findall(r'(?:src|href)="([^"]+)"', html)
        for ref in refs:
            if ref.startswith(("http", "//", "data:", "#")):
                continue
            path = ref.split("?")[0].lstrip("/")
            if not path:
                continue
            r = client.get("/" + path)
            assert r.status_code == 200, f"index.html references broken local asset: {ref}"
            assert r.content, f"index.html references empty asset: {ref}"

    def test_no_stale_script_references(self) -> None:
        """Index.html must reference only scripts that exist in Web/."""
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        for ref in re.findall(r'<script src="([^"]+)"', html):
            if ref.startswith(("http", "//")):
                continue
            assert (WEB_DIR / ref).exists(), f"script src references missing file: {ref}"

    def test_color_css_url_assets_exist(self) -> None:
        """FontAwesome CSS url() references resolve to real local files."""
        css = (WEB_DIR / "vendor" / "fontawesome" / "all.min.css").read_text(encoding="utf-8")
        urls = re.findall(r"url\(([^)]+)\)", css)
        for raw_url in urls:
            url = raw_url.strip("'\"")
            if url.startswith("data:"):
                continue
            resolved = (WEB_DIR / "vendor" / "fontawesome" / url).resolve()
            assert resolved.exists(), f"CSS url() asset missing: {url} ({resolved})"


# ---------------------------------------------------------------------------
# 4. DOM contract (initApp completes without null deref)
# ---------------------------------------------------------------------------


class TestDomContract:
    def test_all_getelementbyid_refs_exist(self) -> None:
        app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        index_html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        ids_used = set(re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", app_js))
        ids_defined = set(re.findall(r'id="([^"]+)"', index_html))
        missing = sorted(ids_used - ids_defined)
        assert not missing, f"app.js references missing DOM ids: {missing}"


# ---------------------------------------------------------------------------
# 5. Chart history contract
# ---------------------------------------------------------------------------


class TestChartHistoryContract:
    def test_chart_history_response_shape(self, client: TestClient) -> None:
        r = client.get("/api/chart/history")
        assert r.status_code == 200
        body = r.json()
        for key in (
            "bars",
            "bars_available",
            "source",
            "requested",
            "returned",
            "first_timestamp",
            "last_timestamp",
            "generated_at",
        ):
            assert key in body, f"chart history missing diagnostic key: {key}"
        # Bar shape contract (chart library expects time/open/high/low/close)
        for bar in body["bars"]:
            for key in ("time", "open", "high", "low", "close"):
                assert key in bar, f"bar missing key: {key}"

    def test_chart_history_no_synthetic_source_when_offline(self, client: TestClient) -> None:
        """engine_ref=None: source must be UNAVAILABLE, not a fake 'MT5'."""
        r = client.get("/api/chart/history")
        body = r.json()
        if not body["bars"]:
            assert body["source"] in ("UNAVAILABLE", "ENGINE_STATE")

    def test_chart_history_error_payload_is_safe(self, client: TestClient) -> None:
        """Even on failure the payload must not contain tracebacks."""
        r = client.get("/api/chart/history")
        assert r.status_code == 200
        text = r.text
        assert "Traceback" not in text
        assert 'File "' not in text
