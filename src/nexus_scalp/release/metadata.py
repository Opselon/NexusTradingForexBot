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
from pathlib import Path
from typing import Any

PRODUCT_NAME = "NexusScalpEngine"
PRODUCT_DISPLAY = "Nexus Trading Forex Bot"

# Changelog-derived release channel this source tree represents. Do not let a
# beta/nightly artifact masquerade as a stable release: the build scripts
# stamp a channel explicitly (release.yml drafts prereleases for `vX.Y.Z-*`).
DEFAULT_CHANNEL = "stable"

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)([-+]?.*)?$")


def _repo_root() -> Path | None:
    """Best-effort repo root for git-anchored probes.

    Uses the install root derived from ``__file__`` when it still carries a
    ``.git`` directory (repo checkout) or is a child of one (installed
    venv engine at <NexusHome>/engine). That makes ``nexus version`` CWD-
    independent so it reports the same commit from *C:\\Users\\Capsizer* or
    from *C:\\NexusTradingForexBot*. Returns ``None`` outside a git checkout
    (packaged / truly non-git environments).
    """
    try:
        # __file__ = <root>/src/nexus_scalp/release/metadata.py — so the
        # install root is 4 levels up. Installed venv engines live as
        # <NexusHome>/engine (a separate clone), not as parent of src/ in
        # the installed tree — check both the derived root and CWD walk.
        probe = Path(__file__).resolve().parent.parent.parent.parent
        # direct: <root>/.git
        if (probe / ".git").is_dir():
            return probe
        # installed venv engine case: probe is <NexusHome> (no .git), engine
        # clone is at <NexusHome>/engine/.git — the install's source of truth
        engine_probe = probe / "engine"
        if (engine_probe / ".git").is_dir():
            return engine_probe
        # Editable or repo-Python: also try CWD-anchored walk as fallback
        # (get_build_info_file already prefers CWD for non-frozen runs; this
        # keeps the git probe consistent with the version source).
        probe = Path.cwd()
        for _ in range(6):
            if (probe / ".git").is_dir():
                return probe
            if probe.parent == probe:
                break
            probe = probe.parent
    except Exception:
        pass
    return None


def _git_commit(rev: str = "HEAD") -> str | None:
    try:
        root = _repo_root()
        cwd = str(root) if root is not None else None
        out = subprocess.run(
            ["git", "rev-parse", "--short", rev],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            cwd=cwd,
        )
        sha = out.stdout.strip()
        return sha or None
    except Exception:
        return None


def _git_dirty() -> bool:
    try:
        root = _repo_root()
        cwd = str(root) if root is not None else None
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            cwd=cwd,
        )
        return bool(out.stdout.strip())
    except Exception:
        return False


def _git_commit_timestamp_iso() -> str | None:
    """Author date (%cI) of HEAD in the anchored repo — used to populate
    ``build_timestamp`` on dev/source runs that have no CI build stamp.
    Returns the ISO 8601 string (e.g. ``2026-09-04T06:30:33+03:30``) or
    ``None`` outside a git checkout / on git failure.
    """
    try:
        root = _repo_root()
        cwd = str(root) if root is not None else None
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cI"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            cwd=cwd,
        )
        ts = out.stdout.strip()
        return ts or None
    except Exception:
        return None


def _read_pyproject_version() -> str | None:
    """Read the version from pyproject.toml (source checkout)."""
    # Prefer the install/repo root derived from __file__ (CWD-independent);
    # fall back to a CWD-anchored walk so `nexus version` reports the same
    # version from C:\\Users\\Capsizer and from inside the repo (and keeps the
    # get_build_info_file CWD precedence contract for dev runs).
    probe_root = Path(__file__).resolve().parent.parent.parent.parent
    # installed venv engine case: src/ lives under <NexusHome>/engine/src,
    # so probe_root above is <NexusHome> (no pyproject); engine checkout
    # is at <NexusHome>/engine/pyproject.toml — check it first.
    engine_root = probe_root / "engine"
    if (engine_root / "pyproject.toml").exists():
        try:
            text = (engine_root / "pyproject.toml").read_text(encoding="utf-8")
            m = re.search(r"^version\s*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
            if m:
                return m.group(1)
        except OSError:
            pass
    if (probe_root / "pyproject.toml").exists():
        try:
            text = (probe_root / "pyproject.toml").read_text(encoding="utf-8")
            m = re.search(r"^version\s*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
            if m:
                return m.group(1)
        except OSError:
            pass
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
        # BUG-174: PyInstaller ONEFILE bundles unpack --add-data payloads
        # (build-info.json) into the runtime extraction dir ``sys._MEIPASS``
        # (a %TEMP%\\_MEIxxxx dir), NOT next to the EXE. Without this
        # candidate the CLI could never see its stamped identity and fell
        # back to Commit None + a runtime-generated timestamp. The onedir
        # path is unchanged (payloads land in _internal/).
        meipass = getattr(sys, "_MEIPASS", None)
        candidates = [
            exe_base / "build-info.json",
            exe_base / "_internal" / "build-info.json",
            *(  # onefile payload dir (empty string guard: never match cwd)
                [Path(meipass) / "build-info.json"] if meipass else []
            ),
            Path.cwd() / "build-info.json",
            Path(__file__).resolve().parent.parent.parent.parent / "build-info.json",
        ]
    else:
        candidates = [
            Path.cwd() / "build-info.json",
            exe_base / "build-info.json",
            exe_base / "_internal" / "build-info.json",
            Path(__file__).resolve().parent.parent.parent.parent / "build-info.json",
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


def _canonical_feature_schema(info: dict[str, Any]) -> str:
    """Feature-schema identity for the version block (CHG-0043).

    The canonical 70D contract (features/schema_contract.py) is the
    truth; a stamped build value is only a fallback; the legacy
    ``features/schema.py`` constant is the last resort. The literal
    ``scalp_v1`` is never asserted as the active contract here.
    """
    try:
        from nexus_scalp.features.schema_contract import SCHEMA_ID as CANON_SCHEMA_ID

        return str(CANON_SCHEMA_ID)
    except Exception:
        pass
    stamped = str(info.get("feature_schema") or "").strip()
    if stamped:
        return stamped
    try:
        from nexus_scalp.features.schema import ACTIVE_SCHEMA_ID

        return str(ACTIVE_SCHEMA_ID)
    except Exception:
        return "unknown"


def get_version_info() -> dict[str, Any]:
    """Full version/identity block for CLI, manifest and diagnostics.

    Returns a dict with keys: product, version, commit, dirty, build_timestamp,
    platform, architecture, python, channel, mode, schema. Never raises.
    """
    info = read_build_info()
    # CHG-0043 stale build-info precedence (dev/source runs only): a
    # leftover build-info.json stamped by a PREVIOUS release build must
    # never mask the live repository identity (version truth, BUG-092
    # family). Frozen bundles always report their own stamp.
    # Stale is ONLY when the stamped build-info was found via the package-
    # relative / CWD repo-root path (a leftover file on disk). An
    # intentional tmp_path/build-info.json written by a test or a CI
    # artifact that does NOT live at the repo root is NOT stale — it is the
    # deployment's own stamp and must win (test_get_version_info_never_
    # invents_commit_when_stamped writes its stamp to tmp_path).
    stamped_file = get_build_info_file()
    stale_build_info = False
    if not getattr(sys, "frozen", False) and info and stamped_file is not None:
        stamped = str(info.get("git_commit") or "").strip()
        if stamped:
            head = _git_commit("HEAD")
            if head:
                try:
                    repo_root = _repo_root()
                    stale_build_info = (
                        repo_root is not None and stamped_file.resolve() == (repo_root / "build-info.json").resolve()
                    )
                except Exception:
                    stale_build_info = False
    arch = info.get("architecture") or platform.machine()
    channel = info.get("channel") or DEFAULT_CHANNEL
    mode = info.get("build_mode") or "Release"
    # CHG-0043 commit identity truth: record WHERE the commit came from
    # and say NOT_RECORDED instead of a bare falsy value. A source
    # checkout reports the repository HEAD; a packaged bundle reports its
    # stamped build identity; genuinely unavailable stays empty with
    # commit_status NOT_RECORDED (never misleading None/n/a).
    stamped_commit = None if stale_build_info else info.get("git_commit")
    commit_value = stamped_commit or _git_commit()
    if stamped_commit:
        commit_source = "build-info"
    elif commit_value:
        commit_source = "repository"
    else:
        commit_source = "unavailable"
    # BUG-221: identity precedence is atomic. The CHG-0043 stale rule
    # already forces version/commit/build_timestamp to repository truth in
    # a dev checkout; the dirty flag must follow the same rule. The previous
    # `info.get("dirty_tree", _git_dirty())` never fired its default when a
    # stamp carries the key, so a stale stamp's cleanliness claim masked a
    # dirty repo (and vice versa).
    dirty_tree = _git_dirty() if stale_build_info else bool(info.get("dirty_tree", _git_dirty()))
    # Build timestamp: prefer a CI-stamped build-info.json on frozen bundles
    # (source-root stamps are ignored there). On dev/source runs where the
    # stamp is stale-ignored, surface the commit's author date instead of
    # UNKNOWN. Frozen bundles always keep their own stamp.
    if stale_build_info:
        ts = _git_commit_timestamp_iso()
        build_timestamp: str | None = ts if ts else None
    elif getattr(sys, "frozen", False):
        build_timestamp = info.get("build_timestamp")
    else:
        # Pure dev/source run with no build-info at all (e.g. installed venv
        # engine at <NexusHome>/engine with no bundle build-info.json):
        # report the commit's author date rather than UNKNOWN. Non-git
        # environments stay None (UNKNOWN) — never synthesize datetime.now().
        raw_ts = info.get("build_timestamp")
        if raw_ts:
            build_timestamp = raw_ts  # type: ignore[assignment]
        else:
            ts = _git_commit_timestamp_iso()
            build_timestamp = ts if ts else None
    return {
        "product": PRODUCT_NAME,
        "product_display": PRODUCT_DISPLAY,
        "version": get_version() if stale_build_info else (info.get("version") or get_version()),
        "commit": commit_value,
        "commit_source": commit_source,
        "commit_status": "RECORDED" if commit_value else "NOT_RECORDED",
        "dirty_tree": dirty_tree,
        "build_timestamp": build_timestamp,
        "platform": info.get("platform") or sys_platform(),
        "architecture": arch,
        "python": info.get("python") or platform.python_version(),
        "channel": channel,
        "build_mode": mode,
        "feature_schema": _canonical_feature_schema(info),
        "installer_version": info.get("installer_version") or "1.0.0",
    }


def sys_platform() -> str:
    return platform.system() or "unknown"


def is_windows() -> bool:
    return sys_platform().lower() == "windows"
