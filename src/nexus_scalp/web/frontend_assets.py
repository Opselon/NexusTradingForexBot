"""
Frontend production-build resolution seam (END-USER-RUNTIME-UI-INTEGRATION)
============================================================================
PURPOSE:
    Locate the built React Control Center bundle (`frontend/dist`) for THIS
    runtime and report whether it is servable, so the backend can serve the
    production UI at `/` (frozen decision #4), keep the `/alt` dual-serve
    mount byte-compatible (#2/#6), and expose honest serving truth on the
    root `/health` probe (#9).

OWNER:
    Lane A (runtime/static) of the END-USER-RUNTIME-UI-INTEGRATION wave.
    Other lanes import ONLY the two public names below
    (`resolve_frontend_dist`, `frontend_status`) — this module is the frozen
    cross-lane seam; do not reach around it into server internals.

CONSUMES:
    * env `NEXUS_ALT_UI_DIR` (explicit override — MUST contain index.html)
    * `sys._MEIPASS` (PyInstaller onefile/frozen layout)
    * `<exe_dir>/_internal/frontend/dist` (PyInstaller onedir portable layout)
    * `<repo>/frontend/dist` and `<cwd>/frontend/dist` (dev checkouts)
    Nothing else: no engine state, no config files, no network.

PROVIDES:
    * `resolve_frontend_dist() -> Path | None` — the dist DIRECTORY that
      actually contains `index.html`, following the FROZEN search order
      (contract frozen decision #9): env override -> sys._MEIPASS/frontend/dist
      -> <exe_dir>/_internal/frontend/dist -> <repo>/frontend/dist ->
      <cwd>/frontend/dist. Returns None when no candidate is servable.
    * `frontend_status() -> dict[str, str | bool | None]` —
      `{"dist_present": bool, "index": bool, "dir": str | None}` for the
      additive `frontend` object on root `/health`.

INVARIANTS:
    * Search order is FROZEN (contract #9) — never reorder, never add slots
      without an orchestrator-approved contract change.
    * The env override is AUTHORITATIVE: set-but-invalid (no index.html)
      means NO dist — never a silent fallthrough to a different bundle.
      This preserves the shipped `/alt` resolver semantics byte-for-byte.
    * Never raises: every filesystem probe is OSError-guarded (this seam
      runs on the `/health` request path).
    * `resolve_frontend_dist()` only ever returns a directory containing
      `index.html` — callers can trust `dist / "index.html"` is a file.

EXTENDS:
    * `web/server.py` — `/` + `/index.html` document serving, the `/alt`
      StaticFiles mount, and the root SPA fallback mount (all read the seam
      at request/app-construction time).
    * `web/diagnostics_state_routes.py` — additive `frontend` object on
      `/health` (never removes or renames existing health fields, #9).
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

#: Frozen env override slot (contract #9, search slot 1).
DIST_ENV_VAR: str = "NEXUS_ALT_UI_DIR"


def _has_index(candidate: Path) -> bool:
    """True when `candidate` is a directory holding a servable index.html."""
    try:
        return (candidate / "index.html").is_file()
    except OSError:
        return False


def _repo_root() -> Path:
    """Repository root derived from this module's own location (src layout)."""
    # .../src/nexus_scalp/web/frontend_assets.py -> parents[3] == repo root
    return Path(__file__).resolve().parents[3]


def resolve_frontend_dist() -> Path | None:
    """Resolve the built React dist directory (contract #9 frozen order).

    Search order:
      1. env `NEXUS_ALT_UI_DIR` — authoritative: set-but-invalid (no
         index.html) returns None (never falls through; matches the shipped
         `/alt` resolver semantics).
      2. `sys._MEIPASS/frontend/dist` (PyInstaller frozen).
      3. `<exe_dir>/_internal/frontend/dist` (onedir portable release).
      4. `<repo>/frontend/dist` then `<cwd>/frontend/dist` (dev checkouts).

    Returns the directory ONLY when it contains index.html; None otherwise.
    Never raises (OSError-guarded probes) — safe on the /health hot path.
    """
    override = os.environ.get(DIST_ENV_VAR, "").strip()
    if override:
        candidate = Path(override)
        return candidate if _has_index(candidate) else None

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(str(meipass)) / "frontend" / "dist"
        if _has_index(candidate):
            return candidate

    exe_dir: Path | None = None
    with contextlib.suppress(OSError):
        exe_dir = Path(sys.executable).resolve().parent
    if exe_dir is not None:
        candidate = exe_dir / "_internal" / "frontend" / "dist"
        if _has_index(candidate):
            return candidate

    for base in (_repo_root(), Path.cwd()):
        candidate = base / "frontend" / "dist"
        if _has_index(candidate):
            return candidate
    return None


def frontend_status() -> dict[str, str | bool | None]:
    """Serving truth for the additive `frontend` object on `/health` (#9).

    Shape (frozen): `{"dist_present": bool, "index": bool, "dir": str | None}`.
      * resolved dist            -> {"dist_present": True,  "index": True,  "dir": "..."}
      * override dir, index lost -> {"dist_present": True,  "index": False, "dir": "..."}
      * nothing found            -> {"dist_present": False, "index": False, "dir": None}
    Never raises: a stat failure degrades to "not present", never to a 500
    on the health probe.
    """
    try:
        dist = resolve_frontend_dist()
        if dist is not None:
            return {
                "dist_present": True,
                "index": (dist / "index.html").is_file(),
                "dir": str(dist),
            }
        # Distinguish "override points at a dir that LOST its index" from
        # "no candidate anywhere" so operators can see the stale-override case.
        override = os.environ.get(DIST_ENV_VAR, "").strip()
        if override:
            candidate = Path(override)
            if candidate.is_dir():
                return {"dist_present": True, "index": False, "dir": str(candidate)}
    except OSError:
        pass
    return {"dist_present": False, "index": False, "dir": None}
