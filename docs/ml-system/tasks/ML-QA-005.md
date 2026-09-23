# ML-QA-005 — Merge-Marker Residue Guard (diff3 arm leak class)

**STATUS:** DONE (2026-09-23, AGENT-QA)
**PRIORITY:** P2
**AGENT_ROLE:** AGENT-QA
**DEPENDENCIES:** ML-QA-004 (DONE, PR #402)
**HUMAN_DECISION_REQUIRED:** NO
**PARALLEL_CLASS:** PARALLEL_SAFE
**OWNERSHIP_SCOPE:** `scripts/ci/check_merge_marker_residue.py` (new),
`tests/unit/test_merge_marker_residue.py` (new),
`scripts/ci/check_local.py` (stage wiring),
`agents/taskboard.md`, `docs/ml-system/06_TASK_LEDGER.md`,
`docs/ml-system/TASK_BOARD.md`, `tests/critical_suite.txt`, `agents/locks.yaml`
**FORBIDDEN_CHANGES:** `.github/workflows/*`, RemoteMT5GatewayAdapter and its
client contract, Telegram secrets, frozen domain models, `_process_tick_pipeline`.

---

## OBJECTIVE

A failed conflict resolution must not reach git — especially the partial-marker
shape where only the leading `<<<<<<<` line is stripped and the diff3 middle
arm is committed as literal content.

This is not hypothetical. It happened on `main`: PR #347 (merge `159cd3b3`)
shipped the `||||||| eb73440a` arm into two SSOT metadata files, together with
a duplicate contradictory task row, and it survived every existing gate because
`grep -n '<<<<<<<'` looks for the one line that was stripped.

## ACCEPTANCE_CRITERIA

- [x] **AC-1** The diff3 `|||||||` arm left in a tracked file is detected and
  fails a gate. Evidence: `tests/unit/test_merge_marker_residue.py::
  test_diff3_original_arm_detected` builds the exact incident shape and asserts
  `not rep.ok` with `kind == "diff3-original"` at the right line; the gate
  found the real 4 sites on the untouched tree.
- [x] **AC-2** All three unambiguous marker families are detected
  (`<<<<<<<`, `|||||||`, `>>>>>>>`). Evidence: `test_every_marker_family_detected`
  (parametrised over all three).
- [x] **AC-3** No false positive on a lone `=======` RST underline. Evidence:
  `test_lone_equals_rst_underline_is_not_residue`; `=======` is deliberately
  not matched (documented in the gate docstring).
- [x] **AC-4** The committed tree at HEAD is residue-free and the gate cannot
  ship red. Evidence: `test_gate_passes_on_committed_repo`,
  `test_cli_exit_code_zero_on_committed_repo`.
- [x] **AC-5** The actual residue is removed from `agents/taskboard.md` and
  `docs/ml-system/06_TASK_LEDGER.md`, including the duplicate/stale
  `ML-CI-002` rows. Evidence: `test_incident_files_have_no_diff3_marker_lines`,
  `test_no_duplicate_ml_ci_002_rows_in_ledger`,
  `test_no_duplicate_ml_ci_002_row_in_taskboard` (all 20/20 green).
- [x] **AC-6** The gate runs in CI (required check) AND in the local pre-push
  gate — no CI-only gate class. Evidence: test registered in
  `tests/critical_suite.txt` (233→234, manifest OK) and wired as
  `scripts/ci/check_local.py` stage `[5c]` (verified
  `merge_marker_residue -> passed` in the `--json` envelope).
- [x] **AC-7** The gate is deterministic, offline, dependency-free and fast.
  Evidence: `test_gate_is_deterministic`, `test_gate_stays_under_budget`
  (<30 s; measured 758.8 ms over 3,406 text files),
  `test_gate_has_no_repo_runtime_dependency` (no torch/polars/pydantic import).

## INVESTIGATION_PLAN (executed)

1. Reproduce the residue at HEAD with `git grep -nE '^(<<<<<<<|\|\|\|\|\|\|\||>>>>>>>)'`
   over tracked files → 4 sites across 2 files.
2. `git log -L` the corrupted region → PR #347 (`159cd3b3`) introduced the
   marker + duplicate rows.
3. Confirm no existing gate detects the class: grep
   `scripts/ci/`, `scripts/docs/`, `tests/` for marker/conflict checks → none.
4. Verify the `=======` hits in `champion_sentinel.py` and `trading_metrics.py`
   are RST underlines (context inspected) → exclude from the detector.
5. Negative-control the new gate against the pre-fix tree (4 findings, exit 1)
   then the fixed tree (clean, exit 0).
6. Prove every other failure is pre-existing by re-running it on the untouched
   shared `main` checkout (torch-absent collection errors, identical).

## IMPLEMENTATION_STEPS (executed)

1. New `scripts/ci/check_merge_marker_residue.py` — `git ls-files` walk,
   binary/oversize skip, three marker families, text + `--json` output,
   exit 0/1/2, stdlib-only.
2. Remove the residue and stale duplicate rows from both SSOT files
   (4 marker lines + 2 duplicate `ML-CI-002` rows).
3. New `tests/unit/test_merge_marker_residue.py` (20 tests) covering the
   incident class, all marker families, false-positive resistance, engineering
   properties and regression pins for the two incident files.
4. Register the test in `tests/critical_suite.txt`; wire
   `scripts/ci/check_local.py` stage `[5c]`.
5. Run all gates; update the board, ledger and this file; write the handoff
   report; PR.

## VERIFICATION_EVIDENCE

- `pytest tests/unit/test_merge_marker_residue.py` → **20/20 pass** (5.13 s,
  Python 3.11.16, slim venv, serial).
- Negative control on the pre-fix shared tree → **4 findings**, exit 1
  (exactly the 4 incident sites).
- Post-fix gate → `clean: 3406/3444 tracked files scanned (758.8 ms)`, exit 0.
- `ruff check` + `ruff format --check` on all touched files → clean.
- `mypy` on gate + test → `Success: no issues found in 2 source files`.
- `scripts/ci/verify_critical_suite_manifest.py` → 234 paths OK.
- `scripts/docs/check_docs.py` → `DOCS_HEALTH = PASS` (27,484 links).
- `scripts/ci/check_ml_contract_drift.py` → clean (8 constants, 4.8 ms).
- `tests/unit/test_gate_bypass_detection.py` + `test_ml_contract_drift.py` →
  **31 passed**.
- PRE-EXISTING (identical on the untouched shared `main`): the
  `check_local.py` `fast_tests` stage aborts on
  `tests/unit/test_50d_normalization_parity.py` and `test_critical_suite.py`
  with `ModuleNotFoundError: No module named 'torch'` (slim venv has no torch).

## NOTES

- `=======` is deliberately NOT matched (RST-underline ambiguity). Any real
  conflict block containing it also contains `<<<<<<<`/`>>>>>>>`, which the
  gate flags — no coverage lost.
- The owner attribution on the duplicate ledger rows was also wrong: the task
  file's own `AGENT_ROLE:` is `AGENT-QA`; the retained row uses `AGENT-QA`.
- The docs gate returned PASS over the malformed table; that blind spot is
  recorded in the handoff rather than silently "fixed".
