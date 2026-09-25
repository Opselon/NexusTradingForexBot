# 70D FINAL FORENSIC BASELINE — TASK-10 (current repository state)

> Agent: AGENT-10 · TASK-10-70D-FINAL-FORENSIC · 2026-08-19
> Read-only inventory of the ACTUAL repository state as of the final
> verification pass. Cross-checked against executable code.

## System version

| Item | Value | Evidence |
| :--- | :--- | :--- |
| Branch | `main` | `git branch --show-current` |
| HEAD | `b3c8d35` (AGENT-10 probes) — tree is a parallel 70D swarm | `git log --oneline -1` |
| Application version | dev tree (build-info absent; packaged releases v9.0.0/v9.1.0 exist under release/) | release/metadata |
| Web bundle | Web/index.html + app.js (hand-maintained, CRLF app.js) | Web/ |
| CLI | `nexus` (cli/main.py) — db/update/hygiene/incidents commands | cli/ |
| DB schema versions | audit=6 (AUDIT-0001..0006), news=2, candle_intel=2 | schema_migrations tables (integrity ok) |
| Feature schema ACTIVE | `scalp_v1` (50D) — legacy live contract PROTECTED | features/schema.py |
| 70D schema | `scalp_v3` (canonical 70D per TASK-03: Base50+News10+Liquidity10) AND `scalp_v4` (TASK-02 alternate 70D family layout) — both 70D, both candidate-only | features/schema.py |
| Liquidity algorithm | TASK-01 `liquidity_engine.py` (60D scalp_liquidity_v1) + TASK-02 runtime; shadow70 uses the same producer | features/liquidity_engine.py |
| News version | news_context_v1 (12-field canonical; 70D NEWS block = fields 0..8 + news_state at index 59) | model_generation/models.py, schema_contract.py |
| Champion | RESTORED_CANDIDATE: bench_a_v1-derived (50D scalp_v1, hash 9105cef7…) — original frozen f0f70efb… UNRECOVERABLE (BUG-104 incident) | docs/CHAMPION_ARTIFACT_INCIDENT_20260819.md |
| Challenger | NONE registered (registry has only 50D rows) | audit.db experience_model_registry |
| Shadow | shadow70 infra present + tested; NO validated 70D candidate → NO_VALIDATED_CANDIDATE (idle) | docs/70D_SHADOW_RUNTIME.md |
| Migration state | all applied, checksums present, idempotent (TEST-DBM, CLI tests) | artifacts/*.db |
| Model registry | 2 rows : primary_scalp scalp_v1 50D (one preserved f0f70efb fingerprint, one RESTORED_CANDIDATE) | audit.db |

## Feature contract (canonical)

```
0..49  = Base 50D (scalp_v1, FEATURE_NAMES tuple — protected, INV-70D-001)
50..59 = NEWS 10D: active_high_impact_events, xauusd_relevance, usd_relevance,
         bullish_pressure, bearish_pressure, conflict_score, novelty, freshness,
         confidence, news_state            (news_state at index 59, INV-70D-002)
60..69 = LIQUIDITY 10D: bsl_distance_atr, ssl_distance_atr, eqh_strength,
         eql_strength, htf_liquidity_score, internal_liquidity_distance,
         external_liquidity_distance, liquidity_confluence,
         liquidity_sweep_state, post_sweep_displacement (INV-70D-003)
Total  = 70 (schema hash 235b8fccc96b7e0e — feature_schema_hash())
```

## Dependency map (verified)

| Component | Input | Transformation | Output | Persistence | Consumer |
| :--- | :--- | :--- | :--- | :--- | :--- |
| MT5/adapter | broker rates | reseed REPLACE+ALIGN (BUG-058) | normalized M1 bars | — | bar aggregator |
| 50D base | bars + tick | ScalpFeatureEngine.compute_from_bars | 50 floats [-3,3] | FeatureVector | models, replay |
| News 10D | news_context_v1 | vectorize_news_context / features70.news_10d_from_context | 10 floats (state at 59) | samples | 70D builder, shadow70 |
| Liquidity 10D | bars + decision_at | liquidity_engine.compute_liquidity_features | 10 floats causal | — | 70D builder, shadow70 |
| 70D vector | base+news+liquidity | schema_contract.canonical / build_70d_vector / features70.assemble_70d | 70 floats validated | samples | model, shadow70 |
| Model | 70D vector | scaler → ScalpNet | 4 logits | manifests | policy |
| Shadow70 | 70D vector + champion | runtime.observe | disagreement obs | audit.db (queued) | UI, research |
| Governance | lifecycle state | PROMOTION_STATE_MACHINE | transitions | audit.db | operator, UI |
| UI | /api/live/state + /api/liquidity/state | JS render (schema-derived indices) | panels | — | user |

## Probes / evidence

- `scratch/task10_1_causal_baseline.out.txt` — 10D liquidity block exact, 70D composite strict, old [:10] news bug proved.
- `scratch/task10_2_parity_scaler_forensics.out.txt` — scaler inventory, canonical hash, champion restore state.
- `scratch/task10_3_causality_audit.out.txt` — future-data injection → 0.0 diff at T (CAUSAL).

## Known current limitations (recorded, NOT hidden)

1. Active Champion artifact is RESTORED_CANDIDATE (bench_a_v1-derived), NOT the original frozen f0f70efb… — operator decision pending per INV-015 (BUG-104/105 incident docs).
2. No validated 70D candidate/dataset/model exists → Shadow idle (NO_VALIDATED_CANDIDATE), benchmark A/B/C not yet executed (TASK-04 blocked at protocol level).
3. Two 70D schema ids coexist (scalp_v3 canonical per TASK-03 + scalp_v4 alt); shadow70 restored to scalp_v3; the governance/registry reconciliation of v3-vs-v4 semantics is a REMAINING RISK (release gate item).
4. 6D scalers in cand_* dirs are legacy TASK-5 experiment artifacts (not part of the 70D path).