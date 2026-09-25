# P0-5 HANDOFF — ECONOMIC DATA FOUNDATION (OPERATIONS task, not a code task) (deep audit Section K)

**Date:** 2026-09-09 | **Priority:** P0 (correctness/profitability blocker) | **Status:** TODO (dispatch-ready, unclaimed)
**TASK-ID to claim:** `TASK-P0-5-ECONOMIC-DATA-FOUNDATION`
**Suggested agent role:** OPERATIONS (run engine on DEMO); code-side calibration only where listed
**Source of truth:** `artifacts/audits/research_strategy_factory_deep_audit.txt` §K P0-5 (lines 849-862); §L line 966 ("feed it real reconciled outcomes (P0-5)")
**Sub-report evidence:** `/tmp/audit_sub/research.txt` §2 (friction conventions); Appendix R (artifact state)

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

The economic data foundation is missing: the ledger is empty/zero-R on this dev box,
fills are paper-only, three unreconciled friction conventions co-exist, and no live
slippage sample exists. Every gate and every P0 above consumes this data — without it
nothing can validate. This is a ZERO-CODE-MOSTLY operations task: run the engine on a
DEMO account with live prices and real fills long enough to accumulate executed+closed
outcomes, then reconcile frictions and re-calibrate.

## Evidence (verified 2026-09-09)

- `audit.db` on this dev box: `audit_experience_outcomes` = 0 rows (queried; wave-3
  taskboard row TASK-EXIT-LEDGER-EXPORT is BLOCKED-ON-OPERATOR on the same fact — the
  158-trade paper ledger lives only on the production Windows host).
- Prior paper record is NEGATIVE and paper-only: PF 0.381, avg R -0.082, 158 executed
  closed trades (`docs/agent_handoffs/2026-09-07_final_production_readiness_audit.md` §5).
- Three unreconciled friction conventions co-exist: labeling friction 0.35R
  (`src/nexus_scalp/labeling/triple_barrier.py:40`, also `live_engine.py:1395`),
  baseline_eval FRICTION_R=0.15 (`src/nexus_scalp/research/baseline_eval.py:52`),
  backtest tick model (pipeline artifact = 15+5 ticks vs economics.production_like
  = 20+2.2 ticks — same order of magnitude, but not one number).
- `configs/execution_assumptions.json` limitations block (its own words): calibration
  window has no NY-session bars; slippage evidence is PAPER-only ("no LIVE slippage
  sample exists yet"); commission/swap are 0 on this spread-only paper account.

## Required capability (DO this)

1. **Run the engine on DEMO** (MT5 demo account, live prices + real fills) long enough
   to accumulate executed+closed outcomes with nonzero R. Engine-launch discipline:
   `nse smoke --runtime` / `tests/unit/test_engine_runtime_launch.py` green before any
   production-config run; runtime safety surfaces (persisted HALT/kill, `nexus risk status/release`)
   live-tested per the runtime-safety mission row.
2. **Reconcile friction end-to-end**: pick ONE friction convention (canonical, from the
   demo fills) and unify across `src/nexus_scalp/research/economics.py`,
   `src/nexus_scalp/research/baseline_eval.py`, and the labeling path
   (`src/nexus_scalp/labeling/triple_barrier.py`). The conventions above are the three to reconcile; document the chosen constant in `configs/execution_assumptions.json`.
3. **Re-calibrate `configs/execution_assumptions.json`** with live/demo fills (spread
   curve per session incl. NY; live slippage sample replaces the paper p95; commission
   and swap from the demo account terms). Keep the limitations block honest — replace
   "no LIVE slippage sample exists yet" only with a real measured sample.
4. **Ledger transfer (operator):** the prod-host 158-trade export (see TASK-EXIT-LEDGER-EXPORT
   row) should be provided to the replay/attribution agents (P0-2) — separate lane, same data foundation.

## Affected files (code-side only where listed)

- OPERATIONAL: no repo change for the demo run itself.
- `configs/execution_assumptions.json` (re-calibration; bump CAL-ID, e.g. CAL-2026-09-XX-B, keep old file versioned)
- `src/nexus_scalp/research/economics.py` + `src/nexus_scalp/research/baseline_eval.py` + labeling friction constant (single-convention unification)
- DB protocol §27: demo fills write to the standard audit.db — no schema changes needed; do not delete/trim prod data.

## Coordination locks

- Blocks P0-1/2/3 effectiveness (they need ledger volume with nonzero R). P0-4 and P0-6 are NOT blocked by this and can run in parallel.
- `live_engine.py` / `order_manager.py` are hot-path convention-locked (INV-001/004) — an ops run modifies CONFIG, not code; any defect discovered mid-run is documented and routed to owners, not hot-fixed by the ops agent.
- Honest-status rule: report fills as DEMO, never as LIVE; never fabricate or synthesize fills to unblock P0-2/P0-3 consumers.

## Why / benefit / difficulty / risk

- Why: every gate and every P0 above consumes this data; without it nothing can validate.
- Benefit: zero code, all operation + calibration; unblocks the evidence layer.
- Difficulty/risk: live/demo fills differ from paper — that difference is exactly the measurement to capture.

## Acceptance criteria

1. Demo run accumulates executed+closed outcomes with nonzero R (count reported honestly; 0 = run failed).
2. One friction convention end-to-end (the three constants above reconciled; grep-provable single source).
3. `configs/execution_assumptions.json` re-calibrated from demo fills with a bumped CAL-ID and an honest limitations block.
4. Per-session spread curve documented (incl. NY session — the current artifact's blind spot).
5. Taskboard row updated with run stats + closing handoff written.
