# src/nexus_scalp/strategies/factory/orchestrator.py

- PURPOSE: Strategy Factory Orchestrator (2026-08-20) — coordinates the
  full strategy lifecycle: generate → validate → backtest (VIA THE
  AUTHORITATIVE research pipeline) → walk-forward → OOS → robustness →
  score → rank → elite selection → failure analysis → evolution → next
  generation (spec 109 phases B-H). RESPONSIBILITY BOUNDARY (spec
  14/62/63/105): never computes backtest performance itself — all measured
  results come from `ResearchPipeline.validate_candidate`; never touches
  the live path (no adapter/risk engine/order authority); global risk
  governance always wins; generated strategies only declare risk
  ASSUMPTIONS. CRASH RECOVERY (spec 41/74/75): every candidate persisted
  per stage; `resume_generation()` reloads and continues from the first
  candidate without a recorded evaluation.
- ARCHITECTURE LAYER: Application orchestration (wired into LiveEngine as
  `strategy_factory`, runs off the tick path via asyncio.to_thread).
- RESPONSIBILITY: control plane (loop start/pause/resume/stop, spec 73),
  generation lifecycle + persistence, population generation (G0 mixture /
  evolved mixture with adaptive operator probabilities), structural
  validation over the population, per-candidate evaluation through the
  research pipeline with derived failure reasons, generation completion
  (rank + elite + summary + memory), and resume/crash recovery.
- DEPENDENCIES: dsl (generators/hash/catalog), evolution (mutate/crossover/
  explore/adapt_probabilities), models, provider (optional LLM), ranking
  (selection_score/rank_strategies/population_diversity), store (all
  persistence), summarizer (build_summary/memory_summary), validators
  (validate_candidate), research.store (list_registry / get_registry_entry),
  research.pipeline, research.candidates (StrategyCandidate), telegram
  (send_factory_event lazily).
- CONNECTS TO: AutonomousLoopWorker (drives it), LiveEngine wiring, web/API
  (loop_status, generation views), research registry (its evaluation
  writes standard strategy_registry rows).

- KEY CONCEPTS:
  - Control plane (130-194): start_loop (persisted RUNNING), pause_loop /
    resume_loop (only from RUNNING/PAUSED), stop_loop (kill switch — sets
    STOPPING + `_kill_requested` then STOPPED; blocks new generations and
    new LLM requests, spec 106), loop_status telemetry incl. operator_stats.
  - create_generation (200-242): G<number> with number = max(list)+1
    (bounded MAX_GENERATIONS_READ=1000); persists shell + GENERATION_
    CREATED event + Telegram GENERATION_STARTED.
  - generate_population (270-298): marks generation RUNNING; G0 (number<=1)
    via dsl.generate_generation_zero + `_ensure_family_coverage` (all 11
    families present); later generations via `_evolved_population`:
    elite preservation (int(population × elite_preservation_rate) from
    `_load_elite` — registry VALIDATED rows with final_score ≥ 0.6, sorted,
    capped elite_size; missing elites backfilled with fresh templates) +
    adaptive-probability loop (mutation / crossover / exploration; failed
    operators fall back to a fresh TEMPLATE candidate — never an invalid
    strategy); then population-level dedup (`_dedupe_population` by
    definition_hash, spec 13).
  - validate_population (494-532): gate chain per candidate with
    accumulating existing_hashes (dedup), persist verdict per candidate
    (lifecycle REJECTED + record_failure for failures); emit
    STRUCTURAL_VALIDATION event.
  - evaluate_candidate (575-668): converts to research StrategyCandidate
    (`_to_strategy_candidate`, 670-706 — strategy_id = SF-id,
    feature_schema_id "scalp_v3", feature_dimension 70, context embeds the
    full DSL + family + symbol + fingerprint for family-select validation),
    calls pipeline.validate_candidate, derives failure reasons
    (`_derived_failure_reasons`, 708-745 — REJECTED verdict → map
    OOS/ROBUSTNESS/WALK_FORWARD; otherwise factory floors: min_trades 20,
    expectancy > 0, drawdown ≤ 4.0R, profit_factor ≥ 1.2, OOS PASS,
    robustness PASS; INCONCLUSIVE-with-no-reason → INSUFFICIENT_TRADES),
    persists candidate lifecycle + failures + CANDIDATE_EVALUATED event,
    tallies operator survival (survived/elite when VALIDATED/SHADOW/ACTIVE).
    Any exception → lifecycle FAILED + record_failure with stage BACKTEST,
    returns None (never raises).
  - complete_generation (772-818): builds GenerationSummary (via
    summarizer.build_summary over the generation's candidates + decoded
    registry rows from `_registry_rows_for_generation`), ranks
    (rank_strategies limit 100), elite = ranked VALIDATED rows capped
    elite_size, persists COMPLETED + config.summary, GENERATION_COMPLETED
    event + Telegram.
  - build_memory (835-848): pulls stored summaries from generations'
    config + registry rows → memory_summary (bounded windows).
  - run_generation_cycle (854-881): one full cycle (manual mode default)
    with kill-switch checks between candidates.
  - resume_generation (887-907) + `_candidate_from_row` (909-928):
    crash recovery — reloads generation + candidates with lifecycle
    GENERATED/None/"" and evaluates them; already-evaluated candidates
    skipped (idempotent).
  - `_decode_registry_row` (931-957): decodes the raw JSON-text registry
    row columns into nested dicts for ranking/summaries (BUG-075
    discipline: ''/null → {}).
- HOT PATH / PERFORMANCE: off the tick path (asyncio.to_thread); writes
  queued; generation work bounded by budgets (400 candidates default);
  dataset built once per cycle via pipeline.dataset_builder.build().
- EDGE CASES & PITFALLS:
  - `_evolved_population` rehydrates elites from registry rows via
    `_candidate_from_registry` reading `context_definition.dsl` — a row
    whose context lacks a "dsl" key canonicalizes to an EMPTY StrategyDsl
    (all defaults → HYBRID family) and produces a degenerate mutant parent;
    `_load_elite` does not filter for dsl presence.
  - `_tally_operator` increments "generated" for the CHILD's op but the
    fallback TEMPLATE child records op "TEMPLATE" — population stats mix
    real operator outputs with fallbacks.
  - `evaluate_candidate` passes NO run_id — the pipeline derives a fresh
    RUN- id per call; resuming a crashed generation therefore creates NEW
    runs for re-evaluated candidates (the resume loop only skips
    candidates whose ROW lifecycle was already moved off GENERATED, so
    crashed mid-evaluation rows re-run — acceptable but creates run
    duplication).
  - `_derived_failure_reasons` INCONCLUSIVE-with-no-reason defaults to
    INSUFFICIENT_TRADES (line 743-744) — a candidate that merely lacks a
    reason gets a potentially misleading "insufficient trades" label.
  - `run_generation_cycle` ignores `mode`/population when called with
    defaults; `create_generation(mode=...)` records MANUAL even in the
    autonomous loop (worker calls run_generation_cycle which hardcodes
    mode="MANUAL") — loop-state vs generation-mode can disagree.
  - Telegram events are best-effort (never raise; `_send_telegram` checks
    notifier.enabled) — failure only logged.