"""Route contracts for Lane A (db-provider-pro, contract §3.2 / §3.3).

Pinned here:
  * POST /api/db/manage/parse-url returns the FIVE non-secret fields only —
    a password carried by the URL is routed to the OS SecretStore under
    PG_PASSWORD_SECRET_KEY and never appears in any response body;
  * every refusal is the code envelope (DB_URL_PARSE_FAILED /
    DB_OPTIONS_INVALID) with a human sentence + request_id, never a traceback;
  * parse-url NEVER persists the discrete connection fields;
  * options writes are additive on the existing PG_CONFIG row: existing keys
    survive and the password_secret REFERENCE is never replaced by plaintext;
  * /api/db/manage/status keeps every pre-existing key's name and shape and
    GAINS the `options` block + the known domain database-name list.

The app is assembled the same way production assembles it
(register_diagnostics_state_routes), so the mount site itself is covered.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus_scalp.database.config import PG_CONFIG_SETTING_KEY, PG_PASSWORD_SECRET_KEY
from nexus_scalp.web import db_provider_routes as provider_routes

# Test-only placeholder.  Deliberately built at runtime (never a `user:pass@`
# literal in this file) and never echoed — see _assert_no_password().
FAKE_PASSWORD = "not-a-real-" + "credential"
_USER = "ops"
_HOST = "db.internal"
_DB = "nse_audit"


def _url(*, password: str = "", sslmode: str = "require", port: int = 5433) -> str:
    """Build a URL from parts so no credential URL appears in source."""
    creds = f"{_USER}:{password}@" if password else f"{_USER}@"
    query = f"?sslmode={sslmode}" if sslmode else ""
    return f"postgresql://{creds}{_HOST}:{port}/{_DB}{query}"


def _assert_no_password(body: str) -> None:
    """Secret law: a leaked password fails the test WITHOUT printing it."""
    if FAKE_PASSWORD in body:
        pytest.fail("response body leaked the connection password (value withheld)")


@pytest.fixture()
def settings_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[Any]:
    """Isolated settings database per test (never the developer's real one)."""
    db_path = tmp_path / "app_settings.db"
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(db_path))

    from nexus_scalp.settings.service import SettingsDatabase

    db = SettingsDatabase(db_path=db_path)
    yield db
    db.close()


@pytest.fixture()
def secret_writes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Record SecretStore writes instead of touching the real OS store."""
    from nexus_scalp.settings.secret_store import SecureSecretStore

    writes: list[tuple[str, str]] = []
    monkeypatch.setattr(
        SecureSecretStore,
        "set_secret",
        lambda self, name, value: writes.append((name, value)),
    )
    return writes


@pytest.fixture()
def client(settings_db: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A test app assembled exactly like production's mount site.

    Depends on `settings_db` so EVERY test in this module is isolated from
    the developer's real settings database (writes never escape tmp_path).
    """
    from nexus_scalp.web.diagnostics_state_routes import (
        register_diagnostics_state_routes,
    )
    from nexus_scalp.web.errors import new_request_id, safe_error_payload

    def _err(code: str = "INTERNAL_ERROR", **kw: Any) -> dict[str, Any]:
        return safe_error_payload(code=code, request_id=new_request_id(), **kw)

    app = FastAPI()
    register_diagnostics_state_routes(app, _err, lambda *a, **k: None, lambda x: x, lambda: {})
    with TestClient(app) as test_client:
        yield test_client


class TestParseUrl:
    def test_success_returns_exactly_the_five_non_secret_fields(
        self, client: TestClient, secret_writes: list[tuple[str, str]]
    ) -> None:
        resp = client.post(
            "/api/db/manage/parse-url",
            json={"url": _url(password=FAKE_PASSWORD)},
        )
        assert resp.status_code == 200
        body = resp.text
        _assert_no_password(body)
        payload = resp.json()
        assert payload["success"] is True
        assert payload["fields"] == {
            "host": _HOST,
            "port": 5433,
            "database": _DB,
            "username": _USER,
            "ssl_mode": "require",
        }
        assert "password" not in payload["fields"]

    def test_url_password_is_routed_to_the_secret_store(
        self, client: TestClient, secret_writes: list[tuple[str, str]]
    ) -> None:
        resp = client.post(
            "/api/db/manage/parse-url",
            json={"url": _url(password=FAKE_PASSWORD)},
        )
        assert resp.status_code == 200
        _assert_no_password(resp.text)
        assert secret_writes == [(PG_PASSWORD_SECRET_KEY, FAKE_PASSWORD)]

    def test_url_without_password_writes_no_secret(
        self, client: TestClient, secret_writes: list[tuple[str, str]]
    ) -> None:
        resp = client.post("/api/db/manage/parse-url", json={"url": _url()})
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert secret_writes == []

    def test_url_encoded_password_is_decoded_before_the_store(
        self, client: TestClient, secret_writes: list[tuple[str, str]]
    ) -> None:
        encoded = "p%40ss%3Aword"
        resp = client.post(
            "/api/db/manage/parse-url",
            json={"url": f"postgresql://{_USER}:{encoded}@{_HOST}:5433/{_DB}"},
        )
        assert resp.status_code == 200
        _assert_no_password(resp.text)
        # the operator's real value reaches the store, decoded — not the raw
        # percent-encoding they typed
        assert secret_writes and secret_writes[0][0] == PG_PASSWORD_SECRET_KEY
        assert secret_writes[0][1] != encoded

    @pytest.mark.parametrize(
        "bad",
        [
            f"mysql://user@{_HOST}:5433/{_DB}",
            f"postgresql://{_USER}@{_HOST}:5433",  # no database name in the path
            f"postgresql://{_USER}@{_HOST}:notaport/{_DB}",
            f"postgresql://{_USER}@{_HOST}:70000/{_DB}",
            f"postgresql://{_USER}@/{_DB}",  # missing host
        ],
    )
    def test_parse_failure_is_a_code_envelope_with_a_sentence(
        self,
        client: TestClient,
        secret_writes: list[tuple[str, str]],
        bad: str,
    ) -> None:
        resp = client.post("/api/db/manage/parse-url", json={"url": bad})
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["success"] is False
        error = payload["error"]
        assert error["code"] == "DB_URL_PARSE_FAILED"
        message = error["message"]
        assert message.endswith(".")
        assert error["request_id"].startswith("req_")
        # never exception text / traceback / credentials
        assert "Traceback" not in resp.text
        assert "line " not in message
        assert "fields" not in payload

    def test_empty_url_is_refused_with_a_sentence(self, client: TestClient) -> None:
        for payload in ({}, {"url": ""}, {"url": "   "}, {"url": None}):
            resp = client.post("/api/db/manage/parse-url", json=payload)
            assert resp.status_code == 200
            error = resp.json()["error"]
            assert error["code"] == "DB_URL_PARSE_FAILED"
            assert error["message"].endswith(".")

    def test_parse_never_persists_the_discrete_fields(
        self, client: TestClient, settings_db: Any, secret_writes: list[tuple[str, str]]
    ) -> None:
        resp = client.post("/api/db/manage/parse-url", json={"url": _url()})
        assert resp.json()["success"] is True
        assert settings_db.get(PG_CONFIG_SETTING_KEY) is None

    def test_request_id_is_echoed_from_the_client_header(self, client: TestClient) -> None:
        resp = client.post(
            "/api/db/manage/parse-url",
            json={"url": "nonsense"},
            headers={"X-Request-ID": "req_client42"},
        )
        assert resp.json()["error"]["request_id"] == "req_client42"


class TestProviderOptions:
    def test_get_returns_the_four_knobs_plus_domain_truth(
        self, client: TestClient, settings_db: Any
    ) -> None:
        resp = client.get("/api/db/manage/options")
        assert resp.status_code == 200
        options = resp.json()["options"]
        assert options == {
            "command_timeout_sec": 0,
            "connect_timeout_sec": 10,
            "migrate_on_startup": True,
            "pooling_enabled": True,
            "domain": "audit",
            "database": "nse_audit",
        }
        _assert_no_password(resp.text)

    def test_post_persists_and_reports_written_keys(
        self, client: TestClient, settings_db: Any
    ) -> None:
        resp = client.post(
            "/api/db/manage/options",
            json={"command_timeout_sec": 30, "pooling_enabled": False},
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "success": True,
            "persisted": ["command_timeout_sec", "pooling_enabled"],
        }

        reread = client.get("/api/db/manage/options").json()["options"]
        assert reread["command_timeout_sec"] == 30
        assert reread["pooling_enabled"] is False
        assert reread["connect_timeout_sec"] == 10  # untouched by the write

    @pytest.mark.parametrize("key", ["bogus", "password", "host", "provider"])
    def test_unknown_keys_are_rejected_fail_loud(
        self, client: TestClient, settings_db: Any, key: str
    ) -> None:
        resp = client.post("/api/db/manage/options", json={key: "x"})
        payload = resp.json()
        assert payload["success"] is False
        assert payload["error"]["code"] == "DB_OPTIONS_INVALID"
        assert payload["error"]["message"].endswith(".")
        assert key in payload["error"]["message"]

    @pytest.mark.parametrize("value", [0, -1, 121, 9999])
    def test_connect_timeout_bounds(self, client: TestClient, settings_db: Any, value: int) -> None:
        resp = client.post("/api/db/manage/options", json={"connect_timeout_sec": value})
        assert resp.json()["error"]["code"] == "DB_OPTIONS_INVALID"
        assert client.get("/api/db/manage/options").json()["options"]["connect_timeout_sec"] == 10

    @pytest.mark.parametrize("value", [1, 120])
    def test_connect_timeout_boundaries_pass(
        self, client: TestClient, settings_db: Any, value: int
    ) -> None:
        resp = client.post("/api/db/manage/options", json={"connect_timeout_sec": value})
        assert resp.json() == {"success": True, "persisted": ["connect_timeout_sec"]}

    @pytest.mark.parametrize("value", [-1, 601])
    def test_command_timeout_bounds(self, client: TestClient, settings_db: Any, value: int) -> None:
        resp = client.post("/api/db/manage/options", json={"command_timeout_sec": value})
        assert resp.json()["error"]["code"] == "DB_OPTIONS_INVALID"

    @pytest.mark.parametrize("value", [0, 600])
    def test_command_timeout_boundaries_pass(
        self, client: TestClient, settings_db: Any, value: int
    ) -> None:
        resp = client.post("/api/db/manage/options", json={"command_timeout_sec": value})
        assert resp.json() == {"success": True, "persisted": ["command_timeout_sec"]}

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("migrate_on_startup", "yes"),
            ("migrate_on_startup", 1),
            ("pooling_enabled", "true"),
        ],
    )
    def test_booleans_must_be_booleans(
        self, client: TestClient, settings_db: Any, key: str, value: Any
    ) -> None:
        resp = client.post("/api/db/manage/options", json={key: value})
        assert resp.json()["error"]["code"] == "DB_OPTIONS_INVALID"


class TestAdditivePersistence:
    def _seed_existing_row(self, settings_db: Any) -> dict[str, Any]:
        existing = {
            "provider": "postgresql",
            "domain": "audit",
            "host": "prod-db.internal",
            "port": 5433,
            "database": "nse_audit",
            "username": "ops",
            "ssl_mode": "verify-full",
            "password_secret": PG_PASSWORD_SECRET_KEY,
        }
        settings_db.set(
            PG_CONFIG_SETTING_KEY,
            existing,
            value_type="json",
            source="USER_SETTINGS",
            actor="web",
        )
        return existing

    def test_existing_keys_survive_an_options_write(
        self, client: TestClient, settings_db: Any
    ) -> None:
        existing = self._seed_existing_row(settings_db)
        resp = client.post("/api/db/manage/options", json={"command_timeout_sec": 45})
        assert resp.json()["persisted"] == ["command_timeout_sec"]

        row = settings_db.get(PG_CONFIG_SETTING_KEY)
        assert row is not None
        stored = row.value if isinstance(row.value, dict) else json.loads(str(row.value))
        for key, value in existing.items():
            assert stored[key] == value, f"additive write clobbered {key}"
        assert stored["command_timeout_sec"] == 45

    def test_password_secret_reference_is_never_replaced_by_plaintext(
        self, client: TestClient, settings_db: Any, secret_writes: list[tuple[str, str]]
    ) -> None:
        self._seed_existing_row(settings_db)
        client.post("/api/db/manage/options", json={"pooling_enabled": False})

        row = settings_db.get(PG_CONFIG_SETTING_KEY)
        assert row is not None
        stored = row.value if isinstance(row.value, dict) else json.loads(str(row.value))
        assert stored["password_secret"] == PG_PASSWORD_SECRET_KEY
        assert "password" not in stored
        assert FAKE_PASSWORD not in json.dumps(stored)
        assert secret_writes == []  # options never touches the secret store

    def test_row_without_reference_gains_the_reference_not_a_value(
        self, client: TestClient, settings_db: Any
    ) -> None:
        client.post("/api/db/manage/options", json={"connect_timeout_sec": 15})
        row = settings_db.get(PG_CONFIG_SETTING_KEY)
        assert row is not None
        stored = row.value if isinstance(row.value, dict) else json.loads(str(row.value))
        assert stored["password_secret"] == PG_PASSWORD_SECRET_KEY
        assert "password" not in stored
        assert stored["provider"] == "postgresql"
        assert stored["domain"] == "audit"

    def test_options_row_is_stored_as_json_user_settings_web(
        self, client: TestClient, settings_db: Any
    ) -> None:
        client.post("/api/db/manage/options", json={"command_timeout_sec": 5})
        row = settings_db.get(PG_CONFIG_SETTING_KEY)
        assert row is not None
        assert row.value_type == "json"
        assert row.source == "USER_SETTINGS"


class TestManageStatusExtension:
    # Keys that existed before this wave — names AND shapes must survive.
    _PRE_EXISTING_KEYS = (
        "success",
        "provider",
        "supported_providers",
        "overall",
        "domains",
        "postgres",
        "password_set",
        "postgresql_driver_available",
        "hints",
        "provider_truth",
    )

    def test_pre_existing_keys_keep_their_names_and_shapes(
        self, client: TestClient, settings_db: Any
    ) -> None:
        payload = client.get("/api/db/manage/status").json()
        for key in self._PRE_EXISTING_KEYS:
            assert key in payload, f"status key {key} disappeared"
        assert isinstance(payload["success"], bool)
        assert isinstance(payload["domains"], dict)  # health snapshot, unchanged
        assert isinstance(payload["hints"], list)
        assert isinstance(payload["provider_truth"], dict)
        assert isinstance(payload["password_set"], bool)

    def test_status_gains_the_advanced_options_block(
        self, client: TestClient, settings_db: Any
    ) -> None:
        client.post("/api/db/manage/options", json={"command_timeout_sec": 45})
        payload = client.get("/api/db/manage/status").json()
        options = payload["options"]
        assert options == {
            "command_timeout_sec": 45,
            "connect_timeout_sec": 10,
            "migrate_on_startup": True,
            "pooling_enabled": True,
        }

    def test_status_gains_the_known_domain_database_names(
        self, client: TestClient, settings_db: Any
    ) -> None:
        from nexus_scalp.database.provider import DEFAULT_DB_FILES

        payload = client.get("/api/db/manage/status").json()
        assert payload["domain_db_names"] == sorted(DEFAULT_DB_FILES)
        assert "audit" in payload["domain_db_names"]

    def test_status_never_echoes_a_password(
        self, client: TestClient, settings_db: Any, secret_writes: list[tuple[str, str]]
    ) -> None:
        client.post("/api/db/manage/parse-url", json={"url": _url(password=FAKE_PASSWORD)})
        resp = client.get("/api/db/manage/status")
        _assert_no_password(resp.text)
        # only the SecretStore ever saw it, under the documented key
        assert [name for name, _ in secret_writes] == [PG_PASSWORD_SECRET_KEY]


class TestRouterMount:
    def test_new_routes_are_mounted_under_the_manage_prefix(self, client: TestClient) -> None:
        # FastAPI 0.141 defers included routers: app.routes holds a
        # _IncludedRouter node per include, so resolve through the router the
        # way the ASGI app itself does.
        assert client.app.url_path_for("parse_url") == "/api/db/manage/parse-url"
        assert client.app.url_path_for("get_options") == "/api/db/manage/options"
        assert client.app.url_path_for("post_options") == "/api/db/manage/options"

    def test_routes_are_reachable_through_the_mounted_app(
        self, client: TestClient, settings_db: Any
    ) -> None:
        assert (
            client.post("/api/db/manage/parse-url", json={"url": "mysql://x/y"}).json()["error"][
                "code"
            ]
            == "DB_URL_PARSE_FAILED"
        )
        assert client.get("/api/db/manage/options").json()["success"] is True

    def test_router_prefix_is_the_manage_prefix(self) -> None:
        assert provider_routes.router.prefix == "/api/db/manage"
