# 70D SCHEMA RECONCILIATION — TASK-11

> Agent: AGENT-11 · 2026-08-19 · Canonical 70D schema decision with
> executable evidence (probe: scratch/task11_1_schema_reconciliation_probe).

## Decision

**CANONICAL_70D_SCHEMA = `scalp_v3`** (dimension 70, schema hash
`235b8fccc96b7e0e`).

`scalp_v4` is the LEGACY TASK-02 integration id — retained in the
registry as a superseded contract, blocked from new production candidates.

## scalp_v3 vs scalp_v4 — explicit diff

| Attribute | scalp_v3 (CANONICAL) | scalp_v4 (LEGACY) |
| :--- | :--- | :--- |
| Feature dimension | 70 | 70 |
| Family layout | Base 0..49 \| News 50..59 \| Liquidity 60..69 | Base 0..49 \| Family 50..59 \| Liquidity 60..69 (ambiguous Family) |
| News block | canonical news_context_v1 fields 0..8 + `news_state` at index 59 (TASK-10 fix) | news 10D unspecified placement (slot 50..59 claimed by TASK-5 momentum OR liquidity) |
| Liquidity placement | 60..69 (canonical engine as_vector order) | 60..69 |
| Schema hash | `235b8fccc96b7e0e` (feature_schema_hash()) | not hashed (no contract module) |
| Producer | schema_contract.py (single source of truth) | none (registry description only) |
| Dataset builder | `build_70d_dataset` (SEVENTY_D_SCHEMA_ID=scalp_v3) | none |
| Runtime governor | SCHEMA_70D=scalp_v3 (after TASK-11) | SCHEMA_70D=scalp_v4 (before TASK-11) |
| Shadow70 | SHADOW70_SCHEMA_ID=scalp_v3 | was v4 (BUG-105 reconciliation) |
| Governance | ALLOWED_SCHEMA_IDS includes scalp_v3, NOT v4 | excluded |
| Release classify | ACTIVE-contract class | LEGACY (classify_artifact) |
| Datasets | ds_task5_real70d_2500, ds_d3886, ds_d3f35 (all scalp_v3 manifest) | none exist |
| Tests | TEST-SCHEMA-70D-01..08 + parity suite | legacy refs only |

## Why scalp_v3 (not commit-timestamp pick)

1. **Source-of-truth architecture**: `features/schema_contract.py` is the
   canonical 70D module (names, hash, family layout, validation). Its
   SCHEMA_ID = scalp_v3.
2. **Governance contract**: contracts.md FEATURE_SCHEMA_70D v1 documents
   scalp_v3 as the canonical 70D (TASK-03-70D-PARITY).
3. **Runtime contract**: shadow70, inference_validator, replay,
   release/model_artifacts, model_generation all key on scalp_v3.
4. **Latest validated implementation**: TASK-03 parity suite (14 tests) +
   TEST-SCHEMA-70D-01..08 assert scalp_v3 == 70D.
5. scalp_v4's "Family 50..59" was ambiguous (TASK-5 momentum OR liquidity
   at 50..59) and never produced a hash/dataset — a research-only id.

## Reference classification (post-TASK-11)

| Reference | Class |
| :--- | :--- |
| schema_contract.py SCHEMA_ID | ACTIVE_RUNTIME (canonical) |
| features/schema.py scalp_v3 registry | ACTIVE_RUNTIME |
| features/schema.py scalp_v4 registry | LEGACY (kept for replay/evidence; classified LEGACY by release) |
| shadow70.SHADOW70_SCHEMA_ID | ACTIVE_RUNTIME (scalp_v3) |
| liquidity_runtime.SCHEMA_70D | ACTIVE_RUNTIME (scalp_v3 after TASK-11) |
| web/server.py error path | ACTIVE_RUNTIME (scalp_v3 after TASK-11) |
| governance/alignment.ALLOWED_SCHEMA_IDS | ACTIVE_RUNTIME (v3 in, v4 absent) |
| release/model_artifacts (v3+v4 maps) | MIGRATION/RETAINED (both have liquidity dep; v4 classified LEGACY) |
| model_generation schema_v2 SEVENTY_D_SCHEMA_ID | ACTIVE_RUNTIME (scalp_v3) |
| tests referencing scalp_v4 | TEST (legacy expectations updated to canonical) |
| docs (TASK-02/TASK-04 handoffs) | DOCUMENTATION (historical) |

## Collision prevention

- `resolve_schema` / registry is the single lookup; no module hardcodes a
  70D id anymore except the canonical constants (schema_contract, shadow70,
  liquidity_runtime, schema_v2) — all = scalp_v3.
- TEST-SCHEMA-70D-05/06/07/08 assert runtime/shadow/governance/legacy
  behavior so a reintroduced v4 ACTIVE_RUNTIME reference fails the suite.

## Schema hash determinism

`feature_schema_hash()` = SHA-256 of the canonical registry JSON
(index/name/family for all 70 dims + schema identity), prefixed 16 hex.
Identical across dataset/replay/runtime/shadow/governance (asserted in
TEST-SCHEMA-70D-04; manifests carry 235b8fccc96b7e0e).