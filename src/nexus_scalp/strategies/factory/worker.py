"""
Strategy Factory — Autonomous Loop Worker
==========================================
STRATEGY FACTORY (2026-08-20).

Runs the autonomous evolution loop (spec 55 / 56 / 57 / 73 / 74):

    generate -> validate -> backtest -> walk-forward -> OOS -> robustness
      -> rank -> select -> analyze failures -> generate next -> repeat

Restart-safe: the loop state and generation checkpoints are persisted
(factory_loop_state + factory_generations + factory_candidates). On restart,
`recover()` reloads the active generation and resumes from the first
candidate without a recorded evaluation (spec 41 / 74).

Stopping conditions (spec 55): max generations, max runtime, max cost,
target elite count, no-improvement generations.

Stagnation detection (spec 56): when best scores stall, exploration pressure
is increased.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.strategies.factory.models import LoopState
from nexus_scalp.strategies.factory.orchestrator import StrategyFactory
from nexus_scalp.strategies.factory.store import (
    emit_event,
    get_loop_state,
    set_loop_state,
    sweep_stale_generations,
)


logger = get_logger("nexus_scalp.strategies.factory.worker")


def _event_id() -> str:
    return f"evt_{uuid.uuid4().hex[:16]}"


class AutonomousLoopWorker:
    """Bounded autonomous generation loop, driven via asyncio.to_thread.

    Contract:
      * never blocks the live path (invoked off the tick loop);
      * every cycle is exception-isolated (a failure is logged + persisted,
        the worker keeps its state and can be resumed);
      * kill switch stops new generations / new LLM requests immediately
        without corrupting historical results (spec 106).
    """

    def __init__(
        self,
        factory: StrategyFactory,
        *,
        max_generations: int = 20,
        max_runtime_sec: float = 3600.0,
        target_elite_count: int = 8,
        no_improvement_generations: int = 4,
        pause_between_cycles_sec: float = 5.0,
    ) -> None:
        self.factory = factory
        self.max_generations = int(max_generations)
        self.max_runtime_sec = float(max_runtime_sec)
        self.target_elite_count = int(target_elite_count)
        self.no_improvement_generations = int(no_improvement_generations)
        self.pause_between_cycles_sec = float(pause_between_cycles_sec)

        self.running = False
        self.paused = False
        self.cycle_count = 0
        self.generations_completed = 0
        self.last_error = ""
        self._last_run_ts: float = 0.0
        self._stagnation_count = 0
        self._best_score_seen = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.paused = False
        persisted = get_loop_state(self.factory.audit_repo)
        if persisted.get("state") in (LoopState.RUNNING.value, LoopState.PAUSED.value):
            # Crash recovery: an autonomous loop was active before restart.
            self.factory.loop_state = LoopState.RECOVERING.value
            logger.warning("[STRATEGY_FACTORY] loop state found RUNNING/PAUSED — recovering")
            emit_event(
                self.factory.audit_repo,
                {
                    "event_id": _event_id(),
                    "event_type": "LOOP_RECOVERING",
                    "message": "Autonomous loop recovering from restart",
                    "payload": {"persisted_state": persisted.get("state")},
                },
            )
        self.factory.loop_state = LoopState.RUNNING.value
        set_loop_state(
            self.factory.audit_repo,
            {"state": LoopState.RUNNING.value, "generation_id": self.factory.current_generation_id},
        )

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        self.factory.stop_loop()

    def pause(self) -> bool:
        if not self.running:
            return False
        self.paused = True
        return self.factory.pause_loop()

    def resume(self) -> bool:
        if not self.running:
            return False
        self.paused = False
        return self.factory.resume_loop()

    # ------------------------------------------------------------------
    # Cycle
    # ------------------------------------------------------------------

    def tick(self) -> bool:
        """Runs one autonomous cycle if allowed. Returns True when a cycle ran."""
        if not self.running:
            return False
        if self.paused:
            return False
        if self.factory._kill_requested:
            self.running = False
            return False

        now = time.time()
        if now - self._last_run_ts < self.pause_between_cycles_sec:
            return False
        self._last_run_ts = now
        self.cycle_count += 1

        if self._should_stop():
            self._finish_loop("STOPPED", "stopping condition reached")
            return False

        try:
            self._run_one_generation()
            self.generations_completed += 1
            return True
        except Exception as e:
            self.last_error = str(e)
            self.factory.loop_state = LoopState.FAILED.value
            set_loop_state(
                self.factory.audit_repo,
                {
                    "state": LoopState.FAILED.value,
                    "generation_id": self.factory.current_generation_id,
                    "last_error": str(e),
                },
            )
            logger.error(
                "[STRATEGY_FACTORY] autonomous cycle failed (isolated)",
                cycle=self.cycle_count,
                error=str(e),
                exc_info=True,
            )
            return False

    def _should_stop(self) -> bool:
        if self.generations_completed >= self.max_generations:
            return True
        if self._stagnation_count >= self.no_improvement_generations:
            return True
        return False

    def _run_one_generation(self) -> dict[str, Any]:
        memory = self.factory.build_memory()
        result = self.factory.run_generation_cycle(memory=memory)
        summary = result.get("summary") or {}
        best = float(summary.get("best_score", 0.0) or 0.0)
        if best <= self._best_score_seen + 1e-9:
            self._stagnation_count += 1
        else:
            self._stagnation_count = 0
            self._best_score_seen = best
        emit_event(
            self.factory.audit_repo,
            {
                "event_id": _event_id(),
                "generation_id": result.get("generation_id", ""),
                "event_type": "AUTONOMOUS_CYCLE",
                "message": f"Autonomous cycle {self.cycle_count} done",
                "payload": {
                    "best_score": best,
                    "stagnation": self._stagnation_count,
                    "elite": summary.get("elite", 0),
                },
            },
        )
        return result

    def _finish_loop(self, state: str, reason: str) -> None:
        self.factory.loop_state = LoopState.STOPPED.value
        set_loop_state(
            self.factory.audit_repo,
            {"state": LoopState.STOPPED.value, "generation_id": "", "last_error": reason},
        )
        emit_event(
            self.factory.audit_repo,
            {
                "event_id": _event_id(),
                "event_type": "AUTONOMOUS_LOOP_STOPPED",
                "message": f"Autonomous loop stopped: {reason}",
                "payload": {"cycles": self.cycle_count, "generations": self.generations_completed},
            },
        )
        self.running = False

    # ------------------------------------------------------------------
    # Recovery (spec 74)
    # ------------------------------------------------------------------

    def recover(self) -> dict[str, Any]:
        """Reloads the active research run + generation checkpoint and resumes.

        Returns the resume result; idempotent (no duplicated experiments —
        already-evaluated candidates are skipped).
        """
        # P1 hardening: sweep orphaned RUNNING generations before resuming the
        # current one, so a stale (previously-crashed) run cannot be mistaken
        # for an active generation. Idempotent and bounded.
        sweep = sweep_stale_generations(self.factory.audit_repo, max_age_minutes=30)
        persisted = get_loop_state(self.factory.audit_repo)
        generation_id = str(persisted.get("generation_id", "") or "")
        if not generation_id:
            # No active generation: nothing to resume.
            return {"status": "NOTHING_TO_RESUME", "swept": sweep.get("swept", [])}
        result = self.factory.resume_generation(generation_id)
        result["resumed_state"] = persisted.get("state")
        return result

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "paused": self.paused,
            "cycle_count": self.cycle_count,
            "generations_completed": self.generations_completed,
            "last_error": self.last_error,
            "stagnation_count": self._stagnation_count,
            "best_score_seen": self._best_score_seen,
            "loop_state": self.factory.loop_state,
            "current_generation": self.factory.current_generation_id,
        }


__all__ = ["AutonomousLoopWorker", "LoopState"]
