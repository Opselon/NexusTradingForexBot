# tests/unit/test_domain_models.py

- GUARDS: Domain models & value-object invariants — validation embedded in the value objects themselves.
- KEY ASSERTIONS:
  - `test_tick_data_valid_instantiation` / `test_tick_data_invalid_spread_raises_validation_error`: TickData validates spread at construction; `test_trade_proposal_buy_invariants` / `test_trade_proposal_execution_id_default_none` (5 asserts).
- PITFALLS IT ENCODES: invalid domain state must be impossible to construct (fail-fast value objects), not caught later.
- NOTES: Smallest unit suite (87 lines); imports nexus_scalp.domain.models + enums only — the purity anchor of the domain layer.
