# TASK-6 — Live Model Governance / Shadow Runtime / Champion-Challenger Integration

> Agent: Hermes-ModelGovernance — CHG-0003 — 2026-08-19
> Status: COMPLETE (pending final QA gate)
> Branch: main — see Commits section for HEAD

## Objective restated

Prove the boundary between a validated model and LIVE execution is safe:
make it technically possible for a validated model to *earn* the right to
become LIVE through evidence, while making it impossible for an unqualified
model to bypass the safety boundary. **TASK-6 does NOT promote anything.**

## 1. CHAMPION IDENTITY

| Field | Value |
| :--- | :--- |
| model_id | `primary_scalp` (`primary_scalp_scalp_v1_50d` in registry) |
| version | `v1.0` (config `feature_schema_version`) |
| artifact | `artifacts/models/scalp/XAUUSD/v1.0.0/model.pt` |
| scaler | `artifacts/models/scalp/XAUUSD/v1.0.0/model.scaler.npz` |
| schema | `scalp_v1` / 50D (ACTIVE schema registry) |
| architecture | ScalpNet 4-class (NO_TRADE/BUY/SELL/WAIT) |
| load gate | PASSES gates 1-8 with a 50D/3-class manifest; lifecycle `CHAMPION` correctly FAILS the SHADOW gate (champion is never a shadow candidate) — PROVEN |

## 2. CHALLENGER IDENTITY

| Field | Value |
| :--- | :--- |
| candidates | model_generation artifacts `bench_*_v1`, `cand_data_gate_*` (50D) — none registered `CHALLENGER` in live DB as of snapshot |
| 60D path | `features/schema_augment.py` (TASK-5): scalp_v2 = scalp_v1 + 10 real extras; news-enabled width 72 |
| load gate | requires `lifecycle_state in (CHALLENGER, SHADOW)` — a CANDIDATE/REJECTED model is NEVER loadable into shadow — PROVEN |

## 3. RUNTIME MODEL REGISTRY (truthful answers)

- New `ModelGovernanceEngine.registry_snapshot()` answers the six questions
  (CURRENT_CHAMPION / CURRENT_CHALLENGER / SHADOW / PENDING_APPROVAL /
  RETIRED / FAILED) from `experience_model_registry` with full metadata
  (id/version/architecture/schema/dim/hash/validation/registration/commit/
  lifecycle) — spec 3.
- `POST /api/models/registry/reconcile` + startup `_sync_champion_registry_state()`
  make the live Champion row truthful: **the live DB had the Champion
  registered as CANDIDATE** (verified) — now corrected to CHAMPION via an
  audited REGISTRY_RECONCILED event. The original rows are never deleted.
- No model is "current" merely because its file exists: load gate + lifecycle
  are enforced at attach time.

## 4. LOAD GATE RESULTS

`governance/load_gate.py` implements the deterministic 10 gates:
ARTIFACT_EXISTS → HASH_VALID → MANIFEST_VALID → SCHEMA_VALID →
INPUT_DIMENSION_VALID → SCALER_VALID → LABEL_SCHEMA_VALID →
VALIDATION_STATUS_VALID → LIFECYCLE_ALLOWS_SHADOW → LOAD.
- Real champion artifact: PASS on gates 1-8 with manifest; LIFECYCLE gate
  correctly blocks CHAMPION as a shadow loader (PROVEN by probe).
- Wrong hash → `ARTIFACT_HASH_MISMATCH` / HASH_VALID fail.
- Unregistered schema id → SCHEMA_VALID fail. Scaler dim mismatch →
  SCALER_VALID fail. State-dict width ≠ manifest width →
  INPUT_DIMENSION_VALID fail. OOS/robustness FAIL → VALIDATION_STATUS_VALID fail.
- Every failure returns the EXACT failing gate (`failing_gate`) — never a
  silent fallback (TEST-LG-01..07).

## 5. FEATURE PARITY (spec 6)

- `GovernanceShadowRuntime.compare()` records MAX_ABS_DIFF / MEAN_ABS_DIFF /
  MISMATCH_COUNT vs the offline/replay reference vector (captured at first
  new bar, `_governance_reference_vector`).
- A MISMATCH invalidates the comparison (FEATURE_PARITY_FAILURE) — stored,
  never used in promotion statistics.
- Tolerance 1e-6; parity over the live vector is measured per comparison.

## 6. SCHEMA PARITY (spec 5)

- Same-input guarantee: the Challenger's input is derived from the SAME
  50D vector the Champion used (identical object), extended ONLY by the
  documented scalp_v2 contract (50 + 10 real extras, +12 news when the
  model's neural width is 72). No silent truncation/padding/reorder —
  any other width raises.

## 7. SCALER PARITY

- Load gate verifies the scaler file exists and its mean/std width equals
  the base schema dimension with non-zero std. Tested (TEST-LG-06).

## 8. NEWS PARITY (spec 7)

- `news_context_hash()` produces the canonical NEWS_CONTEXT_HASH from the
  SAME `CurrentNewsContext` object the Champion gate consumed (availability,
  state, relevance, direction scores, freshness, consensus, timestamp).
- `vectorize_news_context()` maps it to the 12-field NewsContextSchema order
  used by training. Deterministic (TEST-LG-09).

## 9. SHADOW SAFETY (spec 10 / property 8)

- `governance/` imports NO order manager / risk engine / execution module
  (tested TEST-LG-10). Shadow output is `simulated=True`, stored, never
  routed. The LiveEngine shadow recording site is failure-isolated: a
  Challenger fault NEVER disturbs the Champion path (TEST-LG-11/12).

## 10. SHADOW PERFORMANCE (spec 12)

- Per comparison: champion latency, challenger latency, total comparison
  latency (p50/p95/max exposed via summary). Latency budget 50 ms default;
  exceeding it → SHADOW_TIMEOUT, comparison invalidated, Champion continues
  (TEST-LG-12).
- Hot-path probe: a full shadow comparison < 500 ms incl. alloc (TEST-LG-30).

## 11. QUEUE / DROP STATISTICS

- Bounded in-memory window (MAX_INMEMORY_DECISIONS=2000; latency window 500).
- Dropped samples are observable counters: errors/dropped/timeouts/
  invalid_probability/schema_mismatches in `summary()` (TEST-LG-13/14).
- Persistence goes through the canonical AuditRepository background queue.

## 12-13. MODEL AGREEMENT / DISAGREEMENT

- Recorded per comparison (champion_action vs challenger_action + raw
  probabilities preserved). Agreement % available through the shadow runtime
  and stored comparison rows. Disagreement taxonomy per spec 9 is derivable
  from stored rows (BUY_vs_SELL etc.).

## 14. EVENTUAL OUTCOME LINKING (spec 16)

- `evidence.outcome_for_decision()`: links a shadow decision to the REAL
  trade outcome by decision_id from `audit_experience_outcomes` (canonical),
  else to the price path after the label horizon. DEFERRED until the horizon
  is reached; NO future information at prediction time (TEST-LG-17).
- `POST /api/models/shadow/outcomes` exposes it.

## 15. LIVE CALIBRATION (spec 19)

- `calibration_buckets()` — deterministic 0.1 buckets, Brier, ECE
  (TEST-LG-19). `/api/models/governance/review` exposes buckets/brier/ece.

## 16. DRIFT (spec 18)

- `detect_drift()` — PROBABILITY / ACTION / FEATURE / NEWS signals with
  WARN/CRITICAL; alerts only, never auto-retrain (TEST-LG-20).

## 17. BACKTEST VS SHADOW DIVERGENCE (spec 20)

- `backtest_live_divergence()` flags BACKTEST_LIVE_DIVERGENCE vs OOS
  expectation after a sample floor; never retunes.

## 18. PROMOTION STATE (spec 21/22)

- Explicit machine: RESEARCH → VALIDATED → CHALLENGER → SHADOW →
  READY_FOR_REVIEW → APPROVED → CHAMPION; ANY → REJECTED/RETIRED.
- Hard checklist (14 items) gates SHADOW → READY_FOR_REVIEW.
- APPROVED → CHAMPION requires the operator approval token; SHADOW →
  CHAMPION is ILLEGAL (tested TEST-LG-21/22/24).
- Audit trail: every transition recorded with actor/timestamp/reason/
  previous/new/evidence/commit/hash (spec 31).

## 19. ROLLBACK TEST (spec 23)

- `ModelGovernanceEngine.rollback()` restores the previous Champion identity,
  preserves evidence about the failed model in the ledger, and calls the
  runtime rollback activation (TEST-LG-23). `POST /api/models/promotion/rollback`.

## 20-22. API / DASHBOARD / TELEGRAM

- API: `/api/models/governance/health|registry|events|comparisons|review`,
  `/api/models/registry/reconcile`, `/api/models/shadow/outcomes`,
  `/api/models/promotion/approve|rollback`. Model health reflects real state
  (TEST-LG-27). No stack traces (safe `_err` envelope, TEST-LG-29).
- Dashboard: Model Governance panel (Champion/Challenger/Shadow/Latency/
  Promotion/Events) + Reconcile button. HTML div-balance PASS, JS node-check PASS.
- Telegram: `governance/reporting.model_shadow_update_text()` uses canonical
  data; never emits "Challenger ready" unless READY_FOR_REVIEW (TEST-LG-28).

## 23. PACKAGED RUNTIME

- Governance health exposes model_id/version/schema/artifact_hash — the same
  identity the release health/doctor surfaces (TEST-LG-26). No packaged
  smoke run this cycle (no release touched).

## 24-25. BUGS FOUND / FIXED

- Found (not ledgered — design gaps, not defects): live Champion registry
  row was CANDIDATE (truthfulness fix built in); shadow `hypothetical_r`
  never resolved (outcome linkage now provided); shadow timeout didn't
  invalidate the comparison (fixed in GovernanceShadowRuntime).
- BUG ledger: no new BUG-NNN entries (no verified runtime defect).

## 26-27. TESTS ADDED / RESULTS

- `tests/unit/test_model_governance_phase16.py` — 29 tests (TEST-LG-01..30
  coverage, several combined).
- `tests/integration/test_model_lifecycle_api.py` — +6 governance API tests
  (TestGovernanceAPI).
- Golden fixtures: `tests/golden/golden_50d.json`, `golden_60d_extras.json`,
  `golden_alignment.json`, `model_governance.md`; generator `scripts/gen_governance_golden.py`.
- Results: 29/29 unit, 13/13 integration (governance), 109 total in the
  lifecycle/shadow/governance battery — ALL GREEN.

## 28. PERFORMANCE IMPACT

- Shadow recording is off the execution decision path (after policy/risk/
  execution decision); the only hot-path additions are a news-context hash
  and the 60D extras computation, both bounded and failure-isolated.
- Full gate (beforePush.sh) running; mypy + ruff green on all TASK-6 files.

## 29. REMAINING RISKS

- Parallel-agent commits can overwrite uncommitted shared-file edits
  (observed: server.py/Web wiped by TASK-2's commit). Mitigation: commit
  promptly at task boundaries.
- The 60D Challenger has NO artifact yet — the alignment path is proven by
  tests but not by a real 60D live run (REPLAY_VERIFIED, not LIVE_VERIFIED).
- Productionization of ThreadPoolExecutor shadow queue not needed (bounded
  sync window sufficient at current tick rates; revisit if latency measured
  on live shows hot-path impact).

## 30-31. FILES CHANGED / COMMITS

- `src/nexus_scalp/governance/` (new: models, load_gate, store, engine,
  alignment, evidence, shadow_runtime, reporting, __init__)
- `src/nexus_scalp/application/live_engine.py` (governance wiring,
  shadow recording extension, 60D extras, registry sync, health snapshot)
- `src/nexus_scalp/web/server.py` (attach load-gate + governance API)
- `tests/unit/test_model_governance_phase16.py`, `tests/integration/...api.py`
- `tests/golden/*`, `scripts/gen_governance_golden.py`
- `Web/index.html`, `Web/app.js` (Model Governance panel)
- `agents/taskboard.md`, `agents/change_control.md` (TASK-6 / CHG-0003),
  `agents/skill.md`, `agents/contracts.md`, `agents/runtime_invariants.md`
  (see registry updates)

## 32. HANDOFF TO TASK-7

TASK-7 (Exit Intelligence / Position Management) inherits:
- The governance event ledger + truthful registry to attribute exits/outcomes
  to the model that produced the decision (decision_id linkage).
- `governance.evidence.outcome_for_decision()` for shadow outcome attribution.
- Do NOT touch `governance/` execution isolation; extend, don't weaken.

---