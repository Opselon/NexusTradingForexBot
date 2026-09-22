# AGENT-GIT — ML-CI-002 — ML Contract & Canonical Documentation Drift Prevention CI Gate

**Date:** 2026-09-21 · **Specialist:** AGENT-GIT (Stream L) · **Status:** DONE — PR opened
**Branch:** `agent/qa/ml-ci-002` · **Base:** `origin/main` @ `9fab7ff7`

---

## 1. Objective

Implement an automated CI gate that parses canonical ML constants from source
code and asserts that `docs/ml-system/` contracts never drift from executable
reality. The gap this closes is historical: past audits found the canonical
docs claiming 70D was production-active while `ACTIVE_SCHEMA_ID` stayed
`scalp_v1` (50D), and describing a 4-class head while only 3 classes are
trained. "The developer must remember to update the docs" is not an
enforcement mechanism.

## 2. What shipped

**`scripts/ci/check_ml_contract_drift.py`** — a dependency-free static gate
that:

- Extracts 8 canonical constants from source via anchored regex
  (`ACTIVE_SCHEMA_ID`, `FEATURE_DIMENSION`, `TRAINED_CLASS_COUNT`,
  `TRAINED_CLASS_NAMES`, `LEGACY_HEAD_CLASSES`, `WAIT_LOGIT_INDEX`,
  `SCALP_V3_DIMENSION`, `HARD_MAX_LOTS`).
- Asserts each value is asserted somewhere in the four canonical contract
  docs (`01_SYSTEM_CONTRACT.md`, `02_DATA_CONTRACT.md`,
  `03_MODEL_ARCHITECTURE.md`, `05_INFERENCE_FORWARDTEST_GOVERNANCE.md`).
- Emits two finding classes: `STALE_DOC` (a doc line asserts a value that
  differs from source) and `MISSING_DOC` (the canonical declaration moved or
  the docs never cited it).
- Reports text (`::error::` annotations) or machine-readable `--json`, exit
  0 clean / 1 drift / 2 tooling error.

**Wiring (both sides, deliberately):**

- `.github/workflows/ci.yml` `ci-integrity` lane — new step
  `ML contract drift guard (docs/ml-system vs source)`.
- `scripts/ci/check_local.py` stage **[5b]** `ml_contract_drift` — the local
  pre-push gate runs the same check, so no CI-only gate class exists
  (`scripts/ci/gate_parity.py` exists precisely to catch that; it reports
  6/6 PASS, 0 drifts on this branch).

**Docs fix:** the contract suite never cited the position-size ceiling
anywhere in `docs/ml-system/`. Rather than excluding the constant from the
contract, §2.2 "Execution Ceiling (Position Safety Contract)" was added to
`05_INFERENCE_FORWARDTEST_GOVERNANCE.md`, citing `HARD_MAX_LOTS = 10.0` and
`MAX_TOTAL_EXPOSURE = 1`. This is real coverage, not an exception.

## 3. Design decisions and why

- **Direction of truth is SOURCE**, matching the evidence hierarchy in
  `01_SYSTEM_CONTRACT.md` §2. The gate never fails because a doc added a
  sentence; it fails only when a doc asserts a value the source no longer
  holds, or when the canonical declaration itself moved.
- **Regex-on-source, not import.** Importing the feature/model packages pulls
  torch and polars into a static CI lane that must stay cheap and
  dependency-free — the same design as `check_torch_load_safety.py` and
  `check_migration_safety.py`. Patterns are anchored to the declaration line
  so a comment quoting the symbol cannot satisfy them (pinned by
  `test_source_comment_cannot_satisfy_extraction`).
- **Prose immunity.** `_assertes_contract_value` accepts only
  assignment/copula shapes (`KEY = v`, `KEY: int = v`, `KEY is v`). Past
  tense and file references never fire
  (`test_historical_prose_is_not_a_value_claim`). Without this the first
  sentence of `01_SYSTEM_CONTRACT.md` §5.2 ("3 trained, 1 dead logit") would
  be a permanent STALE_DOC finding for a legitimate historical description.
- **No CI-only gate class.** CHG-0049 gate-parity contract: every CI gate
  must run locally too, otherwise a CI failure is unverifiable offline.

## 4. Bugs found and fixed in this task's own work

1. **`--json` emitted a Python dict repr, not JSON** — `print(rep.public())`
   on a dict produces `{'ok': True, ...}` with single quotes, which
   `json.loads` rejects. The `--json` path now writes
   `json.dumps(..., indent=2)` to unwrapped stdout. This is the same defect
   class as the BUG-300 CLI lesson (rich `console.print` wrapping corrupts
   machine-readable output at 80 columns); caught by
   `test_cli_json_output_is_machine_readable`.
2. **Prose-detector over-match.** The first `_assertes_contract_value` used a
   split-on-key tail regex that treated *any* `KEY <word>` sequence as a
   value claim, so "TRAINED_CLASS_COUNT was discussed in audits" counted as
   an assertion and would have hidden real drift. Now anchored to
   assignment/copula shapes only.
3. **Tests passing vacuously (the important one).** The first
   `test_stale_doc_detected_when_class_count_changes` passed only because
   `_doc_citations` matched a substring (`7` inside `127-132`) of an
   unrelated doc line, satisfying the "cited" check. Any value change was
   therefore undetectable. Root cause: matching the *symbol name* instead of
   the *source value*, plus no stale-claim scan. Fixed on both axes, and the
   test now asserts the finding names the new value with a doc `file:line`.
4. **Anchor `(\d+)`, not `(50)`/`(3)`.** A literal in the capture group makes
   every "the value changed" test report "declaration not found" instead of
   "value differs" — the finding is still correct but the evidence is
   misleading. Capture groups now stay generic; the value comparison carries
   the semantics.

## 5. Verification evidence (all executed, not claimed)

| Check | Result |
|---|---|
| `pytest tests/unit/test_ml_contract_drift.py` | 20 passed, 0 failed (0.23 s, Python 3.11.16) |
| `check_ml_contract_drift.py` on committed tree | `ML contract drift check clean: 8 canonical constants verified against 4 contract docs (4.2 ms)` rc=0 |
| `ruff check` / `ruff format --check` | clean on `check_ml_contract_drift.py`, `check_local.py`, `test_ml_contract_drift.py` |
| `mypy` | clean on both touched `scripts/ci` modules |
| `verify_critical_suite_manifest.py` | `CRITICAL_SUITE_MANIFEST_OK: 222 paths` |
| `check_workflows.py --format text --strict` | 0 ERROR, 0 WARNING, 0 INFO |
| `gate_parity.py --json` | 6/6 checks PASS, 0 drifts |
| `check_dependency_drift.py` | OK, 99 pins (unchanged) |
| BENCHMARK_PLAN (<0.5 s) | 4.2 ms single run; 5-run average asserted by `test_benchmark_completes_under_half_second` |
| Determinism | two consecutive runs produce byte-identical findings |

**Pre-existing failure, NOT caused by this change:** `test_check_local_gate.py`
`test_gate_passes_clean_tree` / `test_fix_repairs_mechanical_and_gate_repasses`
fail identically with my changes stashed — its `fast_tests` stage exits 2
(pytest cannot resolve the repo venv's `Scripts/python.exe` path on Linux and
falls back to a slim interpreter missing ruff/mypy). Reproduced on untouched
`origin/main` in the same worktree.

## 6. Scope compliance

- `OWNERSHIP_SCOPE` = `scripts/ci/check_ml_contract_drift.py`. Honored: the
  new gate is the artifact. Supporting edits are only the wiring the task's
  own IMPLEMENTATION_PLAN step 5 requires (`.github/workflows/ci.yml`),
  the local-gate parity equivalent (`check_local.py`), the critical-suite
  manifest entry, and the docs fix that makes the contract complete.
- `HUMAN_DECISION_REQUIRED: NO`, `ABORT_CONDITIONS: None`.
- No frozen domain model, no tick path, no MT5 adapter, no Telegram secret
  touched. No destructive git operation performed.

## 7. Handoff to the operator

The gate runs on every push and every PR to `main` starting at this merge.
The failure mode an operator will see first is `::error::ML contract drift
[STALE_DOC] <KEY>` with the offending doc `file:line` — the fix is either
update the doc to the new value or revert the source change; the gate is
deliberately not auto-repairing (docs are human-authored prose).
