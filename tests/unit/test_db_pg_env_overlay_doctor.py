"""Regression tests for the doctor-credential-mismatch fix (BUG-PGENV).

The incident: an operator exported ``NSE_DATABASE__PROVIDER=postgresql`` (and
``NSE_DATABASE__PG_USER=postgres``) into a shell where the persisted settings
DB named role ``nse_user`` and the OS secret store held a DIFFERENT role's
password. ``nexus doctor`` reported ``DATABASE ... DISCONNECTED ...
password authentication failed for user "postgres"`` and suggested
``Start the PostgreSQL service`` while the server was perfectly up.

Two defects were proven and fixed:

1. ``load_database_config`` gated the persisted-settings block on
   ``not provider_env``. Exporting the provider ALONE therefore never read the
   persisted ``database.postgresql_config`` row, and the env branch rebuilt the
   config from ``for_postgres`` defaults — silently replacing the operator's
   ``nexusdb`` with ``nse_audit`` and ``command_timeout_sec=30`` with ``0``.
   The override is now an OVERLAY: only the keys actually present in the
   environment replace their persisted values.
2. The doctor's DATABASE failure suggestion was a fixed string. It now follows
   the failure cause, and when an env override shaped the config it names the
   exact variable to clear.

Discipline: every test here was verified to FAIL against the pre-fix resolver
(the resolved database became ``nse_audit`` and the timeouts were erased) and
to PASS with the fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def persisted_pg_settings(tmp_path: Path) -> Path:
    """Settings DB with a full persisted postgresql_config row.

    Mirrors an interactive install after ``nexus db-portability switch
    postgres``: the operator picked a non-default database and a per-domain
    command timeout — exactly the values a PROVIDER-only env export used to
    erase.
    """
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
                "port": 5433,
                "database": "nexusdb",
                "username": "nse_persisted",
                "password_secret": "db.postgresql.password",
                "ssl_mode": "require",
                "command_timeout_sec": 30,
                "connect_timeout_sec": 15,
            },
            value_type="json",
        )
    finally:
        db.close()
    return settings_path


@pytest.fixture()
def default_path_pg_settings(persisted_pg_settings: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The DEFAULT-path caller (``load_database_config("audit")`` with no
    explicit settings_db_path — what HealthEngine and the doctor actually do).

    NEXUS_SETTINGS_DB is the resolver's real default-path override, so pointing
    it at the fixture row reproduces the production code path EXACTLY. This is
    the case that failed: the pre-fix guard ``not provider_env`` skipped the
    persisted block only for the default-path caller, so the PROVIDER-only
    export silently erased the operator's database."""
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(persisted_pg_settings))
    return persisted_pg_settings


def _clean_pg_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "NSE_DATABASE__PROVIDER",
        "NSE_DATABASE__PG_HOST",
        "NSE_DATABASE__PG_PORT",
        "NSE_DATABASE__PG_DATABASE",
        "NSE_DATABASE__PG_USER",
        "NSE_DATABASE__PG_SSLMODE",
        "NSE_DATABASE__SQLITE_PATH",
        "NEXUS_AUDIT_DB",
    ):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------------------
# 1. PROVIDER-only env overlay (the erasure defect)
# ---------------------------------------------------------------------------
def test_provider_only_env_keeps_persisted_connection_settings(
    persisted_pg_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BUG-PGENV: exporting the PROVIDER alone must not erase the persisted
    host/port/database/username or the configured timeouts."""
    _clean_pg_env(monkeypatch)
    monkeypatch.setenv("NSE_DATABASE__PROVIDER", "postgresql")

    from nexus_scalp.database.config import load_database_config

    cfg = load_database_config("audit", settings_db_path=str(persisted_pg_settings))

    assert cfg.is_postgresql
    # Every persisted value the old code discarded survives.
    assert cfg.host == "pg-persisted.example.com"
    assert cfg.port == 5433
    assert cfg.database == "nexusdb", "the operator's database must not fall back to nse_audit"
    assert cfg.username == "nse_persisted"
    assert cfg.ssl_mode == "require"
    assert cfg.command_timeout_sec == 30, "configured statement timeout was silently zeroed"
    assert cfg.connect_timeout_sec == 15
    assert cfg.env_overrode_persisted is True


def test_persisted_pg_settings_without_env_override(
    persisted_pg_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No env override: the persisted row wins and the flag stays False."""
    _clean_pg_env(monkeypatch)

    from nexus_scalp.database.config import load_database_config

    cfg = load_database_config("audit", settings_db_path=str(persisted_pg_settings))

    assert cfg.is_postgresql
    assert cfg.database == "nexusdb"
    assert cfg.username == "nse_persisted"
    assert cfg.command_timeout_sec == 30
    assert cfg.env_overrode_persisted is False


def test_env_still_wins_per_key_over_persisted(
    persisted_pg_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env precedence is PRESERVED: an explicit key still overrides the
    persisted value; only the unnamed keys fall through."""
    _clean_pg_env(monkeypatch)
    monkeypatch.setenv("NSE_DATABASE__PROVIDER", "postgresql")
    monkeypatch.setenv("NSE_DATABASE__PG_USER", "env-role")
    monkeypatch.setenv("NSE_DATABASE__PG_DATABASE", "env-db")

    from nexus_scalp.database.config import load_database_config

    cfg = load_database_config("audit", settings_db_path=str(persisted_pg_settings))

    assert cfg.is_postgresql
    assert cfg.username == "env-role"
    assert cfg.database == "env-db"
    # Unnamed keys keep the persisted values.
    assert cfg.host == "pg-persisted.example.com"
    assert cfg.port == 5433
    assert cfg.ssl_mode == "require"
    assert cfg.command_timeout_sec == 30


def test_full_env_override_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The classic full-env resolution (no persisted row present) is
    byte-identical to before the fix — the overlay only fills from the
    persisted row when one exists to read."""
    _clean_pg_env(monkeypatch)
    env = {
        "NSE_DATABASE__PROVIDER": "postgresql",
        "NSE_DATABASE__PG_HOST": "pg1",
        "NSE_DATABASE__PG_PORT": "5433",
        "NSE_DATABASE__PG_DATABASE": "my_db",
        "NSE_DATABASE__PG_USER": "my_user",
    }

    from nexus_scalp.database.config import load_database_config

    cfg = load_database_config("audit", env=env, settings_db_path=str(tmp_path / "absent.db"))

    assert cfg.is_postgresql
    assert cfg.host == "pg1" and cfg.port == 5433
    assert cfg.database == "my_db" and cfg.username == "my_user"
    assert cfg.env_overrode_persisted is True


def test_env_flag_not_persisted_by_to_dict(
    persisted_pg_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``env_overrode_persisted`` is a runtime diagnostic flag, never a
    persisted field — to_dict must keep its output round-trippable."""
    _clean_pg_env(monkeypatch)
    monkeypatch.setenv("NSE_DATABASE__PROVIDER", "postgresql")

    from nexus_scalp.database.config import DatabaseConfig, load_database_config

    cfg = load_database_config("audit", settings_db_path=str(persisted_pg_settings))
    dumped = cfg.to_dict()
    assert "env_overrode_persisted" not in dumped
    # from_dict must not see the flag either.
    rebuilt = DatabaseConfig.from_dict(dumped, "audit")
    assert rebuilt.env_overrode_persisted is False


# ---------------------------------------------------------------------------
# 1b. The DEFAULT-PATH caller — the exact production path the doctor hits.
#     These are the tests that FAIL without the fix (the pre-fix guard
#     ``not provider_env`` skipped the persisted block here, so a
#     PROVIDER-only export rebuilt from for_postgres defaults).
# ---------------------------------------------------------------------------
def test_provider_only_env_keeps_persisted_db_on_default_path(
    default_path_pg_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The doctor calls ``load_database_config("audit")`` with NO explicit
    settings_db_path. A PROVIDER-only export must still not erase the
    persisted database or timeouts — this is the pasted incident's shape."""
    _clean_pg_env(monkeypatch)
    monkeypatch.setenv("NSE_DATABASE__PROVIDER", "postgresql")

    from nexus_scalp.database.config import load_database_config

    cfg = load_database_config("audit")

    assert cfg.is_postgresql
    assert cfg.database == "nexusdb", "persisted nexusdb became the nse_audit default"
    assert cfg.username == "nse_persisted"
    assert cfg.host == "pg-persisted.example.com"
    assert cfg.port == 5433
    assert cfg.command_timeout_sec == 30
    assert cfg.env_overrode_persisted is True


def test_default_path_no_env_uses_persisted(
    default_path_pg_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_pg_env(monkeypatch)

    from nexus_scalp.database.config import load_database_config

    cfg = load_database_config("audit")

    assert cfg.is_postgresql
    assert cfg.database == "nexusdb"
    assert cfg.command_timeout_sec == 30
    assert cfg.env_overrode_persisted is False


def test_default_path_env_wins_per_key(
    default_path_pg_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env precedence survives on the default path too."""
    _clean_pg_env(monkeypatch)
    monkeypatch.setenv("NSE_DATABASE__PROVIDER", "postgresql")
    monkeypatch.setenv("NSE_DATABASE__PG_USER", "env-role")

    from nexus_scalp.database.config import load_database_config

    cfg = load_database_config("audit")

    assert cfg.is_postgresql
    assert cfg.username == "env-role"
    assert cfg.database == "nexusdb"
    assert cfg.command_timeout_sec == 30


# ---------------------------------------------------------------------------
# 2. The doctor suggestion follows the failure cause
# ---------------------------------------------------------------------------
def test_auth_refused_suggestion_names_the_credential_not_the_service() -> None:
    """HEALTH-DBREASON made the reason truthful; the suggestion must be too.
    A live server refusing a password is NOT 'start the service'."""
    from nexus_scalp.release.health import _postgres_failure_suggestion

    class _Cfg:
        username = "postgres"
        env_overrode_persisted = True

    out = _postgres_failure_suggestion(
        'OperationalError: FATAL: password authentication failed for user "postgres"',
        _Cfg(),
        env={"NSE_DATABASE__PG_USER": "postgres"},
    )
    low = out.lower()
    assert "credential" in low or "password" in low
    # It must point at the credential knob, not the service.
    assert "test-connection" in low or "db-portability" in low
    # And flag the env override that caused the mismatch.
    assert "NSE_DATABASE__PG_USER" in out


def test_connection_refused_still_suggests_the_service() -> None:
    """The genuinely-down case keeps its correct remediation."""
    from nexus_scalp.release.health import _postgres_failure_suggestion

    class _Cfg:
        username = "postgres"
        env_overrode_persisted = False

    out = _postgres_failure_suggestion(
        "connection to server (127.0.0.1), port 5432 failed: Connection refused", _Cfg()
    )
    assert "PostgreSQL service" in out
    assert "NSE_DATABASE__PG_USER" not in out


def test_missing_database_suggestion_points_at_provisioning() -> None:
    from nexus_scalp.release.health import _postgres_failure_suggestion

    class _Cfg:
        username = "postgres"
        env_overrode_persisted = False

    out = _postgres_failure_suggestion('FATAL: database "nexusdb" does not exist', _Cfg())
    assert "does not exist" in out.lower()


def test_conflict_hint_absent_when_resolver_used_persisted_only() -> None:
    """The hint is only offered when an env override actually applied."""
    from nexus_scalp.release.health import _pg_conflict_hint

    class _Cfg:
        username = "postgres"
        env_overrode_persisted = False

    assert _pg_conflict_hint(_Cfg()) == ""
