# ML-QA-012 — Push-Gate Wall-Clock Determinism: BUG-140 Outcome-Flush Race Suite

## Objective

Remove the 7 live non-determinism sources the ML-QA-003 census
(`docs/ml-system/test_determinism_roster.md` §5/§6) counted in
`tests/unit/test_outcome_flush_race_bug140.py` — 4 `time.monotonic()` timing
probes and 3 `datetime.now(UTC)` wall-clock reads — while keeping every
BUG-140 (read-after-write) and BUG-288 (worker-binding) contract assert
byte-identical. Test-only: zero production code changes.

## Why the wall clock was a real defect here

Two distinct classes, neither of which any assertion needed:

1. **Wall-clock liveness bound (4 `time.monotonic()` probes).** Both poll
   loops bounded *themselves* on the wall clock
   (`deadline = time.monotonic() + N`, polled inside a `time.sleep()` loop).
   That measures how long the OS gave the test, not the code under test: under
   xdist saturation on a 2-core CI runner a co-tenant scheduler stall inflates
   the wait with zero change in the contract, and the bound trips while the
   contract holds. The real invariants were already asserted by the loop's
   exit condition (`row is not None`, `unfinished_tasks == 1`) — i.e. "the
   loop *exits*" is the contract; a hang is caught by the CI-level test
   timeout, never by a wall-clock magnitude.

2. **Read drift (3 `datetime.now(UTC)` reads).** The decision timestamp
   (`_record`) and the two outcome timestamps read the clock independently.
   No assertion compares any of them to `now()` — the clock contributed
   nothing — but independent reads made the suite's own causality window
   scheduler-dependent. The causality guard in `ledger._merge_row`
   (ledger.py:506), `ledger.record_terminal_outcome` (ledger.py:323) and
   `ExperienceIntelligenceEngine.record_trade_outcome` (intelligence.py:756)
   is `outcome_timestamp < decision_timestamp` (**strict**), so an EQUAL pair
   is accepted on the write path and merged on the read path. Under
   independent reads that equality was a coin flip across a clock tick, and a
   date-boundary stall could stamp the outcome in a different day from the
   decision.

No production path compares the supplied timestamps against the real clock
(unlike the shadow70 freshness gate of ML-QA-011), so the frozen instant is a
**fixed calendar value**, not a captured read — nothing would make it age
out.

## Remediation (test-only; zero production code)

`src/` and `tests/e2e/chain_clock.py` are byte-identical to `origin/main`
(pinned by battery rule `test_no_production_source_changed_for_this`).

- One module-level frozen instant (`_FIXED_NOW = datetime(2026, 9, 25, ...)`)
  reached through a single `_now()` supplier; all 3 former `datetime.now(UTC)`
  sites read it (`ts = _now()` in `_record`, `outcome_timestamp=_now()` x2).
- Both poll loops bounded on **CPU time**: `budget_cpu_ms(...)` (the shared
  `tests/e2e/chain_clock` helper ML-QA-004/007/008/009/010/011 standardised
  on) around the loop, an inner `time.process_time()` bound as the fail-fast
  exit, and `sw.consumed_ms < limit` asserted. The removed `time.sleep()`
  sleeps are gone.
- All 6 contract tests kept their names, structure and hard asserts.

## What was NOT weakened

- `assert repo.flush(timeout_sec=5.0) is True` — unchanged.
- `assert row is not None` (read-after-write durability) — unchanged.
- `ok is True` + `merged.realized_r_multiple == 2.0` (outcome accepted and
  merged) — unchanged.
- `merged.exit_reason == "CANCELED_UNFILLED"` +
  `merged.realized_r_multiple == 0.0` (no fabricated R on a cancel) —
  unchanged.
- `assert result is False` (stalled worker returns instead of hanging) —
  unchanged; boundedness is proven by the return value, never a magnitude.
- `assert stalled_queue.unfinished_tasks == 1` (BUG-288: the worker never
  adopts a rebound queue) — unchanged.
- `test_bug288_handshake_source_pins` (class-level source guard on the
  `.start()` → `ready.wait()` ordering) — untouched.

## Acceptance criteria

- [x] No live `datetime.now(...)` call remains in the module (docstring/comment
  mentions permitted). Verified: `ast`-based `_code_lines` + `_call_spans`
  report 0 spans.
- [x] No `time.monotonic()` / `time.perf_counter()` / `time.sleep()` call
  remains in the module.
- [x] Exactly one frozen instant, reached through one `_now()` supplier; all
  three former clock sites read it.
- [x] Both poll loops bounded on CPU time (`budget_cpu_ms` +
  `time.process_time()` inner bound + `consumed_ms` assert).
- [x] All 6 durable contract tests present with their hard asserts intact.
- [x] Contract battery `tests/unit/test_ml_qa_012_outcome_flush_clock_determinism.py`
      pins every rule above, and is itself registered in
      `tests/critical_suite.txt`.
- [x] Negative control: 9 battery rules FAIL on the pre-remediation module
      text, 15 pass; on the remediated text all 15 pass (21 total with the
      6 contract tests).
- [x] No production or shared-fixture file modified (`src/**`,
      `tests/e2e/chain_clock.py` byte-identical to `origin/main`).
- [x] `ruff check` + `ruff format --check` clean; `mypy` clean;
      `verify_critical_suite_manifest.py` (247 paths),
      `check_merge_marker_residue.py`, `check_duplicate_task_rows.py`,
      `check_dependency_drift.py`, `scripts/docs/check_docs.py` all PASS.
- [x] Related suites unaffected: `test_audit_flush_contract.py` +
      `test_ml_qa_009_audit_flush_cpu_budget.py` (14) and
      `test_ml_qa_011_shadow70_clock_determinism.py` (21) still green.

## Verification evidence

- `tests/unit/test_outcome_flush_race_bug140.py`: **6 passed** (slim venv,
  `-p no:randomly` and default order).
- `tests/unit/test_ml_qa_012_outcome_flush_clock_determinism.py`: **15 passed**.
- Negative control (module reverted to `origin/main` text): **9 failed /
  6 passed**; restore confirmed by grep (`datetime.now(UTC)` x0 live,
  `time.monotonic` x0 live, `_now()` x4).
- `ruff check`: All checks passed. `ruff format --check`: 2 files already
  formatted. `mypy`: Success: no issues found in 2 source files.
- `CRITICAL_SUITE_MANIFEST_OK: 247 paths all exist`.
- `Merge-marker residue clean`. `DUPLICATE_TASK_ROWS_OK: 0 duplicate rows`.
- `dependency drift check: OK - 100 pins`. `DOCS_HEALTH = PASS`.

## Ownership scope / forbidden changes

- OWNERSHIP_SCOPE: `tests/unit/test_outcome_flush_race_bug140.py`,
  `tests/unit/test_ml_qa_012_outcome_flush_clock_determinism.py`,
  `tests/critical_suite.txt`, this task file, `docs/ml-system/TASK_BOARD.md`,
  `docs/ml-system/06_TASK_LEDGER.md`, `agents/taskboard.md`,
  `docs/agent_handoffs/`, `docs/ml-system/test_determinism_roster.md`.
- FORBIDDEN_CHANGES: any file under `src/`, `tests/e2e/chain_clock.py`,
  `.github/workflows/*`, the RemoteMT5GatewayAdapter contract, frozen domain
  models, and any change to the 6 durable contract tests' hard asserts.
- STATUS: DONE (PR pending)
