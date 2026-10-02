"""Effective-config diagnostics: per-field source attribution (GROUND_TRUTH §C2).

``config_diagnostics.effective_config_diagnostics`` reports the effective
non-secret PostgreSQL connection config together with WHICH layer of
``load_database_config``'s documented precedence ladder supplied each field:

    defaults < persisted settings < per-key env overlay < audit test-seam

The incident this serves: an operator seeing
``password authentication failed for user "postgres"`` had no way to tell
whether that username came from the persisted
``database.postgresql_config`` row or from an ``NSE_DATABASE__PG_USER``
export in a shell they forgot about. This suite pins that attribution for
every rung of the ladder, and pins the security contract (no password value
ever leaves the diagnostic) just as hard.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus_scalp.database.config import (
    PG_CONFIG_SETTING_KEY,
    PG_PASSWORD_SECRET_KEY,
    PROVIDER_SETTING_KEY,
    DatabaseConfig,
    load_database_config,
)
from nexus_scalp.database.config_diagnostics import (
    SOURCE_DEFAULT,
    SOURCE_ENV,
    SOURCE_PERSISTED,
    SOURCE_SEAM,
    SOURCE_UNKNOWN,
    effective_config_diagnostics,
)

_FIELDS = ("provider", "host", "port", "database", "username", "ssl_mode")


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every overlay knob + the audit seam (see the skill's env-hazard
    note: ``NSE_DATABASE__*`` exported into the agent shell poisons runs)."""
    for key in (
        "NSE_DATABASE__PROVIDER",
        "NSE_DATABASE__PG_HOST",
        "NSE_DATABASE__PG_PORT",
        "NSE_DATABASE__PG_DATABASE",
        "NSE_DATABASE__PG_USER",
        "NSE_DATABASE__PG_SSLMODE",
        "NSE_DATABASE__SQLITE_PATH",
        "NEXUS_AUDIT_DB",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def persisted_pg(tmp_path: Path) -> Path:
    """A settings DB holding a full persisted postgresql_config row.

    The shape of an interactive install after
    ``nexus db-portability switch postgres``: a non-default host, port,
    database, role and ssl mode — values no default constructor produces, so
    a misattributed source is unambiguous in the assertions below.
    """
    from nexus_scalp.settings.service import SettingsDatabase

    path = tmp_path / "settings" / "app_settings.db"
    db = SettingsDatabase(db_path=path)
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
                "password_secret": PG_PASSWORD_SECRET_KEY,
                "ssl_mode": "require",
                "command_timeout_sec": 30,
                "connect_timeout_sec": 15,
            },
            value_type="json",
        )
    finally:
        db.close()
    return path


# ---------------------------------------------------------------------------
# 1. Every rung of the documented ladder
# ---------------------------------------------------------------------------
def test_default_layer_attributes_every_field_when_nothing_else_applies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Nothing persisted, no env: every field's source is ``default``."""
    _clean_env(monkeypatch)
    settings = str(tmp_path / "absent_settings.db")

    cfg = load_database_config("audit", settings_db_path=settings, env={})
    out = effective_config_diagnostics(cfg, settings_db_path=settings, env={})

    assert out["field_sources"] == {name: SOURCE_DEFAULT for name in _FIELDS}


def test_persisted_settings_are_attributed_to_the_persisted_layer(
    persisted_pg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The persisted row supplied host/port/database/username/ssl_mode; the
    persisted ``database.provider`` row supplied the provider."""
    _clean_env(monkeypatch)

    cfg = load_database_config("audit", settings_db_path=str(persisted_pg), env={})
    out = effective_config_diagnostics(cfg, settings_db_path=str(persisted_pg), env={})

    assert cfg.is_postgresql
    assert out["field_sources"]["provider"] == SOURCE_PERSISTED
    for name in ("host", "port", "database", "username", "ssl_mode"):
        assert out["field_sources"][name] == SOURCE_PERSISTED, name
    assert out["effective"]["database"] == "nexusdb"
    assert out["effective"]["username"] == "nse_persisted"
    assert out["warnings"] == []


def test_per_key_env_overlay_is_attributed_to_the_env_layer(
    persisted_pg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key present in the environment wins per key, and the diagnostic
    says so for THAT key only — the unnamed keys stay persisted
    (BUG-PGENV: the overlay fills only what it names)."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("NSE_DATABASE__PROVIDER", "postgresql")
    monkeypatch.setenv("NSE_DATABASE__PG_USER", "env-role")
    monkeypatch.setenv("NSE_DATABASE__PG_DATABASE", "env-db")

    cfg = load_database_config("audit", settings_db_path=str(persisted_pg))
    out = effective_config_diagnostics(cfg, settings_db_path=str(persisted_pg))

    assert cfg.username == "env-role" and cfg.database == "env-db"
    # The two keys the env named came from the env layer.
    assert out["field_sources"]["username"] == SOURCE_ENV
    assert out["field_sources"]["database"] == SOURCE_ENV
    # The provider key was also exported -> the env layer set the provider.
    assert out["field_sources"]["provider"] == SOURCE_ENV
    # The keys env did NOT name stayed persisted — the overlay does not
    # erase them (the pre-PGENV defect).
    for name in ("host", "port", "ssl_mode"):
        assert out["field_sources"][name] == SOURCE_PERSISTED, name
    assert out["effective"]["host"] == "pg-persisted.example.com"
    assert out["effective"]["port"] == 5433


def test_audit_seam_is_attributed_to_the_seam_layer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``NEXUS_AUDIT_DB=postgresql://...`` sits ABOVE the persisted settings:
    the fields it names are attributed to the seam (audit domain only, no
    explicit env provider, no explicit sqlite path)."""
    _clean_env(monkeypatch)
    settings = str(tmp_path / "absent_settings.db")
    seam = "postgresql://seam-host:5599/seam_db"
    monkeypatch.setenv("NEXUS_AUDIT_DB", seam)

    cfg = load_database_config("audit", settings_db_path=settings)
    out = effective_config_diagnostics(cfg, settings_db_path=settings)

    assert cfg.is_postgresql, "the seam must build a postgres config"
    assert out["field_sources"]["provider"] == SOURCE_SEAM
    for name in ("host", "port", "database"):
        assert out["field_sources"][name] == SOURCE_SEAM, name
    assert out["effective"]["host"] == "seam-host"
    assert out["effective"]["port"] == 5599
    assert out["effective"]["database"] == "seam_db"


def test_explicit_env_provider_beats_the_seam(
    persisted_pg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ladder's top rule: an explicit ``NSE_DATABASE__PROVIDER`` always
    wins, even over the audit seam."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("NEXUS_AUDIT_DB", "postgresql://seam-host:5599/seam_db")
    monkeypatch.setenv("NSE_DATABASE__PROVIDER", "postgresql")

    cfg = load_database_config("audit", settings_db_path=str(persisted_pg))
    out = effective_config_diagnostics(cfg, settings_db_path=str(persisted_pg))

    assert out["field_sources"]["provider"] == SOURCE_ENV
    assert out["field_sources"]["host"] == SOURCE_PERSISTED


def test_explicit_sqlite_path_disables_the_seam(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicit ``NSE_DATABASE__SQLITE_PATH`` wins over the seam — the
    seam no longer applies, so the sqlite path is a deliberate choice and
    the connection fields fall back to the default layer."""
    _clean_env(monkeypatch)
    settings = str(tmp_path / "absent_settings.db")
    monkeypatch.setenv("NEXUS_AUDIT_DB", "postgresql://seam-host:5599/seam_db")
    explicit = str(tmp_path / "explicit" / "audit.db")
    monkeypatch.setenv("NSE_DATABASE__SQLITE_PATH", explicit)

    cfg = load_database_config("audit", settings_db_path=settings)
    out = effective_config_diagnostics(cfg, settings_db_path=settings)

    assert cfg.is_sqlite
    assert out["field_sources"]["provider"] == SOURCE_DEFAULT
    # The connection fields an sqlite config does not carry stay default.
    assert out["field_sources"]["host"] == SOURCE_DEFAULT


def test_non_audit_domain_ignores_the_seam_entirely(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The seam is audit-only; another domain keeps the default layer."""
    _clean_env(monkeypatch)
    settings = str(tmp_path / "absent_settings.db")
    monkeypatch.setenv("NEXUS_AUDIT_DB", "postgresql://seam-host:5599/seam_db")

    cfg = load_database_config("news", settings_db_path=settings)
    out = effective_config_diagnostics(cfg, settings_db_path=settings)

    assert out["field_sources"] == {name: SOURCE_DEFAULT for name in _FIELDS}


# ---------------------------------------------------------------------------
# 2. Unavailable evidence is reported, never default-filled
# ---------------------------------------------------------------------------
def test_unreadable_settings_db_reports_unknown_not_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A settings DB that cannot be opened yields ``UNKNOWN`` provenance —
    "could not check" must never look like "nothing set it"."""
    _clean_env(monkeypatch)
    bogus = str(tmp_path / "a_directory_not_a_db")
    Path(bogus).mkdir()

    out = effective_config_diagnostics(
        DatabaseConfig.for_sqlite("audit"), settings_db_path=bogus, env={}
    )

    assert out["field_sources"]["provider"] == SOURCE_UNKNOWN
    assert any("could not be opened" in w for w in out["warnings"])


# ---------------------------------------------------------------------------
# 3. Security contract — no password, ever
# ---------------------------------------------------------------------------
def test_no_secret_material_anywhere_in_the_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The payload carries no password and no URL that could contain one.
    A secret is planted in the store first so the boolean is true and the
    redaction is actually exercised."""
    _clean_env(monkeypatch)
    settings = str(tmp_path / "absent_settings.db")
    monkeypatch.setenv("NSE_DATABASE__PROVIDER", "postgresql")
    monkeypatch.setenv("NSE_DATABASE__PG_HOST", "h")
    monkeypatch.setenv("NSE_DATABASE__PG_PORT", "5432")
    monkeypatch.setenv("NSE_DATABASE__PG_DATABASE", "d")
    monkeypatch.setenv("NSE_DATABASE__PG_USER", "u")

    marker = "SUPER-SECRET-MARKER-12345"

    # The store location is a constructor argument, not an env var the store
    # itself reads: point the diagnostic at the temp store via its
    # ``secret_root`` so the probe never touches the machine's real store.
    from nexus_scalp.settings.secret_store import SecureSecretStore

    store_root = tmp_path / "secrets-root"
    store = SecureSecretStore(root=store_root)
    store.set_secret(PG_PASSWORD_SECRET_KEY, marker)

    cfg = load_database_config("audit", settings_db_path=settings)
    out = effective_config_diagnostics(cfg, settings_db_path=settings, secret_root=store_root)

    blob = json.dumps(out, sort_keys=True)
    assert marker not in blob, "the password value leaked into the payload"
    assert "password_configured" in out
    assert out["password_configured"] is True
    assert out["password_secret_key"] == PG_PASSWORD_SECRET_KEY
    # The temp store is discarded with tmp_path; nothing to clean up.


def test_password_configured_reports_availability_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """SQLite carries no password: the boolean is False (not-applicable),
    never a value."""
    _clean_env(monkeypatch)

    cfg = DatabaseConfig.for_sqlite("audit")
    out = effective_config_diagnostics(cfg, settings_db_path=str(tmp_path / "x.db"))
    assert out["password_configured"] is False


# ---------------------------------------------------------------------------
# 4. The fingerprint is stable and secret-free
# ---------------------------------------------------------------------------
def test_fingerprint_is_stable_across_calls(
    persisted_pg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same effective config yields the same identity; a different
    config yields a different one (two shells can now be compared)."""
    _clean_env(monkeypatch)

    cfg = load_database_config("audit", settings_db_path=str(persisted_pg), env={})
    first = effective_config_diagnostics(cfg, settings_db_path=str(persisted_pg), env={})
    second = effective_config_diagnostics(
        cfg, settings_db_path=str(persisted_pg), env={"NSE_DATABASE__PG_USER": "x"}
    )

    assert first["config_identity"]["fingerprint"] == second["config_identity"]["fingerprint"]
    assert first["config_identity"]["fingerprint"].startswith("dbcfg-")
    # The fingerprint is derived from the non-secret connection fields only.
    assert PG_PASSWORD_SECRET_KEY not in first["config_identity"]["fingerprint"]


def test_fingerprint_changes_when_the_connection_changes(
    persisted_pg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch)

    base = load_database_config("audit", settings_db_path=str(persisted_pg), env={})
    base_diag = effective_config_diagnostics(base, settings_db_path=str(persisted_pg), env={})
    changed = DatabaseConfig.for_postgres(domain="audit", host="other", port=7000)
    changed_diag = effective_config_diagnostics(changed, settings_db_path=str(persisted_pg), env={})

    assert (
        base_diag["config_identity"]["fingerprint"]
        != changed_diag["config_identity"]["fingerprint"]
    )


# ---------------------------------------------------------------------------
# 5. Effective fields match the resolved config
# ---------------------------------------------------------------------------
def test_effective_fields_mirror_the_resolved_config(
    persisted_pg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``effective`` is a faithful projection of the config the caller
    passed — the diagnostic never re-resolves, never invents fields."""
    _clean_env(monkeypatch)

    cfg = load_database_config("audit", settings_db_path=str(persisted_pg), env={})
    out = effective_config_diagnostics(cfg, settings_db_path=str(persisted_pg), env={})

    for name in _FIELDS:
        assert out["effective"][name] == getattr(cfg, name), name
    assert set(out["field_sources"]) == set(_FIELDS)
