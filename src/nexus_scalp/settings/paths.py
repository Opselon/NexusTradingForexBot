"""Canonical path helpers for the isolated Nexus settings subsystem.

Reuses the existing release/paths.py user-data architecture
(%LOCALAPPDATA%\\NexusScalpEngine) so settings live OUTSIDE the source tree,
survive upgrades/repairs, and are isolated from trading databases
(artifacts/audit.db, artifacts/news.db, candle_intel.db).
"""

from __future__ import annotations

from pathlib import Path

from nexus_scalp.release.paths import app_data_root, ensure_user_dirs

#: Dedicated settings database (installation/user configuration ONLY).
SETTINGS_DB_FILENAME = "app_settings.db"

#: Settings database lives under <user-data>/databases/ (isolated from artifacts/*).
SETTINGS_DB_DIRNAME = "databases"


def settings_db_path() -> Path:
    """Absolute path of the isolated application-settings database."""
    ensure_user_dirs()
    db_dir = app_data_root() / SETTINGS_DB_DIRNAME
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / SETTINGS_DB_FILENAME


def settings_db_url() -> str:
    """SQLAlchemy-style sqlite URL for the settings DB."""
    return f"sqlite:///{settings_db_path().as_posix()}"
