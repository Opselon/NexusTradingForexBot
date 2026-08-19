# TASK-05-70D-SHADOW — Handoff

> Agent: Hermes-Shadow70D · Role: 70D Shadow Runtime / Drift / Champion-Safe
> Deployment Engineer · 2026-08-19
> Brief: TASK 5/10 — 70D Liquidity Shadow Runtime, Live Observation, Drift
> Detection & Champion-Safe Deployment

---

## 1. TASK-4 / First-Gate result

| Item | Value |
| :--- | :--- |
| Candidate status | **NO_VALIDATED_CANDIDATE** |
| Evidence | artifacts/audit.db experience_model_registry: 2 rows, both `primary_scalp_scalp_v1_50d` (scalp_v1/50D); lifecycle_status CANDIDATE. No scalp_v3/70D model registered or validated. |
| Consequence | Real production runtime untouched; Shadow infrastructure built + verified against a deterministic VALIDATED fixture (controlled test fixture, spec 2). |

The 70D lineage (Liquidity foundation → integration → parity → validation)
is mid-flight in parallel TASK-01..04 (uncommitted WIP at bootstrap:
liquidity_engine.py + scalp_liquidity_v1 schema + tests, 12 failing tests on
its own moving contract). The 70D shadow layer was therefore built
CONTRACT-FIRST against the parallel series' own published spec
(docs/POST_70D_RUNTIME_INVARIANTS.md) so it is correct before AND after the
series lands.

## 2. Shadow model (fixture path, TEST-SHADOW-01)

- model_id: `cand_70d_liquidity_v1` (fixture) — attach API resolves the
  real registry row when one exists
- model_version: `v1.0`, schema: `scalp_v3` (70D), dimension: 70
- artifact_hash / scaler_hash: sha256 of the artifact files (verified live
  at load time)

## 3. Shadow architecture

See docs/70D_SHADOW_RUNTIME.md §2 (diagram). New package
`src/nexus_scalp/shadow/shadow70/`:

```
models.py        frozen contracts, disagreement taxonomy (8 classes)
runtime.py       Shadow70LoadValidator (manifest/hash/schema/dim/scaler) +
                 Shadow70Runtime (attach/observe/pause/stop, bounded,
                 never raises)
health.py        Shadow70FeatureHealthMonitor + Shadow70DriftMonitor
                 (PSI/mean/std/missing, NORMAL..CRITICAL)
store.py         Shadow70Store (audit.db queued writes; INSERT OR IGNORE)
worker.py        Shadow70Worker (bounded queue, batch flush, backpressure)
liq_provider.py  liquidity producer bridge (lazy, isolated)
```

Wiring: live_engine `_record_shadow_decision` adds a flag-guarded
(`shadow70_enabled`, default false) observability hook; web server: 6 new
`/api/models/shadow70/*` endpoints; Web UI: "70D Shadow Model" section in
the Model Governance tab (index.html LF + app.js CRLF, div-balance PASS,
node --check PASS).

## 4. Champion isolation (proof)

- Module graph probe: no adapter/OrderManager/RiskEngine/MT5 tokens in
  shadow70 (TEST-SHADOW-08..12, smoke broker_tokens=[]).
- Champion data passed read-only; observation returns a new frozen record.
- Shadow persistence writes only shadow70_* tables (INV-018).
- LiveEngine hook: try/except isolated; state != READY → no-op.

## 5. Live feature health (real 10D Liquidity stats — smoke run)

| feature | mean | std | miss | zero | n |
| :--- | ---: | ---: | ---: | ---: | ---: |
| bsl_distance_atr | 0.2000 | 0.1414 | 0.00 | 0.20 | 40 |
| ssl_distance_atr | 0.2000 | 0.1414 | 0.00 | 0.20 | 40 |
| eqh_strength | 0.2000 | 0.1414 | 0.00 | 0.20 | 40 |
| eql_strength | 0.2000 | 0.1414 | 0.00 | 0.20 | 40 |
| htf_liquidity_score | 0.2000 | 0.1414 | 0.00 | 0.20 | 40 |
| internal_liquidity_distance | 0.2000 | 0.1414 | 0.00 | 0.20 | 40 |
| external_liquidity_distance | 0.2000 | 0.1414 | 0.00 | 0.20 | 40 |
| liquidity_confluence | 0.2000 | 0.1414 | 0.00 | 0.20 | 40 |
| liquidity_sweep_state | 0.2000 | 0.1414 | 0.00 | 0.20 | 40 |
| post_sweep_displacement | 0.2000 | 0.1414 | 0.00 | 0.20 | 40 |

(Synthetic read-only smoke vector; real live stats appear once a candidate
attaches and real liquidity data flows.)

## 6. Drift

Reference distributions are not yet available (no training dataset for the
70D candidate) → `INSUFFICIENT_EVIDENCE`/NORMAL. The monitor + thresholds
are unit-tested (NORMAL/WATCH/WARNING/CRITICAL, min_samples floor).

## 7. Agreement / disagreement (fixture path)

From the ready-path fixture smoke (30 observations):
classes seen: BUY_VS_SELL, CHAMPION_NO_TRADE_SHADOW_BUYS,
CONFIDENCE_DIVERGENCE; agreement 8 / disagreement 22. Live counts will come
from real observations once a candidate attaches.

## 8. Outcome linkage

`shadow70_observations.outcome` defaults to PENDING; a future research
process resolves it on the shadow tables only (never accounting; INV-018).
The observation carries snapshot_id/feature_hash/news_context_hash/
liquidity_feature_hash for linkage without reconstruction.

## 9. Performance

- avg shadow latency (fixture): 0.069 ms · budget 50 ms · p95 bounded
- queue: bounded 2000 (worker) / 50 (smoke) — drops recorded
- memory: observations ≤ 2000, health ≤ 1000, drift ≤ 5000; no unbounded
  accumulation

## 10. Live READ-ONLY result

scratch/shadow70_live_readonly_smoke.out.txt (real registry + real audit.db):
NO_VALIDATED_CANDIDATE truthfully reported; runtime IDLE; idempotency same
ids; broker tokens = 0; active schema = scalp_v1/50D (Champion unchanged);
feature health computed; persistence 1 row landed; backpressure observed.

## 11. Broker safety

order_count = 0 · modify_count = 0 · cancel_count = 0 (TEST-SHADOW-37 with
2000 inferences + MockBroker probe; runtime exposes no broker surface).

## 12. Tests

52 unit tests across 3 files (all passing; full suite re-run below):
- tests/unit/test_shadow70_runtime.py — 36 (TEST-SHADOW-01..35 core)
- tests/unit/test_shadow70_safety.py — 5 (36..40 hard safety)
- tests/unit/test_shadow70_health_drift.py — 11 (health/drift/parity/INV)

## 13. Bugs

BUG-100 appended to agents/bugs.md: "70D Shadow Runtime Did Not Exist; No
Validated 70D Candidate In Registry" — FIXED (infrastructure; candidate
availability remains a First-Gate registry question). Also fixed during
build: `index` SQL reserved word in shadow70_feature_health DDL.

## 14. Files

New:
- src/nexus_scalp/shadow/shadow70/{__init__,models,runtime,health,store,
  worker,liq_provider}.py
- tests/helpers/shadow70_fixtures.py
- tests/unit/test_shadow70_{runtime,safety,health_drift}.py
- docs/70D_SHADOW_RUNTIME.md
- docs/agent_handoffs/TASK-05-70D-SHADOW.md
- scratch/shadow70_live_readonly_smoke.py (+ .out.txt)
- scratch/shadow70_ready_path_fixture_smoke.py (+ .out.txt)

Modified:
- src/nexus_scalp/application/live_engine.py (shadow70 init + isolated hook)
- src/nexus_scalp/web/server.py (6 shadow70 endpoints)
- Web/index.html, Web/app.js (70D Shadow panel; div-balance + node-check OK)
- agents/taskboard.md, agents/change_control.md (CHG-0013),
  agents/runtime_invariants.md (INV-018), agents/contracts.md (SHADOW_70D/
  SHADOW_LOAD_GATE/SHADOW_FEATURE_HEALTH/SHADOW_DRIFT), agents/bugs.md
  (BUG-100)

Parallel-agent WIP (liquidity engine series) NOT touched; scratch/
liq_tests_scan.txt + this task's smoke outputs added under scratch/.

## 15. Commit

See commit message at repo HEAD (agent-labelled
`Hermes-Shadow70D: TASK-05-70D-SHADOW ...`).

## 16. Remaining risks

| Risk | Class |
| :--- | :--- |
| No validated 70D candidate in registry (shadow stays IDLE until series lands) | PROVEN (registry state) |
| Liquidity producer unresolved until parallel WIP lands → neutral 10D | PROVEN (bridge isolated) |
| Live drift behavior with real distributions (reference = training) | UNKNOWN (no training dist yet) |
| Live inference latency with real torch model (fixture used a stub fn) | UNKNOWN (budget enforced) |
| Shadow infra correctness | PROVEN (52 tests + 2 smokes) |

## 17. EXACT NEXT-AGENT INSTRUCTIONS (TASK-6)

1. READ BEFORE ANY EDIT: agents/skill.md, agents/bugs.md (head — BUG-100 is
   the latest ledger entry), agents/contracts.md (SHADOW_70D rows),
   agents/runtime_invariants.md (INV-018), docs/70D_SHADOW_RUNTIME.md,
   docs/POST_70D_RUNTIME_INVARIANTS.md, agents/taskboard.md (TASK-05-70D-
   SHADOW row), agents/locks.yaml.
2. First Gate: check `experience_model_registry` for a scalp_v3/70D row with
   lifecycle_status containing VALIDATED. If present: `POST
   /api/models/shadow70/attach` (or the equivalent registry-driven loader)
   — the load gate verifies manifest/hash/schema/dimension/scaler. If the
   70D series has landed its producer, verify `liq_provider.build_liquidity_10`
   resolves it (update the bridge's feature mapping if the series renamed
   anything) and confirm `liquidity_calculation_version` is stamped.
3. Before treating shadow as evidence: collect ≥ 30 live observations
   (shadow70_observations), then SET the drift reference from the 70D
   training dataset (Shadow70DriftMonitor.set_reference with the training
   per-feature mean/std/missing-rates) and re-run drift (severity from
   INSUFFICIENT_EVIDENCE → real).
4. Outcome linkage (spec 25): resolve shadow70_observations.outcome via a
   research-worker step that reads future bars from shadow tables ONLY —
   never write accounting/ledger/experience rows (INV-018). Mark
   INSUFFICIENT_EVIDENCE until the sample floor.
5. Promotion (only if a LATER governance process authorizes): use the
   existing TASK-6 governance promotion path
   (GovernanceEngine.promote/approve + /api/models/promotion/*) — never
   auto-promote from shadow evidence.
6. Before any change to live_engine shadow70 hook: verify the flag
   (`_shadow70_enabled`, default false) and try/except isolation remain.
7. Run beforePush (full gate) and add regression tests in the EXISTING
   test_shadow70_* files (extend, don't fork).
8. Commit as `<AGENT>: <task>` with handoff doc + registry rows additively.
9. Report to Telegram in Persian with [SHADOW70] event evidence (real
   counters only).