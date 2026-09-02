# SHADOW SYSTEM FORENSIC MAP — 2026-09-02 (CHG-0046)

Agent: Hermes-Main (Shadow owner). Scope: Shadow subsystem only.
Method: read-only code trace + live API/DB probes. No implementation changes
before this map. Every claim cites file:line or a live probe.

## 1. WHAT "SHADOW" ACTUALLY MEANS TODAY (verdict: E — combination, three parallel layers)

| Layer | Location | Purpose (proven, not inferred) | DB tables | Status |
|---|---|---|---|---|
| L1 PHASE 11 Challenger Shadow | `src/nexus_scalp/shadow/` (engine.py, challenger.py, comparison.py, store.py, worker.py, models.py) | B: champion-vs-alternative MODEL comparison. `ShadowEngine.record_shadow_decision` runs `ChallengerRuntime.infer` on the vector passed by the engine hook and stores action/confidence/probabilities pairs; `ShadowComparer` aggregates expectancy/drawdown/regime/strategy + promotion vetoes. Outcome fields exist but are never populated (see D3). | shadow_runs, shadow_decisions, shadow_comparisons, shadow_promotions (audit.db) | WIRED, dormant (no challenger attached; worker RUNNING cycle_count=28 live probe) |
| L2 TASK-6 Governance shadow | `src/nexus_scalp/governance/shadow_runtime.py` + `governance/store.py` | A: model-vs-model inference comparison with same-input alignment (50D→60D/72D extension via `challenger_input_for`), latency governance, parity vs replay reference, failure isolation; appends `model_shadow_comparisons`. | model_shadow_comparisons (TIER_2 never_delete) | WIRED via `/api/models/shadow/attach`; 0 rows live (never attached) |
| L3 TASK-05 70D shadow | `src/nexus_scalp/shadow/shadow70/` (runtime.py, models.py, store.py, worker.py, health.py, liq_provider.py, news_provider.py) | D: partial runtime observer. `Shadow70Runtime.observe` validates the assembled 70D vector, runs the candidate model, classifies disagreement (8+7-class taxonomy), persists observations + health/drift. NO outcome resolution (hardcoded "PENDING"), NO promotion path. | shadow70_observations, shadow70_events, shadow70_feature_health, shadow70_drift_alerts | LIVE hook on every tick; runtime IDLE (no validated candidate attached); 2 historical rows, both invalid SHADOW_BLOCKED (pre-BUG-105-fix era) |

NOT present: C (full strategy simulation) — no SL/TP walk, no fills, no costs.
The certified counterfactual infrastructure
(`research/counterfactual.py` TICK_COUNTERFACTUAL v1, `research/mt5_tick_dataset.py`,
committed CHG-0041 651710d/d8b5c2c) exists and is the designated reuse path for
outcome-aware evaluation; today NOTHING in shadow/ calls it.

## 2. EXECUTION PATHS (traced)

### Champion path (production, live probe 2026-09-02 :8080)
`_process_tick_pipeline` → `_infer_probabilities` (live_engine.py:5011)
→ `_build_live_feature_vector` (4696): base50 = `fv.to_tensor_input()`
(scalp_features.py:349, [-3,+3] clamped); when `effective_feature_dim == 70`:
news10 = `build_news_10(vectorize_news_context(ctx))` (BUG-190 canonical
projection), liq10 = liquidity governor snapshot **only when causal_state==VALID**
(else RuntimeError → inference blocked that tick) → `build_70d_vector`
(strict 50+10+10, INV-009) → `validate_70d_vector(feature_schema_hash())`
→ `bundle.scaler.transform` (ScalerBundle.transform live_engine.py:170:
`(x-mean)/std` clip ±5, no epsilon — trainer already clamps std ≥1e-3 at fit,
walk_forward_trainer.py:824) → `nan_to_num` → ScalpNet → softmax 4 logits
→ `SignalPolicy.evaluate_probabilities` → TradeProposal
(confidence = trained-class directional share, policy.py:156 — CHG-0042).
Live truth: feature_dimension=70, feature_schema_id=scalp_v3,
model_id=primary_scalp, version=v1.0. Artifact:
`artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt` (input width (128,70),
scaler (70,), 0 zero-std; meta num_classes=3, head classes=4).

### L1 shadow path
`_record_shadow_decision` (live_engine.py:5106) — called for every tick after
the proposal exists, gated only on `self._shadow_challenger is not None`:
x50 = `fv.to_tensor_input()` → **the base-50 vector even when the champion
served a 70D tensor** → `ShadowEngine.record_shadow_decision`
(shadow/engine.py:131) → schema gate (challenger ref vs passed schema) →
`ChallengerRuntime.infer` (challenger.py:159: own scaler `(x-mean)/(std+1e-8)`,
NO clip, nan_to_num, softmax, argmax → NO_TRADE/BUY_MARKET/SELL_MARKET/WAIT)
→ ShadowDecisionRecord (hypothetical_r=0.0 hardcoded engine.py:229) →
ShadowStore.save_decision (audit queue, INSERT) → ShadowWorker.finalize at 30
decisions/300s → ShadowComparer.compare → shadow_comparisons.

### L2 governance shadow path
`_record_shadow_decision` also calls `GovernanceShadowRuntime.compare`
(shadow_runtime.py:88) when `engine.active_run_id` — computes REAL 60D extras
from the same bars (compute_60d_extras), aligns via `challenger_input_for`,
latency budget 50ms, parity vs `_governance_reference_vector` (None live →
parity skipped, not failed) → model_shadow_comparisons.

### L3 shadow70 path
`_record_shadow70_observation` (live_engine.py:5240) — every tick, independent
of L1 (BUG-105 fix): base50 from fv, news10 via the SAME canonical projection
as the champion (aligned), liq10 via `build_liquidity_10` (governor snapshot
if <90s old — **no causal_state check**, else bounded engine recompute ≤4000
bars, else neutral zeros — DIVERGES from champion's VALID-only rule) →
`build_70d_vector` → `rt70.observe` (shadow70/runtime.py:348: vector validation
[dim/finite/[-3,3]/schema-hash/freshness 300s/provenance] → `_InferenceFn.infer`
→ `classify_disagreement` → Shadow70Observation) → health/drift monitors →
Shadow70Worker.enqueue (bounded queue 2000, batch 100, daemon thread) →
Shadow70Store.save_observation → audit queue (INSERT OR IGNORE, deterministic
observation_id).

## 3. DEFECT LEDGER (P0/P1/P2, all evidence-backed)

### P0 — invalid comparison / contamination class
- **D1 Champion identity lie.** `champ_ref_dict` (live_engine.py:5141-5148)
  records `self.FEATURE_SCHEMA_ID` (class attr = scalp_v1, schema.py:95) and
  `self.FEATURE_DIM` (class attr = 50) while the loaded champion serves
  scalp_v3/70D (live probe). Both L1 records and L3 provenance inherit it.
  Registry rows corroborate the mess: CHAMPION row scalp_v1/50D pointing at
  the 70d_liquidity path (BUG-125/140 labeling artifact) + CANDIDATE rows
  scalp_v3/70D same artifact. Every L1 decision would carry a false champion
  schema → `SharedInputRef.matches` semantics polluted → promotion-grade
  evidence impossible. FIX: use `self.effective_feature_schema_id` /
  `self.effective_feature_dim` (bundle-authoritative, live_engine.py:218/240).
- **D2 Action-vocabulary mismatch.** Champion action = policy value
  (`BUY`/`SELL`/`NO_TRADE`/`WAIT`/…, enums.py:30) vs shadow argmax
  (`BUY_MARKET`/`SELL_MARKET`/…). `champion_action == challenger_action`
  (engine.py:226) and `classify_disagreement` (shadow70/models.py:266) compare
  raw strings → BUY vs BUY_MARKET counted as a disagreement. FIX:
  `normalize_action` in shadow/models.py applied on both sides before compare.
- **D3 Outcome layer is dead.** `hypothetical_r=0.0  # resolved on exit
  simulation` (engine.py:229) — grep proves NO code ever resolves it; L3
  outcome hardcoded "PENDING" (shadow70/runtime.py:634). Therefore
  ShadowComparer expectancy/drawdown/calibration/tail metrics are all computed
  over zeros (comparison.py:76-118) and the champion-R mirror
  (`-d.hypothetical_r`, comparison.py:86) is mathematically wrong whenever
  either side was NO_TRADE (a flat champion has R=0, not the shadow's loss).
  Shadow today answers "how often do they differ", never "which was better".
  FIX: populate SL/TP/entry geometry from the proposal at record time; add
  offline outcome resolver consuming the CERTIFIED tick/counterfactual
  surface; paired Delta_R; fix champion-R derivation (direction-equality,
  flat=0).
- **D4 shadow70 model reload per tick.** `/api/models/shadow70/attach` builds
  `_infer` with `torch.load(path)` + `np.load(scaler)` INSIDE the per-call
  closure (model_governance_routes.py:~546-560). Every tick would hit disk +
  deserialize; the 50ms budget marks SHADOW_INFERENCE_TIMEOUT but the tick
  path already paid the wall time. FIX: load once at attach; reuse module.
- **D5 Salted, irreproducible feature fingerprint.** `feature_hash =
  getattr(fv,"feature_hash","") or str(hash(tuple(x50[:5])))`
  (live_engine.py:5123-5125) — PYTHON `hash()` is salted per process and only
  the first 5 values: same-input identity (spec 3) is not verifiable across
  restarts and barely within one. FIX: sha1 over the full normalized vector
  (mirrors the engine's model_input_hash convention, live_engine.py:3886).
- **D6 Scaler semantics divergence.** Challenger/shadow70 scale
  `(x-mean)/(std+1e-8)` with NO clipping (challenger.py:177,
  model_governance_routes closure) vs champion `np.clip((x-mean)/std,-5,5)`
  with std pre-clamped ≥1e-3 by the trainer. A challenger is NOT evaluated
  under its training transform. FIX: champion-identical transform in both
  shadow runtimes.

### P1 — persistence / evidence plumbing
- **D7 shadow70 attach cannot succeed for real bundles.** Contract
  `feature_schema_hash` is filled only from `model.json`
  (model_governance_routes.py:507-521); the canonical bundle ships
  `model.meta.json` (no model.json, no scaler_hash key) → validator gate 7
  (runtime.py:204-211) returns SHADOW_DEGRADED → attach fails. Shadow70 is
  structurally UNATTACHABLE today. FIX: read model.meta.json fallback
  (num_features/feature_schema_id), derive `feature_schema_hash` via
  `feature_schema_hash("scalp_v3")` and `scaler_hash` via live sha256 when the
  manifest lacks them.
- **D8 Liquidity input divergence unlabeled.** Champion: governor snapshot
  only when causal VALID, else inference blocked. L3: governor snapshot if
  <90s old REGARDLESS of causal state, else recompute, else neutral zeros
  (liq_provider.py:87-117). The shadow can see liquidity the champion refused,
  or zeros where the champion would have blocked — with no INPUT_MISMATCH
  marker. FIX: stamp liquidity_state/causal evidence on the observation; add
  `INPUT_MISMATCH` disagreement class (additive enum) when provenance diverges.
- **D9 Disagreement counts include invalid rows.** The 2 historical
  SHADOW_BLOCKED rows (valid=0, no shadow model) are counted in
  `disagreement_counts` (store.py:421-435 counts all rows) → UI agreement%
  starts poisoned. FIX: `WHERE valid=1` (analytical read; no history rewrite).
- **D10 Retention gap.** retention.py covers model_shadow_comparisons
  (never_delete, TIER_2) but NO shadow70_* rule while L3 persists per-tick
  (≈86k-430k rows/day when active). Unbounded growth. FIX: bounded raw window
  + aggregated daily summaries (summaries durable, raw bounded) — consistent
  with the existing RETENTION_POLICY v1 pattern.
- **D11 Run freeze incompleteness.** ShadowRun persists model refs/timestamps
  but NO git revision, NO configuration_version; nothing re-verifies that the
  challenger artifact hash at finalize equals the hash at attach
  (artifact replacement mid-run would silently mix identities within one run).
  FIX (minimal): stamp git commit + config version on run start; verify
  artifact hash at finalize; mismatch → run FAILED with reason.

### P2 — analysis depth / semantics
- **D12 Champion confidence semantics.** `champion_confidence` fed to shadow70
  is the POLICY directional share while shadow_confidence is argmax prob —
  confidence-divergence classification compares different quantities. FIX:
  pass the model argmax (from champ_probs) for model-vs-model comparison;
  policy confidence remains available in audit_signals (joinable via
  decision_id) — documented.
- **D13 Status taxonomy.** L3 has a good lifecycle enum; L1/L2 have no status
  field at all (implicit from active_run_id/challenger). FIX: canonical status
  mapping (DISABLED/NOT_CONFIGURED/INITIALIZING/ACTIVE/DEGRADED/INCOMPATIBLE/
  ERROR/NO_DATA) exposed in both summary endpoints.
- **D14 Stale run timestamps.** `ShadowEngine._started` is a CLASS attribute
  default; `start_run` never sets `self._started` → evaluation_duration_hours
  measured from process import time. Minor truthfulness fix.
- **D15 Explainability drill-down** (what did each side see, which features
  differed) — partially served (news/liquidity hashes, parity metrics in L2);
  no per-observation feature attribution. Deferred with design note.

## 4. WHAT IS ALREADY SOUND (verified — do not break)
- Load gates: L2 ten-gate ModelLoadGate (load_gate.py) + L3 seven-gate
  Shadow70LoadValidator (hash-vs-live-file, scaler-vs-live, schema) — a
  malformed candidate is REJECTED/DEGRADED, never silently reshaped. No 50D
  fallback anywhere: dim mismatch → explicit invalid/incompatible.
- Failure isolation: both hooks wrapped; `observe()` never raises; workers
  daemonized with bounded queues; tests TEST-SHADOW-36..40 assert zero broker
  calls under shadow storms.
- Persistence: all writes via the AuditRepository background queue (no sync DB
  on tick path); shadow70 INSERT OR IGNORE on deterministic ids (idempotent).
- News block alignment: L3 and the 70D champion path share the SAME canonical
  projection (`vectorize_news_context` → `build_news_10`) post-BUG-190.
- Governance alignment for L2 refuses zero-filled extras (INV-009).

## 5. DB STATE (live probe 2026-09-02, artifacts/audit.db)
shadow_runs/shadow_decisions/shadow_comparisons/shadow_promotions: 0 rows.
model_shadow_comparisons: 0. shadow70_observations: 2 (both 2026-08-18/19,
valid=0 SHADOW_BLOCKED — historical evidence, preserved). shadow70_events/
feature_health/drift_alerts: 0. No orphan/duplicate/id anomalies found in
existing rows; indexes idx_shadow_* exist for L1 tables; shadow70 tables have
NO secondary indexes (acceptable at current volume; revisit with retention).

## 6. DB → API → UI MAP
- L3: shadow70_observations → Shadow70Store → /api/models/shadow70/{summary,
  health, disagreements, attach, detach, start, stop} → Web/app.js
  loadShadow70Panel/renderShadow70 (status, model id/hash, runtime counters,
  disagreement class counts, feature health, last 30 disagreements + outcome).
  Gaps: outcome always PENDING (D3); disagreement counts include invalid rows
  (D9); canonical status absent (D13); attach broken (D7) so the panel can
  never leave IDLE against real bundles.
- L1: shadow_runs/decisions/comparisons/promotions → ShadowStore →
  /api/models/shadow/{summary,runs,decisions,compare/{id},promotion/{id},
  attach,evaluate-promotion,worker/{start,stop}} → governance panel section
  (Web/app.js gov-*). Gaps: outcome metrics zero (D3); champion identity (D1).
- L2: model_shadow_comparisons → GovernanceStore → /api/governance/comparisons
  → governance panel. Sound but dormant.

## 7. OWNERSHIP / SAFETY
Zero order authority verified by import graph: shadow/* imports no
adapter/order_manager/risk/policy module. Champion untouched by shadow
failures (structural + tests). Promotion: `evaluate_promotion` computes
evidence only; no auto-promotion call site exists (grep verified).
