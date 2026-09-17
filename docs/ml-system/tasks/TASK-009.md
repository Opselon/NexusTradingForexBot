# TASK-009 — 2D Snapshot vs 3D Sequence Routing Verification & Architectural Decision

Priority: P2
Status: HUMAN DECISION REQUIRED
Type: Architecture & Research Decision
Dependencies: TASK-001, TASK-003
Blocks: None
Human Decision Required: YES (Decide whether to deprecate 3D sequence modeling or invest in live sequence buffering)
Risk: Medium (Research benchmarking, CPU inference latency trade-offs)
Estimated Scope: Benchmark suite, comparative latency/expectancy report (~200 LOC)

## Objective
Execute a controlled empirical benchmark comparing 2D ScalpNet (MLP ResNet) against 3D ScalpNet (Causal TCN + MultiheadAttention, seq_len=32) on identical market data, delivering hard evidence for the sequence deprecation decision.

## Problem / Why
3D sequence modeling was designed for temporal attention over 32 M1 bars, but live production inference operates exclusively on single 2D snapshots. Maintaining the complex 3D causal conv and multihead attention layers in ScalpNet adds maintenance burden unless empirical alpha is proven.

## Current Evidence
- **Path:** `src/nexus_scalp/models/scalp_net.py` (Lines: `194-209`)
  - **Symbol:** `ScalpNet.forward`
  - **Behavior:** Dispatches to _forward_2d if seq_len == 1, or _forward_3d if seq_len > 1
  - **Classification:** `PRODUCTION (Dual Path)`
  - **Confidence:** 100%
  - **Contradiction:** 3D path exists in model but live engine only passes 2D snapshots
- **Path:** `src/nexus_scalp/application/live_sequence.py` (Lines: `98-150`)
  - **Symbol:** `SequenceInferenceService`
  - **Behavior:** Maintains 32-bar sliding window buffer for 3D inference
  - **Classification:** `RESEARCH`
  - **Confidence:** 100%
  - **Contradiction:** Not wired into default LiveEngine tick loop
- **Path:** `src/nexus_scalp/model_generation/sequence_training.py` (Lines: `50-115`)
  - **Symbol:** `SequenceCandidateTrainer`
  - **Behavior:** Trains TCN_ATTENTION_V1 candidates on (N, 32, 50) tensors
  - **Classification:** `RESEARCH`
  - **Confidence:** 100%
  - **Contradiction:** None

## Scope
1. Implement benchmark runner scripts/benchmarks/benchmark_2d_vs_3d.py.
2. Train both 2D ScalpNet and 3D ScalpNet on identical 180-day market dataset using identical fold boundaries.
3. Measure: validation fold Sharpe, OOS expectancy (R), and per-tick CPU inference latency (microseconds).
4. Compile comparative report and present to operator for formal architectural decision.

## Non-Goals
Do not delete 3D sequence code before operator reviews benchmark evidence.

## Preconditions
TASK-001 and TASK-003 complete; historical dataset available.

## Dependencies
TASK-001, TASK-003

## Blocks
None

## Source Areas
- `src/nexus_scalp/models/scalp_net.py:1-250`
- `src/nexus_scalp/model_generation/sequence_training.py:1-150`
- `src/nexus_scalp/application/live_sequence.py:1-180`

## Investigation
Measure CPU inference latency of MultiheadAttention with embed_dim=128 on host hardware to assess whether tick processing SLA (< 10ms) is violated.

## Implementation Plan
1. Write scripts/benchmarks/benchmark_2d_vs_3d.py.
2. Run 2D candidate training and record metrics.
3. Run 3D sequence candidate training and record metrics.
4. Run 10,000 forward passes on CPU and compute p50, p95, p99 inference latency.
5. Compile markdown report in docs/benchmarks/2D_VS_3D_BENCHMARK.md.
6. STOP for Human Decision: Operator decides between:
   - Option A: Deprecate 3D sequence modeling, strip Attention from ScalpNet, simplify to pure 2D ResNet.
   - Option B: Retain 3D as research, wire sliding window buffer into LiveEngine.

## Tests
- `python scripts/benchmarks/benchmark_2d_vs_3d.py --dry-run`
- `pytest tests/unit/test_scalp_net.py -v`

## Validation / Benchmark
Empirical evidence collected across >= 5 folds: Fold Sharpe delta, OOS Expectancy delta, Latency delta (p99 latency <= 10ms SLA).

## Evidence Required
- Benchmark report docs/benchmarks/2D_VS_3D_BENCHMARK.md
- Recorded operator decision on 3D sequence lifecycle

## Acceptance Criteria
1. Benchmark successfully runs and generates comparative evidence.
2. Human decision recorded specifying whether to deprecate or promote sequence modeling.

## Failure / Abort Conditions
If 3D sequence model fails to train or crashes with CUDA/CPU tensor shape mismatches, document error and abort benchmark.

## Human Stop Conditions
HUMAN STOP CONDITION: Operator must choose architectural path for 3D sequence modeling.

## Expected Output
Comprehensive benchmark evidence report and recorded operator architectural decision.
