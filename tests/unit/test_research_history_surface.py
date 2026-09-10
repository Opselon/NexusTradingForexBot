"""EDGE ROUND-4 (2026-09-09): archive-aware history surface.

The archive is the history — after archive-only retention moves research
events/evidence past the horizon, the observability read path must still see
them:

  * list_events / list_evidence UNION live + archive by default;
    include_archive=False restores hot-only reads.
  * history_counts() reports live vs archived row counts (honest zeros when
    archive tables are absent).
  * Rows read from the archive carry their full original payload (no data
    loss in the move).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.research.archive import archive_research_history
from nexus_scalp.research.observability import ResearchObservabilityStore


@pytest.fixture
def obs_db(tmp_path):
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    db = tmp_path / "obs_history.db"
    repo = AuditRepository(db_url=f"sqlite:///{db}", flush_interval_sec=0.02)
    conn = sqlite3.connect(db)
    ts_old = (datetime.now(UTC) - timedelta(days=400)).isoformat()
    ts_new = datetime.now(UTC).isoformat()
    for i in range(6):
        stamp = ts_old if i < 3 else ts_new
        conn.execute(
            "INSERT INTO research_events (event_id, strategy_id, research_run_id,"
            " gate_id, event_type, message, payload, occurred_at) "
            "VALUES (?, 'S', 'R1', 'G', 'GATE', ?, '{\"k\": 1}', ?)",
            (f"EVT-{i}", f"old-{i}" if stamp == ts_old else f"new-{i}", stamp),
        )
        conn.execute(
            "INSERT INTO research_evidence (evidence_id, strategy_id,"
            " research_run_id, gate_id, kind, content, content_hash,"
            " dataset_version, engine_version, created_at) "
            "VALUES (?, 'S', 'R1', 'G', 'K', '{\"v\": 2}', 'h', 'D', 'V', ?)",
            (f"EV-{i}", stamp),
        )
    conn.commit()
    yield repo, conn
    conn.close()
    repo.close()


def test_history_counts_live_and_archived(obs_db) -> None:
    repo, conn = obs_db
    try:
        store = ResearchObservabilityStore(repo)
        before = store.history_counts()
        assert before["events_live"] == 6 and before["evidence_live"] == 6
        assert before["events_archived"] == 0
        archive_research_history(conn, older_than_days=365)
        after = store.history_counts()
        assert after["events_archived"] == 3
        assert after["evidence_archived"] == 3
        assert after["events_live"] == 3
        assert after["events_live"] + after["events_archived"] == 6
    finally:
        pass


def test_events_read_union_archived_by_default(obs_db) -> None:
    repo, conn = obs_db
    store = ResearchObservabilityStore(repo)
    archive_research_history(conn, older_than_days=365)
    merged = store.list_events(limit=50)
    ids = {e["event_id"] for e in merged}
    assert len(merged) == 6
    assert {"EVT-0", "EVT-5"} <= ids
    # archived rows keep their full payload (no data loss in the move)
    old = next(e for e in merged if e["event_id"] == "EVT-0")
    assert old["message"] == "old-0"
    assert old["payload"] == {"k": 1}


def test_events_hot_only_when_include_archive_false(obs_db) -> None:
    repo, conn = obs_db
    store = ResearchObservabilityStore(repo)
    archive_research_history(conn, older_than_days=365)
    hot = store.list_events(limit=50, include_archive=False)
    assert len(hot) == 3
    assert all(e["event_id"].startswith("EVT-") for e in hot)
    # the hot set is exactly the fresh rows
    assert all(e["message"].startswith("new-") for e in hot)


def test_evidence_read_union_archived_by_default(obs_db) -> None:
    repo, conn = obs_db
    store = ResearchObservabilityStore(repo)
    archive_research_history(conn, older_than_days=365)
    merged = store.list_evidence(limit=50)
    assert len(merged) == 6
    assert {"EV-0", "EV-5"} <= {e["evidence_id"] for e in merged}
    hot = store.list_evidence(limit=50, include_archive=False)
    assert len(hot) == 3


def test_reads_work_without_archive_tables(tmp_path) -> None:
    # Pre-AUDIT-0009 databases (or fresh stores): honest zeros + no crash.
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    db = tmp_path / "noarchive.db"
    repo = AuditRepository(db_url=f"sqlite:///{db}", flush_interval_sec=0.02)
    try:
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS research_events (id INTEGER PRIMARY KEY,"
            " event_id TEXT, strategy_id TEXT, research_run_id TEXT, gate_id TEXT,"
            " event_type TEXT, message TEXT, payload TEXT, occurred_at TEXT)"
        )
        conn.commit()
        store = ResearchObservabilityStore(repo)
        counts = store.history_counts()
        assert counts == {
            "events_live": 0,
            "events_archived": 0,
            "evidence_live": 0,
            "evidence_archived": 0,
        }
        assert store.list_events() == []
        conn.close()
    finally:
        repo.close()
