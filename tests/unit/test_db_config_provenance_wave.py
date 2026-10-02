"""The db-config provenance wave: stale-editable detection + doctor wiring.

GROUND_TRUTH §B: on this box, a bare ``import nexus_scalp`` resolves to
``...\\nse-pr597\\src`` — a STALE non-git snapshot — while the work happens in
other checkouts. Two shells in two checkouts then run DIFFERENT CODE with no
signal; the incident's "AI Providers page throws auth error" is consistent
with imports resolving to ``nse-pr597`` while config resolution ran from
another tree's app-data root.

Two things this wave pins:

1. ``effective_config_diagnostics`` detects the mismatch (module root vs the
   repo root the process CWD belongs to) and reports it as
   ``stale_editable_install`` — LOUD, never silently corrected. The fix is an
   environment fix (install target / PYTHONPATH), NOT a repo-code fallback.
2. ``release/health.py`` attaches the redacted diagnostics to the doctor's
   PostgreSQL AUTH failure report, so an operator sees WHICH source supplied
   which field — the distinction the incident turned on.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

from nexus_scalp.database.config import (
    PG_CONFIG_SETTING_KEY,
    PG_PASSWORD_SECRET_KEY,
    PROVIDER_SETTING_KEY,
    DatabaseConfig,
    load_database_config,
)
from nexus_scalp.database.config_diagnostics import effective_config_diagnostics


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
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
def isolated_settings(tmp_path: Path) -> str:
    """NEXUS_SETTINGS_DB pointed at a fresh temp settings DB (the same
    test-isolation seam the existing doctor tests use)."""
    path = tmp_path / "settings" / "app_settings.db"
    return str(path)


@pytest.fixture()
def persisted_pg_settings(tmp_path: Path) -> str:
    """A settings DB with a full persisted postgresql_config row."""
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
    return str(path)


# ---------------------------------------------------------------------------
# 1. Stale-editable-install detection
# ---------------------------------------------------------------------------
def test_foreign_module_path_is_flagged_as_stale_editable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The resolved module lives under a DIFFERENT checkout than the process
    CWD -> stale_editable_install is True with a loud detail (the §B
    defect: imports from a foreign tree like nse-pr597 while working
    elsewhere)."""
    _clean_env(monkeypatch)
    foreign = tmp_path / "foreign-checkout" / "src" / "nexus_scalp" / "__init__.py"
    runtime = {
        "nexus_scalp_module": str(foreign),
        "cwd": str(tmp_path / "this-checkout"),
    }

    out = effective_config_diagnostics(
        DatabaseConfig.for_sqlite("audit"),
        settings_db_path=str(tmp_path / "x.db"),
        env={},
        runtime_identity=runtime,
    )

    assert out["stale_editable_install"]["detected"] is True
    detail = out["stale_editable_install"]["detail"]
    assert "foreign-checkout" in detail
    assert "this-checkout" in detail
    assert out["runtime_identity"]["nexus_scalp_module"] == str(foreign)


def test_same_checkout_module_path_is_clean(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Module under THIS checkout's src/ -> not stale. The happy path every
    PYTHONPATH=src run (and CI) must satisfy."""
    _clean_env(monkeypatch)
    home = tmp_path / "this-checkout"
    runtime = {
        "nexus_scalp_module": str(home / "src" / "nexus_scalp" / "__init__.py"),
        "cwd": str(home),
    }

    out = effective_config_diagnostics(
        DatabaseConfig.for_sqlite("audit"),
        settings_db_path=str(tmp_path / "x.db"),
        env={},
        runtime_identity=runtime,
    )

    assert out["stale_editable_install"]["detected"] is False
    assert out["stale_editable_install"]["detail"] == ""


def test_missing_module_path_is_not_mislabeled_stale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No module path -> no claim either way (UNKNOWN-ish, never a false
    positive stale flag)."""
    _clean_env(monkeypatch)
    out = effective_config_diagnostics(
        DatabaseConfig.for_sqlite("audit"),
        settings_db_path=str(tmp_path / "x.db"),
        env={},
        runtime_identity={"nexus_scalp_module": None, "cwd": str(tmp_path)},
    )
    assert out["stale_editable_install"]["detected"] is False


def test_real_process_identity_matches_the_suite_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With PYTHONPATH=src (the gate's mandatory contract), the diagnostic's
    REAL runtime identity must resolve inside THIS repo's checkout."""
    _clean_env(monkeypatch)
    import nexus_scalp

    expected_root = Path(__file__).resolve().parents[2]

    out = effective_config_diagnostics(
        DatabaseConfig.for_sqlite("audit"),
        settings_db_path=str(tmp_path / "x.db"),
        env={},
    )
    module_file = out["runtime_identity"]["nexus_scalp_module"]
    assert module_file is not None
    assert expected_root in Path(module_file).resolve().parents
    assert out["stale_editable_install"]["detected"] is False


def test_default_runtime_identity_reports_the_imported_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The no-argument identity still carries cwd + a resolvable module —
    the diagnostic must never silently omit the evidence."""
    _clean_env(monkeypatch)
    out = effective_config_diagnostics(
        DatabaseConfig.for_sqlite("audit"),
        settings_db_path=str(tmp_path / "x.db"),
        env={},
    )
    assert out["runtime_identity"]["cwd"]
    assert "nexus_scalp_module" in out["runtime_identity"]


# ---------------------------------------------------------------------------
# 2. Doctor wiring — the AUTH failure path carries the redacted diagnostics
# ---------------------------------------------------------------------------
def test_pg_failure_report_carries_diagnostics(
    monkeypatch: pytest.MonkeyPatch, persisted_pg_settings: str
) -> None:
    """On an AUTH failure the doctor's DATABASE entry attaches the
    diagnostics (redacted): the operator sees WHICH layer supplied each
    field, and the report stays pasteable (no secrets, JSON-serializable)."""
    import os

    from nexus_scalp.release.health import HealthEngine

    _clean_env(monkeypatch)
    monkeypatch.setenv("NEXUS_SETTINGS_DB", persisted_pg_settings)
    monkeypatch.setenv("NSE_TEST_PG_FAIL", "1")

    engine = HealthEngine()
    entries = [e for e in engine.run_all() if e.category == "DATABASE"]
    assert len(entries) == 1
    entry = entries[0]
    blob = json.dumps(entry.to_dict(), sort_keys=True)
    assert "PG_CONFIG_DIAGNOSTICS" in blob, blob
    assert "persisted_settings" in blob
    # The report carries no credential material — only the boolean + the
    # (non-secret) key name, never a password value.
    assert "password=" not in blob


def test_auth_failure_entry_includes_field_sources(
    monkeypatch: pytest.MonkeyPatch, persisted_pg_settings: str
) -> None:
    """The attached diagnostics name the persisted layer as the source of
    the persisted fields — the provenance the incident needed."""
    from nexus_scalp.release.health import HealthEngine, _pg_failure_diagnostics

    _clean_env(monkeypatch)
    monkeypatch.setenv("NEXUS_SETTINGS_DB", persisted_pg_settings)

    cfg = load_database_config("audit")
    diag = _pg_failure_diagnostics(cfg)
    assert diag["field_sources"]["host"] == "persisted_settings"
    assert diag["field_sources"]["database"] == "persisted_settings"
    assert diag["effective"]["database"] == "nexusdb"
    assert diag["effective"]["username"] == "nse_persisted"
    assert diag["password_configured"] is None or isinstance(diag["password_configured"], bool)


def test_pg_failure_diagnostics_never_contain_a_password(
    monkeypatch: pytest.MonkeyPatch, persisted_pg_settings: str, tmp_path: Path
) -> None:
    """A real secret is present in a temp store; the diagnostics still carry
    no value — only the boolean + the key name."""
    from nexus_scalp.release.health import _pg_failure_diagnostics
    from nexus_scalp.settings.secret_store import SecureSecretStore

    _clean_env(monkeypatch)
    marker = "LEAK-CHECK-9d81f2"
    store_root = tmp_path / "secrets-root"
    SecureSecretStore(root=store_root).set_secret(PG_PASSWORD_SECRET_KEY, marker)

    cfg = load_database_config("audit", settings_db_path=persisted_pg_settings)
    diag = _pg_failure_diagnostics(cfg, secret_root=store_root)
    blob = json.dumps(diag, sort_keys=True)
    assert marker not in blob
    assert diag["password_configured"] is True
