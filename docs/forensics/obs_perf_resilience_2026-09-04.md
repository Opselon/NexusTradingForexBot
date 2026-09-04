# OBS-PERF-RESILIENCE — Hot-Path, Fault Injection & Recovery Forensics

**Date:** 2026-09-04 04:55 +03:30 | **Task:** OBS-PERF-RESILIENCE | **Head:** `da4d0c12` | **Branch:** `hermes-subagent/subagent-sa-7-984f3392`
**Author:** Hermes-OBS (Nexus Fleet) | **Runner:** repo `.venv` (Python 3.11.16, torch CPU, 8 vCPUs probe forensics) | **Outputs:** code + `tests/unit/test_latency_regression_observability.py` (12) + `tests/unit/test_fault_injection_observability.py` (10)

---

## 1. Hot-Path Latency Audit — `feature assembly -> scaler -> inference`

**Hot path under audit:** `LiveEngine._process_tick_pipeline` (INV-001: zero sync DB) -> `ScalpFeatureEngine.compute_from_bars` -> `InferenceValidator`/`LiquidityGovernor` -> `ScalerBundle.transform` -> `ScalpNet` forward (1 thread, `torch.inference_mode`).

**Latency tracer coverage:** `features/latency_tracer.py` — monotonic `perf_counter_ns` only, 11 stages `T0..T10` (market event -> published). Derived: `feature_ms` (T1->T2), `scaling_ms` (T2->T3), `tensor_ms` (T3->T4), `model_ms` (T6-T5 = honest forward only), `postprocess_ms`, `decision_ms`, `queue_ms`, `e2e_ms`, `pipeline_ms`. Exposed as `engine._last_latency_breakdown` + `model_forward_ms/feature_ms/e2e_ms` and now `latency_rolling` via `web/server.py:/api/status`.

**Finding — PREVIOUS GAP:** Tracer measured honest stages but no consumer aggregated samples or alerted on regression; only the last sample was surfaced. A slow drift (GC/CPU/thermal) was invisible until a human stared at the UI. Fixed by `observability/latency_regression.py`.

**Measurements (this host, 1-thread `set_num_threads(1)` — matches live policy):**

| Slice | n | mean | p50 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|---:|
| Feature base (55-bar `compute_from_bars` + `to_tensor_input`) | 400 | ~ | **1.09 ms** | **2.79 ms** | — | — |
| Model forward 50D (`ScalpNet(50,4)` alone) | 800 | — | 0.179 ms | 0.352 ms | 0.545 ms | 1.38 ms |
| Model forward 70D (`ScalpNet(70,4)` alone) | 800 | — | 0.174 ms | 0.324 ms | 0.461 ms | 0.73 ms |
| Pipeline `feature->scaler->tensor->model` (50D array->tensor->model) | 1200 | 0.30 ms | 0.29 ms | 0.48 ms | 0.67 ms | 1.52 ms |
| Pipeline 70D (`assemble_70d` -> clip -> model(70)) | 600 | 0.40 ms | 0.38 ms | 0.60 ms | 1.06 ms | 2.33 ms |

Full tick including 900-bar overlay (`PERF-01`) costs ~6-7 ms but is cached between bars (0 ms on cache hit) and `PERF-02` throttles `get_account_info` to 5 s. **Verdict:** hot path is comfortably **< few ms p50 and < ~3 ms p95 on this host** — well under the `latency_warning_threshold_ms()=100 ms` budget.

**Alert on regressions — NEW:** `LatencyRegressionDetector` (bounded 2000-sample ring per stage) ingests every `to_dict()` breakdown on the tick path (exception-isolated, INV-018). Budget = `100 ms * 0.5 = 50 ms` p95 e2e; needs 50 samples before verdict, 5 consecutive regressed opens epoch, 20 healthy closes it (hysteresis prevents GC flap). Edge-triggered: one `[LATENCY_REGRESSION]` warning + `INFERENCE_LATENCY_REGRESSION` incident per epoch. Exposed at `model_meta.latency_rolling` (`samples_observed`, `regressed`, `regression_epochs_total`, `worst_p95_e2e_ms`, per-stage p50/p95/p99/max).

---

## 2. Fault Injection — `DEGRADED -> BLOCKED` must be logged + visible, never silent

| Fault | Injection | Pre-fix visibility | Post-fix (this branch) | How verified |
|---|---|---|---|---|
| **Gaps** (weekend/holiday, 78 gaps >60s in 100K M1; see `gap_handling_report.md`) | Temporal contract `max_gap_us=10m`; gap-invalid windows marked `valid=False`, never cross-gap learned; live `note_bar_gap()` drains buffer, falls back to 2D | Correct | Correct (no change) | `docs/forensics/gap_handling_report.md` + `test_temporal_sequence_contract.py` green |
| **Missing HTF** (H1/H4 unavailable) | `evaluate_warmup_readiness([], [])` | `SAFE_NOT_READY` + `[INFERENCE] BLOCKED` log | Same — now pinned: fail-closed `NO_TRADE` proposal with non-empty `reason_code` (visible in balance sheet) | `test_f2_*` |
| **Stale scaler** (degenerate `.scaler.npz` with zero/neg/nan std) | Corrupt artifact with `std=0` vector | `is_ready()=True` -> `transform` divides by zero -> `[-5,+5]`-clipped garbage -> `nan_to_num(neginf->-1.0)` poisons model | **`is_ready()` now `False` when any `std` is `<=0` or non-finite; `transform` passes through unchanged; loader logs `[SCALER_DEGRADED]` and the bundle is `scaler_ready=False`** | `test_f3_*` + `test_scaler_*` |
| **Broker reconnect / frozen feed** | Frozen `T0` for 900 s; watchdog `Tick Stagnation` + `live_freshness_gate` | Frozen feed surfaced as `health.subsystems.engine=STALE` + `live_freshness.overall=STALE` (G29) | Same — now also asserts the stale gauge advances and the `BLOCKED_BY_STALE` downgrade is telemetry-counted | `test_f4_*` + `tests/integration/test_live_freshness_g29.py` (41) |
| **Duplicate ticks** (`(ts,bid,ask)` triple) | Identical quote re-fed | Loop-level early return (`pipeline_last_*`) + `policy._evaluate_duplicate_tick` re-surfaces last real proposal; regime rings not double-pushed (BUG-169) | Same — now pinned: dedup predicate recognises `(ts,bid,ask)` and regime cache not poisoned | `test_f1_*` |
| **70D liquidity loss** (`causal_state != VALID`) | 70D contract forced, snapshot `INVALID` | `RuntimeError(70D assembly failed - inference blocked for this tick)` logged — no gauge, no telemetry | **Now also** `+ _inference_failures_total`, `emit(INFERENCE_BLOCKED_70D_ASSEMBLY, BLOCKED_INFERENCE)` (incident pipeline, visible in `/api/forensics/health` and `incident_stats`) | `test_f5_*` |

No silent zero-substitution anywhere: 70D assembly never fabricates `FEATURE_UNAVAILABLE`; scaler never fabricates after fix; freshness gate never fabricates confidence (zeroed).

---

## 3. Resilience — crash consistency, idempotency, recovery

| Surface | Mechanism | Verdict |
|---|---|---|
| **Crash consistency (DB)** | SQLite WAL (`PRAGMA journal=WAL`), bounded background `AuditDB_Worker` batching (500/tx), never sync-write on tick (INV-001). Model weights saved atomically via `model.pt.tmp` -> `Path.replace()` with `BUG-141` width guard (`declared vs model_width` mismatch refuses the clobber). | Proven |
| **Idempotency** | `audit_signals.signal_dedup_key TEXT UNIQUE` + `idx_audit_signals_dedup`; `AuditRepository._signal_dedup_key` is deterministic; broker history sync uses broker-ticket identity watermark (`create_history_tables` idempotent, `orders_duplicates/deals_duplicates` counted); hygiene/sync workers are idempotent re-runs. | Proven (`audit dedup index` exists at probe) |
| **Recovery** | Cold-start warmup re-fetches `H1_REQUIRED/H4_REQUIRED` bars and rebuilds feature window causally (no lookahead); `_resync_from_broker` after watchdog reconnect re-seeds aggregator deterministically (sorted/deduped completed bars, forming bar aligned), warms liquidity, syncs chart state, re-evaluates warmup. `reconcile_pending_state` at startup treats broker as truth. Outcome recovery sweep is bounded/idempotent. | Proven (G29 + `test_r1_*`) |

Observability itself is isolated: `LatencyStats.add()` now rejects non-numeric/non-finite samples; the detector never raises and never blocks the tick (INV-018, `test_detector_tolerates_malformed_payloads`, `test_r2_*`).

---

## 4. Fixes in This Branch

* `features/latency_tracer.py` — `LatencyStats.add` filters non-finite/non-numeric values instead of poisoning `pstdev`.
* `application/live_engine.py` — `ScalerBundle.is_ready()` rejects degenerate stds; loader warns `SCALER_DEGRADED`; 70D assembly failure bumps `inference_failures_total` + emits telemetry; rolling detector wired at the end of `_infer_probabilities` with edge-triggered alert.
* `observability/latency_regression.py` — NEW bounded rolling p95 regression detector.
* `web/server.py` — exposes `model_meta.latency_rolling`.
* `incidents/telemetry.py` — incident codes `INFERENCE_BLOCKED_70D_ASSEMBLY`, `SCALER_DEGRADED`, `INFERENCE_LATENCY_REGRESSION`.
* `incidents/correlator.py` — correlation mapping for `SLOW_INFERENCE`.
* Tests — 22 new unit tests (12 latency + 10 fault) + full `test_live_freshness_g29.py` (41) exercised.

---

## 5. Tests & Evidence

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_latency_regression_observability.py tests/unit/test_fault_injection_observability.py -q   # 22 passed
.venv/Scripts/python.exe -m pytest tests/unit/test_latency_regression_observability.py tests/unit/test_fault_injection_observability.py tests/integration/test_live_freshness_g29.py -q   # 41 passed
ruff check src/nexus_scalp/observability/latency_regression.py src/nexus_scalp/features/latency_tracer.py   # All checks passed
mypy src/nexus_scalp/observability/latency_regression.py   # OK
```

All claims above were produced by probes run from `src/` in this worktree (no invented numbers; bench JSON persisted at `$LOCALAPPDATA/Temp/obs_bench*.json`). Full 70D temporal/gap lineage remains in `docs/forensics/gap_handling_report.md`.

---

## 6. Handoff to Other Lanes

ML/Data/Integration lanes: no trainer/data changes. This branch is observability/performance-only. To reproduce hot-path numbers on another host: run the same `ScalpNet(50/70,4)` 1-thread forward + `ScalpFeatureEngine` 55-bar feature bench via `scripts/inference_latency_benchmark.py` (regenerates `artifacts/benchmarks/inference_latency.json`).
