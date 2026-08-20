# tests/unit/test_incident_runtime_task13.py

- GUARDS: TASK-13 incident runtime activation (TEST-INCIDENT-RUNTIME-01..20): IncidentWorker state machine + lifecycle, off-tick-path guarantee, structured telemetry ingestion + correlation, causal chains, accounting first-divergence, zero-outcome recovery, reconstruction idempotency, split-fill accounting, timebase probe, telegram runtime, export, UI worker state.
- KEY ASSERTIONS:
  - state machine transitions + FAILED state after persistent failures; worker has NO sync DB call on the tick path (`to_thread` used); collector emit/flush + unknown events lenient; runtime 55-repeats → one incident; causal chain reconstructed; first-incorrect-stage is the ledger; candidate reconstruction deterministic (57 asserts).
- PITFALLS IT ENCODES: the incident worker must NEVER touch the tick path (off-tick-path is structural, not stylistic); timebase probes must be UTC-aware.
- NOTES: _BrokenStore / _FakeNotifier harness; pairs with the TASK-12 unit suite.
