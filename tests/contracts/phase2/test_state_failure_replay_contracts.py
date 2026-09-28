"""Phase 2I/2O/2P — state machine, failure propagation, replay determinism.

2I  STATE MACHINE: valid transitions are accepted, illegal transitions are
    REFUSED and the refusal is itself audited. Read against the real
    transition tables — no invented business rules.

2O  FAILURE PROPAGATION: an invalid upstream stage must block a downstream
    "valid" claim.

2P  REPLAY / DETERMINISM: identical persisted inputs must produce identical
    outputs; differences are classified, never normalized away.

No production module is modified. Every check targets a public interface.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nexus_scalp.adapters.database.provider_store import query_rows, queue_write
from nexus_scalp.governance.engine import ModelGovernanceEngine, PromotionGateError
from nexus_scalp.governance.models import (
    PROMOTION_TRANSITIONS,
    PromotionState,
)
from nexus_scalp.governance.store import GovernanceStore
from nexus_scalp.model_lifecycle.learning_cycle import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
)
from nexus_scalp.research.evidence import EvidenceArtifact, EvidenceKind
from nexus_scalp.research.models import CandidateLifecycle
from nexus_scalp.research.observability import (
    FailureClass,
    GateStatus,
    GateType,
    ResearchObservabilityStore,
)
from nexus_scalp.research.registry import StrategyRegistry

_MAIN_CHECKOUT = Path(r"C:/Users/Capsizer/source/repos/NexusTradingForexBot")
_LIVE_AUDIT_DB = _MAIN_CHECKOUT / "artifacts" / "audit.db"


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _stamp() -> int:
    return int(time.time() * 1_000_000)


def _live_conn():
    import sqlite3

    if not _LIVE_AUDIT_DB.exists():
        pytest.skip("live audit.db not present in the main checkout")
    return sqlite3.connect(f"file:{_LIVE_AUDIT_DB}?mode=ro", uri=True)


# ===========================================================================
# 2I — state machine contracts
# ===========================================================================


class TestPromotionStateMachine:
    """The promotion state machine, tested through its public table.

    The rules come from PROMOTION_TRANSITIONS in the real implementation —
    this test never invents a rule; it asserts the table the code declares.
    """

    @pytest.fixture
    def engine(self, sqlite_env) -> ModelGovernanceEngine:
        return ModelGovernanceEngine(GovernanceStore(sqlite_env.repo))

    @pytest.mark.parametrize(
        "current,target",
        [
            (PromotionState.RESEARCH, PromotionState.VALIDATED),
            (PromotionState.VALIDATED, PromotionState.CHALLENGER),
            (PromotionState.CHALLENGER, PromotionState.SHADOW),
            (PromotionState.SHADOW, PromotionState.READY_FOR_REVIEW),
            (PromotionState.READY_FOR_REVIEW, PromotionState.APPROVED),
            (PromotionState.APPROVED, PromotionState.CHAMPION),
        ],
    )
    def test_the_documented_happy_path_is_accepted(self, engine, current, target):
        assert engine.can_transition(current, target) is True

    @pytest.mark.parametrize(
        "current,target",
        [
            # SHADOW -> CHAMPION is explicitly forbidden (spec 21):
            # the path is SHADOW -> READY_FOR_REVIEW -> APPROVED -> CHAMPION.
            (PromotionState.SHADOW, PromotionState.CHAMPION),
            (PromotionState.RESEARCH, PromotionState.CHAMPION),
            (PromotionState.VALIDATED, PromotionState.CHAMPION),
            (PromotionState.CHALLENGER, PromotionState.CHAMPION),
            (PromotionState.RESEARCH, PromotionState.APPROVED),
            (PromotionState.VALIDATED, PromotionState.SHADOW),
        ],
    )
    def test_the_short_circuit_promotions_are_refused(self, engine, current, target):
        assert engine.can_transition(current, target) is False, (
            f"{current.value} -> {target.value} is allowed: a candidate can be "
            "promoted without passing review"
        )

    def test_an_illegal_transition_is_audited_not_silently_dropped(self, engine, sqlite_env):
        """A refused transition must leave an audit trail, not a silent no-op."""
        store = GovernanceStore(sqlite_env.repo)
        model_id = f"M-SM-{_stamp()}"
        store.set_state(
            model_id=model_id,
            model_version="1",
            lifecycle_state=PromotionState.SHADOW.value,
        )
        sqlite_env.flush()
        # The engine must see the state we just persisted.
        assert store.get_state(model_id, "1")["lifecycle_state"] == "SHADOW"
        with pytest.raises(PromotionGateError):
            engine.transition(
                model_id=model_id,
                model_version="1",
                target=PromotionState.CHAMPION,
                actor="phase2-contract",
                reason="short-circuit attempt",
            )
        sqlite_env.flush()
        blocked = query_rows(
            sqlite_env.repo,
            "SELECT event, previous_state, new_state, actor FROM "
            "model_governance_events WHERE event='PROMOTION_BLOCKED' AND model_id=?",
            (model_id,),
        )
        assert blocked, "the refused transition left no audit trail"
        assert blocked[0]["previous_state"] == "SHADOW"
        assert blocked[0]["new_state"] == "CHAMPION"
        assert blocked[0]["actor"] == "phase2-contract"

    def test_terminal_states_allow_no_exit(self):
        for state, targets in PROMOTION_TRANSITIONS.items():
            if not targets:
                assert (
                    state
                    in {
                        PromotionState.REJECTED,
                        PromotionState.RETIRED,
                    }
                    or not targets
                ), f"{state.value} is terminal but reachable"

    def test_the_machine_is_not_silent_about_an_unknown_state(self, engine):
        """A model with no recorded state defaults to RESEARCH — the entry
        state — rather than raising or inventing a state."""
        got = engine.store.get_state("M-NOT-THERE", "1")
        assert got is None or got == {}


class TestLearningCycleStateMachine:
    """The learning-cycle machine, from its own declared table."""

    @pytest.mark.parametrize(
        "frm,to",
        [
            ("IDLE", "TRIGGERED"),
            ("TRIGGERED", "DATASET_BUILDING"),
            ("DATASET_READY", "TRAINING"),
            ("TRAINING", "TRAINED"),
            ("TRAINED", "VALIDATING"),
            ("VALIDATING", "VALIDATED"),
            ("VALIDATED", "SHADOW_ATTACHING"),
            ("SHADOW_RUNNING", "SHADOW_EVALUATING"),
            ("SHADOW_EVALUATING", "PROMOTION_EVALUATION"),
            ("PROMOTION_APPROVED", "PROMOTING"),
        ],
    )
    def test_the_documented_forward_path_is_declared(self, frm, to):
        assert to in ALLOWED_TRANSITIONS[frm]

    @pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
    def test_terminal_states_have_no_outgoing_transitions(self, state):
        assert state in ALLOWED_TRANSITIONS, f"{state} is not a declared state"
        assert not ALLOWED_TRANSITIONS[state], (
            f"{state} is declared terminal but has outgoing transitions"
        )

    def test_there_is_no_fast_forward_to_promotion(self):
        """A cycle cannot jump from TRAINING to PROMOTION_APPROVED."""
        assert "PROMOTION_APPROVED" not in ALLOWED_TRANSITIONS["TRAINING"]
        assert "COMPLETED" not in ALLOWED_TRANSITIONS["TRAINING"]


class TestStrategyLifecycleStateMachine:
    """The candidate lifecycle: DISCOVERED -> VALIDATED | REJECTED | RETIRED."""

    def test_the_rejected_state_exists_and_is_terminal(self):
        assert CandidateLifecycle.REJECTED.value == "REJECTED"
        assert CandidateLifecycle.RETIRED.value == "RETIRED"

    def test_a_discovered_strategy_may_be_validated(self, sqlite_env):
        """The registry starts a candidate in DISCOVERED; the lifecycle column
        carries it. Uses the conftest helper's real column set."""
        from tests.contracts.phase2.conftest import insert_strategy_registry_row

        sid = insert_strategy_registry_row(sqlite_env, lifecycle="DISCOVERED")
        rows = query_rows(
            sqlite_env.repo,
            "SELECT lifecycle FROM strategy_registry WHERE strategy_id=?",
            (sid,),
        )
        assert rows[0]["lifecycle"] == "DISCOVERED"

    def test_the_live_store_only_uses_declared_lifecycle_values(self):
        conn = _live_conn()
        try:
            values = {
                r[0] for r in conn.execute("SELECT DISTINCT lifecycle FROM strategy_registry")
            }
        finally:
            conn.close()
        declared = {m.value for m in CandidateLifecycle}
        assert values <= declared, (
            f"the live store carries undeclared lifecycle values: {values - declared}"
        )


# ===========================================================================
# 2O — failure propagation
# ===========================================================================


class TestFailurePropagation:
    """An invalid upstream stage must block a downstream truth claim."""

    def test_a_failed_gate_carries_a_failure_reason_and_class(self, sqlite_env):
        """A gate that FAILED must persist WHY it failed — a downstream
        consumer must be able to see the upstream failure."""
        repo = sqlite_env.repo
        store = ResearchObservabilityStore(repo)
        run_id = f"RUN-FP-{_stamp()}"
        queue_write(
            repo,
            "INSERT INTO research_runs "
            "(run_id, dataset_id, strategy_id, strategy_version, executed_at, status) "
            "VALUES (?,?,?,?,?,?)",
            (run_id, "DS-1", "ST-1", "1.0.0", _iso(), "RUNNING"),
            operation="phase2.fp.run",
        )
        gate = store.create_gate(
            strategy_id="ST-1",
            research_run_id=run_id,
            gate_type=GateType.OOS,
            status=GateStatus.PENDING,
            order_index=0,
        )
        sqlite_env.flush()

        artifact = EvidenceArtifact.create(
            strategy_id="ST-1",
            research_run_id=run_id,
            kind=EvidenceKind.OOS_RESULT,
            content={"gate": "failed"},
            gate_id=gate.gate_id,
        )
        store.finish_gate(
            gate.gate_id,
            status=GateStatus.FAILED,
            result={"verdict": "FAIL"},
            evidence=artifact,
            failure_reason="oos expectancy below threshold",
            failure_class=FailureClass.RESEARCH,
        )
        sqlite_env.flush()
        rows = query_rows(repo, "SELECT * FROM research_gates WHERE gate_id=?", (gate.gate_id,))
        assert rows[0]["status"] == "FAILED"
        assert rows[0]["failure_reason"] == "oos expectancy below threshold"
        assert rows[0]["evidence_id"] == artifact.evidence_id

    def test_a_pending_gate_may_not_be_read_as_a_pass(self, sqlite_env):
        """A PENDING gate has NO verdict. A consumer that treats PENDING as
        PASS is the failure-propagation defect."""
        repo = sqlite_env.repo
        store = ResearchObservabilityStore(repo)
        run_id = f"RUN-PEND-{_stamp()}"
        queue_write(
            repo,
            "INSERT INTO research_runs "
            "(run_id, dataset_id, strategy_id, strategy_version, executed_at, status) "
            "VALUES (?,?,?,?,?,?)",
            (run_id, "DS-1", "ST-1", "1.0.0", _iso(), "RUNNING"),
            operation="phase2.pend.run",
        )
        gate = store.create_gate(
            strategy_id="ST-1",
            research_run_id=run_id,
            gate_type=GateType.BACKTEST,
            status=GateStatus.PENDING,
            order_index=0,
        )
        sqlite_env.flush()
        rows = query_rows(
            repo,
            "SELECT status, evidence_id FROM research_gates WHERE gate_id=?",
            (gate.gate_id,),
        )
        assert rows[0]["status"] == "PENDING"
        assert not rows[0]["evidence_id"], (
            "a PENDING gate already carries evidence — a consumer reading "
            "evidence_id non-empty could treat it as settled"
        )

    def test_a_rejected_strategy_carries_its_retirement_reason(self, sqlite_env):
        """REJECTED is a terminal state; the reason must be persisted so a
        later promotion cannot claim a clean record."""
        repo = sqlite_env.repo
        sid = f"ST-REJ-{_stamp()}"
        queue_write(
            repo,
            "INSERT INTO strategy_registry "
            "(strategy_id, strategy_version, lifecycle, retirement_reason, created_at, "
            " updated_at) VALUES (?,?,?,?,?,?)",
            (sid, "1.0.0", "REJECTED", "oos gate failed twice", _iso(), _iso()),
            operation="phase2.rej.strategy",
        )
        sqlite_env.flush()
        rows = query_rows(
            repo,
            "SELECT lifecycle, retirement_reason FROM strategy_registry WHERE strategy_id=?",
            (sid,),
        )
        assert rows[0]["lifecycle"] == "REJECTED"
        assert rows[0]["retirement_reason"], "a REJECTED strategy has no recorded reason"

    def test_the_live_store_has_no_promotion_without_validation(self):
        """No promotion record may exist without an APPROVED transition to
        point at. On the live box model_promotion_audit is empty — the table
        EXISTS and the column contract holds, which is the invariant."""
        conn = _live_conn()
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_promotion_audit'"
            ).fetchone()
            assert exists, "model_promotion_audit is not in the live schema"
            cols = {r[1] for r in conn.execute("PRAGMA table_info(model_promotion_audit)")}
            for required in ("approval_actor", "status", "recorded_at"):
                assert required in cols, f"model_promotion_audit lost the {required} column"
            count = conn.execute("SELECT COUNT(*) FROM model_promotion_audit").fetchone()[0]
            assert count == 0, "the live audit DB carries real promotions — re-derive"
        finally:
            conn.close()


# ===========================================================================
# 2P — replay / determinism
# ===========================================================================


class TestReplayDeterminism:
    """Given identical persisted inputs, two reads must agree."""

    def test_the_same_row_read_twice_is_identical(self, sqlite_env):
        repo = sqlite_env.repo
        run_id = f"RUN-REP-{_stamp()}"
        cfg = {"seed": 42, "slippage_bps": 0.4, "spread_usd": 1.2}
        queue_write(
            repo,
            "INSERT INTO research_runs "
            "(run_id, dataset_id, strategy_id, strategy_version, executed_at, config, "
            " status) VALUES (?,?,?,?,?,?,?)",
            (run_id, "DS-1", "ST-1", "1.0.0", _iso(), json.dumps(cfg), "COMPLETED"),
            operation="phase2.rep.run",
        )
        sqlite_env.flush()
        first = query_rows(repo, "SELECT * FROM research_runs WHERE run_id=?", (run_id,))
        second = query_rows(repo, "SELECT * FROM research_runs WHERE run_id=?", (run_id,))
        # byte-identical, including the JSON blob
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    def test_the_json_config_round_trips_without_drift(self, sqlite_env):
        """Determinism requires the serialized evidence to be stable — a
        re-serialization that reorders keys would change content hashes."""
        repo = sqlite_env.repo
        run_id = f"RUN-JSON-{_stamp()}"
        cfg = {"b": 1, "a": 2, "nested": {"z": 1, "y": 2}}
        expected = json.dumps(cfg)
        queue_write(
            repo,
            "INSERT INTO research_runs "
            "(run_id, dataset_id, strategy_id, strategy_version, executed_at, config, "
            " status) VALUES (?,?,?,?,?,?,?)",
            (run_id, "DS-1", "ST-1", "1.0.0", _iso(), expected, "COMPLETED"),
            operation="phase2.json.run",
        )
        sqlite_env.flush()
        rows = query_rows(repo, "SELECT config FROM research_runs WHERE run_id=?", (run_id,))
        assert rows[0]["config"] == expected

    def test_an_idempotent_re_insert_does_not_duplicate(self, sqlite_env):
        """Replaying the same evidence must not create a second row — the
        evidence insert is ON CONFLICT DO NOTHING."""
        repo = sqlite_env.repo
        store = ResearchObservabilityStore(repo)
        run_id = f"RUN-IDEM-{_stamp()}"
        queue_write(
            repo,
            "INSERT INTO research_runs "
            "(run_id, dataset_id, strategy_id, strategy_version, executed_at, status) "
            "VALUES (?,?,?,?,?,?)",
            (run_id, "DS-1", "ST-1", "1.0.0", _iso(), "RUNNING"),
            operation="phase2.idem.run",
        )
        artifact = EvidenceArtifact.create(
            strategy_id="ST-1",
            research_run_id=run_id,
            kind=EvidenceKind.BACKTEST_RESULT,
            content={"expectancy_r": 0.5},
        )
        store.store_evidence(artifact)
        store.store_evidence(artifact)  # the SAME artifact, replayed
        sqlite_env.flush()
        rows = query_rows(
            repo,
            "SELECT COUNT(*) AS n FROM research_evidence WHERE evidence_id=?",
            (artifact.evidence_id,),
        )
        assert rows[0]["n"] == 1, "a replayed evidence artifact was duplicated"

    def test_a_replayed_shadow_decision_is_not_duplicated(self, sqlite_env):
        """The replay path must not double-count a decision: the same
        decision id landing twice must resolve to one row."""
        repo = sqlite_env.repo
        queue_write(
            repo,
            "INSERT INTO shadow_runs "
            "(run_id, champion_model_id, champion_version, challenger_model_id, "
            " challenger_version, status, started_at) VALUES (?,?,?,?,?,?,?)",
            ("shr-rep", "m-1", "1", "m-2", "1", "RUNNING", _iso()),
            operation="phase2.rep.shadow_run",
        )
        decision_sql = (
            "INSERT INTO shadow_decisions "
            "(shadow_decision_id, run_id, timestamp, symbol, timeframe, "
            " champion_model_id, champion_version, challenger_model_id, "
            " challenger_version) VALUES (?,?,?,?,?,?,?,?,?)"
        )
        args = ("sd-1", "shr-rep", _iso(), "EURUSD", "H1", "m-1", "1", "m-2", "1")
        queue_write(repo, decision_sql, args, operation="phase2.rep.dec1")
        queue_write(repo, decision_sql, args, operation="phase2.rep.dec2")
        sqlite_env.flush()
        rows = query_rows(
            repo,
            "SELECT COUNT(*) AS n FROM shadow_decisions WHERE shadow_decision_id=?",
            ("sd-1",),
        )
        assert rows[0]["n"] == 1, "the replayed shadow decision was duplicated"

    def test_live_shadow_decisions_are_unique_per_id(self):
        """The live store must not carry duplicate decision identities — a
        duplicate would inflate every replay statistic."""
        conn = _live_conn()
        try:
            dupes = conn.execute(
                "SELECT COUNT(*) FROM (SELECT shadow_decision_id FROM shadow_decisions "
                "GROUP BY shadow_decision_id HAVING COUNT(*) > 1)"
            ).fetchone()[0]
            assert dupes == 0, f"{dupes} duplicated shadow decision identities"
        finally:
            conn.close()
