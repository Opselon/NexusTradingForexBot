# ML-ARCH-001 — ScalpNet Dual-Path Tensor Contract & 3-Class Head Sunset

STREAM: STREAM D — MODEL ARCHITECTURE
PRIORITY: P0
STATUS: HUMAN DECISION REQUIRED
DEPENDENCIES: None
AGENT_ROLE: AGENT-ML-ARCH
OWNERSHIP_SCOPE: src/nexus_scalp/models/scalp_net.py, model_class_contract.py
HUMAN_DECISION_REQUIRED: YES (Operator must approve 4-logit WAIT sunset schedule)
PARALLELIZATION_CLASS: REQUIRES_DECISION

## OBJECTIVE
Enforce the canonical 3-class tensor contract (0: NO_TRADE, 1: BUY, 2: SELL) across ScalpNet and establish the formal sunset policy for legacy 4-logit WAIT masking.

## WHY_IT_EXISTS
ScalpNet currently maintains a legacy 4-logit output head where the 4th logit is masked to -1e4 before softmax. Maintaining this dead logit introduces dead weight parameters, calibration complexity, and architectural ambiguity.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/model_lifecycle/model_class_contract.py` (Lines: `50-127`)
  - **Symbol:** `TRAINED_CLASS_COUNT, mask_wait_logit`
  - **Behavior:** TRAINED_CLASS_COUNT = 3; mask_wait_logit sets index 3 = -1e4
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** Contract declares 3 classes but legacy models have 4 logits
- **Path:** `src/nexus_scalp/models/scalp_net.py` (Lines: `120-145`)
  - **Symbol:** `ScalpNet.__init__`
  - **Behavior:** nn.Linear(hidden_dim, num_classes) accepts num_classes parameter (defaults to 3)
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/application/live/inference.py` (Lines: `150-185`)
  - **Symbol:** `InferenceService._predict_probabilities`
  - **Behavior:** Applies mask_wait_logit before masked_softmax if output dimension == 4
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** Live engine must maintain dual branch

## FACTS
- 3 classes are trained.
- 4th logit is dead and masked.
- ScalpNet supports num_classes=3.

## UNKNOWNs
- Whether any production client still depends on 4-wide checkpoint loading.

### RESOLVED (AGENT-ML-ARCH evidence run 2026-09-22, `eb73440a`)
**Answer: no in-repo dependency exists.** Evidence (all live, reproducible via
`python3 scripts/audit/audit_legacy4_surface.py --repo .`):

- **Zero `.pt` checkpoints committed to git** (`git ls-tree -r HEAD` → 0 `.pt`
  paths); `find artifacts -name '*.pt'` → 0; `~/.nexusscalpengine/models/` empty.
  Clean-checkout training is impossible by design, so no production client can
  depend on a 4-wide checkpoint held in this repository.
- **No `num_classes=4` literal on the production serving path.** The probe
  classifies every 4-wide literal by subsystem: `PRODUCTION-SERVING = 0`. The
  only two survivors are `src/nexus_scalp/smoke/runner.py:731` (paper-smoke
  synthetic forward) and `src/nexus_scalp/web/model_governance_routes.py:515`
  (shadow-70 challenger contract; `num_classes: int = Field(default=4)` at
  `shadow70/models.py:131`). Neither is a money path.
- **The serving head resolver is already 3-class-first.**
  `live_engine.py:2448-2465` returns the meta-declared head only when it is `3`
  or `4`, else falls back to `TRAINED_CLASS_COUNT` (3). With a 3-declaring meta,
  `mask_wait_logit` is a no-op (`model_class_contract.py:114`) — so the per-tick
  mask is **dead work for every bundle that can actually be served today**.
- **Option A blast radius: 66 `num_classes=4` occurrences across 29 test files.**
  Most are deliberate negative probes (e.g. `test_bug243_bundle_class_head_mint.py`,
  `test_promotion_rejects_degenerate_model.py`) that *prove* gates reject
  incoherent bundles; removing the 4-wide constructor deletes that coverage.

**Recommendation: Option B (strict sunset + legacy adapter).** Removing the 4th
logit buys no safety when zero 4-wide artifacts exist, and it destroys the
negative-test coverage that currently proves serving safety. Full reasoning in
`agents/decisions/DEC-0010-scalpnet-3class-head-sunset.md`.

## SCOPE
Present formal sunset options to operator: Option A (Immediate removal) vs Option B (Strict sunset with legacy adapter). Upon decision, implement clean 3-class enforcement for all new candidate models.

## NON_GOALS
Do not delete 4-logit support before operator approval.

## SOURCE_AREAS
- `src/nexus_scalp/model_lifecycle/model_class_contract.py`
- `src/nexus_scalp/models/scalp_net.py`
- `src/nexus_scalp/application/live/inference.py`

## FILES_LIKELY_TO_CHANGE
- `src/nexus_scalp/model_lifecycle/model_class_contract.py`
- `tests/unit/test_model_class_contract.py`

## INVESTIGATION_PLAN
Audit artifacts/ directory to confirm whether any active production model bundle requires 4 logits.

## IMPLEMENTATION_PLAN
1. STOP for Human Decision: Operator chooses sunset schedule (Option A vs Option B).
2. Enforce num_classes = 3 for all new model instantiations in ModelFactory.
3. If Option B: Wrap legacy 4-logit models in an adapter shim that emits a DeprecationWarning.
4. Add unit test asserting all fresh models emit exactly (B, 3) logits without masking overhead.

## TEST_PLAN
- `pytest tests/unit/test_model_class_contract.py -v`

## BENCHMARK_PLAN
Measure forward pass latency with 3 logits vs 4 logits + mask_wait_logit.

## EVIDENCE_REQUIRED
- Recorded operator decision
- Pytest output verifying 3-class tensor shape contract

## ACCEPTANCE_CRITERIA
1. Operator decision recorded.
2. All newly built ScalpNet checkpoints output exactly (B, 3) logits.
3. Legacy checkpoints function via backwards-compatibility adapter.

### EVIDENCE_STATUS (2026-09-22)
- [x] **AC-1 (evidence delivered, awaiting operator selection):** the decision
  record `agents/decisions/DEC-0010-scalpnet-3class-head-sunset.md` presents
  Options A/B/C with a recommendation (B) and the measured blast radius. The
  operator has not yet selected — the task's own `NON_GOALS` forbid executing a
  sunset without that selection, so this criterion is *serviced*, not closed.
- [x] **AC-2 (already true at HEAD):** fresh mints default to
  `TRAINED_CLASS_COUNT = 3` (`live_engine.py:2463-2465`, `scalp_net.py:127-132`),
  and `integrity.py:37` makes `EXPECTED_NUM_CLASSES = 3` the default for all
  artifact probes. Verified statically by the probe's
  `trained_class_count_src = 3`.
- [ ] **AC-3 (pending operator decision):** the backwards-compat adapter shim
  with `DeprecationWarning` is the Option B implementation surface
  (DEC-0010 §3.2). Not implemented — `HUMAN_DECISION_REQUIRED: YES`.
- [x] **INVESTIGATION_PLAN complete:** the artifact audit demanded by this task
  is done and *automated* — `scripts/audit/audit_legacy4_surface.py` is a
  torch-free reproducible probe (12 tests in
  `tests/unit/test_audit_legacy4_surface.py`, pinned in the critical suite).

## ABORT_CONDITIONS
If operator rejects sunset, retain current masking logic.

## HUMAN_DECISION_REQUIRED
YES (Operator must approve 4-logit WAIT sunset schedule)

## EXPECTED_ARTIFACTS
- `tests/unit/test_model_class_contract.py`
- `docs/decisions/DEC-003_3CLASS_CONTRACT.md`

## SHARED_FILE_RISK
Medium. Shared with AGENT-INFERENCE.
