# tests/unit/test_schema_70d_reconciliation.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- TEST-SCHEMA-70D-01..08 — TASK-11 canonical 70D schema reconciliation guards: ONE canonical 70D contract (`scalp_v3`, dimension 70, deterministic hash 235b8fccc96b7e0e) used by registry, dataset builder, runtime, shadow, governance and serializers.
- Guards: (01) exact canonical schema; (02) feature order stable; (03) dimension EXACTLY 70; (04) hash deterministic; (05/06) runtime AND shadow schemas match canonical; (07) governance accepts ONLY canonical; (08) legacy schema BLOCKED from production (`test_current_70d_01_current_head_verified`, `test_current_70d_02_single_canonical_schema`, `test_current_70d_03_schema_hash`).
- Model-output guard: `info.actual_output_classes is None or == 4` — the 128-class regression must never resurface.
- 29 defs / 459 lines; pure-schema tests cross-checking `schema_contract` + consumers.
- NOTE: the hash is the single source of truth — any feature rename/add/remove must bump it, and this suite will fail first.