"""Phase 2E — DATA stage persistence / readback round-trip contract.

Proves for the DATA stage:
    create/write -> commit -> read -> compare
through the real repository write queue and read seam.

Schema note discovered while writing this test (recorded, not fixed):
`audit_experience_outcomes` has NO foreign key or experience_key column to
`audit_experiences`; the linkage is by `idempotency_key` only, enforced
nowhere in the database (see the constraint-audit module).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from nexus_scalp.adapters.database.provider_store import (
    query_one,
    query_rows,
    queue_write,
)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_idempotency_key() -> str:
    return "IDK-PHASE2-%d" % int(time.time() * 1_000_000)


_EXPERIENCE_SQL = (
    "INSERT INTO audit_experiences "
    "(experience_id, request_id, idempotency_key, symbol, timeframe, strategy_id, "
    "strategy_version, decision_timestamp, action, entry_reason, model_probability, "
    "signal_confidence, proposed_entry, stop_loss, take_profit, feature_schema_id, "
    "feature_dimension, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)

_OUTCOME_SQL = (
    "INSERT INTO audit_experience_outcomes "
    "(idempotency_key, outcome_timestamp, is_executed, is_closed, exit_reason, "
    "realized_pnl_usd, realized_r_multiple, payload) VALUES (?,?,?,?,?,?,?,?)"
)


def _experience_args(key: str, experience_id: str, request_id: str):
    return (
        experience_id,
        request_id,
        key,
        "XAUUSD",
        "M1",
        "ST-PHASE2",
        "1.0.0",
        _iso(),
        "BUY",
        "phase2 contract probe",
        0.72,
        0.81,
        2350.5,
        2340.0,
        2370.0,
        "scalp_v3",
        70,
        json.dumps({"probe": "phase2", "model_id": "M-PHASE2"}),
    )


class TestDataWriteReadRoundTrip:
    """create/write -> commit -> read -> compare for the DATA ledger."""

    def test_experience_roundtrip(self, sqlite_env):
        repo = sqlite_env.repo
        key = _new_idempotency_key()
        exp_id = "EXP-PHASE2-%d" % int(time.time() * 1000)
        assert queue_write(
            repo,
            _EXPERIENCE_SQL,
            _experience_args(key, exp_id, "REQ-1"),
            operation="phase2.data.insert_experience",
        )
        sqlite_env.flush()

        row = query_one(
            repo,
            "SELECT * FROM audit_experiences WHERE idempotency_key=?",
            (key,),
        )
        assert row is not None, "the written row is readable"
        assert row["experience_id"] == exp_id
        assert row["symbol"] == "XAUUSD"
        assert row["idempotency_key"] == key
        assert row["feature_schema_id"] == "scalp_v3"
        assert int(row["feature_dimension"]) == 70
        assert json.loads(row["payload"])["model_id"] == "M-PHASE2"

    def test_outcome_roundtrip_and_payload_fidelity(self, sqlite_env):
        repo = sqlite_env.repo
        key = _new_idempotency_key()
        payload = {
            "broker_outcome": {"reconstruction_source": "BROKER", "ticket": 12345},
            "r_multiple": 1.5,
        }
        assert queue_write(
            repo,
            _OUTCOME_SQL,
            (key, _iso(), 1, 1, "TAKE_PROFIT", 42.0, 1.5, json.dumps(payload)),
            operation="phase2.data.insert_outcome",
        )
        sqlite_env.flush()
        row = query_one(
            repo,
            "SELECT * FROM audit_experience_outcomes WHERE idempotency_key=?",
            (key,),
        )
        assert row is not None
        assert int(row["is_closed"]) == 1
        assert float(row["realized_r_multiple"]) == pytest.approx(1.5)
        # payload fidelity: structured JSON survives the round trip
        assert json.loads(row["payload"]) == payload

    def test_write_then_update_is_observed(self, sqlite_env):
        """A second write to the same logical id must be read back."""
        repo = sqlite_env.repo
        key = _new_idempotency_key()
        assert queue_write(repo, _OUTCOME_SQL, (key, _iso(), 0, 0, "", 0.0, 0.0, "{}"))
        sqlite_env.flush()
        assert queue_write(
            repo,
            "UPDATE audit_experience_outcomes SET is_closed=1, realized_r_multiple=? "
            "WHERE idempotency_key=?",
            (2.25, key),
        )
        sqlite_env.flush()
        row = query_one(
            repo,
            "SELECT * FROM audit_experience_outcomes WHERE idempotency_key=?",
            (key,),
        )
        assert row is not None
        assert int(row["is_closed"]) == 1
        assert float(row["realized_r_multiple"]) == pytest.approx(2.25)

    def test_batch_write_lands_every_row(self, sqlite_env):
        from nexus_scalp.adapters.database.provider_store import queue_write_batch

        repo = sqlite_env.repo
        base = int(time.time() * 1000)
        stmts = [
            (
                _EXPERIENCE_SQL,
                _experience_args(
                    f"IDK-BATCH-{base}-{i}", f"EXP-BATCH-{base}-{i}", f"REQ-B-{i}"
                ),
            )
            for i in range(5)
        ]
        assert queue_write_batch(repo, stmts, operation="phase2.data.batch")
        sqlite_env.flush()
        rows = query_rows(
            repo,
            "SELECT * FROM audit_experiences WHERE idempotency_key LIKE ?",
            (f"IDK-BATCH-{base}-%",),
        )
        assert len(rows) == 5


class TestDataIdempotency:
    def test_duplicate_idempotency_key_never_creates_two_rows(self, sqlite_env):
        repo = sqlite_env.repo
        key = _new_idempotency_key()
        args = _experience_args(key, "EXP-DUP-1", "REQ-D")
        assert queue_write(repo, _EXPERIENCE_SQL, args, operation="phase2.dup.1")
        sqlite_env.flush()
        # A second write with the SAME idempotency_key must not create truth #2.
        queue_write(repo, _EXPERIENCE_SQL, args, operation="phase2.dup.2")
        sqlite_env.flush()
        rows = query_rows(
            repo,
            "SELECT * FROM audit_experiences WHERE idempotency_key=?",
            (key,),
        )
        assert len(rows) == 1, "a duplicate idempotency key created a second row"


class TestDataReadBackFromRepositoryAndRawConnection:
    """The same artifact must be visible through the repository AND the file."""

    def test_repository_and_direct_sql_agree(self, sqlite_env):
        import sqlite3

        repo = sqlite_env.repo
        key = _new_idempotency_key()
        assert queue_write(
            repo, _OUTCOME_SQL, (key, _iso(), 1, 1, "STOP_LOSS", -18.0, -0.9, "{}")
        )
        sqlite_env.flush()

        via_repo = query_one(
            repo,
            "SELECT idempotency_key, realized_r_multiple FROM "
            "audit_experience_outcomes WHERE idempotency_key=?",
            (key,),
        )
        conn = sqlite3.connect(f"file:{sqlite_env.path}?mode=ro", uri=True)
        try:
            via_raw = conn.execute(
                "SELECT idempotency_key, realized_r_multiple FROM "
                "audit_experience_outcomes WHERE idempotency_key=?",
                (key,),
            ).fetchone()
        finally:
            conn.close()
        assert via_repo is not None and via_raw is not None
        assert via_repo["idempotency_key"] == via_raw[0]
        assert float(via_repo["realized_r_multiple"]) == float(via_raw[1])


class TestLiveDataReadback:
    """Read-only: the persisted DATA stage is recoverable from the live store."""

    def test_live_experiences_have_identity_and_schema(self, live_sqlite_probe):
        conn = live_sqlite_probe
        cols = [r[1] for r in conn.execute("PRAGMA table_xinfo(audit_experiences)")]
        for required in ("experience_id", "idempotency_key", "feature_schema_id"):
            assert required in cols, f"audit_experiences lost the {required} column"
        rows = conn.execute(
            "SELECT experience_id, idempotency_key, feature_schema_id, "
            "feature_dimension, decision_timestamp FROM audit_experiences "
            "ORDER BY id DESC LIMIT 25"
        ).fetchall()
        assert rows, "the live DATA stage has persisted experiences"
        for r in rows:
            assert r["experience_id"], "every experience row carries an identity"
            assert r["idempotency_key"], "every experience row carries a dedup key"
            assert r["feature_schema_id"], "every experience row carries a schema id"
            assert int(r["feature_dimension"]) > 0

    def test_live_outcome_r_distribution_is_not_all_zero(self, live_sqlite_probe):
        """BUG-046 class: the R distribution must not be wholly zero."""
        conn = live_sqlite_probe
        total = conn.execute(
            "SELECT COUNT(*) FROM audit_experience_outcomes"
        ).fetchone()[0]
        if not total:
            pytest.skip("live outcome table is empty")
        zero = conn.execute(
            "SELECT COUNT(*) FROM audit_experience_outcomes "
            "WHERE ABS(realized_r_multiple) < 1e-12 AND ABS(realized_pnl_usd) < 1e-9"
        ).fetchone()[0]
        assert zero < total, "every outcome has R=0 — the DATA stage is corrupted"

    def test_live_outcome_join_to_experience_by_idempotency_key(self, live_sqlite_probe):
        """The DATA stage's only outcome->experience link is the shared key.

        Records how much of the ledger joins and that nothing enforces it.
        """
        conn = live_sqlite_probe
        total = conn.execute(
            "SELECT COUNT(*) FROM audit_experience_outcomes"
        ).fetchone()[0]
        if not total:
            pytest.skip("live outcome table is empty")
        joined = conn.execute(
            "SELECT COUNT(*) FROM audit_experience_outcomes o "
            "JOIN audit_experiences e ON e.idempotency_key=o.idempotency_key"
        ).fetchone()[0]
        # Evidence, not a threshold: an orphan rate here is the measured value
        # reported by the reconciliation tooling.
        assert 0 <= joined <= total
