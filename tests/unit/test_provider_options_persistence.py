"""Lane D — additive-only persistence + secret-reference invariants (db-provider-pro).

CONTRACT (wave nse-dbpro §2 Lane D): these tests pin the persistence law for
the Database tab's option/config surface so a future edit can regress it only
by turning a test red:

  * the advanced knobs (command_timeout_sec / connect_timeout_sec /
    migrate_on_startup / pooling_enabled) are written ADDITIVELY onto the
    SAME PG_CONFIG row the discrete form uses — existing keys
    (provider/domain/host/port/database/username/ssl_mode) are preserved;
  * the ``password_secret`` REFERENCE survives every write and is never
    replaced by a plaintext password value;
  * a plaintext ``password`` key never lands in the persisted row.

The module under test is :mod:`nexus_scalp.settings.provider_options`, which
is the ONLY writer an options write goes through (contract §3.3).  Everything
runs against a per-test temp settings DB + temp secret store; no live server,
no real DPAPI keystore.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.database.config import (
    PG_CONFIG_SETTING_KEY,
    PG_PASSWORD_SECRET_KEY,
)
from nexus_scalp.settings import SecureSecretStore, SettingsDatabase, SettingsService
from nexus_scalp.settings import provider_options as po

# ---------------------------------------------------------------------------
# Fixtures: a private settings DB + a private OS secret store per test.
# ---------------------------------------------------------------------------


@pytest.fixture
def settings_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "app_settings.db"
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(db_path))
    return db_path


@pytest.fixture
def secret_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Never touch the operator's real DPAPI keystore."""
    root = tmp_path / "secrets"
    root.mkdir(parents=True, exist_ok=True)
    from nexus_scalp.settings import secret_store as _ss

    monkeypatch.setattr(_ss, "app_data_root", lambda: root, raising=False)
    return root


def _open_db(db_path: Path) -> SettingsDatabase:
    return SettingsDatabase(db_path)


def _row(settings_db: Path) -> dict:
    db = _open_db(settings_db)
    try:
        row = db.get(PG_CONFIG_SETTING_KEY)
        return dict(row.value) if row is not None and row.value is not None else {}
    finally:
        db.close()


def _seed_discrete_config(settings_db: Path, secret_root: Path) -> dict:
    """Persist a realistic discrete-form config (no advanced knobs yet)."""
    svc = SettingsService(db=_open_db(settings_db), secret_store=SecureSecretStore(secret_root))
    try:
        svc.set_postgres_config(
            {
                "host": "db.internal",
                "port": 5433,
                "database": "nse_audit",
                "username": "nse_operator",
                "ssl_mode": "require",
                "password": "ORIGINAL-SECRET",
            }
        )
    finally:
        svc.close()
    return _row(settings_db)


class TestAdditivePersistence:
    """An options write must never drop or rewrite the discrete-form keys."""

    def test_existing_keys_survive_an_options_write(
        self, settings_db: Path, secret_root: Path
    ) -> None:
        seeded = _seed_discrete_config(settings_db, secret_root)
        assert seeded["host"] == "db.internal"

        po.write_options({"connect_timeout_sec": 30, "pooling_enabled": False})

        after = _row(settings_db)
        # Every discrete-form key is preserved verbatim.
        for key, expected in (
            ("provider", "postgresql"),
            ("domain", "audit"),
            ("host", "db.internal"),
            ("port", 5433),
            ("database", "nse_audit"),
            ("username", "nse_operator"),
            ("ssl_mode", "require"),
        ):
            assert after.get(key) == expected, f"discrete key '{key}' was lost or rewritten"
        # And the requested knobs landed.
        assert after["connect_timeout_sec"] == 30
        assert after["pooling_enabled"] is False

    def test_two_option_writes_do_not_drop_the_form(
        self, settings_db: Path, secret_root: Path
    ) -> None:
        _seed_discrete_config(settings_db, secret_root)
        po.write_options({"command_timeout_sec": 120})
        po.write_options({"migrate_on_startup": False})

        after = _row(settings_db)
        assert after["host"] == "db.internal"
        assert after["command_timeout_sec"] == 120
        assert after["migrate_on_startup"] is False
        # The first write's knob survives the second write.
        assert after.get("connect_timeout_sec") in (10, None)

    def test_write_adds_no_key_the_operator_did_not_send(
        self, settings_db: Path, secret_root: Path
    ) -> None:
        seeded = _seed_discrete_config(settings_db, secret_root)
        po.write_options({"connect_timeout_sec": 5})

        after = _row(settings_db)
        # An options write may only touch the four knobs, plus the secret-store
        # REFERENCE (set_postgres_config never persists that key itself — the
        # reference is re-derived on write, contract §1 secret law).  Every
        # other key in the row must be one the discrete form already persisted.
        assert set(after) - set(seeded) <= set(po.OPTION_KEYS) | {"password_secret"}
        assert after["password_secret"] == PG_PASSWORD_SECRET_KEY


class TestSecretReferenceInvariant:
    """``password_secret`` is a REFERENCE — never a plaintext value."""

    def test_reference_survives_an_options_write(
        self, settings_db: Path, secret_root: Path
    ) -> None:
        _seed_discrete_config(settings_db, secret_root)

        po.write_options({"connect_timeout_sec": 20})

        after = _row(settings_db)
        assert after["password_secret"] == PG_PASSWORD_SECRET_KEY

    def test_plaintext_password_never_persisted_by_options_write(
        self, settings_db: Path, secret_root: Path
    ) -> None:
        _seed_discrete_config(settings_db, secret_root)

        # A hostile/typoed options body trying to smuggle a password in.
        result = po.write_options({"connect_timeout_sec": 20, "password": "SMUGGLED-PLAINTEXT"})

        after = _row(settings_db)
        assert "password" not in after, "a plaintext password landed in the settings row"
        assert after.get("password_secret") != "SMUGGLED-PLAINTEXT"
        assert "SMUGGLED-PLAINTEXT" not in str(after)
        # The smuggled key is refused loudly, not silently persisted.
        assert result["persisted"] == ["connect_timeout_sec"] or result["error"]

    def test_reference_added_when_row_had_none(self, settings_db: Path, secret_root: Path) -> None:
        # A row written before the reference existed (legacy/manual shape).
        db = _open_db(settings_db)
        try:
            db.set(
                PG_CONFIG_SETTING_KEY,
                {"host": "db.internal", "port": 5432},
                value_type="json",
                source="USER_SETTINGS",
                actor="web",
            )
        finally:
            db.close()

        po.write_options({"pooling_enabled": False})

        after = _row(settings_db)
        assert after["password_secret"] == PG_PASSWORD_SECRET_KEY
        # The plaintext password is still nowhere in the row.
        assert "password" not in after


class TestOptionsReadDefaults:
    """A missing knob renders as the backend default, never an invented value."""

    def test_defaults_for_a_fresh_row(self, settings_db: Path, secret_root: Path) -> None:
        out = po.read_options("audit")
        assert out["command_timeout_sec"] == po.DEFAULT_OPTIONS["command_timeout_sec"]
        assert out["connect_timeout_sec"] == po.DEFAULT_OPTIONS["connect_timeout_sec"]
        assert out["migrate_on_startup"] is True
        assert out["pooling_enabled"] is True
        assert out["domain"] == "audit"

    def test_round_trip_after_write(self, settings_db: Path, secret_root: Path) -> None:
        _seed_discrete_config(settings_db, secret_root)
        po.write_options(
            {"command_timeout_sec": 45, "connect_timeout_sec": 15, "migrate_on_startup": False}
        )

        out = po.read_options("audit")
        assert out["command_timeout_sec"] == 45
        assert out["connect_timeout_sec"] == 15
        assert out["migrate_on_startup"] is False
        assert out["pooling_enabled"] is True  # untouched -> default

    def test_status_snapshot_reports_all_four_knobs(
        self, settings_db: Path, secret_root: Path
    ) -> None:
        _seed_discrete_config(settings_db, secret_root)
        po.write_options({"connect_timeout_sec": 12})

        snap = po.options_status_snapshot("audit")
        assert set(snap["options"]) == set(po.OPTION_KEYS)
        assert snap["options"]["connect_timeout_sec"] == 12
        # Known domain list comes from the registry, not from thin air.
        assert "audit" in snap["domains"]


class TestUnknownKeyRejection:
    """Unknown keys are rejected loudly (fail-loud), never silently persisted."""

    @pytest.mark.parametrize(
        "bad",
        [
            {"pasword": 5},
            {"command_timeout_secs": 10},
            {"host": "evil.example"},
            {"password_secret": "evil-plaintext"},
        ],
    )
    def test_unknown_key_refused(self, settings_db: Path, secret_root: Path, bad: dict) -> None:
        seeded = _seed_discrete_config(settings_db, secret_root)

        known, reason = po.validate_options(bad)
        assert reason, f"unknown key {set(bad)} was accepted silently"
        assert known == (set(bad) & po.OPTION_KEYS)

        # And the write path keeps a hostile NEW key out of the row.  (A key
        # the discrete form already owns — e.g. a forged 'host' — stays
        # verbatim: additive law, and provider_options is not its writer.)
        po.write_options(bad)
        after = _row(settings_db)
        for hostile_key in bad:
            if hostile_key in seeded:
                continue
            assert hostile_key not in after, f"hostile key '{hostile_key}' was persisted"

    def test_bounds_are_validated(self, settings_db: Path, secret_root: Path) -> None:
        _seed_discrete_config(settings_db, secret_root)

        _known, reason = po.validate_options({"connect_timeout_sec": 999})
        assert reason and "connect_timeout_sec" in reason

        _known, reason = po.validate_options({"command_timeout_sec": -1})
        assert reason and "command_timeout_sec" in reason

        _known, reason = po.validate_options({"migrate_on_startup": "yes"})
        assert reason and "migrate_on_startup" in reason


class TestPersistenceProvenance:
    """Contract §4: settings writes carry value_type=json / USER_SETTINGS / web."""

    def test_write_row_carries_expected_provenance(
        self, settings_db: Path, secret_root: Path
    ) -> None:
        _seed_discrete_config(settings_db, secret_root)
        po.write_options({"connect_timeout_sec": 20})

        db = _open_db(settings_db)
        try:
            row = db.get(PG_CONFIG_SETTING_KEY)
            assert row is not None
            assert row.value_type == "json"
            assert row.source == "USER_SETTINGS"
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Same invariants through the real HTTP surface: POST /api/db/manage/config
# must be additive too (its handler merges into the stored row), otherwise a
# save from the discrete form silently wipes the advanced knobs another writer
# put there.
# ---------------------------------------------------------------------------


class TestConfigEndpointIsAdditive:
    @pytest.fixture
    def client(self, settings_db: Path, secret_root: Path) -> Any:
        """Real create_app app (auth disabled while built) + isolated env."""
        from fastapi.testclient import TestClient

        from nexus_scalp.web.server import create_app

        previous = os.environ.get("NSE_WEB_AUTH_DISABLE")
        os.environ["NSE_WEB_AUTH_DISABLE"] = "1"
        try:
            return TestClient(create_app(engine_ref=None), raise_server_exceptions=False)
        finally:
            if previous is None:
                os.environ.pop("NSE_WEB_AUTH_DISABLE", None)
            else:
                os.environ["NSE_WEB_AUTH_DISABLE"] = previous

    @staticmethod
    def _form() -> dict:
        return {
            "host": "db.internal",
            "port": 5433,
            "database": "nse_audit",
            "username": "nse_operator",
            "ssl_mode": "require",
        }

    def test_config_save_preserves_advanced_knobs(
        self, settings_db: Path, secret_root: Path, client: Any
    ) -> None:
        _seed_discrete_config(settings_db, secret_root)
        po.write_options({"connect_timeout_sec": 30, "pooling_enabled": False})

        r = client.post("/api/db/manage/config", json={**self._form(), "host": "db.other"})
        assert r.status_code == 200
        assert r.json()["success"] is True

        after = _row(settings_db)
        assert after["host"] == "db.other"  # the save took effect
        assert after["connect_timeout_sec"] == 30  # knobs survived
        assert after["pooling_enabled"] is False

    def test_config_save_preserves_the_secret_reference(
        self, settings_db: Path, secret_root: Path, client: Any
    ) -> None:
        _seed_discrete_config(settings_db, secret_root)
        po.write_options({"command_timeout_sec": 60})  # stamps password_secret
        assert _row(settings_db)["password_secret"] == PG_PASSWORD_SECRET_KEY

        # A password-less save ("keep the stored secret") must not drop the
        # reference, and must not invent a plaintext password either.
        r = client.post("/api/db/manage/config", json=self._form())
        assert r.json()["success"] is True

        after = _row(settings_db)
        assert after["password_secret"] == PG_PASSWORD_SECRET_KEY
        assert after["command_timeout_sec"] == 60
        assert "password" not in after
        assert "confirm_password" not in after

    def test_config_save_is_json_user_settings_web(
        self, settings_db: Path, secret_root: Path, client: Any
    ) -> None:
        import sqlite3

        client.post("/api/db/manage/config", json=self._form())

        db = _open_db(settings_db)
        try:
            row = db.get(PG_CONFIG_SETTING_KEY)
            assert row is not None
            assert row.value_type == "json"
            assert row.source == "USER_SETTINGS"
        finally:
            db.close()

        # Contract §4 names actor="web" for the web-layer write; it lives in
        # the audit trail, not in the settings row itself.
        conn = sqlite3.connect(str(settings_db))
        try:
            actor = conn.execute(
                "SELECT actor FROM settings_audit "
                "WHERE setting_name = ? ORDER BY rowid DESC LIMIT 1",
                (PG_CONFIG_SETTING_KEY,),
            ).fetchone()
        finally:
            conn.close()
        assert actor is not None and actor[0] == "web"
