# tests/unit/test_70d_contract_parity_task3.py

- GUARDS: TASK-03-70D-PARITY — canonical 70D schema contract (TEST-70D-PARITY-*): single-source-of-truth schema with Base + News + Liquidity dimensions == 70, schema ID validation (scalp_v3 == 70D in registry).
- KEY ASSERTIONS:
  - 70-dimension count; schema ID registered and validated; feature names/order deterministic; vectors rejected when dimension or schema mismatched (27 asserts).
- PITFALLS IT ENCODES: dimension/schema mismatch must be BLOCKED at the boundary (inference, model load), not silently padded.
- NOTES: Foundation the other task3 parity suites build on; registry is the normative source (scalp_v3 identifier).
