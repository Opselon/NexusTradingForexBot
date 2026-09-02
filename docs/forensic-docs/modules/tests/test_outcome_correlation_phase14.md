# tests/unit/test_outcome_correlation_phase14.py + test_incident_accounting_timebase_task13.py + test_latency_forensics_task.py

# test_outcome_correlation_phase14.py
- **GUARDS:** PHASE 14 outcome correlation — broker reconstruction &
  break-even learning bridges.
- **KEY ASSERTIONS:** broker deal reconstruction feeds outcomes correctly;
  break-even outcome classification matches the canonical taxonomy;
  correlation joins through the OUTCOME table (audit_experiences.
  execution_id empty by design — BUG-008/021 discipline).

# test_incident_accounting_timebase_task13.py
- **GUARDS:** incident accounting + timebase (broker-vs-host time
  awareness) — incidents attributed to the correct timebase.
- **KEY ASSERTIONS:** incident timestamps use the broker/UTC timebase
  (never host wall clock); accounting rollups inside incident windows are
  correct; the timebase conversion helpers are pinned.

# test_latency_forensics_task.py
- **GUARDS:** LatencyTracer + latency forensics (features/latency_tracer).
- **KEY ASSERTIONS:** staged timing pipeline (T0..T10) marks/elapsed
  correctness; missing stages → None (never 0); percentiles (p50/p90/
  p99) math; the latency telemetry dict shape.
- **PITFALLS IT ENCODES:** a 0.0 would fake a zero-latency measurement —
  None is the honest answer.