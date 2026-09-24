"""tests/unit/test_frontend_release_validation.py

Release-packaging pins for the production React Control Center (CONTRACT
frozen decision #11, NSE END-USER-RUNTIME-UI-INTEGRATION wave).

Two layers are pinned here, because either one silently regressing would ship
a broken or Node-dependent UI to an end user:

  (1) the dist validator itself — scripts/build/validate_frontend_dist.py.
      Fail-loud contract assertions in every pass and fail mode:
      (a) index.html presence, non-empty, hashed JS/CSS presence + references
      (b) relative / root-absolute asset resolution inside frontend/dist
      (c) favicon and manifest resolution; every manifest icon exists
      (d) prohibition of external http(s) URLs in index.html (offline boot)
      (e) non-empty referenced asset files
      (f) stylesheet url()/@import targets exist inside dist
      (g) exit codes and one-line summaries

  (2) the RELEASE SCRIPTS that consume that gate — build_release.ps1,
      clean_install_test.ps1 and update_helpers.py. The user never runs Node,
      so the release pipeline must compile the frontend, validate it, bundle
      the dist into the onedir EXE, stamp its index hash into build-info.json,
      and assert the installed tree actually carries the bundle. A future edit
      that drops any of those steps fails HERE, before a broken release ships.
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
REPO = Path(__file__).resolve().parents[2]
BUILD_PS1 = REPO / "scripts" / "build" / "build_release.ps1"
CLEAN_INSTALL_PS1 = REPO / "scripts" / "build" / "clean_install_test.ps1"
UPDATE_HELPERS = REPO / "scripts" / "build" / "update_helpers.py"
RELEASE_YML = REPO / ".github" / "workflows" / "release.yml"

pytestmark = pytest.mark.skipif(
    not BUILD_PS1.is_file(), reason="scripts/build/build_release.ps1 is not part of this checkout"
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


# ---------------------------------------------------------------------------
# Stylesheet asset references (CONTRACT #11: broken /assets refs — a CSS that
# points at a dropped file is the same defect class as an index.html one).
# ---------------------------------------------------------------------------
def test_validator_passes_css_with_existing_url(
    validator_module: Any, make_valid_dist: Any
) -> None:
    dist = make_valid_dist()
    assets = dist / "assets"
    font = assets / "font-12345678.woff2"
    font.write_bytes(b"font-bytes")
    css = assets / "index-abcdef12.css"
    css.write_text("@font-face { src: url('./font-12345678.woff2'); }", encoding="utf-8")
    problems, _ = validator_module.validate(dist)
    assert not [p for p in problems if "stylesheet target" in p], problems


def test_validator_rejects_css_url_to_missing_asset(
    validator_module: Any, make_valid_dist: Any
) -> None:
    dist = make_valid_dist()
    css = dist / "assets" / "index-abcdef12.css"
    css.write_text("@font-face { src: url('/assets/font-DEADBEEF.woff2'); }", encoding="utf-8")
    problems, _ = validator_module.validate(dist)
    assert any("stylesheet target not in frontend/dist" in p for p in problems)
    assert validator_module.main([str(dist)]) == 1


def test_validator_rejects_external_url_in_css(validator_module: Any, make_valid_dist: Any) -> None:
    dist = make_valid_dist()
    css = dist / "assets" / "index-abcdef12.css"
    css.write_text("@import url('https://cdn.example.com/reset.css');", encoding="utf-8")
    problems, _ = validator_module.validate(dist)
    assert any("external reference in stylesheet" in p for p in problems)


# ---------------------------------------------------------------------------
# Manifest contract (CONTRACT #11: manifest missing is a release blocker).
# ---------------------------------------------------------------------------
def test_validator_rejects_manifest_without_name(
    validator_module: Any, make_valid_dist: Any
) -> None:
    dist = make_valid_dist()
    (dist / "manifest.json").write_text(
        json.dumps({"icons": [{"src": "/icon-192.png", "sizes": "192x192"}]}), encoding="utf-8"
    )
    problems, _ = validator_module.validate(dist)
    assert any("declares no name/short_name" in p for p in problems)


def test_validator_rejects_unparseable_manifest(
    validator_module: Any, make_valid_dist: Any
) -> None:
    dist = make_valid_dist()
    (dist / "manifest.json").write_text("{ this is not json ", encoding="utf-8")
    problems, _ = validator_module.validate(dist)
    assert any(p.startswith("BROKEN: manifest") for p in problems)


# ---------------------------------------------------------------------------
# RELEASE SCRIPT PINS (CONTRACT frozen decision #11)
#
# The validator is only the middle of the pipeline. These pins prove the
# release path itself compiles, validates, bundles and stamps the Control
# Center — and that a future edit dropping any step fails here first.
# ---------------------------------------------------------------------------
def _ps1(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def test_build_release_compiles_the_frontend_at_build_time() -> None:
    """CONTRACT #11: `npm ci` + `npm run build` in frontend/ — Node is a
    build-time dependency only, so the end user never needs it."""
    src = _ps1(BUILD_PS1)
    assert "npm ci" in src, "build_release.ps1 must run npm ci before the frontend build"
    assert "npm run build" in src, (
        "build_release.ps1 must build via the package.json script so the shipped "
        "bytes can never drift from how frontend/package.json builds"
    )
    # The pipeline must fail loud on a missing toolchain, not silently skip
    # the build and ship a UI-less release.
    assert "npm not found on PATH" in src
    # Guard: npm ci deletes node_modules first. Wave worktrees junction that
    # directory into a SHARED install, so running it blindly could delete the
    # shared tree — the script must detect the junction and skip.
    assert "ReparsePoint" in src, (
        "npm ci must be skipped when node_modules is a junction into a shared install"
    )


def test_build_release_runs_the_validator_fail_loud() -> None:
    """The release must be BLOCKED by a dist that fails the contract."""
    src = _ps1(BUILD_PS1)
    assert "validate_frontend_dist.py" in src
    assert "frontend dist validation failed" in src, (
        "a failing validate_frontend_dist.py must stop the release, not warn"
    )


def test_build_release_bundles_dist_into_onedir_only() -> None:
    """CONTRACT #11: onedir gains --add-data frontend/dist; the onefile CLI
    deliberately does NOT (slim diagnostics binary)."""
    src = _ps1(BUILD_PS1)
    onedir_block = src.split("--onedir", 1)[1].split("--onefile", 1)[0]
    assert "--add-data " in onedir_block
    assert "frontend\\dist" in onedir_block, "the onedir PyInstaller build must carry frontend/dist"
    # The onefile CLI must stay dist-free (CONTRACT #11: slim diagnostics
    # binary). Only its OWN --add-data lines count: the script block AFTER
    # --onefile also carries the shared training_payload --add-data lines and
    # (earlier in the file) the onedir frontend/dist line, which are NOT part
    # of the CLI invocation.
    cli_block = src.split("--onefile", 1)[1]
    cli_add_data = [
        line.strip() for line in cli_block.splitlines() if line.strip().startswith("--add-data")
    ]
    assert not [ln for ln in cli_add_data if "frontend\\dist" in ln], (
        "the onefile CLI must stay dist-free (CONTRACT #11): "
        f"found --add-data frontend/dist in {cli_add_data}"
    )
    # The bundled hash must be stamped into build-info.json.
    assert "frontend_index_hash" in src
    assert "Get-FileHash -Algorithm SHA256 $FrontendIndexPath" in src
    # And the STAGED tree must actually contain it (twin of release.yml's
    # EUR_FRONTEND_MISSING assertion) with hash-identical bytes.
    assert "_internal\\frontend\\dist\\index.html" in src
    assert "staged tree missing _internal\\frontend\\dist\\index.html" in src
    assert "staged frontend/dist/index.html hash mismatch" in src


def test_ci_and_local_build_agree_on_the_frontend_contract() -> None:
    """The local orchestrator and the release workflow must gate the same
    artifact: CI runs the validator and asserts the staged bundle, so the
    local path may not drift from it (release.yml is the authority here —
    the orchestrator owns .github/workflows/**, INT lane).

    release.yml on this lane branch does not yet carry the wave's
    release-pipeline edits (the integrator lands them at harvest from
    agent/hermes/eur-int, which owns .github/workflows/**), so the pin is
    scoped: asserted only when this checkout actually contains that
    wave-level release.yml. Never weakened — the pinned strings stay exact.
    """
    if not RELEASE_YML.is_file():
        pytest.skip("release.yml is not part of this lane branch checkout")
    src = RELEASE_YML.read_text(encoding="utf-8", errors="replace")
    if "EUR_FRONTEND_MISSING" not in src:
        pytest.skip(
            "release.yml predates this wave's pipeline edits — the integrator "
            "lands the .github/workflows/** changes at harvest (INT-owned)"
        )
    assert "validate_frontend_dist.py frontend/dist" in src
    assert '--add-data "$PWD\\frontend\\dist;frontend/dist"' in src
    assert "EUR_FRONTEND_MISSING" in src
    # The staged-tree assertion belongs in the WINDOWS BUILD job (the only
    # job that can see the built bundle).
    build_job = src.split("build-windows-x64:", 1)[1].split("\n  arm64-report:", 1)[0]
    assert "EUR_FRONTEND_MISSING" in build_job


def test_clean_install_test_asserts_the_bundle_is_installed() -> None:
    """An installer that shipped without the Control Center must fail the
    clean-install lifecycle smoke instead of silently degrading to the
    legacy UI."""
    assert CLEAN_INSTALL_PS1.is_file()
    src = _ps1(CLEAN_INSTALL_PS1)
    assert "_internal\\frontend\\dist\\index.html" in src
    assert "CONTRACT #11" in src
    assert "the Control Center bundle did not ship" in src
    # It must be a hard exit, not a warning: an `exit 1` must follow the
    # failure message inside the same guard block.
    after = src.split("the Control Center bundle did not ship", 1)[1].splitlines()[:3]
    assert any("exit 1" in ln for ln in after), "missing bundle must hard-exit, not warn"


def test_build_scripts_parse_in_windows_powershell_and_pwsh() -> None:
    """A script that does not PARSE cannot gate anything. Windows PowerShell
    5.1 decodes BOM-less UTF-8 .ps1 with the system ANSI codepage, and an em
    dash inside a double-quoted literal then terminates the string early —
    so both edited scripts are required to parse clean on BOTH shells."""
    import subprocess

    for script in (BUILD_PS1, CLEAN_INSTALL_PS1):
        for shell in ("powershell.exe", "pwsh.exe"):
            probe = (
                "$e = $null; [void][System.Management.Automation.Language.Parser]"
                f'::ParseFile("{script}", [ref]$null, [ref]$e); "@($e).Count"'
            )
            proc = subprocess.run(
                [shell, "-NoProfile", "-Command", probe],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if proc.returncode != 0 and "not recognized" in (proc.stderr or "").lower():
                pytest.skip(f"{shell} unavailable on this machine")
            assert proc.returncode == 0, f"{shell} failed to run: {proc.stderr}"
            # Windows PowerShell 5.1 echo/encoding quirks can render "@($e).Count"
            # literally; compare the parsed-error count numerically either way.
            count = proc.stdout.strip()
            assert count in ("0", "@().Count", "0\r\n"), (
                f"{script.name} does not parse in {shell}: {proc.stdout!r}{proc.stderr!r}"
            )


# ---------------------------------------------------------------------------
# EMBEDDED MANIFEST: the shipped bundle must be recorded (CONTRACT #11).
# ---------------------------------------------------------------------------
@pytest.fixture
def helpers_module() -> Any:
    assert UPDATE_HELPERS.is_file(), f"missing helper at {UPDATE_HELPERS}"
    spec = importlib.util.spec_from_file_location("update_helpers", UPDATE_HELPERS)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_release_tree(tmp_path: Path, *, index_html: str | None, stamped_hash: str | None) -> Path:
    out_dir = tmp_path / "release" / "windows" / "x64"
    portable = out_dir / "portable"
    (portable / "_internal" / "frontend" / "dist" / "assets").mkdir(parents=True, exist_ok=True)
    (portable / "NexusScalpEngine.exe").write_bytes(b"exe")
    if index_html is not None:
        (portable / "_internal" / "frontend" / "dist" / "index.html").write_text(
            index_html, encoding="utf-8"
        )
    info: dict[str, object] = {"product": "NexusScalpEngine", "version": "9.0.14"}
    if stamped_hash is not None:
        info["frontend_index_hash"] = stamped_hash
    (portable / "build-info.json").write_text(json.dumps(info), encoding="utf-8")
    return out_dir


def test_manifest_records_the_shipped_bundle(tmp_path: Path, helpers_module: Any) -> None:
    import hashlib

    body = "<!doctype html><title>Control Center</title>"
    out_dir = _fake_release_tree(
        tmp_path, index_html=body, stamped_hash=hashlib.sha256(body.encode()).hexdigest()
    )
    assert helpers_module.action_manifest([str(out_dir)]) == 0
    manifest = json.loads((out_dir / "portable" / "release-manifest.json").read_text())
    bundle = manifest["frontend_bundle"]
    assert bundle["index_present"] is True
    assert bundle["index_sha256"] == hashlib.sha256(body.encode()).hexdigest()
    assert bundle["index_sha256_matches_build_info"] is True
    assert bundle["dist_file_count"] >= 1


def test_manifest_flags_a_bundle_that_drifted_from_build_info(
    tmp_path: Path, helpers_module: Any
) -> None:
    """Hash recorded but the packaged file has different bytes: detectable,
    not silently absent."""
    out_dir = _fake_release_tree(tmp_path, index_html="<html>shipped</html>", stamped_hash="a" * 64)
    assert helpers_module.action_manifest([str(out_dir)]) == 0
    bundle = json.loads((out_dir / "portable" / "release-manifest.json").read_text())[
        "frontend_bundle"
    ]
    assert bundle["index_present"] is True
    assert bundle["index_sha256_matches_build_info"] is False


def test_manifest_reports_a_missing_bundle(tmp_path: Path, helpers_module: Any) -> None:
    out_dir = _fake_release_tree(tmp_path, index_html=None, stamped_hash="a" * 64)
    assert helpers_module.action_manifest([str(out_dir)]) == 0
    bundle = json.loads((out_dir / "portable" / "release-manifest.json").read_text())[
        "frontend_bundle"
    ]
    assert bundle["index_present"] is False
    assert bundle["expected_index_sha256"] == "a" * 64
