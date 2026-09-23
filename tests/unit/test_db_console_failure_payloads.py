"""Failure-payload contract for the Database console (`web/db_console.py`).

Regression pins for the 2026-09-23 fix: the console used to answer every
driver failure with the bare exception TYPE name ("RuntimeError") while the
actionable sentence the driver raised was discarded — with provider=postgresql
and psycopg absent the whole explorer (tables/rows/columns/quick/query)
reported the single word "RuntimeError" and gave the operator nothing to act
on.

Pinned here:
  * known conditions map to a stable `code` + an actionable `hint`;
  * exception TEXT is never echoed (CodeQL py/stack-trace-exposure);
  * `error` stays a human sentence (the UI renders it verbatim);
  * `truncated` is true only when rows were actually withheld.
"""

from __future__ import annotations

from typing import Any

import pytest

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.web import db_console
from nexus_scalp.web.db_console import QUERY_LIMIT, _connection_hint, _fail

PG_GUARD = (
    "PostgreSQL support is not installed. Run: "
    "pip install 'nexus[postgres]'  (psycopg[binary]==3.2.*)"
)


class TestFailPayload:
    def test_missing_psycopg_is_actionable_not_a_type_name(self) -> None:
        out = _fail(RuntimeError(PG_GUARD), "reading rows from 'audit'")
        assert out["success"] is False
        assert out["code"] == "PG_DRIVER_MISSING"
        assert "psycopg is not installed" in out["error"]
        assert "nexus[postgres]" in out["hint"]
        # the old contract: the bare type name was the whole answer
        assert out["error"] != "RuntimeError"
        assert not out["error"].startswith("RuntimeError")

    def test_connection_failure_names_the_target_and_the_next_action(self) -> None:
        pg_error = type("OperationalError", (Exception,), {"__module__": "psycopg"})
        cfg = DatabaseConfig.for_postgres(
            domain="audit", host="db.internal", port=5433, database="nse_audit", username="nse"
        )
        out = _fail(pg_error("connection to server failed"), "listing tables", cfg)
        assert out["success"] is False
        assert out["code"] == "DB_UNREACHABLE"
        assert "postgresql://db.internal:5433/nse_audit" in out["error"]
        assert isinstance(out.get("hint"), str) and out["hint"]

    def test_unknown_failure_never_echoes_exception_text(self) -> None:
        secret = "C:\\Users\\operator\\secret\\password=hunter2"
        out = _fail(ValueError(secret), "listing tables")
        assert out["success"] is False
        assert out["code"] == "DB_CONSOLE_ERROR"
        assert "hunter2" not in out["error"]
        assert "ValueError" in out["error"]  # type name only, for the log join
        assert "server log" in out["error"]

    def test_connection_hint_is_none_for_sqlite(self) -> None:
        assert _connection_hint(None) is None
        assert _connection_hint(DatabaseConfig.for_sqlite("audit")) is None


class TestDatabaseListHints:
    def test_unreachable_postgres_entries_carry_a_hint(self) -> None:
        entries = {d["name"]: d for d in db_console._list_databases()}
        assert {"audit", "news", "candle_intel", "settings"} <= set(entries)
        for entry in entries.values():
            if entry["provider"] != "postgresql" or entry["status"] == "CONNECTED":
                continue
            assert "hint" in entry, f"{entry['name']} is {entry['status']} without guidance"
            assert isinstance(entry["hint"], str) and entry["hint"]


class _StubDriver:
    """Driver double: returns exactly N rows, never touches a real database."""

    def __init__(self, rows: int) -> None:
        self._rows = [{f"c{i}": i for i in range(4)} for _ in range(rows)]

    def query_readonly(self, sql: str, args: Any = (), conn: Any = None) -> list[dict[str, Any]]:
        return list(self._rows)

    def query(self, sql: str, args: Any = (), conn: Any = None) -> list[dict[str, Any]]:
        return list(self._rows)

    def close(self) -> None:
        return None


class TestTruncationFlag:
    @pytest.mark.parametrize(
        ("rows", "expected"),
        [(QUERY_LIMIT - 1, False), (QUERY_LIMIT, False), (QUERY_LIMIT + 1, True)],
    )
    def test_truncated_only_when_rows_were_withheld(
        self, monkeypatch: pytest.MonkeyPatch, rows: int, expected: bool
    ) -> None:
        # The old endpoint tested `len(rows) >= QUERY_LIMIT` AFTER slicing to
        # the cap, so an exactly-cap-sized result set claimed truncation.
        monkeypatch.setattr(
            db_console, "_driver_for", lambda name, cors=True: (_StubDriver(rows), None)
        )
        out = db_console.console_query({"database": "audit", "sql": "SELECT 1"})
        assert out["success"] is True
        assert out["truncated"] is expected
        assert len(out["rows"]) == min(rows, QUERY_LIMIT)
        assert out["rows_returned"] == len(out["rows"])

    def test_read_only_still_refuses_writes(self) -> None:
        out = db_console.console_query({"database": "audit", "sql": "DELETE FROM audit_signals"})
        assert out["success"] is False
        assert "read-only" in out["error"]
