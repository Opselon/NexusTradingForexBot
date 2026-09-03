"""
Marketplace UI contract tests (CHG-0058, NX-MKT).

Validates that the Strategy Marketplace tab is wired into the dashboard:
  * Web/index.html has the nav button (switchTab 'tab-marketplace')
  * Web/index.html has the hidden tab section (id="tab-marketplace")
  * Web/index.html loads Web/marketplace.js
  * Web/marketplace.js exists, is an IIFE, exposes NX.marketplace, and uses
    the frozen /api/v1/marketplace contract paths
  * XSS safety: marketplace.js renders via createElement/textContent, never
    assigns API data through innerHTML (no innerHTML = usage with dynamic data)

These tests are pure file-content probes (no browser, no server) — xdist-safe.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "Web"
_INDEX = _WEB_DIR / "index.html"
_JS = _WEB_DIR / "marketplace.js"


@pytest.fixture(scope="module")
def index_html() -> str:
    assert _INDEX.exists(), f"missing dashboard entry {_INDEX}"
    return _INDEX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def marketplace_js() -> str:
    assert _JS.exists(), f"missing marketplace script {_JS}"
    return _JS.read_text(encoding="utf-8")


class TestMarketplaceTabWiring:
    def test_nav_button_present(self, index_html: str) -> None:
        assert "switchTab('tab-marketplace'" in index_html

    def test_tab_section_present(self, index_html: str) -> None:
        assert 'id="tab-marketplace"' in index_html

    def test_marketplace_script_loaded(self, index_html: str) -> None:
        assert re.search(r'<script[^>]*src="[^"]*marketplace\.js"', index_html)

    def test_nav_label_present(self, index_html: str) -> None:
        assert "Strategy Marketplace" in index_html


class TestMarketplaceJsContract:
    def test_iife_and_namespace(self, marketplace_js: str) -> None:
        assert "window.NX.marketplace" in marketplace_js
        assert "(function ()" in marketplace_js

    def test_frozen_api_paths_used(self, marketplace_js: str) -> None:
        for path in (
            "/api/v1/marketplace/packs",
            "/api/v1/marketplace/seeds",
            "/api/v1/marketplace/rankings",
            "/api/v1/marketplace/runtime-snapshot",
        ):
            assert path in marketplace_js, f"missing contract path {path}"

    def test_control_paths_used(self, marketplace_js: str) -> None:
        for path in (
            "/enable",
            "/disable",
            "/run-research",
            "/repair",
        ):
            assert path in marketplace_js, f"missing control path {path}"

    def test_honest_not_available_rendering(self, marketplace_js: str) -> None:
        # Missing scores must render as NOT_AVAILABLE, never fabricated green.
        assert "NOT_AVAILABLE" in marketplace_js

    def test_no_inner_html_with_api_data(self, marketplace_js: str) -> None:
        # Static layout scaffolding may use innerHTML; any usage must not
        # interpolate API-derived variables (no `${` templates inside
        # innerHTML assignments). This is the XSS guard contract.
        for match in re.finditer(r"innerHTML\s*=\s*([^;]+);", marketplace_js):
            expr = match.group(1)
            assert "${" not in expr, (
                f"innerHTML assignment contains template interpolation "
                f"(potential XSS vector): {expr.strip()[:120]}"
            )
            assert "res." not in expr and "data." not in expr and "seed." not in expr, (
                f"innerHTML assignment references API-derived data: {expr.strip()[:120]}"
            )


class TestMarketplaceApiSurface:
    def test_router_module_importable_and_mounted(self) -> None:
        """The API half of the contract: router module imports and wiring mounts it."""
        import sys

        sys.path.insert(0, str(_REPO_ROOT / "src"))
        from fastapi import FastAPI

        from nexus_scalp.web.api_v1_wiring import _include_routers
        from nexus_scalp.web.api_v1.errors import register_v1_exception_handlers

        app = FastAPI(title="mkt-contract-test")
        register_v1_exception_handlers(app)
        _include_routers(app)
        oa = app.openapi()
        paths = set(oa.get("paths", {}).keys())
        required = {
            "/api/v1/marketplace/packs",
            "/api/v1/marketplace/packs/{pack_id}/install",
            "/api/v1/marketplace/seeds",
            "/api/v1/marketplace/seeds/{seed_id}",
            "/api/v1/marketplace/seeds/{seed_id}/enable",
            "/api/v1/marketplace/seeds/{seed_id}/disable",
            "/api/v1/marketplace/seeds/{seed_id}/run-research",
            "/api/v1/marketplace/rankings",
            "/api/v1/marketplace/seeds/{seed_id}/repair",
            "/api/v1/marketplace/repairs",
            "/api/v1/marketplace/runtime-snapshot",
            "/api/v1/marketplace/scores/{seed_id}/history",
        }
        missing = required - paths
        assert not missing, f"marketplace API surface missing routes: {sorted(missing)}"
