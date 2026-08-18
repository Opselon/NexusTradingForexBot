"""Canonical version & build metadata for the Nexus release system.

The SINGLE authoritative version source stays ``pyproject.toml``
(``[project] version``), exactly as the repository already defines it. This
module reads it dynamically (with a frozen fallback when running from a
packaged/cold environment where the project metadata is unavailable), so there
is exactly ONE version and no drift between source, PyInstaller build metadata
and the CLI ``nexus version`` output.

Build-time metadata (git commit, timestamp, architecture, channel, ...) is
provided either by the release build scripts (via a JSON file written into the
artifact tree) or regenerated at runtime from the environment.
"""

from __future__ import annotations

import importlib.metadata
import json
import platform
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PRODUCT_NAME = "NexusScalpEngine"
PRODUCT_DISPLAY = "Nexus Trading Forex Bot"

# Changelog-derived release channel this source tree represents. Do not let a
# beta/nightly artifact masquerade as a stable release: the build scripts
# stamp a channel explicitly (release.yml drafts prereleases for `vX.Y.Z-*`).
DEFAULT_CHANNEL = "stable"

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)([-+]?.*)?$")


def _git_commit(rev: str = "HEAD") -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", rev],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        sha = out.stdout.strip()
        return sha or None
    except Exception:
        return None


def _git_dirty() -> bool:
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return bool(out.stdout.strip())
    except Exception:
        return False


def _read_pyproject_version() -> str | None:
    """Read the version from pyproject.toml (source checkout)."""
    # Walk up from cwd to find pyproject.toml — works for source installs and
    # for the repo checkout; packaged installs fall back to dist metadata.
    root = Path.cwd()
    for _ in range(6):
        candidate = root / "pyproject.toml"
        if candidate.exists():
            try:
                text = candidate.read_text(encoding="utf-8")
                m = re.search(r"^version\s*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
                if m:
                    return m.group(1)
            except OSError:
                pass
        if root.parent == root:
            break
        root = root.parent
    return None


def _read_dist_version() -> str | None:
    try:
        return importlib.metadata.version("nexus-scalp-engine")
    except Exception:
        return None


def get_version() -> str:
    """Return the canonical version string (e.g. ``9.0.0``)."""
    for source in (_read_pyproject_version, _read_dist_version):
        try:
            v = source()
            if v:
                return v.lstrip("v")
        except Exception:
            continue
    # Frozen fallback for fully-cold bundled environments where neither the
    # checkout nor dist metadata is reachable (defensive only — the build
    # scripts always stamp a build-info file which is preferred).
    return "0.0.0"


def parse_version(version: str) -> tuple[int, int, int] | None:
    m = _VERSION_RE.match(version)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def get_build_info_file() -> Path | None:
    """Locate the stamped build-info.json produced by the release build.

    Candidates (first match wins):
        1. repo/source checkout root (``Path.cwd()``).
        2. next to the running executable — portable roots and onedir
           bundles carry build-info.json next to the EXE.
        3. inside a PyInstaller onedir ``_internal`` bundle.
        4. package-relative fallback for a repo checkout.
    """
    frozen = bool(getattr(sys, "frozen", False))
    exe_base = Path(sys.executable if frozen else Path(__file__).resolve()).parent
    if frozen:
        # A packaged EXE MUST report ITS OWN bundle identity — never the CWD
        # (version truth, BUG-092/093). Source/dev runs keep repo-cwd first.
        candidates = [
            exe_base / "build-info.json",
            exe_base / "_internal" / "build-info.json",
            Path.cwd() / "build-info.json",
            Path(__file__).resolve().parent.parent.parent.parent.parent / "build-info.json",
        ]
    else:
        candidates = [
            Path.cwd() / "build-info.json",
            exe_base / "build-info.json",
            exe_base / "_internal" / "build-info.json",
            Path(__file__).resolve().parent.parent.parent.parent.parent / "build-info.json",
        ]
    seen: set[Path] = set()
    for c in candidates:
        try:
            resolved = c.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists():
            return resolved
    return None


def read_build_info() -> dict[str, Any]:
    f = get_build_info_file()
    if f:
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def get_version_info() -> dict[str, Any]:
    """Full version/identity block for CLI, manifest and diagnostics.

    Returns a dict with keys: product, version, commit, dirty, build_timestamp,
    platform, architecture, python, channel, mode, schema. Never raises.
    """
    info = read_build_info()
    arch = info.get("architecture") or platform.machine()
    channel = info.get("channel") or DEFAULT_CHANNEL
    mode = info.get("build_mode") or "Release"
    return {
        "product": PRODUCT_NAME,
        "product_display": PRODUCT_DISPLAY,
        "version": info.get("version") or get_version(),
        "commit": info.get("git_commit") or _git_commit(),
        "dirty_tree": bool(info.get("dirty_tree", _git_dirty())),
        "build_timestamp": info.get("build_timestamp") or datetime.now(UTC).isoformat(),
        "platform": info.get("platform") or sys_platform(),
        "architecture": arch,
        "python": info.get("python") or platform.python_version(),
        "channel": channel,
        "build_mode": mode,
        "feature_schema": info.get("feature_schema") or "scalp_v1",
        "installer_version": info.get("installer_version") or "1.0.0",
    }


def sys_platform() -> str:
    return platform.system() or "unknown"


def is_windows() -> bool:
    return sys_platform().lower() == "windows"
