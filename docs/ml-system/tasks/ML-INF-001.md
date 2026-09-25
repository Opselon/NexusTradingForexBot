# ML-INF-001 — Inference Preprocessing & Scaler Latency SLA (< 10ms) Verification

STREAM: STREAM H — INFERENCE
PRIORITY: P1
STATUS: DONE (2026-09-19; verified p99 = 1.8561 ms over 50,000 ticks on CPU — PR #312)
DEPENDENCIES: ML-FEAT-001 (DONE)
AGENT_ROLE: AGENT-INFERENCE
OWNERSHIP_SCOPE: src/nexus_scalp/application/live/inference.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Profile and verify that the complete live inference path (feature calculation, shape validation, scaler transform, model forward pass, masked softmax) executes within the strict < 10ms tick latency SLA.

## WHY_IT_EXISTS
In live scalping on M1 XAUUSD, incoming market ticks arrive every few milliseconds during high-volatility events. If inference latency exceeds 10ms, tick queue buildup occurs, causing slippage and stale signal generation.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/application/live/inference.py` (Lines: `52-190`)
  - **Symbol:** `InferenceService.infer`
  - **Behavior:** Validates vector, transforms with scaler, passes to model, applies masked softmax
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** No automated benchmark asserting p99 latency < 10ms in CI

## FACTS
- InferenceService runs on the live tick hot path.
- 2D forward pass uses ScalpNet.

## UNKNOWNs
- p99 latency distribution under CPU thermal throttling on live VPS host.

## SCOPE
Create tests/performance/test_inference_latency_sla.py; measure p50, p95, p99 latency across 50,000 tick evaluations; assert p99 < 10ms on CPU.

## NON_GOALS
Do not rewrite PyTorch in C++ unless latency SLA is violated.

## SOURCE_AREAS
- `src/nexus_scalp/application/live/inference.py`
- `src/nexus_scalp/models/scalp_net.py`

## FILES_LIKELY_TO_CHANGE
- `tests/performance/test_inference_latency_sla.py`

## INVESTIGATION_PLAN
Inspect whether tensor memory allocations (torch.from_numpy) occur inside the tick loop or can be pre-allocated.

## IMPLEMENTATION_PLAN
1. Create tests/performance/test_inference_latency_sla.py.
2. Initialize InferenceService with production ScalpNet and scaler.
3. Warm up model with 1,000 forward passes.
4. Time 50,000 sequential single-tick inference calls using time.perf_counter_ns().
5. Compute p50, p95, and p99 latencies.
6. Assert p99 < 10,000 microseconds (10ms).

## TEST_PLAN
- `pytest tests/performance/test_inference_latency_sla.py -v -s`

## BENCHMARK_PLAN
50,000 tick inference benchmark; output latency percentile distribution table.

## EVIDENCE_REQUIRED
- Latency benchmark report output
- Pytest assertion log confirming p99 < 10ms

## ACCEPTANCE_CRITERIA
1. tests/performance/test_inference_latency_sla.py passes. [x] VERIFIED — 4/4 tests green (pytest -v -s, slim Linux venv).
2. p99 inference latency strictly < 10.0 milliseconds on host CPU. [x] VERIFIED — p99 = 1.8561 ms over 50,000 ticks (SLA headroom 5.4x); p50 = 1.2549 ms, p95 = 1.4014 ms, throughput 778.9 inf/sec.

## VERIFICATION_EVIDENCE (2026-09-19, AGENT-INFERENCE, slim venv .venv-linux)
- Command: `PYTHONPATH=src:. .venv-linux/bin/python -m pytest tests/performance/test_inference_latency_sla.py -v -s`
  - Result: 4 passed in 65.57s (full 50k benchmark + 70D path + fail-closed contract checks).
- 50,000-tick benchmark report (production 50D ScalpNet, 3-class, torch 2.14.0+cpu, 1000-pass warmup):
  - Min 0.6559 ms | p50 1.2549 ms | p90 1.3250 ms | p95 1.4014 ms | p99 1.8561 ms | Max 11.6767 ms
  - Mean 1.2835 ms (+/- 0.2321 ms); wall 64.191 s; throughput 778.9 inf/sec; SLA verdict PASSED.
- 70D path (5,000 ticks, scalp_v3): p99 < 10 ms, p50 < 5 ms — PASSED.
- Regression guards: tests/unit/test_runtime_failure_injection.py + test_temporal_sequence_contract.py — 27 passed (no behavior change from the hot-path edits).
- Lint/format: ruff check + ruff format --check clean on both touched files; mypy: Success (no issues).
- Manifest: tests/critical_suite.txt 210 paths, verify_critical_suite_manifest.py CRITICAL_SUITE_MANIFEST_OK.

## OPTIMIZATIONS_APPLIED (within OWNERSHIP_SCOPE, behavior-preserving)
1. `torch.tensor(x_np, dtype=torch.float32)` -> `torch.from_numpy(x_np)` (zero-copy view; x_np is already float32 C-contiguous from `np.array(..., dtype=np.float32)`).
2. `bundle.model.eval()` guarded by `if bundle.model.training` — avoids redundant state mutation on every tick (module already in eval mode after load).
3. `torch.set_num_threads(1)` / restore made conditional (`if _prior_threads != 1`) — the two host side-effects were unconditional per tick even when the value was already correct; removes redundant atomics on the hot path.

## ABORT_CONDITIONS
If p99 latency exceeds 10ms, profile bottleneck (feature math vs torch forward) and report.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `tests/performance/test_inference_latency_sla.py`

## SHARED_FILE_RISK
Low. AGENT-INFERENCE owns performance test.
