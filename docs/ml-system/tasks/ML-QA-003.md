# ML-QA-003 — Test Determinism Audit: Flaky-Source Census + Manifest Risk Roster

- **STATUS:** READY
- **AGENT_ROLE:** AGENT-QA
- **Priority:** P2
- **Wave:** 5 (post-board-completion hardening cycle)
- **DEPENDENCIES:** ML-CI-002 (DONE)
- **Parallel class:** PARALLEL_SAFE
- **OWNERSHIP_SCOPE:** `docs/ml-system/`, `docs/agent_handoffs/`, `agents/taskboard.md`
- **HUMAN_DECISION_REQUIRED:** NO

## Background

The 30-task ML board is complete: 24 DONE, 6 held at human-decision or
upstream-blocked gates. The swarm's remaining unblocked work is CI/CD and
verification hardening (Stream L).

Run 34's BUG-308C cycle exposed the exact failure class this task targets:
a real `Code Quality` gate went red, the evidence was artifact-only
(`gh run view --log-failed` returned empty), and the fix path required
downloading the `ci-results-*` artifact to read `pytest/pytest.txt`.
Delays and misattributions in that loop are what a determinism census
prevents.

## Objective

Produce the project's first complete census of non-determinism sources in
the test tree, cross-referenced against `tests/critical_suite.txt`, so that:

1. A reviewer triaging a red `Code Quality` gate can tell in seconds whether
   a failing test is one of the known flaky shapes (timing/thread/temp)
   rather than a real regression.
2. Test authors adding a `perf_counter`/wall-clock/threaded test to the push
   gate have a documented, checked pattern to follow (seed/freeze/inject).
3. The push-gate manifest's flaky exposure is quantified — how many of the
   manifest entries carry a timing or thread source today.

The canonical shapes this census must classify (each is a proven CI-red
class on this repo, from `references/audit-method.md` and the skill's
Pitfalls):

- **`perf_counter`/`monotonic` timing asserts** comparing wall-clock between
  two legs — trips on co-tenant CI runners. Preferred fix: inject the clock
  (a `now` callable or a monotonic knob), assert the *relationship* the code
  guarantees (ordering, sentinel, expiry), not the elapsed magnitude.
- **Wall-clock `datetime.now()`/`utcnow()` asserts** — breaks at timezone or
  date boundaries. Preferred fix: inject/patch the clock.
- **`os.getpid()` asserts** — breaks across process boundaries (fork,
  subprocess, hot-reload). Preferred fix: assert the invariant the pid
  proves (liveness, ownership) rather than its literal value.
- **Unseeded `random`** — non-reproducible failures. Preferred fix: seed.
- **`tempfile.mkdtemp` / `NamedTemporaryFile`** — untracked cleanup, and on
  macOS the temp path can be a randomly-generated symlink target. Preferred
  fix: `tmp_path` fixture.
- **Threads / process pools** — timing-sensitive joins; `Event`/`set()` are
  not nondeterminism sources themselves (they are the deterministic
  *synchronizers*), so this census must distinguish synchronization
  primitives from genuine race exercisers.

## Acceptance Criteria

- [x] AC-1: A scanner exists that enumerates every test file under `tests/`
  containing a non-determinism source, classifies the source by canonical
  shape, and emits `file:line` evidence for the first occurrence of each
  shape per file. It runs from a clean checkout with stdlib only (no torch,
  no polars, no repo import) so it works in the slim Linux venv.
- [x] AC-2: The scanner cross-references each flagged file against
  `tests/critical_suite.txt` and reports which flagged files are in the push
  gate (the flaky-exposure number) versus which run only in the
  nightly/extended lanes.
- [x] AC-3: A roster file is committed at
  `docs/ml-system/test_determinism_roster.md` carrying: the census method,
  the canonical shape taxonomy with the preferred fix for each, the full
  flagged-file table (in-gate vs out-of-gate, per-shape counts, first
  `file:line` evidence), the top remediation candidates ranked by
  in-gate×source-count, and the triage recipe (artifact download path,
  `--log-failed` empty signal, serial-rerun confirmation).
- [x] AC-4: The roster documents the `set()` / `Event()` false-positive
  guard: synchronization primitives are NOT non-determinism sources; the
  census counts only genuine sources.
- [x] AC-5: `scripts/ci/check_workflows.py` reports 0 ERROR / 0 WARNING /
  0 INFO (no workflow changed), `tests/critical_suite.txt` is untouched, and
  `scripts/ci/verify_critical_suite_manifest.py` still reports all manifest
  paths exist.
- [x] AC-6: Repo-wide `ruff check` and `ruff format --check` are clean;
  `check_dependency_drift.py` reports OK (no lock touched).
- [x] AC-7: Handoff report `docs/agent_handoffs/YYYY-MM-DD_AGENT-QA_ML-QA-003.md`
  written with the census numbers and the top remediation candidates.

## Verification Evidence (run 35, 2026-09-23)

- AC-1/AC-2: scanner at `artifacts/` (not committed — stdlib-only, runs in
  the slim venv; the committed roster embeds its output). 611 test files
  scanned; 123 carry ≥1 source. `grep` confirmation of the headline numbers
  is recorded in the handoff report.
- AC-3: `docs/ml-system/test_determinism_roster.md` committed.
- AC-4: dedicated "Synchronization primitives are not nondeterminism"
  section in the roster.
- AC-5: `scripts/ci/check_workflows.py` 0/0/0 (no workflow touched);
  `verify_critical_suite_manifest.py` OK; `tests/critical_suite.txt`
  byte-identical to `origin/main`.
- AC-6: `ruff check` clean repo-wide; `ruff format --check` clean;
  `check_dependency_drift.py` OK (99 pins, lock untouched).
- AC-7: `docs/agent_handoffs/2026-09-23_AGENT-QA_ML-QA-003.md` committed.

## NON_GOALS

- Fixing the flagged tests (separate remediation tasks; the roster ranks the
  candidates so an operator or a later cycle can pick them off one at a
  time).
- Adding any test file to the critical-suite manifest.
- Changing `pyproject.toml`, `requirements.lock`, or any workflow.
