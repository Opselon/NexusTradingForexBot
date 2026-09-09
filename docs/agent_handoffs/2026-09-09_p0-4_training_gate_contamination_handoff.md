# P0-4 HANDOFF — TRAINING-GATE CONTAMINATION (benchmark "OOS" includes train rows; CHALLENGER granted on self-asserted string) (deep audit Section K)

**Date:** 2026-09-09 | **Priority:** P0 (correctness/profitability blocker) | **Status:** TODO (dispatch-ready, unclaimed)
**TASK-ID to claim:** `TASK-P0-4-TRAINING-GATE-CONTAMINATION`
**Suggested agent role:** Nexus-ModelLifecycle (training-lane coder)
**Source of truth:** `artifacts/audits/research_strategy_factory_deep_audit.txt` §K P0-4 (lines 840-847)
**Sub-report evidence:** `/tmp/audit_sub/training.txt` (ephemeral path; findings quoted inline)

**Repo state at dispatch:** branch `main`, HEAD `76a3da01`, ahead of origin by 18 commits; working tree is DIRTY with FOREIGN agents' WIP (live_engine.py, tick_pipeline.py, policy.py, config.py, runtime_config.py, ci.yml, research/historical/, untracked strays `-` and `file::memory:?cache=shared`). Never stage, revert, or clean foreign/untracked work. Stage only your own files, run `git diff --cached --name-only` IMMEDIATELY before `git commit` (never `git add .`), per `agents/multi-agent-git-contract.md` §1/§18 and taskboard hazard rows 792d1a38/130ab14f.

## Mandatory bootstrap (before any code change)

1. `git status --short` / `git branch --show-current` / `git log -10 --oneline` / `git diff` / `git diff --cached`.
2. Read: `agents/multi-agent-git-contract.md`, `agents/skill.md`, `agents/bugs.md`, `agents/contracts.md`, `agents/runtime_invariants.md`, `agents/change_control.md`, `agents/taskboard.md`, `agents/repository_state.md`.
3. Claim this item's TASK-ID on `agents/taskboard.md` (append-only row) BEFORE starting; check `agents/locks.yaml` for ownership conflicts.
4. This is a multi-agent live tree: re-verify every file:line citation below at read time — concurrent commits may have shifted lines.

## Deliverables & discipline (every P0 agent)

- Fails-before probe + regression test per defect fixed (contract §6/§7/§22); tests travel with the fix (§21).
- Quality gates §55: focused suite -> subsystem suite -> ruff -> format -> mypy on touched files; engine-touching work additionally runs the engine-launch gate (`tests/unit/test_engine_runtime_launch.py` or `nse smoke --runtime`).
- Commit contract §18: `<AGENT>: <summary>` + structured body. NO FAKE GREEN (§10); honest-status labels TESTED/OBSERVED/INFERRED/NOT TESTED (§56).
- On completion: append your own completion row to `agents/taskboard.md` and write your closing handoff to `docs/agent_handoffs/YYYY-MM-DD_<agent>_<task>.md` (§37-38, §52).

## Problem (one paragraph)

The model-generation track's largest integrity defect: the benchmark "OOS" evaluation
feeds the ENTIRE dataset frame (train+val+test rows) into the validation gates, so
CHALLENGER_ELIGIBLE rests on inflated metrics — and the three-model lane grants
CHALLENGER status from a self-reported string instead of reading gate artifacts.
Any challenger eligibility number is meaningless until this is fixed.

## Evidence (verified at HEAD 76a3da01)

- `src/nexus_scalp/model_generation/benchmark.py:214-218`: passes the ENTIRE dataset
  frame (all splits) to `ValidationFactory.validate`; `validation.py` then computes
  oos_accuracy / macro_f1 / balanced-accuracy gates (validation.py:254-283) over ALL
  rows — including rows the candidate trained on (three_model trains on train+val =
  ~85% of the frame). The verdict CHALLENGER_ELIGIBLE (validation.py:282-283) rests on
  inflated metrics. Contrast: the sequence-path prediction-alignment guard (AGENT-16,
  benchmark.py:219-238) refuses to fabricate metrics on row mismatch — extend that
  honesty, do not weaken it.
- Test block reuse: the same "test" block is re-used across repeated candidate
  selection runs (benchmark.py:186-259) — repeated selection on the same block.
- `src/nexus_scalp/model_generation/three_model.py:282-333`: for 70D variants the
  "benchmark" evidence is literally the string `PASS (purged walk-forward completed)`
  (three_model.py:282-292) and `set_status(CHALLENGER)` proceeds on that self-report
  (audit finding L4). Gates exist and are real (`ValidationFactory` floors: macro-F1
  > 0.34, bacc > 0.34, ECE <= 0.15, N >= 100 — verified 2026-09-05 Agent-17) — the
  defect is what data reaches them and what evidence grants the status.

## Required capability (build this)

1. **`src/nexus_scalp/model_generation/benchmark.py`**: validate ONLY on rows with
   `_split in {val, test}`; the test block is used ONCE per candidate (single-shot;
   record its consumption — a second use must fail-closed or be ledger-flagged).
2. **`src/nexus_scalp/model_generation/validation.py`**: gates must refuse to evaluate
   on rows outside {val, test} (fail-closed, not silent widening — BUG-245 precedent:
   empty contract => FAIL, never widen the population).
3. **`src/nexus_scalp/model_generation/three_model.py`**: read the gate ARTIFACTS
   (persisted validation result: metrics, sample counts, split ids) instead of
   self-reporting; CHALLENGER is granted only on artifact evidence, never a string.

## Affected files

- `src/nexus_scalp/model_generation/benchmark.py`, `src/nexus_scalp/model_generation/validation.py`,
  `src/nexus_scalp/model_generation/three_model.py` + regression tests.

## Coordination locks

- These files are model-generation lane (not the live hot path) — no INV-001/004 concerns. The wave-2/3 agents (C4/A4/EXIT) own policy.py / audit_repository / order_manager — no overlap.
- `validation.py` has a `force=True` bypass switch (validation.py:233, 242-245, 263-282 — no src caller passes it today): do NOT remove it, but your regression tests must pin that the split-scoping fix is not reachable via force.
- Changes may retire current candidates — that is CORRECT behavior (audit's own risk note); do not tune to preserve them. Flag any production_eligible artifact invalidated (candidate_default/meta already carries production_eligible=False, label_origin=UNKNOWN — see Appendix R line 998-999).

## Why / benefit / difficulty / risk

- Why: any challenger eligibility number becomes meaningful; removes the repo's largest integrity defect in the ML track.
- Difficulty: LOW. Risk: changes may retire current candidates (correct).

## Acceptance criteria

1. Fails-before probe: current benchmark on a val/test-overlapping dataset reports CHALLENGER_ELIGIBLE with inflated metrics; after fix it FAILs or reports honest val/test-only metrics (test shows both).
2. Test block consumed once per candidate; reuse attempt fails-closed or is ledger-flagged (test).
3. CHALLENGER requires gate artifact evidence; string-only self-report cannot set CHALLENGER (test: tamper the string, status unchanged).
4. Model-lane suites (incl. tests/unit/test_70d_model_validation_task4.py where runnable) stay green; ruff/mypy clean on touched files.
5. Taskboard row + closing handoff written.
