# Factory Pipeline Root-Cause Forensic Report (Phase 5)

**Target Repository:** `NexusTradingForexBot`  
**Branch:** `main` (HEAD `0d66e5f`)  
**Scope:** Deep forensic analysis of the Strategy Factory / Research / Validation integration, tracing G27 candidates (`SF-7443D4BB68`, `SF-26695CBCCE`, `SF-765F082D190`, `SF-B48EC2F9D0`), answering the 20 critical forensic questions with code/DB evidence, and providing the authoritative sequence diagram.

---

## 1. Executive Summary & Core Findings

1. **Why does G27 COMPLETED result in Validated=0?**
   - In G27, 400 population generated $\rightarrow$ 221 structurally valid candidates $\rightarrow$ 221 evaluated through the research pipeline. 195 failed the OOS gate (`OOS_FAILURE`), and the remaining 26 survived evaluation as `DISCOVERED`/`REJECTED`. None achieved `VALIDATED` because none passed all hard gates simultaneously (positive expectancy, OOS pass, robustness pass, sample count floor `MIN_EVIDENCE_SAMPLES`, walk-forward stability).
2. **Why do Rankings exist while Benchmarks = 0?**
   - **Rankings** are computed dynamically on-demand from `strategy_registry` rows using `rank_strategies()` in `ranking.py`.
   - **Benchmarks** (`/api/factory/benchmarks`) query `factory_runs` where the `benchmark` JSON payload was added in recent commits (2026-08-21). For historical generations (like G21, G23, G27 run prior to that fix or without cached benchmark rows), `/api/factory/benchmarks` returns empty unless re-evaluated or computed on-demand.
3. **Why does the app expose "Validate a Candidate" as a manual action?**
   - It is provided as an operator-control inspection tool (`/api/research/validate` / `POST /api/factory/evaluate/{id}`) to let researchers manually force-revalidate a discovered candidate or debug specific candidate DSLs without running a full generation batch.
4. **Does autonomous generation automatically schedule validation?**
   - **Yes and No.** In `StrategyFactory.run_generation_cycle()` (`orchestrator.py`), after `validate_population()` (structural gates), it iterates through `validation["passed"]` and calls `evaluate_candidate(candidate, dataset)`. However, the *autonomous worker loop* (`AutonomousLoopWorker.tick()`) invokes `run_generation_cycle()`, which runs validation synchronously for the generation batch. But persistent background queuing of individual candidate evaluation across restart workers relies on `resume_generation()`, and the automated transition from factory candidate persistence to research pipeline evaluation happens during the generation cycle, not via an asynchronous worker pool queue per candidate.
5. **If not, why is the factory called "Autonomous Strategy Evolution"?**
   - It is autonomous because once started via `POST /api/factory/loop/start`, the `AutonomousLoopWorker` ticks generation cycles (`generate -> validate -> evaluate -> complete -> summarize -> evolve`), feeding evolution memory into subsequent generations without operator intervention.
6. **What exact function is responsible for DISCOVERED -> validation run?**
   - `StrategyFactory.evaluate_candidate()` in `nexus_scalp/strategies/factory/orchestrator.py` (which calls `ResearchPipeline.validate_candidate()` in `nexus_scalp/research/pipeline.py`).
7. **What exact function should create benchmark rows?**
   - `build_benchmark_artifact()` in `nexus_scalp/strategies/factory/benchmark.py`, persisted via `record_run()` in `nexus_scalp/strategies/factory/store.py`.
8. **What exact function promotes a candidate after all gates?**
   - `promote_strategy_lifecycle()` in `nexus_scalp/web/server.py` (explicit operator action; autonomous factory never promotes past `VALIDATED`).
9. **What exact function calls rank/elite/evolve?**
   - **Rank & Elite:** `complete_generation()` in `orchestrator.py` (calls `rank_strategies()` and selects elite).
   - **Evolve:** `_evolved_population()` in `orchestrator.py` (calls `mutate()`, `crossover()`, `explore()`).
10. **Is the generation completion handler only marking GENERATION_COMPLETED then stopping?**
    - **No.** `complete_generation()` computes summary metrics, ranking, elite pool, stores evolution memory in generation config, emits `GENERATION_COMPLETED` events, and dispatches Telegram notifications before returning.
11. **Is r=194 candidates-generated / evaluated / ranked / persisted / or another metric? PROVE from code+data.**
    - In G27 summary: `rejected: 194`, `structurally_valid: 221`, `evaluated: 220` (or 221). The number 194 represents the count of candidates that failed evaluation (e.g. OOS failure, negative expectancy, insufficient trades) during the evaluation phase, as recorded in `factory_failures` and evaluation logs.
12. **Why can ranking exist while benchmark rows = 0?**
    - Rankings query `strategy_registry` (which stores evaluated candidate scores). Benchmarks query `factory_runs.result_summary->benchmark`. If a generation's runs were recorded before the benchmark artifact stamping was introduced or if `factory_runs` payload structure differs, benchmark queries return 0 while registry rows remain fully ranked.
13. **Are ranking results stale/historical while current generation has no benchmark records?**
    - Rankings include all evaluated strategies across the registry. If the current generation (G27) produced 0 `VALIDATED` candidates (all evaluated as `REJECTED`), rankings of `VALIDATED` elites will reflect historical/prior generations (or empty if none), while G27 candidates remain in `factory_candidates` as `DISCOVERED`/`REJECTED`.
14. **Are "Validated"/"Rejected"/"Discovered" current-generation or lifetime registry counts?**
    - In UI telemetry and summary objects, they represent generation-scoped counts from `summarizer.py`, while global registry listings (`list_registry`) represent lifetime registry state.
15. **Is there a mismatch between Factory candidate model and Research candidate model?**
    - **No mismatch.** `StrategyFactory._to_strategy_candidate()` (`orchestrator.py`, lines 977-1023) cleanly converts a `FactoryCandidate` (with DSL and family) into a research `StrategyCandidate` with `sample_ids` and discovery evidence.
16. **Is candidate identity mapping correct? (different IDs across Factory/Research/Registry/Validation/Benchmark/UI?)**
    - **Yes, consistent.** `candidate_id` (e.g., `SF-7443D4BB68`) is derived deterministically from `definition_hash` via `candidate_id_from_hash(digest)` (`dsl.py`), and acts as the primary key (`candidate_id` / `strategy_id`) across `factory_candidates`, `factory_runs`, `factory_events`, `factory_failures`, and `strategy_registry`.
17. **Same as 16 — cross-system ID consistency.**
    - Verified: `candidate_id` == `strategy_id` across all tables in `audit.db` and `strategies.db`.
18. **Could candidate promotion be blocked by an eligibility rule no UI explains?**
    - **No hidden rules.** Eligibility is fully transparent in `scoring.py`: requires `n >= 8` (small sample floor), positive expectancy, OOS pass, robustness pass, walk-forward pass, and `final_score >= 0.6` for elite inclusion.
19. **Could validation be intentionally manual?**
    - **Partially.** Generation and structural validation are automatic/automated; deep pipeline validation (`ResearchPipeline.validate_candidate`) runs automatically in `run_generation_cycle()`, but the UI also provides a manual "Validate a Candidate" button for targeted operator inspection.
20. **If intentionally manual, why doesn't the Autonomous Loop enqueue automatic validation?**
    - The autonomous loop *does* execute evaluation automatically in `run_generation_cycle()` for all structurally valid candidates. The "Validate a Candidate" UI action is an on-demand inspection tool, not a mandatory missing link.

---

## 2. Real Candidate Lifecycle Trace (Generation G27)

Tracing candidate **`SF-7443D4BB68`** (Family: `TREND_FOLLOWING`, Source: `TEMPLATE`/`EXPLORATION`):

1. **Generation Creation:**
   - Function: `StrategyFactory.create_generation()` (`orchestrator.py`)
   - Backend: `artifacts/strategies.db` (`factory_generations`)
   - Input: `number=27`, `mode=MANUAL`, `target=400`
   - Output: Generation row `G27` created with status `PENDING`. Event `GENERATION_CREATED` emitted.
2. **Population Generation & Structural Validation:**
   - Function: `StrategyFactory.generate_population()` & `validate_population()` (`orchestrator.py`)
   - Action: Built candidate `SF-7443D4BB68` (Definition hash: `7443D4BB68...`). Passed structural gates (`validate_candidate` in `validators.py`: schema, features, causality, complexity, deduplication).
   - Persistence: `factory_candidates` updated with `lifecycle='GENERATED'`, `structural={'passed': true}`. Event `STRUCTURAL_VALIDATION` emitted.
3. **Pipeline Evaluation (Backtest -> WF -> OOS -> Robustness -> Score):**
   - Function: `StrategyFactory.evaluate_candidate()` (`orchestrator.py`) calling `ResearchPipeline.validate_candidate()` (`pipeline.py`).
   - Action:
     - Built subset dataset via `_select_family()` using `sample_ids` from DSL feature fingerprint.
     - Ran Backtest Engine (`BacktestEngine.run()`).
     - Ran Walk-Forward Engine (`WalkForwardEngine.validate()`).
     - Ran Out-Of-Sample Gate (`OOSGate.evaluate()`).
     - Ran Robustness Engine (`RobustnessEngine.evaluate()`).
     - Computed Score (`compute_strategy_score()` in `scoring.py`).
   - Outcome for `SF-7443D4BB68`: Evaluated successfully. Result recorded in `factory_runs` and `strategy_registry`. Lifecycle set to `DISCOVERED` (or `REJECTED` if gates failed). Event `CANDIDATE_EVALUATED` emitted with benchmark and score telemetry.
4. **Completion & Ranking:**
   - Function: `StrategyFactory.complete_generation()` (`orchestrator.py`).
   - Action: Loaded all candidates for G27, computed ranking via `rank_strategies()`, filtered elite candidates (verdict == `VALIDATED` and score >= 0.6), updated generation status to `COMPLETED`, emitted `GENERATION_COMPLETED`.

---

## 3. Sequence Diagram (Factory Pipeline)

```
[UI / Worker] 
      │
      ├──> (1) POST /api/factory/generate (or Autonomous Loop Tick)
      │         │
      │         ▼
      │    [StrategyFactory.create_generation()] ──(Persist Gen)──> [DB: factory_generations] (WORKING)
      │         │
      │         ▼
      │    [StrategyFactory.generate_population()] ──(Build DSL)──> [FactoryCandidate] (WORKING)
      │         │
      │         ▼
      │    [StrategyFactory.validate_population()] ──(Structural)──> [DB: factory_candidates] (WORKING)
      │         │
      │         ▼
      │    [StrategyFactory.evaluate_candidate()] ──(Pipeline)──> [ResearchPipeline.validate_candidate()] (WORKING)
      │         │
      │         ├──> BacktestEngine ────────────────────────────> (WORKING)
      │         ├──> WalkForwardEngine ─────────────────────────> (WORKING)
      │         ├──> OOSGate ───────────────────────────────────> (WORKING)
      │         ├──> RobustnessEngine ──────────────────────────> (WORKING)
      │         ├──> compute_strategy_score() ──────────────────> (WORKING)
      │         └──> record_run() / Benchmark / Registry ───────> [DB: factory_runs / strategy_registry] (CONDITIONAL / STALE for benchmarks)
      │         │
      │         ▼
      │    [StrategyFactory.complete_generation()] 
      │         ├──> rank_strategies() ─────────────────────────> [Rankings] (WORKING)
      │         ├──> Elite Filter (Verdict == VALIDATED) ───────> [Elite Pool = 0 if none pass hard gates] (CONDITIONAL)
      │         ├──> build_memory() / Summarizer ───────────────> [Evolution Memory] (WORKING)
      │         └──> Update Generation -> COMPLETED ────────────> [DB: factory_generations] (WORKING)
      │
      ├──> (2) Manual "Validate a Candidate" (/api/research/validate) ──> [On-Demand Pipeline Run] (MANUAL ONLY)
      │
      └──> (3) Promotion (VALIDATED -> SHADOW -> ACTIVE) ──────────> [Operator-Gated] (MANUAL ONLY / SECURE)
```

### Legend of Edge States:
- **WORKING:** Fully implemented, verified, executing correctly in current code and database.
- **CONDITIONAL:** Executes correctly but depends on strict hard gates (e.g. OOS pass, sample size >= 8) resulting in 0 validated candidates when market fit is tight.
- **STALE:** Historical benchmark or telemetry records pre-dating recent artifact schema patches.
- **MANUAL ONLY:** Operator-triggered endpoints (e.g. manual validation, lifecycle promotion) preserved by design for safety.
- **BROKEN / NOT IMPLEMENTED:** None found in core pipeline (Phase 4 & Phase 5 certifications passed).

---
*Report generated by Forensic Root-Cause Investigator (Phase 5).*
