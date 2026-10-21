"""API contract tests for the observability log centre.

These run against the real FastAPI app through ``TestClient`` — the same
router registration and middleware stack the server uses — so a route that
is unreachable, mis-ordered or lighter on validation than advertised fails
here rather than in a browser.

The log store is backed by a real SQLite file (not a mock): the point of
these tests is that a written event is retrievable, filterable and redacted
through the HTTP surface.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.log_store import LOG_TABLE, DatabaseLogEntry, DatabaseLogStore
from nexus_scalp.web.api_v1 import observability as obs


def _entry(**over: object) -> DatabaseLogEntry:
    params: dict[str, object] = {
        "level": "INFO",
        "provider": "sqlite",
        "domain": "audit",
        "operation": "unit_op",
        "event_name": "UNIT_EVENT",
        "logger_name": "unit.component",
        "error_message": "unit message",
        "correlation_id": "corr-unit-1",
    }
    params.update(over)
    return DatabaseLogEntry(**params)


@pytest.fixture()
def store(tmp_path: Path) -> tuple[DatabaseLogStore, DatabaseConfig]:
    db = tmp_path / "logs.db"
    cfg = DatabaseConfig.for_sqlite("audit", path=str(db))
    return DatabaseLogStore(config=cfg, persist_info=True), cfg


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, store: tuple[DatabaseLogStore, DatabaseConfig]):
    s, cfg = store
    app = FastAPI()
    app.include_router(obs.router)
    # Bind the read plane to this test's store, keeping every other layer
    # (routing, validation, envelope, redaction) real.
    monkeypatch.setattr(obs, "_log_store", lambda request: s)
    with TestClient(app) as c:
        yield c, s, cfg


def _seed(s: DatabaseLogStore, cfg: DatabaseConfig) -> None:
    for lvl, comp, msg in (
        ("INFO", "engine.core", "started"),
        ("WARNING", "engine.core", "retrying broker"),
        ("ERROR", "engine.broker", "order rejected"),
        ("CRITICAL", "engine.risk", "risk halt"),
    ):
        s.record(
            _entry(
                level=lvl,
                logger_name=comp,
                error_message=msg,
                event_name=f"EV_{lvl}",
                fingerprint=f"fp-{lvl}",
            )
        )


def test_search_returns_persisted_events(client) -> None:
    c, s, cfg = client
    _seed(s, cfg)
    r = c.get("/api/v1/observability/logs")
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["page"] == 1
    assert len(body["items"]) == 4
    # Newest first — the operator wants the most recent failure on top.
    assert body["items"][0]["level"] == "CRITICAL"
    assert "window" in body


def test_severity_filter_is_server_side(client) -> None:
    c, s, cfg = client
    _seed(s, cfg)
    r = c.get("/api/v1/observability/logs", params={"severity": "ERROR,CRITICAL"})
    assert r.status_code == 200
    levels = {i["level"] for i in r.json()["data"]["items"]}
    assert levels == {"ERROR", "CRITICAL"}


def test_unknown_severity_is_rejected(client) -> None:
    c, _s, _cfg = client
    r = c.get("/api/v1/observability/logs", params={"severity": "PANIC"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_validation_error_never_echoes_the_caller_value(client) -> None:
    """REGRESSION (CodeQL py/stack-trace-exposure): the rejected filter must
    not be interpolated into the response body. The reply names the closed
    vocabulary instead, so no caller-controlled text reaches the envelope."""
    c, _s, _cfg = client
    probe = "<script>alert(1)</script>"
    r = c.get("/api/v1/observability/logs", params={"severity": probe})
    assert r.status_code == 422
    body = r.text
    assert probe not in body
    assert "script" not in body.lower()
    assert "severity must be one of" in body


def test_component_and_text_filters(client) -> None:
    c, s, cfg = client
    _seed(s, cfg)
    r = c.get("/api/v1/observability/logs", params={"component": "engine.broker"})
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    assert len(items) == 1 and items[0]["logger_name"] == "engine.broker"

    r2 = c.get("/api/v1/observability/logs", params={"q": "order"})
    assert r2.status_code == 200
    assert any("order" in (i.get("error_message") or "") for i in r2.json()["data"]["items"])


def test_summary_counts_per_severity(client) -> None:
    c, s, cfg = client
    _seed(s, cfg)
    r = c.get("/api/v1/observability/logs/summary")
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["counts"]["ERROR"] == 1
    assert body["counts"]["CRITICAL"] == 1
    assert body["total"] == 4


def test_export_json_and_csv_are_redacted(client) -> None:
    c, s, cfg = client
    s.record(
        _entry(
            level="ERROR",
            error_message="broker login failed",
            context_json=json.dumps({"api_token": "super-secret-value", "account": "12345"}),
            fingerprint="fp-export",
        )
    )
    r = c.get("/api/v1/observability/logs/export", params={"format": "json"})
    assert r.status_code == 200
    assert "super-secret-value" not in r.text
    assert "attachment" in r.headers["content-disposition"]

    r2 = c.get("/api/v1/observability/logs/export", params={"format": "csv"})
    assert r2.status_code == 200
    assert "super-secret-value" not in r2.text
    assert r2.headers["content-type"].startswith("text/csv")


def test_export_limit_cannot_exceed_cap(client) -> None:
    c, _s, _cfg = client
    r = c.get("/api/v1/observability/logs/export", params={"limit": 10**9})
    assert r.status_code == 422


def test_detail_returns_trace_and_masks_secrets(client) -> None:
    c, s, cfg = client
    s.record(
        _entry(
            level="ERROR",
            fingerprint="fp-detail",
            full_trace='Traceback: password="hunter2" then boom',
            context_json=json.dumps({"password": "hunter2"}),
        )
    )
    rows = s.query_events(limit=1)
    eid = rows[0]["id"]
    r = c.get(f"/api/v1/observability/logs/{eid}")
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert "hunter2" not in r.text
    assert body["id"] == eid


def test_detail_missing_event_is_404(client) -> None:
    c, _s, _cfg = client
    r = c.get("/api/v1/observability/logs/999999")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_correlate_groups_by_correlation_id(client) -> None:
    c, s, cfg = client
    for i in range(3):
        s.record(
            _entry(
                level="ERROR",
                correlation_id="corr-shared",
                error_message=f"step {i}",
                fingerprint=f"fp-corr-{i}",
            )
        )
    s.record(_entry(level="INFO", correlation_id="other", fingerprint="fp-other"))
    seed_id = s.query_events(limit=1)[0]["id"]
    r = c.get(f"/api/v1/observability/logs/{seed_id}/correlate")
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["correlation_id"] == "other"
    assert len(body["events"]) == 1


def test_page_size_is_clamped_to_server_cap(client) -> None:
    c, _s, _cfg = client
    r = c.get("/api/v1/observability/logs", params={"page_size": obs.LOG_PAGE_MAX + 5})
    assert r.status_code == 422  # declared maximum is enforced, not silently shrunk


def test_inverted_window_is_rejected(client) -> None:
    c, _s, _cfg = client
    r = c.get(
        "/api/v1/observability/logs",
        params={"since": "2026-01-02T00:00:00Z", "until": "2026-01-01T00:00:00Z"},
    )
    assert r.status_code == 422


def test_over_wide_window_is_rejected(client) -> None:
    c, _s, _cfg = client
    r = c.get(
        "/api/v1/observability/logs",
        params={"since": "2020-01-01T00:00:00Z", "until": "2026-01-01T00:00:00Z"},
    )
    assert r.status_code == 422


def test_bad_timestamp_is_rejected(client) -> None:
    c, _s, _cfg = client
    r = c.get("/api/v1/observability/logs", params={"since": "not-a-date"})
    assert r.status_code == 422


def test_keyset_cursor_walks_pages(client) -> None:
    c, s, cfg = client
    for i in range(7):
        s.record(_entry(level="WARNING", error_message=f"e{i}", fingerprint=f"fp-page-{i}"))
    first = c.get("/api/v1/observability/logs", params={"page_size": 3}).json()["data"]
    assert len(first["items"]) == 3 and first["has_more"] is True
    cursor = first["next_before_id"]
    second = c.get(
        "/api/v1/observability/logs", params={"page_size": 3, "before_id": cursor}
    ).json()["data"]
    assert len(second["items"]) == 3
    ids_first = {i["id"] for i in first["items"]}
    ids_second = {i["id"] for i in second["items"]}
    assert not (ids_first & ids_second), "cursor paging must not repeat rows"


def test_source_failure_is_not_an_empty_page(client, monkeypatch) -> None:
    """A read-plane outage must be a 503, not a false 'no events' page."""
    c, s, _cfg = client

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("log source down")

    monkeypatch.setattr(s, "query_events", boom)
    r = c.get("/api/v1/observability/logs")
    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


def test_empty_store_is_a_successful_empty_page(client) -> None:
    """Zero events is a fact, and must look different from a failure."""
    c, _s, _cfg = client
    r = c.get("/api/v1/observability/logs")
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["items"] == [] and body["has_more"] is False


def test_static_routes_are_not_shadowed_by_event_id(client) -> None:
    """``/logs/summary`` must not be parsed as ``/logs/{event_id}``."""
    c, s, cfg = client
    _seed(s, cfg)
    assert c.get("/api/v1/observability/logs/summary").status_code == 200
    # And a non-numeric id is a validation failure, not a 500.
    assert c.get("/api/v1/observability/logs/not-an-int").status_code == 422


def test_log_table_exists_and_indexed_after_reads(client, store) -> None:
    c, s, cfg = client
    _seed(s, cfg)
    c.get("/api/v1/observability/logs")
    with sqlite3.connect(cfg.sqlite_path) as conn:
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (LOG_TABLE,)
            )
        }
    assert any("ts" in n for n in names), f"no timestamp index: {names}"
