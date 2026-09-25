# ML-OBS-002 — Online Fine-Tuning Safe Sandbox, Quarantine Buffer & Circuit Breakers

STREAM: STREAM L — OBSERVABILITY
PRIORITY: P3
STATUS: HUMAN DECISION REQUIRED
DEPENDENCIES: ML-GOV-001, ML-GOV-003
AGENT_ROLE: AGENT-REDTEAM
OWNERSHIP_SCOPE: src/nexus_scalp/application/live_engine.py, persist_decision.py
HUMAN_DECISION_REQUIRED: YES (Operator must authorize online fine-tuning in demo mode)
PARALLELIZATION_CLASS: REQUIRES_DECISION

## OBJECTIVE
Design and implement a hardened shadow-quarantine buffer and degradation circuit breaker for online fine-tuning, ensuring live neural network weights cannot be updated without passing strict safety validation.

## WHY_IT_EXISTS
Online fine-tuning code exists in WalkForwardTrainer.fine_tune_online and is wired to bar_handler.py, but is disabled by default. If enabled carelessly, noisy live market labels can corrupt neural network weights, destroy calibration, and cause sudden account drawdowns.

## CURRENT_EVIDENCE
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

## FACTS
- Online fine-tune function exists.
- Disabled by default in configuration.
- PersistDecision gates weight persistence.

## UNKNOWNs
- Catastrophic forgetting rate after 50 online gradient updates on volatile intraday swings.

## SCOPE
Formulate safety framework: Fine-tuned candidate model must be placed in a Shadow-Quarantine buffer for 100 evaluation bars; circuit breaker: discard candidate if validation loss increases by > 10%; STOP for operator decision.

## NON_GOALS
Do not enable online fine-tuning in production live trading accounts.

## SOURCE_AREAS
- `src/nexus_scalp/application/live/bar_handler.py`
- `src/nexus_scalp/training/walk_forward_trainer.py`

## FILES_LIKELY_TO_CHANGE
- `src/nexus_scalp/model_lifecycle/persist_decision.py`
- `tests/unit/test_online_finetune_sandbox.py`
- `docs/safety/ONLINE_FINETUNE_GUARDRAILS.md`

## INVESTIGATION_PLAN
Analyze learning rate and gradient clipping norms in fine_tune_online to assess weight drift risk.

## IMPLEMENTATION_PLAN
1. Design Shadow-Quarantine evaluation hook in LiveEngine.
2. Implement loss degradation and calibration circuit breakers.
3. Write tests/unit/test_online_finetune_sandbox.py.
4. Publish safety report docs/safety/ONLINE_FINETUNE_GUARDRAILS.md.
5. STOP for Human Decision: Operator decides whether to authorize demo mode testing.

## TEST_PLAN
- `pytest tests/unit/test_online_finetune_sandbox.py -v`

## BENCHMARK_PLAN
Simulate 50 fine-tune micro-batches; assert degraded candidate is rejected in 100% of simulated attacks.

## EVIDENCE_REQUIRED
- Safety report: docs/safety/ONLINE_FINETUNE_GUARDRAILS.md
- Passing unit test log
- Recorded human decision

## ACCEPTANCE_CRITERIA
1. Degraded fine-tuned candidates are safely rejected without affecting active Champion.
2. Explicit operator approval recorded before any sandbox activation.

## ABORT_CONDITIONS
If fine-tuned model cannot be rolled back atomically upon degradation, abort.

## HUMAN_DECISION_REQUIRED
YES (Operator must authorize online fine-tuning in demo mode)

## EXPECTED_ARTIFACTS
- `docs/safety/ONLINE_FINETUNE_GUARDRAILS.md`
- `tests/unit/test_online_finetune_sandbox.py`

## SHARED_FILE_RISK
Medium. Touches live_engine.py (HOT PATH - requires lock).
