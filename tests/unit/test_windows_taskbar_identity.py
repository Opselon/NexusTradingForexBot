"""WINDOWS-UX-001 — Windows taskbar / shell application identity.

NSE is a console application: the real running process on a user's desktop is
``python.exe`` / ``pythonw.exe`` (source launch) or the PyInstaller bootloader
process of the packaged EXE. Windows derives the taskbar label, icon and
grouping from the process's AppUserModelID, and when an application never sets
one the shell falls back to the hosting executable image name — which is why a
normally-launched NSE showed up on the taskbar as ``python`` / ``Python``
instead of the product.

The fix is layered:

  * AppUserModelID  — one stable, deterministic ID, set BEFORE the console
                      window is shown (taskbar grouping + relaunch identity)
  * console title   — the visible window/taskbar text ("NexusTraderBot")
  * PE version-info — ProductName / FileDescription / InternalName /
                      OriginalFilename / CompanyName embedded in the EXE so
                      Explorer + Task Manager brand the image as the product

This suite pins all three layers plus the cross-platform safety contract:
identity is a no-op off Windows, never unconditionally imports Windows-only
APIs, and can never block the engine boot.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from nexus_scalp.platform import windows_identity as wident

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Identity constants — stable, deterministic, unique, consistent
# ---------------------------------------------------------------------------
def test_app_user_model_id_is_stable_and_deterministic() -> None:
    """The AppUserModelID is a module constant: identical on every launch."""
    assert wident.TASKBAR_APP_ID == "Opselon.NexusTraderBot"


def test_app_user_model_id_shape() -> None:
    """Company-qualified, reversed-domain, unique to this product."""
    assert re.match(r"^[A-Za-z][A-Za-z0-9.]*\.[A-Za-z][A-Za-z0-9.]*$", wident.TASKBAR_APP_ID), (
        "AppUserModelID must be a company-qualified dotted identifier"
    )
    assert wident.TASKBAR_APP_ID.endswith(".NexusTraderBot")


def test_visible_name_is_the_product_label() -> None:
    """The user-facing taskbar text is NexusTraderBot — never an internal ID."""
    assert wident.TASKBAR_APP_NAME == "NexusTraderBot"
    # The visible name must NOT be the raw AppUserModelID (they are separate
    # concepts: window title / app id / executable filename / product name).
    assert wident.TASKBAR_APP_NAME != wident.TASKBAR_APP_ID
    assert "." not in wident.TASKBAR_APP_NAME


def test_visible_name_does_not_leak_python() -> None:
    assert "python" not in wident.TASKBAR_APP_NAME.lower()
    assert "python" not in wident.TASKBAR_APP_ID.lower()


# ---------------------------------------------------------------------------
# Cross-platform import safety (Windows-only code must not break Linux/macOS CI)
# ---------------------------------------------------------------------------
def test_module_imports_on_all_platforms() -> None:
    """Importing the module can never fail off Windows.

    The Windows-only ``ctypes.windll`` binding is reached only inside the
    guarded ``sys.platform == "win32"`` body, so collecting this module on
    Linux/macOS CI is safe.
    """
    import importlib

    mod = importlib.reload(wident)
    assert mod.TASKBAR_APP_ID == wident.TASKBAR_APP_ID


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_apply_is_noop_off_windows() -> None:
    """Off Windows the identity call is a documented no-op (returns False)."""
    assert wident.apply_windows_identity() is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only behaviour")
def test_apply_and_readback_on_windows() -> None:
    """On Windows the AppUserModelID is actually applied to THIS process.

    This is the strongest deterministic assertion possible without a live
    taskbar: read the identity back through the shell API and require it to
    equal the canonical ID. Run-time taskbar verification (observing the
    taskbar label of a launched EXE) is documented in the PR and performed
    in scripts/build/start_smoke.ps1 shape on the release box.
    """
    applied = wident.apply_windows_identity()
    assert applied is True, "SetCurrentProcessExplicitAppUserModelID must succeed"
    readback = wident.current_app_user_model_id()
    assert readback == wident.TASKBAR_APP_ID, (
        f"AppUserModelID readback mismatch: {readback!r} != {wident.TASKBAR_APP_ID!r}"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only behaviour")
def test_apply_is_idempotent_and_stable() -> None:
    """Re-applying must keep ONE identity (no ghost taskbar groups on restart)."""
    wident.apply_windows_identity()
    first = wident.current_app_user_model_id()
    wident.apply_windows_identity()
    second = wident.current_app_user_model_id()
    assert first == second == wident.TASKBAR_APP_ID


def test_apply_is_failure_isolated() -> None:
    """A branding failure must never propagate into the engine boot path."""
    import builtins

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if "ctypes" in name:
            raise OSError("simulated ctypes failure")
        return real_import(name, *args, **kwargs)

    # Only reachable on Windows; off Windows apply() returns before importing.
    if sys.platform != "win32":
        pytest.skip("Windows-only failure path")
    builtins.__import__ = _boom  # type: ignore[method-assign]
    try:
        assert wident.apply_windows_identity() is False
    finally:
        builtins.__import__ = real_import  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Startup ordering — identity is established BEFORE the GUI/taskbar registration
# ---------------------------------------------------------------------------
def _source_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_packaged_main_sets_identity_before_cli_import() -> None:
    """The packaged EXE pins the ID before the CLI tree is imported."""
    src = _source_text(REPO / "src" / "nexus_scalp" / "release" / "packaged_main.py")
    idx_identity = src.index("apply_windows_identity()")
    idx_cli = src.index("from nexus_scalp.cli.main import app")
    assert idx_identity < idx_cli, "identity must be applied BEFORE the CLI is imported"


def test_cli_shim_sets_identity_before_cli_import() -> None:
    src = _source_text(REPO / "src" / "nexus_scalp" / "release" / "cli_shim.py")
    idx_identity = src.index("apply_windows_identity()")
    idx_cli = src.index("from nexus_scalp.cli.main import app")
    assert idx_identity < idx_cli


def test_engine_boot_sets_identity_before_engine_boot() -> None:
    """The `nexus start` path applies the identity at the top of the boot lock."""
    src = _source_text(REPO / "src" / "nexus_scalp" / "cli" / "engine_boot.py")
    idx_identity = src.index("apply_windows_identity()")
    # The workspace anchor is the first boot side effect; identity precedes it.
    idx_anchor = src.index("rboot.anchor_workspace()")
    assert idx_identity < idx_anchor


def test_legacy_launcher_sets_identity_before_banner() -> None:
    """The source launcher (python NexusTradingForexBot.py) brands before render."""
    src = _source_text(REPO / "NexusTradingForexBot.py")
    idx_identity = src.index("apply_windows_identity()")
    idx_banner = src.index("def display_startup_banner")
    # apply_windows_identity() is at module import, display_startup_banner at
    # first render — module-level code always runs first by construction; pin
    # that ordering so a future move of the call AFTER the banner is caught.
    assert idx_identity < idx_banner
    # The banner itself pins the VISIBLE label (window title = taskbar text
    # once the AppUserModelID has pinned the entry).
    assert 'console.set_window_title("NexusTraderBot")' in src[idx_banner:]


def _py_files_with_pattern(pattern: str) -> list[str]:
    """Filesystem grep over the package + root launchers (untracked-safe)."""
    import re

    rx = re.compile(pattern)
    roots = [REPO / "src", REPO / "scripts", REPO / "tests"]
    hits: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if rx.search(text):
                rel = path.relative_to(REPO).as_posix()
                hits.append(rel)
    for name in ("NexusTradingForexBot.py", "main.py"):
        p = REPO / name
        if p.exists() and rx.search(p.read_text(encoding="utf-8", errors="ignore")):
            hits.append(name)
    return hits


# ---------------------------------------------------------------------------
# PE version-info resource — packaged executable metadata coherence
# ---------------------------------------------------------------------------
def _version_info_fields() -> dict[str, str]:
    """Parse the generated PyInstaller version-info resource into a dict."""
    path = REPO / "installer" / "version_info.txt"
    if not path.exists():
        pytest.skip("version_info.txt is generated at build time")
    text = path.read_text(encoding="utf-8")
    fields: dict[str, str] = {}
    for m in re.finditer(r"StringStruct\('(\w+)',\s*'([^']*)'\)", text):
        fields[m.group(1)] = m.group(2)
    return fields


def test_generator_produces_coherent_pe_metadata(tmp_path: Path) -> None:
    out = tmp_path / "version_info.txt"
    wpath = wident.__file__  # keep import surface referenced
    assert wpath
    from scripts.build.generate_version_info import generate_version_info

    generate_version_info("9.0.14", output=out, original_filename="NexusScalpEngine.exe")
    text = out.read_text(encoding="utf-8")
    assert "VSVersionInfo(" in text
    assert "ProductName', 'NexusTraderBot'" in text
    assert "FileDescription', 'NexusTraderBot'" in text
    assert "InternalName', 'NexusTraderBot'" in text
    assert "OriginalFilename', 'NexusScalpEngine.exe'" in text
    assert "CompanyName', 'Opselon'" in text
    # The visible PE product name must be the NexusTraderBot label, never Python.
    assert "Python" not in text.replace("All rights reserved", "")


def test_generated_version_info_fields() -> None:
    fields = _version_info_fields()
    assert fields.get("ProductName") == "NexusTraderBot"
    assert fields.get("FileDescription") == "NexusTraderBot"
    assert fields.get("InternalName") == "NexusTraderBot"
    assert fields.get("OriginalFilename") == "NexusScalpEngine.exe"
    assert fields.get("CompanyName") == "Opselon"


def test_pyinstaller_builds_pass_version_file() -> None:
    """Both PyInstaller invocations embed the PE version-info resource."""
    iss_space = "   "
    src = _source_text(REPO / ".github" / "workflows" / "release.yml")
    assert '--version-file "installer\\version_info.txt"' in src, (
        "release.yml onedir build must pass --version-file"
    )
    assert src.count("--version-file") >= 2, "onedir AND onefile CLI must both brand the PE"
    # The local orchestrator must do the same (keeps local builds honest).
    ps1 = _source_text(REPO / "scripts" / "build" / "build_release.ps1")
    assert "--version-file $VersionInfoPath" in ps1
    assert ps1.count("--version-file") >= 2
    assert "generate_version_info.py" in ps1
    assert iss_space  # placeholder to avoid unused-var lint on a literal


def test_release_yaml_generates_version_info_before_pyinstaller() -> None:
    """The resource is generated BEFORE the PyInstaller steps consume it."""
    src = _source_text(REPO / ".github" / "workflows" / "release.yml")
    idx_gen = src.index("Generate PE version-info resource")
    idx_build = src.index("PyInstaller onedir")
    assert idx_gen < idx_build


# ---------------------------------------------------------------------------
# No duplicate / competing launcher — one canonical identity implementation
# ---------------------------------------------------------------------------
def test_single_canonical_identity_helper() -> None:
    """SetCurrentProcessExplicitAppUserModelID lives in ONE module only."""
    # Source modules (scripts + src) that name the Win32 API.
    # Exclude the test itself from the search so it does not count as a hit.
    hits = [
        h
        for h in _py_files_with_pattern("SetCurrentProcessExplicitAppUserModelID")
        if not h.startswith("tests/")
    ]
    assert hits == ["src/nexus_scalp/platform/windows_identity.py"], (
        f"AppUserModelID must be set from exactly ONE module, found: {hits}"
    )


def test_no_duplicate_launcher_created() -> None:
    """The taskbar fix must not introduce a competing launcher."""
    root = REPO
    for name in (
        "NexusTraderBotLauncher.py",
        "launcher.py",
        "startup.py",
        "desktop.py",
        "bootstrap.py",
    ):
        assert not (root / name).exists(), f"competing launcher introduced: {name}"


def test_console_title_pinned_to_product_label() -> None:
    """The visible console window text is NexusTraderBot."""
    src = _source_text(REPO / "NexusTradingForexBot.py")
    assert 'console.set_window_title("NexusTraderBot")' in src


# ---------------------------------------------------------------------------
# Installer & shortcut relaunch surface (Inno Setup)
# ---------------------------------------------------------------------------
def test_installer_script_names_nexus_trader_bot() -> None:
    """Inno Setup declares MyAppName/MyAppPublisher as NexusTraderBot/Opselon."""
    src = _source_text(REPO / "installer" / "NexusScalpEngine.iss")
    assert '#define MyAppName "NexusTraderBot"' in src
    assert '#define MyAppPublisher "Opselon"' in src
    assert 'Name: "{group}\\NexusTraderBot"' in src
    assert 'Name: "{autodesktop}\\NexusTraderBot"' in src
