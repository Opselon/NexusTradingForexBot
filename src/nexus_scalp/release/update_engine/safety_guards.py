"""Extracted from release/updater.py (update-package split); behavior preserved."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

from nexus_scalp.release.update_engine.pidfile_lock import _pid_alive


class EngineGuard:
    """Reports the engine runtime state without ever killing it.

    LIVE detection prefers the engine's own config mode; a dead pidfile is
    reported STOPPED.  An update NEVER proceeds against a LIVE engine unless
    the user explicitly authorizes the maintenance flow (quiesce).
    """

    def __init__(self, pidfile: Path | None = None, config_path: Path | None = None) -> None:
        self.pidfile = pidfile
        self.config_path = config_path

    def engine_state(self) -> str:
        if self.pidfile is None or not self.pidfile.exists():
            return "STOPPED"
        try:
            pid = int(self.pidfile.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return "UNKNOWN"
        if pid <= 0:
            return "UNKNOWN"
        if not _pid_alive(pid):
            return "STOPPED"
        mode = self._config_mode()
        return mode if mode in ("LIVE", "PAPER", "SHADOW") else "RUNNING"

    def _config_mode(self) -> str | None:
        if self.config_path is None or not self.config_path.exists():
            return None
        try:
            m = re.search(r"(?m)^\s*mode\s*:\s*(\S+)", self.config_path.read_text(encoding="utf-8"))
            return m.group(1).upper() if m else None
        except Exception:
            return None

    def assert_safe_to_update(self, *, live_policy: str = "BLOCK") -> None:
        """Raises UpdateBlockedError when a LIVE engine would be disrupted."""
        state = self.engine_state()
        if state == "LIVE" and live_policy == "BLOCK":
            raise UpdateBlockedError(
                "engine is LIVE — update would disrupt open positions/pending orders. "
                "Run `nexus update` explicitly with `--force` only after the documented "
                "maintenance quiesce flow."
            )


class UpdateBlockedError(RuntimeError):
    """Raised when the update cannot proceed for safety reasons."""


class QuiesceProtocol:
    """Explicit maintenance authorization: stop new entries, stop the engine.

    Never closes positions — the engine's own shutdown persists its state;
    an update alone never liquidates anything (section 14).
    """

    def __init__(self) -> None:
        self._requested = False

    def requested(self) -> bool:
        return self._requested

    def quiesce(self, pidfile: Path | None, timeout_s: int = 30) -> bool:
        self._requested = True
        if pidfile is None or not pidfile.exists():
            return True  # nothing running — quiesce trivially satisfied
        try:
            pid = int(pidfile.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return True
        if not _pid_alive(pid):
            pidfile.unlink(missing_ok=True)
            return True
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=timeout_s,
            )
        else:
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not _pid_alive(pid):
                pidfile.unlink(missing_ok=True)
                return True
            time.sleep(0.25)
        return False


# ---------------------------------------------------------------------------
# User-data backup (sections 15/22)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Installed-release local state (spec section 33)
# ---------------------------------------------------------------------------
