"""Phase 2 — VALIDATION stage persistence contract.

Proves for the model governance boundary:
    create/write -> commit -> read -> compare
over the REAL columns of ``model_governance_events``, and that a PROMOTED
event carries actor + previous_state + new_state (the auditable transition
contract, spec 31).

Interfaces used (all public):
    nexus_scalp.governance.store.GovernanceStore.record_event
    nexus_scalp.governance.models.GovernanceEvent / GovernanceStage
    nexus_scalp.governance.models.PromotionTransition
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from nexus_scalp.governance.models import (
    GovernanceEvent,
    GovernanceStage,
    PromotionState,
    PromotionTransition,
)
from nexus_scalp.governance.store import GovernanceStore


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _stamp() -> int:
    return int(time.time() * 1_000_000)


def test_validation_event_roundtrip(sqlite_env):
    repo = sqlite_env.repo
    store = GovernanceStore(repo)
    stamp = _stamp()
    model_id = f"VAL-MODEL-{stamp}"

    event = GovernanceEvent(
        event_id=f"ev_phase2_{stamp}",
        event="VALIDATION_STATUS",
        stage=GovernanceStage.OUTCOME,
        model_id=model_id,
        model_version="v1.0",
        schema_id="scalp_v3",
        correlation_id=f"corr_{stamp}",
        error_code="",
        error_type="",
        duration_ms=120.5,
        actor="phase2-contract",
        previous_state="CHALLENGER",
        new_state="APPROVED",
        reason="oos + robustness passed",
        payload={"oos": "PASS", "robustness": "PASS", "score": 0.63},
    )
    assert store.record_event(event), "GovernanceStore.record_event accepted the event"
    sqlite_env.flush()

    rows = store.list_events(limit=50, model_id=model_id)
    assert len(rows) == 1, "the written governance event is readable"
    row = rows[0]
    assert row["event_id"] == event.event_id
    assert row["event"] == "VALIDATION_STATUS"
    assert row["stage"] == "OUTCOME"
    assert row["model_id"] == model_id
    assert row["model_version"] == "v1.0"
    assert row["schema_id"] == "scalp_v3"
    assert row["correlation_id"] == f"corr_{stamp}"
    assert float(row["duration_ms"]) == 120.5
    assert row["actor"] == "phase2-contract"
    assert row["previous_state"] == "CHALLENGER"
    assert row["new_state"] == "APPROVED"
    assert row["reason"] == "oos + robustness passed"
    assert json.loads(row["payload"])["oos"] == "PASS"


class TestPromotedEvent:
    """A PROMOTED event is an audited transition (spec 31)."""

    def test_promoted_event_carries_actor_and_states(self, sqlite_env):
        repo = sqlite_env.repo
        store = GovernanceStore(repo)
        stamp = _stamp()
        model_id = f"PROM-MODEL-{stamp}"

        transition = PromotionTransition(
            transition_id=f"tr_phase2_{stamp}",
            model_id=model_id,
            model_version="v1.0",
            previous_state=PromotionState.APPROVED,
            new_state=PromotionState.CHAMPION,
            actor="operator.alice",
            reason="approved by phase2 contract",
            evidence_snapshot={"oos": "PASS", "score": 0.63},
            source_commit="abc123",
            artifact_hash="hash-phase2",
        )
        assert store.record_transition(transition), "the transition was recorded"
        sqlite_env.flush()

        rows = store.list_events(limit=50, model_id=model_id)
        assert len(rows) >= 1
        promoted = [r for r in rows if r["event"] == "PROMOTION_TRANSITION"]
        assert promoted, "the PROMOTION_TRANSITION event is persisted"
        row = promoted[0]
        # The auditable transition contract: actor + previous + new.
        assert row["actor"] == "operator.alice", (
            "a PROMOTED event must carry the actor who promoted it"
        )
        assert row["previous_state"] == "APPROVED", (
            "a PROMOTED event must carry the state it promoted FROM"
        )
        assert row["new_state"] == "CHAMPION", (
            "a PROMOTED event must carry the state it promoted TO"
        )
        assert row["model_id"] == model_id
        assert json.loads(row["payload"])["artifact_hash"] == "hash-phase2"

    def test_promoted_event_without_actor_is_a_finding(self, sqlite_env):
        """An event that omits the actor still lands (actor defaults to
        'system') — recorded as the finding that the schema does NOT require a
        human actor on a promotion event; the audit trail must be read from
        the ``actor`` string itself, not from any constraint.
        """
        repo = sqlite_env.repo
        store = GovernanceStore(repo)
        stamp = _stamp()
        model_id = f"PROM-NOACTOR-{stamp}"

        transition = PromotionTransition(
            transition_id=f"tr_na_{stamp}",
            model_id=model_id,
            model_version="v1.0",
            previous_state=PromotionState.SHADOW,
            new_state=PromotionState.READY_FOR_REVIEW,
        )
        assert store.record_transition(transition)
        sqlite_env.flush()

        rows = store.list_events(limit=50, model_id=model_id)
        promoted = [r for r in rows if r["event"] == "PROMOTION_TRANSITION"]
        assert promoted
        # No actor was supplied; the model's default is 'system'.
        assert promoted[0]["actor"] == "system"
        assert promoted[0]["previous_state"] == "SHADOW"
        assert promoted[0]["new_state"] == "READY_FOR_REVIEW"


class TestLiveValidationReadback:
    """Read-only probes of the RUNNING engine's real governance ledger."""

    def test_live_events_carry_transition_columns(self, live_sqlite_probe):
        conn = live_sqlite_probe
        cols = [r[1] for r in conn.execute("PRAGMA table_xinfo(model_governance_events)")]
        for required in (
            "event_id", "event", "stage", "model_id", "actor",
            "previous_state", "new_state", "timestamp",
        ):
            assert required in cols, (
                f"model_governance_events lost the {required} column"
            )

    def test_live_promoted_events_carry_actor_and_states(self, live_sqlite_probe):
        conn = live_sqlite_probe
        total = conn.execute(
            "SELECT COUNT(*) FROM model_governance_events"
        ).fetchone()[0]
        if not total:
            pytest.skip("live model_governance_events is empty")
        promoted = conn.execute(
            "SELECT actor, previous_state, new_state, model_id FROM "
            "model_governance_events "
            "WHERE UPPER(COALESCE(new_state,'')) LIKE '%PROMOT%' "
            "OR UPPER(COALESCE(event,'')) LIKE '%PROMOT%'"
        ).fetchall()
        # Evidence: every PROMOTED-class event present must carry the triple.
        for r in promoted:
            assert r["actor"], "a PROMOTED event carries an actor"
            assert r["previous_state"], "a PROMOTED event carries previous_state"
            assert r["new_state"], "a PROMOTED event carries new_state"
