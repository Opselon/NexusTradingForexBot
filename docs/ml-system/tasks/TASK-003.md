# TASK-003 — Strict 3-Class Contract Enforcement & Legacy 4-Logit WAIT Sunset

Priority: P0
Status: HUMAN DECISION REQUIRED
Type: Architecture & Model Contract
Dependencies: None
Blocks: TASK-009, TASK-015
Human Decision Required: YES (Confirm formal sunset schedule of legacy 4-logit WAIT mask)
Risk: High (Breaking change for legacy model bundle checkpoints if compatibility shims are removed)
Estimated Scope: Contract definition, deprecation policy document, adapter shims (~100 LOC)

## Objective
Formalize the 3-class model contract (0: NO_TRADE, 1: BUY_MARKET, 2: SELL_MARKET) across all trainers and model heads, establishing an explicit deprecation timeline for legacy 4-logit WAIT masking.

## Problem / Why
The 4th logit (WAIT at index 3) is contractually dead and suppressed with -10000.0 before softmax in live inference. Maintaining dual 3-class and 4-class code paths adds complexity, potential miscalibration, and confusion across documentation.

## Current Evidence
- **Path:** `src/nexus_scalp/model_lifecycle/model_class_contract.py` (Lines: `50-127`)
  - **Symbol:** `TRAINED_CLASS_COUNT, NUM_CLASSES_V1, mask_wait_logit`
  - **Behavior:** TRAINED_CLASS_COUNT=3, NUM_CLASSES_V1=4. mask_wait_logit sets logits[:, 3] = -1e4
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** Legacy heads have 4 logits; new training uses 3 classes
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
  - **Contradiction:** Live engine must support both 3-wide and 4-wide models

## Scope
1. Author formal decision document outlining deprecation options: Option A (Immediate removal of 4-logit support) vs Option B (Strict sunset: new models MUST be 3-class; legacy 4-logit wrapped in legacy adapter until next major version).
2. Audit all active checkpoints in artifacts/ to identify which models use 4 logits.
3. Upon Human Approval, implement explicit deprecation warning in InferenceService when loading 4-logit models.

## Non-Goals
Do not delete 4-logit inference support before human decision approval and without backwards compatibility shims.

## Preconditions
Human operator review and selection of sunset schedule.

## Dependencies
None

## Blocks
TASK-009 (2D vs 3D Sequence Routing), TASK-015 (ML Contract Drift Gate)

## Source Areas
- `src/nexus_scalp/model_lifecycle/model_class_contract.py:1-135`
- `src/nexus_scalp/models/scalp_net.py:115-190`
- `src/nexus_scalp/application/live/inference.py:140-195`

## Investigation
Determine if any official release model bundle currently in production uses 4 logits, or if all official candidates are already 3-class.

## Implementation Plan
1. STOP for Human Decision: Operator selects Option A (Immediate) or Option B (Sunset with adapter).
2. If Option B selected: Keep mask_wait_logit in InferenceService, but emit DeprecationWarning.
3. Enforce that CandidateTrainer, WalkForwardTrainer, and SequenceCandidateTrainer strictly build ScalpNet(num_classes=3).
4. Add unit test asserting that all newly initialized models have exactly 3 output units.
5. Update documentation to reflect sunset timeline.

## Tests
- `pytest tests/unit/test_model_class_contract.py -v`

## Validation / Benchmark
Assert that model forward pass on (1, 50) input produces exactly (1, 3) logits for all new candidates. Output probabilities sum to 1.0 across [P_NO_TRADE, P_BUY, P_SELL].

## Evidence Required
- Human decision record signed by operator
- Audit log of existing checkpoints and class head widths
- Unit tests asserting 3-class emission across all trainers

## Acceptance Criteria
1. Human decision on sunset schedule is recorded.
2. All trainers instantiate models with exactly 3 output classes.
3. InferenceService loads 3-class models directly without masking overhead.

## Failure / Abort Conditions
If human operator rejects sunsetting 4-logit WAIT, STOP and keep current masking mechanism.

## Human Stop Conditions
HUMAN STOP CONDITION: Operator must approve whether to retain or drop legacy 4-logit checkpoint support.

## Expected Output
Approved class contract decision document and updated class contract test suite.
