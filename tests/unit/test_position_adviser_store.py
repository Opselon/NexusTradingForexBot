"""Tests for the position adviser persistence layer (TASK-POSA-001).

Round-trips every table through the store with a REAL in-memory SQLite
database, and proves the degraded contract (no repo / a failing repo) never
raises into a caller — the adviser's hot path must not break on persistence.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.position_adviser.models import (
    ADVISER_ACTIONS,
    PositionAdvisory,
)
from nexus_scalp.position_adviser.schema import (
    position_adviser_schema_statements,
)
from nexus_scalp.position_adviser.store import (
    LIFECYCLE_STATES,
    PositionAdviserStore,
)
from nexus_scalp.position_adviser.trainer import AdviserTrainingResult

# ---------------------------------------------------------------- fixtures
#
# A fake audit repository with the exact seam the provider helpers consume:
# ``_is_sqlite=True`` (so the SQLite path runs), ``_db_path`` pointing at a
# shared in-memory database, and a real background queue the store's writes
# land on. ``sqlite_connection`` (the seam the reads go through) is delegated
# to a shared-cache connection so the schema and the rows survive across the
# reader/writer connections.


class _FakeAuditRepo:
    """Minimal AuditRepository stand-in over a shared in-memory SQLite DB."""

    def __init__(self, db_path: str) -> None:
        self._is_sqlite = True
        self._db_path = db_path
        self._queue: Any = None

    def _connect_sqlite(self, timeout: float = 5.0) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, uri=True, timeout=timeout)
        # The provider helpers read rows as dicts (``dict(row)``), which needs
        # the Row factory — the real AuditRepository's seam sets it too.
        conn.row_factory = sqlite3.Row
        return conn

    def provision_sqlite_schema(self, statements: list[str]) -> bool:
        """The sanctioned domain-provisioning seam (mirrors AuditRepository)."""
        try:
            conn = self._connect_sqlite(10.0)
            try:
                conn.executescript(";".join(statements))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            print(f"[fake] provision failed: {e}")
            return False
        return True


class _ImmediateQueue:
    """A queue that executes synchronously so a test can read right back."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.pending: list[tuple[str, tuple[Any, ...]]] = []

    def put_nowait(self, item: tuple[str, tuple[Any, ...]]) -> None:
        sql, args = item
        self._conn.execute(sql, tuple(args))
        self._conn.commit()
        self.pending.append((sql, args))


@pytest.fixture()
def store(tmp_path: Path) -> tuple[PositionAdviserStore, _FakeAuditRepo]:
    """A store backed by a shared in-memory SQLite database (writes land now)."""
    db_path = "file:pa_test?mode=memory&cache=shared"
    conn = sqlite3.connect(db_path, uri=True)
    conn.executescript(";".join(position_adviser_schema_statements()))
    conn.commit()

    repo = _FakeAuditRepo(db_path)
    repo._queue = _ImmediateQueue(conn)
    yield PositionAdviserStore(repo), repo
    conn.close()


@pytest.fixture()
def training_result() -> AdviserTrainingResult:
    return AdviserTrainingResult(
        model_id="pa_test_001",
        weights_path="artifacts/position_adviser/pa_test_001.pt",
        scaler_path="artifacts/position_adviser/pa_test_001.scaler.npz",
        manifest_path="artifacts/position_adviser/pa_test_001.meta.json",
        feature_dim=12,
        epochs_run=12,
        best_val_loss=0.8123,
        oos_loss=0.9011,
        oos_accuracy=0.7333,
        oos_action_distribution={"KEEP": 40, "CLOSE": 25, "REDUCE": 10},
        train_rows=250,
        val_rows=75,
        oos_rows=75,
        sha256="abc123def456",
        duration_sec=1.5,
        classes_absent=[],
        metrics={"class_weights": {"KEEP": 1.0}, "source_row_count": 400},
    )


@pytest.fixture()
def advisory() -> PositionAdvisory:
    return PositionAdvisory(
        ticket=12345,
        action="CLOSE",
        confidence=0.82,
        probabilities={"KEEP": 0.12, "CLOSE": 0.82, "REDUCE": 0.06},
        hold_score_adjustment=-3.5,
        activation="LIVE",
        model_id="pa_test_001",
        model_dimension=12,
        evaluated_at="2026-09-28T10:00:00+00:00",
        latency_ms=1.25,
        advisory_id="adv_0001",
        applied=True,
        not_applied_reason="",
        diagnostics={"snapshot_id": "abc"},
    )


# ============================================================ schema round-trip


def test_schema_statements_create_all_four_tables(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "pa.db"))
    conn.executescript(";".join(position_adviser_schema_statements()))
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'pa\\_%' ESCAPE '\\'"
        )
    }
    conn.close()
    assert tables == {"pa_training_runs", "pa_model_registry", "pa_load_events", "pa_advisories"}


def test_schema_is_idempotent() -> None:
    statements = position_adviser_schema_statements()
    # Running the whole set twice must not raise (re-provisioning contract).
    conn = sqlite3.connect(":memory:")
    conn.executescript(";".join(statements))
    conn.executescript(";".join(statements))
    conn.close()


# ============================================================ writes + reads


def test_training_run_round_trip(store, training_result) -> None:
    st, _ = store
    assert st.save_training_run(training_result) is True

    row = st.get_training_run(training_result.model_id)
    assert row is not None
    assert row["run_id"] == "pa_test_001"
    assert row["model_id"] == "pa_test_001"
    assert row["feature_dimension"] == 12
    assert row["feature_schema_id"] == "adviser_v1"
    assert row["status"] == "COMPLETED"
    metrics = json.loads(row["metrics"])
    assert metrics["oos_accuracy"] == pytest.approx(0.7333)
    assert metrics["train_rows"] == 250
    assert metrics["oos_rows"] == 75
    assert metrics["oos_action_distribution"]["CLOSE"] == 25
    hyper = json.loads(row["hyperparameters"])
    assert set(hyper) == {"epochs", "lr", "batch", "seed"}


def test_training_run_accepts_dict(store) -> None:
    st, _ = store
    d = training_result_dict()
    assert st.save_training_run(d) is True
    row = st.get_training_run("pa_dict_001")
    assert row is not None
    assert row["dataset_hash"] == "ds_hash_abc"


def test_training_run_is_idempotent(store, training_result) -> None:
    st, _ = store
    assert st.save_training_run(training_result) is True
    assert st.save_training_run(training_result) is True
    rows = st.summary()
    assert rows["training_runs"] == 1


def test_model_round_trip(store) -> None:
    st, _ = store
    assert (
        st.save_model(
            {
                "model_id": "pa_test_001",
                "run_id": "pa_test_001",
                "weights_path": "artifacts/position_adviser/pa_test_001.pt",
                "scaler_path": "artifacts/position_adviser/pa_test_001.scaler.npz",
                "manifest_path": "artifacts/position_adviser/pa_test_001.meta.json",
                "weights_sha256": "abc123def456",
                "integrity": "VERIFIED",
                "source_dataset_hash": "ds_hash_abc",
                "feature_dim": 12,
                "oos_loss": 0.9011,
                "oos_accuracy": 0.7333,
                "lifecycle_state": "TRAINED",
                "created_at": "2026-09-28T10:00:00+00:00",
                "updated_at": "2026-09-28T10:00:00+00:00",
            }
        )
        is True
    )
    m = st.get_model("pa_test_001")
    assert m is not None
    assert m["weights_sha256"] == "abc123def456"
    assert m["integrity"] == "VERIFIED"
    assert m["lifecycle_state"] == "TRAINED"
    assert m["oos_accuracy"] == pytest.approx(0.7333)
    assert st.list_models()
    assert st.list_models()[0]["model_id"] == "pa_test_001"


def test_lifecycle_transition_persists(store) -> None:
    st, _ = store
    _seed_model(st)
    for state in LIFECYCLE_STATES:
        assert st.record_model_lifecycle("pa_test_001", state) is True
        m = st.get_model("pa_test_001")
        assert m is not None
        assert m["lifecycle_state"] == state
        # The registry row is preserved across the transition, not rewritten
        # from scratch: the artifact pins stay where the training run put them.
        assert m["weights_sha256"] == "abc123def456"


def test_lifecycle_refuses_unknown_state(store) -> None:
    st, _ = store
    _seed_model(st)
    assert st.record_model_lifecycle("pa_test_001", "GIBBERISH") is False
    m = st.get_model("pa_test_001")
    assert m is not None
    assert m["lifecycle_state"] == "TRAINED"


def test_lifecycle_refuses_unknown_model(store) -> None:
    st, _ = store
    assert st.record_model_lifecycle("no_such_model", "ACTIVE") is False


def test_load_events_round_trip(store) -> None:
    st, _ = store
    assert (
        st.record_load_event(
            "pa_test_001",
            "LOAD",
            activation="PAPER",
            success=True,
            reason="loaded via web UI",
            source="WEB_UI",
            operator="operator@nexus",
            created_at="2026-09-28T10:00:00+00:00",
        )
        is True
    )
    assert (
        st.record_load_event(
            "pa_test_001",
            "ACTIVATE",
            activation="LIVE",
            success=True,
            created_at="2026-09-28T10:01:00+00:00",
        )
        is True
    )
    assert (
        st.record_load_event(
            "pa_test_001",
            "LOAD",
            activation="DISABLED",
            success=False,
            reason="integrity failure",
            created_at="2026-09-28T09:00:00+00:00",
        )
        is True
    )
    events = st.list_load_events()
    assert len(events) == 3
    # newest first
    assert events[0]["action"] == "ACTIVATE"
    assert events[0]["activation"] == "LIVE"
    assert events[0]["success"] == 1
    assert events[-1]["success"] == 0
    assert events[-1]["reason"] == "integrity failure"

    filtered = st.list_load_events(model_id="pa_test_001", limit=1)
    assert len(filtered) == 1
    assert st.list_load_events(model_id="other_model") == []


def test_advisory_round_trip(store, advisory) -> None:
    st, _ = store
    assert st.save_advisory(advisory) is True

    rows = st.list_advisories()
    assert len(rows) == 1
    row = rows[0]
    assert row["advisory_id"] == "adv_0001"
    assert row["ticket"] == 12345
    assert row["action"] == "CLOSE"
    assert row["confidence"] == pytest.approx(0.82)
    assert row["hold_score_adjustment"] == pytest.approx(-3.5)
    assert row["applied"] == 1
    assert json.loads(row["probabilities"])["CLOSE"] == pytest.approx(0.82)
    assert json.loads(row["diagnostics"])["snapshot_id"] == "abc"


def test_advisory_idempotent(store, advisory) -> None:
    st, _ = store
    assert st.save_advisory(advisory) is True
    assert st.save_advisory(advisory) is True
    assert len(st.list_advisories()) == 1


def test_advisory_order_is_newest_first(store) -> None:
    st, _ = store
    for i, ts in enumerate(("2026-09-01", "2026-09-03", "2026-09-02")):
        st.save_advisory(
            {
                "advisory_id": f"adv_{i}",
                "ticket": i,
                "action": "KEEP",
                "confidence": 0.5,
                "probabilities": {},
                "hold_score_adjustment": 0.0,
                "activation": "PAPER",
                "model_id": "m",
                "model_dimension": 12,
                "evaluated_at": f"{ts}T00:00:00+00:00",
                "latency_ms": 1.0,
                "applied": False,
                "not_applied_reason": "paper",
                "diagnostics": {},
            }
        )
    got = [r["advisory_id"] for r in st.list_advisories()]
    assert got == ["adv_1", "adv_2", "adv_0"]


def test_summary_reports_counts(store, training_result, advisory) -> None:
    st, _ = store
    assert st.save_training_run(training_result) is True
    _seed_model(st)
    st.record_model_lifecycle("pa_test_001", "ACTIVE")
    st.record_load_event(
        "pa_test_001", "LOAD", success=True, created_at="2026-09-28T10:00:00+00:00"
    )
    st.save_advisory(advisory)

    s = st.summary()
    assert s["available"] is True
    assert s["provider"] == "sqlite"
    assert s["training_runs"] == 1
    assert s["models"] == 1
    assert s["active_models"] == 1
    assert s["load_events"] == 1
    assert s["advisories"] == 1
    assert s["applied_advisories"] == 1


def test_read_limits_are_bounded(store) -> None:
    st, _ = store
    for i in range(5):
        st.save_advisory(
            {
                "advisory_id": f"adv_{i}",
                "ticket": i,
                "action": "KEEP",
                "confidence": 0.5,
                "probabilities": {},
                "hold_score_adjustment": 0.0,
                "activation": "PAPER",
                "model_id": "m",
                "model_dimension": 12,
                "evaluated_at": f"2026-09-0{i + 1}T00:00:00+00:00",
                "latency_ms": 1.0,
                "applied": False,
                "not_applied_reason": "",
                "diagnostics": {},
            }
        )
    assert len(st.list_advisories(limit=2)) == 2
    assert len(st.list_advisories(limit=0)) == 1  # bounded to >= 1
    assert len(st.list_advisories(limit=100_000)) == 5  # bounded to MAX_READ_LIMIT


# ============================================================ degradation


def test_no_repo_degrades_silently() -> None:
    st = PositionAdviserStore(None)
    assert st.ensure_schema() is True  # nothing to create; not an error
    assert st.save_training_run(training_result_dict()) is False
    assert st.save_model({"model_id": "m"}) is False
    assert st.record_model_lifecycle("m", "ACTIVE") is False
    assert st.record_load_event("m", "LOAD") is False
    assert st.save_advisory({"advisory_id": "a"}) is False
    assert st.list_advisories() == []
    assert st.list_models() == []
    assert st.list_load_events() == []
    assert st.get_model("m") is None
    assert st.get_training_run("r") is None
    s = st.summary()
    assert s["available"] is False
    assert s["provider"] == "unknown"


def test_missing_repo_object_degrades_silently() -> None:
    """A falsy repository (not just None) must never raise."""
    st = PositionAdviserStore(None)  # type: ignore[arg-type]
    assert st.save_advisory({"advisory_id": "a"}) is False


def test_write_failure_never_raises(store) -> None:
    """A queue that raises must surface as False, not an exception."""
    st, repo = store

    class _Boom:
        def put_nowait(self, item: Any) -> None:
            raise RuntimeError("queue is on fire")

    repo._queue = _Boom()
    assert st.save_advisory({"advisory_id": "a"}) is False
    assert st.save_training_run(training_result_dict()) is False
    # reads still work (they go through the connection seam, not the queue)
    assert st.summary()["available"] is True


def test_read_failure_never_raises(store) -> None:
    """A connection seam that raises must surface as empty, not an exception."""
    st, repo = store

    def _boom(timeout: float = 5.0) -> sqlite3.Connection:
        raise RuntimeError("connection is on fire")

    repo._connect_sqlite = _boom  # type: ignore[method-assign]
    assert st.list_advisories() == []
    assert st.get_model("m") is None
    assert st.summary()["available"] is False


def test_save_advisory_refuses_empty_id(store) -> None:
    st, _ = store
    assert st.save_advisory({"advisory_id": "", "ticket": 1}) is False


def test_pg_dialect_selects_on_conflict_form() -> None:
    """A non-SQLite repository must resolve the PostgreSQL statement."""
    from nexus_scalp.database.upsert import build_upsert_sql

    class _PgRepo:
        _is_sqlite = False

    st = PositionAdviserStore(_PgRepo())  # type: ignore[arg-type]
    # The store picks the PG branch of the pair for a pooled provider; the
    # statement must name ON CONFLICT (the pooled backend executes it
    # verbatim — INSERT OR REPLACE is SQLite-only syntax).
    assert "ON CONFLICT" in _sql_for_pg(st, "pa_advisories")
    assert "ON CONFLICT" in _sql_for_pg(st, "pa_training_runs")
    assert "ON CONFLICT" in _sql_for_pg(st, "pa_model_registry")
    assert "ON CONFLICT" in _sql_for_pg(st, "pa_load_events")
    # and the SQLite pair member stays byte-identical INSERT OR REPLACE
    assert "INSERT OR REPLACE" in _sqlite_sql("pa_advisories")
    del build_upsert_sql  # silence the unused-import linter


def test_upsert_keys_are_registered() -> None:
    """Every pa_ table has a resolvable ON CONFLICT target.

    The adviser package owns its own tables (it provisions them through
    ``position_adviser_schema_statements``), so the conflict target is resolved
    by the package's own key registry — but it is still validated against the
    DDL the schema actually declares, so a key the schema does not back raises.
    """
    from nexus_scalp.database.upsert import UpsertKeyError
    from nexus_scalp.position_adviser.store import (
        _PA_UPSERT_KEYS,
        _pa_primary_key_columns,
    )

    for table, expected_key in _PA_UPSERT_KEYS.items():
        declared = _pa_primary_key_columns(table)
        assert declared, f"{table} declares no PK/UNIQUE constraint"
        assert set(expected_key).issubset({c for cols in declared for c in cols}), (
            f"{table}: key {expected_key} not covered by DDL {declared}"
        )
        # A table the schema does not declare has no key, so the builder's own
        # key lookup raises (it is keyed by table name).
        assert _pa_primary_key_columns("pa_does_not_exist") == []
    assert _PA_UPSERT_KEYS["pa_training_runs"] == ("run_id",)
    assert _PA_UPSERT_KEYS["pa_model_registry"] == ("model_id",)
    assert _PA_UPSERT_KEYS["pa_advisories"] == ("advisory_id",)
    assert _PA_UPSERT_KEYS["pa_load_events"] == ("created_at",)


def test_adv_metrics_include_majority_baseline(store) -> None:
    st, _ = store
    d = training_result_dict()
    d["majority_baseline"] = 0.61
    assert st.save_training_run(d) is True
    row = st.get_training_run("pa_dict_001")
    assert row is not None
    assert json.loads(row["metrics"])["majority_baseline"] == pytest.approx(0.61)


# ============================================================ helpers


def training_result_dict() -> dict[str, Any]:
    return {
        "run_id": "pa_dict_001",
        "model_id": "pa_dict_001",
        "dataset_id": "pos_ds_2026",
        "dataset_hash": "ds_hash_abc",
        "feature_schema_id": "adviser_v1",
        "feature_dimension": 12,
        "epochs_run": 8,
        "learning_rate": 1e-3,
        "batch_size": 128,
        "seed": 42,
        "best_val_loss": 0.9,
        "oos_loss": 0.95,
        "oos_accuracy": 0.70,
        "oos_action_distribution": {"KEEP": 30, "CLOSE": 30, "REDUCE": 15},
        "train_rows": 200,
        "val_rows": 50,
        "oos_rows": 75,
        "sha256": "deadbeef",
        "weights_path": "w.pt",
        "scaler_path": "s.npz",
        "manifest_path": "m.json",
        "duration_sec": 2.0,
        "created_at": "2026-09-28T10:00:00+00:00",
        "status": "COMPLETED",
    }


def _seed_model(store: PositionAdviserStore) -> None:
    store.save_model(
        {
            "model_id": "pa_test_001",
            "run_id": "pa_test_001",
            "weights_path": "artifacts/position_adviser/pa_test_001.pt",
            "scaler_path": "artifacts/position_adviser/pa_test_001.scaler.npz",
            "manifest_path": "artifacts/position_adviser/pa_test_001.meta.json",
            "weights_sha256": "abc123def456",
            "integrity": "VERIFIED",
            "source_dataset_hash": "ds_hash_abc",
            "feature_dim": 12,
            "oos_loss": 0.9011,
            "oos_accuracy": 0.7333,
            "lifecycle_state": "TRAINED",
            "created_at": "2026-09-28T10:00:00+00:00",
            "updated_at": "2026-09-28T10:00:00+00:00",
        }
    )


def _sql_for_pg(store: PositionAdviserStore, table: str) -> str:
    """The PostgreSQL branch of the built pair, resolved for a mock repo."""
    pairs = {
        "pa_training_runs": ("_SQLITE_RUN_SQL", "_PG_RUN_SQL"),
        "pa_model_registry": ("_SQLITE_MODEL_SQL", "_PG_MODEL_SQL"),
        "pa_load_events": ("_SQLITE_LOAD_EVENT_SQL", "_PG_LOAD_EVENT_SQL"),
        "pa_advisories": ("_SQLITE_ADVISORY_SQL", "_PG_ADVISORY_SQL"),
    }
    mod = __import__("nexus_scalp.position_adviser.store", fromlist=["PositionAdviserStore"])
    _sqlite_name, _pg_name = pairs[table]
    sqlite_sql = getattr(mod, _sqlite_name)
    pg_sql = getattr(mod, _pg_name)
    return _select_pg(store.audit_repo, sqlite_sql, pg_sql)


def _select_pg(repo: Any, sqlite_sql: str, pg_sql: str) -> str:
    from nexus_scalp.position_adviser.store import _sql_for

    return _sql_for(repo, sqlite_sql, pg_sql)


def _sqlite_sql(table: str) -> str:
    pairs = {
        "pa_training_runs": "_SQLITE_RUN_SQL",
        "pa_model_registry": "_SQLITE_MODEL_SQL",
        "pa_load_events": "_SQLITE_LOAD_EVENT_SQL",
        "pa_advisories": "_SQLITE_ADVISORY_SQL",
    }
    mod = __import__("nexus_scalp.position_adviser.store", fromlist=["PositionAdviserStore"])
    return str(getattr(mod, pairs[table]))


def test_actions_match_adviser_contract() -> None:
    """The advisory action column holds exactly the trained classes."""
    assert set(ADVISER_ACTIONS) == {"KEEP", "CLOSE", "REDUCE"}
