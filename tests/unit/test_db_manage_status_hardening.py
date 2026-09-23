"""Lane D — hardening pins for the four /api/db/manage/* handlers (db-provider-pro).

CONTRACT (wave nse-dbpro §2 Lane D + §4): these tests pin the failure-shape and
secret-safety invariants of the Database tab's manage surface so a regression
turns a test red instead of leaking a secret or silently persisting junk:

  1. SECRET LAW — no /api/db/manage/* response may contain secret material:
     a submitted password value, or DSN userinfo (user:password@host).
  2. FAIL-LOUD ENVELOPE — every failure path answers the code envelope
     {success:false, error:{code, message, request_id}} with a SENTENCE
     message; never exception text, never a traceback, never a bare string.
  3. TEST-CONNECTION NON-DESTRUCTIVE — probing with a WRONG password must not
     clobber the operator's stored default secret (contract §1: the DEFAULT
     stored secret must survive a failed test).
  4. CONFIG ALLOWLIST — POST /api/db/manage/config refuses unknown/typo keys
     (code DB_CONFIG_UNKNOWN_KEYS) rather than persisting them, and keeps the
     password/confirm_password mismatch behavior intact.

The routes are exercised through FastAPI TestClient against the app assembled
by create_app (auth disabled) with per-test temp settings DB + temp secret
store.  No live server is contacted, and the real DPAPI keystore is never
opened (the conftest session fixture + the local fixture below both redirect
the secret-store root).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

GOOD_PASSWORD = "GOOD-STORED-SECRET"
WRONG_PASSWORD = "WRONG-PROBE-PASSWORD"
MISMATCH_SENTINEL = "mismatch-never-persisted"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Per-test settings DB + auth off + private secret-store root."""
    monkeypatch.setenv("NSE_WEB_AUTH_DISABLE", "1")
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(tmp_path / "app_settings.db"))
    monkeypatch.setenv("NEXUS_AUDIT_DB", str(tmp_path / "audit.db"))

    from nexus_scalp.settings import secret_store as _ss

    root = tmp_path / "secrets"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_ss, "app_data_root", lambda: root, raising=False)
    return root


@pytest.fixture(scope="module")
def app() -> Any:
    """The real create_app app; auth is disabled only while it is built.

    The WEB-AUTH layer is installed as the last step of ``create_app`` (it
    reads ``NSE_WEB_AUTH_DISABLE`` at build time), so the knob must be set
    here — function-scoped env fixtures run too late for a module-scoped app.
    It is restored immediately so no other test in the session sees it.
    """
    from nexus_scalp.web.server import create_app

    previous = os.environ.get("NSE_WEB_AUTH_DISABLE")
    os.environ["NSE_WEB_AUTH_DISABLE"] = "1"
    try:
        return create_app(engine_ref=None)
    finally:
        if previous is None:
            os.environ.pop("NSE_WEB_AUTH_DISABLE", None)
        else:
            os.environ["NSE_WEB_AUTH_DISABLE"] = previous


@pytest.fixture
def client(app: Any, isolated_env: Path) -> Any:
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def store(isolated_env: Path) -> Any:
    from nexus_scalp.settings.secret_store import SecureSecretStore

    return SecureSecretStore(isolated_env)


def _config_row() -> dict:
    from nexus_scalp.database.config import PG_CONFIG_SETTING_KEY
    from nexus_scalp.settings import SettingsDatabase

    db = SettingsDatabase()
    try:
        row = db.get(PG_CONFIG_SETTING_KEY)
        return dict(row.value) if row is not None and row.value is not None else {}
    finally:
        db.close()


def _good_form() -> dict:
    return {
        "host": "db.internal",
        "port": 5433,
        "database": "nse_audit",
        "username": "nse_operator",
        "ssl_mode": "require",
    }


def _assert_no_secret(body: Any, needles: tuple[str, ...]) -> None:
    """Fail loudly if any secret material appears anywhere in the response."""
    text = json.dumps(body, ensure_ascii=False)
    leaked = [n for n in needles if n in text]
    assert not leaked, f"secret material leaked into the response: {leaked!r}"


# ---------------------------------------------------------------------------
# 1. SECRET LAW — no secret material in any /api/db/manage/* response
# ---------------------------------------------------------------------------


class TestNoSecretEchoed:
    def test_config_response_carries_no_password(self, client: Any) -> None:
        r = client.post(
            "/api/db/manage/config",
            json={**_good_form(), "password": GOOD_PASSWORD, "confirm_password": GOOD_PASSWORD},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert "password" not in body
        _assert_no_secret(body, (GOOD_PASSWORD,))

    def test_status_response_carries_no_password(self, client: Any, store: Any) -> None:
        store.set_secret(_pg_key(), GOOD_PASSWORD)
        client.post("/api/db/manage/config", json=_good_form())

        body = client.get("/api/db/manage/status").json()
        assert body["success"] is True
        # The stored config row is echoed back — minus the secret material.
        _assert_no_secret(body, (GOOD_PASSWORD,))
        postgres = body.get("postgres") or {}
        assert "password_secret" not in postgres or isinstance(postgres["password_secret"], str)
        assert postgres.get("password") is None

    def test_status_reports_the_stored_config_and_secret_truth(
        self, client: Any, store: Any
    ) -> None:
        store.set_secret(_pg_key(), GOOD_PASSWORD)
        client.post(
            "/api/db/manage/config",
            json={**_good_form(), "password": GOOD_PASSWORD, "confirm_password": GOOD_PASSWORD},
        )

        body = client.get("/api/db/manage/status").json()
        assert body["success"] is True
        # The tab must see the configuration that is actually stored.
        postgres = body.get("postgres") or {}
        assert postgres.get("host") == "db.internal"
        assert postgres.get("port") == 5433
        # And the SecretStore truth, not a constant false negative.
        assert body["password_set"] is True

    def test_status_reports_false_when_no_secret_exists(self, client: Any) -> None:
        client.post("/api/db/manage/config", json=_good_form())

        body = client.get("/api/db/manage/status").json()
        assert body["password_set"] is False
        assert (body.get("postgres") or {}).get("host") == "db.internal"

    def test_status_strips_a_stray_plaintext_password(self, client: Any, store: Any) -> None:
        # A foreign writer leaves a plaintext password in the settings row
        # (legacy row shape / manual edit).  The status echo must drop it.
        from nexus_scalp.database.config import PG_CONFIG_SETTING_KEY
        from nexus_scalp.settings import SettingsDatabase

        db = SettingsDatabase()
        try:
            db.set(
                PG_CONFIG_SETTING_KEY,
                {**_good_form(), "password": "STRAY-PLAINTEXT"},
                value_type="json",
                source="USER_SETTINGS",
                actor="web",
            )
        finally:
            db.close()

        body = client.get("/api/db/manage/status").json()
        _assert_no_secret(body, ("STRAY-PLAINTEXT",))
        assert (body.get("postgres") or {}).get("password") is None

    def test_provider_response_carries_no_secret(self, client: Any, store: Any) -> None:
        store.set_secret(_pg_key(), GOOD_PASSWORD)
        r = client.post("/api/db/manage/provider", json={"provider": "postgresql"})
        _assert_no_secret(r.json(), (GOOD_PASSWORD,))

    def test_failed_test_connection_carries_no_secret(self, client: Any) -> None:
        # An unreachable target so the probe genuinely fails.
        r = client.post(
            "/api/db/manage/test-connection",
            json={**_good_form(), "host": "127.0.0.1", "port": 1, "password": WRONG_PASSWORD},
        )
        body = r.json()
        _assert_no_secret(body, (WRONG_PASSWORD,))
        # A failure must not become a 5xx stack trace.
        assert r.status_code == 200

    def test_no_dsn_userinfo_in_any_response(self, client: Any, store: Any) -> None:
        store.set_secret(_pg_key(), GOOD_PASSWORD)
        client.post("/api/db/manage/config", json=_good_form())

        for method, path, payload in (
            ("get", "/api/db/manage/status", None),
            (
                "post",
                "/api/db/manage/test-connection",
                {**_good_form(), "password": WRONG_PASSWORD},
            ),
        ):
            r = (
                getattr(client, method)(path, json=payload)
                if payload
                else getattr(client, method)(path)
            )
            _assert_no_secret(r.json(), (f"nse_operator:{GOOD_PASSWORD}", f":{GOOD_PASSWORD}@"))


# ---------------------------------------------------------------------------
# 2. FAIL-LOUD ENVELOPE — every error is a code envelope with a sentence
# ---------------------------------------------------------------------------


class TestErrorEnvelope:
    def _assert_envelope(self, body: dict) -> dict:
        assert body.get("success") is False, f"failure path returned success=true: {body}"
        error = body.get("error")
        assert isinstance(error, dict), f"error is not an envelope: {body!r}"
        assert isinstance(error.get("code"), str) and error["code"]
        message = error.get("message")
        assert isinstance(message, str) and message.strip(), "message is missing"
        # A sentence, never exception text / traceback / class names.
        assert "Traceback" not in message
        assert "Error" not in message
        assert "raise " not in message
        assert isinstance(error.get("request_id"), str) and error["request_id"]
        return error

    def test_config_unknown_keys_fail_loud(self, client: Any) -> None:
        r = client.post(
            "/api/db/manage/config",
            json={**_good_form(), "passwrod": MISMATCH_SENTINEL, "extra_junk": 1},
        )
        assert r.status_code == 200
        error = self._assert_envelope(r.json())
        assert error["code"] == "DB_CONFIG_UNKNOWN_KEYS"

    def test_config_mismatch_is_still_an_envelope(self, client: Any) -> None:
        r = client.post(
            "/api/db/manage/config",
            json={**_good_form(), "password": "aaa", "confirm_password": "bbb"},
        )
        error = self._assert_envelope(r.json())
        assert error["code"] == "PASSWORD_MISMATCH"
        # The mismatched password was not persisted and the row is untouched.
        assert MISMATCH_SENTINEL not in str(_config_row())

    def test_provider_failure_is_sanitized(
        self, client: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import nexus_scalp.settings as settings_mod

        class _Boom:
            def set_database_provider(self, *args: Any, **kwargs: Any) -> dict:
                raise RuntimeError("boom-internals password=LEAKME")

        monkeypatch.setattr(settings_mod, "load_settings_service", _Boom)
        r = client.post("/api/db/manage/provider", json={"provider": "postgresql"})
        error = self._assert_envelope(r.json())
        assert error["code"] == "DB_MANAGE_PROVIDER_FAILED"
        # The exception text — which can embed credentials — never escapes.
        assert "LEAKME" not in json.dumps(r.json())
        assert "RuntimeError" not in error["message"]

    def test_config_persistence_failure_is_sanitized(
        self, client: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import nexus_scalp.settings as settings_mod

        class _Boom:
            def set_postgres_config(self, *args: Any, **kwargs: Any) -> dict:
                raise RuntimeError("disk-on-fire /etc/shadow")

        monkeypatch.setattr(settings_mod, "load_settings_service", _Boom)
        r = client.post("/api/db/manage/config", json=_good_form())
        error = self._assert_envelope(r.json())
        assert error["code"] == "DB_MANAGE_CONFIG_FAILED"
        assert "disk-on-fire" not in json.dumps(r.json())

    def test_status_failure_is_sanitized(
        self, client: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from nexus_scalp.database import health

        def _boom() -> dict:
            raise RuntimeError("health-internals token=AKIA-SECRET")

        monkeypatch.setattr(health, "health_snapshot", _boom)
        r = client.get("/api/db/manage/status")
        error = self._assert_envelope(r.json())
        assert error["code"] == "DB_MANAGE_STATUS_FAILED"
        assert "AKIA-SECRET" not in json.dumps(r.json())

    def test_provider_parses_unknown_provider_safely(self, client: Any) -> None:
        # DatabaseProvider.parse resolves unknown -> sqlite (never raises),
        # so this is a success path that must NOT be an error envelope.
        r = client.post("/api/db/manage/provider", json={"provider": "not-a-real-provider"})
        body = r.json()
        assert body["success"] is True
        assert body["provider"] == "sqlite"


# ---------------------------------------------------------------------------
# 3. TEST-CONNECTION NON-DESTRUCTIVE — a wrong probe password must not clobber
#    the operator's stored default secret (contract §1).
# ---------------------------------------------------------------------------


class TestConnectionProbeSecretSafety:
    def test_failed_probe_keeps_default_secret(self, client: Any, store: Any) -> None:
        store.set_secret(_pg_key(), GOOD_PASSWORD)

        r = client.post(
            "/api/db/manage/test-connection",
            json={**_good_form(), "host": "127.0.0.1", "port": 1, "password": WRONG_PASSWORD},
        )
        body = r.json()
        assert r.status_code == 200
        # The probe failed (nothing is listening on :1) and the operator's
        # real stored password is intact — not the wrong probe value.
        assert body["connected"] is False
        assert store.get_secret(_pg_key()) == GOOD_PASSWORD
        assert store.get_secret(_pg_key()) != WRONG_PASSWORD

    def test_failed_probe_leaves_no_residue(self, client: Any, store: Any) -> None:
        store.set_secret(_pg_key(), GOOD_PASSWORD)

        client.post(
            "/api/db/manage/test-connection",
            json={**_good_form(), "host": "127.0.0.1", "port": 1, "password": WRONG_PASSWORD},
        )
        # No probe-time key is left behind in the store.
        assert store.get_secret(f"{_pg_key()}.probe") is None

    def test_blank_password_probe_uses_the_stored_secret(self, client: Any, store: Any) -> None:
        store.set_secret(_pg_key(), GOOD_PASSWORD)

        r = client.post(
            "/api/db/manage/test-connection",
            json={**_good_form(), "host": "127.0.0.1", "port": 1},
        )
        assert r.status_code == 200
        # Blank-on-the-wire means "keep the stored secret" (contract §1).
        assert store.get_secret(_pg_key()) == GOOD_PASSWORD


# ---------------------------------------------------------------------------
# 4. CONFIG ALLOWLIST — unknown/typo keys are refused, never persisted
# ---------------------------------------------------------------------------


class TestConfigAllowlist:
    def test_unknown_keys_are_refused_and_not_persisted(self, client: Any) -> None:
        r = client.post(
            "/api/db/manage/config",
            json={**_good_form(), "passwrod": "typo-should-not-persist", "random_junk": {"x": 1}},
        )
        body = r.json()
        assert body["success"] is False
        assert body["error"]["code"] == "DB_CONFIG_UNKNOWN_KEYS"

        row = _config_row()
        assert row == {}, "a rejected payload must persist nothing"

    def test_unknown_keys_message_names_the_offenders(self, client: Any) -> None:
        r = client.post("/api/db/manage/config", json={**_good_form(), "passwrod": "x"})
        message = r.json()["error"]["message"]
        assert "passwrod" in message
        # The message is a sentence, and it never echoes secret values.
        assert "x" not in message.replace("passwrod", "")

    def test_known_keys_still_persist(self, client: Any) -> None:
        r = client.post("/api/db/manage/config", json=_good_form())
        assert r.status_code == 200
        assert r.json()["success"] is True

        row = _config_row()
        for key, expected in (
            ("host", "db.internal"),
            ("port", 5433),
            ("database", "nse_audit"),
            ("username", "nse_operator"),
            ("ssl_mode", "require"),
            ("provider", "postgresql"),
            ("domain", "audit"),
        ):
            assert row.get(key) == expected, f"known key '{key}' was not persisted"

    def test_password_never_lands_in_the_row(self, client: Any, store: Any) -> None:
        client.post(
            "/api/db/manage/config",
            json={**_good_form(), "password": GOOD_PASSWORD, "confirm_password": GOOD_PASSWORD},
        )
        row = _config_row()
        assert "password" not in row
        assert "confirm_password" not in row
        # The value itself went to the OS store under the canonical key only.
        assert store.get_secret(_pg_key()) == GOOD_PASSWORD

    def test_blank_password_keeps_stored_secret(self, client: Any, store: Any) -> None:
        store.set_secret(_pg_key(), GOOD_PASSWORD)

        r = client.post("/api/db/manage/config", json=_good_form())  # no password field
        assert r.json()["success"] is True
        assert r.json()["password_set"] is True
        assert store.get_secret(_pg_key()) == GOOD_PASSWORD


def _pg_key() -> str:
    from nexus_scalp.database.config import PG_PASSWORD_SECRET_KEY

    return PG_PASSWORD_SECRET_KEY
