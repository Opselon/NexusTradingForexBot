"""CHG-0067: NEXUS_AUDIT_DB must win over the PERSISTED provider in
load_database_config (BUG-223 / BUG-278 precedence fix).

Precedence ladder codified here for domain == "audit":
  1. explicit NSE_DATABASE__PROVIDER beats everything;
  2. explicit NSE_DATABASE__SQLITE_PATH beats the seam;
  3. NEXUS_AUDIT_DB beats the persisted provider AND the persisted PostgreSQL
     connection settings (the fix — previously the persisted settings block
     overrode the seam and a machine that had chosen PostgreSQL resolved to
     the PRODUCTION PostgreSQL instead of the isolated audit file);
  4. with the seam ABSENT, persisted settings behave exactly as before — a
     production install that chose PostgreSQL keeps PostgreSQL;
  5. non-audit domains ignore the seam entirely.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def persisted_postgres_settings(tmp_path: Path) -> Path:
    """A settings DB whose persisted provider is PostgreSQL, with a full
    persisted ``database.postgresql_config`` row — mirrors what an interactive
    install has after ``nexus db-portability switch postgres``."""
    from nexus_scalp.database.config import PG_CONFIG_SETTING_KEY, PROVIDER_SETTING_KEY
    from nexus_scalp.settings.service import SettingsDatabase

    settings_path = tmp_path / "settings" / "app_settings.db"
    db = SettingsDatabase(db_path=settings_path)
    try:
        db.set(PROVIDER_SETTING_KEY, "postgresql", value_type="str")
        db.set(
            PG_CONFIG_SETTING_KEY,
            {
                "provider": "postgresql",
                "domain": "audit",
                "host": "pg-persisted.example.com",
                "port": 5432,
                "database": "nse_audit_persisted",
                "username": "nse_persisted",
                "password_secret": "db.postgresql.password",
                "ssl_mode": "require",
            },
            value_type="json",
        )
    finally:
        db.close()
    return settings_path


def _norm(value: str) -> str:
    return value.replace("\\", "/").lower()


def test_seam_wins_over_persisted_postgres_provider(
    persisted_postgres_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract 3: with NEXUS_AUDIT_DB set, load_database_config('audit')
    resolves to SQLITE at the seam path — NOT the persisted PostgreSQL."""
    from nexus_scalp.database.config import load_database_config

    seam = persisted_postgres_settings.parent / "isolated_audit.db"
    monkeypatch.setenv("NEXUS_AUDIT_DB", str(seam))
    monkeypatch.delenv("NSE_DATABASE__PROVIDER", raising=False)
    monkeypatch.delenv("NSE_DATABASE__SQLITE_PATH", raising=False)

    cfg = load_database_config("audit", settings_db_path=str(persisted_postgres_settings))

    assert cfg.is_sqlite, "the seam must win over the persisted postgres provider"
    assert not cfg.is_postgresql
    assert _norm(cfg.sqlite_connect_path) == _norm(str(seam))
    # no leaked persisted PG fields
    assert cfg.host == "" and cfg.username == "" and cfg.database == ""
    assert cfg.port == 0


def test_seam_wins_over_persisted_postgres_config_row(
    persisted_postgres_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract 3 (second half): even when the persisted provider row is
    absent, a persisted database.postgresql_config row must not leak past the
    seam into the resolved audit config."""
    from nexus_scalp.database.config import PROVIDER_SETTING_KEY, load_database_config
    from nexus_scalp.settings.service import SettingsDatabase

    db = SettingsDatabase(db_path=persisted_postgres_settings)
    try:
        db.set(PROVIDER_SETTING_KEY, "", value_type="str")
    finally:
        db.close()

    seam = persisted_postgres_settings.parent / "isolated_audit2.db"
    monkeypatch.setenv("NEXUS_AUDIT_DB", str(seam))
    monkeypatch.delenv("NSE_DATABASE__PROVIDER", raising=False)
    monkeypatch.delenv("NSE_DATABASE__SQLITE_PATH", raising=False)

    cfg = load_database_config("audit", settings_db_path=str(persisted_postgres_settings))

    assert cfg.is_sqlite
    assert _norm(cfg.sqlite_connect_path) == _norm(str(seam))


def test_persisted_postgres_still_applies_when_seam_absent(
    persisted_postgres_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract 4: without the seam, the persisted provider + persisted PG
    connection settings keep their full authority (production installs that
    chose PostgreSQL keep PostgreSQL)."""
    from nexus_scalp.database.config import load_database_config

    monkeypatch.delenv("NEXUS_AUDIT_DB", raising=False)
    monkeypatch.delenv("NSE_DATABASE__PROVIDER", raising=False)
    monkeypatch.delenv("NSE_DATABASE__SQLITE_PATH", raising=False)

    cfg = load_database_config("audit", settings_db_path=str(persisted_postgres_settings))

    assert cfg.is_postgresql, "persisted postgres must apply when the seam is unset"
    assert cfg.host == "pg-persisted.example.com"
    assert cfg.port == 5432
    assert cfg.database == "nse_audit_persisted"
    assert cfg.username == "nse_persisted"
    assert cfg.ssl_mode == "require"


def test_explicit_provider_env_still_beats_seam(
    persisted_postgres_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract 1: an explicit NSE_DATABASE__PROVIDER wins over both the seam
    and the persisted provider (container / CI contract, BUG-278)."""
    from nexus_scalp.database.config import load_database_config

    seam = persisted_postgres_settings.parent / "isolated_audit3.db"
    monkeypatch.setenv("NEXUS_AUDIT_DB", str(seam))
    monkeypatch.setenv("NSE_DATABASE__PROVIDER", "sqlite")

    cfg = load_database_config("audit", settings_db_path=str(persisted_postgres_settings))

    assert cfg.is_sqlite
    assert _norm(cfg.sqlite_connect_path) != _norm(str(seam))


def test_explicit_sqlite_path_env_beats_seam(
    persisted_postgres_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract 2: an explicit NSE_DATABASE__SQLITE_PATH takes precedence over
    the seam (and over the persisted provider)."""
    from nexus_scalp.database.config import load_database_config

    seam = persisted_postgres_settings.parent / "isolated_audit4.db"
    explicit = persisted_postgres_settings.parent / "explicit.db"
    monkeypatch.setenv("NEXUS_AUDIT_DB", str(seam))
    monkeypatch.setenv("NSE_DATABASE__PROVIDER", "sqlite")
    monkeypatch.setenv("NSE_DATABASE__SQLITE_PATH", str(explicit))

    cfg = load_database_config("audit", settings_db_path=str(persisted_postgres_settings))

    assert cfg.is_sqlite
    assert _norm(cfg.sqlite_connect_path) == _norm(str(explicit))


def test_seam_ignored_for_non_audit_domains(
    persisted_postgres_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract 5: the seam is the AUDIT domain seam only; other domains read
    the persisted provider exactly as before (persisted PG is NOT skipped for
    them, so a persisted PostgreSQL still resolves)."""
    from nexus_scalp.database.config import load_database_config

    seam = persisted_postgres_settings.parent / "isolated_audit5.db"
    monkeypatch.setenv("NEXUS_AUDIT_DB", str(seam))
    monkeypatch.delenv("NSE_DATABASE__PROVIDER", raising=False)
    monkeypatch.delenv("NSE_DATABASE__SQLITE_PATH", raising=False)

    cfg = load_database_config("news", settings_db_path=str(persisted_postgres_settings))

    assert "isolated_audit" not in _norm(cfg.sqlite_connect_path)
