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


def resolve_decision_store_target() -> Path | str:
    """Resolve the AI-provider decision ledger to the ACTIVE provider.

    Returns either a SQLite ``Path`` (the ``db_path`` contract) or a
    PostgreSQL DSN ``str`` (the ``dsn`` contract) — the two shapes
    ``ProviderDecisionStore`` accepts.

    The decision ledger is OPERATIONAL DATA (a durable record of every final
    provider decision), so it must follow the persisted application database
    provider, not an unconditional SQLite file. A box switched to PostgreSQL
    via ``nexus db-portability switch`` (or the management UI) previously kept
    writing decisions into ``<user-data>/databases/ai_provider_decisions.db``
    while the rest of the engine used PostgreSQL — an architecture violation
    with no runtime signal (PR #454 regression, found by the SQLite runtime
    trap).

    Resolution order (mirrors ``resolve_audit_db_url``, which is the proven
    pattern for the audit domain):
      1. the persisted ``database.provider`` + ``database.postgresql_config``
         settings (the app-level provider switch): PostgreSQL yields a DSN;
      2. ``NEXUS_DECISIONS_DB`` explicit override — a TEST-ISOLATION SEAM, not
         a provider choice: when set, the ledger is pinned to a SQLite file
         even on a PostgreSQL-configured box, exactly like ``NEXUS_AUDIT_DB``
         pins the audit domain (BUG-223). A test that points this at a temp
         file never wants a live PostgreSQL pool.
    Never raises: a settings DB that cannot be opened falls back to the
    SQLite path (a fresh install has no settings DB yet).
    """
    try:
        from nexus_scalp.database.config import build_postgres_url, load_database_config
        from nexus_scalp.settings.secret_store import SecureSecretStore

        cfg = load_database_config("audit")
        if cfg.is_postgresql and not os.environ.get("NEXUS_DECISIONS_DB", "").strip():
            return build_postgres_url(cfg, SecureSecretStore())
    except Exception:  # pragma: no cover - settings DB unavailable / malformed
        pass
    return decisions_db_path()


def resolve_registry_target() -> Path | str:
    """Resolve the AI-provider *configuration* registry to the ACTIVE provider.

    Returns either a SQLite ``Path`` (the ``db_path`` contract) or a
    PostgreSQL DSN ``str`` (the ``dsn`` contract) — the two shapes
    ``ProviderRegistryStore`` accepts.

    The provider configuration registry is APPLICATION STATE (which provider is
    configured and which is active), so it must follow the persisted
    application database provider, not an unconditional SQLite file. A box
    switched to PostgreSQL via ``nexus db-portability switch`` previously kept
    its provider configuration in ``<user-data>/databases/app_settings.db``
    while the engine used PostgreSQL — the same architecture violation PR #462
    found for the decision ledger (operational data), proven by the same
    SQLite runtime trap: with ``database.provider=postgresql`` the registry
    read and wrote SQLite and PostgreSQL's ``ai_provider_config`` was ignored.

    Resolution order (mirrors ``resolve_decision_store_target``, which mirrors
    ``resolve_audit_db_url`` — the proven pattern for this domain class):
      1. the persisted ``database.provider`` + ``database.postgresql_config``
         settings (the app-level provider switch): PostgreSQL yields a DSN;
      2. ``NEXUS_SETTINGS_DB`` explicit override — a TEST-ISOLATION SEAM, not
         a provider choice: when set, the registry is pinned to a SQLite file
         even on a PostgreSQL-configured box, exactly like
         ``NEXUS_AUDIT_DB`` (BUG-223) / ``NEXUS_DECISIONS_DB`` pin their
         domains. A test that points this at a temp file never wants a live
         PostgreSQL pool.
    Never raises: a settings DB that cannot be opened falls back to the
    SQLite path (a fresh install has no settings DB yet).
    """
    if override := os.environ.get("NEXUS_SETTINGS_DB", "").strip():
        return Path(override).expanduser()
    try:
        from nexus_scalp.database.config import build_postgres_url, load_database_config
        from nexus_scalp.settings.secret_store import SecureSecretStore

        cfg = load_database_config("audit")
        if cfg.is_postgresql:
            return build_postgres_url(cfg, SecureSecretStore())
    except Exception:  # pragma: no cover - settings DB unavailable / malformed
        pass
    return settings_db_path()
