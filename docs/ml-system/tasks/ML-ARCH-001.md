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

## ABORT_CONDITIONS
If operator rejects sunset, retain current masking logic.

## HUMAN_DECISION_REQUIRED
YES (Operator must approve 4-logit WAIT sunset schedule)

## EXPECTED_ARTIFACTS
- `tests/unit/test_model_class_contract.py`
- `docs/decisions/DEC-003_3CLASS_CONTRACT.md`

## SHARED_FILE_RISK
Medium. Shared with AGENT-INFERENCE.
