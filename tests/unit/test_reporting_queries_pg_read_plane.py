"""PG-ACCT-READ-002 — report stages read through the audit read plane.

Follow-up to PG-ACCT-READ-001 (AccountingCore). Once the core's ``_enabled``
became provider-neutral, ``reporting/queries.py`` still called ``core._connect()``
— a raw ``sqlite3.connect`` — so on PostgreSQL every report stage logged
``[TELEGRAM_REPORT] ... stage failed error='unable to open database file'``.
The CI runtime soak counts those logged errors and fails the run
(`Full NSE Runtime (postgres)`).

The reads must additionally:
- compare ISO ``T``-separated timestamps against space-separated literals
  (``%Y-%m-%d %H:%M:%S``), like ``load_snapshots``;
- join tickets without SQLite temp tables, because the read plane is
  readonly and cannot create temp tables on PostgreSQL.
"""

from __future__ import annotations

from typing import Any

import pytest

from nexus_scalp.accounting.core import AccountingCore
from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.database import fabric as fabric_mod
from nexus_scalp.reporting.queries import (
    FetchResult,
    fetch_anomaly_rows,
    fetch_behavioral_rows,
    fetch_execution_rows,
    fetch_model_rows,
)


class _FakePlane:
    """Captures what the fetchers push down to the store."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.signals = [
            {
                "action": "BUY_MARKET",
                "blocked_by": None,
                "payload": "{}",
                "generated_at": "2026-09-30T08:00:00+00:00",
            },
            {
                "action": "SELL_MARKET",
                "blocked_by": None,
                "payload": "{}",
                "generated_at": "2026-09-30T10:30:00+00:00",
            },
            {
                "action": "BUY_LIMIT",
                "blocked_by": "model",
                "payload": "{}",
                "generated_at": "2026-09-30T12:00:00+00:00",
            },
        ]
        self.orders = [
            {
                "latency": 15.0,
                "reason": "ok",
                "execution_mode": "STANDARD",
                "action": "BUY_MARKET",
                "timestamp": "2026-09-30T10:30:00+00:00",
            },
            {
                "latency": 99.0,
                "reason": "late",
                "execution_mode": "STANDARD",
                "action": "SELL_MARKET",
                "timestamp": "2026-09-30T23:00:00+00:00",
            },
        ]
        self.detections = [
            {
                "behavior_key": "k1",
                "ticket": "100002",
                "pattern": "p",
                "severity": "high",
                "confidence": 0.9,
                "evidence": "{}",
            },
            {
                "behavior_key": "k2",
                "ticket": "100999",
                "pattern": "p",
                "severity": "low",
                "confidence": 0.1,
                "evidence": "{}",
            },
        ]
        self.analysis = [{"ticket": "100002", "note": "x"}, {"ticket": "100999", "note": "y"}]
        self.anomalies = [
            {
                "anomaly_type": "a1",
                "severity": "low",
                "algorithm_version": "1.0",
                "ticket": "100002",
            },
            {
                "anomaly_type": "a2",
                "severity": "high",
                "algorithm_version": "1.0",
                "ticket": "100999",
            },
        ]

    def _in_window(self, row: dict[str, Any], start_s: str, end_s: str) -> bool:
        ts = str(row.get("generated_at") or row.get("timestamp") or "")
        # emulate REPLACE(column, 'T', ' ') + strip +00:00 on the column side
        norm = ts.replace("T", " ").replace("+00:00", "")
        return start_s <= norm < end_s

    def query(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.calls.append((sql, tuple(args)))
        if "audit_signals" in sql:
            return [s for s in self.signals if self._in_window(s, str(args[0]), str(args[1]))]
        if "audit_orders" in sql:
            return [o for o in self.orders if self._in_window(o, str(args[0]), str(args[1]))]
        wanted = {str(a) for a in args}
        if "behavior_detections" in sql:
            return [d for d in self.detections if str(d["ticket"]) in wanted]
        if "behavior_analysis" in sql:
            return [a for a in self.analysis if str(a["ticket"]) in wanted]
        if "anomaly_events" in sql:
            return [e for e in self.anomalies if str(e["ticket"]) in wanted]
        return []

    def query_one(self, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, args)
        return rows[0] if rows else None


@pytest.fixture()
def isolated_fabric_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Private fabric registry: the module-level slot is process-global."""
    monkeypatch.setattr(fabric_mod, "_DOMAIN_BACKENDS", {})


@pytest.fixture()
def pg_core(isolated_fabric_registry: None) -> tuple[AccountingCore, _FakePlane]:
    """A non-SQLite audit repo whose read plane is registered in the fabric.

    Same wiring as tests/unit/test_accounting_core_pg_read_plane.py: the plane
    is registered under (domain='audit', readonly=True) and resolved by the
    repository's own _registered_audit_read_plane(), so the reporting fetchers
    reach it through core._query without a second connection path.
    """
    plane = _FakePlane()
    fabric_mod.register_domain_read_backend("audit", plane)
    repo = AuditRepository.__new__(AuditRepository)
    repo._is_sqlite = False
    repo._db_url = "postgresql://localhost:5432/nexusdb"
    repo._db_path = "postgresql://localhost:5432/nexusdb"
    core = AccountingCore(audit_repo=repo)
    yield core, plane


def test_disabled_core_returns_disabled_not_error(
    isolated_fabric_registry: None,
) -> None:
    """A store with no readable backend reports disabled; never raises, never queries."""
    repo = AuditRepository.__new__(AuditRepository)
    repo._is_sqlite = False
    repo._db_url = "postgresql://localhost:5432/nexusdb"
    repo._db_path = ""
    core = AccountingCore(audit_repo=repo)

    for fn in (fetch_model_rows, fetch_execution_rows):
        res = fn(core, "2026-09-30 00:00:00", "2026-10-01 00:00:00")
        assert res is not None
        assert res.enabled is False
        assert res.rows == []
        assert res.error is None


def test_model_rows_use_the_read_plane_not_sqlite(pg_core: tuple[Any, _FakePlane]) -> None:
    """audit_signals read goes through core._query; no sqlite3 connection is made."""
    core, plane = pg_core

    res = fetch_model_rows(core, "2026-09-30 00:00:00", "2026-10-01 00:00:00")

    assert res.enabled is True
    assert res.error is None
    assert len(res.rows) == 3
    assert res.rows[0]["action"] == "BUY_MARKET"
    # The SQL pushed down must normalize the timestamp column, not compare raw.
    pushed = plane.calls[0][0]
    assert "audit_signals" in pushed
    assert "REPLACE(" in pushed


def test_model_rows_subday_window_admits_only_the_window(
    pg_core: tuple[Any, _FakePlane],
) -> None:
    """The T-vs-space comparison bug is fixed: a sub-day window is honoured."""
    core, _ = pg_core

    res = fetch_model_rows(core, "2026-09-30 10:00:00", "2026-09-30 11:00:00")

    assert [r["action"] for r in res.rows] == ["SELL_MARKET"]


def test_execution_rows_normalize_audit_orders_timestamp(
    pg_core: tuple[Any, _FakePlane],
) -> None:
    core, _ = pg_core

    res = fetch_execution_rows(core, "2026-09-30 10:00:00", "2026-09-30 11:00:00")

    assert [r["latency"] for r in res.rows] == [15.0]


def test_behavioral_rows_join_tickets_without_temp_tables(
    pg_core: tuple[Any, _FakePlane],
) -> None:
    """No CREATE TEMP TABLE: the read plane is readonly on PostgreSQL."""
    core, plane = pg_core

    res = fetch_behavioral_rows(core, ["100002"])

    assert res.enabled is True and res.error is None
    assert [r["behavior_key"] for r in res.rows] == ["k1"]
    assert [r["ticket"] for r in res.rows2] == ["100002"]
    for sql, _ in plane.calls:
        assert "TEMP TABLE" not in sql, "temp tables are not creatable on a readonly plane"
        assert "IN (" in sql


def test_behavioral_rows_chunk_over_400_tickets(pg_core: tuple[Any, _FakePlane]) -> None:
    """Ticket joins chunk at 400 so SQLite's variable limit is respected."""
    core, plane = pg_core
    plane.detections = [
        {
            "behavior_key": f"k{i}",
            "ticket": str(i),
            "pattern": "",
            "severity": "",
            "confidence": 0.0,
            "evidence": "",
        }
        for i in range(950)
    ]
    plane.analysis = [{"ticket": str(i), "note": "x"} for i in range(950)]

    res = fetch_behavioral_rows(core, [str(i) for i in range(950)])

    assert len(res.rows) == 950
    # 950 tickets / 400 chunk -> 3 behavior_detections queries + 3 behavior_analysis queries
    detection_calls = [c for c in plane.calls if "behavior_detections" in c[0]]
    assert len(detection_calls) == 3
    assert all(len(c[1]) <= 400 for c in plane.calls)


def test_anomaly_rows_join_tickets_without_temp_tables(
    pg_core: tuple[Any, _FakePlane],
) -> None:
    core, plane = pg_core

    res = fetch_anomaly_rows(core, ["100002"])

    assert [r["anomaly_type"] for r in res.rows] == ["a1"]
    assert [r["ticket"] for r in res.rows2] == ["100002"]
    for sql, _ in plane.calls:
        assert "TEMP TABLE" not in sql


def test_empty_ticket_list_short_circuits_without_queries(
    pg_core: tuple[Any, _FakePlane],
) -> None:
    core, plane = pg_core

    for fn in (fetch_behavioral_rows, fetch_anomaly_rows):
        res = fn(core, [])
        assert res.enabled is True
        assert res.rows == []
        assert res.rows2 == []
        assert res.error is None
    assert plane.calls == []


def test_store_error_surfaces_as_fetch_error_not_exception(
    isolated_fabric_registry: None,
) -> None:
    """A read-plane failure is reported through FetchResult.error, not raised."""

    class _Boom(_FakePlane):
        def query(self, sql: str, args: tuple[Any, ...] = ()):  # type: ignore[override]
            raise RuntimeError("connection reset")

    fabric_mod.register_domain_read_backend("audit", _Boom())
    repo = AuditRepository.__new__(AuditRepository)
    repo._is_sqlite = False
    repo._db_url = "postgresql://localhost:5432/nexusdb"
    repo._db_path = ""
    core = AccountingCore(audit_repo=repo)

    res = fetch_model_rows(core, "2026-09-30 00:00:00", "2026-10-01 00:00:00")

    assert isinstance(res, FetchResult)
    assert res.enabled is True
    assert res.rows == []
    assert res.error == "connection reset"


def test_fetch_result_default_shape() -> None:
    res = FetchResult(enabled=False)
    assert res.rows == []
    assert res.rows2 == []
    assert res.error is None
