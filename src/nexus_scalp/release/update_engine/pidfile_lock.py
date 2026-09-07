"""Extracted from release/updater.py (update-package split); behavior preserved."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.release.update_engine.constants import (
    INSTALL_MODE_DEVELOPER,
    INSTALL_MODE_EXE,
    INSTALL_MODE_INNO,
    INSTALL_MODE_PORTABLE,
    INSTALL_MODE_SOURCE,
    INSTALL_MODE_UNKNOWN,
)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            for line in (out.stdout or "").splitlines():
                if re.search(rf"\b{pid}\b", line) and "Image Name" not in line:
                    return True
            return False
        except Exception:
            return True  # undeterminable -> assume alive (conservative)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True



class UpdateLock:
    """Single-instance update lock (atomic mkdir, stale-steal after 30 min).

    The marker is a subdirectory inside ``lock_dir`` so the surrounding home
    may already exist; only one updater can hold the marker at a time.
    """

    def __init__(self, lock_dir: Path) -> None:
        self.lock_dir = lock_dir
        self.lock_path = lock_dir / ".update-lock"
        self._held = False

    def acquire(self, correlation_id: str) -> bool:
        if self._held:
            return True
        try:
            self.lock_dir.mkdir(parents=True, exist_ok=True)
            self.lock_path.mkdir()
            (self.lock_path / "owner.json").write_text(
                json.dumps(
                    {
                        "correlation_id": correlation_id,
                        "pid": os.getpid(),
                        "acquired_at": datetime.now(UTC).isoformat(),
                    }
                ),
                encoding="utf-8",
            )
            self._held = True
            return True
        except FileExistsError:
            if self._stale():
                shutil.rmtree(self.lock_path, ignore_errors=True)
                return self.acquire(correlation_id)
            return False

    def _stale(self) -> bool:
        try:
            age = time.time() - self.lock_path.stat().st_mtime
            return age > 30 * 60
        except OSError:
            return False

    def release(self) -> None:
        if self._held:
            shutil.rmtree(self.lock_path, ignore_errors=True)
            self._held = False



class SettingsGuard:
    """Guards the secure settings/secret store during updates.

    Update operations are forbidden from touching the isolated settings DB
    (app_settings.db) or the DPAPI secret store (secrets.enc).  Telegram
    credentials survive an update because they live in the OS-protected
    store under the user-data root, outside the replaceable payload.
    """

    def ensure_credentials_untouched(self) -> bool:
        """Idempotent guard: returns True and performs NO writes."""
        return True

    def verify_secure_store_reference(self, user_root: Path) -> bool:
        """True when the secure credential surface exists in user data."""
        refs = [user_root / "secrets.enc", user_root / "databases" / "app_settings.db"]
        return any(p.exists() for p in refs)


# ---------------------------------------------------------------------------
# Install-mode detection (sections 2/49)
# ---------------------------------------------------------------------------
class InstallModeDetector:
    """Identifies SOURCE / PORTABLE / INSTALLED_EXE / INNO_SETUP / DEVELOPER."""

    def detect(self, app_root: Path | None = None) -> str:
        root = app_root or _current_app_root()
        frozen = bool(getattr(sys, "frozen", False))
        if frozen:
            if (root / "unins000.exe").exists():
                return INSTALL_MODE_INNO
            return INSTALL_MODE_EXE
        if (root / ".git").exists():
            return (
                INSTALL_MODE_DEVELOPER
                if (root / "pyproject.toml").exists()
                else INSTALL_MODE_SOURCE
            )
        if (root / "NexusScalpEngine.exe").exists() and (root / "build-info.json").exists():
            return INSTALL_MODE_PORTABLE
        if (root / "pyproject.toml").exists():
            return INSTALL_MODE_SOURCE
        return INSTALL_MODE_UNKNOWN

    def describe(self, app_root: Path | None = None) -> dict[str, Any]:
        root = app_root or _current_app_root()
        mode = self.detect(root)
        info_path = root / "build-info.json"
        info: dict[str, Any] = {}
        if info_path.exists():
            try:
                info = json.loads(info_path.read_text(encoding="utf-8"))
            except Exception:
                info = {}
        return {
            "mode": mode,
            "app_root": str(root),
            "exe": str(root / "NexusScalpEngine.exe"),
            "build_info": info,
            "frozen": bool(getattr(sys, "frozen", False)),
        }



def _current_app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


# ---------------------------------------------------------------------------
# Installed-release local state (spec section 33)
# ---------------------------------------------------------------------------
