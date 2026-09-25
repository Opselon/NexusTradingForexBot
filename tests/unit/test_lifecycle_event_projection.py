"""
Tests for the lifecycle event projection (Phase 2 traceability).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from nexus_scalp.research.event_projection import (
    LifecycleEventProjection,
    parse_lineage_entry,
)


class TestParseLineageEntry:
    def test_basic_transition(self):
        ev = parse_lineage_entry("2026-08-23T10:00:00+00:00:VALIDATED:gate_pass")
        assert ev is not None
        assert ev["to_state"] == "VALIDATED"
        assert ev["reason"] == "gate_pass"
        assert ev["event_type"] == "LIFECYCLE_TRANSITION"

    def test_iso_colons_not_fragmented(self):
        # The ISO timestamp contains colons; parsing must not break it.
        ev = parse_lineage_entry(
            "2026-08-23T10:30:45.123456+00:00:SHADOW:operator_promotion:actor=alice"
        )
        assert ev is not None
        assert ev["to_state"] == "SHADOW"
        assert "2026-08-23T10:30:45" in ev["timestamp"]
        assert "operator" in ev["actor"]

    def test_operator_actor_detected(self):
        ev = parse_lineage_entry(
            "2026-08-23T10:00:00+00:00:ACTIVE:operator_promotion:actor=bob:manual_review"
        )
        assert ev["actor"] == "operator"
        assert "bob" in ev["reason"]

    def test_unknown_garbage_returns_none(self):
        assert parse_lineage_entry("not-a-real-line") is None
        assert parse_lineage_entry("") is None


class TestEventProjection:
    @pytest.fixture()
    def repo(self, tmp_path):
        """Minimal SQLite fixture mirroring strategy_registry / research_runs."""
        import sqlite3

        from nexus_scalp.adapters.database.audit_repository import AuditRepository

        db_path = tmp_path / "proj.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE strategy_registry (
                strategy_id TEXT, strategy_version TEXT, feature_schema_id TEXT,
                feature_dimension INTEGER, discovery_source TEXT, discovery_window TEXT,
                context_definition TEXT, parent_strategy_ids TEXT,
                lifecycle TEXT, backtest TEXT, walkforward TEXT, oos TEXT,
                robustness TEXT, score TEXT, confidence REAL, sample_count INTEGER,
                validation_lineage TEXT, retirement_reason TEXT,
                created_at TEXT, updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE research_runs (
                run_id TEXT PRIMARY KEY, dataset_id TEXT, strategy_id TEXT,
                strategy_version TEXT, executed_at TEXT, config TEXT,
                build_identity TEXT, result_summary TEXT, completed_at TEXT,
                status TEXT, run_outcome TEXT, snapshot_id TEXT, gates TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO strategy_registry VALUES (
                'strat-a', '1.0.0', 'scalp_v1', 50, 'discovery', '2026-W33',
                '{}', '[]', 'SHADOW',
                '{}', '{}', '{}', '{}', '{}', 0.7, 120,
                ?, '', '2026-08-20T09:00:00+00:00', '2026-08-23T11:00:00+00:00'
            )
            """,
            (
                json.dumps(
                    [
                        "2026-08-20T09:00:00+00:00:DISCOVERED",
                        "2026-08-21T09:41:00+00:00:VALIDATED",
                        "2026-08-23T11:03:00+00:00:SHADOW",
                    ]
                ),
            ),
        )
        conn.execute(
            """
            INSERT INTO research_runs VALUES (
                'run-1', 'ds-1', 'strat-a', '1.0.0',
                '2026-08-21T09:40:00+00:00', '{}', 'build-x',
                '{"oos_status": "PASS"}', '2026-08-21T09:41:00+00:00',
                'COMPLETED', 'VALIDATED', 'snap-1', '[]'
            )
            """
        )
        conn.commit()
        conn.close()

        class _Repo:
            _is_sqlite = True
            _db_path = str(db_path)

        return _Repo()

    def test_events_for_strategy_chronological(self, repo):
        proj = LifecycleEventProjection(repo)
        events = proj.events_for_strategy("strat-a")
        types = [e["event_type"] for e in events]
        assert "LIFECYCLE_TRANSITION" in types
        assert "VALIDATION_RUN" in types
        states = [e.get("to_state") for e in events if e["event_type"] == "LIFECYCLE_TRANSITION"]
        assert states == ["DISCOVERED", "VALIDATED", "SHADOW"]

    def test_recent_events_bounded(self, repo):
        proj = LifecycleEventProjection(repo)
        events = proj.recent_events(limit=2)
        assert len(events) <= 2

    def test_evidence_completeness_incomplete(self, repo):
        proj = LifecycleEventProjection(repo)
        report = proj.evidence_completeness("strat-a")
        # All gate columns are '{}' → decode to None → missing.
        assert report["verdict"] == "INCOMPLETE"
        assert set(report["missing"]) >= {"backtest", "oos"}

    def test_evidence_completeness_missing_strategy(self, repo):
        proj = LifecycleEventProjection(repo)
        report = proj.evidence_completeness("no-such-strategy")
        assert report["verdict"] == "NOT_AVAILABLE"

    def test_evidence_completeness_complete(self, repo):
        import sqlite3

        proj = LifecycleEventProjection(repo)
        conn = sqlite3.connect(proj.audit_repo._db_path)
        conn.execute(
            "UPDATE strategy_registry SET backtest=?, walkforward=?, oos=?, robustness=?, score=? "
            "WHERE strategy_id='strat-a'",
            tuple(json.dumps({"k": "v"}) for _ in range(5)),
        )
        conn.commit()
        conn.close()
        report = proj.evidence_completeness("strat-a")
        assert report["verdict"] == "COMPLETE"
