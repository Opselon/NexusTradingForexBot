"""Canonical path helpers for the isolated Nexus settings subsystem.

Reuses the existing release/paths.py user-data architecture
(%LOCALAPPDATA%\\NexusScalpEngine) so settings live OUTSIDE the source tree,
survive upgrades/repairs, and are isolated from trading databases
(artifacts/audit.db, artifacts/news.db, candle_intel.db).
"""

from __future__ import annotations

import os
from pathlib import Path

from nexus_scalp.release.paths import app_data_root, ensure_user_dirs

#: Dedicated settings database (installation/user configuration ONLY).
SETTINGS_DB_FILENAME = "app_settings.db"

#: Settings database lives under <user-data>/databases/ (isolated from artifacts/*).
SETTINGS_DB_DIRNAME = "databases"


def settings_db_path() -> Path:
    """Absolute path of the isolated application-settings database.

    Honors the NEXUS_SETTINGS_DB env override (test isolation / diagnostics
    escape hatch — same pattern as the NEXUS_TELEGRAM_BOT_TOKEN override).
    When unset, falls back to <user-data>/databases/app_settings.db.
    """
    override = os.environ.get("NEXUS_SETTINGS_DB")
    if override:
        p = Path(override).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    ensure_user_dirs()
    db_dir = app_data_root() / SETTINGS_DB_DIRNAME
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / SETTINGS_DB_FILENAME


def settings_db_url() -> str:
    """SQLAlchemy-style sqlite URL for the settings DB."""
    return f"sqlite:///{settings_db_path().as_posix()}"


def decisions_db_path() -> Path:
    """Absolute path of the AI-provider decision ledger (ECOSYSTEM-001).

    Lives beside the settings DB (same ``databases/`` dir, isolated from the
    trading databases), so a ``NEXUS_SETTINGS_DB`` override relocates both
    together. Honors an explicit ``NEXUS_DECISIONS_DB`` override first — same
    per-knob convention as ``settings_db_path``.
    """
    override = os.environ.get("NEXUS_DECISIONS_DB")
    if override:
        p = Path(override).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    # Follow the settings DB: one override moves both, and the ledger always
    # sits in the same (non-trading) directory as the provider configuration.
    return settings_db_path().parent / "ai_provider_decisions.db"
