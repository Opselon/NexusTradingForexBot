# 2026-09-19 AGENT-INFERENCE ML-INF-001 — Inference Preprocessing & Scaler Latency SLA (<10ms)

**Task:** ML-INF-001 (Stream H — Real-Time Inference), P1
**Role:** AGENT-INFERENCE
**Status:** DONE — PR opened (`agent/inference/ml-inf-001`)
**Worktree:** `/tmp/wt-inference-ml-inf-001` off `origin/main` @ `117ce7f1`
**Lock:** `src/nexus_scalp/application/live/inference.py` registered in `agents/locks.yaml` (released at closeout)

## Objective
Profile and verify the complete live inference path (feature assembly, shape validation,
scaler transform, model forward pass, masked softmax) against the strict p99 < 10ms tick
latency SLA, across 50,000 tick evaluations on CPU.

## Result — SLA PASSED with 5.4x headroom

Full 50,000-tick benchmark, production 50D ScalpNet (267k params, 3-class contract),
torch 2.14.0+cpu, 1,000-pass warmup, `time.perf_counter_ns()` timing:

| Metric | Value |
|---|---|
| Min | 0.6559 ms |
| p50 (median) | 1.2549 ms |
| p90 | 1.3250 ms |
| p95 | 1.4014 ms |
| **p99 (SLA gate)** | **1.8561 ms** |
| Max | 11.6767 ms |
| Mean | 1.2835 ms (+/- 0.2321 ms) |
| Wall time | 64.191 s |
| Throughput | 778.9 inferences/sec |

**SLA verdict: PASSED** — p99 = 1.8561 ms < 10.0 ms; the SLA-critical percentiles
(p50/p90/p95/p99) sit in a 1.25–1.86 ms band. The single 11.68 ms max outlier is a
GC/scheduler jitter spike on a 2-vCPU host, far above p99 and outside the SLA criterion.

Secondary gate: 70D `scalp_v3` path (Base 0..49 + News 50..59 + Liquidity 60..69),
5,000 ticks — p99 < 10 ms and p50 < 5 ms, PASSED.

## Hot-path hardening (behavior-preserving, inside OWNERSHIP_SCOPE)
All edits confined to `src/nexus_scalp/application/live/inference.py`:

1. **Zero-copy tensor build** (`inference.py:271`): `torch.tensor(x_np, dtype=torch.float32)`
   → `torch.from_numpy(x_np)`. `x_np` is already float32 C-contiguous
   (`np.array(x_vec, dtype=np.float32).reshape(1, -1)` then scaler transform), so
   `from_numpy` shares memory instead of copying. Saves a per-tick 50-float copy and the
   associated dtype re-validation.
2. **Conditional eval()** (`inference.py:289`): `bundle.model.eval()` guarded by
   `if bundle.model.training`. The module is already in eval mode after bundle load; the
   unconditional call was a redundant per-tick state mutation.
3. **Conditional thread pinning** (`inference.py:295` / `:316`): the single-thread pin
   (`torch.set_num_threads(1)`) and its restore were made conditional on
   `_prior_threads != 1`. Removes two redundant per-tick host atomics when the process is
   already pinned (the common case on the live host), with identical numerics.

None of these change the output contract: identical logits path, masked softmax
unchanged, `torch.inference_mode()` guard unchanged, and the WAIT-index masking contract
(`model_lifecycle.model_class_contract.masked_softmax`) is untouched.

## Verification evidence (all commands run in the isolated worktree)
- `PYTHONPATH=src:. .venv-linux/bin/python -m pytest tests/performance/test_inference_latency_sla.py -v -s`
  → **4 passed in 65.57s** (50k benchmark + 70D path + corrupt-scaler fail-closed +
  uninitialized-bundle). Benchmark report table printed live (see above).
- Regression guards for the shared contract surface:
  `PYTHONPATH=src:. .venv-linux/bin/python -m pytest tests/unit/test_runtime_failure_injection.py tests/unit/test_temporal_sequence_contract.py`
  → **27 passed** (fail-closed scaler corruption, FI-4c/FI-5, sequence tensor contract).
- `ruff check` on touched files → clean (one F541 fixed).
- `ruff format --check` on touched files → clean (one long-signature reformat).
- `mypy src/nexus_scalp/application/live/inference.py` → **Success: no issues found**.
- `scripts/ci/verify_critical_suite_manifest.py` →
  `CRITICAL_SUITE_MANIFEST_OK: 210 paths all exist`.
- Repo-wide `ruff format --check` clean (2165 files) at the parent commit — no new
  malformed files introduced.

## Artifacts
- `tests/performance/test_inference_latency_sla.py` (NEW, 4 tests, registered in
  `tests/critical_suite.txt` under `# ML-INF-001`). Benchmark tick count overridable via
  `NSE_INFERENCE_BENCHMARK_TICKS` for CI time budgets; the default is the spec's 50,000.
- `docs/ml-system/tasks/ML-INF-001.md` — STATUS → DONE, acceptance criteria `[x]` with
  measured evidence, full benchmark table recorded.
- `docs/ml-system/TASK_BOARD.md` / `docs/ml-system/06_TASK_LEDGER.md` → DONE.
- `agents/taskboard.md` — closeout row appended.

## Contract notes for the next agent
- `inference.py` is an **unbound-delegation** module: methods are invoked as
  `InferenceService.method(engine, ...)` where `self` IS the LiveEngine. Tests construct a
  `SimpleNamespace` engine double matching the real attribute surface
  (`_bundle`, `_bundle_lock`, `_build_live_feature_vector`, `_validate_50d_tensor`,
  `_maybe_build_live_sequence_tensor`, `effective_feature_dim/schema_id`, telemetry sink).
- The 70D test uses a straight 70-length vector through `scalp_v3`; the REAL 70D live path
  additionally requires a VALID causal liquidity snapshot (`liquidity_governor`) and
  assembles via `build_70d_vector` — the latency measured here is the inference-stage
  budget, not the full assembly I/O cost.
- Latency is host-dependent: this run is a 2-vCPU CI-class host. The max-tail outlier
  shape will differ on the live VPS; if p99 ever exceeds 10ms there, profile per the task's
  ABORT_CONDITIONS (feature math vs torch forward) before assuming a regression.

## Next candidate
`ML-PLAT-002` (P1 | AGENT-PLATFORM: Signed Official Model Bundle Verification & Staging),
then `ML-VAL-001` (P1 | AGENT-ML-VALIDATION: Purged Walk-Forward Monotonicity & Embargo —
unblocked by ML-DATA-002 DONE), then `ML-BT-001`/`ML-TRAIN-001`.
