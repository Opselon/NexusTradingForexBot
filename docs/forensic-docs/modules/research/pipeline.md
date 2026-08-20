# src/nexus_scalp/research/pipeline.py

- PURPOSE: PHASE 09B end-to-end research orchestrator:
  dataset → discovery → backtest → walk-forward → OOS → robustness → score →
  registry. A candidate NEVER becomes live automatically; promotion is
  operator-gated on the production side; research is OFFLINE/BACKGROUND and
  never blocks the LiveEngine tick path (spec 31/32/42).
- ARCHITECTURE LAYER: Research orchestration (invoked from the worker via
  asyncio.to_thread; no adapter, no order authority).
- RESPONSIBILITY: run each gate as an independently callable stage, persist
  registry entries + research_runs, and (TASK-21) drive the full
  observability gate chain (research_gates / events / evidence / snapshots)
  when an observability facade is attached (legacy mode otherwise).
- DEPENDENCIES: BacktestEngine, WalkForwardEngine, OOSGate,
  RobustnessEngine, compute_strategy_score, ResearchDatasetBuilder,
  discover_candidates, StrategyRegistry, evidence module (EvidenceArtifact,
  GateStatus, ...), observability store facade, models.
- CONNECTS TO: worker._refresh_validation, seeder-driven registry rows,
  factory orchestrator (evaluate_candidate → validate_candidate + dataset),
  research store/observability reads.

- KEY CONCEPTS:
  - `_select_family` (lines 50-71): TASK-4 family-select validation — when
    discovery_evidence.sample_ids exists, gates run on the candidate's OWN
    context family only (a "LONDON RANGING" candidate is no longer scored
    on 22 unrelated families); falls back to the full dataset (legacy
    revalidates) when sample_ids absent or no family rows match.
  - `validate_candidate` (lines 124-578): the complete gate chain per run.
    - Run identity: `RUN-<stable_digest(strategy,version,now)[:6]>.upper()`
      — one unique run per validation attempt; runs are append-only.
    - TASK-21 observability: snapshot FIRST (build_run_snapshot over the
      live candidate definition + family dataset, stored via
      store_run_snapshot returning a fingerprint), then RECORD_EVENT
      RESEARCH_RUN_STARTED.
    - Gate 0 STATIC_VALIDATION (`_static_validation_problems`, lines
      679-697): symbol + fingerprint present, entry/exit logic non-empty,
      feature_dimension >= 1 — malformed candidates fail BEFORE the costly
      backtest (spec 14) → registered REJECTED.
    - Gate 1 BACKTEST: `use_split=True` (train+val only; never OOS);
      total_trades == 0 → gate FAILED with FailureClass.DATA + STRATEGY_
      BLOCKED event + retryable=True → registered REJECTED (empty family).
    - Gate 2 WALK_FORWARD: n_folds=3, purge/embargo passed through; gate
      status from wf.passed; failure class RESEARCH when not passed.
    - Gate 3 OOS (hard gate): status from oos.status; FAILED →
      FailureClass.RESEARCH.
    - Gate 4 ROBUSTNESS: status from rob.status.
    - Gate 5 SCORING: compute_strategy_score(family_ds, bt, wf, oos, rob);
      verdict → lifecycle VALIDATED / REJECTED / DISCOVERED; score gate
      always PASSED; STRATEGY_PROMOTED / STRATEGY_REJECTED event recorded.
    - `_register` (lines 584-619): builds StrategyRegistryEntry with
      confidence = score.sample_confidence, sample_count from backtest,
      validation_lineage [now:lifecycle] and upserts.
    - `_record_run` (lines 621-676): ResearchRun envelope inserted into
      research_runs ON CONFLICT(run_id) DO NOTHING via the queue; summary
      carries expectancy, oos/robustness status, score, family_samples,
      primary_failure reasoning (OOS > ROBUSTNESS > WALK_FORWARD) and
      rejection_reason.
  - `discover` (lines 107-118): bounded discovery pass (no registry writes).
- HOT PATH / PERFORMANCE: every gate is a pure computation over the family
  dataset (O(n) backtests × 6); the heavy part is the dataset build upstream;
  runs on the worker thread, bounded to MAX_VALIDATIONS_PER_CYCLE=5 in
  worker; all persistence queued.
- EDGE CASES & PITFALLS:
  - Aborted-empty-family path registers REJECTED with backtest embedded but
    REQUIRED gates missing — invariant_check's REJECTED rule accepts it
    because backtest.total_trades==0 counts as a failed gate.
  - `_register` sets lifecycle directly (DISCOVERED→VALIDATED jump) without
    consulting lifecycle.transition — the state machine is bypassed on the
    main path (see lifecycle.md pitfall).
  - `_record_run` hardcodes config {n_folds:3, purge 0, embargo 0} regardless
    of the caller's actual n_folds/purge/embargo parameters — run records
    can misstate the configuration that produced them (reproducibility gap;
    the snapshot (when observability attached) is the accurate source).
  - run_id is time-dependent — the same strategy revalidated twice yields
    two different run ids (append-only semantics preserved).
  - `last_run` in-memory only; worker status reads it via format_research_
    worker_status -> validated_count, not run records.