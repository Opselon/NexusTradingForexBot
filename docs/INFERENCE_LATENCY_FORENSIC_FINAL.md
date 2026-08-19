# INFERENCE LATENCY FORENSIC FINAL — honest staged measurement + root-cause fix

> Agent: Hermes-Latency · 2026-08-19 · Prediction Latency Forensics + Low-Latency Inference Fix
> Starting HEAD: `a190602` · Ending HEAD: `ee17d37` (all pushed, remote verified)
> Companion: `docs/agent_handoffs/LATENCY-FORENSICS.md`

---

## CURRENT MEASUREMENT (what 5.40ms actually represented)

**PROVEN** — the UI's `Latency: 5.40ms` was NOT model-forward-only. It was the
entire `_infer_probabilities` body:

```text
validate_50d_tensor + to_tensor_input()        (feature conversion)
np.array(x50).reshape(1,-1)                    (numpy copy)
bundle lock acquire
scaler.transform_50d(x_np)                     (scaler)
torch.tensor(x_np)                             (tensor creation)
nan_to_num                                     (sanitize)
x.detach().cpu().numpy().reshape(-1).tolist()  (debug copy — EVERY inference)
model.eval()                                   (redundant per-call)
model(x)                                       (the actual forward)
```

Timer: `time.perf_counter()` (monotonic — correct clock), scope = whole
function. The number was honest for that scope but the scope was mislabeled
as "Prediction Latency" when it meant "feature+preprocess+model+debug copy".

## LATENCY BREAKDOWN (T0..T10 — staged, measured)

| Stage | Meaning | After fix (champion 50D, p50) |
| :--- | :--- | ---: |
| feature_ms | T2-T1 (70D feature vector) | ~0.001 ms (vector already materialized in live path) |
| scaling_ms | T3-T2 | ~0.04 ms |
| tensor_ms | T4-T3 | ~0.02 ms |
| model_ms | T6-T5 (HONEST Model Forward) | **0.30 ms** |
| postprocess_ms | T8-T6 | ~0.01 ms |
| decision_ms | T10-T8 | ~0 ms (policy outside this path) |
| queue_ms | T0-T1 | measured when applicable |
| e2e_ms | T10-T0 | 0.33 ms |

## BOTTLENECK (first proven)

**PyTorch intra-op thread contention on a tiny model.** ScalpNet
(267,492 params) with `torch.get_num_threads()` = 4 (default) spends ~50-120 ms
per forward on this host under parallel load — the intra-op thread pool
thrashes on small matmuls. Single-threaded forward: 0.25-0.30 ms. **The model
math is NOT the bottleneck; thread scheduling is.**

## 70D IMPACT

- Base 50D (champion, live): model forward p50 **48.8 ms → 0.30 ms** after fix
- Candidate 70D: model forward p50 **29.5 ms → 0.30 ms** after fix
- 70D feature build + liquidity: measured separately (feature stage ~µs in
  live path when vector precomputed; liquidity engine ~15-25 ms full rebuild
  on 200 bars — NOT on the per-tick model path)
- 70D adds ~20 extra matmul inputs (50→70) — negligible for a 267k-param net

## MODEL

- CPU inference (no CUDA on this host; benchmark reports `cuda_available: false`)
- Architecture: ScalpNet MLP (267,492 params), 4-logit head
- Champion artifact: `artifacts/models/scalp/XAUUSD/v1.0.0/model.pt` (50D,
  scalp_v1) — measured via the real state_dict
- Device: CPU, dtype float32 end-to-end (no dtype churn)
- Model + scaler loaded ONCE at startup (pre-flight bundle); NOT in hot path

## OPTIMIZATION (exact root-cause fix)

`_infer_probabilities` (live_engine.py):
1. **Pin intra-op threads to 1 for the forward** (`torch.set_num_threads(1)`
   around `model(x)`, restored in `finally` under the bundle lock) — the
   proven bottleneck. Logits byte-identical (maxdiff 0.0).
2. **Debug input capture is now sampled** (every 64th inference) instead of
   `.detach().cpu().numpy().tolist()` on EVERY inference.
3. Removed nothing else: scaler, tensor, dtype, model semantics unchanged.

## BEFORE / AFTER (1500 warm samples, same host, same model)

| Metric | Champion 50D BEFORE | Champion 50D AFTER | Δ |
| :--- | ---: | ---: | ---: |
| model p50 | 48.8 ms | 0.30 ms | -99.4% |
| model p95 | 96.4 ms | 0.65 ms | -99.3% |
| model p99 | 143.0 ms | 4.52 ms | -96.8% |
| e2e p50 | 49.0 ms | 0.33 ms | -99.3% |

Output equivalence: **maxdiff 0.0** (byte-identical logits, threads=1 vs 4).

## UI (exact displayed metrics)

Model panel now shows: Model Forward / Feature Build / Preprocess /
Inference Total / Decision / End-to-End / Queue — each labelled, from
`latency_breakdown` (backend). `model-inference-time` displays Model Forward
only. Legacy single-timer fallback is explicitly labelled "(legacy
single-timer)". Weights status: truthfulness contract (LIVE/ACTIVE/CANDIDATE/
INVALID/INCOMPATIBLE) preserved via model registry provenance.

## TESTS

- `tests/unit/test_latency_forensics_task.py`: **TEST-LATENCY-01..22**, 26
  tests (1 CUDA skip on this host) — monotonic clock, stage isolation,
  percentiles math, no-load/no-DB/no-network structural proofs, output
  equivalence, stale detection, UI-field parity, deterministic benchmark.
- Full affected-suite gate (live_state_contract + latency): green.

## RUNTIME (real sample evidence)

- Benchmark: `scripts/inference_latency_benchmark.py` → 1500 warm inferences,
  real champion state_dict + 70D candidate, staged percentiles.
- Live-style probe: `[FEATURE_CONTRACT]` traces + latency tracer stages in
  the perf probe (synthetic live-style state — no broker connection used;
  PAPER-mode limitation documented). 100+ real inference events: **NOT
  PROVEN** (no broker session in this run; benchmark is representative of
  the deployed artifact on this host).

## BUGS

Only proven defects:
- **No new product bug** — the 5.40ms was a *labeling* defect (scope
  mislabeled), now honest. The thread-contention slowdown is environmental
  (host load) + architectural (default intra-op threads); fixed with the
  thread pin.
- Pre-existing (parallel-agent, NOT mine, reported per contract):
  `server.py:6368` `state_version` undefined in an SSE serialization
  diagnostic (F821) — belongs to the SSE/forensics owner.

## COMMITS

| Commit | Content |
| :--- | :--- |
| `e459b30` | STEP-01 latency_tracer + TEST-LATENCY-01..22 + benchmark script |
| (absorbed into parallel commits `8635c66`/`ee25378`) | STEP-02/03 live_engine instrumentation + thread-pin + server latency_breakdown API |
| `ee17d37` | STEP-06 UI latency breakdown panel |

## REMAINING RISKS

- **PROVEN**: staged honest latency; root-cause thread-pin fix; output
  equivalence (maxdiff 0.0); percentiles benchmark; no-load/no-DB/no-network
  in hot path; UI breakdown labels.
- **NOT PROVEN**: real-broker live latency sample (no MT5 session in this
  run; PAPER limitation); GPU timing path (no CUDA host).
- **UNKNOWN**: long-run stability of the thread-pin under sustained load;
  whether `torch.set_num_threads(1)` interacts with other torch callers in
  the same process (restored in finally; tested on the shared suites).
