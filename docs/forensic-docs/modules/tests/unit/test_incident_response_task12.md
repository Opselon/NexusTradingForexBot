# tests/unit/test_incident_response_task12.py

- GUARDS: TASK-12 incident response & forensic diagnostics (TEST-INCIDENT-01..35): incident model, deduplication, root-cause confidence, timeline reconstruction, correlation-ID propagation, value lineage, MT5/ledger divergence trace, clock skew, split-fill, learning-loss, model/feature/news traces, worker stall, governance, migration, version mismatch, why-traces, quarantine, recovery plans/approval, export masking, telegram throttling, regression detection, bug linkage.
- KEY ASSERTIONS:
  - 55 repeats collapse into ONE incident; distinct fingerprints stay separate; windowed merges; proven status requires evidence; quarantine NEVER deletes (marks suspect); recovery states enforced; export zip bundles artifacts with NO secrets; value-level secret masking (high-entropy catchall); incident package has NO execution imports and NO self-modification code (132 asserts).
- PITFALLS IT ENCODES: evidence-first discipline (unproven hypothesis ≠ bug; resolved requires evidence + regression test); incidents must stay read-only toward trading (no trading API in incident routes).
- NOTES: 35 requirement-mapped classes, 1360 lines; pairs with TASK-13 runtime activation suite.
