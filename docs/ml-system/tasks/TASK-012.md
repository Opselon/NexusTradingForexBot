# TASK-012 — Online Fine-Tuning Safe Sandbox, Quarantine Buffer & Circuit Breakers

Priority: P3
Status: HUMAN DECISION REQUIRED
Type: Runtime Safety & Adaptation
Dependencies: TASK-004, TASK-008
Blocks: None
Human Decision Required: YES (Authorize online fine-tuning in demo/paper execution mode)
Risk: High (Model weight corruption, catastrophic forgetting, gradient instability)
Estimated Scope: Quarantine buffer, circuit breaker checks, rollback hook (~200 LOC)

## Objective
Design and implement a hardened shadow-quarantine buffer and degradation circuit breaker for online fine-tuning, ensuring live neural network weights cannot be updated without passing strict safety validation.

## Problem / Why
Online fine-tuning code exists in WalkForwardTrainer.fine_tune_online and is wired to bar_handler.py, but is disabled by default. If enabled carelessly, noisy live market labels can corrupt neural network weights, destroy calibration, and cause sudden account drawdowns.

## Current Evidence
- **Path:** `src/nexus_scalp/application/live/bar_handler.py` (Lines: `311`)
  - **Symbol:** `LiveBarHandler._handle_online_finetune`
  - **Behavior:** Disabled by default: if not self.om._online_finetune_enabled: return
  - **Classification:** `PRODUCTION GATING`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/training/walk_forward_trainer.py` (Lines: `1041-1120`)
  - **Symbol:** `WalkForwardTrainer.fine_tune_online`
  - **Behavior:** Runs micro-batch SGD updates; returns fine-tuned weights
  - **Classification:** `CAPABILITY`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/model_lifecycle/persist_decision.py` (Lines: `45-90`)
  - **Symbol:** `PersistDecision`
  - **Behavior:** Evaluates candidate metrics before allowing persistence
  - **Classification:** `SAFETY GATE`
  - **Confidence:** 100%
  - **Contradiction:** None

## Scope
1. Formulate safety framework: Fine-tuned candidate model must be placed in a Shadow-Quarantine buffer for 100 evaluation bars.
2. Circuit breaker: If validation loss increases by > 10% or calibration error exceeds threshold, discard fine-tuned candidate and alert operator.
3. Keep default setting learning.online_finetune.enabled: False.
4. STOP for Human Decision: Operator authorizes or rejects sandbox deployment.

## Non-Goals
Do not enable online fine-tuning in production live accounts.

## Preconditions
TASK-004 and TASK-008 complete.

## Dependencies
TASK-004, TASK-008

## Blocks
None

## Source Areas
- `src/nexus_scalp/application/live/bar_handler.py:305-340`
- `src/nexus_scalp/training/walk_forward_trainer.py:1040-1130`
- `src/nexus_scalp/model_lifecycle/persist_decision.py:1-120`

## Investigation
Analyze learning rate and gradient clipping norms in fine_tune_online to assess catastrophic forgetting risks.

## Implementation Plan
1. Design Shadow-Quarantine evaluation hook in LiveEngine.
2. Implement loss degradation and calibration circuit breakers.
3. Write tests/unit/test_online_finetune_sandbox.py testing corrupted gradient rejection.
4. Compile safety report docs/safety/ONLINE_FINETUNE_GUARDRAILS.md.
5. STOP for Human Decision: Operator decides whether to authorize demo mode testing.

## Tests
- `pytest tests/unit/test_online_finetune_sandbox.py -v`

## Validation / Benchmark
Injected adverse data batches must trigger circuit breaker and reject weight updates in 100% of simulated attacks.

## Evidence Required
- Safety report docs/safety/ONLINE_FINETUNE_GUARDRAILS.md
- Test execution log showing circuit breaker rejection of degraded models
- Signed human operator authorization

## Acceptance Criteria
1. Degraded fine-tuned candidates are safely rejected without affecting active Champion.
2. Explicit operator approval recorded before any sandbox activation.

## Failure / Abort Conditions
If fine-tuned model cannot be rolled back atomically upon degradation, STOP and abort task.

## Human Stop Conditions
HUMAN STOP CONDITION: Operator must approve online fine-tuning authorization.

## Expected Output
Safe, quarantined online fine-tuning harness protected by degradation circuit breakers.
