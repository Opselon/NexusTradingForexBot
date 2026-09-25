# ML-QA-016 — Hot-reload suite process-identity determinism

- **Status**: DONE (this file records the completed work)
- **Agent**: AGENT-QA
- **Stream**: L (CI/CD & Verification)
- **Priority**: P2
- **Wave**: QA determinism roster continuation
- **Task type**: test-only determinism remediation (zero production files changed)

## Objective

`tests/unit/test_runtime_config_hot_reload.py` (push-gate module,
`tests/critical_suite.txt:461`) was listed by the ML-QA-003 determinism census
(`docs/ml-system/test_determinism_roster.md` section 6, recount row) as 3 live
non-determinism sources: 2 `os.getpid()` asserts in the §65 acceptance test
`test_save_changes_deterministic_behavior_without_restart` and 1
`tempfile.mkdtemp()` at its line 70. Remove the process-identity and untracked
temp-root dependence so the hot-reload contract the module pins is a property
of the code, not of the OS's process assignment or the host's temp layout.

## The defect class

`os.getpid()` returns a process identity, not a contract value. The §65 test
captured it at the top (`engine_pid = os.getpid()`, line 75) and re-asserted
the same literal at the bottom (`assert os.getpid() == engine_pid`,
line 128). No production path reads the pid, so the magnitude carried no
information about the hot-reload contract — the suite passed identically on
any pid the OS assigned, and would have passed under a regression that
changed every behaviour the test actually pins.

Worse, the pid assert was a *proxy with a blind spot*. It stood in for "the
same engine instance served the snapshot before and after the apply", but
`fork()` keeps the parent's pid in the child, so a regression that copied the
store into a new process would have passed the old assert while breaking the
invariant the test believed it was proving.

`tempfile.mkdtemp()` created a temp root pytest does not track, which on some
hosts resolves to a per-run symlink target (the roster's macOS note), and
left cleanup to the OS rather than the fixture.

## Remediation

The two pid reads are consolidated into ONE injected supplier (`_pid`), so
the semantic "one process identity throughout the hot-reload cycle" is still
covered but asserts a *stable identity* rather than a particular number. A
fork between the two points still flips the value and fails the assert.

The invariant the pid stood in for is now asserted directly, by the thing
that actually proves it — OBJECT IDENTITY:

- `store_before = store` captured before the apply, `assert store is
  store_before` after it. A hot reload is an in-object atomic swap, so the
  store the test holds at the top *is* the object serving the post-apply
  snapshot. This is exactly what a pid comparison cannot distinguish from a
  legitimate same-pid re-construction.
- a new second-reference leg `test_second_reference_observes_the_swap`: a
  second reference to the same store object sees the new version on its next
  read. This covers the other half of the blind spot — a fork keeps the pid
  in the child, so the old assert passed while the store it compared was
  already a copy.

`mkdtemp()` became the `tmp_path` fixture (the §65 test now takes it and
builds its settings DB at `tmp_path / "app_settings.db"`), so the temp root
is tracked and cleaned by pytest.

## OWNERSHIP_SCOPE

- `tests/unit/test_runtime_config_hot_reload.py` (remediated, 14 -> 15 tests)
- `tests/unit/test_ml_qa_016_hot_reload_identity_determinism.py` (new, 12 tests)
- `tests/critical_suite.txt` (manifest entry for the new battery, 504 -> 505)
- `docs/ml-system/test_determinism_roster.md` (recount row update)
- `docs/ml-system/TASK_BOARD.md` + `docs/ml-system/06_TASK_LEDGER.md` (status)
- `agents/taskboard.md` (task row)
- `docs/agent_handoffs/2026-09-25_AGENT-QA_ML-QA-016.md` (this report)

## FORBIDDEN_CHANGES

- No production file. The hot-reload contract (`RuntimeConfigStore.apply` ->
  validate -> persist -> build snapshot -> publish event -> atomic swap) is
  byte-for-byte unchanged; only the test's *expression* of it changed.
- The 14 durable contract tests must survive the pass with names and hard
  asserts unchanged (the version/apply/reject/rehydrate/exit-policy legs).
- No `re` import in the textual battery (a shadowing `re` module ahead of
  stdlib on `sys.path` can drop a negative lookahead and INVERT a rule). The
  tokenize-based `_code_lines` extractor is used instead, which excludes
  string-literal interiors explicitly — the module's ML-QA-016 header block
  and the battery's own docstring both name `os.getpid()` while documenting
  the removed defect, exactly how these regressions stay explained.

## ACCEPTANCE_CRITERIA

- [x] Zero live `os.getpid()` calls in the two hot-reload tests' executable
      lines (the single remaining read lives in the `_pid` supplier)
- [x] Exactly one `os.getpid()` read in the whole module, inside the supplier
- [x] The "same engine instance" invariant asserted by object identity, not a
      pid literal (`store is store_before` + a second-reference leg)
- [x] `mkdtemp` and `import tempfile` gone; the temp root comes from `tmp_path`
- [x] All 14 durable contract tests preserved, names and hard asserts unchanged
- [x] Zero production files changed
- [x] 12-test contract battery pins the shape (manifest-registered)
- [x] Negative control: the battery FAILS on the pre-remediation module
      (7 failed / 5 passed; restore confirmed — 1 `os.getpid()` at line 59
      inside the supplier, `_pid()` x4, 0 `mkdtemp`, no `import tempfile`)
- [x] ruff check + ruff format clean; mypy clean; manifest 504 -> 505 paths;
      merge-marker residue, duplicate task rows, dependency drift and docs
      health all PASS

## Verification evidence

- `tests/unit/test_runtime_config_hot_reload.py`: 15 passed
- `tests/unit/test_ml_qa_016_hot_reload_identity_determinism.py`: 12 passed
- Combined 27 passed (slim venv: `/tmp/nse-slim/bin/python`,
  `PYTHONPATH=src:.`)
- Negative control on the pre-remediation text: 7 failed / 5 passed, then the
  fixed module was restored and re-verified green. The 5 non-failing rules
  are the three behavioural legs (they construct their own stores, so they
  do not depend on the analysed module's text) plus the durable-names and
  manifest rules — all 7 textual source-shape rules fail on the old text.
- Related suites unaffected: ML-QA-013 + ML-QA-014 + ML-QA-015 batteries =
  46 passed, 2 skipped (torch-absent skips, a pre-existing environment fact —
  the ML-QA-014 battery needs the torch+polars combined interpreter)
- `ruff check`: All checks passed; `ruff format`: 1 file reformatted
  (a wrapped manifest assert); `mypy`: Success, no issues found in 2 files
- `scripts/ci/verify_critical_suite_manifest.py`:
  CRITICAL_SUITE_MANIFEST_OK: 253 paths all exist
- `scripts/ci/check_merge_marker_residue.py`: clean (3738/3776 scanned)
- `scripts/ci/check_duplicate_task_rows.py`: 0 duplicate rows
- `scripts/ci/check_dependency_drift.py`: OK, 100 pins
- `scripts/docs/check_docs.py`: DOCS_HEALTH = PASS
