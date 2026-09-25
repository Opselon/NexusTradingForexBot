# PHASE 13 MODEL GENERATION MIGRATION — FINAL REPORT
# Artifact-First Model Factory
# Repository: NexusTradingForexBot (github.com/Opselon/NexusTradingForexBot)
# Date: 2026-08-16

## 1. LEGACY MODEL STATUS
ScalpNet (`src/nexus_scalp/models/scalp_net.py`) is classified as
**LEGACY BASELINE** (control group). NOT deleted; remains loadable and
reproducible for benchmarking/rollback. No new architecture claims
superiority until it beats the baseline on the SAME dataset/split/labels/
friction via the new pipeline.

## 2. FILES CHANGED (new)
- src/nexus_scalp/model_generation/ — 11 modules:
  models.py, artifact_store.py, sample_factory.py, dataset_factory.py,
  model_factory.py, experiment_factory.py, training.py, validation.py,
  runtime.py, replay.py, __init__.py
- src/nexus_scalp/cli/main.py — 7 new CLI commands
- tests/unit/test_model_generation_phase13.py (52 tests)
- tests/integration/test_model_generation.py (3 tests)

## 3-11. ARCHITECTURE + CONTRACTS
See agents/skill.md section 15g for the full write-up. Highlights:
- LabelSchema triple_barrier_3class_v1 (explicit 3-class; WAIT = policy)
- ModelManifest / DatasetManifest / NewsContextSchema (12 fields)
- Sample/Setup/Strategy/Model independently versionable
- Causally-correct historical news context (epoch-us, tz-safe)

## 12-15. FACTORIES
- ModelFactory: LEGACY_SCALPNET_V1 baseline + MLP_V2; TCN/Transformer
  registered-not-built (bounded, evidence-driven)
- DatasetFactory: deterministic parquet artifacts + manifests
- ExperimentFactory: bounded explainable space (4 templates)
- LocalModelRuntime: load/validate/predict/health — NO DB (proven by
  blocking sqlite3 import during prediction)

## 16. CHAMPION/CHALLENGER
Candidate training writes only candidate ids; legacy Champion path
untouched (test-verified). Validation gates reject -> REJECTED, pass ->
CHALLENGER_ELIGIBLE. No auto promotion.

## 17-18. REPLAY + DRIFT
SampleReplay reconstructs sample context + model prediction (news-aware
models get base+news input rebuilt). Feature/prediction drift detection is
an alert/research trigger — no auto-retrain.

## 19-24. VALIDATION / TESTS / GATES
- ValidationFactory: label integrity, class collapse (>=95%), regime
  results, calibration (ECE), OOS floor, news ablation comparison
- 52 unit + 3 integration tests (all behavioral, no dummy asserts)
- Ruff: CLEAN (repo-wide) · Mypy: 145 files, 0 errors
- Full unit regression: 486 passed · Integration: running
- beforePush.sh / beforePush.ps1: pending final run

## 25-26. DOCUMENTATION
- skill.md: section 15g added (architecture, contracts, invariants, CLI,
  legacy classification, tests)
- bugs.md: BUG-036 (news-aware input width manifest) + BUG-037 (label
  contract ambiguity) — both resolved
- README.md: Phase 13 banner, repo layout row, test suite counts

## 27. REMAINING RISKS
- TCN_V2 / TCN_ATTENTION_V1 / TRANSFORMER_V1 registered but not yet
  implemented (by design: baseline-first benchmarking required)
- Full production-scale training still routes through Phase 10
  WalkForwardTrainer; CandidateTrainer is the artifact-first harness
- News feature contribution must be validated per-experiment (ablation
  harness provided; no claim that news helps until proven)

## 28. EXPLICIT NOT IMPLEMENTED
- New neural architectures beyond MLP_V2 (await baseline benchmark)
- Automatic champion promotion (forbidden by design)
- Automatic retrain on drift (alert-only, by design)