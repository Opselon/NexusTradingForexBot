# tests/unit/test_critical_suite.py

- GUARDS: CRITICAL TEST SUITE — whole-application heartbeat + suite classification: the small, fast, high-signal regression net that must stay green as the release gate (15 asserts).
- KEY ASSERTIONS:
  - core import graph loads; minimal end-to-end heartbeat (config → engine → policy → risk → execution stubs); suite marker/classification machinery identifies the critical set.
- PITFALLS IT ENCODES: critical tests are kept few and fast by design — anything slow or flaky must live elsewhere; this file IS the gate definition.
- NOTES: Marker module; a failing critical test blocks release preflight (see test_forensic_monitoring_task11 TestMonitor32 deploy gate).
