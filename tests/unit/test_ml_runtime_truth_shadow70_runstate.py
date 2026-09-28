"""ML runtime truth — Shadow70 store run-state (ML-RUNTIME-TRUTH D2 / Phase 23).

The drift/feature-health panel used to infer "no drift" from empty
collections. That conflates three different facts:

    NO_ALERTS    the monitor ran, compared against a reference, flagged nothing
    NOT_RUN      the store is writable but the monitor never wrote anything
    UNAVAILABLE  the store has no backing repository and cannot answer at all

``Shadow70Store.summary()`` now reports an explicit ``monitor_state`` so the
UI cannot present an unwritten store as a clean bill of health. These tests
pin each branch against a real, isolated SQLite audit database — no mocks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.shadow.shadow70.models import Shadow70Observation
from nexus_scalp.shadow.shadow70.store import Shadow70Store


@pytest.fixture
def audit_repo(tmp_path: Path) -> AuditRepository:
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'audit.db'}")
    yield repo
    with contextlib_suppress():
        repo.close()


def contextlib_suppress():
    import contextlib

    return contextlib.suppress(Exception)


def _store(repo: AuditRepository) -> Shadow70Store:
    return Shadow70Store(audit_repo=repo)


def test_summary_without_repository_is_unavailable(tmp_path: Path) -> None:
    """No backing repo -> the store cannot answer: UNAVAILABLE, not NOT_RUN."""
    summary = Shadow70Store(audit_repo=None).summary()
    assert summary["available"] is False
    assert summary["monitor_state"] == "UNAVAILABLE"


def test_fresh_store_reports_not_run_not_no_alerts(audit_repo: AuditRepository) -> None:
    """A writable but never-written store is NOT_RUN.

    This is the regression: before D2 the panel rendered "No drift alerts: the
    backend has not flagged the 70D feature distribution" for exactly this
    state, which is a claim the empty store cannot support.
    """
    summary = _store(audit_repo).summary()
    assert summary["available"] is True
    assert summary["monitor_state"] == "NOT_RUN"
    assert summary["observations"] == 0
    assert summary["events"] == 0
    assert summary["latest_event_at"] is None


def test_store_with_events_reports_no_alerts(audit_repo: AuditRepository) -> None:
    """Worker activity in the event ledger proves the monitor ran.

    Attach/queue/error events are written even when no observation survives
    comparison, so a single event row is sufficient evidence of a run.
    """
    store = _store(audit_repo)
    store.record_event(
        {
            "event_id": "evt-1",
            "event": "SHADOW70_ATTACHED",
            "stage": "attach",
            "model_id": "scalp_70d_liquidity_scalp_v3_70d",
            "model_version": "v1.0",
            "schema_id": "scalp_v3",
            "error_code": "",
            "reason": "attached",
            "correlation_id": "c-1",
            "payload": "{}",
            "timestamp": "2026-09-28T00:00:00+00:00",
        }
    )
    # SQLite writes land on the repository's background queue; drain it before
    # reading so the summary observes the row we just queued (test_bug278's
    # repo._queue.join() idiom).
    audit_repo._queue.join()
    summary = store.summary()
    assert summary["available"] is True
    assert summary["monitor_state"] == "NO_ALERTS"
    assert summary["events"] == 1
    assert summary["latest_event_at"] == "2026-09-28T00:00:00+00:00"


def test_store_with_drift_alerts_reports_evaluated(audit_repo: AuditRepository) -> None:
    """Alerts present -> EVALUATED, the strongest positive evidence state."""
    store = _store(audit_repo)
    store.record_event(
        {
            "event_id": "evt-1",
            "event": "SHADOW70_ATTACHED",
            "stage": "attach",
            "model_id": "m",
            "model_version": "v1.0",
            "schema_id": "scalp_v3",
            "error_code": "",
            "reason": "attached",
            "correlation_id": "c-1",
            "payload": "{}",
            "timestamp": "2026-09-28T00:00:00+00:00",
        }
    )
    store.save_drift_alerts(
        [
            {
                "alert_id": "a-1",
                "timestamp": "2026-09-28T00:01:00+00:00",
                "feature": "htf_liquidity_score",
                "metric": "psi",
                "value": 0.41,
                "threshold": 0.2,
                "severity": "WARNING",
                "reference_mean": 0.1,
                "live_mean": 0.6,
                "reference_std": 0.05,
                "live_std": 0.07,
                "samples": 100,
                "payload": "{}",
            }
        ]
    )
    audit_repo._queue.join()
    summary = store.summary()
    assert summary["monitor_state"] == "EVALUATED"
    assert summary["drift_alerts"] == 1


def test_observations_alone_prove_a_run(audit_repo: AuditRepository) -> None:
    """A compared observation row is direct evidence of a real run."""
    store = _store(audit_repo)
    obs = Shadow70Observation(
        observation_id="obs-1",
        snapshot_id="snap-1",
        timestamp="2026-09-28T00:00:00+00:00",
        symbol="XAUUSD",
        timeframe="M1",
        simulated=True,
        model_id="scalp_70d_liquidity_scalp_v3_70d",
        model_version="v1.0",
        model_hash="h",
        scaler_hash="s",
        schema_id="scalp_v3",
        schema_dimension=70,
        champion_action="WAIT",
        champion_probabilities=[0.2, 0.3, 0.5],
        champion_confidence=0.5,
        shadow_action="BUY",
        shadow_probabilities=[0.1, 0.2, 0.7],
        shadow_confidence=0.7,
        confidence_delta=0.2,
        disagreement="ACTION_DISAGREEMENT",
        agreement=0,
        valid=1,
        reason="ok",
        regime="RANGE",
        session="ASIA",
        news_state="fresh",
        liquidity_state="ok",
        news_context_hash="n",
        liquidity_feature_hash="l",
        liquidity_features_10=[0.1] * 10,
        feature_hash="f",
        sample_source="live",
        latency_ms=1.5,
        error_code="",
        outcome="PENDING",
        outcome_resolved_at="2026-09-28T00:00:00+00:00",
    )
    assert store.save_observation(obs) is True
    audit_repo._queue.join()
    summary = store.summary()
    assert summary["monitor_state"] == "NO_ALERTS"
    assert summary["observations"] == 1
    assert summary["compared"] == 1


def test_blocked_observations_do_not_count_as_a_run(audit_repo: AuditRepository) -> None:
    """A SHADOW_BLOCKED row is the runtime's "I did not observe" marker.

    This is the LIVE store's actual condition (2026-09-28): two rows from
    2026-08-18/19, both valid=0 with error_code=SHADOW_BLOCKED, written when
    the shadow runtime was not READY. Counting them as runs would let a
    never-compared attempt pass as a clean bill of health, so the summary
    must report NOT_RUN while still disclosing that the rows exist.
    """
    store = _store(audit_repo)
    obs = Shadow70Observation(
        observation_id="obs-blocked",
        snapshot_id="snap-1",
        timestamp="2026-08-18T23:51:04+00:00",
        symbol="XAUUSD",
        timeframe="M1",
        simulated=True,
        model_id="",
        model_version="",
        model_hash="",
        scaler_hash="",
        schema_id="scalp_v3",
        schema_dimension=70,
        champion_action="NO_TRADE",
        champion_probabilities=[0.3, 0.3, 0.4],
        champion_confidence=0.4,
        shadow_action="NO_TRADE",
        shadow_probabilities=[],
        shadow_confidence=0.0,
        confidence_delta=0.0,
        disagreement="NO_TRADE_DISAGREEMENT",
        agreement=0,
        valid=0,
        reason="runtime state NOT_READY — no observation",
        regime="",
        session="",
        news_state="NORMAL",
        liquidity_state="",
        news_context_hash="",
        liquidity_feature_hash="",
        liquidity_features_10=[0.0] * 10,
        feature_hash="",
        sample_source="",
        latency_ms=0.0,
        error_code="SHADOW_BLOCKED",
        outcome="PENDING",
        outcome_resolved_at="2026-09-28T00:00:00+00:00",
    )
    assert store.save_observation(obs) is True
    audit_repo._queue.join()
    summary = store.summary()
    # Rows are disclosed, but they are NOT evidence of a run.
    assert summary["observations"] == 1
    assert summary["invalid"] == 1
    assert summary["compared"] == 0
    assert summary["monitor_state"] == "NOT_RUN"
