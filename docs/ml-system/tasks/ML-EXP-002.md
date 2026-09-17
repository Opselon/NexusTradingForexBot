# ML-EXP-002 — Architectural Ablation Harness: 2D MLP vs 3D TCN vs Attention

STREAM: STREAM F — EXPERIMENTATION
PRIORITY: P2
STATUS: HUMAN DECISION REQUIRED
DEPENDENCIES: ML-EXP-001, ML-ARCH-001, ML-ARCH-002
AGENT_ROLE: AGENT-ML-EXP
OWNERSHIP_SCOPE: scripts/benchmarks/benchmark_architectures.py
HUMAN_DECISION_REQUIRED: YES (Operator decides whether to deprecate or promote 3D sequence modeling)
PARALLELIZATION_CLASS: REQUIRES_DECISION

## OBJECTIVE
Execute a controlled empirical benchmark comparing: 1. 2D MLP ResNet (ScalpNet), 2. 3D Dilated Causal TCN, 3. 3D Causal TCN + Multihead Attention on identical market data, delivering hard evidence for model selection.

## WHY_IT_EXISTS
3D sequence modeling adds substantial computational latency. We currently do not have empirical data proving whether temporal sequence modeling provides genuine statistical edge over 2D snapshots on Gold M1 bars.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/models/scalp_net.py` (Lines: `194-209`)
  - **Symbol:** `ScalpNet.forward`
  - **Behavior:** Dispatches 2D vs 3D based on seq_len; dual path maintained without comparative alpha proof
  - **Classification:** `PRODUCTION (Dual Path)`
  - **Confidence:** 100%
  - **Contradiction:** 3D path exists but production only uses 2D
- **Path:** `src/nexus_scalp/model_generation/benchmark.py` (Lines: `45-90`)
  - **Symbol:** `MATRIX`
  - **Behavior:** Defines template matrix A, B, C, D comparing baseline vs TCN_ATTENTION
  - **Classification:** `RESEARCH`
  - **Confidence:** 100%
  - **Contradiction:** Benchmark not executed on standardized dataset

## FACTS
- Dual-path ScalpNet exists.
- Benchmark matrix structure exists in benchmark.py.

## UNKNOWNs
- OOS economic expectancy and Sharpe delta of 3D attention vs 2D MLP.

## SCOPE
Execute benchmark across 5 expanding folds on standardized 180-day Gold dataset; record fold Sharpe, OOS expectancy, Brier score, and per-tick CPU latency; publish comparative report; STOP for operator decision.

## NON_GOALS
Do not delete 3D sequence modeling before operator reviews benchmark report.

## SOURCE_AREAS
- `src/nexus_scalp/models/scalp_net.py`
- `src/nexus_scalp/model_generation/benchmark.py`
- `src/nexus_scalp/model_lab/architectures.py`

## FILES_LIKELY_TO_CHANGE
- `scripts/benchmarks/benchmark_architectures.py`
- `docs/benchmarks/ARCHITECTURE_BENCHMARK_REPORT.md`

## INVESTIGATION_PLAN
Verify whether CPU inference latency of 3D TCN+Attention satisfies the < 10ms per-tick execution budget.

## IMPLEMENTATION_PLAN
1. Write scripts/benchmarks/benchmark_architectures.py executing 2D MLP, 3D TCN, and 3D TCN+Attention.
2. Train models on identical fold splits using ML-TRAIN-001 deterministic engine.
3. Record classification metrics (balanced accuracy, F1) and economic metrics (OOS expectancy R).
4. Measure p50, p95, and p99 CPU inference latency over 10,000 vectors.
5. Publish docs/benchmarks/ARCHITECTURE_BENCHMARK_REPORT.md.
6. STOP for Human Decision: Operator selects production architecture path.

## TEST_PLAN
- `pytest tests/unit/test_benchmark_runner.py -v`

## BENCHMARK_PLAN
Full 5-fold evaluation across 3 architectures; metrics logged in ExperimentRegistry.

## EVIDENCE_REQUIRED
- Report: docs/benchmarks/ARCHITECTURE_BENCHMARK_REPORT.md
- Experiment manifests in artifacts/benchmarks/
- Recorded human decision

## ACCEPTANCE_CRITERIA
1. All 3 architectures evaluated on identical data and splits.
2. Complete report published with latency, Sharpe, and expectancy deltas.
3. Operator decision recorded.

## ABORT_CONDITIONS
If 3D models fail to train or throw CUDA/CPU dimension mismatch, document error and abort.

## HUMAN_DECISION_REQUIRED
YES (Operator decides whether to deprecate or promote 3D sequence modeling)

## EXPECTED_ARTIFACTS
- `docs/benchmarks/ARCHITECTURE_BENCHMARK_REPORT.md`
- `scripts/benchmarks/benchmark_architectures.py`

## SHARED_FILE_RISK
Low. AGENT-ML-EXP owns benchmark runner.
