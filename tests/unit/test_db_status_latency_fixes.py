"""PERF-DB-STATUS (2026-09-23) — /api/db/status and /api/db/hygiene must not
run full DB integrity sweeps / table rescans per poll.

Measured on the live tree (this host):

  DatabaseMigrationEngine.status() sub-steps
    audit.db      426MB  _integrity 5-41s   (everything else < 0.4s)
    news.db       241MB  _integrity 1.9-5s
    candle_intel   11MB  _integrity 0.06-0.4s
    PRAGMA quick_check (warm): audit 1.6s, news 1.0s, candle 0.09s

  GET /api/db/status   6.8-12.4s per call (3 domains, warm)
  GET /api/db/hygiene  ~9s per call (plan_database rescans all 3 files)

Both are polled by the Database tab every 30s/60s and fired concurrently,
which pushed /api/db/status past the frontend's 15000ms fetch timeout —
the live symptom was the schema panel showing "Request timed out
(request_id: altui_*)".

Pinned contract:
  1. engine._integrity() runs PRAGMA quick_check FIRST and only escalates
     to the full PRAGMA integrity_check when quick_check flags a problem —
     a healthy fast path may never weaken the verdict for a real problem;
  2. GET /api/db/status serves its serialized payload from a 30s TTL cache
     (_DB_STATUS_CACHE); GET /api/db/hygiene likewise (_DB_HYGIENE_CACHE);
  3. failure payloads are NEVER cached (errors must stay visible + retryable);
  4. the deeper integrity authorities are untouched: nexus doctor /
     HealthEngine (/health, PERF-HEALTH) / forensics still run full
     integrity sweeps on demand.
"""

from __future__ import annotations

import time

from nexus_scalp.database.engine import DatabaseMigrationEngine

# ---------------------------------------------------------------------
# 1. engine._integrity: quick_check first, escalate on flag
# ---------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = list(rows)

    def fetchone(self) -> tuple | None:
        return self._rows.pop(0) if self._rows else None


class _FakeCon:
    def __init__(self, responses: dict[str, list[tuple]]) -> None:
        self._responses = responses
        self.sqls: list[str] = []

    def execute(self, sql: str, *args: object) -> _FakeCursor:
        self.sqls.append(sql)
        return _FakeCursor(self._responses.get(sql, []))

    def close(self) -> None:
        return None


def _engine(tmp_path) -> DatabaseMigrationEngine:
    return DatabaseMigrationEngine(db_path=tmp_path / "audit.db", domain="audit")


def test_integrity_quick_check_first_when_healthy(tmp_path, monkeypatch) -> None:
    eng = _engine(tmp_path)
    con = _FakeCon({"PRAGMA quick_check": [("ok",)]})
    monkeypatch.setattr(eng, "_connect", lambda timeout=5.0: con)

    assert eng._integrity() == "ok"
    assert con.sqls == ["PRAGMA quick_check"], (
        "healthy path must not run the full PRAGMA integrity_check "
        "(measured 5-41s on the live 426MB audit.db)"
    )


def test_integrity_escalates_to_full_check_when_quick_flags(
    tmp_path, monkeypatch
) -> None:
    eng = _engine(tmp_path)
    con = _FakeCon(
        {
            "PRAGMA quick_check": [("row missing from index quick_check_fake",)],
            "PRAGMA integrity_check": [("ok",)],
        }
    )
    monkeypatch.setattr(eng, "_connect", lambda timeout=5.0: con)

    # verdict = the FULL check's verdict, never quick_check alone
    assert eng._integrity() == "ok"
    assert con.sqls == ["PRAGMA quick_check", "PRAGMA integrity_check"]


def test_integrity_reports_full_check_failure_verdict(
    tmp_path, monkeypatch
) -> None:
    eng = _engine(tmp_path)
    con = _FakeCon(
        {
            "PRAGMA quick_check": [("database disk image is malformed",)],
            "PRAGMA integrity_check": [("*** in database 1 is malformed",)],
        }
    )
    monkeypatch.setattr(eng, "_connect", lambda timeout=5.0: con)

    assert eng._integrity() == "*** in database 1 is malformed"


def test_integrity_error_path_still_returns_error(tmp_path, monkeypatch) -> None:
    import sqlite3

    eng = _engine(tmp_path)

    def _boom(timeout: float = 5.0):
        raise sqlite3.Error("locked")

    monkeypatch.setattr(eng, "_connect", _boom)
    assert eng._integrity() == "error"


# ---------------------------------------------------------------------
# 2. route TTL caches: fresh serve, stale miss, invalidate, no error cache
# ---------------------------------------------------------------------


def test_db_status_cache_ttl_semantics() -> None:
    import nexus_scalp.web.debug_research_routes as dr

    dr.invalidate_db_status_cache()
    assert dr._db_status_cached() is None

    payload = {"available": True, "databases": {"audit": {"schema_version": 7}}}
    dr._db_status_store(payload)
    assert dr._db_status_cached() is payload, "fresh entry must be served"

    # age it out past the TTL → miss (recompute happens on next poll)
    dr._DB_STATUS_CACHE.clear()
    dr._DB_STATUS_CACHE.update(
        {"at": time.monotonic() - (dr._DB_STATUS_TTL_SEC + 1.0), "payload": payload}
    )
    assert dr._db_status_cached() is None

    dr._db_status_store(payload)
    dr.invalidate_db_status_cache()
    assert dr._db_status_cached() is None


def test_db_status_ttl_covers_the_ui_poll_interval() -> None:
    # the Database tab polls /api/db/status every 30s; TTL must at least
    # cover one full interval or the cache never serves the second poll.
    import nexus_scalp.web.debug_research_routes as dr

    assert dr._DB_STATUS_TTL_SEC >= 30.0


def test_hygiene_cache_ttl_semantics() -> None:
    import nexus_scalp.web.diagnostics_state_routes as routes

    routes.invalidate_db_hygiene_cache()
    assert routes._db_hygiene_cached() is None

    payload = {"status": {"state": "IDLE"}, "plans": {}}
    routes._db_hygiene_store(payload)
    assert routes._db_hygiene_cached() is payload

    routes._DB_HYGIENE_CACHE.clear()
    routes._DB_HYGIENE_CACHE.update(
        {"at": time.monotonic() - (routes._DB_HYGIENE_TTL_SEC + 1.0), "payload": payload}
    )
    assert routes._db_hygiene_cached() is None


def test_hygiene_route_serves_second_poll_from_cache(monkeypatch) -> None:
    """Two GETs inside the TTL → one worker construction (no rescan)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import nexus_scalp.hygiene.hygiene_runtime as hr
    import nexus_scalp.hygiene.worker_runner as wr
    import nexus_scalp.web.diagnostics_state_routes as routes

    routes.invalidate_db_hygiene_cache()

    calls = {"worker_ctor": 0, "plans": 0}

    class _FakeWorker:
        def __init__(self, **kwargs: object) -> None:
            calls["worker_ctor"] += 1

        def status(self) -> dict:
            return {"state": "IDLE", "mode": "AUDIT_ONLY"}

        def plan_database(self, db_key: str) -> dict:
            calls["plans"] += 1
            return {"database": db_key, "plan": {"database": db_key}}

    class _FakeQuarantine:
        def stats(self) -> dict:
            return {"count": 0}

        def list(self, limit: int = 20) -> list:
            return []

    class _FakeSched:
        def __init__(self, repo_root: object = None) -> None:
            self.quarantine = _FakeQuarantine()

        def status(self) -> dict:
            return {"next_run_at": "2026-09-23T03:00:00+00:00"}

    monkeypatch.setattr(wr, "DatabaseHygieneWorker", _FakeWorker)
    monkeypatch.setattr(hr, "RuntimeCleanupScheduler", _FakeSched)

    app = FastAPI()
    routes.register_diagnostics_state_routes(
        app,
        _err=None,
        _log_err=lambda *a, **k: None,
        serialize_enums=lambda x: x,
        get_system_state=lambda: {},
    )
    client = TestClient(app)

    first = client.get("/api/db/hygiene")
    second = client.get("/api/db/hygiene")
    assert first.status_code == 200 and second.status_code == 200
    assert first.json() == second.json()
    assert calls["worker_ctor"] == 1, (
        "second poll inside the TTL must NOT construct the worker / rescan "
        "the three SQLite files (~9s measured)"
    )

    # cache must expire: age it out and the next poll recomputes
    if routes._DB_HYGIENE_CACHE:
        routes._DB_HYGIENE_CACHE["at"] = (
            time.monotonic() - (routes._DB_HYGIENE_TTL_SEC + 1.0)
        )
    client.get("/api/db/hygiene")
    assert calls["worker_ctor"] == 2, "stale entry must be recomputed"

    routes.invalidate_db_hygiene_cache()
