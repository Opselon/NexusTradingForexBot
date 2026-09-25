# P0-6 HANDOFF — SERVING TRUST ANCHOR (self-referential) + UNWIRED DRIFT BREAKER (deep audit Section K)

**Date:** 2026-09-09 | **Priority:** P0 (correctness/profitability blocker) | **Status:** TODO (dispatch-ready, unclaimed)
**TASK-ID to claim:** `TASK-P0-6-SERVING-TRUST-ANCHOR`
**Suggested agent role:** Nexus-LiveEngine-adjacent coder (model serving lane; NOT the hot-path seam)
**Source of truth:** `artifacts/audits/research_strategy_factory_deep_audit.txt` §K P0-6 (lines 864-871); Appendix R (lines 973-1000)
**Sub-report evidence:** `/tmp/audit_sub/serving.txt` (ephemeral path; findings quoted inline)

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

The serving trust anchor is self-referential: the bundle loader verifies artifact
integrity against the bundle's own metadata, with no cross-check against the lifecycle
registry's governed CHAMPION record, no scaler-hash re-verification, and an unguarded
`force_fresh` bypass. Separately, the drift breaker is built but unwired (zero callers),
so silent distribution drift is observed but never enforced. Appendix R found DIRECT
evidence the on-disk serving state already drifted from its governance record.

## Evidence (verified at HEAD 76a3da01 + on-disk artifacts)

- `src/nexus_scalp/application/live/model_bundle_store.py:71`: `force_fresh_model=True`
  (constructor kwarg) skips the integrity gate entirely (CLI-only today, but no guard).
- `model_bundle_store.py:150-160`: live width check compares the vector against
  metadata only — no registry cross-check; `model_bundle_store.py:240-288`
  (`_load_scaler_artifacts`): the scaler content hash is NOT re-verified against any
  governed record.
- `src/nexus_scalp/risk/drift_breaker.py:169-183`: `FeatureDriftBreaker` +
  `build_drift_breaker_for_engine` have ZERO production callers — PSI drift is
  OBSERVED, not ENFORCED (only other reference: `src/nexus_scalp/reporting/operational_digest.py`,
  reporting-only).
- **Appendix R (drift happened already):** on-disk
  `artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt` sha256 `da6eab534bfde12c`
  with input_projection (128,50) does NOT match the recovery record's governed
  champion `c9982ddde1755591` (128,70) per
  `docs/agent_handoffs/2026-09-07_bundle_recovery_70d_champion_handoff.md`; the bundle
  directory contains ONLY model.pt (no manifest.json, no scaler sidecar) — it can
  serve only via LEGACY_UNVERIFIED/cold-start paths. "The on-disk serving state
  drifted from its governance record and nothing at rest detects it."

## Required capability (build this)

1. **Load-time registry cross-check** in `model_bundle_store.py`: before serving, compare
   the bundle's artifact hash against the lifecycle registry's CHAMPION record hash
   (fail-closed on mismatch/missing record; mismatch => ArtifactIntegrityError, same
   fail-closed family as the proven HASH_MISMATCH behavior).
2. **Scaler hash re-verify**: scaler.npz content hash checked against the governed
   record (not just width/mean/std shape checks).
3. **Guard the force_fresh bypass**: require an explicit env/operator opt-in
   (pattern-match `allow_legacy_unverified_artifacts` fail-closed discipline) + loud
   CRITICAL log + audit trail when used.
4. **Wire `FeatureDriftBreaker` into the entry gate as a degrading block** (block/degrade
   entries on drift breach; INSUFFICIENT_DATA forbids action — preserve that
   data-only-by-design semantics). Drift-breaker construction should live OUTSIDE
   order_manager/live_engine hot-path edits: a post-policy stage in tick_pipeline or a
   gate inside the serving layer. NOTE `reporting/operational_digest.py` already imports
   FeatureDriftBreaker — additive coordination only.

## Affected files

- `src/nexus_scalp/application/live/model_bundle_store.py` (cross-check + scaler hash + force_fresh guard)
- `src/nexus_scalp/risk/drift_breaker.py` (wiring-ready consumer, already exists at THIS path — NOT under model_lifecycle/)
- `src/nexus_scalp/application/live/tick_pipeline.py` (post-policy drift stage) — **CONTESTED FILE (foreign WIP in tree)**; additive gate only, coordinate via taskboard first
- registry read surface: `src/nexus_scalp/research/registry.py` / lifecycle champion record (READ-ONLY consumption — the bridge P0-1 owns writes; do not blur the two)
- regression tests (new)

## Coordination locks

- `tick_pipeline.py` and `live_engine.py` are hot-path convention-locked (INV-001/004, foreign WIP in tree) — additive, minimal hunks; engine-launch gate MANDATORY.
- Do not confuse paths: drift breaker lives at `src/nexus_scalp/risk/drift_breaker.py`; the bundle store at `src/nexus_scalp/application/live/model_bundle_store.py`.
- The current on-disk artifact mismatch (Appendix R) is EVIDENCE, not something to "fix" silently — surface it; the operator decides re-recovery vs retirement. Your code must make such states detectable at rest/at load.
- P2-4 (wire-or-delete FeatureDriftBreaker) is downstream of this item — after wiring, that row's drift entry is resolved.

## Why / benefit / difficulty / risk

- Why: the served bytes must be provably the governed artifact; closes the last accidental-serve path and stops silent distribution drift.
- Difficulty: LOW. Risk: none (fail-closed additions only).

## Acceptance criteria

1. Load refuses a bundle whose hash does not match the registry CHAMPION record (red-before/green-after test with a tampered hash).
2. Scaler content-hash mismatch refuses load (test).
3. force_fresh without explicit operator opt-in is impossible (test); with opt-in it logs CRITICAL + writes an audit trail.
4. Drift breaker is constructed by the engine and gates entries (degrading block; INSUFFICIENT_DATA -> no action, pinned by test).
5. At-rest detector: the current Appendix-R disk state (50D on disk vs 70D governed record) would now be REFUSED or ALARMED at load (test reproduces it).
6. Engine-launch gate green; ruff/mypy clean; taskboard + closing handoff written.
