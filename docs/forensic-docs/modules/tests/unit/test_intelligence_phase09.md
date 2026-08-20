# tests/unit/test_intelligence_phase09.py

- GUARDS: PHASE 09 Trade Intelligence Brain — behavioral suite: adaptive strategy evolution + position lifecycle intelligence; every test asserts OBSERVABLE behaviour (persisted records, gate outcomes).
- KEY ASSERTIONS:
  - full position timeline observed; timeline immutable + deduped; MFE/MAE normalization; profit giveback detected; quality decomposition (bad management ≠ bad strategy); strategy degrades and recovers; RETIRED strategy blocks BEFORE dispatch; worker failure isolated + checkpoint persists across restart; intelligence holds NO execution capability; lifecycle works without MT5 (40 asserts).
- PITFALLS IT ENCODES: intelligence is an advisory gate — it must be incapable of execution; worker checkpointing is required for restart safety.
- NOTES: Companion of integration test_intelligence_api.py; no-MT5 test proves the subsystem runs in paper/offline mode.
