# ML-QA-017 — Training-env worker suite process-identity determinism

- **Status**: DONE (this file records the completed work)
- **Agent**: AGENT-QA
- **Stream**: L (CI/CD & Verification)
- **Priority**: P2
- **Wave**: QA determinism roster continuation
- **Task type**: test-only determinism remediation (zero production files changed)

## Objective

`tests/unit/test_training_env_worker.py` (push-gate module,
`tests/critical_suite.txt:481`) was the open row of the ML-QA-003 determinism
census recount (`docs/ml-system/test_determinism_roster.md` section 6) with 2
live `os.getpid()` sources: the subprocess fixture stamped its pid onto the
progress line and the parent asserted `events[0].metrics["pid"] != os.getpid()`
(an `import os` executed inside the test body, at its line 61). Remove the
process-identity dependence so the transport contract the module pins is a
property of the pipe protocol, not of the OS's process assignment.

## The defect class

`os.getpid()` returns a process identity, not a contract value. The parent read
its OWN pid and asserted the worker's differed from it. No production path
compares the two, so the magnitude carried no information about the transport
contract — the suite passed identically on any two pids the OS assigned.

Worse, the assert was a *proxy with a blind spot*. It stood in for "the
transport delivers ONE worker process identity across the whole run" (the pipe
protocol stamps the identity at the progress stage and again at the result
stage), but comparing against the PARENT's pid cannot prove that: a regression
that routed the result line through a different process than the progress line
still yields a worker pid different from the parent's, so the old assert passed
while the continuity invariant it was pretending to prove broke. The battery's
`test_a_fork_between_the_stages_breaks_continuity` leg demonstrates this
empirically: its split-identity fixture stamps the parent's own pid on the
result line, the old assert still passes on the progress line alone, and only
the cross-stamp comparison catches the discontinuity.

## Remediation

The parent's pid read is consolidated into ONE injected supplier (`_pid`), so
the semantic "the observed identity is not the parent's own" is still covered
but asserts a STABLE IDENTITY rather than a number the OS chose. The invariant
the pid stood in for is now asserted directly, by the transport's OWN two stamp
sites:

- `test_real_subprocess_streams_progress_and_result` asserts the identity the
  WORKER stamps (progress line) is the identity the RESULT line carries
  (`metrics["pid"] == result["second_pid"]`) AND that it differs from the
  parent's own (`!= _pid()`) — the isolation property the transport promises
  when it spawns its own interpreter.
- the new `test_transport_reports_one_worker_identity_across_the_run` drives
  the transport with a FIXED identity (`_FIXED_ID`), so the continuity
  invariant is reproducible to the digit and independent of the OS's process
  assignment; the isolation leg (`_FIXED_ID != _pid()`) cross-checks the
  real-pid leg, so neither passes alone.
- both tests source their worker from ONE `_identity_fixture(root, fixed)`
  helper that stamps the SAME expression (`stamp`) on both transport lines from
  ONE source — two separate stamp expressions could drift, which is exactly
  the regression the old parent-pid assert could not catch.

## OWNERSHIP_SCOPE

- `tests/unit/test_training_env_worker.py` (remediated, 20 -> 22 tests)
- `tests/unit/test_ml_qa_017_worker_identity_determinism.py` (new, 13 tests)
- `tests/critical_suite.txt` (manifest entry for the new battery, 505 -> 506)
- `docs/ml-system/test_determinism_roster.md` (recount row update)
- `docs/ml-system/TASK_BOARD.md` + `docs/ml-system/06_TASK_LEDGER.md` (status)
- `agents/taskboard.md` (task row)
- `docs/agent_handoffs/2026-09-26_AGENT-QA_ML-QA-017.md` (this report)

## FORBIDDEN_CHANGES

- No production file. The worker transport (`run_training_worker` ->
  `subprocess.Popen` -> the stdio JSON protocol, the gate re-check, the cancel
  path and the grace kill) is byte-for-byte unchanged; only the test's
  *expression* of the identity contract changed.
- The 14 durable contract tests must survive the pass with names and hard
  asserts unchanged (the interpreter-gate, publish-forbid, cancel-streaming,
  grace-kill, malformed-worker, install-honouring and parent-verification
  legs).
- No `re` import in the textual battery (a shadowing `re` module ahead of
  stdlib on `sys.path` can drop a negative lookahead and INVERT a rule). The
  tokenize-based `_code_lines` extractor is used instead, which excludes
  string-literal interiors explicitly — the module's ML-QA-017 header block
  and the battery's own docstring both name `os.getpid()` while documenting
  the removed defect, exactly how these regressions stay explained.

## ACCEPTANCE_CRITERIA

- [x] Zero live `os.getpid()` calls in the two identity tests' executable lines
- [x] Exactly one `os.getpid()` read in the whole module, inside the supplier
      (the fixture builder's stamp selector is a STRING LITERAL on a code row,
      not a call — see the battery's `_call_spans`, which is token-based for
      exactly this reason)
- [x] The continuity invariant asserted between the transport's own two stamp
      sites, never against the parent's pid literal
- [x] No `import os` in an identity test body (the read goes through the
      module-level supplier)
- [x] All 14 durable contract tests preserved, names and hard asserts unchanged
- [x] Zero production files changed
- [x] 13-test contract battery pins the shape (manifest-registered)
- [x] Negative control: the battery FAILS on the pre-remediation module
      (9 failed / 4 passed; restore confirmed — 1 `os.getpid()` at line 41
      inside the supplier, 0 in test bodies)

## Verification evidence

- `tests/unit/test_training_env_worker.py`: 21 passed (22 tests; the 22nd,
  `test_pipeline_forwards_backend_and_honest_stage_events`, needs torch and
  fails in the slim venv on this host — a pre-existing environment fact, not a
  defect: baseline at origin/main shows the identical failure)
- `tests/unit/test_ml_qa_017_worker_identity_determinism.py`: 13 passed
- Combined 32 passed, 1 pre-existing env failure (slim venv:
  `/tmp/nse-slim/bin/python`, `PYTHONPATH=src:.`)
- Negative control on the pre-remediation text: 9 failed / 4 passed, then the
  fixed module was restored and re-verified green. The 4 non-failing rules are
  the behavioural-legs helper wrappers plus the durable-names and manifest
  rules; all 9 textual + transport-behaviour rules fail on the old text.
- `ruff check`: All checks passed; `ruff format`: 2 files already formatted;
  `mypy`: Success, no issues found in 2 source files
- `scripts/ci/verify_critical_suite_manifest.py`:
  CRITICAL_SUITE_MANIFEST_OK: 254 paths all exist
- `scripts/ci/check_merge_marker_residue.py`: clean (3744/3782 scanned)
- `scripts/ci/check_duplicate_task_rows.py`: 0 duplicate rows
- `scripts/ci/check_dependency_drift.py`: OK, 100 pins
