"""Regression suite for the NSE Node.js runtime-role audit (ARCHITECTURE: Node is
build/dev/test-only, NOT a runtime dependency).

Proves the architecture decision and guards it against regression:

  * The Web UI is a buildless vanilla-JS SPA served entirely by FastAPI.
  * The engine and UI runtime require NO Node.js, npm, bundler, or node_modules.
  * Node.js is used only to (a) compile Tailwind at build time and (b) run the
    JS syntax/test gate in CI (js-tests.yml).

These tests assert *structural* facts about the repository so the "Node is not a
runtime dependency" contract cannot silently regress (e.g. a future asset ref to a
CDN or a committed node_modules, or a route that assumes a Node server).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "Web"

# Files that MUST exist for a Node-free, buildless, runtime-served UI.
REQUIRED_FRONTEND_ASSETS = [
    "index.html",
    "app.js",
    "styles.css",
    "api_client.js",
    "tailwind.css",  # compiled artifact, served at /tailwind.css
    "tailwind_input.css",  # build source
]

# Browser JS must never reference a bundler, node_modules, or a CDN (it is served
# locally by FastAPI). The one allowed external family is self-hosted webfonts
# referenced relatively under /vendor/webfonts (already localized, see BUG-047).
FORBIDDEN_JS_PATTERNS = (
    "cdn.tailwindcss.com",
    "node_modules",
    "require(",
    "import ",  # ES module import — no bundler/loader exists at runtime
)


def _load_python_module(path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _create_app():
    """Build a real FastAPI app without constructing the heavy LiveEngine."""
    server_path = REPO_ROOT / "src" / "nexus_scalp" / "web" / "server.py"
    mod = _load_python_module(server_path)
    return mod.create_app(engine_ref=None)


@pytest.mark.parametrize("asset", REQUIRED_FRONTEND_ASSETS)
def test_buildless_assets_present(asset: str) -> None:
    assert (WEB_DIR / asset).exists(), f"missing required Web asset: {asset}"


def test_browser_js_has_no_bundler_or_cdn_refs() -> None:
    offenders: list[str] = []
    for js in WEB_DIR.glob("*.js"):
        text = js.read_text(encoding="utf-8", errors="replace")
        for pattern in FORBIDDEN_JS_PATTERNS:
            if pattern in text:
                offenders.append(f"{js.name}: contains {pattern!r}")
    assert not offenders, (
        "Browser JS must be buildless (no bundler/CDN/node_modules):\n" + "\n".join(offenders)
    )


def test_web_ui_served_without_node() -> None:
    """The Control Center must be servable by FastAPI alone — no Node server."""
    app = _create_app()
    client = TestClient(app)
    index = client.get("/")
    assert index.status_code == 200, "GET / (index.html) must be served by FastAPI"
    assert "text/html" in index.headers.get("content-type", "")
    # Asset routes the browser actually loads — all served by the Python process.
    for route in ("/app.js", "/styles.css", "/api_client.js", "/tailwind.css"):
        resp = client.get(route)
        assert resp.status_code == 200, f"{route} must be served by FastAPI"


def test_no_package_json_runtime_marker() -> None:
    """No package.json => Node is not a declared runtime/manifest dependency.
    (A future dev-only package.json may exist, but its absence today proves there
    is no npm-driven runtime contract.)"""
    found = list(REPO_ROOT.glob("package.json")) + list(REPO_ROOT.glob("**/package.json"))
    # Filter out anything inside node_modules (test-only playwright) and .venv.
    real = [p for p in found if "node_modules" not in str(p) and ".venv" not in str(p)]
    assert not real, f"unexpected package.json at runtime root: {real}"


def test_build_tailwind_script_locatable() -> None:
    """The canonical build entrypoint exists and is importable."""
    script = REPO_ROOT / "scripts" / "build" / "build_tailwind.py"
    assert script.exists()
    mod = _load_python_module(script)
    assert hasattr(mod, "build") and hasattr(mod, "main")


def test_node_not_referenced_by_engine_runtime() -> None:
    """No Python source may spawn node/npm/npx/vite as a runtime subprocess."""

    hits: list[str] = []
    for py in (REPO_ROOT / "src").rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        low = text.lower()
        for token in ("node.exe", "npx ", "npm run", "vite", "webpack", "esbuild"):
            if token in low:
                # ignore incidental words (e.g. 'nodes' contains 'node' -> skip)
                if token == "node.exe" or token in (
                    "npx ",
                    "npm run",
                    "vite",
                    "webpack",
                    "esbuild",
                ):
                    hits.append(f"{py.name}: {token}")
    assert not hits, "engine runtime must not depend on Node tooling:\n" + "\n".join(hits)


def test_js_tests_workflow_declares_buildless() -> None:
    """The CI JS workflow must document the buildless contract."""
    wf = REPO_ROOT / ".github" / "workflows" / "js-tests.yml"
    assert wf.exists()
    text = wf.read_text(encoding="utf-8")
    assert "buildless" in text.lower()
    assert "node --check" in text  # syntax gate exists
