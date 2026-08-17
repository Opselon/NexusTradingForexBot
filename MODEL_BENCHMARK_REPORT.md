# MODEL GENERATION FIRST NEW ARCHITECTURE BENCHMARK — FINAL REPORT
# TCN_ATTENTION_V1 vs LEGACY_SCALPNET_V1 (fair A/B/C/D matrix)
# Repository: NexusTradingForexBot
# Date: 2026-08-16/17

## 1. NEW ARCHITECTURE
TCN_ATTENTION_V1 — dedicated causal temporal model:
Linear projection → N dilated causal conv blocks (residual + LayerNorm) →
multi-head self-attention → final-state pooling → 3-logit head.

## 2. WHY THIS ARCHITECTURE
The repo's input is a 50D per-bar feature vector with a temporal dimension
already present in the legacy ScalpNet (which routes 3D inputs through a
causal TCN path). TCN_ATTENTION_V1 makes temporal causality the PRIMARY
contract with an explicit 3-logit head (no legacy 4-head WAIT bridge),
bounded parameters, and full configurability (hidden_dim/blocks/heads/
dropout) — the natural first candidate per the task's preferred choice.

## 3. FILES CHANGED (new)
- src/nexus_scalp/model_generation/architectures.py (TCNAttentionV1)
- src/nexus_scalp/model_generation/sequence.py (SequenceBuilder)
- src/nexus_scalp/model_generation/sequence_training.py (SequenceCandidateTrainer)
- src/nexus_scalp/model_generation/benchmark.py (BenchmarkRunner + report)
- model_factory.py (registry: TCN_ATTENTION_V1 now built; TCN_V2/TRANSFORMER stay registered-not-built)
- experiment_factory.py (+2 templates: tcn_attention_v1, tcn_attention_v1_news)
- validation.py (+ confusion_and_class_metrics, head_to_head)

## 4. SEQUENCE / DATA CONTRACT
- SequenceBuilder: timestamp_0 < ... < timestamp_N, same symbol/timeframe
  per window, max-gap configurable, deterministic, causal (left-pad only).
- news_enabled=False excludes news_* columns (50D); news_enabled=True adds
  the 12 NewsContext fields (62D) — dimensions come from the live schema,
  never hardcoded.

## 5-9. EXPERIMENTS (SAME dataset ds_cb30f87520e9e6a4, 800 rows,
3-class triple_barrier labels, friction $0.35, embargo 3, seed 4242)
| Kind | Arch | News | Status | val_acc | ECE | macro-F1 | Gates |
|------|------|------|--------|---------|-----|----------|-------|
| A | LEGACY_SCALPNET_V1 | OFF | COMPLETED | 0.8187 | n/a | 0.2934 | REJECTED |
| B | LEGACY_SCALPNET_V1 | ON | COMPLETED | 0.8187 | n/a | 0.2934 | REJECTED |
| C | TCN_ATTENTION_V1 | OFF | COMPLETED | 0.7261 | 0.246 | 0.2597 | REJECTED |
| D | TCN_ATTENTION_V1 | ON | COMPLETED | 0.7898 | 0.199 | 0.2554 | REJECTED |

## 10-18. METRICS SUMMARY
- Validation: all four REJECTED by ValidationFactory gates (class-collapse /
  regime coverage / OOS floor). None earns CHALLENGER eligibility.
- OOS / robustness / drawdown: not separately scored beyond the gate
  results (gate = REJECTED => no promotion path exercised).
- Calibration: TCN ECE 0.246 (news off) / 0.199 (news on). Legacy 2D
  trainer does not emit ECE (reporting gap, not bias).
- Regime: TRENDING/RANGING label fractions recorded in validation.
- News effect: news ON improved TCN val_acc +0.064 and ECE −0.047, but
  macro-F1 declined (−0.004). NO statistical significance at n=800.
- Resource: TCN params bounded (hidden 128, 3 blocks, 4 heads);
  seq training ~2-3s for 800 rows on CPU.

## 19. STATISTICAL CONFIDENCE
LOW EVIDENCE (single synthetic-style dataset run, n=800, no bootstrap).
Point estimates only. No significance claim in either direction.

## 20-21. CHALLENGER / CHAMPION DECISION
- NO candidate passed validation → NO CHALLENGER promoted.
- CHAMPION untouched (legacy artifact hash verified unchanged; candidate
  training writes only candidate ids).

## 22-26. TESTS / GATES (final)
- Phase 13 + 13b focused: **88 passed** (60 + 28)
- Full unit (excluding the single user-WIP web flake): **523 passed, 0 failed**
- Full integration: **59 passed**
- Ruff: **ALL CHECKS PASSED** (repo-wide) · Format: 209 files clean
- Mypy (model-generation scope): **0 errors, 22 files** · repo-wide only
  flags the user-WIP `web/server.py` BoundLogger sites (10, pre-existing)
- beforePush.sh: mypy step aborts on the user-WIP web/server.py errors
  ONLY (my subsystem is mypy-clean) · beforePush.ps1: executed (same WIP
  caveat)
- NOTE: `tests/unit/test_web_security.py::test_06` fails ONLY in the full
  suite (passes in isolation and with benchmark tests) — a pre-existing
  user-WIP suite-ordering flake (its new sanitized-error `web/errors.py`
  behavior contradicts the test's expectation). Not caused by, and not
  fixable within, the model-generation subsystem.

## 27-28. ARTIFACT / REPORT PATHS
- Artifacts: artifacts/model_generation/{datasets,experiments,models}/<id>
- Benchmark reports: artifacts/model_generation/model_benchmark_report.json
  + model_benchmark_report.md (runtime-generated; also written to the
  runner's report_dir during each run).

## 29. BUGS FOUND/FIXED (this task)
- SequenceBuilder included news_* columns even with news_enabled=False
  (input_dim 62 vs manifest 50) → runtime rejected load. Fixed with
  news_enabled flag threading (sequence.py + sequence_training.py +
  benchmark.py). No BUG number needed (new code, fixed before merge).
- Benchmark _predict_probs 2D path ignored manifest news_enabled (scaler
  shape mismatch 50 vs 62) → fixed to respect the manifest.
- (Both are pre-release defects in new code; no production defect.)

## 30-31. DOCUMENTATION
- agents/skill.md: add section 15h (this benchmark + TCN_ATTENTION_V1)
- agents/bugs.md: no new production bug entries (new-code defects fixed
  before merge)

## 32. REMAINING RISKS
- Synthetic 800-row dataset: NOT evidence of real-market superiority in
  either direction; real historical bars needed for a production decision.
- Legacy ECE not reported (2D path doesn't compute it) — a reporting gap
  for future A/B calibration comparisons.
- TCN training cost higher than legacy MLP path (sequence windows).

## 33. EXPLICIT NOT IMPLEMENTED
- No auto-promotion; no Champion swap; no TCN_V2/TRANSFORMER_V1 builds;
  no hyperparameter search beyond the bounded 2-template matrix;
  no bootstrap/CI machinery (flagged LOW EVIDENCE instead).