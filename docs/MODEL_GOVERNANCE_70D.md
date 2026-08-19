# MODEL GOVERNANCE 70D — Nexus Scalp Engine (NSE)

> TASK-08-70D-GOVERNANCE (2026-08-19) · Agent: Hermes-GovAgent8
> Extends the TASK-6 governance boundary (`src/nexus_scalp/governance/`) for
> the 70D Liquidity challenger lifecycle. Central invariant of this task:

```
RESEARCH -> EVIDENCE -> VALIDATION -> SHADOW -> ELIGIBILITY ->
HUMAN APPROVAL -> ATOMIC PROMOTION -> MONITOR -> ROLLBACK IF REQUIRED
```

It must be **impossible** for an interesting research result to silently
become a production trading model. Nothing in this document promotes,
auto-promotes, or weakens any validation gate.

---

## 1. Lifecycle state machine

The canonical lifecycle is `PROMOTION_STATE_MACHINE v1` (governance/models.py,
TASK-6). TASK-8 does NOT invent a second machine. Evidence-driven states:

```
DISCOVERED -> RESEARCHED -> TECHNICALLY_VALIDATED -> OOS_VALIDATED ->
ROBUSTNESS_VALIDATED -> SHADOW_VALIDATED -> PRODUCTION_ELIGIBLE ->
AWAITING_APPROVAL -> APPROVED -> PROMOTION_TRANSACTION -> CHAMPION
```

Failure/archive states: `REJECTED | DEGRADED | QUARANTINED | REVOKED |
ROLLED_BACK | RETIRED`.

In code the legal transition table is `PROMOTION_TRANSITIONS`:
RESEARCH → VALIDATED → CHALLENGER → SHADOW → READY_FOR_REVIEW → APPROVED →
CHAMPION. **SHADOW → CHAMPION is an illegal transition** (INV-015 / TEST-GOV-28).

## 2. Mandatory gate matrix (machine-readable)

`governance/verify.py::verify_candidate` produces a per-gate verdict matrix.
Every gate is explicit: `PASS | FAIL | SKIP | INCONCLUSIVE`. **No averaging —
an OOS=GREEN, Robustness=GREEN, Shadow=RED candidate is FINAL=NOT ELIGIBLE**
(spec 18/19). A SKIPPED gate is INSUFFICIENT_EVIDENCE, never GREEN.

| Gate | Evidence key | Hard failure |
| :--- | :--- | :--- |
| artifact | artifact_exists / artifact_hash_matches | FAIL |
| schema | schema_registered / schema_matches_runtime | FAIL |
| dataset | manifest_valid | FAIL |
| training | training_commit_recorded | SKIP→INSUFFICIENT |
| walk-forward / OOS | oos_artifact_recorded | SKIP→INSUFFICIENT |
| robustness | liquidity_version_matches (algorithm version) | SKIP→INSUFFICIENT |
| calibration | feature_schema_hash_matches | SKIP→INSUFFICIENT |
| shadow | shadow_evidence_recorded (sample floor) | SKIP→INSUFFICIENT |
| drift | news_contract_valid / liquidity_contract_valid | FAIL |
| news/liquidity deps | news_contract / liquidity_contract | SKIP→INSUFFICIENT |

Promotion requires **all mandatory gates GREEN**: failures block
(PROMOTION_BLOCKED_*), missing evidence blocks (INSUFFICIENT_EVIDENCE).

## 3. Champion immutability

The Champion carries `model_id, version, artifact_hash, manifest_hash,
schema_id, scaler_hash`. Promotion records old Champion + new Champion
atomically in `model_promotion_audit` and **never deletes the old artifact**
(spec 6). The previous Champion is the rollback target.

## 4. Promotion transaction (atomic, spec 8/29/37/38)

`governance/transaction.py::execute_promotion_transaction`:

```
VERIFY CANDIDATE   fresh re-verification (never cached governance state)
LOCK GOVERNANCE    cross-process exclusive lock (PROMOTION_CONFLICT beat)
RECORD OLD CHAMPION promotion audit row PROMOTION_STARTED
ACTIVATE NEW       operator-wired runtime activation
VERIFY NEW         post-activation smoke (load, schema, bundle health)
COMMIT             PROMOTION_COMMITTED audit row + governance event
```

- Requires explicit `actor` (never "system") and an `approval_token`.
- A second concurrent promotion is rejected with PROMOTION_CONFLICT, never a
  partial overwrite.
- Crash states are persisted in the audit row: PROMOTION_STARTED /
  PROMOTION_COMMITTED / PROMOTION_ROLLED_BACK / PROMOTION_FAILED — after a
  restart the audit table is the source of truth (spec 38).
- Any failure restores the previous Champion (rollback callback); never
  leaves "no Champion" or a half-promoted Champion.

## 5. Promotion preview (spec 28)

`/api/models/governance/promotion-preview?model_id=...` is READ-ONLY:
current Champion + candidate identity/hash, schema pair, gate verdicts,
rollback availability. No mutation, no lock.

## 6. Rollback (spec 23/30)

`rollback_preview` verifies the old artifact is still valid (load gate +
hash) BEFORE the operator commits. Manual rollback requires an explicit
operator; only predefined catastrophic technical failures (model cannot
load, schema mismatch, nonfinite inference, latency breach) may trigger a
rollback path. A losing session/hour/trade is NEVER a rollback trigger
(spec 11/12, TEST-GOV-21).

## 7. Concurrency (spec 37)

`governance/lock.py::PromotionLock` — exclusive-create lock file with
stale-lock reclaim (same pattern as the database migration engine). Two
agents/processes cannot promote simultaneously (TEST-GOV-29).

## 8. Emergency controls (spec 31)

`freeze_promotions / unfreeze_promotions / disable_candidate` — model
governance is distinct from Stop Bot: freezing promotion NEVER stops the
engine, and disabling a candidate NEVER deletes its evidence. Every action
is recorded in the governance event ledger (`PROMOTION_FREEZE`,
`CANDIDATE_DISABLED`, ...).

## 9. Audit trail (spec 29/30)

- `model_governance_events` — append-only narrative ledger (TASK-6).
- `model_promotion_audit` — structured promotion transaction rows
  (old/new champion pair, approver, token, hashes, rollback target).
- `model_rollback_audit` — structured rollback rows.
- Tables created by migration `AUDIT-0005-governance-audit-tables`
  (idempotent, versioned, rollback-aware; INV-013). API:
  `/api/models/governance/audits`.

## 10. Feature drift + News/Liquidity dependency gates (spec 22/23)

The 70D model depends on Base 50D + News 10D + Liquidity 10D. Verify gate
flags WARNING / CRITICAL drift; CRITICAL blocks promotion, and an
unavailable News/Liquidity contract blocks promotion (spec 23,
TEST-GOV-09/23/24). The runtime policy follows the feature contract — the
model never silently receives malformed data.

## 11. Installed-release compatibility (spec 24/25)

- `load_gate._registered_schema_ids()` reads the CANONICAL schema registry
  (features/schema.py) — scalp_v1/v2/liquidity_v1/v3/v4 resolve dynamically;
  an unregistered schema is rejected.
- Production runtime compatibility is verified in the promotion transaction
  (runtime_schema_id + runtime_dimension must match the candidate).

## 12. Sample floors (spec 20)

OOS / shadow / calibration / post-promotion monitoring floors are enforced
by the verify gates: `shadow_evidence.sample_floor_met` must be True with
`sample_floor` counts present. A 3-win candidate is never Champion (TEST-GOV-22).

## 13. API / UI governance (spec 27/32/33)

New endpoints (all serializer-enum safe, no stack traces leaked):

```
GET  /api/models/governance/status             spec 32 status contract
GET  /api/models/governance/promotion-preview  spec 28 preview
POST /api/models/promotion/execute             the ONLY promotion path
GET  /api/models/governance/rollback-preview   spec 30
POST /api/models/governance/emergency/freeze|unfreeze|disable   spec 31
GET  /api/models/governance/audits             promotion/rollback audit trail
```

UI (`Web/`): Promotion Controls block — candidate/token fields, Preview,
Promote (explicit confirm + actor prompt), Freeze/Unfreeze, promotion-freeze
badge from `/api/models/governance/status`. A button never calls a hidden
auto-promotion path.

## 14. Governance error trace (spec 34)

Every governance failure logs `[GOVERNANCE] event=... candidate=... stage=...
gate=... expected=... actual=... correlation_id=... error_code=...`.
Codes: PROMOTION_BLOCKED_OOS / _SCHEMA / _SHADOW / _DRIFT, PROMOTION_FAILED,
ROLLBACK_TRIGGERED, ROLLBACK_FAILED, PROMOTION_CONFLICT. No silent failures.

## 15. Evidence status (2026-08-19)

- Current Champion: 50D `scalp_v1` (`primary_scalp` v1.0.0, see
  docs/task5_champion_baseline.json).
- 70D candidate: **NO validated 70D candidate exists in the registry**
  (TASK-04 benchmark BLOCKED on TASK-03 parity; shadow70 reports
  NO_VALIDATED_CANDIDATE). Evidence chain = INSUFFICIENT_EVIDENCE → no
  production-eligible Challenger exists → nothing is promotable.
- The governance PLATFORM is complete and tested; it goes live the moment a
  candidate carries real evidence.

## 16. Tests

`tests/unit/test_model_governance_phase16.py::TestGovernance70` —
TEST-GOV-01..30 (lifecycle, rejection, schema/artifact/scaler/OOS/
robustness/shadow/drift blocks, human approval, audit, atomicity, rollback,
immutability, monitoring, sample floor, migration compatibility, no
auto-promotion, lock, restart safety).

`tests/integration/test_model_lifecycle_api.py::TestGovernance70API` —
API endpoints (status, preview read-only, token requirement, rollback
preview, freeze blocks execution, audits).

## 17. Safety contract

This package imports NO adapter, NO order manager, NO risk engine and NO
execution object (INV-002/003/004). Governance can never place, modify or
close a trade. `execute_promotion_transaction` activates via the
operator-wired LiveEngine `_activate_promoted_model` ONLY after full
verification + lock + approval token.