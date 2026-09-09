# P0-3 HANDOFF — RESEARCH GATE INTEGRITY (selection bias, reuse contamination, no multiple-testing control) (deep audit Section K)

**Date:** 2026-09-09 | **Priority:** P0 (correctness/profitability blocker) | **Status:** TODO (dispatch-ready, unclaimed)
**TASK-ID to claim:** `TASK-P0-3-GATE-INTEGRITY`
**Suggested agent role:** Nexus-Research (research-lane coder)
**Source of truth:** `artifacts/audits/research_strategy_factory_deep_audit.txt` §K P0-3 (lines 826-838)
**Sub-report evidence:** `/tmp/audit_sub/research.txt` §4/§5 (ephemeral path; findings quoted inline)

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

Candidate validation is selection-biased and reuse-contaminated: discovery selects
context families over the FULL sample (including the windows later used as validation),
OOS passes at break-even with 100% degradation allowed, the OOS tail is reused across
candidates with no multiple-testing control, and robustness latency scenarios are no-ops.
Without this fix, the P0-1 bridge would feed self-deceiving evidence to production.

## Evidence (verified at HEAD 76a3da01)

- `src/nexus_scalp/research/discovery.py:105-115`: family expectancy computed over the
  WHOLE dataset — no `as_of` restriction. `pipeline.discover` passes the full dataset
  (`src/nexus_scalp/research/pipeline.py:171-182`). The same trades that selected a
  candidate are re-used as its validation/OOS evidence — selection leakage purge/embargo
  cannot fix. `dataset.build_for_strategy(as_of=...)` EXISTS (`dataset.py:648-673`,
  leakage-guarded) but the worker/pipeline discovery path does not use it.
- `src/nexus_scalp/research/oos.py:31-33`: break-even OOS floor (expectancy >= 0) and
  degradation ceiling allowing 100% degradation. `walkforward.py:137-162` re-validates a
  FIXED filter (never re-fit) — WF "unseen" is false at discovery time (P1-1 documents
  the training half; this item owns the discovery/OOS threshold half).
- `src/nexus_scalp/research/metrics.py:230`: latency sensitivity is a pure formula that
  does not touch fills — robustness "latency scenarios" change nothing (no-op evidence).
- No multiple-testing control anywhere in the gate chain (no hypothesis ledger).

## Required capability (build this)

1. **`src/nexus_scalp/research/discovery.py`**: restrict discovery to `as_of` (walk-forward
   discovery) — selection uses only decisions < as_of; wire the existing
   `build_for_strategy(as_of=...)` into the worker/pipeline discovery path.
2. **`src/nexus_scalp/research/oos.py`**: OOS min expectancy > breakeven-WITH-COSTS floor
   (not 0R; use the canonical friction assumptions) + degradation cap < 1.0.
3. **`src/nexus_scalp/research/robustness.py`**: make drop/replace latency scenarios real
   (latency must actually cost P&L in the scenario, or the scenario must fail-closed as
   NOT_SIMULABLE — a silent no-op is forbidden).
4. **`src/nexus_scalp/research/testing_corrections.py` (NEW)**: per-cycle hypothesis ledger
   (every candidate evaluated increments the family/cycle count) + deflated/Bonferroni
   adjustment of gate thresholds by hypothesis count.

## Affected files

- `src/nexus_scalp/research/discovery.py`, `src/nexus_scalp/research/oos.py`,
  `src/nexus_scalp/research/robustness.py`, NEW `src/nexus_scalp/research/testing_corrections.py`,
  `src/nexus_scalp/research/pipeline.py` (discovery path wiring only) + regression tests.

## Coordination locks

- BUG-183 already wired purge/embargo defaults into pipeline/OOS/WF — do not regress those surfaces; extend, don't re-derive splits (reuse `split_temporal` + `OOSGate`, never re-implement; see 2026-09-05_agent17_oos_gate.md "EXACT NEXT-AGENT INSTRUCTIONS").
- E1 zero-friction guard (80f17ef2) means every engine refuses zero-cost assumptions — build the breakeven-with-costs floor on the canonical artifact, not new magic numbers.
- These are research-lane files with NO live-path overlap: no INV-001/004 concerns, but research-family suites must stay green.

## Why / benefit / difficulty / risk

- Why: gates become trustworthy — precondition for P0-1 (bridge feeds only honest evidence).
- Benefit: the gate chain stops passing self-deceiving candidates.
- Difficulty: MEDIUM. Risk: stricter gates = fewer candidates pass — that is CORRECT behavior; do not soften thresholds to "restore throughput."

## Acceptance criteria

1. Discovery cannot see decisions >= as_of (leakage test red-before/green-after).
2. OOS floor > 0R after canonical costs; degradation cap < 1.0 enforced with a clear rejection reason.
3. Latency scenarios either change P&L or fail loud (NOT_SIMULABLE); silent no-op impossible (test pins it).
4. Hypothesis ledger counts per cycle; adjusted thresholds demonstrably tighten as count grows (test).
5. Regression tests green; ruff/mypy clean; taskboard + closing handoff written.
