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

    # ------------------------------------------------------------------
    # CodeQL py/path-injection (#62/#63/#67) regression: the webfonts
    # route must never build a path from user input. Traversal attempts
    # must 404, and only real bundled font files may be served.
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "malicious",
        [
            "..%2f..%2f..%2fetc%2fpasswd",
            "..\..\..\Windows\win.ini",
            "../../../../etc/passwd",
            "....//....//etc/passwd",
            "%2e%2e%2f%2e%2e%2fsecret.txt",
            "fa-solid-900.woff2/../../server.py",
            "C:/Windows/system.ini",
            "//etc/hosts",
        ],
    )
    def test_webfont_traversal_attempts_404(self, client: TestClient, malicious: str) -> None:
        r = client.get(f"/vendor/webfonts/{malicious}")
        assert r.status_code == 404, f"traversal {malicious!r} must 404"

    def test_webfont_unknown_name_404(self, client: TestClient) -> None:
        r = client.get("/vendor/webfonts/../server.py")
        assert r.status_code == 404
        r2 = client.get("/vendor/webfonts/no-such-font.woff2")
        assert r2.status_code == 404

    def test_webfont_real_file_still_served(self, client: TestClient) -> None:
        r = client.get("/vendor/webfonts/fa-solid-900.woff2")
        assert r.status_code == 200
        assert r.headers["content-type"] in (
            "font/woff2",
            "application/octet-stream",
            "application/font-woff2",
            "font/woff",
        )  # FileResponse from a real bundled asset, never text/html
        body = r.content
        assert len(body) > 1000  # real binary font payload

    def test_webfont_response_not_script_html(self, client: TestClient) -> None:
        """A successful webfont response must never be HTML/script - the
        FileResponse must point at a real binary asset (path-injection side
        effect check: no content-type confusion)."""
        r = client.get("/vendor/webfonts/fa-brands-400.ttf")
        assert r.status_code == 200
        assert "text/html" not in r.headers.get("content-type", "")
        assert "<script" not in r.text


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


# ---------------------------------------------------------------------------
# 6. Chart resync (BUG-054) frontend contract
# ---------------------------------------------------------------------------


class TestChartResyncContract:
    def test_resync_button_present(self) -> None:
        """The chart toolbar must expose a Resync action (BUG-054)."""
        index_html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        assert "resyncChart()" in index_html
        assert 'id="btn-resync-chart"' in index_html

    def test_resync_function_defined(self) -> None:
        """app.js must define resyncChart() and call /api/chart/history?count=900."""
        app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        assert "async function resyncChart()" in app_js
        assert "/api/chart/history?count=900" in app_js

    def test_resync_wired_to_reconnect(self) -> None:
        """SSE reconnect + stale watchdog must trigger a chart resync."""
        app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        assert "resyncChart();" in app_js
        assert "lastChartResyncAt" in app_js


# ---------------------------------------------------------------------------
# 7. Tab-section nesting contract (Forensic Incident Center regression)
# ---------------------------------------------------------------------------


class TestTabSectionNesting:
    """BUG-120 regression: every .tab-content section must be a SIBLING of
    the others, never nested inside another .tab-content section.

    The Forensic Incident Center was nested inside the Liquidity panel
    (a missing </section>), so switchTab() revealed the child while its
    hidden parent kept it invisible (0x0 rect -> blank tab).
    The legacy div-balance checker PASSED because the imbalance was in
    <section> elements, not <div>s.
    """

    def test_tab_sections_are_siblings(self) -> None:
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        # Locate every .tab-content section open tag.
        opens = list(re.finditer(r'<section\s+id="([^"]+)"\s+class="tab-content', html))
        assert len(opens) >= 8, f"suspiciously few tab sections: {len(opens)}"
        for _i, m in enumerate(opens):
            sec_id = m.group(1)
            # The section body runs until the </section> that closes it.
            # A nested .tab-content section before that close proves nesting.
            depth = 1
            pos = m.end()
            while depth > 0:
                nxt_open = html.find("<section", pos)
                nxt_close = html.find("</section>", pos)
                if nxt_close == -1:
                    break
                if nxt_open != -1 and nxt_open < nxt_close:
                    depth += 1
                    pos = nxt_open + len("<section")
                else:
                    depth -= 1
                    pos = nxt_close + len("</section>")
            body = html[m.end() : pos]
            nested = re.findall(r'<section\s+id="([^"]+)"\s+class="tab-content', body)
            assert not nested, (
                f"tab section #{sec_id} CONTAINS nested tab sections {nested}; "
                "every .tab-content must be a sibling (BUG-120: incidents was "
                "nested inside liquidity)"
            )

    def test_incident_tab_section_has_expected_panels(self) -> None:
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        m = re.search(r'<section\s+id="tab-incidents"', html)
        assert m, "Forensic Incident Center section missing"
        # The 4 panels + list + detail containers the incident JS populates.
        for dom_id in (
            "incident-list",
            "incident-detail",
            "incident-search-input",
            "inc-summary-open",
            "inc-worker-state",
        ):
            assert f'id="{dom_id}"' in html, f"incident panel missing id={dom_id}"

    def test_every_nav_button_target_has_a_sibling_section(self) -> None:
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        nav_targets = set(re.findall(r"switchTab\('(tab-[^']+)'", html))
        sections = set(re.findall(r'<section\s+id="(tab-[^"]+)"', html))
        missing = sorted(t for t in nav_targets if t not in sections)
        assert not missing, f"nav buttons reference missing tab sections: {missing}"
