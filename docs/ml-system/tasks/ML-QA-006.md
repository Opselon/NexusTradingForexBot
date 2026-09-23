# ML-QA-006 — Duplicate Canonical Task-Row Detector (BUG-309 companion to ML-QA-005)

STREAM: STREAM L — CI/CD & Verification
PRIORITY: P2
STATUS: DONE (2026-09-23, AGENT-QA)
DEPENDENCIES: ML-QA-005 (DONE, PR #405)
AGENT_ROLE: AGENT-QA
OWNERSHIP_SCOPE: scripts/ci/, tests/
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Generalize the duplicate-contradictory-task-row defect class exposed by ML-QA-005
(PR #347 leaked diff3 residue bracketing THREE contradictory `ML-CI-002` rows in
two SSOT metadata files) into a standalone structural gate: at most one canonical
table row per task id in each declared SSOT table.

## DELIVERED
- `scripts/ci/check_duplicate_task_rows.py` — stdlib-only (re/pathlib/argparse/json),
  no repo import, no torch/polars. Scans declared SSOT tables:
  - `docs/ml-system/06_TASK_LEDGER.md` section "## 2. Master Backlog Table"
    (section-scoped, so the Historical Migration Map §1 legitimately pairing
    legacy + new ids per row is correctly excluded);
  - `docs/ml-system/TASK_BOARD.md` (whole file).
  A canonical row is identified by the LEADING task id in the first cell —
  dependency-cell mentions of other ids never count (that shape would drown
  the gate in false positives; the ledger's dependency column restates other
  ids on every row by design). Backticked and plain ids are the same identity.
  Exit 0/1/2 = clean/defect/config-error; `--json` single-line payload via
  `sys.stdout.write` (BUG-300: never Rich-wrapped); `--root` for sandboxed tests.
- `tests/unit/test_duplicate_task_rows.py` — 10 tests:
  real tables clean at HEAD (plain + JSON), duplicate-row negative control,
  the exact 3-contradictory-rows PR #347 shape, dependency-mention false-positive
  pin, migration-map section scoping pin, backtick/plain identity, stdlib-only
  import purity, missing-root config error rc=2, one-line JSON purity.
- Wired BOTH sides of CHG-0049 gate parity:
  - registered in `tests/critical_suite.txt` (238 paths; test rides the required
    Code Quality & Tests / Pytest check over the committed tree);
  - `scripts/ci/check_local.py` stage `[5d]` duplicate_task_rows (no workflow
    file edited — CI-only gate class not introduced).

## VERIFICATION (all at worktree branch tip, Python 3.11.16)
- pytest tests/unit/test_duplicate_task_rows.py -> 10/10 pass (0.94s, slim venv)
- Combined regression slice test_duplicate_task_rows + test_merge_marker_residue
  + test_ml_qa_004_determinism_remediation -> 54 passed
- NEGATIVE CONTROL: injected exact duplicate `ML-CI-002` row into the real
  ledger -> gate flagged rows [82, 83], exit 1; restored -> clean exit 0
- check_local.py --all --fast: all stages passed except fast_tests, whose only
  failures (tests/unit/test_capital_protection_a15.py) are the PROVEN
  PRE-EXISTING torch-absent red (ModuleNotFoundError: torch at
  src/nexus_scalp/position_adviser/service.py:35), identical on the pristine
  main shared tree; 22/22 green under the torch venv
  (PYTHONPATH=src:.:/tmp/nse-slim/lib/python3.11/site-packages /tmp/nse-venv/bin/python)
- ruff check + format --check clean (3 touched files); mypy Success (2 source files)
- verify_critical_suite_manifest.py -> CRITICAL_SUITE_MANIFEST_OK: 238 paths
- check_merge_marker_residue.py clean (3457 files, 757.7 ms);
  gate_parity.py PASS; check_ml_contract_drift.py clean; check_docs.py
  DOCS_HEALTH = PASS
- Both declared tables scan CLEAN at HEAD — no duplicate rows currently exist.
