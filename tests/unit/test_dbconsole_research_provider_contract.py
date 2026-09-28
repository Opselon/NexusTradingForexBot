"""Regression suite for the DB-console / research-observability defect pair.

Two INDEPENDENT failures were reported live and are pinned here:

1. ``/api/db/console/query`` with a form-encoded (or raw-bytes) body raised
   ``RequestValidationError``, and the VALIDATION ERROR HANDLER then crashed
   serializing the raw body bytes — ``TypeError: Object of type bytes is not
   JSON serializable`` — turning a clean 4xx client error into an HTTP 500
   with an empty body and an ASGI traceback.

2. ``[RESEARCH_OBS] heatmap failed`` / ``family analytics failed`` with
   ``error='unable to open database file'``: ``research/observability.py``
   opens the audit store through a module-level ``_connect()`` that assumed a
   SQLite file, while ``AuditRepository._db_path`` holds the PROVIDER URI
   (``postgresql://...``) under a non-SQLite provider. The tables exist in
   BOTH planes, so the read must follow the configured provider.

The contract this file defends:

    malformed request  ->  4xx + valid JSON + NO secondary exception
    database failure   ->  available: False + error   (never empty data)
    empty result       ->  available: True  + empty aggregates
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.research.observability import (
    ResearchObservabilityStore,
    _reader,
)
from nexus_scalp.web.api_v1.errors import (
    _BINARY_BODY_PLACEHOLDER,
    _json_safe,
    register_v1_exception_handlers,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def console_app() -> TestClient:
    """The real DB-console router + the v1 exception handlers (as wired live)."""
    from nexus_scalp.web.db_console import router as db_console_router

    app = FastAPI()
    app.include_router(db_console_router)
    register_v1_exception_handlers(app)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def sqlite_repo(tmp_path: Path) -> AuditRepository:
    """A real SQLite audit repository with the research schema created."""
    db_file = tmp_path / "audit.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")
    handle = repo._connect_sqlite(5.0)
    try:
        repo._create_sqlite_tables(handle)
    finally:
        handle.close()
    return repo


def _seed_gate(conn: sqlite3.Connection, gate_id: str, gate_type: str, status: str) -> None:
    conn.execute(
        "INSERT INTO research_gates (gate_id,strategy_id,research_run_id,gate_type,status,"
        "started_at,completed_at,configuration_version,dataset_version,engine_version,"
        "result,failure_reason,failure_class,retryable,order_index) "
        "VALUES (?, 'S1', 'R1', ?, ?, '', '', '', '', '', '{}', 'ci_below', '', 0, 0)",
        (gate_id, gate_type, status),
    )


def _non_sqlite_repo_without_pool() -> AuditRepository:
    """A non-SQLite repository that never touches a real server.

    The established test shape (``test_provider_read_guard_observability_chg0067.py``,
    ``test_audit_flush_contract.py``): ``AuditRepository.__new__`` plus the
    seams the read path reads. Constructing the class normally against a
    bogus PostgreSQL URL would BOOTSTRAP the fabric's pooled read plane from
    the box's resolved DSN, leaking global pool state into every later suite
    in the same process.
    """
    repo = AuditRepository.__new__(AuditRepository)
    repo._is_sqlite = False
    repo._db_url = "postgresql://127.0.0.1:1/nonexistent"
    repo._db_path = "postgresql://127.0.0.1:1/nonexistent"
    return repo


# ===========================================================================
# Defect 1 — the validation error handler must never crash on bytes
# ===========================================================================
class TestValidationHandlerBytesSafety:
    """A malformed request stays a client error, never an ASGI crash."""

    # The exact body from the live incident (application/x-www-form-urlencoded).
    FORM_BODY = b"sql=SELECT+count%28%2A%29+FROM+strategy_registry&database=audit"

    def test_form_encoded_body_is_4xx_with_valid_json(self, console_app: TestClient) -> None:
        res = console_app.post(
            "/api/db/console/query",
            content=self.FORM_BODY,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        # Was 500 + empty body while the handler died on the bytes.
        assert 400 <= res.status_code < 500, res.text
        payload = res.json()  # must be parseable — this is the regression
        assert isinstance(payload["detail"], list)
        assert payload["detail"], "the 4xx must explain the failure"

    def test_raw_binary_body_is_4xx_with_valid_json(self, console_app: TestClient) -> None:
        res = console_app.post(
            "/api/db/console/query",
            content=b"\x00\x01\x02binary garbage",
            headers={"Content-Type": "application/octet-stream"},
        )
        assert 400 <= res.status_code < 500, res.text
        assert res.json()["detail"]

    @pytest.mark.parametrize(
        ("content", "headers", "expected_status"),
        [
            (b"{not json", {"Content-Type": "application/json"}, 422),
            (b"[1,2,3]", {"Content-Type": "application/json"}, 422),
            (b'{"database": "audit"}', {"Content-Type": "application/json"}, 200),
            (b'{"sql": 12345, "database": "audit"}', {"Content-Type": "application/json"}, 200),
            (b"", {"Content-Type": "application/json"}, 422),
        ],
        ids=["malformed-json", "json-list", "missing-field", "wrong-type", "empty-body"],
    )
    def test_every_malformed_body_shape_returns_clean_json(
        self, console_app: TestClient, content: bytes, headers: dict[str, str], expected_status: int
    ) -> None:
        """Every body shape answers with parseable JSON and no ASGI exception.

        The endpoint takes a ``dict`` payload, so a JSON body that parses as a
        dict reaches the handler even when ``sql`` is absent/typed wrong — it
        answers ``200 success:false`` from the SQL guard, which is the
        endpoint's documented contract (the guard never lets bad SQL reach the
        DB). The regression this pins is the RESPONSE, not the status: none of
        these may be a 500 with an empty body.
        """
        res = console_app.post("/api/db/console/query", content=content, headers=headers)
        assert res.status_code == expected_status, res.text
        payload = json.loads(res.text)
        if res.status_code == 200:
            assert payload["success"] is False
        else:
            assert payload["detail"]

    def test_no_raw_binary_is_ever_echoed(self, console_app: TestClient) -> None:
        """Raw bytes must be redacted, never returned to the client."""
        marker = b"SUPERSECRETBYTES"
        res = console_app.post(
            "/api/db/console/query",
            content=marker,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert marker.decode() not in res.text
        assert _BINARY_BODY_PLACEHOLDER in res.text

    def test_v1_envelope_contract_is_unchanged(self) -> None:
        """The /api/v1 branch keeps its envelope — only the legacy body changed."""
        app = FastAPI()

        @app.post("/api/v1/probe")
        def probe(payload: dict) -> dict:  # pragma: no cover - never reached
            return payload

        register_v1_exception_handlers(app)
        client = TestClient(app, raise_server_exceptions=False)
        res = client.post(
            "/api/v1/probe",
            content=self.FORM_BODY,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"


class TestJsonSafeCoercion:
    """The coercer itself: nested/odd values must not reach the JSON encoder."""

    def test_bytes_become_the_placeholder(self) -> None:
        assert _json_safe(b"raw") == _BINARY_BODY_PLACEHOLDER

    def test_nested_containers_are_coerced(self) -> None:
        coerced = _json_safe({"a": [b"x", (b"y",)], "b": {b"k": b"v"}})
        json.dumps(coerced)  # must not raise
        assert coerced["a"] == [_BINARY_BODY_PLACEHOLDER, [_BINARY_BODY_PLACEHOLDER]]

    def test_unknown_object_becomes_the_placeholder(self) -> None:
        json.dumps(_json_safe(object()))  # must not raise

    def test_scalars_pass_through(self) -> None:
        assert _json_safe("s") == "s"
        assert _json_safe(7) == 7
        assert _json_safe(None) is None


# ===========================================================================
# Defect 2 — research observability under a non-SQLite provider
# ===========================================================================
class TestProviderPortableReader:
    """The shipped reader follows the configured provider."""

    def test_reader_reads_a_sqlite_repo(self, sqlite_repo: AuditRepository) -> None:
        reader = _reader(sqlite_repo)
        assert reader.available is True
        assert reader.rows("SELECT count(*) AS c FROM research_gates") == [{"c": 0}]

    def test_reader_reports_unavailable_when_no_plane_is_registered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pooled repo with NO resolvable plane must be UNAVAILABLE, not empty.

        _ProviderRead resolves the registered fabric read plane; when none
        exists and it cannot bootstrap one, available is False and the
        readers raise, so heatmap / family_analytics mark the read as failed
        rather than returning an empty-shaped success.
        """
        repo = _non_sqlite_repo_without_pool()
        monkeypatch.setattr(
            AuditRepository, "research_read_plane", lambda self: None
        )
        store = ResearchObservabilityStore(audit_repo=repo)
        heatmap = store.gate_failure_heatmap()
        families = store.family_analytics()
        assert heatmap["available"] is False
        assert families["available"] is False
        assert heatmap["error"] and families["error"]


class TestResearchObservabilityProviderContract:
    """Empty data and a database failure are distinct outcomes."""

    def test_empty_sqlite_database_is_available_with_empty_aggregates(
        self, sqlite_repo: AuditRepository
    ) -> None:
        store = ResearchObservabilityStore(audit_repo=sqlite_repo)
        heatmap = store.gate_failure_heatmap()
        families = store.family_analytics()
        assert heatmap["available"] is True
        assert heatmap["by_gate"] == {}
        assert families["available"] is True
        assert families["families"] == {}

    def test_heatmap_reads_real_rows_on_sqlite(self, sqlite_repo: AuditRepository) -> None:
        conn = sqlite_repo._connect_sqlite(5.0)
        try:
            _seed_gate(conn, "G1", "OOS", "FAILED")
            _seed_gate(conn, "G2", "OOS", "FAILED")
            _seed_gate(conn, "G3", "BACKTEST", "ERROR")
            conn.commit()
        finally:
            conn.close()
        heatmap = ResearchObservabilityStore(audit_repo=sqlite_repo).gate_failure_heatmap()
        assert heatmap["available"] is True
        assert heatmap["by_gate"] == {"OOS": 2, "BACKTEST": 1}
        assert heatmap["total_failures"] == 3

    def test_family_analytics_reads_real_rows_on_sqlite(self, sqlite_repo: AuditRepository) -> None:
        conn = sqlite_repo._connect_sqlite(5.0)
        try:
            conn.execute(
                "INSERT INTO strategy_registry (strategy_id,strategy_version,lifecycle,"
                "context_definition,score,created_at,updated_at) VALUES "
                "('S1','v1','VALIDATED','{\"fingerprint\":\"MOMENTUM|M30\"}',"
                "'{\"final_score\":0.8}','','')"
            )
            conn.execute(
                "INSERT INTO strategy_registry (strategy_id,strategy_version,lifecycle,"
                "context_definition,score,created_at,updated_at) VALUES "
                "('S2','v1','REJECTED','{\"fingerprint\":\"BREAKOUT|H1\"}',"
                "'{\"final_score\":0.2}','','')"
            )
            conn.commit()
        finally:
            conn.close()
        families = ResearchObservabilityStore(audit_repo=sqlite_repo).family_analytics()
        assert families["available"] is True
        assert families["families"]["MOMENTUM|M30"]["validated"] == 1
        assert families["families"]["MOMENTUM|M30"]["pass_rate"] == 1.0
        assert families["families"]["BREAKOUT|H1"]["rejected"] == 1


class TestDbConsoleQueryOnSqlite:
    """The console's valid path keeps working end to end."""

    def test_valid_query_returns_a_real_result(
        self, console_app: TestClient, sqlite_repo: AuditRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Point the console's domain resolver at this test's scratch SQLite
        # file so the query exercises the real driver + read guard, not a stub.
        from nexus_scalp.database.config import DatabaseConfig

        monkeypatch.setattr(
            "nexus_scalp.web.db_console._config_for",
            lambda name: DatabaseConfig.for_sqlite(name, path=str(sqlite_repo._db_path))
            if name == "audit"
            else None,
        )
        res = console_app.post(
            "/api/db/console/query",
            json={"sql": "SELECT count(*) AS row_count FROM strategy_registry", "database": "audit"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert body["rows"] == [{"row_count": 0}]
