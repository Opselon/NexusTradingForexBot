# TASK-012 — Online Fine-Tuning Safe Sandbox & Guardrail Hardening

## Priority
P3

## Status
PENDING

## Objective
Design and harden a safe sandbox for online fine-tuning with strict rollback journals and degradation circuit breakers.

## Why This Task Exists
Online fine-tuning is currently disabled by default. If enabled carelessly, noisy live paper labels can corrupt Champion weights.

## Current Evidence
src/nexus_scalp/application/live/bar_handler.py:311; src/nexus_scalp/training/walk_forward_trainer.py:1041; PersistDecision gating.

## Scope
Enforce shadow-quarantine for fine-tuned weights: any fine-tuned model must pass 100 shadow evaluation ticks before being swapped into live serving.

## Explicit Non-Goals
Do not enable online fine-tuning in default live configuration without explicit operator opt-in.

## Dependencies
TASK-004, TASK-008

## Source Areas
src/nexus_scalp/application/live_engine.py, src/nexus_scalp/model_lifecycle/persist_decision.py

## Files Likely Involved
src/nexus_scalp/application/live_engine.py, src/nexus_scalp/model_lifecycle/persist_decision.py

## Investigation Required
Evaluate gradient explosion risk during micro-batch live updates.

## Implementation Outline
Implement Shadow-Quarantine buffer for online fine-tuned candidate models.

## Tests Required
tests/unit/test_online_finetune_sandbox.py

## Benchmark Requirements
Run critical suite to confirm zero regressions.

## Evidence Required
Exact execution trace and test output proving acceptance criteria are met.

## Acceptance Criteria
Corrupted or degrading fine-tune candidate is rejected and rolled back without impacting active Champion.

## Human Stop Conditions
HUMAN DECISION REQUIRED: Operator authorization required to enable online learning in paper/demo mode.

## Expected Output
Tested, verified PR with passing tests and updated task status.
