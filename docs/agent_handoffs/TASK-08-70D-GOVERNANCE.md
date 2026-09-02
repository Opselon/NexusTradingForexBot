# TASK-08-70D-GOVERNANCE — Model Governance / Challenger Lifecycle / Production Readiness

> Agent: Hermes-GovAgent8 · Role: Model Governance · 2026-08-19
> Branch: main · Starting HEAD: 4001e4c (governance baseline 3cca598 TASK-6)

## 1. Mission

Govern the boundary RESEARCH -> EVIDENCE -> VALIDATION -> SHADOW ->
ELIGIBILITY -> HUMAN APPROVAL -> ATOMIC PROMOTION -> MONITOR -> ROLLBACK.
Make it IMPOSSIBLE for an interesting research result to silently become a
production trading model. Nothing here auto-promotes, modifies live risk or
execution policy, or weakens any validation gate.

## 2. Evidence status at handoff (verified)

- Current Champion: 50D `primary_scalp` v1.0.0 (`scalp_v1`), hash
  f0f70efb… (docs/task5_champion_baseline.json).
- 70D candidate: **NO validated 70D candidate registered** — TASK-04-70D
  benchmark blocked on TASK-03-70D-PARITY; shadow70 runtime truthfully
  reports NO_VALIDATED_CANDIDATE; research run_id / OOS / robustness /
  shadow evidence for a 70D model = NONE.
- Classification: **INSUFFICIENT_EVIDENCE**. No production-eligible
  Challenger exists. Nothing was promoted (and nothing CAN be promoted
  until a candidate carries real evidence through the verify gates).

## 3. What was delivered

### New modules (src/nexus_scalp/governance/)
- `verify.py` — `verify_candidate()`: fresh 14-gate re-verification
  (artifact exists/hash, manifest, schema registered+runtime match,
  dimension, scaler, feature-schema hash, liquidity algorithm version,
  training commit, OOS artifact, shadow sample floor, news contract,
  liquidity contract). Every gate is PASS/FAIL/SKIP/INCONCLUSIVE —
  SKIP = INSUFFICIENT_EVIDENCE, never GREEN (spec 18/19). Records
  PROMOTION_BLOCKED_VERIFICATION events.
- `transaction.py` — `execute_promotion_transaction()`: VERIFY -> LOCK ->
  RECORD OLD CHAMPION (PROMOTION_STARTED) -> ACTIVATE -> VERIFY NEW ->
  COMMIT. Audit rows carry PROMOTION_STARTED/COMMITTED/ROLLED_BACK/FAILED
  (crash-recoverable, spec 38). Requires explicit actor + approval token.
- `lock.py` — `PromotionLock`: cross-process exclusive-create lock with
  stale reclaim (PROMOTION_CONFLICT, not partial overwrite, spec 37).

### Extended
- `governance/engine.py` — `promotion_preview()` (spec 28, read-only),
  `rollback_preview()` (spec 30), `emergency_freezes()`,
  `freeze_promotions()`, `unfreeze_promotions()`, `disable_candidate()`
  (spec 31); promote() now blocks when frozen/disabled.
- `governance/store.py` — `record_promotion_audit()`,
  `record_rollback_audit()`, `list_promotion_audits()`,
  `list_rollback_audits()` + lazy schema for the two audit tables.
- `governance/load_gate.py` — `_registered_schema_ids()` now reads the
  CANONICAL schema registry (features/schema.py); scalp_liquidity_v1,
  scalp_v3, scalp_v4 are picked up automatically (previously hardcoded
  tuple failed liquidity/70D challengers).
- `database/registry.py` — migration `AUDIT-0005-governance-audit-tables`
  (model_promotion_audit + model_rollback_audit; idempotent, additive,
  verify+rollback; audit domain now version 6 with TASK-12 AUDIT-0006).
- `application/live_engine.py` — `_champion_bundle_healthy()`
  post-activation smoke (read-only).
- `web/server.py` — `/api/models/governance/status` (spec 32),
  `/api/models/governance/promotion-preview`, `/api/models/promotion/execute`
  (THE only promotion path), `/api/models/governance/rollback-preview`,
  `/api/models/governance/emergency/freeze|unfreeze|disable`,
  `/api/models/governance/audits`.
- `Web/index.html` + `Web/app.js` — Promotion Controls block (candidate +
  approval token fields, Preview, Promote with confirm+actor, Freeze/
  Unfreeze, frozen badge from status API). Div-balance verified (0
  unclosed), node --check clean.

### Registries
- `agents/taskboard.md` — TASK-08-70D-GOVERNANCE row (IN_PROGRESS).
- `agents/change_control.md` — CHG-0017 (PROPOSED).
- `agents/contracts.md` — MODEL_GOVERNANCE v2 row.
- `docs/MODEL_GOVERNANCE_70D.md` — full governance documentation.

### Tests
- `tests/unit/test_model_governance_phase16.py::TestGovernance70` —
  TEST-GOV-01..30 (30 cases) — all PASS.
- `tests/integration/test_model_lifecycle_api.py::TestGovernance70API` —
  7 cases — all PASS.
- `tests/unit/test_database_migrations_phase18.py` — audit version now 6
  (updated by parallel TASK-12; TEST-GOV-26 version-agnostic).

## 4. Verification

- `pytest tests/unit/test_model_governance_phase16.py` — 59 passed.
- `pytest tests/unit/test_database_migrations_phase18.py` — 38 passed.
- `pytest tests/integration/test_model_lifecycle_api.py` — 19 passed.
- `node --check Web/app.js` — OK. HTML div balance — OK.
- Full gate (ruff/mypy/pytest unit+integration) run before commit.

## 5. Known risks / handoff notes

1. **TASK-03-70D-PARITY is the gate to a real 70D candidate** — the
   verification chain (feature schema hash, liquidity algorithm version,
   OOS artifact, shadow floor) is only satisfiable when parity + a real
   artifact exist. TASK-8 hosts the gates, not the evidence.
2. The verify gates treat missing evidence as SKIP→INSUFFICIENT — if a
   later task wants a *lighter* preview they must pass explicit evidence,
   never weaken the gate logic.
3. `execute_promotion_transaction` requires a manifest carrying
   `feature_schema_hash`, `liquidity_algorithm_version`, `training_commit`
   and `oos_artifact` — the training pipeline must record these fields
   (TASK-04 model manifest extension direction).
4. AUDIT-0005 tables are created by migration AND lazily by
   GovernanceStore.ensure_schema (idempotent). On live DBs, run the
   migration engine normally (`nexus db migrate` / startup gate).

## 6. EXACT NEXT-AGENT INSTRUCTIONS (TASK-9)

TASK-9 — 70D governance continuation:

1. **Do NOT promote anything.** The 70D evidence chain is still
   INSUFFICIENT_EVIDENCE until a validated candidate exists.
2. Read `agents/skill.md`, `agents/bugs.md`, `docs/MODEL_GOVERNANCE_70D.md`,
   `docs/70D_SHADOW_RUNTIME.md`, `docs/MODEL_BENCHMARK_70D_LIQUIDITY.md`,
   `docs/agent_handoffs/TASK-08-70D-GOVERNANCE.md`, `agents/locks.yaml`.
3. Verify the current 70D candidate state:
   - `GET /api/models/governance/status` (or the engine snapshot) — is
     there a VALIDATED 70D candidate with real OOS/robustness/shadow
     evidence?
   - If TASK-03-70D-PARITY landed: run `/api/models/governance/promotion-
     preview?model_id=<cand>` and check the gates. If shadow evidence is
     missing, go back to shadow70.
4. Only when ALL mandatory gates are GREEN **AND** the user explicitly
   authorizes a real production promotion: use
   `POST /api/models/promotion/execute` with actor + approval token
   (never script it without the operator).
5. After any real promotion: verify the audit row exists
   (`/api/models/governance/audits`), the previous Champion is preserved,
   `_champion_bundle_healthy()` is true, and promotion monitoring data is
   attributable via `promotion_id`.
6. Register any proven governance defect as BUG-NNN (append to
   agents/bugs.md; next free id starts at BUG-103).
7. Quality gates: `.venv\Scripts\python.exe -m ruff check src tests`,
   `ruff format --check`, `mypy src`, `pytest tests/unit -q`,
   `pytest tests/integration -q`, then `beforePush.ps1`.
8. Commit contract: `<AGENT-NAME>: <imperative summary>` with Agent/Role/
   Scope/Why/Implementation/Verification/Risk/Handoff body; update
   registries additively; check `git diff --cached --name-only` before
   commit.
9. Write `docs/agent_handoffs/TASK-09-70D-GOVERNANCE.md` and the final
   report (champion/candidate/gates/promotion eligibility/rollback/
   concurrency/crash recovery/tests/bugs/files/commit/risks).

## 7. Files changed (TASK-8 working set)

```
src/nexus_scalp/governance/verify.py          (new)
src/nexus_scalp/governance/transaction.py     (new)
src/nexus_scalp/governance/lock.py            (new)
src/nexus_scalp/governance/engine.py          (+preview/rollback/emergency)
src/nexus_scalp/governance/store.py           (+audit persistence)
src/nexus_scalp/governance/load_gate.py       (canonical schema registry)
src/nexus_scalp/governance/__init__.py        (exports)
src/nexus_scalp/database/registry.py          (AUDIT-0005)
src/nexus_scalp/application/live_engine.py    (_champion_bundle_healthy)
src/nexus_scalp/web/server.py                 (status/preview/execute/rollback/emergency/audits)
Web/index.html + Web/app.js                   (promotion controls)
tests/unit/test_model_governance_phase16.py   (TEST-GOV-01..30)
tests/integration/test_model_lifecycle_api.py (TestGovernance70API)
docs/MODEL_GOVERNANCE_70D.md                  (new)
docs/agent_handoffs/TASK-08-70D-GOVERNANCE.md (this file)
agents/taskboard.md, agents/change_control.md, agents/contracts.md (additive)
```