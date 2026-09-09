# P0-2 HANDOFF — LIVE-BOOK EDGE ATTRIBUTION + IMPROVEMENT LOOP (deep audit Section K)

**Date:** 2026-09-09 | **Priority:** P0 (correctness/profitability blocker) | **Status:** TODO (dispatch-ready, unclaimed)
**TASK-ID to claim:** `TASK-P0-2-EDGE-ATTRIBUTION`
**Suggested agent role:** Nexus-QA-Forensics / experience-lane coder
**Source of truth:** `artifacts/audits/research_strategy_factory_deep_audit.txt` §K P0-2 (lines 814-824); §J (attribution gap)
**Sub-report evidence:** `/tmp/audit_sub/experience.txt` (ephemeral path; findings quoted inline)

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

The live book is measured NEGATIVE (prior 158-trade paper record: PF 0.381, avg R
-0.082, total -$2,896 — `docs/agent_handoffs/2026-09-07_final_production_readiness_audit.md`
§5) and there is NO attribution and NO improvement loop: the system cannot decompose
its own result into model-gated vs channel-only entries, or exit-path contributions.
You cannot improve an edge you cannot decompose. This stops the bleeding and identifies
any profitable sub-book.

## Evidence (verified at HEAD 76a3da01)

- §J: no model-vs-exit attribution anywhere; `entry_reason` is free-form text — it cannot
  be grouped by source (model-gated vs channel-only) without a canonical typed column.
- The counterfactual exit replay harness ALREADY EXISTS: `scripts/forensics/exit_policy_counterfactual.py`
  (commit 76a3da01, wave 3; reads `audit_experience_outcomes` incl. mfe_r/mae_r/exit_reason,
  fail-closed NO_DATA on empty table, SYNTHETIC mode labeled as such). It has NO scheduled
  evaluation job and no digest consumer yet.
- The wave-3 taskboard row TASK-EXIT-LEDGER-EXPORT is BLOCKED-ON-OPERATOR: the 158-trade
  ledger exists only on the production host; this dev box's `audit_experience_outcomes`
  has 0 rows. Do NOT fabricate ledger data (honest-status rule; wave-3 taskboard preamble).

## Required capability (build this)

1. **Typed signal-source attribution:** extend `src/nexus_scalp/experience/intelligence.py`
   so every recorded decision carries a typed channel column (canonicalized from the
   free-form `entry_reason`) distinguishing model-gated vs channel-only (sweep/ICT/
   ichimoku/stat-arb/RuleMatrix). Additive schema evolution only — coordinate with the
   AuditRepository owner (A4 dead-letter split just landed in 305adf33; zero schema change
   was their constraint — respect the DB protocol §27 for additive columns).
2. **Scheduled evaluation job:** run `scripts/forensics/exit_policy_counterfactual.py` on a
   schedule (maintenance-cycle cadence, pattern-match weekly snapshot wiring in
   `src/nexus_scalp/application/live/maintenance.py:37-43`) over the real ledger, plus an
   entry-ablation pass (channel on/off expectancy delta).
3. **Weekly "edge ledger" report:** extend `src/nexus_scalp/reporting/operational_digest.py`
   with an edge-ledger section: expectancy by channel / model-state / exit-path, with
   bootstrap CIs (reuse the deterministic paired-bootstrap pattern from shadow/bootstrap.py,
   seed derived from run_id). Report-only — no gate changes, no tuning.

## Affected files

- `src/nexus_scalp/experience/intelligence.py` (typed channel column)
- evaluation job wiring (maintenance cycle or a standalone scheduled script under `scripts/`)
- `src/nexus_scalp/reporting/operational_digest.py` (edge-ledger section)
- regression tests (new)

## Coordination locks

- Offline analysis only — ZERO live-path changes (no tick-path files). This is why the audit rates difficulty LOW-MED with no risk.
- Depends on **P0-5** for ledger volume with nonzero R; on this dev box the harness will
  exit NO_DATA — that is correct behavior, do not "fix" it with synthetic data cited as evidence.
- `reporting/operational_digest.py` already references FeatureDriftBreaker (P0-6 overlap) — additive section only, coordinate via taskboard if that file gains an owner.

## Why / benefit / difficulty / risk

- Why: attribution is the precondition for any exit/policy repair decision (the wave-3 exit brief explicitly orders evidence before tuning).
- Benefit: stops bleeding; identifies the profitable sub-book (if one exists).
- Difficulty: LOW-MED. Risk: none (offline analysis).

## Acceptance criteria

1. Typed channel column recorded for every NEW decision (old rows NULL-tolerated, never backfilled/fabricated).
2. Scheduled job runs the counterfactual replay + entry ablation; NO_DATA is an honest, loud outcome.
3. Weekly edge-ledger report renders expectancy by channel/model-state/exit-path WITH CIs; PF numbers from synthetic fixtures are labeled SYNTHETIC, never cited as ledger evidence.
4. Regression tests green; ruff/mypy clean; taskboard + closing handoff written.
