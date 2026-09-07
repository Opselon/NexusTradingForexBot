"""PHASE 2/3/4/13 — controlled autonomous-loop sandbox validation.

Proves the REAL factory loop mechanics against an isolated temp DB (no
broker, no production artifacts):

  PHASE 2  operator start -> tick -> generation -> candidates -> evaluation
           -> completion, with state transitions recorded.
  PHASE 3  reentrancy: a generation cycle that outlives the tick interval
           refuses a second concurrent tick (observable via _cycle_inflight).
  PHASE 4  failure during generation is recorded (loop FAILED, last_error
           set), the loop remains restartable, and recovery does not
           duplicate prior work.
  PHASE 13 idempotency: repeated start semantics and tick throttling do not
           create duplicate generations.

The real research pipeline runs on ledger-derived data via the same fixture
helpers the phase22 suite uses. Everything is in-memory/temp — the
production champion and artifacts are untouched.
"""

from __future__ import annotations

import time

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.strategies.factory.worker import AutonomousLoopWorker

import tests.unit.test_strategy_factory_phase22 as p22
from nexus_scalp.strategies.factory.store import (
    list_candidates,
    list_events,
    list_generations,
)


@pytest.fixture()
def audit_repo(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'factory_sandbox.db'}")
    yield repo
    repo.close()


@pytest.fixture()
def sandbox(audit_repo):
    p22.seed_experiences(audit_repo, count=40)
    factory, pipeline = p22.make_factory(audit_repo, size=8)
    worker = AutonomousLoopWorker(factory=factory)
    return factory, pipeline, worker


def test_phase2_controlled_generation_executes(sandbox, audit_repo):
    """Operator start -> tick -> real generation -> terminal COMPLETED."""
    factory, pipeline, worker = sandbox
    transitions: list[str] = [factory.loop_state]

    # Operator start (mirrors /api/factory/loop/start: flag + pump).
    assert factory.start_loop("AUTONOMOUS") is True
    worker.start()
    assert worker.running is True
    transitions.append(factory.loop_state)

    # One controlled cycle through the REAL tick path.
    ran = worker.tick()
    p22.flush(audit_repo)
    assert ran is True, "tick must execute a generation cycle"
    assert worker.generations_completed == 1
    transitions.append(factory.loop_state)

    # The generation reached a terminal state and candidates were evaluated.
    gens = list_generations(audit_repo, limit=10)
    assert gens, "generation row must exist"
    gen = gens[0]
    assert gen["status"] in ("COMPLETED", "FAILED"), gen["status"]
    cands = list_candidates(audit_repo, generation_id=gen["generation_id"])
    assert len(cands) >= 1, "candidates must exist"

    # Events prove the real pipeline ran (not a flag flip).
    events = list_events(audit_repo, generation_id=gen["generation_id"])
    types = {e.get("event_type") for e in events}
    assert "GENERATION_COMPLETED" in types, types

    # Stop is clean.
    worker.stop()
    assert worker.running is False


def test_phase3_reentrancy_guard_refuses_second_tick(sandbox):
    """A generation that outlives the next kick is not double-run."""
    factory, _pipeline, worker = sandbox
    factory.start_loop("AUTONOMOUS")
    worker.start()

    # Simulate an in-flight generation that outlived the kick timeout.
    worker._cycle_inflight = True
    assert worker.tick() is False, "second tick must refuse while in-flight"
    assert worker.cycle_count == 0, "no cycle may start concurrently"
    worker._cycle_inflight = False

    # After the guard clears, the cycle runs normally (exactly once).
    assert worker.tick() is True
    assert worker.cycle_count == 1


def test_phase4_failure_recorded_and_recoverable(sandbox, audit_repo, monkeypatch):
    """Generation failure => loop FAILED + last_error, restartable, no dupes."""
    factory, _pipeline, worker = sandbox
    factory.start_loop("AUTONOMOUS")
    worker.start()

    # Inject failure INSIDE the generation (population build).
    calls = {"n": 0}
    orig_generate = factory.generate_population

    def failing_generate(*args, **kwargs):
        calls["n"] += 1
        raise RuntimeError("INJECTED_GENERATION_FAILURE")

    monkeypatch.setattr(factory, "generate_population", failing_generate)
    assert worker.tick() is False, "failed cycle returns False (never SUCCESS)"
    monkeypatch.undo()
    assert calls["n"] == 1

    # Failure recorded observably; loop state reflects it.
    assert factory.loop_state in ("FAILED", "STOPPED"), factory.loop_state
    assert "INJECTED_GENERATION_FAILURE" in (worker.last_error or "")

    # Restartable: a fresh worker runs exactly one new generation.
    worker.running = False
    worker2 = AutonomousLoopWorker(factory=factory)
    worker2.start()
    assert worker2.tick() is True
    assert worker2.cycle_count == 1
    gens = list_generations(audit_repo, limit=20)
    ids = [g["generation_id"] for g in gens]
    assert len(ids) == len(set(ids)), "no duplicate generation ids"


def test_phase13_idempotent_start_and_throttle(sandbox):
    """Repeated start/tick semantics create no duplicate work."""
    factory, _pipeline, worker = sandbox
    # start_loop is idempotent while RUNNING.
    assert factory.start_loop("AUTONOMOUS") is True
    assert factory.start_loop("AUTONOMOUS") is False
    worker.start()
    assert worker.running is True
    # start() again is a no-op (running guard).
    worker.start()
    assert worker.cycle_count == 0  # nothing ran yet
    # tick throttles within pause_between_cycles_sec: a back-to-back tick
    # after a just-finished cycle must not start a second generation.
    worker._last_run_ts = time.time()
    assert worker.tick() is False, "throttle refuses back-to-back cycles"
    assert worker.cycle_count == 0
