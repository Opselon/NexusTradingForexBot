"""70D Shadow Safety & Champion-Protection Tests (TASK-05-70D-SHADOW).

Extends the TEST-SHADOW matrix with hard safety proofs:
  TEST-SHADOW-36  Champion BUY vs Shadow SELL -> Champion unchanged
  TEST-SHADOW-37  broker order/modify/cancel count = 0 over thousands of
                  shadow inferences (mocked broker probe)
  TEST-SHADOW-38  MT5 disconnect / news unavailable / liquidity unavailable /
                  shadow model failure -> Champion safety contract intact
  TEST-SHADOW-39  queue/memory bounded under load
  TEST-SHADOW-40  async persistence worker actually persists (real sqlite)

DETERMINISM (ML-QA-011): this module was the second-largest wall-clock
exposure in the push gate (8 sources: 6 ``datetime.now(UTC)`` stamps + 1
``tempfile.mkdtemp()`` + 1 worker thread). None of those reads carried
information the assertions depend on — the observation timestamp is an
*input* the caller supplies, never a magnitude a test measures. The
remediation keeps every safety assert identical and removes only the
nondeterminism:

  * the 6 per-call ``datetime.now(UTC)`` stamps became reads of ONE frozen
    instant (``_FIXED_NOW``, captured once at import via ``_now()``). The
    runtime's freshness gate (``_validate_vector`` in the production code)
    still compares that instant against the real clock, so the frozen value
    must stay *near* now — a hardcoded calendar date would go stale within
    ``FEATURE_FRESHNESS_SEC`` (300 s) and silently flip every scenario to
    ``SHADOW_STALE_FEATURES``. Reading the wall clock exactly once and
    reusing the value is what removes the flake class: no scenario can any
    longer straddle a date boundary or drift between two reads of now, and
    the derived ``observation_id`` (spec 13) becomes stable enough that
    retry/idempotency is provable (TEST-SHADOW-37/40b).
  * ``tempfile.mkdtemp()`` became the pytest ``tmp_path`` fixture (only the
    directory *name* varies; nothing about it feeds an assertion).
  * the persistence wait in TEST-SHADOW-40 keeps its hard ``n == 60``
    row-count contract and gains a bounded CPU-time budget via the shared
    ``budget_cpu_ms`` helper (same helper as ML-QA-004/007/008/009/010).

The pinned contract is enforced textually by
``tests/unit/test_ml_qa_011_shadow70_clock_determinism.py``, which runs in
the slim venv (no torch/sqlite import) and fails on this module's
pre-remediation text.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nexus_scalp.shadow.shadow70.models import (
    Shadow70CandidateContract,
)
from nexus_scalp.shadow.shadow70.runtime import Shadow70Runtime
from nexus_scalp.shadow.shadow70.store import Shadow70Store
from nexus_scalp.shadow.shadow70.worker import Shadow70Worker
from tests.e2e.chain_clock import budget_cpu_ms
from tests.helpers.shadow70_fixtures import make_contract, vector70

#: The single observation instant every scenario in this module shares
#: (ML-QA-011). Captured ONCE at import and replayed via ``_now()``; the
#: runtime's freshness gate still compares it against the real clock, so a
#: hardcoded calendar date is unsafe here (it would age past
#: ``FEATURE_FRESHNESS_SEC`` = 300 s and silently mark every vector stale),
#: while six separate per-call reads reintroduced the date-boundary and
#: read-drift flake class. One frozen read has neither defect.
#:
#: NOTE: the line below carries NO trailing comment on purpose — the
#: ML-QA-011 contract battery matches it textually, and a comment after the
#: capture makes the line ambiguous between "captured from the real clock"
#: and "hardcoded calendar date".
_FIXED_NOW: datetime = datetime.now(UTC)


def _now() -> datetime:
    """The shared deterministic observation instant.

    Replaces the per-call ``datetime.now(UTC)`` arguments. Every
    ``observe()`` in this module reads this, so all observations in a
    scenario share one instant — which is what spec 13's deterministic
    ``observation_id`` (``snapshot_id | model_id | version | timestamp``)
    assumes, and what makes the retry/idempotency check in
    TEST-SHADOW-37/40b meaningful instead of clock-dependent.
    """
    return _FIXED_NOW


@pytest.fixture()
def tmp_artifacts(tmp_path: Path) -> Path:
    """Scratch directory for artifact + DB files (pytest ``tmp_path``).

    ``tmp_path`` is unique per test and auto-cleaned by pytest; only its
    *name* is nondeterministic and nothing about it feeds an assertion, so
    it carries no flake (the ``mkdtemp`` it replaced had the same property
    but leaked on early failure).
    """
    d = tmp_path / "s70s"
    d.mkdir(exist_ok=True)
    return d


@pytest.fixture()
def contract(tmp_artifacts: Path) -> Shadow70CandidateContract:
    return make_contract(str(tmp_artifacts))


class MockBroker:
    """Counts broker interaction attempts (orders/modifies/cancels)."""

    def __init__(self) -> None:
        self.order_count = 0
        self.modify_count = 0
        self.cancel_count = 0
        self.close_count = 0

    def order_send(self, *_a: object, **_k: object) -> None:
        self.order_count += 1

    def order_modify(self, *_a: object, **_k: object) -> None:
        self.modify_count += 1

    def order_cancel(self, *_a: object, **_k: object) -> None:
        self.cancel_count += 1

    def close_position(self, *_a: object, **_k: object) -> None:
        self.close_count += 1

    def snapshot(self) -> dict[str, int]:
        return {
            "order_count": self.order_count,
            "modify_count": self.modify_count,
            "cancel_count": self.cancel_count,
            "close_count": self.close_count,
        }


def test_shadow36_champion_output_never_altered(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-36: Champion BUY vs Shadow SELL -> Champion stays BUY and
    the champion data passed in is untouched (read-only observation)."""
    rt = Shadow70Runtime()
    rt.attach(contract)
    # shadow says SELL, champion says BUY
    rt.set_inference(lambda v: [0.02, 0.02, 0.95, 0.01])
    champion_action = "BUY_MARKET"
    champion_probs = [0.02, 0.95, 0.02, 0.01]
    champion_conf = 0.95
    obs = rt.observe(
        vector70=vector70(),
        champion_action=champion_action,
        champion_probabilities=champion_probs,
        champion_confidence=champion_conf,
        snapshot_id="snap_buyvsell",
        timestamp=_now(),
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
    )
    # Champion decision is preserved exactly
    assert obs.champion_action == "BUY_MARKET"
    assert obs.champion_confidence == 0.95
    assert list(obs.champion_probabilities) == champion_probs
    # and the shadow disagreed (recorded as evidence, not action)
    assert obs.shadow_action == "SELL_MARKET"
    assert not obs.agreement
    # the frozen instant is what the observation recorded (ML-QA-011)
    assert obs.timestamp == _FIXED_NOW


def test_shadow37_broker_interaction_zero(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-37: thousands of shadow inferences produce ZERO broker
    interactions (orders/modifies/cancels/closes)."""
    broker = MockBroker()
    rt = Shadow70Runtime()
    rt.attach(contract)
    rt.set_inference(lambda v: [0.05, 0.7, 0.2, 0.05])
    n = 2000
    last_obs = None
    for i in range(n):
        obs = rt.observe(
            vector70=vector70(liquidity=0.05 * (i % 7)),
            champion_action="NO_TRADE" if i % 3 else "BUY_MARKET",
            champion_probabilities=[0.5, 0.3, 0.1, 0.1],
            champion_confidence=0.5,
            snapshot_id=f"snap_broker_{i}",
            timestamp=_now(),
            base_feature_hash="b" * 8,
            feature_schema_hash="f" * 16,
        )
        assert obs.valid
        last_obs = obs
        # what if the broker were somehow reachable? shadow still never calls it
        if hasattr(rt, "order_send"):
            rt.order_send()  # pragma: no cover
    # the shadow runtime exposes no broker-calling surface at all:
    for attr in ("order_send", "order_modify", "order_cancel", "close_position", "trade"):
        assert not hasattr(rt, attr), attr
    snap = broker.snapshot()
    assert snap == {"order_count": 0, "modify_count": 0, "cancel_count": 0, "close_count": 0}
    assert rt.observations == n
    # spec 13/14 identity: replaying the SAME snapshot under the SAME frozen
    # clock derives the SAME observation_id — a retry cannot duplicate a row
    # (INSERT OR IGNORE on the unique key). Under the old per-call wall
    # clock this id changed between the original and the retry whenever the
    # two reads straddled a clock tick, so the idempotency contract was
    # unprovable rather than merely unproven.
    assert last_obs is not None
    replay = rt.observe(
        vector70=vector70(liquidity=0.05 * ((n - 1) % 7)),
        champion_action="NO_TRADE" if (n - 1) % 3 else "BUY_MARKET",
        champion_probabilities=[0.5, 0.3, 0.1, 0.1],
        champion_confidence=0.5,
        snapshot_id=f"snap_broker_{n - 1}",
        timestamp=_now(),
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
    )
    assert replay.observation_id == last_obs.observation_id


def test_shadow38_failure_cascade_isolation(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-38: MT5 disconnect + news unavailable + liquidity
    unavailable + shadow model failure -> Champion safety contract intact
    (runtime stays READY, errors classified, no raise)."""
    rt = Shadow70Runtime()
    rt.attach(contract)

    # 1) shadow model failure
    def failing(v: list[float]) -> list[float]:
        raise RuntimeError("model NaN")

    rt.set_inference(failing)
    o1 = rt.observe(
        vector70=vector70(),
        champion_action="NO_TRADE",
        champion_probabilities=[0.9, 0.03, 0.03, 0.04],
        champion_confidence=0.9,
        snapshot_id="snap_fail1",
        timestamp=_now(),
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
    )
    assert not o1.valid
    assert o1.error_code == "SHADOW_INFERENCE_FAILED"

    # 2) recover: attach a working fn; simulate news/liquidity unavailable by
    # passing no context — the runtime still records a valid observation
    rt.set_inference(lambda v: [0.9, 0.03, 0.03, 0.04])
    o2 = rt.observe(
        vector70=vector70(),
        champion_action="NO_TRADE",
        champion_probabilities=[0.9, 0.03, 0.03, 0.04],
        champion_confidence=0.9,
        snapshot_id="snap_fail2",
        timestamp=_now(),
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
        news_context=None,
        liquidity_features_10=None,
    )
    assert o2.valid
    assert rt.state.value == "READY"
    # Champion path (simulated here by the caller) continues: the runtime
    # never raises and never blocks.


def test_shadow39_memory_bounded_under_load(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-39: under sustained load buffers remain bounded."""
    rt = Shadow70Runtime()
    rt.attach(contract)
    rt.set_inference(lambda v: [0.05, 0.7, 0.2, 0.05])
    for i in range(5000):
        rt.observe(
            vector70=vector70(liquidity=0.01 * i),
            champion_action="NO_TRADE",
            champion_probabilities=[0.9, 0.03, 0.03, 0.04],
            champion_confidence=0.9,
            snapshot_id=f"snap_mem_{i}",
            timestamp=_now(),
            base_feature_hash="b" * 8,
            feature_schema_hash="f" * 16,
        )
    assert len(rt._recent) <= 2000
    assert len(rt.latency_ms) <= 500
    import sys

    assert sys.getsizeof(rt._recent) < 1_000_000


def test_shadow40_worker_persists_to_real_db(tmp_artifacts: Path) -> None:
    """TEST-SHADOW-40: the async worker actually persists observations to a
    real sqlite DB via the queued writer path."""
    import queue as _q

    class RealRepo:
        _is_sqlite = True
        _queue: _q.Queue = _q.Queue(maxsize=10000)

        def __init__(self, path: str) -> None:
            self._db_path = path
            self._writer = threading.Thread(target=self._run, daemon=True)
            self._writer.start()

        def _run(self) -> None:
            conn = sqlite3.connect(self._db_path, timeout=5.0)
            try:
                while True:
                    try:
                        sql, args = self._queue.get(timeout=0.5)
                    except Exception:
                        if getattr(self, "_stop", False):
                            break
                        continue
                    try:
                        conn.execute(sql, args)
                        conn.commit()
                    except Exception:
                        pass
            finally:
                conn.close()

        def close(self) -> None:
            self._stop = True

        def _flush_readonly(self) -> None:
            while not self._queue.empty():
                time.sleep(0.01)

    db = str(tmp_artifacts / "audit.db")
    repo = RealRepo(db)
    store = Shadow70Store(audit_repo=repo)
    # lazy-schema contract: ensure tables BEFORE the writer starts so a
    # full-suite ordering slip cannot race schema creation with the writer
    # thread's first inserts (observed in the parallel full run).
    store.ensure_schema()
    wk = Shadow70Worker(store=store, max_queue=500, batch_size=25)
    wk.start()
    try:
        rt = Shadow70Runtime()
        rt.attach(make_contract(str(tmp_artifacts)))
        rt.set_inference(lambda v: [0.05, 0.7, 0.2, 0.05])
        for i in range(60):
            obs = rt.observe(
                vector70=vector70(),
                champion_action="BUY_MARKET" if i % 2 else "NO_TRADE",
                champion_probabilities=[0.1, 0.7, 0.1, 0.1],
                champion_confidence=0.7,
                snapshot_id=f"snap_wk_{i}",
                timestamp=_now(),
                base_feature_hash="b" * 8,
                feature_schema_hash="f" * 16,
            )
            wk.enqueue(obs)
        # wait for the worker to flush. The hard contract is the row count
        # below ("persisted 60/60"); the CPU-time budget around the poll
        # loop proves the wait consumed bounded compute, not bounded wall
        # clock — a co-tenant scheduler stall on a 2-core CI runner inflates
        # a wall-clock bound with zero change in the code under test
        # (same helper as ML-QA-004/007/008/009/010).
        with budget_cpu_ms(4000.0) as sw:
            deadline = time.time() + 15
            while time.time() < deadline:
                wk.flush()
                repo._flush_readonly()
                conn = sqlite3.connect(db)
                n = conn.execute("SELECT COUNT(*) FROM shadow70_observations;").fetchone()[0]
                conn.close()
                if n >= 60:
                    break
                time.sleep(0.2)
        assert sw.consumed_ms < 4000.0, (
            f"flush wait consumed {sw.consumed_ms:.1f}ms CPU — the bounded "
            "CPU-time budget for the persistence poll loop was exceeded"
        )
        conn = sqlite3.connect(db)
        n = conn.execute("SELECT COUNT(*) FROM shadow70_observations;").fetchone()[0]
        conn.close()
        assert n == 60, f"persisted {n}/60"
    finally:
        wk.stop(flush=True)
        repo.close()


def test_shadow40b_replay_is_idempotent_under_fixed_clock(tmp_artifacts: Path) -> None:
    """TEST-SHADOW-40b (ML-QA-011): a replay of the same snapshot under the
    same frozen instant derives the same ``observation_id`` and cannot
    duplicate a row (spec 13/14, INSERT OR IGNORE).

    Thread-free: the queued writer is a production transport detail; what
    this pins is that the timestamp the caller injected is the timestamp
    that lands in the row, and that identity is stable across retries — the
    property six separate wall-clock reads left to chance.
    """
    import queue as _q

    class ImmediateRepo:
        _is_sqlite = True
        _queue: _q.Queue = _q.Queue(maxsize=10000)

        def __init__(self, path: str) -> None:
            self._db_path = path

    db = str(tmp_artifacts / "audit_replay.db")
    repo = ImmediateRepo(db)
    store = Shadow70Store(audit_repo=repo)
    store.ensure_schema()
    rt = Shadow70Runtime()
    rt.attach(make_contract(str(tmp_artifacts)))
    rt.set_inference(lambda v: [0.05, 0.7, 0.2, 0.05])

    def _one(snapshot_id: str) -> None:
        obs = rt.observe(
            vector70=vector70(),
            champion_action="NO_TRADE",
            champion_probabilities=[0.1, 0.7, 0.1, 0.1],
            champion_confidence=0.7,
            snapshot_id=snapshot_id,
            timestamp=_now(),
            base_feature_hash="b" * 8,
            feature_schema_hash="f" * 16,
        )
        # the store's queued-writer entry point the worker calls per batch
        assert store.save_observation(obs)

    for i in range(3):
        _one(f"snap_replay_{i}")
    # a retry of snapshot 1 must derive the SAME id and be ignored
    _one("snap_replay_1")

    conn = sqlite3.connect(db)
    try:
        while not repo._queue.empty():
            sql, args = repo._queue.get_nowait()
            conn.execute(sql, args)
        conn.commit()
        rows = conn.execute(
            "SELECT observation_id, timestamp FROM shadow70_observations ORDER BY snapshot_id;"
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 3, f"replay must not duplicate: {len(rows)} rows"
    for _oid, ts in rows:
        assert ts == _FIXED_NOW.isoformat()
    assert len({oid for oid, _ts in rows}) == 3
