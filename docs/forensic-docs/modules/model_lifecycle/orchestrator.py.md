# src/nexus_scalp/model_lifecycle/orchestrator.py

- **PURPOSE:** Model Lifecycle Orchestrator — the end-to-end controlled training
  pipeline (spec 25/35): VERIFIED EXPERIENCE → RESEARCH DATASET → TRAINING
  DATASET → CANDIDATE MODEL (offline, staging) → VALIDATION GATES → CHAMPION
  COMPARISON → CHALLENGER (shadow-eligible, NEVER auto-promoted).
- **ARCHITECTURE LAYER:** Research/ML orchestration. Holds no adapter, no order
  manager, no risk engine — cannot place/modify/close an order.
- **RESPONSIBILITY:** Wire dataset → trainer → gates → registry → store for one
  controlled training pass; expose dataset build + champion comparison.
- **DEPENDENCIES:** ledger, ChampionManager, TrainingDatasetBuilder,
  ChallengerTrainer, gates (11 imported gate fns), ModelLifecycleRegistry,
  TrainingRunStore, comparator, Phase 09 research engines (OOSGate,
  RobustnessEngine, WalkForwardEngine) + research models for evaluation.
- **CONNECTS TO:** worker (drives run_controlled_training), web/CLI entry points.
- **NOTABLE:** the orchestrator does NOT import/compose `run_gates` from
  gates.py — it re-implements the loop inline (lines 281-291); GATE10 is never
  invoked in this module's gate list.

- **KEY CONCEPTS:**
  - `run_controlled_training` (line 110): ① trains a candidate via
    ChallengerTrainer (staging paths; parent champion lineage recorded when the
    champion exists); ② evaluates gates; ③ run status stays COMPLETED even when
    gates fail (`"training finished"`, line 151-154 — eligibility is decided by
    gates, not status); ④ persists the run; ⑤ registry transition:
    CHALLENGER if all gates passed else REJECTED (line 160) with reason +
    gate_summary; returns summary dict (run, gates, all_gates_passed,
    candidate_status, registry_updated, champion_unavailable).
  - NO AUTO-PROMOTION: the strongest CHALLENGER status is stored
    shadow-eligible; nothing here rewrites the champion artifact or the
    champion registry row.
  - `_evaluate_gates` (line 235): always runs GATE1_DATASET, GATE2_SCHEMA,
    GATE3_LABELS, GATE4_STABILITY, GATE5_VALIDATION (floors from run.metrics),
    GATE11_ARTIFACT (run.artifacts[0] or None — a missing artifact ⇒ failed
    gate), GATE12_REPRODUCIBILITY. Then, ONLY when training COMPLETED with
    artifacts: OOS (GATE7), robustness (GATE8), risk (GATE9 via oos_result) are
    appended; walk-forward (GATE6) is attempted separately. IMPORTANT:
    a failed/absent OOS/robustness evaluation appends NOTHING — those gates are
    silently OMITTED rather than failed (gates 6-9 are optional-by-absence).
  - `_evaluate_oos`/`_evaluate_robustness`/`_evaluate_walkforward` (lines
    293-336) evaluate the Phase 09 engines over a `ResearchDataset` converted
    from executed+closed dataset rows (`_research_dataset`, line 302; synthetic
    price/MAE/MFE conventions: realized_pnl_usd = outcome_r×100, risk_distance
    10.0, holding 300s, mae 0.2R, mfe 1.0R).
  - `compare_against_champion` (line 200): uses run.metrics as CHALLENGER
    metrics but a DEFAULT-ZERO champion metrics dict (docstring admits: "In
    production, these would come from the research registry... expose the
    comparison skeleton", lines 212-222) — the comparison is a structural
    skeleton, not an evidence-backed verdict, and is persisted via save_comparison.

- **HOT PATH / PERFORMANCE:** Offline; invoked from the worker (bounded) only.

- **EDGE CASES & PITFALLS:**
  - Gate omission bug: `_evaluate_gates` catches ALL exceptions from OOS/robust-
    ness evaluation and logs, so a broken research engine silently disables
    GATE7/8/9/6 — the pass verdict then rests on 7 gates. An explicit
    `all gates present` invariant is NOT enforced (GATE12 checks lineage only).
  - GATE10 (champion comparison) is defined but never wired into
    `run_controlled_training` — a candidate can become CHALLENGER without the
    multi-dimension comparison gate.
  - `evaluate_champion` parameter is accepted (line 117) but unused in
    `_evaluate_gates`.
  - Gate numbers in gate fn names are stable; the registry `gate_summary` uses
    gate names as keys; the summary `metrics["validation_gates"]="FAIL"` tag
    (line 152) informs dashboards of gate failure.
  - Worker-chosen hyperparameters (num_folds=5, epochs_per_fold=3, batch 64,
    num_epochs=3) drastically reduce training quality versus trainer defaults —
    a worker run is a smoke cycle, not a production-quality training.