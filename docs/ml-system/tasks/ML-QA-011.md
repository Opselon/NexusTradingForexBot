# ML-QA-011 — Push-Gate Wall-Clock Determinism: Shadow70 Safety Suite

- **Status**: DONE (PR pending)
- **Owner**: AGENT-QA (NSE Autonomous ML Specialist Swarm)
- **Priority**: P2
- **Stream**: L (CI/CD & Verification)
- **Dependencies**: ML-QA-004 (DONE, PR #402), ML-QA-003 census (DONE), ML-QA-010 (DONE, PR #432)
- **Human decision required**: NO
- **Parallel-safe**: YES (test-only; no production path under `src/` touched)

## Objective

Remove the second-largest wall-clock exposure from the push gate —
`tests/unit/test_shadow70_safety.py`, 8 live nondeterminism sources per the
ML-QA-003 census recount (6 `datetime.now(UTC)` arguments to
`Shadow70Runtime.observe()`, 1 `tempfile.mkdtemp()`, 1 real worker thread) —
without weakening any TEST-SHADOW-36..40 champion-protection or 70D-shadow
safety contract.

## Why the wall clock was a real defect here

The timestamp is an **input** the caller supplies to `observe()`, never a
magnitude a test measures. Reading the wall clock per call therefore carried
no information, but broke two things:

1. **Read drift → unprovable idempotency.** Spec 13 derives the
   `observation_id` as `sha256(snapshot_id|model_id|model_version|ts)`. Six
   independent `datetime.now(UTC)` reads meant an observation and its retry
   derived *different* ids whenever the reads straddled a clock tick. The
   spec 13/14 `INSERT OR IGNORE` idempotency contract was consequently
   **unprovable**, not merely unproven — a retry could land a duplicate row
   purely because of when the scheduler ran the two calls.
2. **Date boundary.** A scenario reading `now` at 23:59:59 UTC and asserting
   on a timestamp derived from it crossed a day/week boundary whenever the
   scheduler stalled between setup and assertion, with zero change in the
   code under test.

The `mkdtemp` scratch directory also leaked whenever an early assert aborted
the fixture generator before its `shutil.rmtree` — a real disk cost across a
779-test suite on a 3.9 GB host.

## Remediation (test-only; zero production code)

- The 6 per-call `datetime.now(UTC)` stamps read **one** module-level frozen
  instant (`_FIXED_NOW = datetime.now(UTC)`, captured at import) through
  `_now()`. All observations in a scenario share one instant, which is what
  spec 13's deterministic identity assumes.
- The instant is **captured, not hardcoded** as a calendar date: the runtime
  freshness gate (`_validate_vector` in
  `src/nexus_scalp/shadow/shadow70/runtime.py`) still compares the supplied
  timestamp against the real clock with a 300 s budget
  (`FEATURE_FRESHNESS_SEC`), so a hardcoded date would age out and silently
  flip every scenario to `SHADOW_STALE_FEATURES`. One captured read has
  neither that defect nor the flake class.
- `tmp_artifacts` became the pytest `tmp_path` fixture.
- The TEST-SHADOW-40 persistence wait gained a `budget_cpu_ms(4000.0)`
  CPU-time bound around the poll loop (the shared helper ML-QA-004/007/008/
  009/010 standardised on). The hard `n == 60` row-count contract is
  untouched; the real thread (the worker under test) stays, because a real
  thread is required to prove async persistence.

## What was NOT weakened

All five durable TEST-SHADOW-36..40 tests are preserved by name with their
load-bearing asserts byte-identical: champion preservation (36), zero broker
interaction over 2000 inferences (37), failure-cascade isolation staying
READY (38), buffer bounds 2000/500 (39), 60 rows persisted (40).

Two **new** proofs became possible only under the fixed clock:

- TEST-SHADOW-37 replay derives the **same** `observation_id` as the original
  (spec 13 idempotency — unprovable under six wall-clock reads).
- TEST-SHADOW-40b persists rows carrying the fixed instant and proves a
  replay cannot duplicate a row (3 rows survive a retry, `INSERT OR IGNORE`).

## Acceptance criteria

- [x] No per-call wall-clock read remains; exactly one read exists and it is
      the `_FIXED_NOW` capture.
- [x] Every `observe()` site reads the frozen instant via `_now()`.
- [x] `tempfile` / `mkdtemp` / `shutil` removed from the module.
- [x] All five TEST-SHADOW-36..40 tests preserved by name with hard asserts.
- [x] The persistence wait is bounded on CPU time, not the wall clock.
- [x] Spec 13/14 retry idempotency is proven (two new tests).
- [x] Contract battery `tests/unit/test_ml_qa_011_shadow70_clock_determinism.py`
      registered in `tests/critical_suite.txt` and passing.
- [x] Negative control: the battery fails on the pre-remediation module text
      (8 rules fail) and passes on the remediated text.
- [x] No production file under `src/` modified (runtime/store/worker/models/
      fixtures/chain_clock all byte-identical to `origin/main`).
- [x] Gates: ruff check + format clean, mypy Success, manifest 243 paths OK,
      merge-marker residue clean, duplicate rows OK, dependency drift OK,
      `check_docs.py` DOCS_HEALTH = PASS.

## Verification evidence

- Branch `agent/qa/shadow70-clock`, Python 3.11.16, **hermes venv** (the only
  interpreter of the three that collects the shadow70 import chain).
- Baseline: 5 tests passed before the change (already green — this is a
  determinism remediation, not a bug fix).
- After: module 8/8 pass; module + battery **27/27 pass**.
- **Negative control**: restored the pre-remediation module text from
  `origin/main` → battery **8 FAILED / 15 passed**
  (`test_no_wall_clock_now_in_module_source`,
  `test_single_frozen_instant_defined`, `test_now_helper_is_the_clock_source`,
  `test_tempfile_is_gone`, `test_shadow36_asserts_unchanged`,
  `test_shadow37_asserts_unchanged`, `test_shadow40_budget_uses_cpu_time`,
  `test_replay_idempotency_proof_exists`). Restored the fix → 27 pass;
  restore confirmed by grep (`timestamp=_now()` × 8,
  `timestamp=datetime.now` × 0).
- ruff check: `All checks passed!`; ruff format --check: 2 files formatted.
- mypy: `Success: no issues found in 1 source file`.
- `verify_critical_suite_manifest.py`: `CRITICAL_SUITE_MANIFEST_OK: 243
  paths all exist`.
- `check_merge_marker_residue.py`: clean (3621/3659 tracked files).
- `check_duplicate_task_rows.py`: `DUPLICATE_TASK_ROWS_OK`.
- `check_dependency_drift.py`: OK (100 pins, lock untouched).
- `scripts/docs/check_docs.py`: `DOCS_HEALTH = PASS`.

## Ownership scope / forbidden changes

- **Scope**: `tests/unit/test_shadow70_safety.py`,
  `tests/unit/test_ml_qa_011_shadow70_clock_determinism.py`,
  `tests/critical_suite.txt`, `docs/ml-system/{06_TASK_LEDGER.md,
  TASK_BOARD.md, test_determinism_roster.md}`,
  `docs/agent_handoffs/2026-09-25_AGENT-QA_ML-QA-011.md`,
  `agents/taskboard.md`, `docs/ml-system/tasks/ML-QA-011.md`.
- **Forbidden**: any production code under `src/` (the runtime already
  accepts `timestamp` as a caller keyword — no seam was added or needed);
  `.github/workflows/*`; RemoteMT5GatewayAdapter and its client contract;
  frozen domain models; the shared `tests/helpers/shadow70_fixtures.py` and
  `tests/e2e/chain_clock.py` helpers (imported read-only).
