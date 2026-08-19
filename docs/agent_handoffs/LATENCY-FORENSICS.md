# Handoff — Inference Latency Forensics + Low-Latency Inference Fix

> Agent: Hermes-Latency · 2026-08-19 · TASK: Prediction Latency Forensics
> Status: **COMPLETE** — measurement + root-cause fix + honest UI + tests + benchmark
> Starting HEAD: `a190602` · Ending HEAD: `ee17d37` (all pushed, remote verified)

---

## 1. WHAT THE 5.40ms WAS (PROVEN)

The UI's single "Latency" number = whole `_infer_probabilities` body
(validate + to_tensor + numpy copy + scaler + tensor + nan_to_num + debug
`.detach().cpu().numpy().tolist()` EVERY inference + `model.eval()` +
forward). Correct clock (perf_counter), wrong label: it was never
"model forward".

## 2. ROOT CAUSE + FIX (PROVEN)

**Bottleneck = PyTorch intra-op thread contention on a 267k-param net.**
Default `get_num_threads()`=4 thrashes small matmuls (~50-120ms forward under
host load). Fix in `_infer_probabilities`:

```python
_prior_threads = torch.get_num_threads()
torch.set_num_threads(1)
try:
    with torch.inference_mode():
        probs = bundle.model(x)
finally:
    torch.set_num_threads(_prior_threads)
```

+ debug input capture sampled every 64th inference.
Output equivalence: maxdiff 0.0 (byte-identical logits).

## 3. MEASURED (1500 warm samples, real champion state_dict)

| | BEFORE | AFTER |
| :--- | ---: | ---: |
| model p50 | 48.8 ms | 0.30 ms |
| model p95 | 96.4 ms | 0.65 ms |
| model p99 | 143.0 ms | 4.52 ms |
| e2e p50 | 49.0 ms | 0.33 ms |

## 4. FILES (mine)

- `src/nexus_scalp/features/latency_tracer.py` (NEW) — staged T0..T10 tracer
- `tests/unit/test_latency_forensics_task.py` (NEW) — TEST-LATENCY-01..22
- `scripts/inference_latency_benchmark.py` (NEW) — 1500-sample benchmark
- `src/nexus_scalp/application/live_engine.py` — staged timing + thread pin
- `src/nexus_scalp/web/server.py` — `latency_breakdown` in model_meta + debug
  endpoint staged timing
- `Web/app.js` + `Web/index.html` — honest latency stage panel
- `docs/INFERENCE_LATENCY_FORENSIC_FINAL.md` — final report
- `docs/agent_handoffs/LATENCY-FORENSICS.md` — this handoff

## 5. EXACT NEXT-AGENT INSTRUCTIONS

1. **Verify**: `git rev-parse HEAD origin/main` (must match), `git status`.
2. **Real-live sample**: run the engine in PAPER mode (or READ_ONLY MT5) and
   capture ≥100 real inference events; confirm the benchmark p50/p95/p99 is
   representative (`latency_breakdown` in `/api/status` model_meta).
3. **GPU path** (when a CUDA host is available): benchmark with
   `cuda_available: true`; add CUDA-event sampled timing if the forward is
   materially async (do NOT synchronize every live tick).
4. **Long-run stability**: watch `torch.set_num_threads(1)` interaction with
   other torch callers in the same process (restored in `finally`; verified
   on shared suites). If a second caller needs >1 thread concurrently, move
   the pin to a dedicated inference executor.
5. **Threshold/alerting** (brief 39): wire `latency_warning_threshold_ms`
   (default 100.0) into runtime diagnostics + aggregated Telegram telemetry
   (never per-sample). Add `[INFERENCE_LATENCY]` structured event with
   prediction_id/correlation_id when the trace is surfaced.
6. **P50/P95/P99 UI panel** (brief 35/37): the backend exposes
   `latency_breakdown`; a rolling percentile summary (`LatencyStats`) can be
   exposed via the debug snapshot endpoint — add if the UI needs it.
7. **Pre-existing parallel bug (NOT mine)**: `server.py:6368` undefined
   `state_version` in SSE serialization diagnostic (F821) — belongs to the
   SSE/forensics owner; report, do not silently fix.

## 6. KNOWN RISKS

- Thread pin is process-global for the duration of the forward; restored
  every call. Tested on shared suites (live_state_contract green).
- Benchmark host was under heavy parallel-agent load — absolute ms vary by
  host; the RELATIVE before/after (threads=4 vs 1) is the robust finding.
- No broker session used; real-live sample is the next agent's first step.
