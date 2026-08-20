# src/nexus_scalp/strategies/factory/worker.py

- PURPOSE: Strategy Factory — Autonomous Loop Worker (2026-08-20): runs
  the autonomous evolution loop (spec 55/56/57/73/74):
  generate → validate → backtest → walk-forward → OOS → robustness → rank →
  select → analyze failures → generate next → repeat. Restart-safe: loop
  state + generation checkpoints persisted (factory_loop_state +
  factory_generations + factory_candidates); on restart `recover()` reloads
  the active generation and resumes from the first candidate without a
  recorded evaluation (spec 41/74). Stopping conditions (spec 55): max
  generations, max runtime, max cost, target elite count, no-improvement
  generations. Stagnation detection (spec 56): exploration pressure
  increases when best scores stall.
- ARCHITECTURE LAYER: Application periodic worker (invoked via
  asyncio.to_thread from LiveEngine — never blocks the asyncio loop).
- RESPONSIBILITY: bounded, exception-isolated generation cycles with
  pause/resume/stop control, persisted loop state, stagnation tracking and
  crash recovery.
- DEPENDENCIES: `factory.orchestrator` (StrategyFactory),
  `factory.store` (emit_event, get_loop_state, list_generations,
  set_loop_state), `factory.models` (LoopState), observability.logging.
- CONNECTS TO: LiveEngine wiring, /api strategy-factory loop endpoints
  (status()), the factory's tick source; the loop state table is also read
  by recover().

- KEY CONCEPTS:
  - Constructor (58-81): budgets — max_generations 20, max_runtime_sec
    3600, target_elite_count 8, no_improvement_generations 4,
    pause_between_cycles_sec 5 (BOTH the throttle and the inter-cycle
    gap). Note max_runtime_sec/target_elite_count are stored but never
    enforced in tick (see pitfalls).
  - `start` (88-111): idempotent; on a persisted RUNNING/PAUSED state it
    flags the factory RECOVERING, logs `[STRATEGY_FACTORY] loop state
    found RUNNING/PAUSED — recovering`, emits LOOP_RECOVERING event, then
    sets RUNNING and persists it.
  - `stop` / `pause` / `resume` (113-129): delegate to the factory control
    plane; pause/resume gated on self.running.
  - `tick` (135-176): gated on running / not paused / not kill-requested /
    pause_between_cycles elapse; checks `_should_stop` BEFORE running
    (generations_completed >= max_generations OR stagnation_count >=
    no_improvement_generations → `_finish_loop("STOPPED", ...)` returns
    False); otherwise `_run_one_generation()`; ANY exception → last_error,
    factory.loop_state = FAILED + persisted FAILED with last_error,
    FAILURE log; returns False (never propagates).
  - `_run_one_generation` (185-209): builds memory (factory.build_memory),
    runs factory.run_generation_cycle(memory), updates
    `_best_score_seen` / `_stagnation_count` (best > seen + 1e-9 ⇒ reset,
    else stagnation++), emits AUTONOMOUS_CYCLE event with best/stagnation/
    elite.
  - `_finish_loop` (211-226): STOPPED + persisted, AUTONOMOUS_LOOP_STOPPED
    event with cycle/generation counts, running = False.
  - `recover` (232-245): reads persisted loop state; no generation_id ⇒
    {"status": "NOTHING_TO_RESUME"}; else factory.resume_generation(gen)
    + resumed_state annotated. Idempotent — already-evaluated candidates
    skipped.
  - `status` (251-262): running/paused/cycle_count/generations_completed/
    last_error/stagnation_count/best_score_seen/loop_state/
    current_generation.
- HOT PATH / PERFORMANCE: 5s minimum cycle gap; one generation per cycle
  bounded by EvolutionConfig budgets; all writes queued; off the tick path.
- EDGE CASES & PITFALLS:
  - `max_runtime_sec` and `target_elite_count` are stored on the worker but
    NEVER checked anywhere in tick/_should_stop — the only stopping
    conditions actually enforced are max_generations and stagnation; the
    spec 55 "max runtime / target elite" stops are dead parameters.
  - `_should_stop` checks stagnation BEFORE running the next generation —
    with no_improvement_generations=4 the worker does 4 stall generations
    then stops; the "increase exploration pressure" (spec 56) is delegated
    to adapt_probabilities inside the orchestrator, but the worker itself
    never boosts exploration — the stagnation floor application happens
    only when memory carries diversity < floor.
  - On `_run_one_generation` failure the worker returns False but does NOT
    increment generations_completed nor call _finish_loop — the loop stays
    RUNNING and the next tick retries (generation id may collide: the
    factory's create_generation computes next number from persisted rows;
    a FAILED generation row with status PENDING/RUNNING still counts in
    max(list)+1, so a NEW generation id is created — no id collision, but
    the failed generation is orphaned until resume_generation is called
    manually).
  - `recover` does not set self.running — a caller must start() first; two
    recovery paths (worker.recover vs factory.resume_generation) exist
    with slightly different semantics.
  - `self.paused` mirrors factory loop state but `tick` reads
    factory._kill_requested directly (private attribute) — the worker
    couples to the orchestrator's internals.