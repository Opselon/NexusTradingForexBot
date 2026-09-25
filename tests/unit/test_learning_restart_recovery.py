"""Learning-loop restart-safety tests (P1).

Scenarios: interrupted cycles (DATASET_BUILDING, TRAINING, VALIDATING,
SHADOW_RUNNING) are marked FAILED by recover_interrupted() on the next
construction; no duplicate active cycles are created; re-running a cycle
after a crash starts a NEW cycle (retry) rather than resurrecting partial
work; and no code path here can promote (LearningCycleOrchestrator has no
promotion authority).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from nexus_scalp.model_lifecycle.learning_cycle import (
    IN_FLIGHT_STATES,
    TERMINAL_STATES,
    LearningCycleStore,
)


@pytest.fixture()
def cycle_db(tmp_path: Path) -> str:
    return str(tmp_path / "cycles.db")


def _drive_to(store: LearningCycleStore, trigger_identity: str, state: str) -> str:
    """Drives one cycle to a given in-flight state (legal transition path)."""
    cid = store.start_cycle("scheduled", trigger_identity)
    path = {
        "DATASET_BUILDING": ["DATASET_BUILDING"],
        "TRAINING": ["DATASET_BUILDING", "DATASET_READY", "TRAINING"],
        "VALIDATING": ["DATASET_BUILDING", "DATASET_READY", "TRAINING", "TRAINED", "VALIDATING"],
        "SHADOW_RUNNING": [
            "DATASET_BUILDING",
            "DATASET_READY",
            "TRAINING",
            "TRAINED",
            "VALIDATING",
            "VALIDATED",
            "SHADOW_ATTACHING",
            "SHADOW_RUNNING",
        ],
    }[state]
    for target in path:
        store.transition(cid, target)
    assert store.get_cycle(cid)["status"] == state
    return cid


class TestRestartRecovery:
    def test_kill_during_dataset_building_marks_failed(self, cycle_db: str) -> None:
        s1 = LearningCycleStore(cycle_db)
        cid = s1.start_cycle("scheduled", "sched:1")
        s1.transition(cid, "DATASET_BUILDING")
        # --- process death: a NEW store instance simulates restart ---
        s2 = LearningCycleStore(cycle_db)
        repaired = s2.recover_interrupted()
        assert repaired == [cid]
        assert s2.get_cycle(cid)["status"] == "FAILED"
        assert s2.get_cycle(cid)["error_code"] == "RESTART_INTERRUPTED"

    def test_kill_during_training_marks_failed(self, cycle_db: str) -> None:
        s1 = LearningCycleStore(cycle_db)
        cid = _drive_to(s1, "sched:2", "TRAINING")
        s2 = LearningCycleStore(cycle_db)
        assert s2.recover_interrupted() == [cid]
        assert s2.get_cycle(cid)["status"] == "FAILED"

    def test_kill_during_validation_marks_failed(self, cycle_db: str) -> None:
        s1 = LearningCycleStore(cycle_db)
        cid = _drive_to(s1, "sched:3", "VALIDATING")
        s2 = LearningCycleStore(cycle_db)
        assert s2.recover_interrupted() == [cid]
        assert s2.get_cycle(cid)["status"] == "FAILED"

    def test_kill_during_shadow_running_marks_failed(self, cycle_db: str) -> None:
        s1 = LearningCycleStore(cycle_db)
        cid = _drive_to(s1, "sched:4", "SHADOW_RUNNING")
        s2 = LearningCycleStore(cycle_db)
        assert s2.recover_interrupted() == [cid]
        assert s2.get_cycle(cid)["status"] == "FAILED"

    def test_no_duplicate_cycle_after_restart(self, cycle_db: str) -> None:
        """After recovery, the same trigger identity starts a FRESH cycle —
        no duplicate active cycle, no resurrection of the failed one."""
        s1 = LearningCycleStore(cycle_db)
        cid1 = _drive_to(s1, "sched:5", "TRAINING")
        s2 = LearningCycleStore(cycle_db)
        s2.recover_interrupted()
        cid2 = s2.start_cycle("scheduled", "sched:5")
        assert cid2 != cid1
        rows = s2.list_cycles()
        active = [c for c in rows if c["status"] not in TERMINAL_STATES]
        assert len(active) == 1
        assert active[0]["cycle_id"] == cid2

    def test_in_flight_states_are_exhaustive(self) -> None:
        from nexus_scalp.model_lifecycle.learning_cycle import ALLOWED_TRANSITIONS

        running = {s for s, targets in ALLOWED_TRANSITIONS.items() if targets}
        running.discard("IDLE")
        assert running == set(IN_FLIGHT_STATES) | {
            "TRIGGERED",
            "DATASET_READY",
            "TRAINED",
            "VALIDATED",
            "PROMOTION_EVALUATION",
            "PROMOTION_APPROVED",
        }

    def test_interrupted_cycle_is_never_interpreted_as_success(self, cycle_db: str) -> None:
        s1 = LearningCycleStore(cycle_db)
        cid = _drive_to(s1, "sched:6", "TRAINING")
        s2 = LearningCycleStore(cycle_db)
        s2.recover_interrupted()
        row = s2.get_cycle(cid)
        assert row["decision"] == ""  # no decision recorded
        assert row["status"] == "FAILED"
        # and it is terminal: no transition out
        from nexus_scalp.model_lifecycle.learning_cycle import LearningCycleError

        with pytest.raises(LearningCycleError):
            s2.transition(cid, "COMPLETED")
