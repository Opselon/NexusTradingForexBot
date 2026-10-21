"""OBS-001 / OBS-003 / OBS-015 regression gates.

These pin the three gaps the 2026-08-31 observability audit opened and that
survived until now, each with a RUNTIME assertion rather than a source grep:

  * **OBS-001** — ``/api/debug/trace/{execution_id}`` resolved its database
    with a hardcoded ``<cwd>/artifacts/audit.db``, ignoring the
    ``NEXUS_AUDIT_DB`` isolation seam and the persisted provider. A probe
    pinned to its own audit database was read by nobody, and on PostgreSQL
    the route opened a SQLite file holding stale data — while ``/health``
    reported READY. It also crashed with ``No module named
    nexus_scalp.adapters.audit_db`` at the audited commit.
  * **OBS-003** — ``structlog.contextvars.merge_contextvars`` was installed
    but nothing ever bound a contextvar: zero correlation ids per day, no
    boot id, ``correlation_id="-"`` placeholders.
  * **OBS-015** — ``X-Request-ID`` survived header -> response but never
    reached a log line, so a client-reported id could not be joined to the
    record explaining it.

The tests deliberately do NOT assert on log file contents (that would couple
them to rotation/formatting): they capture what the real configured pipeline
emits and assert on the parsed fields.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus_scalp.observability.correlation import (
    bind_boot_id,
    bind_request_scope,
    clear_request_scope,
    current_boot_id,
    current_request_id,
    new_boot_id,
    request_scope,
)
from nexus_scalp.observability.logging import configure_logging
from nexus_scalp.web.audit_read import (
    AuditReadError,
    audit_database_label,
    audit_table_columns,
    query_audit_rows,
)
from nexus_scalp.web.debug_research_routes import register_debug_research_routes
from nexus_scalp.web.errors import attach_request_id_middleware, safe_error_payload

EXEC_ID = "EXEC-20260831-192600-abc123"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _build_audit_db(
    path: Path,
    *,
    with_execution_id: bool = True,
    rows: int = 1,
) -> Path:
    """A minimal audit database shaped like the live one."""
    con = sqlite3.connect(path)
    try:
        if with_execution_id:
            con.execute(
                "CREATE TABLE audit_signals (id INTEGER PRIMARY KEY, "
                "execution_id TEXT, generated_at TEXT, reason_code TEXT)"
            )
            for i in range(rows):
                con.execute(
                    "INSERT INTO audit_signals (execution_id, generated_at, reason_code) "
                    "VALUES (?,?,?)",
                    (EXEC_ID, f"2026-08-31T19:26:0{i}", "ALLOW"),
                )
        else:
            # Schema generation that predates the trace-chain instrumentation.
            con.execute(
                "CREATE TABLE audit_signals (id INTEGER PRIMARY KEY, "
                "generated_at TEXT, reason_code TEXT)"
            )
            con.execute(
                "INSERT INTO audit_signals (generated_at, reason_code) VALUES (?,?)",
                ("2026-08-31T19:26:00", "ALLOW"),
            )
        con.execute(
            "CREATE TABLE audit_orders (id INTEGER PRIMARY KEY, reason TEXT, timestamp TEXT)"
        )
        con.execute(
            "INSERT INTO audit_orders (reason, timestamp) VALUES (?,?)",
            (f"{EXEC_ID} dispatch", "2026-08-31T19:26:01"),
        )
        con.commit()
    finally:
        con.close()
    return path


@pytest.fixture
def audit_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the audit isolation seam at a scratch database."""
    db = _build_audit_db(tmp_path / "audit.db")
    monkeypatch.setenv("NEXUS_AUDIT_DB", str(db))
    monkeypatch.delenv("NSE_DATABASE__SQLITE_PATH", raising=False)
    yield db


@pytest.fixture
def trace_client() -> TestClient:
    """The REAL debug trace route, registered exactly as server.py does."""
    app = FastAPI()

    def _err(code: str = "INTERNAL_ERROR", **kw: object) -> dict[str, object]:
        return safe_error_payload(code=code, request_id="req_test", **kw)

    register_debug_research_routes(app, _err, lambda *a, **k: None, lambda o: o)
    return TestClient(app, raise_server_exceptions=False)


class _LogCapture(logging.Handler):
    """Captures the ACTUAL RENDERED lines of the configured pipeline.

    Capturing ``record.getMessage()`` would miss the contextvars: structlog's
    ``ProcessorFormatter`` merges them while FORMATTING, not in the message.
    The handler therefore borrows the pipeline's own formatter, so what these
    tests assert on is the line an operator would actually read.
    """

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            fmt = self.formatter
            self.lines.append(fmt.format(record) if fmt is not None else record.getMessage())
        except Exception:  # pragma: no cover - defensive
            pass

    def clear(self) -> None:
        self.lines.clear()


@pytest.fixture
def captured() -> Iterator[_LogCapture]:
    """Configure the real pipeline and capture what it emits."""
    configure_logging(log_level="INFO", log_to_file=False)
    root = logging.getLogger()
    handler = _LogCapture()
    # Reuse the pipeline's OWN ProcessorFormatter (the thing that merges
    # contextvars). Selecting "the first handler with a formatter" is wrong:
    # pytest's log-capture plugin is already on the root logger with a plain
    # formatter, which would silently hide every contextvar.
    for existing in root.handlers:
        if isinstance(existing.formatter, structlog.stdlib.ProcessorFormatter):
            handler.setFormatter(existing.formatter)
            break
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        clear_request_scope()


@pytest.fixture(autouse=True)
def _clean_context() -> Iterator[None]:
    """Every test starts and ends with an unbound correlation context."""
    clear_request_scope()
    yield
    clear_request_scope()


# ---------------------------------------------------------------------------
# OBS-001 — the trace route resolves the audit domain the way everything else does
# ---------------------------------------------------------------------------


def test_trace_route_reads_the_seam_database(audit_db: Path, trace_client: TestClient) -> None:
    """The flagship join must read the database the seam names, not a cwd guess."""
    body = trace_client.get(f"/api/debug/trace/{EXEC_ID}").json()
    assert body["available"] is True
    assert body["signal"], "signal half of the join returned nothing"
    assert body["orders"], "order half of the join returned nothing"
    assert body["signal"][0]["execution_id"] == EXEC_ID
    # The label must name the SAME database that answered.
    assert str(audit_db) in str(body["db_path"])


def test_trace_route_survives_a_pre_instrumentation_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trace_client: TestClient
) -> None:
    """A database without audit_signals.execution_id still answers the order half.

    The column only exists after the trace-chain instrumentation, so the route
    schema-guards it. The guard used to read ``PRAGMA table_info`` column 0
    (the cid) and therefore always concluded the column was absent.
    """
    db = _build_audit_db(tmp_path / "old.db", with_execution_id=False)
    monkeypatch.setenv("NEXUS_AUDIT_DB", str(db))
    body = trace_client.get(f"/api/debug/trace/{EXEC_ID}").json()
    assert body["available"] is True
    assert body["orders"], "order half must still answer"
    assert body["signal_execution_id_indexed"] is False


def test_trace_route_reports_a_missing_database_without_leaking_internals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trace_client: TestClient
) -> None:
    """A missing audit DB is a clean, public-safe reason - never a traceback."""
    monkeypatch.setenv("NEXUS_AUDIT_DB", str(tmp_path / "absent.db"))
    body = trace_client.get(f"/api/debug/trace/{EXEC_ID}").json()
    assert body["available"] is False
    reason = str(body["reason"])
    assert "not found" in reason
    for leak in ("OperationalError", "postgresql://", "password", "Traceback"):
        assert leak not in reason


def test_audit_reads_are_bounded(audit_db: Path) -> None:
    """A forensic read can never stream a whole audit table."""
    many = _build_audit_db(audit_db.parent / "many.db", rows=40)
    audit_db.unlink()
    many.rename(audit_db)
    rows = query_audit_rows("SELECT * FROM audit_signals")
    assert len(rows) == 5


def test_audit_reads_refuse_non_select_statements(audit_db: Path) -> None:
    """A diagnostic probe must never be able to mutate audit evidence."""
    for statement in ("DELETE FROM audit_signals", "  update audit_signals set x=1"):
        with pytest.raises(AuditReadError):
            query_audit_rows(statement)


def test_audit_reads_never_create_a_missing_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading a missing database raises; it must not manufacture an empty file."""
    absent = tmp_path / "never.db"
    monkeypatch.setenv("NEXUS_AUDIT_DB", str(absent))
    with pytest.raises(AuditReadError):
        query_audit_rows("SELECT * FROM audit_signals")
    assert not absent.exists()


def test_audit_table_columns_returns_names_not_cids(audit_db: Path) -> None:
    """`PRAGMA table_info` column 0 is the cid; the guard needs the NAME."""
    cols = audit_table_columns("audit_signals")
    assert "execution_id" in cols
    assert all(c.isdigit() is False for c in cols)


def test_audit_database_label_names_the_seam(audit_db: Path) -> None:
    assert str(audit_db) in audit_database_label()


# ---------------------------------------------------------------------------
# OBS-003 / OBS-015 — the request id reaches the log, and only for its request
# ---------------------------------------------------------------------------


def _request_app() -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _corr(request, call_next):  # type: ignore[no-untyped-def]
        return await attach_request_id_middleware(request, call_next)

    @app.get("/probe")
    def probe() -> dict[str, str]:
        structlog.get_logger("nexus_scalp.probe").info("PROBE_RAN")
        logging.getLogger("nexus_scalp.probe").info("STDLIB_PROBE_RAN")
        return {"rid": current_request_id()}

    return TestClient(app)


def test_request_id_reaches_structlog_records(captured: _LogCapture) -> None:
    """OBS-015: the id echoed in the header must appear on the emitted record."""
    client = _request_app()
    captured.clear()
    resp = client.get("/probe", headers={"X-Request-ID": "req_trace_abc123"})
    assert resp.headers["x-request-id"] == "req_trace_abc123"
    joined = "\n".join(captured.lines)
    assert "PROBE_RAN" in joined
    assert "req_trace_abc123" in joined


def test_request_id_reaches_stdlib_records_too(captured: _LogCapture) -> None:
    """The foreign_pre_chain merges the same contextvars for stdlib loggers."""
    client = _request_app()
    captured.clear()
    client.get("/probe", headers={"X-Request-ID": "req_stdlib_zzz9"})
    stdlib_lines = [line for line in captured.lines if "STDLIB_PROBE_RAN" in line]
    assert stdlib_lines, "stdlib record was not captured"
    assert all("req_stdlib_zzz9" in line for line in stdlib_lines)


def test_generated_request_id_is_bound_when_no_header_arrives(
    captured: _LogCapture,
) -> None:
    """A client that sends no id still gets a bound, coherent one."""
    client = _request_app()
    captured.clear()
    resp = client.get("/probe")
    generated = resp.headers["x-request-id"]
    assert generated.startswith("req_")
    assert generated in "\n".join(captured.lines)


def test_request_context_does_not_leak_between_requests(captured: _LogCapture) -> None:
    """A stale id silently attributes work to the wrong request - never leak it."""
    client = _request_app()
    for rid in ("req_first111", "req_second22", "req_third333"):
        captured.clear()
        body = client.get("/probe", headers={"X-Request-ID": rid}).json()
        assert body["rid"] == rid, f"route saw a stale id instead of {rid}"
        stray = [line for line in captured.lines if "req_" in line and rid not in line]
        assert not stray, f"previous request's id leaked into {stray}"
    assert current_request_id() == ""


def test_request_scope_clears_on_exception() -> None:
    """The finally-clear is unconditional, including when the body raises."""
    with pytest.raises(RuntimeError):
        with request_scope("req_boom1234"):
            assert current_request_id() == "req_boom1234"
            raise RuntimeError("boom")
    assert current_request_id() == ""


def test_boot_id_is_bound_once_and_readable() -> None:
    """OBS-003: a process-wide boot id exists at all, and is not a fresh uuid."""
    assert current_boot_id() == ""
    bid = bind_boot_id(pid=4321)
    assert bid.startswith("boot_")
    assert current_boot_id() == bid


def test_boot_id_shape_survives_redaction() -> None:
    """A boot id must never be eaten by the high-entropy secret catcher."""
    from nexus_scalp.observability.logging import _redact_value

    bid = new_boot_id()
    assert bid in _redact_value(f"boot_id={bid}")
    assert "[REDACTED_SECRET]" not in _redact_value(f"boot_id={bid}")


def test_request_scope_binds_extra_fields() -> None:
    """Method/path ride along with the id so a record is self-describing."""
    with request_scope("req_extra9999", http_method="GET", http_path="/api/x"):
        bound = structlog.contextvars.get_contextvars()
    assert bound["request_id"] == "req_extra9999"
    assert bound["http_method"] == "GET"
    assert bound["http_path"] == "/api/x"


def test_bind_request_scope_ignores_empty_values() -> None:
    """An empty id must not bind "" and render as a blank field."""
    bind_request_scope("")
    assert current_request_id() == ""
