# AGENT HANDOFF — BUG-228 Quality-gate misfire on zero-improvement fine-tunes

- Agent: Nexus-Main
- Role: Orchestrator / Training-Loop Reliability
- Date: 2026-09-03
- Branch: main
- Task: Eliminate the false red [QUALITY GATE REJECTION] + phantom "atomic revert" emitted whenever an online fine-tune early-stops without ever beating the baseline validation loss.

## Starting HEAD
4ef4ecaf (Nexus-Fleet-Orchestrator: BUG-227 mutation-gap census)

## Scope
- src/nexus_scalp/training/walk_forward_trainer.py — `fine_tune_online()` tail only + new static helper `_state_dicts_equal()`
- tests/unit/test_walk_forward_trainer.py — +1 regression test
- agents/bugs.md — +BUG-228
- agents/change_control.md — +CHG-0054

## Functions / Classes Changed
- walk_forward_trainer.py
   - WalkForwardTrainer.fine_tune_online() — inserted zero-improvement skip between gate evaluation and accept/rollback; replaced raw ANSI `print(f"[error] ...")` with structured `logger.error(..., reasons=...)`
   - WalkForwardTrainer._state_dicts_equal() — NEW exact-tensor-equality helper
- tests/unit/test_walk_forward_trainer.py
   - test_wf_zero_improvement_early_stop_skips_quality_gate_rejection() — NEW

## Root Cause (PROVEN)
Early stopping restores `best_state` = best validation-LOSS state seen. When no epoch beats the baseline val loss, `best_state` IS the baseline state, so the "candidate" evaluated by the quality gate is the unchanged production model: accuracy_delta = 0.0 by construction, the +3% gain check and effective-threshold check fire deterministically, and the engine logs a red rejection + "atomic revert" of identical weights. Live evidence 2026-09-03T13:04:02 (accepted=False, accuracy_delta=0.0, baseline 0.667 vs val 0.667). Secondary: the rejection banner used `print()` with raw ANSI escapes, bypassing the structured logger.

## Behavior Change
1. Zero-improvement runs (`early_stopping_triggered AND _state_dicts_equal(best_state, baseline_state)`): log INFO "Online fine-tune produced no improvement over baseline; keeping baseline weights", return baseline, write no checkpoint, run no gate.
2. Genuine gate rejections: banner now `logger.error` (structured, with reasons). Old raw ANSI print removed.
3. Gate thresholds/semantics and rollback for real candidates: UNCHANGED.

## Contracts / Invariants
- SHARED API UNCHANGED (no signature changes; new helper is private/static).
- INV-002 untouched (trainer places no orders). Baseline-preserving contract preserved.
- Not a 50D/70D contract change; schema binding untouched.

## Tests Added / Run
- NEW: test_wf_zero_improvement_early_stop_skips_quality_gate_rejection
  (epochs=3 + lr=1.0 divergence forces early-stop-without-improvement; asserts baseline weights unchanged, no checkpoint write, skip line present, no QUALITY GATE REJECTION, no raw ANSI escapes; red on pre-fix code).
- Full file: 6/6 green (repo venv).
- beforePush (ruff/format/mypy/CRITICAL suite): run gate before commit — see commit message for final numbers.

## Runtime Verification
- NOT LIVE-VERIFIED (trainer change; next natural retrain window in the running engine will exercise the INFO skip path; engine lifecycle owned by user — no restart performed).

## Bugs Fixed / Discovered
- Fixed: BUG-228.
- Discovered (not fixed here): fine-tune quality itself is buffer-bound — with ~90-120 effective rows a +3% gain is rarely achievable; the deeper model-quality repair path is the BUG-225 handoff (clean research-dataset retrain, not live buffer). Also: parallel working-tree changes for BUG-226 (audit provenance: live_engine.py, audit_repository.py, accounting/core.py) and order_manager.py belong to other agents — NOT included in the BUG-228 commit.

## Risks
- LOW. Failure mode changes from "false red alarm + no-op revert" to "honest INFO skip". Genuine rejections still reject and roll back identically.

## Unfinished Work / Next-Agent Instructions
1. If a red `[QUALITY GATE REJECTION]` appears after this fix, it is now a REAL rejected candidate — investigate buffer quality/labels, not the logger.
2. Model owner: follow BUG-225 RUNTIME REPAIR handoff for the champion artifact (clean-dataset retrain; live buffer is contaminated by paper fills).
3. Consider surfacing "no improvement" counts on a dashboard/tally so chronic no-improvement windows stay visible instead of disappearing into INFO logs.
