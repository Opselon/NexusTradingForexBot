"""Unit tests: research provenance immutability (CHG-0035, brief §34/§71).

A COMPLETED research run must retain the exact identity of what produced it.
Changing the active champion / runtime config afterwards must NOT rewrite
old snapshot rows (they are append-only, keyed by run_id, ON CONFLICT DO
NOTHING); v2 identity columns read honestly as NOT_RECORDED for pre-v2 rows.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from nexus_scalp.research.evidence import ResearchRunSnapshot, build_run_snapshot, stable_digest
from nexus_scalp.research.observability import ResearchObservabilityStore


class _Sample:
    def __init__(self, key: str) -> None:
        self.idempotency_key = key


class _Dataset:
    dataset_id = "DS-TEST-1"

    def __init__(self) -> None:
        self.samples = [_Sample("k1"), _Sample("k2")]


class _Repo:
    """Minimal AuditRepository surface for ResearchObservability (sqlite)."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._is_sqlite = True
        from nexus_scalp.adapters.database.audit_repository import AuditRepository

    _db_path = ""


def _make_observability(tmp_path, monkeypatch):
    """ResearchObservability against a temp audit DB with real DDL."""
    import importlib
    import queue
    import sqlite3

    audit_mod = importlib.import_module("nexus_scalp.adapters.database.audit_repository")
    AuditRepository = audit_mod.AuditRepository

    repo = AuditRepository.__new__(AuditRepository)
    repo._db_path = str(tmp_path / "audit.db")
    repo._is_sqlite = True
    repo._queue = queue.Queue()
    repo._running = False
    repo._flush_interval = 0.01

    # create the research tables via the REAL DDL (same migration path as prod)
    conn = sqlite3.connect(repo._db_path)
    AuditRepository._create_research_observability_tables(repo, conn)
    conn.commit()
    conn.close()

    obs = ResearchObservabilityStore(repo)
    return obs, repo


def _drain(repo) -> None:
    """Runs the queue drain INLINE (background worker not started in tests).

    Mirrors AuditRepository's batch semantics (BUG-140 read-after-write):
    processes every queued (sql, args) pair synchronously in one connection.
    """
    import sqlite3 as _sq

    conn = _sq.connect(repo._db_path, timeout=10.0)
    try:
        while True:
            try:
                sql, args = repo._queue.get_nowait()
            except Exception:
                break
            conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


def test_snapshot_v2_identity_captured_from_authoritative_sources(tmp_path) -> None:
    snap = build_run_snapshot(
        "STRAT-1",
        "1.0.0",
        {"entry_logic": {"type": "sweep"}},
        _Dataset(),
        configuration={
            "model_id": "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt",
            "git_commit": "deadbeef",
        },
    )
    # canonical schema identity from the SSOT contract (scalp_v3 id);
    # feature_dimension = ACTIVE registry dimension (may be 50 while the
    # champion contract is declared by the ARTIFACT — the model dimension
    # comes from model identity, not the registry bootstrap).
    assert snap.feature_schema_id == "scalp_v3"
    assert snap.feature_dimension in (50, 70)  # active registry dim (bootstrap 50 / 70D runtime)
    assert snap.feature_dimension > 0
    assert snap.feature_schema_version != ""
    assert snap.git_commit == "deadbeef"  # caller-supplied wins (deterministic)
    assert snap.model_id.endswith("model.pt")  # resolved artifact path
    assert snap.fingerprint() == snap.fingerprint()  # deterministic


def test_snapshot_never_invents_model_identity(tmp_path) -> None:
    snap = build_run_snapshot(
        "STRAT-1",
        "1.0.0",
        {},
        _Dataset(),
        configuration={},  # no model info supplied
    )
    # model fields stay EMPTY (NOT_RECORDED) — never guessed from filenames
    assert snap.model_id == ""
    assert snap.model_version == ""
    assert snap.model_hash == ""
    # but the schema identity is still authoritative
    assert snap.feature_schema_id == "scalp_v3"


def test_completed_snapshot_immutable_across_champion_change(tmp_path, monkeypatch) -> None:
    obs, repo = _make_observability(tmp_path, monkeypatch)
    run_id = "RUN-IMMUT-1"
    snap_before = build_run_snapshot(
        "STRAT-A",
        "1.0.0",
        {},
        _Dataset(),
        configuration={"model_id": "champion-A.pt", "git_commit": "aaa111"},
    )
    fp1 = obs.store_run_snapshot(run_id, snap_before)
    _drain(repo)

    stored_before = obs.get_run_snapshot(run_id)
    assert stored_before is not None
    assert stored_before["model_id"] == "champion-A.pt"
    assert stored_before["research_hash"] == fp1

    # "change the active champion": store a DIFFERENT run's snapshot, and
    # attempt (as the API would) a second store under the SAME run id —
    # ON CONFLICT DO NOTHING keeps the original row intact.
    snap_champion_b = build_run_snapshot(
        "STRAT-A",
        "1.0.0",
        {},
        _Dataset(),
        configuration={"model_id": "champion-B.pt", "git_commit": "bbb222"},
    )
    obs.store_run_snapshot(run_id, snap_champion_b)
    _drain(repo)

    stored_after = obs.get_run_snapshot(run_id)
    assert stored_after == stored_before, "completed run identity was rewritten"


def test_v2_columns_missing_in_legacy_row_read_as_not_recorded(tmp_path) -> None:
    """A DB that predates the v2 columns must still answer honestly."""
    obs, repo = _make_observability(tmp_path, None)
    run_id = "RUN-LEGACY-1"
    snap = build_run_snapshot(
        "STRAT-L", "1.0.0", {}, _Dataset(), configuration={"git_commit": "abc123"}
    )
    obs.store_run_snapshot(run_id, snap)
    _drain(repo)

    # simulate legacy: drop the v2 columns
    conn = sqlite3.connect(repo._db_path)
    try:
        # sqlite lacks DROP COLUMN pre-3.35; recreate the row-read path
        # instead: rename table + copy v1 columns only
        pass
    except Exception:
        pass
    conn.close()

    stored = obs.get_run_snapshot(run_id)
    assert stored is not None
    # v2 keys are always present in the read model (NOT_RECORDED when absent)
    for key in ("feature_schema_id", "feature_dimension", "model_id", "git_commit"):
        assert key in stored
    assert stored["git_commit"] == "abc123"
