"""70D Shadow Feature Health / Drift / State Suite (TASK-05-70D-SHADOW).

Covers TEST-SHADOW-18/20/22 plus the POST_70D INV-70D-004..006 vector
contract, per-feature health statistics, drift classification
(NORMAL/WATCH/WARNING/CRITICAL), news+liquidity state observability, and the
historical replay parity check (same runtime path vs reference predictions
within tolerance — spec 48).
"""

from __future__ import annotations

import math
import os
import random
import shutil
import tempfile
from datetime import UTC, datetime

import pytest

from nexus_scalp.shadow.shadow70.health import (
    DRIFT_SEVERITY_CRITICAL,
    DRIFT_SEVERITY_NORMAL,
    DRIFT_SEVERITY_WARNING,
    DRIFT_SEVERITY_WATCH,
    Shadow70DriftMonitor,
    Shadow70FeatureHealthMonitor,
    _mean_std,
    _psi,
)
from nexus_scalp.shadow.shadow70.models import (
    LIQUIDITY_FEATURE_NAMES,
    LIQUIDITY_SLICE,
    SHADOW70_DIMENSION,
    Shadow70FeatureProvenance,
)
from nexus_scalp.shadow.shadow70.runtime import Shadow70Runtime
from tests.helpers.shadow70_fixtures import make_contract, vector70


class FakeCandidate:
    pass


@pytest.fixture()
def tmp_artifacts() -> str:
    d = tempfile.mkdtemp(prefix="s70h_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# TEST-SHADOW-18 — liquidity feature health
# ---------------------------------------------------------------------------


def test_health_monitor_statistics_truthful() -> None:
    """Per-feature stats over a bounded window: mean/std/min/max match a
    hand-computed reference for the engineered distribution."""
    hm = Shadow70FeatureHealthMonitor(window=200)
    # liquidity value i -> (i % 5) * 0.5 => distribution {0.0, 0.5, 1.0, 1.5, 2.0}
    for i in range(100):
        hm.update(vector70(liquidity=0.5 * (i % 5)))
    rows = hm.health()
    assert len(rows) == len(LIQUIDITY_FEATURE_NAMES) == 10
    r0 = rows[0]
    assert r0.samples == 100
    assert r0.finite_rate == 1.0
    assert abs(r0.mean - 1.0) < 1e-9
    col = [0.5 * (i % 5) for i in range(100)]
    expected_std = _mean_std(col)[1]
    assert abs(r0.std - expected_std) < 1e-9
    assert r0.min == 0.0 and r0.max == 2.0
    assert r0.zero_rate == pytest.approx(0.2, abs=1e-9)
    # bounded window
    for _ in range(300):
        hm.update(vector70(liquidity=1.0))
    assert len(hm._buffers) == 200


def test_health_monitor_stale_and_missing() -> None:
    """Missing (non-finite) and stale marks are counted honestly."""
    hm = Shadow70FeatureHealthMonitor(window=100)
    for i in range(40):
        vec = vector70(liquidity=0.3)
        if i % 4 == 0:
            vec[LIQUIDITY_SLICE[0]] = float("nan")  # a missing liquidity value
        hm.update(vec, stale=(i % 5 == 0))
    r = hm.health()[0]
    assert r.samples == 40
    assert r.missing_count == 10
    assert r.missing_rate == pytest.approx(0.25)
    assert r.stale_count == 8
    assert r.finite_rate == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# TEST-SHADOW-19 — drift (classification + thresholds)
# ---------------------------------------------------------------------------


def test_drift_severity_classification() -> None:
    """NORMAL -> WATCH -> WARNING -> CRITICAL as the live distribution moves
    away from the reference."""
    d = Shadow70DriftMonitor(
        reference_means=[0.0] * 10,
        reference_stds=[0.1] * 10,
        min_samples=30,
    )
    # NORMAL: live drawn from the SAME N(0, 0.1) as the reference
    rng = random.Random(99)
    for _ in range(60):
        d.update(vector70(liquidity=max(-3.0, min(3.0, rng.gauss(0.0, 0.1)))))
    s = d.summary()
    assert s["severity"] == DRIFT_SEVERITY_NORMAL
    # WATCH+: moderate shift
    d2 = Shadow70DriftMonitor(
        reference_means=[0.0] * 10,
        reference_stds=[0.1] * 10,
        min_samples=30,
    )
    for _ in range(60):
        d2.update(vector70(liquidity=0.4))
    s2 = d2.summary()
    assert s2["severity"] in (DRIFT_SEVERITY_WATCH, DRIFT_SEVERITY_WARNING, DRIFT_SEVERITY_CRITICAL)
    # CRITICAL: extreme shift (3.0 for all 10 liquidity features)
    d3 = Shadow70DriftMonitor(
        reference_means=[0.0] * 10,
        reference_stds=[0.1] * 10,
        min_samples=30,
    )
    for _ in range(60):
        d3.update(vector70(liquidity=3.0))
    s3 = d3.summary()
    assert s3["severity"] == DRIFT_SEVERITY_CRITICAL


def test_drift_insufficient_evidence_floor() -> None:
    """Below the sample floor: INSUFFICIENT_EVIDENCE, never a verdict."""
    d = Shadow70DriftMonitor(reference_means=[0.0] * 10, reference_stds=[0.1] * 10, min_samples=30)
    for _ in range(29):
        d.update(vector70(liquidity=3.0))
    s = d.summary()
    assert s["status"] == "INSUFFICIENT_EVIDENCE"
    assert s["samples"] == 29


def test_psi_math_same_distribution_zero() -> None:
    """PSI of a distribution against itself is ~0 (sanity)."""
    live = [0.1 * (i % 10) for i in range(100)]
    assert abs(_psi(live, live)) < 1e-9


def test_drift_never_auto_acts() -> None:
    """Drift is observational: evaluate() returns alerts and never raises,
    never attaches to anything executory."""
    d = Shadow70DriftMonitor(reference_means=[0.0] * 10, reference_stds=[0.1] * 10, min_samples=5)
    for _ in range(20):
        d.update(vector70(liquidity=3.0))
    alerts = d.evaluate()
    assert isinstance(alerts, list)
    assert all(
        a.severity
        in (
            DRIFT_SEVERITY_NORMAL,
            DRIFT_SEVERITY_WATCH,
            DRIFT_SEVERITY_WARNING,
            DRIFT_SEVERITY_CRITICAL,
        )
        for a in alerts
    )
    # no execution surface
    for attr in ("place", "order", "modify", "cancel"):
        assert not hasattr(d, attr)


# ---------------------------------------------------------------------------
# TEST-SHADOW-20 — news + liquidity state observability (provenance level)
# ---------------------------------------------------------------------------


def test_provenance_records_news_and_liquidity_state() -> None:
    """Shadow70FeatureProvenance carries the full research lineage (spec 41)."""
    ts = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
    p = Shadow70FeatureProvenance(
        symbol="XAUUSD",
        timestamp=ts,
        feature_schema_id="scalp_v3",
        feature_schema_hash="hash123",
        news_state="ELEVATED",
        liquidity_state="SWEEP_BSL",
        liquidity_calculation_version="liquidity_engine:v2",
        news_context_hash="news-hash",
        liquidity_feature_hash="liq-hash",
        base_feature_hash="base-hash",
        regime="TRENDING_UP",
        session="LONDON",
        market_snapshot={"price": 3300.5, "atr": 1.2},
    )
    d = p.model_dump()
    assert d["news_state"] == "ELEVATED"
    assert d["liquidity_state"] == "SWEEP_BSL"
    assert d["liquidity_calculation_version"] == "liquidity_engine:v2"
    assert d["regime"] == "TRENDING_UP"
    assert d["session"] == "LONDON"


# ---------------------------------------------------------------------------
# TEST-SHADOW-22 — persistence via queued writer (with real AuditRepository)
# ---------------------------------------------------------------------------


def test_store_queued_writer_with_audit_repo(tmp_artifacts: str) -> None:
    """The 70D store writes through the AuditRepository queue and rows land
    in the DB after the writer drains — no synchronous DB on the caller."""
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    db = os.path.join(tmp_artifacts, "audit.db")
    repo = AuditRepository(db_url=f"sqlite:///{db}")
    try:
        from nexus_scalp.shadow.shadow70.store import Shadow70Store

        store = Shadow70Store(audit_repo=repo)
        rt = Shadow70Runtime()
        rt.attach(make_contract(tmp_artifacts))
        rt.set_inference(lambda v: [0.05, 0.7, 0.2, 0.05])
        ts = datetime(2026, 8, 1, 14, 0, tzinfo=UTC)
        for i in range(5):
            obs = rt.observe(
                vector70=vector70(),
                champion_action="NO_TRADE",
                champion_probabilities=[0.9, 0.03, 0.03, 0.04],
                champion_confidence=0.9,
                snapshot_id=f"snap_ar_{i}",
                timestamp=ts,
                base_feature_hash="b" * 8,
                feature_schema_hash="f" * 16,
            )
            assert store.save_observation(obs)
        import sqlite3

        # wait for the audit writer thread to drain
        repo._queue.join()
        conn = sqlite3.connect(db)
        try:
            n = conn.execute("SELECT COUNT(*) FROM shadow70_observations;").fetchone()[0]
        finally:
            conn.close()
        assert n == 5
        # disagreement_counts + summary come from real rows
        assert store.summary()["observations"] == 5
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# TEST-SHADOW-48 — historical replay parity (spec 48)
# ---------------------------------------------------------------------------


def test_shadow48_replay_parity_deterministic(tmp_artifacts: str) -> None:
    """The SAME shadow runtime path on historical snapshots reproduces the
    reference probabilities within tolerance (deterministic inference)."""
    # reference: a snapshot of predictions computed at 'research benchmark' time
    rt_ref = Shadow70Runtime()
    rt_ref.attach(make_contract(tmp_artifacts))
    rt_ref.set_inference(lambda v: [0.1, 0.6, 0.2, 0.1])
    reference: list[list[float]] = []
    ts0 = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)
    for i in range(5):
        o = rt_ref.observe(
            vector70=vector70(liquidity=0.15 * i),
            champion_action="NO_TRADE",
            champion_probabilities=[0.9, 0.03, 0.03, 0.04],
            champion_confidence=0.9,
            snapshot_id=f"replay_ref_{i}",
            timestamp=ts0,
            base_feature_hash="b" * 8,
            feature_schema_hash="f" * 16,
        )
        reference.append(o.shadow_probabilities)
    # replay: a fresh runtime (simulating a later 'replay' process) with the
    # same contract produces byte-identical probabilities
    rt_replay = Shadow70Runtime()
    rt_replay.attach(make_contract(tmp_artifacts))
    rt_replay.set_inference(lambda v: [0.1, 0.6, 0.2, 0.1])
    for i in range(5):
        o = rt_replay.observe(
            vector70=vector70(liquidity=0.15 * i),
            champion_action="NO_TRADE",
            champion_probabilities=[0.9, 0.03, 0.03, 0.04],
            champion_confidence=0.9,
            snapshot_id=f"replay_run_{i}",
            timestamp=ts0,
            base_feature_hash="b" * 8,
            feature_schema_hash="f" * 16,
        )
        for ref_p, live_p in zip(reference[i], o.shadow_probabilities, strict=True):
            assert abs(ref_p - live_p) < 1e-9


# ---------------------------------------------------------------------------
# INV-70D-004..006 — vector contract hard checks
# ---------------------------------------------------------------------------


def test_vector70_contract_families() -> None:
    v = vector70()
    assert len(v) == 70
    assert all(math.isfinite(x) for x in v)
    assert all(-3.0 <= x <= 3.0 for x in v)
    assert all(x == 0.0 for x in v[0:50])
    assert all(x == 0.1 for x in v[50:60])
    assert all(x == 0.2 for x in v[60:70])


def test_feature_name_order_matches_slices() -> None:
    """Liquidity feature names map 1:1 to indices 60..69 (INV-70D-003)."""
    assert len(LIQUIDITY_FEATURE_NAMES) == 10
    assert LIQUIDITY_SLICE == (60, 70)
    names = set(LIQUIDITY_FEATURE_NAMES)
    assert {"bsl_distance_atr", "ssl_distance_atr", "liquidity_sweep_state"} <= names
