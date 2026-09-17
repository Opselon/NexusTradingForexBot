# TASK-009 — 2D Snapshot vs 3D Sequence Routing Verification & Benchmark

## Priority
P2

## Status
PENDING

## Objective
Benchmark 2D MLP ResNet against 3D Causal TCN + Attention on identical market data, quantifying latency and predictive edge.

## Why This Task Exists
3D sequence modeling adds substantial computational latency. We must prove whether temporal attention adds genuine edge over 2D snapshots.

## Current Evidence
src/nexus_scalp/models/scalp_net.py:194-209; src/nexus_scalp/application/live_sequence.py:98.

## Scope
Execute controlled benchmark comparing 2D vs 3D ScalpNet across fold Sharpe, expectancy, and per-tick inference latency.

## Explicit Non-Goals
Do not activate 3D sequence in live trading before benchmark proves superiority.

## Dependencies
TASK-001, TASK-003

## Source Areas
src/nexus_scalp/model_generation/benchmark.py, src/nexus_scalp/models/scalp_net.py

## Files Likely Involved
src/nexus_scalp/model_generation/benchmark.py, src/nexus_scalp/models/scalp_net.py

## Investigation Required
Measure microsecond inference latency overhead of MultiheadAttention on CPU.

## Implementation Outline
Run ModelBenchmarkSuite for SCALPNET_2D vs SCALPNET_3D_SEQ32.

## Tests Required
tests/unit/test_benchmark_2d_vs_3d.py

## Benchmark Requirements
Run critical suite to confirm zero regressions.

## Evidence Required
Exact execution trace and test output proving acceptance criteria are met.

## Acceptance Criteria
Comparative report published documenting exact Sharpe, expectancy, and latency delta.

## Human Stop Conditions
HUMAN DECISION REQUIRED: Decide whether to deprecate 3D sequence modeling or invest in full live sequence buffer wiring.

## Expected Output
Tested, verified PR with passing tests and updated task status.
