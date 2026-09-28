# 70D SCHEMA REFERENCE MATRIX — Nexus Scalp Engine (NSE)

> Generated: 2026-08-19 · Git HEAD: 3f3f3d938c4cb959907ae5a01e475c5db1af47ed
> Agent: NSE Current-State Reconciliation & Governance Verification
> Method: executable-source classification, not doc-opinion

## 1. Registered schemas (features/schema.py — FEATURE_SCHEMAS)

| schema_id | dim | active | supersedes | classification |
| :--- | :--- | :--- | :--- | :--- |
| scalp_v1 | 50 | **ACTIVE (live Champion)** | — | ACTIVE_RUNTIME (production) |
| scalp_v2 | 60 | no | scalp_v1 | ACTIVE_TRAINING (TASK-5 momentum extras, candidate-only) |
| scalp_liquidity_v1 | 60 | no | scalp_v1 | ACTIVE_TRAINING (TASK-1 liquidity at 50..59, candidate-only) |
| scalp_v3 | 70 | no | scalp_v1 | **CANONICAL 70D** (ACTIVE_DATASET + ACTIVE_SHADOW + ACTIVE_GOVERNANCE) |
| scalp_v4 | 70 | no | scalp_v1 | LEGACY / CANDIDATE_ONLY (TASK-02 integration contract; NO dataset/runtime consumer) |

## 2. Family layout (canonical scalp_v3, from features/schema_contract.py)

```
indices 0..49   BASE 50D        canonical scalp_v1 contract (untouched)
indices 50..59  NEWS 10D        active_high_impact_events, xauusd_relevance,
                                usd_relevance, bullish_pressure, bearish_pressure,
                                conflict_score, novelty, freshness, confidence,
                                news_state   ← news_state at index 59 (TASK-10 fix)
indices 60..69  LIQUIDITY 10D   bsl_distance_atr, ssl_distance_atr, eqh_strength,
                                eql_strength, htf_liquidity_score,
                                internal_liquidity_distance, external_liquidity_distance,
                                liquidity_confluence, liquidity_sweep_state,
                                post_sweep_displacement
```

feature_schema_hash(scalp_v3) = `235b8fccc96b7e0e` (deterministic SHA-256 of the
canonical registry JSON; content-addressed, prefix 16).

## 3. scalp_v3 vs scalp_v4 — exact comparison

| attribute | scalp_v3 | scalp_v4 |
| :--- | :--- | :--- |
| schema_id | scalp_v3 | scalp_v4 |
| dimension | 70 | 70 |
| feature order | Base → News → Liquidity | Base → FAMILY → Liquidity |
| indices 50..59 | NEWS 10D (news_context_v1 first-10 + news_state@59) | FAMILY 10D (TASK-5 momentum or TASK-1 liquidity under their own schema ids) |
| indices 60..69 | Liquidity 10D (liquidity_engine as_vector) | Liquidity 10D (TASK-01 liquidity) |
| normalization | scaler [-3,+3]-clip per schema contract | scaler [-3,+3]-clip |
| missing-value | NaN→0 sanitizer; news zero-vector when disabled | same pattern |
| feature hash | 235b8fccc96b7e0e | f97338e2120aa2e2 |
| algorithm version | liquidity_engine 70d-v1.0.0 | liquidity_engine 70d-v1.0.0 |
| runtime producer | schema_contract (compute_70d_frame / build_70d_vector) | (TASK-02 integration legacy) |
| dataset producer | model_generation/schema_v2 build_70d_dataset (ds_d3f35b, ds_d3886c) | none |
| model consumers | (none validated yet) | wf_candidate smoke artifact (INVALID provenance) |
| shadow consumers | shadow70 runtime (SHADOW70_SCHEMA_ID = scalp_v3) | none |
| governance consumers | verify_candidate runtime_schema_id=scalp_v3; load_gate registry | load_gate accepts (registered) but verify blocks |

**Relationship: SEMANTICALLY DIFFERENT.** scalp_v4's 50..59 block is a FAMILY
slot (integration-contract placeholder), while scalp_v3's 50..59 block is the
real NEWS vector produced by the news bridge. A model trained on one layout
cannot consume features from the other without retraining. scalp_v4 is
LEGACY/CANDIDATE_ONLY: no dataset, no runtime, no shadow consumer. The
`wf_candidate` smoke artifact tagged scalp_v4 is a PROVEN SCHEMA DRIFT.

## 4. Canonical 70D decision

```
CANONICAL_70D_SCHEMA = scalp_v3  (dimension 70, hash 235b8fccc96b7e0e)
```

Evidence chain (all verified at HEAD 3f3f3d9):
- features/schema_contract.py: SCHEMA_ID = "scalp_v3"
- features/schema.py: scalp_v3 registered 70D canonical
- model_generation: ds_d3f35b12d63148da + ds_d3886c503d6c0901 manifests both
  carry feature_schema_hash = 235b8fccc96b7e0e (= computed scalp_v3 hash)
- shadow/shadow70/models.py: SHADOW70_SCHEMA_ID = "scalp_v3"
- governance/load_gate.py: canonical registry resolution includes scalp_v3
- tests/unit/test_schema_70d_reconciliation.py: TEST-SCHEMA-70D-01..08 guard
  scalp_v3 as the ONE canonical 70D contract (58 shadow70+schema tests pass)

## 5. Schema consistency chain (spec §8)

```
canonical schema (scalp_v3)          = 235b8fccc96b7e0e   ✓ (schema_contract)
feature registry (features/schema.py)= scalp_v3/70D        ✓
dataset schema (ds_d3f35b manifest)  = 235b8fccc96b7e0e    ✓ (computed match)
runtime schema (shadow70)            = scalp_v3            ✓
shadow schema (shadow70 models)      = scalp_v3            ✓
model manifest (wf_candidate)        = scalp_v4 ✗ — the ONLY broken link
governance schema (load_gate+verify) = scalp_v3 registry   ✓
```

The wf_candidate manifest is the single inconsistent link; it is also an
unvalidated smoke artifact, so the inconsistency does not block the canonical
chain — it confirms the artifact is NOT a real candidate.

## 6. Classification of every reference

| reference | classification |
| :--- | :--- |
| scalp_v1 50D Champion | ACTIVE_RUNTIME |
| scalp_v2 / scalp_liquidity_v1 60D | ACTIVE_TRAINING (candidate-only) |
| scalp_v3 (schema_contract, datasets, shadow70, governance) | ACTIVE_DATASET + ACTIVE_SHADOW + ACTIVE_GOVERNANCE |
| scalp_v4 (registry entry) | LEGACY / RESEARCH_ONLY |
| wf_candidate model.meta.json (scalp_v4) | INVALID (drift; smoke artifact) |
| docs/70D_* reports | DOCUMENTATION (evidence, not truth) |
| tests/test_schema_70d_reconciliation.py | TEST_ONLY (canonical guard) |