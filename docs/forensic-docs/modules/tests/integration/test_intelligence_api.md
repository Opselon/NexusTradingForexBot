# tests/integration/test_intelligence_api.py

- GUARDS: PHASE 09 Trade Intelligence Brain — REST API, worker and LiveEngine wiring for the adaptive strategy-evolution/position-lifecycle subsystem; endpoints must expose REAL persisted intelligence state.
- KEY ASSERTIONS:
  - `TestIntelligenceAPI`: strategy health/gate endpoints, position lifecycle observations, autopsy/causality endpoints return persisted truth; worker cycle refreshes REST state; failures isolated (24 asserts).
- PITFALLS IT ENCODES: API responses must be live pulls from the DB via the worker cache, never synthesized; worker restart/checkpoint tested.
- NOTES: Companion to unit test_intelligence_phase09.py (same subsystem at the HTTP layer).
