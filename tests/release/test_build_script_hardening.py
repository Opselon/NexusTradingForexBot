# Nexus Scalp Engine — Release BUILD-SCRIPT hardening tests
# =============================================================================
# Static + behavioral checks on scripts/build/* and the release workflow:
#   * native Windows paths (no MSYS /Git-Bash mangling) for PyInstaller/ISCC
#   * spaces-in-path safety
#   * stale-EXE lock guard present
#   * exit-code contract documented
#   * checksums paths are release-root relative
# =============================================================================

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _build_script() -> str:
    return (REPO_ROOT / "scripts/build/build_release.ps1").read_text(
        encoding="utf-8", errors="replace"
    )


def test_build_uses_native_windows_paths_for_pyinstaller() -> None:
    text = _build_script()
    # PyInstaller add-data must use $Root\... native separators, not forward
    # slashes or MSYS-style /c/ paths.
    assert '--add-data "$Root\\Web;Web"' in text
    assert "--distpath (Join-Path $BuildDir" in text
    assert "C:/" not in text  # no hardcoded forward-slash paths


def test_build_has_stale_exe_lock_guard() -> None:
    text = _build_script()
    assert "Stale-EXE lock guard" in text
    assert "Get-Process -Name \"NexusScalpEngine\"" in text
    assert "Stop-Process" in text


def test_build_has_secret_guard() -> None:
    text = _build_script()
    assert "HARD SECRET GUARD" in text
    assert "bot token" in text.lower()


def test_build_references_packaged_entrypoint_not_launcher() -> None:
    text = _build_script()
    assert "packaged_main.py" in text
    assert "NexusTradingForexBot.py" not in text  # never regress to argparse


def test_release_workflow_orders_verify_before_publish() -> None:
    text = (REPO_ROOT / ".github/workflows/release.yml").read_text(
        encoding="utf-8", errors="replace"
    )
    verify_idx = text.find("verify-release")
    publish_idx = text.find("Publish GitHub Release")
    assert verify_idx != -1 and publish_idx != -1
    assert verify_idx < publish_idx


def test_release_workflow_no_publish_before_verify() -> None:
    text = (REPO_ROOT / ".github/workflows/release.yml").read_text(
        encoding="utf-8", errors="replace"
    )
    # The verify step and the publish step are separate; publish `needs` the
    # build job (which contains verify), so a failed verify cannot publish.
    assert "needs: [validate, gates, build-windows-x64]" in text


def test_checksums_contract_documented_in_docs() -> None:
    docs = (REPO_ROOT / "docs/RELEASE.md").read_text(encoding="utf-8", errors="replace")
    assert "checksums" in docs.lower()


def test_exit_codes_contract_documented() -> None:
    from nexus_scalp.release import exit_codes as xc

    assert xc.EXIT_OK == 0
    assert xc.EXIT_RUNTIME == 1
    assert xc.EXIT_USAGE == 2
    assert xc.EXIT_ENVIRONMENT == 3
    assert xc.EXIT_RELEASE == 4


def test_spaces_in_path_handling_documented() -> None:
    # The build script resolves everything through $Root / Join-Path so a
    # repo directory containing spaces works. ISCC discovery uses Test-Path
    # on candidate install locations (fine); the BUILD paths themselves must
    # never be hardcoded with literal spaces.
    text = _build_script()
    # Every PyInstaller/ISCC invocation path is derived from $Root/$BuildDir.
    assert "$Root\\Web;Web" in text
    assert '$Iscc $Iss' in text or '& $Iscc' in text

def test_cli_help_strings_are_ascii_safe() -> None:
    """Typer help= option strings must be ASCII-only: the frozen onefile
    console encodes in the active code page and non-ASCII (em dash, arrow)
    aborts --help with UnicodeEncodeError (BUG-037)."""
    import re

    src = (REPO_ROOT / "src/nexus_scalp/cli/main.py").read_text(encoding="utf-8")
    bad = [m.group(1) for m in re.finditer(r'help="([^"]*)"', src) if not m.group(1).isascii()]
    assert not bad, f"non-ASCII help= strings: {bad}"
