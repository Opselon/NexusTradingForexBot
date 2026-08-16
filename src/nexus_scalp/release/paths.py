"""User-data / installation separation for the Nexus release system.

Policy (never store mutable runtime data inside the installation directory):

    Application installation  ->  Program Files\\NexusScalpEngine (or repo root
                                   for source/dev installs).
    User data                 ->  %LOCALAPPDATA%\\NexusScalpEngine  (config,
                                   logs, databases, models, cache).

All health checks, repairs, diagnostics and the installer resolve paths
through this module so the release system behaves identically in source,
installed and packaged (PyInstaller onedir) layouts.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "NexusScalpEngine"
CONFIG_DIR_NAME = "config"
LOG_DIR_NAME = "logs"
DATA_DIR_NAME = "data"
MODELS_DIR_NAME = "models"
CACHE_DIR_NAME = "cache"
DIAGNOSTICS_DIR_NAME = "diagnostics"


def is_windows() -> bool:
    return sys.platform == "win32"

# Subdirectories that may be expected inside configured workspace roots.
RUNTIME_SUBDIRS = ("artifacts", "configs", "data", "logs", "Web")


def is_frozen() -> bool:
    """True when running inside a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def exe_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent.parent


def app_data_root() -> Path:
    """Per-user application data root (config/logs/db live here)."""
    if is_windows():
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_DIR_NAME
    return Path.home() / f".{APP_DIR_NAME.lower()}"


def get_data_root() -> Path:
    """Root that holds a Nexus data set (databases, models, logs)."""
    # Portable layout: <bundle>/data ; installed layout: <LocalAppData>.
    if is_frozen():
        bundle_data = exe_dir() / DATA_DIR_NAME
        if bundle_data.exists():
            return bundle_data
    return app_data_root() / DATA_DIR_NAME


def get_config_dir() -> Path:
    return app_data_root() / CONFIG_DIR_NAME


def get_logs_dir() -> Path:
    return app_data_root() / LOG_DIR_NAME


def get_models_dir() -> Path:
    return app_data_root() / MODELS_DIR_NAME


def get_cache_dir() -> Path:
    return app_data_root() / CACHE_DIR_NAME


def get_diagnostics_dir() -> Path:
    return app_data_root() / DIAGNOSTICS_DIR_NAME


def get_user_config_path() -> Path:
    return get_config_dir() / "nexus.yaml"


def get_runtime_workspace() -> Path:
    """Working directory the engine treats as its runtime root."""
    if is_frozen():
        return exe_dir()
    return Path.cwd()


def ensure_user_dirs() -> None:
    """Create the user-data directory skeleton (idempotent, never deletes)."""
    for d in (
        get_data_root(),
        get_config_dir(),
        get_logs_dir(),
        get_models_dir(),
        get_cache_dir(),
        get_diagnostics_dir(),
    ):
        d.mkdir(parents=True, exist_ok=True)


def user_data_initialized() -> bool:
    return get_config_dir().exists() and get_data_root().exists()


if __name__ == "__main__":  # pragma: no cover
    print("frozen        :", is_frozen())
    print("exe_dir       :", exe_dir())
    print("data_root     :", get_data_root())
    print("config_dir    :", get_config_dir())
    print("logs_dir      :", get_logs_dir())