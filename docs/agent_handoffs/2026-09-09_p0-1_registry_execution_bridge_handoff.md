# P0-1 HANDOFF — REGISTRY -> EXECUTION BRIDGE (deep audit Section K)

**Date:** 2026-09-09 | **Priority:** P0 (correctness/profitability blocker) | **Status:** TODO (dispatch-ready, unclaimed)
**TASK-ID to claim:** `TASK-P0-1-REGISTRY-BRIDGE`
**Suggested agent role:** Nexus-Research-Execution (bridge coder; CROSS-OWNER change — research + execution surfaces)
**Source of truth:** `artifacts/audits/research_strategy_factory_deep_audit.txt` §K P0-1 (lines 798-812); §L verdict lines 959-970
**Sub-report evidence:** `/tmp/audit_sub/serving.txt` §4 (ephemeral path; key findings quoted inline below)

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

No registry->execution bridge exists: validated strategies can never trade. The live
tick path has ZERO consumers of `strategy_registry`; the research/factory side ranks
and validates candidates inside its own databases and no code path turns a VALIDATED
registry entry into a live-executable policy. "NEVER promotes to ACTIVE automatically"
is enforced only by the absence of any promotion consumer at all. This link is the
entire pipeline's purpose — without it, the "strategy factory" is a ranking report.

## Evidence (verified at HEAD 76a3da01)

- Serving-path audit §4: `StrategyRegistry` (`src/nexus_scalp/research/registry.py:71`)
  has readers/writers only in the factory orchestrator — NONE on the tick path
  (tick_pipeline -> SignalPolicy -> DecisionExecutor -> OrderManager never reads it).
- `src/nexus_scalp/research/lifecycle.py:111-120`: operator-gated promote to ACTIVE
  exists, but the ACTIVE status has no consumer downstream.
- Deep audit §A/§I: consumer grep proved zero tick-path consumers of strategy_registry
  (execution firewall — grep over strategies/+research/ finds zero order-authority call sites).

## Required capability (build this)

An operator-gated mechanism that maps a VALIDATED registry entry (context rule) onto a
live signal source — gate/block/enable the matching context, or instantiate the rule as
a policy channel — with:
1. **Kill-switch** (operator can disable any bridged strategy instantly).
2. **Automatic demotion** on live expectancy breach (bridge monitors realized R of the
   bridged strategy's trades and demotes on breach — no silent persistence of a loser).
3. **Shadow-first rollout** (new bridge runs in shadow mode before any real dispatch).
4. **Per-strategy risk caps** (max risk per bridged strategy, enforced at sizing).
5. **Operator token auth** (same discipline as existing promotion/`nexus risk release` surfaces).

## Affected files

- `src/nexus_scalp/execution/registry_bridge.py` — **NEW file; does NOT exist today** (verified 2026-09-09). Create it here.
- `src/nexus_scalp/signals/policy.py` — registry-sourced channel. **CONVENTION-LOCKED (BUG-054 foreign WIP):** policy.py is modified in the shared working tree by another agent; coordinate via taskboard before touching, keep changes additive (new channel enum + one evaluation branch), never rewrite existing channels.
- Operator endpoint (web/operator surface, pattern-match the existing operator-only promotion endpoint in debug_research_routes) + operator CLI subcommand if the web surface is contested.

## Coordination locks

- `signals/policy.py`: BUG-054 foreign WIP (see taskboard TASK-ARCH-DECOMP-SCOPE row). Additive-only, coordinate first.
- Execution surface must stay **fail-closed** (contract §8, INV-001..012): a missing/invalid registry read must result in NO trade, never a default-allow.
- `order_manager.py` / `live_engine.py` are hot-path convention-locked (INV-001/INV-004) — do NOT modify them for this item; the bridge should sit behind the existing policy/gate seams.
- **Dependency: P0-3 must land first** (stronger validation before anything is allowed to bridge). If dispatched concurrently, P0-1 must ship BRIDGE-BLOCKED (shadow mode only) until P0-3 gates are in.

## Why / benefit / difficulty / risk

- Why: converts research output into potential PnL; closes the one broken link in the architecture.
- Benefit: the first VALIDATED strategy can, for the first time, reach a market.
- Difficulty: MEDIUM (contract design needed; execution surface must stay fail-closed).
- Risk: could enable a bad strategy to trade — mitigated by shadow-first + per-strategy risk caps + operator token. Do not loosen any existing gate to make bridging easier.

## Acceptance criteria

1. A VALIDATED registry entry can be bridged to a live signal source ONLY via explicit operator action (token-gated, audited).
2. With the bridge disabled/at rest, live behavior is byte-identical to today (no new tick-path work when no strategy is bridged — INV-001).
3. Kill-switch + auto-demotion proven by tests (breach -> demotion recorded; kill -> no further dispatch).
4. Shadow-first default proven: a fresh bridge trades shadow-only until an explicit operator promotion step.
5. Regression tests green; ruff/mypy clean on new files; taskboard + closing handoff written.
