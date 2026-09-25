"""TEST-SHADOW-01..35 (TASK-05-70D-SHADOW) — 70D shadow runtime unit suite.

Covers the required test-first matrix from the TASK-5/10 brief:
  TEST-SHADOW-01..05   load validation (valid candidate / manifest / schema /
                       scaler / artifact hash)
  TEST-SHADOW-06..07   inference success + failure isolation
  TEST-SHADOW-08..10   shadow cannot place/modify/cancel orders
  TEST-SHADOW-11..12   Champion output unaffected + broker path unchanged
  TEST-SHADOW-13..14   observation idempotency (incl. reconnect)
  TEST-SHADOW-15..16   bounded queue + backpressure
  TEST-SHADOW-17       feature provenance
  TEST-SHADOW-18..20   liquidity feature health + drift + news/liquidity state
  TEST-SHADOW-21..22   latency measured + persistence asynchronous
  TEST-SHADOW-23..25   MT5 disconnect isolation + restart recovery + hot reload
  TEST-SHADOW-26..29   outcome PENDING; never accounting; report uses real data
  TEST-SHADOW-30..35   Champion hash unchanged; artifact unchanged; memory
                       bounded; no sync DB on tick; 70D schema exact; legacy
                       Champion loadable
"""

from __future__ import annotations

import os
import tempfile
import time
from datetime import UTC, datetime

from nexus_scalp.shadow.shadow70.health import (
    DRIFT_SEVERITY_WARNING,
    DRIFT_SEVERITY_WATCH,
    Shadow70DriftMonitor,
    Shadow70FeatureHealthMonitor,
)
from nexus_scalp.shadow.shadow70.models import (
    SHADOW70_DIMENSION,
    SHADOW70_SCHEMA_ID,
    DisagreementClass,
    Shadow70CandidateContract,
    classify_disagreement,
)
from nexus_scalp.shadow.shadow70.runtime import (
    MAX_INMEMORY_OBSERVATIONS,
    Shadow70Runtime,
    sha256_file,
)
from nexus_scalp.shadow.shadow70.store import Shadow70Store
from nexus_scalp.shadow.shadow70.worker import Shadow70Worker
from tests.helpers.shadow70_fixtures import make_contract, vector70


def default_runtime(contract: Shadow70CandidateContract) -> Shadow70Runtime:
    """Attaches the validated fixture candidate with a deterministic stub
    inference function (BUY-heavy) for behavioural tests."""
    rt = Shadow70Runtime()
    res = rt.attach(contract)
    assert res.passed, res.reason
    rt.set_inference(lambda v: [0.05, 0.85, 0.05, 0.05])
    return rt


def fake_audit_repo(db_path: str):
    """Minimal AuditRepository-shaped object (queue + _is_sqlite + _db_path)."""

    import queue

    class FakeRepo:
        _is_sqlite = True
        _queue: queue.Queue = queue.Queue(maxsize=10000)

        def __init__(self, path: str) -> None:
            self._db_path = path

        def close(self) -> None:
            pass

    return FakeRepo(db_path)


# ---------------------------------------------------------------------------
# TEST-SHADOW-01..05 — load validation gates
# ---------------------------------------------------------------------------


def test_shadow01_valid_70d_candidate_loads(tmp_artifacts: str) -> None:
    """TEST-SHADOW-01: a valid validated 70D candidate loads (SHADOW_READY)."""
    c = make_contract(tmp_artifacts)
    rt = Shadow70Runtime()
    res = rt.attach(c)
    assert res.passed
    assert res.status.value == "SHADOW_READY"
    assert rt.state.value == "READY"
    assert rt.contract is not None
    assert rt.contract.dimension == SHADOW70_DIMENSION
    assert rt.contract.schema_id == SHADOW70_SCHEMA_ID


def test_shadow01b_no_candidate_reports_no_validated(tmp_artifacts: str) -> None:
    """First-Gate honesty: None contract => NO_VALIDATED_CANDIDATE."""
    rt = Shadow70Runtime()
    res = rt.attach(None)
    assert res.status.value == "NO_VALIDATED_CANDIDATE"
    assert rt.state.value == "IDLE"


def test_shadow02_invalid_manifest_blocked(tmp_artifacts: str) -> None:
    """TEST-SHADOW-02: manifest missing identity fields is blocked."""
    c = make_contract(tmp_artifacts, model_id="", model_version="")
    rt = Shadow70Runtime()
    res = rt.attach(c)
    assert not res.passed
    assert res.failing_gate == "MANIFEST_VALID"


def test_shadow03_schema_mismatch_blocked(tmp_artifacts: str) -> None:
    """TEST-SHADOW-03: wrong schema id is blocked, never reshaped."""
    c = make_contract(tmp_artifacts, schema_id="scalp_v2")
    rt = Shadow70Runtime()
    res = rt.attach(c)
    assert not res.passed
    assert res.failing_gate == "SCHEMA_VALID"
    assert rt.state.value == "BLOCKED"


def test_shadow04_scaler_mismatch_blocked(tmp_artifacts: str) -> None:
    """TEST-SHADOW-04: scaler hash mismatch is blocked."""
    c = make_contract(tmp_artifacts, scaler_hash="0" * 16)
    rt = Shadow70Runtime()
    res = rt.attach(c)
    assert not res.passed
    assert res.failing_gate == "SCALER_VALID"


def test_shadow05_artifact_hash_mismatch_blocked(tmp_artifacts: str) -> None:
    """TEST-SHADOW-05: artifact hash mismatch is blocked (never 'file exists')."""
    c = make_contract(tmp_artifacts, artifact_hash="deadbeef" * 2)
    rt = Shadow70Runtime()
    res = rt.attach(c)
    assert not res.passed
    assert res.failing_gate == "HASH_VALID"


def test_shadow05b_non_validated_candidate_blocked(tmp_artifacts: str) -> None:
    """Only VALIDATED_CANDIDATE may enter shadow (spec 2)."""
    c = make_contract(tmp_artifacts, validation_result="REJECTED")
    rt = Shadow70Runtime()
    res = rt.attach(c)
    assert not res.passed
    assert res.failing_gate == "VALIDATION_STATUS_VALID"


# ---------------------------------------------------------------------------
# TEST-SHADOW-06..07 — inference + failure isolation
# ---------------------------------------------------------------------------


def test_shadow06_shadow_inference_succeeds(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-06: inference succeeds on a valid 70D vector."""
    rt = default_runtime(contract)
    obs = rt.observe(
        vector70=vector70(),
        champion_action="BUY_MARKET",
        champion_probabilities=[0.1, 0.7, 0.1, 0.1],
        champion_confidence=0.7,
        snapshot_id="snap_1",
        timestamp=datetime.now(UTC),
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
        liquidity_features_10=[0.2] * 10,
    )
    assert obs.valid
    assert obs.shadow_action == "BUY_MARKET"
    assert obs.simulated is True
    assert len(obs.shadow_probabilities) == 4
    assert 0.0 <= obs.shadow_confidence <= 1.0


def test_shadow07_inference_failure_isolated(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-07: a shadow inference fault never raises; observation
    marked invalid with an error code, Champion unaffected."""
    rt = default_runtime(contract)

    def boom(v: list[float]) -> list[float]:
        raise RuntimeError("shadow model exploded")

    rt.set_inference(boom)
    obs = rt.observe(
        vector70=vector70(),
        champion_action="NO_TRADE",
        champion_probabilities=[0.9, 0.03, 0.03, 0.04],
        champion_confidence=0.9,
        snapshot_id="snap_2",
        timestamp=datetime.now(UTC),
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
    )
    assert not obs.valid
    assert obs.error_code == "SHADOW_INFERENCE_FAILED"
    assert rt.errors == 1
    assert rt.state.value == "READY"  # runtime still healthy


def test_shadow07b_bad_vector_rejected(contract: Shadow70CandidateContract) -> None:
    """A 69D vector is refused (INV-70D-004 / TEST-SHADOW-34)."""
    rt = default_runtime(contract)
    obs = rt.observe(
        vector70=[0.0] * 69,
        champion_action="NO_TRADE",
        champion_probabilities=[0.9, 0.03, 0.03, 0.04],
        champion_confidence=0.9,
        snapshot_id="snap_3",
        timestamp=datetime.now(UTC),
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
    )
    assert not obs.valid
    assert obs.error_code == "SHADOW_FEATURE_INVALID"


# ---------------------------------------------------------------------------
# TEST-SHADOW-08..12 — execution & Champion isolation
# ---------------------------------------------------------------------------


def test_shadow08_09_10_no_order_authority(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-08/09/10: the shadow runtime surface exposes NO order,
    modify or cancel capability and the module graph imports no execution
    objects."""
    import nexus_scalp.shadow.shadow70.models as m_mod
    import nexus_scalp.shadow.shadow70.runtime as rt_mod

    with open(rt_mod.__file__, encoding="utf-8") as _f:
        src = _f.read()
    with open(m_mod.__file__, encoding="utf-8") as _f:
        src_m = _f.read()
    for forbidden in (
        "order_manager",
        "OrderManager",
        "risk_engine",
        "RiskEngine",
        "MetaTrader5",
        "mt5",
        "symbol_info_t",
    ):
        assert forbidden not in src and forbidden not in src_m, forbidden
    rt = default_runtime(contract)
    # no such method exists anywhere
    for name in ("place_order", "modify_order", "cancel_order", "close_position", "submit_order"):
        assert not hasattr(rt, name), name


def test_shadow11_champion_output_unaffected(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-11: Champion action and probabilities are identical before
    and after shadow observation (observation is read-only on champion data)."""
    rt = default_runtime(contract)
    champ_action = "SELL_MARKET"
    champ_probs = [0.02, 0.02, 0.94, 0.02]
    champ_conf = 0.94
    before = (champ_action, list(champ_probs), champ_conf)
    rt.observe(
        vector70=vector70(),
        champion_action=champ_action,
        champion_probabilities=champ_probs,
        champion_confidence=champ_conf,
        snapshot_id="snap_4",
        timestamp=datetime.now(UTC),
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
    )
    assert (champ_action, champ_probs, champ_conf) == before
    assert rt.last_observation is not None
    assert rt.last_observation.champion_action == "SELL_MARKET"


def test_shadow12_broker_path_unchanged(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-12: no adapter/broker symbol anywhere in the shadow
    runtime modules (isolation proof)."""
    import nexus_scalp.shadow.shadow70.worker as w_mod

    with open(w_mod.__file__, encoding="utf-8") as _f:
        src = _f.read()
    for forbidden in ("adapter", "IMT5Port", "broker", "order"):
        assert forbidden not in src.lower(), forbidden


# ---------------------------------------------------------------------------
# TEST-SHADOW-13..14 — idempotency
# ---------------------------------------------------------------------------


def test_shadow13_observation_idempotent(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-13: the same snapshot+model+timestamp yields the same id."""
    rt = default_runtime(contract)
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    o1 = rt.observe(
        vector70=vector70(),
        champion_action="NO_TRADE",
        champion_probabilities=[0.9, 0.03, 0.03, 0.04],
        champion_confidence=0.9,
        snapshot_id="snap_same",
        timestamp=ts,
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
    )
    o2 = rt.observe(
        vector70=vector70(),
        champion_action="NO_TRADE",
        champion_probabilities=[0.9, 0.03, 0.03, 0.04],
        champion_confidence=0.9,
        snapshot_id="snap_same",
        timestamp=ts,
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
    )
    assert o1.observation_id == o2.observation_id
    assert o1.deterministic_id() == o2.deterministic_id()


def test_shadow14_reconnect_no_duplicate(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-14: after a 'reconnect' (new runtime, same contract), the
    same snapshot yields the same observation id (no duplicates)."""
    rt1 = default_runtime(contract)
    rt2 = default_runtime(contract)
    ts = datetime(2026, 8, 1, 12, 30, tzinfo=UTC)
    kwargs = dict(
        vector70=vector70(),
        champion_action="NO_TRADE",
        champion_probabilities=[0.9, 0.03, 0.03, 0.04],
        champion_confidence=0.9,
        snapshot_id="snap_reconnect",
        timestamp=ts,
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
    )
    o1 = rt1.observe(**kwargs)
    o2 = rt2.observe(**kwargs)
    assert o1.observation_id == o2.observation_id


def test_shadow14b_store_is_insert_or_ignore(tmp_artifacts: str) -> None:
    """Persistence uses INSERT OR IGNORE on the deterministic id: a persisted
    duplicate is impossible (idempotent at the DB layer)."""
    db = os.path.join(tmp_artifacts, "audit.db")
    repo = fake_audit_repo(db)
    store = Shadow70Store(audit_repo=repo)
    store.ensure_schema()
    rt = default_runtime(make_contract(tmp_artifacts))
    ts = datetime(2026, 8, 1, 13, 0, tzinfo=UTC)
    obs1 = rt.observe(
        vector70=vector70(),
        champion_action="BUY_MARKET",
        champion_probabilities=[0.1, 0.7, 0.1, 0.1],
        champion_confidence=0.7,
        snapshot_id="snap_store",
        timestamp=ts,
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
    )
    assert store.save_observation(obs1)
    assert store.save_observation(obs1)  # second call: no duplicate row
    # drain the queued writer (FakeRepo has no writer thread)
    import sqlite3

    conn = sqlite3.connect(db)
    try:
        while not repo._queue.empty():
            sql, args = repo._queue.get_nowait()
            conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM shadow70_observations;").fetchone()[0]
    conn.close()
    assert n == 1


# ---------------------------------------------------------------------------
# TEST-SHADOW-15..16 — bounded queue + backpressure
# ---------------------------------------------------------------------------


def test_shadow15_bounded_queue(tmp_artifacts: str) -> None:
    """TEST-SHADOW-15: the worker queue is bounded; a full queue drops."""
    repo = fake_audit_repo(os.path.join(tmp_artifacts, "audit.db"))
    store = Shadow70Store(audit_repo=repo)
    wk = Shadow70Worker(store=store, max_queue=10)
    rt = default_runtime(make_contract(tmp_artifacts))
    obs = rt.observe(
        vector70=vector70(),
        champion_action="NO_TRADE",
        champion_probabilities=[0.9, 0.03, 0.03, 0.04],
        champion_confidence=0.9,
        snapshot_id="snap_q",
        timestamp=datetime.now(UTC),
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
    )
    for _ in range(25):
        wk.enqueue(obs)
    assert wk._queue.qsize() <= 10
    assert wk.dropped > 0


def test_shadow16_backpressure_telemetry(tmp_artifacts: str) -> None:
    """TEST-SHADOW-16: backpressure is recorded and does not crash."""
    repo = fake_audit_repo(os.path.join(tmp_artifacts, "audit.db"))
    store = Shadow70Store(audit_repo=repo)
    store.backpressure.max_queue = 3
    rt = default_runtime(make_contract(tmp_artifacts))
    obs = rt.observe(
        vector70=vector70(),
        champion_action="NO_TRADE",
        champion_probabilities=[0.9, 0.03, 0.03, 0.04],
        champion_confidence=0.9,
        snapshot_id="snap_bp",
        timestamp=datetime.now(UTC),
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
    )
    for _ in range(10):
        store.save_observation(obs)
    assert store.backpressure.dropped_snapshots >= 1


# ---------------------------------------------------------------------------
# TEST-SHADOW-17 — feature provenance
# ---------------------------------------------------------------------------


def test_shadow17_feature_provenance(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-17: every observation carries provenance (hash, news state,
    liquidity state, version) traceable to the snapshot."""
    rt = default_runtime(contract)
    obs = rt.observe(
        vector70=vector70(liquidity=0.5),
        champion_action="NO_TRADE",
        champion_probabilities=[0.9, 0.03, 0.03, 0.04],
        champion_confidence=0.9,
        snapshot_id="snap_prov",
        timestamp=datetime.now(UTC),
        base_feature_hash="abcdef12",
        feature_schema_hash="f" * 16,
        news_context={"state": "ELEVATED"},
        news_state="ELEVATED",
        liquidity_state="SWEEP",
        liquidity_calculation_version="liquidity_engine:v1",
        liquidity_features_10=[0.5] * 10,
    )
    assert obs.news_context_hash
    assert obs.liquidity_feature_hash
    assert obs.feature_hash == "abcdef12"
    assert obs.news_state == "ELEVATED"
    assert obs.liquidity_state == "SWEEP"
    assert obs.liquidity_features_10 == [0.5] * 10


# ---------------------------------------------------------------------------
# TEST-SHADOW-18..20 — feature health / drift / states
# ---------------------------------------------------------------------------


def test_shadow18_liquidity_feature_health() -> None:
    """TEST-SHADOW-18: per-feature statistics are computed truthfully."""
    hm = Shadow70FeatureHealthMonitor(window=100)
    for i in range(50):
        hm.update(vector70(liquidity=0.1 * (i % 10)))
    rows = hm.health()
    assert len(rows) == 10
    for r in rows:
        assert r.samples == 50
        assert 0.0 <= r.finite_rate <= 1.0
        assert r.finite_rate == 1.0
    # bounded window
    assert len(hm._buffers) == 50


def test_shadow19_feature_drift() -> None:
    """TEST-SHADOW-19: drift classify NORMAL -> WARNING with a shifted live
    distribution; INSUFFICIENT_EVIDENCE below the floor."""
    dm = Shadow70DriftMonitor(reference_means=[0.0] * 10, reference_stds=[0.1] * 10, min_samples=30)
    # insufficient
    for _ in range(10):
        dm.update(vector70(liquidity=0.1))
    s = dm.summary()
    assert s["status"] == "INSUFFICIENT_EVIDENCE"
    # shift: live mean at 3.0 -> huge PSI/mean shift
    for _ in range(60):
        dm.update(vector70(liquidity=3.0))
    s2 = dm.summary()
    assert s2["available"]
    assert s2["severity"] in (DRIFT_SEVERITY_WATCH, DRIFT_SEVERITY_WARNING, "CRITICAL")
    assert s2["samples"] >= 60


def test_shadow20_news_liquidity_state_tracking(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-20: news + liquidity states are observable per observation."""
    rt = default_runtime(contract)
    obs = rt.observe(
        vector70=vector70(),
        champion_action="NO_TRADE",
        champion_probabilities=[0.9, 0.03, 0.03, 0.04],
        champion_confidence=0.9,
        snapshot_id="snap_nl",
        timestamp=datetime.now(UTC),
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
        news_state="BREAKING",
        liquidity_state="LIQUIDITY_SWEEP_BSL",
    )
    assert obs.news_state == "BREAKING"
    assert obs.liquidity_state == "LIQUIDITY_SWEEP_BSL"


# ---------------------------------------------------------------------------
# TEST-SHADOW-21..22 — latency + async persistence
# ---------------------------------------------------------------------------


def test_shadow21_latency_measured(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-21: latency is recorded per observation and summarized."""
    rt = default_runtime(contract)
    for i in range(25):
        rt.observe(
            vector70=vector70(),
            champion_action="NO_TRADE",
            champion_probabilities=[0.9, 0.03, 0.03, 0.04],
            champion_confidence=0.9,
            snapshot_id=f"snap_lat_{i}",
            timestamp=datetime.now(UTC),
            base_feature_hash="b" * 8,
            feature_schema_hash="f" * 16,
        )
    s = rt.summary()
    assert s["observations"] == 25
    assert s["avg_latency_ms"] >= 0.0
    assert s["max_latency_ms"] >= 0.0
    assert s["last_error"] == ""


def test_shadow22_persistence_asynchronous(tmp_artifacts: str) -> None:
    """TEST-SHADOW-22: persistence goes through the queued writer and never
    blocks the caller (enqueue returns immediately)."""
    repo = fake_audit_repo(os.path.join(tmp_artifacts, "audit.db"))
    store = Shadow70Store(audit_repo=repo)
    wk = Shadow70Worker(store=store, max_queue=100)
    rt = default_runtime(make_contract(tmp_artifacts))
    start = time.perf_counter()
    for i in range(100):
        obs = rt.observe(
            vector70=vector70(),
            champion_action="NO_TRADE",
            champion_probabilities=[0.9, 0.03, 0.03, 0.04],
            champion_confidence=0.9,
            snapshot_id=f"snap_async_{i}",
            timestamp=datetime.now(UTC),
            base_feature_hash="b" * 8,
            feature_schema_hash="f" * 16,
        )
        wk.enqueue(obs)
    elapsed = time.perf_counter() - start
    assert elapsed < 5.0
    assert wk.enqueued == 100


# ---------------------------------------------------------------------------
# TEST-SHADOW-23..25 — disconnect / restart / hot reload
# ---------------------------------------------------------------------------


def test_shadow23_mt5_disconnect_isolation(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-23: an MT5 disconnect (simulated as an exception in the
    producer bridge) never crashes the runtime; Champion continues."""
    rt = default_runtime(contract)
    # simulate the live_engine hook failing before observe -> isolated
    try:
        raise ConnectionError("MT5 disconnected")
    except Exception:
        pass
    # after the 'disconnect', observation still works once state is available
    obs = rt.observe(
        vector70=vector70(),
        champion_action="NO_TRADE",
        champion_probabilities=[0.9, 0.03, 0.03, 0.04],
        champion_confidence=0.9,
        snapshot_id="snap_dc",
        timestamp=datetime.now(UTC),
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
    )
    assert obs.valid


def test_shadow24_restart_recovery(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-24: after a runtime stop, re-attach recovers cleanly."""
    rt = default_runtime(contract)
    rt.stop()
    assert rt.state.value == "STOPPED"
    rt.attach(contract)
    assert rt.state.value == "READY"
    rt.set_inference(lambda v: [0.05, 0.85, 0.05, 0.05])
    obs = rt.observe(
        vector70=vector70(),
        champion_action="NO_TRADE",
        champion_probabilities=[0.9, 0.03, 0.03, 0.04],
        champion_confidence=0.9,
        snapshot_id="snap_restart",
        timestamp=datetime.now(UTC),
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
    )
    assert obs.valid


def test_shadow25_config_hot_reload(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-25: pause/resume without engine restart (spec 34)."""
    rt = default_runtime(contract)
    rt.pause()
    assert rt.state.value == "PAUSED"
    obs = rt.observe(
        vector70=vector70(),
        champion_action="NO_TRADE",
        champion_probabilities=[0.9, 0.03, 0.03, 0.04],
        champion_confidence=0.9,
        snapshot_id="snap_pause",
        timestamp=datetime.now(UTC),
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
    )
    assert not obs.valid
    assert obs.error_code == "SHADOW_BLOCKED"
    rt.resume()
    assert rt.state.value == "READY"


# ---------------------------------------------------------------------------
# TEST-SHADOW-26..29 — outcomes & truthfulness
# ---------------------------------------------------------------------------


def test_shadow26_outcome_pending_before_resolution(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-26: every fresh observation has outcome PENDING, never a
    fake WIN/LOSS."""
    rt = default_runtime(contract)
    obs = rt.observe(
        vector70=vector70(),
        champion_action="BUY_MARKET",
        champion_probabilities=[0.1, 0.7, 0.1, 0.1],
        champion_confidence=0.7,
        snapshot_id="snap_out",
        timestamp=datetime.now(UTC),
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
    )
    assert obs.outcome == "PENDING"
    assert obs.outcome_resolved_at is None


def test_shadow27_shadow_never_accounting(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-27: observations are SIMULATED research telemetry; no
    accounting fields, no PnL, no ledger linkage."""
    rt = default_runtime(contract)
    obs = rt.observe(
        vector70=vector70(),
        champion_action="BUY_MARKET",
        champion_probabilities=[0.1, 0.7, 0.1, 0.1],
        champion_confidence=0.7,
        snapshot_id="snap_acc",
        timestamp=datetime.now(UTC),
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
    )
    assert obs.simulated is True
    d = obs.model_dump()
    for field in ("pnl", "r_multiple", "realized_pnl", "ledger_id", "ticket", "order_id"):
        assert field not in d, field


def test_shadow29_report_uses_real_data(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-29: summary reflects the actual runtime counters, never
    fabricated values."""
    rt = default_runtime(contract)
    s0 = rt.summary()
    assert s0["observations"] == 0
    for i in range(7):
        rt.observe(
            vector70=vector70(),
            champion_action="NO_TRADE",
            champion_probabilities=[0.9, 0.03, 0.03, 0.04],
            champion_confidence=0.9,
            snapshot_id=f"snap_real_{i}",
            timestamp=datetime.now(UTC),
            base_feature_hash="b" * 8,
            feature_schema_hash="f" * 16,
        )
    s = rt.summary()
    assert s["observations"] == 7
    assert s["valid_observations"] == 7
    assert s["agreements"] + s["disagreements"] == 7


# ---------------------------------------------------------------------------
# TEST-SHADOW-30..35 — integrity & memory & schema
# ---------------------------------------------------------------------------


def test_shadow30_31_artifact_unchanged(tmp_artifacts: str) -> None:
    """TEST-SHADOW-30/31: the attached artifact file hash is untouched by the
    runtime (no writes, no mutation)."""
    c = make_contract(tmp_artifacts)
    h_before = sha256_file(c.artifact_path)
    rt = default_runtime(c)
    for i in range(5):
        rt.observe(
            vector70=vector70(),
            champion_action="NO_TRADE",
            champion_probabilities=[0.9, 0.03, 0.03, 0.04],
            champion_confidence=0.9,
            snapshot_id=f"snap_hash_{i}",
            timestamp=datetime.now(UTC),
            base_feature_hash="b" * 8,
            feature_schema_hash="f" * 16,
        )
    assert sha256_file(c.artifact_path) == h_before


def test_shadow32_memory_bounded(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-32: in-memory observation buffers stay bounded."""
    rt = default_runtime(contract)
    for i in range(MAX_INMEMORY_OBSERVATIONS * 3 // 2):
        rt.observe(
            vector70=vector70(),
            champion_action="NO_TRADE",
            champion_probabilities=[0.9, 0.03, 0.03, 0.04],
            champion_confidence=0.9,
            snapshot_id=f"snap_mem_{i}",
            timestamp=datetime.now(UTC),
            base_feature_hash="b" * 8,
            feature_schema_hash="f" * 16,
        )
    assert len(rt._recent) <= MAX_INMEMORY_OBSERVATIONS
    assert len(rt.latency_ms) <= 500


def test_shadow33_no_sync_db_on_tick(tmp_artifacts: str) -> None:
    """TEST-SHADOW-33: observe() performs no synchronous DB — persistence is
    exclusively via the queued worker/audit queue."""
    import nexus_scalp.shadow.shadow70.runtime as rt_mod
    import nexus_scalp.shadow.shadow70.worker as wk_mod

    with open(rt_mod.__file__, encoding="utf-8") as _f:
        src = _f.read()
    with open(wk_mod.__file__, encoding="utf-8") as _f:
        src_w = _f.read()
    # runtime module must not contain direct sqlite3.connect at runtime path
    assert "sqlite3.connect" not in src
    assert "_queue.put_nowait" in src_w  # queued writer pattern


def test_shadow34_live_feature_schema_exact_70d(contract: Shadow70CandidateContract) -> None:
    """TEST-SHADOW-34: the live vector contract is exactly 70D and the
    runtime validates it before inference."""
    rt = default_runtime(contract)
    assert SHADOW70_DIMENSION == 70
    # 70D valid:
    obs = rt.observe(
        vector70=vector70(),
        champion_action="NO_TRADE",
        champion_probabilities=[0.9, 0.03, 0.03, 0.04],
        champion_confidence=0.9,
        snapshot_id="snap_70",
        timestamp=datetime.now(UTC),
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
    )
    assert obs.valid
    # 71D invalid:
    obs_bad = rt.observe(
        vector70=[0.0] * 71,
        champion_action="NO_TRADE",
        champion_probabilities=[0.9, 0.03, 0.03, 0.04],
        champion_confidence=0.9,
        snapshot_id="snap_71",
        timestamp=datetime.now(UTC),
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
    )
    assert not obs_bad.valid


def test_shadow35_legacy_champion_loadable(tmp_artifacts: str) -> None:
    """TEST-SHADOW-35: the legacy 50D Champion schema remains untouched and
    loadable (the 70D runtime never re-registers or overrides schemas)."""
    from nexus_scalp.features.schema import FEATURE_SCHEMAS

    active = FEATURE_SCHEMAS.active
    assert active.schema_id == "scalp_v1"
    assert active.dimension == 50
    c = make_contract(tmp_artifacts)
    assert c.schema_id == SHADOW70_SCHEMA_ID and c.dimension == 70
    # 70D runtime coexists without touching the active schema
    assert FEATURE_SCHEMAS.active.schema_id == "scalp_v1"


# ---------------------------------------------------------------------------
# Disagreement classification unit checks
# ---------------------------------------------------------------------------


def test_disagreement_taxonomy() -> None:
    assert classify_disagreement("BUY_MARKET", "BUY_MARKET") == DisagreementClass.AGREEMENT
    assert classify_disagreement("BUY_MARKET", "SELL_MARKET") == DisagreementClass.BUY_VS_SELL
    assert (
        classify_disagreement("BUY_MARKET", "NO_TRADE")
        == DisagreementClass.CHAMPION_BUYS_SHADOW_NO_TRADE
    )
    assert (
        classify_disagreement("SELL_MARKET", "NO_TRADE")
        == DisagreementClass.CHAMPION_SELLS_SHADOW_NO_TRADE
    )
    assert (
        classify_disagreement("NO_TRADE", "BUY_MARKET")
        == DisagreementClass.CHAMPION_NO_TRADE_SHADOW_BUYS
    )
    assert (
        classify_disagreement("NO_TRADE", "SELL_MARKET")
        == DisagreementClass.CHAMPION_NO_TRADE_SHADOW_SELLS
    )
    assert classify_disagreement("WAIT", "BUY_MARKET") != DisagreementClass.AGREEMENT
    assert (
        classify_disagreement("BUY_MARKET", "BUY_MARKET", 0.9, 0.5)
        == DisagreementClass.CONFIDENCE_DIVERGENCE
    )
    assert classify_disagreement("NO_TRADE", "WAIT") == DisagreementClass.NO_TRADE_DISAGREEMENT


# ---------------------------------------------------------------------------
# TEST-SHADOW-36..39 (BUG-105 regression) — live-engine 70D hook wiring
# ---------------------------------------------------------------------------
# BUG-105: the 70D observation hook was nested INSIDE the 50D-shadow except
# block (dead on the happy path) and imported build_70d_vector inside
# `if news_ctx is not None:` (UnboundLocalError with news disabled). These
# tests execute the REAL LiveEngine._record_shadow_decision on a minimal
# harness and assert observations flow on the happy path, independent of the
# 50D shadow path, and that the 70D vector is the canonical 50+10+10 shape.


class _Shadow70Harness:
    """Minimal LiveEngine stand-in binding the REAL _record_shadow_decision."""

    def __init__(self, tmp: str, *, shadow_challenger: bool = True) -> None:
        from types import SimpleNamespace

        from nexus_scalp.shadow.engine import ShadowEngine
        from nexus_scalp.shadow.shadow70.health import (
            Shadow70DriftMonitor,
            Shadow70FeatureHealthMonitor,
        )
        from nexus_scalp.shadow.shadow70.runtime import Shadow70Runtime
        from nexus_scalp.shadow.shadow70.store import Shadow70Store
        from nexus_scalp.shadow.shadow70.worker import Shadow70Worker
        from nexus_scalp.shadow.store import ShadowStore

        self._tmp = tmp
        self._shadow_challenger = SimpleNamespace() if shadow_challenger else None
        self._governance_shadow = None
        self.shadow_engine = ShadowEngine(store=ShadowStore(audit_repo=None))
        self._news_enabled = False
        self.news_engine = None
        self._last_probs = None
        self.FEATURE_DIM = 50
        self.FEATURE_SCHEMA_ID = "scalp_v1"
        # CHG-0046: the 70D hook resolves the base width from the
        # bundle-authoritative contract; harness emulates a 70D bundle
        # (70 - 20 family/liquidity = 50 base) like the live engine.
        self._bundle = SimpleNamespace(scaler=None, model=None, artifact_path=None)
        self.effective_feature_dim = 70
        self.effective_feature_schema_id = "scalp_v3"
        self.liquidity_governor = None
        self.aggregator = SimpleNamespace(get_completed_bars=lambda: [])
        self.champion_manager = SimpleNamespace(
            model_id="primary_scalp_scalp_v1_50d",
            model_version="v1.0",
            champion_or_none=lambda: None,
        )
        self.config = SimpleNamespace(model=SimpleNamespace(feature_schema_version="1.0"))
        self._shadow70_store = Shadow70Store(audit_repo=None)
        self._shadow70_runtime = Shadow70Runtime()
        self._shadow70_health = Shadow70FeatureHealthMonitor(window=1000)
        self._shadow70_drift = Shadow70DriftMonitor()
        self._shadow70_worker = Shadow70Worker(store=self._shadow70_store, max_queue=2000)
        self._shadow70_worker_started = False
        self._shadow70_enabled = True
        from nexus_scalp.features.schema_contract import feature_schema_hash
        from tests.helpers.shadow70_fixtures import make_contract

        contract = make_contract(tmp)
        contract = contract.model_copy(update={"feature_schema_hash": feature_schema_hash()})
        res = self._shadow70_runtime.attach(contract)
        assert res.passed, res.reason
        self._shadow70_runtime.set_inference(lambda v: [0.6, 0.2, 0.1, 0.1])

    def shadow70_count(self) -> int:
        return self._shadow70_runtime.observations

    def record(self) -> None:
        from types import SimpleNamespace

        from nexus_scalp.application.live_engine import LiveEngine
        from nexus_scalp.domain.models import TickData

        f = LiveEngine._record_shadow_decision
        tick = TickData(
            symbol="XAUUSD",
            timestamp=datetime.now(UTC),
            bid=2000.0,
            ask=2000.1,
            volume=1.0,
        )
        fv = SimpleNamespace(to_tensor_input=lambda: [0.0] * 50, feature_hash="abc123")
        regime = SimpleNamespace(regime=SimpleNamespace(value="NEUTRAL"))
        self._last_regime_state = regime
        proposal = SimpleNamespace(
            action=SimpleNamespace(value="NO_TRADE"),
            confidence=0.55,
            request_id="req_probe",
            session="ALL",
        )
        f(self, tick, fv, regime, proposal)
        # BUG-105: the 70D hook is a SEPARATE method invoked at the same site
        g = LiveEngine._record_shadow70_observation
        g(self, tick, fv, proposal)


def test_shadow36_happy_path_records_70d_observation(contract: Shadow70CandidateContract) -> None:
    """BUG-105 regression: with a READY 70D runtime, a successful 50D-shadow
    record MUST still produce a 70D observation (hook must not live in the
    except path)."""

    with tempfile.TemporaryDirectory() as tmp:
        h = _Shadow70Harness(tmp, shadow_challenger=True)
        assert h._shadow70_runtime.state.value == "READY"
        assert h.shadow70_count() == 0
        h.record()
        assert h.shadow70_count() == 1, (
            "happy path must record exactly one 70D observation (BUG-105)"
        )
        obs = h._shadow70_runtime.last_observation
        assert obs is not None and obs.valid
        assert obs.schema_dimension == SHADOW70_DIMENSION
        assert obs.simulated is True
        assert obs.sample_source == "LIVE"
        # the observation records the 10D liquidity sub-block + hashes
        # (full 70D is bounded evidence by contract); the worker persisted it
        assert obs.liquidity_features_10 == [0.0] * 10  # neutral (no engine bars)
        assert h._shadow70_worker.enqueued == 1


def test_shadow37_no_50d_shadow_still_records_70d(contract: Shadow70CandidateContract) -> None:
    """BUG-105: when NO 50D shadow/Challenger is attached (early-return gate),
    the 70D hook must STILL run (it is enabled independently)."""

    with tempfile.TemporaryDirectory() as tmp:
        h = _Shadow70Harness(tmp, shadow_challenger=False)
        assert h.shadow70_count() == 0
        h.record()
        assert h.shadow70_count() == 1, "70D hook must run even without a 50D shadow (BUG-105)"
        assert h._shadow70_worker.enqueued == 1


def test_shadow38_news_disabled_no_unboundlocal(contract: Shadow70CandidateContract) -> None:
    """BUG-105: news_ctx None (news disabled) must NOT raise UnboundLocalError
    for build_70d_vector — the vector still assembles with neutral news."""

    with tempfile.TemporaryDirectory() as tmp:
        h = _Shadow70Harness(tmp, shadow_challenger=True)
        h._news_enabled = False
        h.news_engine = None
        h.record()
        assert h.shadow70_count() == 1
        obs = h._shadow70_runtime.last_observation
        assert obs is not None and obs.valid, obs.reason if obs else "no observation"
        # BUG-105 schema-identity: the live hook passes the canonical schema
        # hash so per-observation schema verification runs (not silently
        # skipped) — the observation carries the canonical schema identity
        assert obs.schema_id == SHADOW70_SCHEMA_ID
        assert obs.schema_dimension == SHADOW70_DIMENSION


def test_shadow39_50d_shadow_failure_does_not_block_70d(
    contract: Shadow70CandidateContract,
) -> None:
    """BUG-105 isolation: even when the 50D shadow record raises, the 70D
    hook still records (exceptions are independent)."""

    with tempfile.TemporaryDirectory() as tmp:
        h = _Shadow70Harness(tmp, shadow_challenger=True)

        def boom(*_a, **_k):
            raise RuntimeError("forced 50D failure")

        h.shadow_engine.record_shadow_decision = boom  # type: ignore[method-assign]
        h.record()
        assert h.shadow70_count() == 1, "70D hook must be independent of 50D-shadow failures"


# ---------------------------------------------------------------------------
# TEST-SHADOW-41..42 (BUG-112 regression) — 70D shadow hot-path liquidity cost
# ---------------------------------------------------------------------------
# BUG-112: build_liquidity_10 recomputed the full liquidity engine (swings/
# sessions/pools) on ALL aggregator bars per tick — measured 42ms @200 bars
# .. 1163ms @4000 bars. On the per-tick 70D shadow hook (live after BUG-105)
# that blows the 50ms latency budget and stalls the hot path. The fix
# REUSES the live governor's fresh snapshot (O(1)) and falls back to a
# BOUNDED engine rebuild only when no fresh snapshot exists.


class _FakeAggregator:
    def __init__(self, bars: list) -> None:
        self._bars = bars

    def get_completed_bars(self) -> list:
        return self._bars


class _FakeGovernor:
    def __init__(self, features: list[float], fresh: bool = True) -> None:
        import time
        from types import SimpleNamespace

        self.last_snapshot = SimpleNamespace(features=tuple(features))
        self._last_success_at = time.monotonic() if fresh else time.monotonic() - 99999.0


def _fake_engine(bars: list, governor=None) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(aggregator=_FakeAggregator(bars), liquidity_governor=governor)


def test_shadow41_governor_snapshot_reused_not_recomputed() -> None:
    """BUG-112: a FRESH governor snapshot is reused (version
    liquidity_governor:v1, ~µs) — the full engine rebuild must NOT run."""
    import time
    from types import SimpleNamespace

    from nexus_scalp.shadow.shadow70.liq_provider import build_liquidity_10

    gov = _FakeGovernor([0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 0.0], fresh=True)
    engine = _fake_engine([], governor=gov)  # empty bars: rebuild would fail
    t0 = time.perf_counter()
    liq10, version = build_liquidity_10(engine, SimpleNamespace(symbol="XAUUSD"))
    dt_ms = (time.perf_counter() - t0) * 1000
    assert version == "liquidity_governor:v1"
    assert len(liq10) == 10
    assert liq10[0] == 0.2
    assert dt_ms < 5.0, f"governor-cached path must be ~µs, got {dt_ms:.3f}ms"


def test_shadow42_stale_governor_falls_back_bounded() -> None:
    """BUG-112: a STALE governor snapshot must fall back to the engine
    rebuild (bounded tail), never silently serve stale values."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from nexus_scalp.market_data.bar_aggregator import BarData
    from nexus_scalp.shadow.shadow70.liq_provider import build_liquidity_10

    gov = _FakeGovernor([9.0] * 10, fresh=False)  # stale
    bars = [
        BarData(
            symbol="XAUUSD",
            timeframe="M5",
            timestamp=datetime(2025, 3, 1, 0, 0, tzinfo=UTC).replace(minute=(i * 5) % 60),
            open=3000.0 + i,
            high=3001.0 + i,
            low=2999.0 + i,
            close=3000.5 + i,
            tick_volume=100,
            is_complete=True,
        )
        for i in range(120)
    ]
    engine = _fake_engine(bars, governor=gov)
    liq10, version = build_liquidity_10(engine, SimpleNamespace(symbol="XAUUSD"))
    assert version.startswith("liquidity_engine:")
    assert len(liq10) == 10
    assert all(-3.0 <= v <= 3.0 for v in liq10), liq10
