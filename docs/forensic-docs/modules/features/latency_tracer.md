# src/nexus_scalp/features/latency_tracer.py

- **PURPOSE:** Per-inference latency instrumentation — `LatencyTracer`
  stamps nanosecond markers across the pipeline stages (feature → scaling →
  tensor → model → postprocess → decision → e2e) and `LatencyStats`
  aggregates samples into percentiles for the latency observability surface.
- **ARCHITECTURE LAYER:** Observability (features-adjacent; zero business
  logic — measurement only).
- **RESPONSIBILITY:** (a) `LatencyStage` enum of the annotated stages;
  (b) `mark/stamp` — record stage entry/exit with ns timestamps (optional
  external ns for testability); (c) per-pair `elapsed_ns`/`ms` and the
  named accessors (feature_ms, scaling_ms, tensor_ms, model_ms,
  postprocess_ms, decision_ms, e2e_ms, pipeline_ms, queue_ms); (d)
  `percentiles_ms` (p50/p90/p99 etc.) and `LatencyStats.add/summary` for
  rolling aggregates.
- **DEPENDENCIES:** stdlib time; structlog for optional logging.
- **CONNECTS TO:** LiveEngine inference path (marks around the model call),
  `post70d-forensic-monitoring` dashboards, tests (test_latency_forensics_task,
  test_70d_perf_task3).
- **KEY CONCEPTS:** Cheap by construction (monotonic clock + dict of ns
  stamps — no allocations on the path except the dict itself); missing stages
  return None (not 0 — 0 would fake a zero-latency measurement); the named
  accessors make the Debug Console readable ("model_ms": 1.2) instead of
  raw ns tuples.
- **EDGE CASES & PITFALLS:** `LatencyTracer` must tolerate out-of-order marks
  (a stage skipped by an early-exit path) — elapsed returns None when either
  endpoint is missing; percentiles need ≥1 sample (empty → None, never 0.0,
  per the no-synthetic-numbers invariant).