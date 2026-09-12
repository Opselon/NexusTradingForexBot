# Agent-2 Closing Handoff — TASK-P0-4-EVAL-GOVERNANCE-VERIFICATION (ML Evaluation & Governance)

**Date:** 2026-09-11 | **Agent:** Agent 2 (ML Evaluation & Governance Fixer, A2A)
**Worktree:** `../agent2-eval-governance` (isolated git worktree at origin/main `07d83fc1`; the main checkout stays untouched on `agent7-fi9-runtime-resilience` with Agent-7's dirty WIP — never staged, never stashed, never cleaned)
**Status of row 232 (TASK-P0-4-TRAINING-GATE-CONTAMINATION):** CLOSED (verification of the relanded implementation + regression net landed; no source defect found in scope)
**Commit:** `fix(eval-gov): P0-2/P0-4 verification regression net + artifact-store scaler_sha256 stamping (Agent-2, TASK-P0-4)`

---

## 1. What was verified (read-only, no source edits in Agent-1 or serving files)

The P0 reland wave (PR #125 `fe08efe1` + PR #129 carrier `07d83fc1`, 2026-09-11) restores the
consensus implementations. All five guarantees were re-proven against origin/main code:

| # | Guarantee | Implementation verified at 07d83fc1 | Proof |
|---|---|---|---|
| EG-1 | Train rows cannot enter OOS | `validation.py` `scope_oos_frame` (val/test only; train/purged counted and excluded) + the hard `ValueError` on a full-frame label vector (the historical contamination shape) and on any length-mismatched vector; `force=True` cannot bypass (raise precedes every force-able gate) | 4 tests |
| EG-2 | OOS-only metric computation | `validate()` scopes the frame; `overall` carries `evaluated_splits / train_rows_excluded / purged_rows_excluded / rows_eval / rows_dropped`; tampering TRAIN labels provably cannot change the verdict | 3 tests |
| EG-3 | Protected test block single-shot | `evaluate_test_block_once` ledger (durable JSON, atomic write); first use CONSUMED; same-model_id reuse → `TestBlockReuseError` on the primitive and a fail-closed REJECTED verdict (`overall.reason=TEST_BLOCK_REUSE`, `REUSED_REJECTED` record, attempts=2) through `validate()`; explicit ledger path honored, no default-path side effects | 5 tests |
| EG-4 | String-only CHALLENGER impossible | `three_model.py:356-362` `gate_artifact_ok` predicate requires REAL persisted fold evidence (`folds` + `fold_geometry` + `oos_accuracy` + `oos_samples>0`); the self-report string `"PASS (purged walk-forward completed)"` is not an input to the predicate; every incomplete-evidence flavor stays CANDIDATE | 7 tests |
| EG-5 | Champion/artifact + scaler hash fail-closed | `ModelBundleStore._verify_champion_registry_binding` (sha16 vs governed CHAMPION row; mismatch → `ArtifactIntegrityError`; no champion row / no registry → INERT, fresh installs not bricked; check runs inside `_load_or_create_bundle`'s weight-load path, pinned by spy) + `load_integrity.verify_artifact_integrity` scaler branch (declared `scaler_sha256` mismatch / missing sidecar → refuse; no declaration → legacy weights-only) | 12 tests |

Baseline health (read-only runs): `test_model_load_integrity.py` + `test_three_model_pipeline.py` +
`test_model_benchmark_phase13b.py` = 47 passed pre-change; the full model-governance battery
(+ `test_champion_bundle_recovery_contract.py` + `test_model_governance_phase16.py`) = 111 passed.
No regressions; the new suite rides on top: final combined run 142 passed.

## 2. What was changed (2 files, both in Agent-2 scope)

1. **`tests/unit/test_eval_governance_p0_4.py` (NEW, 31 tests)** — the regression net named in
   taskboard row 232 and the P0-4 handoff. Deterministic synthetic frames + real sqlite registry
   fixtures (CHAMPION row inserted through the production `ModelLifecycleRegistry.ensure_schema`
   migration path). No network, no MT5, no real artifacts.
2. **`src/nexus_scalp/model_generation/artifact_store.py` (ADDITIVE, writer side of P0-2)** —
   `save_model_artifact` now stamps `manifest["scaler_sha256"]` alongside the legacy
   `scaler_hash` when a scaler is persisted. This is the binding `load_integrity` verifies; the
   serving-side check existed at main but NO production writer emitted the field, so the scaler
   content verification was dormant for model-generation-lane artifacts. Pinned by
   `test_eg5_writer_side_stamps_scaler_sha256` (stamped value == real sha256 of the sidecar;
   no-scaler path unchanged).

Not touched (per coordinator ruling + collision rules): `model_bundle_store.py`,
`load_integrity.py`, `live_engine.py`, `live_sequence.py`, `inference.py`,
`walk_forward_trainer.py`, `cli/doctor.py`, and every Agent-7 dirty file.

## 3. Verification commands (repo venv, worktree)

```
.venv/Scripts/python.exe -m pytest tests/unit/test_eval_governance_p0_4.py -p no:cacheprovider
  -> 31 passed
... + test_model_load_integrity + test_three_model_pipeline + test_model_benchmark_phase13b
    + test_champion_bundle_recovery_contract + test_model_governance_phase16
  -> 142 passed
ruff check  (both files)      -> clean
ruff format (both files)      -> clean
mypy artifact_store.py        -> clean
```

Worktree quirk (recorded for future agents): the repo venv has an editable install pointing at
the MAIN checkout's `src/`, so tests in a sibling worktree must run with
`PYTHONPATH=<worktree>/src` to exercise the worktree code (`tests/conftest.py` guards only
`.worktrees/`-style paths, not sibling worktrees). All runs above used the worktree source
(import path verified via `nexus_scalp.__file__`).

## 4. Open items (reported, owned elsewhere)

1. **AGENT-1 DEPENDENCY — serving-manifest writer:** the serving lane emits its integrity record
   via `training/walk_forward_trainer.py`, which does not stamp `scaler_sha256`. Until Agent-1's
   lane adds the stamp, the scaler content binding is active only for artifacts written through
   `ArtifactStore.save_model_artifact` (this commit) and remains weights-only for
   walk-forward-emitted bundles (legacy-compatible, not a regression).
2. **Ledger consumer (recommendation, TASK-RUNTIME-TRUTH owner):** `artifacts/model_generation/
   test_block_usage.json` is now proven durable + enforced in-process, but no doctor/CI check
   scans it for `REUSED_REJECTED`/`ERROR` records. A doctor check (or a CI lane assert) would
   surface reuse attempts that were refused. Per ruling I did not touch `cli/doctor.py`.
3. **EG-4 test granularity:** `gate_artifact_ok` is pinned as an extracted, behavior-identical
   predicate (`_candidate_ok_contract`) rather than through a full `train_variant` execution
   (needs ≥10k bars + full walk-forward). The predicate is verbatim from `three_model.py`; if the
   predicate is ever moved/changed, the docstring in the test names the source line contract.
4. **Grand-test debt (pre-existing, not mine):** `dataset_factory`-built frames reach validation
   through `benchmark.py`, which scopes correctly; `CandidateTrainer.train_candidate` still trains
   on `split != test & != purged` BY DESIGN (train pool = train rows) — that is training, not
   evaluation, and out of scope.

## 5. Honesty labels

- TESTED: all EG-1..EG-5 behavior pins, writer-side stamp, baseline/subsystem suites (commands above).
- OBSERVED: reland code inspected line-by-line at `07d83fc1` before test authoring.
- NOT TESTED: real-artifact end-to-end load on the production host (champion artifact not on this
  host — BLOCKED-ON-OPERATOR, unchanged from row 232's handoff note).
