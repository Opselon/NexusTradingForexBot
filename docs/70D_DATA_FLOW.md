# 70D Data Flow (TASK-70D-SYSTEM-FLOW-FORENSICS)

> Agent: Hermes-Forensic-70D · 2026-08-19
> One market event traced end-to-end through the REAL code.

## 1. Tick → 70D vector → model → decision (live path)

| Stage | Component | File | Input | Output | Persistence | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | MT5/Paper tick | adapters/* | broker tick | TickData (frozen, UTC) | — | provenance BROKER_NATIVE/PAPER |
| 2 | Bar aggregation | market_data/bar_aggregator.py | TickData | M1 BarData completed | in-memory | reseed REPLACE+ALIGN (BUG-058) |
| 3 | 50D base | features/scalp_features.py | bars + tick | FeatureVector (50 floats) | — | scalp_v1 contract, FEATURE_NAMES |
| 4 | Regime | features/regime_classifier.py | tick + bars | MarketRegimeState | — | 10 regimes |
| 5 | News context | news/context.py | news DB (worker) | CurrentNewsContext (cache) | news.db | cache-only on tick path |
| 6 | Champion inference | live_engine._infer_probabilities | 50D + scaler | probs (4-class) | — | torch.inference_mode |
| 7 | Policy | signals/policy.py | probs + regime + rules | TradeProposal | audit_signals | rule matrix + SMC |
| 8 | Risk | risk/risk_engine.py | proposal + account | volume | — | dynamic lot + clamps |
| 9 | Execution | execution/order_manager.py | proposal | broker order | audit_orders/ledger | broker-verified |
| 10 | 50D shadow | shadow/engine.py | x50 | shadow decision | shadow_* | simulated=True |
| 11 | 70D shadow | live_engine._record_shadow70_observation | x50+news+liq | Shadow70Observation | shadow70_* | BUG-105 fixed |

## 2. 70D vector assembly (canonical)

```
base50 (0..49)  = live 50D features (ScalpFeatureEngine)
news10 (50..59) = build_news_10(vectorize_news_context(ctx))
                  (fields 0..8 + news_state of news_context_v1)
liq10  (60..69) = build_liquidity_10(self, tick)
                  (liquidity_engine.compute_liquidity_features as_vector)
vector70 = build_70d_vector(base50, family_10=news10, liquidity_10=liq10)
           → strict 50+10+10, raises on mismatch (INV-009 no-silent-pad)
schema_hash = feature_schema_hash()  (per-observation identity)
```

Verified end-to-end by `scratch/trace_70d_vector_assembly.py`:
base 50 finite → news 12→10 → liq 10 → build_70d_vector == assemble_70d →
validate_70d_vector PASS (dimension/finite/bounds/hash).
Per-index mapping: `artifacts/forensics/feature_vector_trace.json`.

## 3. Execution → accounting → research (lineage chain)

```
approved decision (request_id)
   → order creation (order_id / ticket)
   → broker fills (deal_id)
   → audit_ledger (ONE row per ticket, upsert on ticket)
   → audit_experience_outcomes (idempotency_key, execution_id = broker ticket)
   → audit_experiences (immutable decision rows, key exp_<request_id>)
   → strategy_intelligence_registry (derived scores)
   → research dataset (causal, provenance per sample)
   → candidate discovery → backtest → walk-forward → OOS → registry
```

Identity chain: `audit_ledger.ticket == audit_experience_outcomes.execution_id`
and `audit_experience_outcomes.idempotency_key == audit_experiences.idempotency_key`.
The 70D transition did NOT break this chain (schema/provenance per sample
preserved via feature_schema_id + feature_dimension).

## 4. Verification evidence

| Flow segment | Evidence | Status |
| :--- | :--- | :--- |
| tick → 70D vector | trace probe + validate_70d_vector | 🟢 |
| 70D → model | InferenceValidator 10-rejection gate + parity golden | 🟢 |
| model → policy | policy tests + BUG-054 payload contract | 🟢 |
| policy → execution | order lifecycle tests (BUG-081/088/089) | 🟢 |
| execution → accounting | accounting core tests (64) + trade lifecycle (28) | 🟢 |
| accounting → research | research phase09b tests (45) | 🟢 |
| research → registry | registry tests + health endpoint | 🟢 |
| backend → API | integration API suites | 🟢 |
| API → UI | frontend contract tests + live-state contract | 🟢 |