# tests/unit/test_live_state_contract.py

- GUARDS: PHASE 14 FORENSIC HARDENING — canonical live-state contract: the no-synthetic-values invariant — /api/status and /api/live/state NEVER render fake market data when the engine has no live state (a previous revision served bid=2334.0 made up).
- KEY ASSERTIONS:
  - empty engine → no fabricated price/equity; every payload field honestly absent or explicitly labelled unavailable; state version monotonic; no exception text leaking into API payloads (66 asserts).
- PITFALLS IT ENCODES: fabrication of market data is the cardinal sin this suite guards; absent state is displayed as absent, never as zero or a placeholder price.
- NOTES: 13 fake classes (bar/aggregator/tick/fv/proposal/adapter/audit/engine/tensor/np) to model every empty state.
