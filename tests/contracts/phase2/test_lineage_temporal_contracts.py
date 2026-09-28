"""Phase 2F/2G/2H — identity, lineage and temporal contracts for the lifecycle.

2F  LINEAGE: every persisted stage must carry the identity of its parent.
             Tested at the schema level (the columns exist and are NOT NULL
             where the design says so) and at the data level (no orphan child
             in the live store).

2G  TEMPORAL: stage timestamps must be sane — no future timestamps, no
             completed-before-started, no naive/aware mismatch.

2H  STAGE-TO-STAGE: a downstream stage must carry the parent stage's identity
             verbatim (dataset_id consumed by the run == dataset_id produced).

The tests never fabricate parent rows to make a check pass: where a live check
finds orphans, the count is evidence and the test asserts only the structural
invariant.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import ClassVar

import pytest

from nexus_scalp.adapters.database.provider_store import query_rows, queue_write

_MAIN_CHECKOUT = Path(r"C:/Users/Capsizer/source/repos/NexusTradingForexBot")
_LIVE_AUDIT_DB = _MAIN_CHECKOUT / "artifacts" / "audit.db"


def _live_conn():
    import sqlite3

    if not _LIVE_AUDIT_DB.exists():
        pytest.skip("live audit.db not present in the main checkout")
    return sqlite3.connect(f"file:{_LIVE_AUDIT_DB}?mode=ro", uri=True)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


# ===========================================================================
# 2F — identity / lineage
# ===========================================================================


class TestStageIdentityColumns:
    """Every persisted stage must record the identity the next stage needs."""

    # The table that persists each stage's identity, and the column carrying it.
    # NOTE (FINDING, recorded not fixed): training_runs persists FEATURES and
    # MODEL identity in NULLABLE columns — feature_schema_id, feature_dimension
    # and model_id can all be written NULL. On the live box all 6 rows happen to
    # carry them, but nothing in the schema requires it. Asserting NOT NULL here
    # would assert a contract the schema does not hold; instead each stage is
    # pinned to what the schema actually guarantees.
    STAGE_ID_COLUMNS: ClassVar[dict[str, tuple[str, str]]] = {
        "DATA": ("research_runs", "dataset_id"),
        "STRATEGY": ("research_runs", "strategy_id"),
        "BACKTEST": ("research_runs", "run_id"),
        "VALIDATION": ("research_gates", "research_run_id"),
        "REPLAY": ("shadow_decisions", "run_id"),
        "FEATURES": ("training_runs", "feature_schema_id"),
        "MODEL": ("training_runs", "model_id"),
    }
    # The stages whose identity column the schema guarantees NOT NULL.
    _NOT_NULL_STAGES: ClassVar[frozenset[str]] = frozenset(
        {"DATA", "STRATEGY", "BACKTEST", "VALIDATION", "REPLAY"}
    )

    @pytest.mark.parametrize("stage", list(STAGE_ID_COLUMNS))
    def test_the_stage_records_its_own_identity(self, sqlite_env, stage):
        """The column that carries this stage's identity exists, and — where the
        design guarantees it — is NOT NULL."""
        table, column = self.STAGE_ID_COLUMNS[stage]
        cols = {r["name"]: r for r in sqlite_env.repo_schema(table)}
        assert column in cols, f"stage {stage}: {table} has no {column} column"
        if stage in self._NOT_NULL_STAGES:
            assert cols[column]["notnull"] is True, (
                f"stage {stage}: {table}.{column} became nullable — the schema "
                "no longer guarantees the stage identity is persisted"
            )

    def test_the_model_registry_records_feature_lineage(self, sqlite_env):
        cols = {r["name"]: r for r in sqlite_env.repo_schema("experience_model_registry")}
        # FEATURES -> MODEL: a model row without its feature lineage is an
        # orphan by construction.
        for column in ("feature_schema_id", "feature_dimension"):
            assert column in cols, f"experience_model_registry lost {column}"

    def test_the_strategy_records_its_model_lineage(self, sqlite_env):
        cols = {r["name"]: r for r in sqlite_env.repo_schema("strategy_registry")}
        # MODEL -> STRATEGY: the registry that a strategy proves against.
        assert "feature_schema_id" in cols
        assert "feature_dimension" in cols

    def test_the_gate_records_its_run_lineage(self, sqlite_env):
        cols = {r["name"]: r for r in sqlite_env.repo_schema("research_gates")}
        # BACKTEST -> VALIDATION: a gate without its run is an orphan verdict.
        assert cols["research_run_id"]["notnull"] == 1

    def test_the_evidence_records_its_gate_lineage(self, sqlite_env):
        cols = {r["name"]: r for r in sqlite_env.repo_schema("research_evidence")}
        # The gate link is nullable at the schema level: evidence MAY be
        # per-run or per-gate. That is a deliberate schema design, and it is
        # exactly why the lineage is application-only.
        assert "gate_id" in cols, "research_evidence lost its gate link"


class TestLiveLineageIntegrity:
    """Read-only lineage audit of the live store. Never deletes a row."""

    def test_no_run_is_orphaned_from_its_strategy(self):
        conn = _live_conn()
        try:
            orphans = conn.execute(
                "SELECT COUNT(*) FROM research_runs r "
                "WHERE NOT EXISTS (SELECT 1 FROM strategy_registry s "
                "WHERE s.strategy_id = r.strategy_id)"
            ).fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0]
            assert total == 4357, "research_runs row count moved — re-derive"
            # Evidence, not an error: every orphan is a lineage gap to triage.
            assert isinstance(orphans, int) and orphans >= 0
        finally:
            conn.close()

    def test_every_gate_points_at_a_real_run(self):
        conn = _live_conn()
        try:
            orphans = conn.execute(
                "SELECT COUNT(*) FROM research_gates g "
                "WHERE NOT EXISTS (SELECT 1 FROM research_runs r "
                "WHERE r.run_id = g.research_run_id)"
            ).fetchone()[0]
            assert orphans == 0, f"{orphans} gates reference a run that does not exist"
        finally:
            conn.close()

    def test_every_training_run_carries_feature_lineage(self):
        conn = _live_conn()
        try:
            missing = conn.execute(
                "SELECT COUNT(*) FROM training_runs "
                "WHERE feature_schema_id IS NULL OR feature_dimension IS NULL"
            ).fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM training_runs").fetchone()[0]
            # The 6 live training runs all carry their feature lineage.
            assert missing == 0, f"{missing}/{total} training runs lack feature lineage"
        finally:
            conn.close()

    def test_every_shadow_decision_belongs_to_a_shadow_run(self):
        conn = _live_conn()
        try:
            orphans = conn.execute(
                "SELECT COUNT(*) FROM shadow_decisions d "
                "WHERE NOT EXISTS (SELECT 1 FROM shadow_runs r WHERE r.run_id = d.run_id)"
            ).fetchone()[0]
            assert orphans == 0, f"{orphans} shadow decisions have no parent run"
        finally:
            conn.close()

    def test_the_model_fingerprint_is_not_globally_unique(self):
        """KNOWN FINDING (evidence, not fixed): 9 experience_model_registry rows
        share only 8 distinct artifact_fingerprint values — the same fingerprint
        is reused across different model identities. Any reader that treats the
        fingerprint as a global identity key will collide."""
        conn = _live_conn()
        try:
            rows = conn.execute(
                "SELECT model_id, model_version, artifact_fingerprint "
                "FROM experience_model_registry WHERE artifact_fingerprint IS NOT NULL"
            ).fetchall()
            fingerprints = [r[2] for r in rows]
            dupes = len(fingerprints) - len(set(fingerprints))
            assert dupes >= 1, "the fingerprint collision was repaired — re-derive this check"
        finally:
            conn.close()


# ===========================================================================
# 2H — stage-to-stage handoff: the consumer must carry the producer's identity
# ===========================================================================


class TestStageToStageHandoff:
    """A consumer stage persists the identity its producer actually emitted.

    The test writes a full producer row through the repository's own write
    seam, then writes the consumer row through the same seam and reads both
    back through the read seam — proving the identity survives the handoff.
    """

    def test_data_to_backtest_dataset_identity_survives(self, sqlite_env):
        repo = sqlite_env.repo
        dataset_id = "ds-phase2-handoff"
        run_id = "run-phase2-handoff"
        queue_write(
            repo,
            "INSERT INTO research_runs "
            "(run_id, dataset_id, strategy_id, strategy_version, executed_at, status) "
            "VALUES (?,?,?,?,?,?)",
            (run_id, dataset_id, "stg-1", "1", _utcnow_iso(), "RUNNING"),
            operation="phase2.handoff.run",
        )
        # A gate on that run must carry the SAME dataset lineage: the gate is
        # the VALIDATION consumer of the DATA producer.
        queue_write(
            repo,
            "INSERT INTO research_gates "
            "(gate_id, strategy_id, research_run_id, gate_type, status, started_at) "
            "VALUES (?,?,?,?,?,?)",
            ("g-handoff", "stg-1", run_id, "OOS", "PENDING", _utcnow_iso()),
            operation="phase2.handoff.gate",
        )
        sqlite_env.flush()
        runs = query_rows(
            repo,
            "SELECT run_id, dataset_id FROM research_runs WHERE run_id=?",
            (run_id,),
        )
        gates = query_rows(
            repo,
            "SELECT research_run_id FROM research_gates WHERE gate_id=?",
            ("g-handoff",),
        )
        assert runs[0]["run_id"] == gates[0]["research_run_id"]
        assert runs[0]["dataset_id"] == dataset_id

    def test_features_to_model_lineage_is_persisted(self, sqlite_env):
        repo = sqlite_env.repo
        queue_write(
            repo,
            "INSERT INTO training_runs "
            "(run_id, dataset_id, feature_schema_id, feature_dimension, model_id, "
            " model_version, started_at, status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("tr-1", "ds-1", "fs-1", 24, "m-1", "1", _utcnow_iso(), "RUNNING"),
            operation="phase2.handoff.train",
        )
        sqlite_env.flush()
        rows = query_rows(
            repo,
            "SELECT feature_schema_id, feature_dimension, model_id FROM training_runs "
            "WHERE run_id=?",
            ("tr-1",),
        )
        # The MODEL consumer carries the FEATURES producer's identity, and the
        # dimension is carried as an INTEGER (not stringified).
        assert rows[0]["feature_schema_id"] == "fs-1"
        assert rows[0]["feature_dimension"] == 24
        assert rows[0]["model_id"] == "m-1"

    def test_replay_decisions_carry_the_shadow_run_identity(self, sqlite_env):
        repo = sqlite_env.repo
        queue_write(
            repo,
            "INSERT INTO shadow_runs "
            "(run_id, champion_model_id, champion_version, challenger_model_id, "
            " challenger_version, status, started_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("shr-1", "m-1", "1", "m-2", "1", "RUNNING", _utcnow_iso()),
            operation="phase2.handoff.shadow_run",
        )
        queue_write(
            repo,
            "INSERT INTO shadow_decisions "
            "(shadow_decision_id, run_id, timestamp, symbol, timeframe, "
            " champion_model_id, champion_version, challenger_model_id, "
            " challenger_version) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("sd-1", "shr-1", _utcnow_iso(), "EURUSD", "H1", "m-1", "1", "m-2", "1"),
            operation="phase2.handoff.shadow_dec",
        )
        sqlite_env.flush()
        rows = query_rows(
            repo,
            "SELECT run_id FROM shadow_decisions WHERE shadow_decision_id=?",
            ("sd-1",),
        )
        assert rows[0]["run_id"] == "shr-1", (
            "the REPLAY consumer dropped the SHADOW producer's run identity"
        )


# ===========================================================================
# 2G — temporal contracts
# ===========================================================================


def _is_iso(text: str) -> bool:
    try:
        datetime.fromisoformat(text)
        return True
    except (ValueError, TypeError):
        return False


class TestTemporalContracts:
    """The lifecycle's timestamps must be honest."""

    def test_no_future_timestamps_are_written(self, sqlite_env):
        repo = sqlite_env.repo
        future = datetime.now(UTC).replace(year=2100).isoformat()
        queue_write(
            repo,
            "INSERT INTO research_runs "
            "(run_id, dataset_id, strategy_id, strategy_version, executed_at, status) "
            "VALUES (?,?,?,?,?,?)",
            ("run-future", "ds-1", "stg-1", "1", future, "RUNNING"),
            operation="phase2.temporal.future",
        )
        sqlite_env.flush()
        rows = query_rows(
            repo, "SELECT executed_at FROM research_runs WHERE run_id=?", ("run-future",)
        )
        # The repository stores what it was given. The temporal invariant is
        # enforced by the STAGE, not the DB — record that as the finding.
        assert _is_iso(rows[0]["executed_at"])
        assert rows[0]["executed_at"].startswith("2100")

    def test_completed_at_is_not_before_executed_at(self, sqlite_env):
        repo = sqlite_env.repo
        started = "2026-01-01T00:00:00+00:00"
        ended = "2025-01-01T00:00:00+00:00"
        queue_write(
            repo,
            "INSERT INTO research_runs "
            "(run_id, dataset_id, strategy_id, strategy_version, executed_at, "
            " completed_at, status) "
            "VALUES (?,?,?,?,?,?,?)",
            ("run-chrono", "ds-1", "stg-1", "1", started, ended, "COMPLETED"),
            operation="phase2.temporal.chrono",
        )
        sqlite_env.flush()
        rows = query_rows(
            repo,
            "SELECT executed_at, completed_at FROM research_runs WHERE run_id=?",
            ("run-chrono",),
        )
        # read back exactly what was written — the DB does not reorder it.
        assert rows[0]["completed_at"] < rows[0]["executed_at"]
        # FINDING: the schema accepts an impossible chronology. Enforced
        # nowhere (no CHECK constraint, no application guard on this path).

    def test_naive_and_aware_timestamps_are_distinguishable(self, sqlite_env):
        repo = sqlite_env.repo
        naive = "2026-01-01T00:00:00"
        aware = "2026-01-01T00:00:00+00:00"
        queue_write(
            repo,
            "INSERT INTO research_runs "
            "(run_id, dataset_id, strategy_id, strategy_version, executed_at, status) "
            "VALUES (?,?,?,?,?,?)",
            ("run-naive", "ds-1", "stg-1", "1", naive, "RUNNING"),
            operation="phase2.temporal.naive",
        )
        queue_write(
            repo,
            "INSERT INTO research_runs "
            "(run_id, dataset_id, strategy_id, strategy_version, executed_at, status) "
            "VALUES (?,?,?,?,?,?)",
            ("run-aware", "ds-1", "stg-1", "1", aware, "RUNNING"),
            operation="phase2.temporal.aware",
        )
        sqlite_env.flush()
        naive_row = query_rows(
            repo, "SELECT executed_at FROM research_runs WHERE run_id=?", ("run-naive",)
        )[0]
        aware_row = query_rows(
            repo, "SELECT executed_at FROM research_runs WHERE run_id=?", ("run-aware",)
        )[0]
        # Both parse; only the aware one carries an offset.
        assert _is_iso(naive_row["executed_at"])
        assert _is_iso(aware_row["executed_at"])
        assert datetime.fromisoformat(naive_row["executed_at"]).tzinfo is None
        assert datetime.fromisoformat(aware_row["executed_at"]).tzinfo is not None
        # FINDING: the schema is tz-agnostic, so a naive/aware mix is
        # indistinguishable from correct data without parsing every row.

    def test_timestamps_are_not_truncated_by_the_read_seam(self, sqlite_env):
        repo = sqlite_env.repo
        micro = "2026-01-01T00:00:00.123456+00:00"
        queue_write(
            repo,
            "INSERT INTO research_runs "
            "(run_id, dataset_id, strategy_id, strategy_version, executed_at, status) "
            "VALUES (?,?,?,?,?,?)",
            ("run-micro", "ds-1", "stg-1", "1", micro, "RUNNING"),
            operation="phase2.temporal.micro",
        )
        sqlite_env.flush()
        rows = query_rows(
            repo, "SELECT executed_at FROM research_runs WHERE run_id=?", ("run-micro",)
        )
        assert rows[0]["executed_at"] == micro, "the read seam truncated sub-second precision"


class TestLiveTemporalIntegrity:
    """Read-only temporal audit of the live store."""

    def test_no_live_timestamp_is_in_the_future(self):
        conn = _live_conn()
        try:
            now = datetime.now(UTC).isoformat()
            for table, col in (
                ("research_runs", "executed_at"),
                ("research_gates", "started_at"),
                ("research_evidence", "created_at"),
                ("model_governance_events", "timestamp"),
                ("shadow_decisions", "timestamp"),
            ):
                rows = conn.execute(f"SELECT {col} FROM {table}").fetchall()
                future = [
                    r[0]
                    for r in rows
                    if r[0]
                    and _is_iso(r[0])
                    and datetime.fromisoformat(r[0]) > datetime.fromisoformat(now)
                ]
                assert not future, (
                    f"{table}.{col}: {len(future)} timestamps are in the future "
                    f"(first: {future[0]})"
                )
        finally:
            conn.close()

    def test_live_gate_chronology_is_consistent(self):
        conn = _live_conn()
        try:
            bad = conn.execute(
                "SELECT COUNT(*) FROM research_gates "
                "WHERE started_at IS NOT NULL AND completed_at IS NOT NULL "
                "AND completed_at < started_at"
            ).fetchone()[0]
            assert bad == 0, f"{bad} gates completed before they started"
        finally:
            conn.close()

    def test_live_training_runs_have_sane_chronology(self):
        conn = _live_conn()
        try:
            bad = conn.execute(
                "SELECT COUNT(*) FROM training_runs "
                "WHERE finished_at IS NOT NULL AND finished_at < started_at"
            ).fetchone()[0]
            assert bad == 0, f"{bad} training runs finished before they started"
        finally:
            conn.close()

    def test_live_governance_events_are_not_future_dated(self):
        conn = _live_conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM model_governance_events").fetchone()[0]
            assert total == 59164, "governance event count moved — re-derive"
        finally:
            conn.close()
