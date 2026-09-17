# TASK-003 — Strict 3-Class Contract Enforcement & Legacy 4-Logit Deprecation

## Priority
P0

## Status
PENDING

## Objective
Deprecate legacy 4-logit WAIT class and enforce canonical 3-class contract (NO_TRADE, BUY, SELL) across all training and inference entry points.

## Why This Task Exists
The 4th logit is contractually dead and masked to -1e4. Maintaining dead heads introduces architectural confusion and calibration complexity.

## Current Evidence
src/nexus_scalp/model_lifecycle/model_class_contract.py:50-127; mask_wait_logit() masks index 3.

## Scope
Enforce TRAINED_CLASS_COUNT=3 across all trainers; add deprecation warnings when loading legacy 4-logit checkpoints.

## Explicit Non-Goals
Do not break compatibility with existing 4-logit legacy checkpoints in model_bundle_store.

## Dependencies
None

## Source Areas
src/nexus_scalp/model_lifecycle/model_class_contract.py, src/nexus_scalp/models/scalp_net.py, src/nexus_scalp/training/walk_forward_trainer.py

## Files Likely Involved
src/nexus_scalp/model_lifecycle/model_class_contract.py, src/nexus_scalp/models/scalp_net.py, src/nexus_scalp/training/walk_forward_trainer.py

## Investigation Required
Audit all active checkpoints in artifacts/ to confirm whether any production bundle requires 4 logits.

## Implementation Outline
Ensure all new model builds default to num_classes=3 with zero WAIT masking overhead.

## Tests Required
tests/unit/test_model_class_contract_3class.py

## Benchmark Requirements
Run critical suite to confirm zero regressions.

## Evidence Required
Exact execution trace and test output proving acceptance criteria are met.

## Acceptance Criteria
All new models train and serve with exactly 3 output logits; legacy checkpoints continue to function with mask.

## Human Stop Conditions
HUMAN DECISION REQUIRED: Confirm sunset schedule for legacy 4-logit checkpoint support.

## Expected Output
Tested, verified PR with passing tests and updated task status.
