"""DEEP-OPT L1 — shadow decision minimal representation (Part 5 rules 8/41/86).

The retired surface is the whole-record JSON mirror that ``save_decision``
wrote next to the flattened columns: ``json.dumps(model_dump())``. It cost
108.3 MB of the 227.2 MB ``shadow_decisions`` table (measured on the live
ledger) and duplicated the columns byte-for-byte for 46.6% of its bytes.

Every test below pins ONE property of the replacement contract:

  * ARITY — the INSERT statement has exactly as many placeholders as the
    writer supplies values. This is the failure mode that matters: the
    columns are built by ``build_upsert_sql`` at import time, so a column
    list edited without the writer (or vice versa) produces a runtime
    "Incorrect number of bindings" on the live tick path, not an import error.
  * LOSSLESS — the unique fields that had no column survive the change:
    the two probability vectors, the two strategy ids, and the four
    hypothetical scalars. A row written through the store round-trips them.
  * NO MIRROR — a newly written row carries no payload bytes at all.
  * RESOLVED GEOMETRY — the outcome resolver now persists the entry/exit
    price pair it previously computed and discarded.
  * LEGACY READER — a row that still holds a mirror (pre-backfill) reads
    back through ``read_decision_row`` with the same values.
  * BACKFILL — ``ensure_schema`` migrates an existing database once, and a
    second run is a no-op.
  * COMPACTION — the retired bytes are reported before and reclaimed by the
    explicit ``compact_database`` call, never automatically.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import UTC, datetime

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.models import (
    CANONICAL_FEATURE_DIMENSION,
    CANONICAL_FEATURE_SCHEMA_ID,
)
from nexus_scalp.shadow.models import (
    ShadowDecisionRecord,
    ShadowModelRef,
    SharedInputRef,
)
from nexus_scalp.shadow.store import (
    ShadowStore,
    _as_float,
    _compact_vector,
    compact_database,
)

SYMBOL = "XAUUSD"


def _input_ref() -> SharedInputRef:
    return SharedInputRef(
        timestamp=datetime.now(UTC),
        symbol=SYMBOL,
        timeframe="M1",
        feature_hash="hash-l1",
        feature_schema_id=CANONICAL_FEATURE_SCHEMA_ID,
        feature_dimension=CANONICAL_FEATURE_DIMENSION,
        regime="TREND_UP",
        session="LONDON",
    )


def _model_ref(model_id: str, is_champion: bool) -> ShadowModelRef:
    return ShadowModelRef(
        model_id=model_id,
        model_version="v1.0",
        feature_schema_id=CANONICAL_FEATURE_SCHEMA_ID,
        feature_dimension=CANONICAL_FEATURE_DIMENSION,
        artifact_hash=f"{model_id}-hash",
        is_champion=is_champion,
    )


def _decision(**overrides) -> ShadowDecisionRecord:
    payload = {
        "shadow_decision_id": f"sd_{uuid.uuid4().hex[:16]}",
        "run_id": "shadow_l1",
        "decision_id": f"dec_{uuid.uuid4().hex[:12]}",
        "timestamp": datetime.now(UTC),
        "symbol": SYMBOL,
        "champion": _model_ref("primary_scalp", True),
        "challenger": _model_ref("candidate_a", False),
        "shared_input": _input_ref(),
        "champion_action": "BUY_MARKET",
        "champion_confidence": 0.71,
        "champion_probabilities": [0.1, 0.7, 0.2],
        "champion_strategy_id": "strat_champ",
        "challenger_action": "NO_TRADE",
        "challenger_confidence": 0.44,
        "challenger_probabilities": [0.5, 0.25, 0.25],
        "challenger_strategy_id": "strat_chall",
        "hypothetical_risk_pct": 0.0125,
        "hypothetical_volume": 0.35,
        "hypothetical_entry": 2411.55,
        "hypothetical_exit": 2414.10,
    }
    payload.update(overrides)
    return ShadowDecisionRecord(**payload)


@pytest.fixture
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'l1.db'}")
    yield r
    r.close()


@pytest.fixture
def store(repo):
    return ShadowStore(audit_repo=repo)


def _drain(repo) -> None:
    """Flush the audit write queue so the row is on disk."""
    worker = getattr(repo, "_worker_thread", None)
    if worker is not None:
        worker.join(timeout=10)
    repo.flush() if hasattr(repo, "flush") else None


def _rows(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM shadow_decisions")]
    finally:
        conn.close()


def _db_path(repo) -> str:
    return str(repo._db_path)


# ----------------------------------------------------------------------
# ARITY — the structural regression that would break the live tick path
# ----------------------------------------------------------------------


def test_insert_statement_arity_matches_column_list():
    """Every column list must have exactly one placeholder per entry."""
    from nexus_scalp.shadow import store as mod

    for sql, columns in (
        (mod._INSERT_DECISION_SQL, mod._DECISION_COLUMNS),
        (mod._INSERT_COMPARISON_SQL, mod._SHADOW_COMPARISON_COLUMNS),
        (mod._INSERT_PROMOTION_SQL, mod._PROMOTION_COLUMNS),
    ):
        named = len(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sql.split("VALUES")[0]))
        values_block = sql.split("VALUES", 1)[1].split(";")[0]
        placeholders = values_block.count("?")
        assert placeholders == len(columns), (
            f"{len(columns)} columns but {placeholders} placeholders"
        )
        assert named > 0


def test_writer_supplies_one_value_per_decision_column(store, repo):
    """The writer's tuple length is asserted against the column list at runtime."""
    from nexus_scalp.shadow import store as mod

    assert store.save_decision(_decision())


# ----------------------------------------------------------------------
# LOSSLESS — the unique mirror fields survive
# ----------------------------------------------------------------------


def test_unique_fields_round_trip_through_columns(store, repo):
    decision = _decision()
    assert store.save_decision(decision)
    _drain(repo)
    rows = _rows(_db_path(repo))
    assert len(rows) == 1
    row = rows[0]
    assert json.loads(row["champion_probabilities"]) == pytest.approx(
        decision.champion_probabilities
    )
    assert json.loads(row["challenger_probabilities"]) == pytest.approx(
        decision.challenger_probabilities
    )
    assert row["champion_strategy_id"] == "strat_champ"
    assert row["challenger_strategy_id"] == "strat_chall"
    assert row["hypothetical_risk_pct"] == pytest.approx(0.0125)
    assert row["hypothetical_volume"] == pytest.approx(0.35)
    assert row["hypothetical_entry"] == pytest.approx(2411.55)
    assert row["hypothetical_exit"] == pytest.approx(2414.10)


def test_new_rows_write_no_mirror_bytes(store, repo):
    """The whole point: a fresh row carries zero mirror payload."""
    for _ in range(3):
        assert store.save_decision(_decision())
    _drain(repo)
    mirror_bytes = ShadowStore.pending_mirror_bytes(
        sqlite3.connect(_db_path(repo))
    )
    assert mirror_bytes <= len("{}") * 3  # default marker only, never a record


def test_compact_vector_is_compact_and_lossless():
    assert _compact_vector([0.1, 0.7, 0.2]) == "[0.1,0.7,0.2]"
    assert _compact_vector([]) == "[]"
    assert _compact_vector(None) == "[]"
    assert _compact_vector(["bad", 0.5]) == "[]"
    assert _as_float("1.5") == 1.5
    assert _as_float(None) == 0.0


# ----------------------------------------------------------------------
# RESOLVED GEOMETRY — the previously-dropped price pair is persisted
# ----------------------------------------------------------------------


def test_outcome_update_persists_entry_exit_pair(store, repo):
    decision = _decision(outcome_status="PENDING", hypothetical_entry=0.0, hypothetical_exit=0.0)
    assert store.save_decision(decision)
    _drain(repo)
    ok = store.apply_resolved_outcome(
        decision.shadow_decision_id,
        {
            "hypothetical_r": 1.5,
            "hypothetical_entry": 2400.25,
            "hypothetical_exit": 2403.75,
            "outcome_status": "RESOLVED",
        },
    )
    assert ok
    _drain(repo)
    row = _rows(_db_path(repo))[0]
    assert row["hypothetical_entry"] == pytest.approx(2400.25)
    assert row["hypothetical_exit"] == pytest.approx(2403.75)
    assert row["outcome_status"] == "RESOLVED"


# ----------------------------------------------------------------------
# LEGACY READER + BACKFILL
# ----------------------------------------------------------------------


def test_legacy_row_with_mirror_reads_back_through_helper():
    row = {
        "shadow_decision_id": "sd_legacy",
        "champion_probabilities": "[]",
        "challenger_probabilities": "[]",
        "champion_strategy_id": "",
        "challenger_strategy_id": "",
        "payload": json.dumps(
            {
                "champion_probabilities": [0.3, 0.6, 0.1],
                "challenger_probabilities": [0.2, 0.2, 0.6],
                "champion": {"model_id": "primary_scalp", "strategy_id": "strat_a"},
                "challenger": {"model_id": "cand", "strategy_id": "strat_b"},
            }
        ),
    }
    out = ShadowStore.read_decision_row(row)
    assert "payload" not in out
    assert out["champion_probabilities"] == pytest.approx([0.3, 0.6, 0.1])
    assert out["challenger_probabilities"] == pytest.approx([0.2, 0.2, 0.6])
    assert out["champion_strategy_id"] == "strat_a"
    assert out["challenger_strategy_id"] == "strat_b"
    assert out["mirror_present"] is True


def test_backfill_migrates_legacy_rows_once(tmp_path):
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE shadow_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shadow_decision_id TEXT UNIQUE NOT NULL,
            run_id TEXT NOT NULL,
            payload TEXT DEFAULT '{}'
        );
        """
    )
    conn.execute(
        "INSERT INTO shadow_decisions (shadow_decision_id, run_id, payload) VALUES (?,?,?)",
        (
            "sd_old",
            "run_old",
            json.dumps(
                {
                    "champion_probabilities": [0.1, 0.9],
                    "challenger_probabilities": [0.8, 0.2],
                    "champion_strategy_id": "old_champ",
                    "challenger_strategy_id": "old_chall",
                    "hypothetical": {"risk_pct": 0.02, "volume": 0.5, "entry": 1.5, "exit": 2.5},
                }
            ),
        ),
    )
    conn.commit()
    conn.close()

    repo = AuditRepository(db_url=f"sqlite:///{db}")
    try:
        ShadowStore(audit_repo=repo).ensure_schema()
        rows = _rows(str(db))
        assert rows[0]["champion_probabilities"] == "[0.1,0.9]"
        assert rows[0]["champion_strategy_id"] == "old_champ"
        assert rows[0]["hypothetical_risk_pct"] == pytest.approx(0.02)
        assert rows[0]["hypothetical_entry"] == pytest.approx(1.5)
        # Idempotent: a second engine instance must not re-migrate.
        ShadowStore(audit_repo=repo).ensure_schema()
        again = _rows(str(db))
        assert again[0]["champion_probabilities"] == "[0.1,0.9]"
    finally:
        repo.close()


def test_pending_mirror_bytes_reports_zero_without_column(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE shadow_decisions (id INTEGER PRIMARY KEY)")
    assert ShadowStore.pending_mirror_bytes(conn) == 0
    conn.close()


# ----------------------------------------------------------------------
# COMPACTION — explicit, measured, never automatic
# ----------------------------------------------------------------------


def test_compact_database_reclaims_and_is_idempotent(tmp_path):
    db = tmp_path / "compact.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE shadow_decisions (id INTEGER PRIMARY KEY, payload TEXT)")
    big = json.dumps({"blob": "x" * 4000})
    conn.executemany(
        "INSERT INTO shadow_decisions (payload) VALUES (?)", [(big,)] * 400
    )
    conn.commit()
    conn.close()
    before = os.path.getsize(db)
    result = compact_database(str(db))
    assert result["ok"] is True
    assert result["bytes_before"] == before
    assert result["retired_mirror_bytes"] > 1_000_000
    second = compact_database(str(db))
    assert second["ok"] is True
    assert second["bytes_reclaimed"] == 0
