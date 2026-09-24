"""tests/unit/test_frontend_release_validation.py

Tests for scripts/build/validate_frontend_dist.py against CONTRACT frozen
decisions #10 and #11 (NSE END-USER-RUNTIME-UI-INTEGRATION wave).

Verifies the fail-loud contract validator in all pass and fail modes:
  (a) index.html presence, non-empty, hashed JS/CSS presence + references
  (b) relative / root-absolute asset resolution inside frontend/dist
  (c) favicon and manifest resolution; every manifest icon exists
  (d) prohibition of external http(s) URLs in index.html (offline boot)
  (e) non-empty referenced asset files
  (f) exit codes and one-line summaries
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "build" / "validate_frontend_dist.py"
)


@pytest.fixture(scope="session")
def validator_module() -> Any:
    assert MODULE_PATH.is_file(), f"missing validator at {MODULE_PATH}"
    spec = importlib.util.spec_from_file_location("validate_frontend_dist", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def make_valid_dist(tmp_path: Path):
    """Factory fixture building a minimal, fully valid frontend/dist fixture."""

    def _factory(base: str = "/") -> Path:
        dist = tmp_path / "frontend" / "dist"
        dist.mkdir(parents=True, exist_ok=True)
        assets = dist / "assets"
        assets.mkdir(parents=True, exist_ok=True)

        # Hashed assets (Decision #10: at least one .js and one .css)
        js_file = assets / "index-12345678.js"
        js_file.write_text("console.log('boot');", encoding="utf-8")
        css_file = assets / "index-abcdef12.css"
        css_file.write_text(":root { color: #fff; }", encoding="utf-8")

        # Static root assets
        favicon = dist / "favicon.ico"
        favicon.write_bytes(b"\x00\x00\x01\x00")
        icon192 = dist / "icon-192.png"
        icon192.write_bytes(b"\x89PNG\r\n\x1a\n")

        manifest = dist / "manifest.json"
        manifest_data = {
            "name": "Nexus Scalp Engine — Control Center",
            "short_name": "NSE",
            "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"}],
        }
        manifest.write_text(json.dumps(manifest_data), encoding="utf-8")

        # Sibling vite.config.ts with matching base
        vite_cfg = dist.parent / "vite.config.ts"
        vite_cfg.write_text(
            f'import {{ defineConfig }} from "vite";\nexport default defineConfig({{ base: "{base}" }});\n',
            encoding="utf-8",
        )

        # Index referencing the above
        pfx = "" if base == "/" else base.rstrip("/")
        index_html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>Nexus Scalp Engine — Control Center</title>
    <link rel="icon" href="{pfx}/favicon.ico" />
    <link rel="manifest" href="{pfx}/manifest.json" />
    <script type="module" crossorigin src="{pfx}/assets/index-12345678.js"></script>
    <link rel="stylesheet" crossorigin href="{pfx}/assets/index-abcdef12.css" />
  </head>
  <body><div id="root"></div></body>
</html>
"""
        (dist / "index.html").write_text(index_html, encoding="utf-8")
        return dist

    return _factory


def test_validator_passes_on_valid_fixture(validator_module: Any, make_valid_dist: Any) -> None:
    dist = make_valid_dist(base="/")
    problems, summary = validator_module.validate(dist)
    assert problems == [], f"expected no problems, got: {problems}"
    assert "resolved refs" in summary
    assert "1 hashed js" in summary
    assert "1 hashed css" in summary
    # main() entrypoint exit code
    exit_code = validator_module.main([str(dist)])
    assert exit_code == 0


def test_validator_supports_alt_base(validator_module: Any, make_valid_dist: Any) -> None:
    dist = make_valid_dist(base="/alt/")
    problems, _ = validator_module.validate(dist)
    assert problems == [], f"base=/alt/ should resolve cleanly: {problems}"


def test_validator_rejects_missing_index(validator_module: Any, make_valid_dist: Any) -> None:
    dist = make_valid_dist()
    (dist / "index.html").unlink()
    problems, _ = validator_module.validate(dist)
    assert any("index.html not found" in p for p in problems)
    assert validator_module.main([str(dist)]) == 1


def test_validator_rejects_empty_index(validator_module: Any, make_valid_dist: Any) -> None:
    dist = make_valid_dist()
    (dist / "index.html").write_text("   \n\t  ", encoding="utf-8")
    problems, _ = validator_module.validate(dist)
    assert any("empty" in p for p in problems)


def test_validator_rejects_external_http_script(
    validator_module: Any, make_valid_dist: Any
) -> None:
    dist = make_valid_dist()
    idx = dist / "index.html"
    idx.write_text(
        idx.read_text(encoding="utf-8").replace(
            "</head>", '<script src="https://cdn.jsdelivr.net/npm/vue"></script></head>'
        ),
        encoding="utf-8",
    )
    problems, _ = validator_module.validate(dist)
    assert any("external http(s) URL" in p for p in problems)
    assert validator_module.main([str(dist)]) == 1


def test_validator_rejects_missing_referenced_asset(
    validator_module: Any, make_valid_dist: Any
) -> None:
    dist = make_valid_dist()
    (dist / "assets" / "index-12345678.js").unlink()
    problems, _ = validator_module.validate(dist)
    assert any("MISSING: referenced file not in frontend/dist" in p for p in problems)
    assert any("no hashed assets/*.js" in p for p in problems)


def test_validator_rejects_empty_referenced_asset(
    validator_module: Any, make_valid_dist: Any
) -> None:
    dist = make_valid_dist()
    (dist / "assets" / "index-abcdef12.css").write_text("", encoding="utf-8")
    problems, _ = validator_module.validate(dist)
    assert any(
        "BROKEN: referenced asset file is empty: assets/index-abcdef12.css" in p for p in problems
    )


def test_validator_rejects_path_escape(validator_module: Any, make_valid_dist: Any) -> None:
    dist = make_valid_dist()
    idx = dist / "index.html"
    idx.write_text(
        idx.read_text(encoding="utf-8").replace("</body>", '<img src="../../etc/passwd" /></body>'),
        encoding="utf-8",
    )
    problems, _ = validator_module.validate(dist)
    assert any("escapes frontend/dist" in p for p in problems)


def test_validator_rejects_missing_manifest_icon(
    validator_module: Any, make_valid_dist: Any
) -> None:
    dist = make_valid_dist()
    (dist / "icon-192.png").unlink()
    problems, _ = validator_module.validate(dist)
    assert any("manifest icon not in frontend/dist: /icon-192.png" in p for p in problems)


def test_validator_rejects_manifest_with_empty_icons(
    validator_module: Any, make_valid_dist: Any
) -> None:
    dist = make_valid_dist()
    (dist / "manifest.json").write_text(json.dumps({"name": "NSE", "icons": []}), encoding="utf-8")
    problems, _ = validator_module.validate(dist)
    assert any("manifest declares no icons" in p for p in problems)


def test_validator_rejects_missing_favicon_links(
    validator_module: Any, make_valid_dist: Any
) -> None:
    dist = make_valid_dist()
    idx = dist / "index.html"
    # Remove favicon link
    lines = [ln for ln in idx.read_text(encoding="utf-8").splitlines() if 'rel="icon"' not in ln]
    idx.write_text("\n".join(lines), encoding="utf-8")
    problems, _ = validator_module.validate(dist)
    assert any("declares no favicon" in p for p in problems)
