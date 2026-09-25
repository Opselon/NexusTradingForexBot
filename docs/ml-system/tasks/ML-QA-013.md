# ML-QA-013 — Push-Gate Monotonic-Clock Determinism: BUG-285 Overflow-Drain Cadence Suite

## Objective

Remove the 3 live non-determinism sources the ML-QA-003 census
(`docs/ml-system/test_determinism_roster.md` §5/§6) counted in
`tests/unit/test_bug285_overflow_drain.py` — 2 `time.monotonic()` reads plus
1 hand-written stamp derived from the same clock — while keeping every
BUG-285 (overflow recovery) contract assert byte-identical, and WITHOUT
changing production behaviour on the runtime path.

## Why the monotonic clock was a real defect here

The production throttle compares `time.monotonic()` against the stored stamp
over `OVERFLOW_RECOVERY_INTERVAL_SEC` (60s, `audit_repository.py:3272`):

```
now = time.monotonic()
if self._last_overflow_drain is not None and now - self._last_overflow_drain < INTERVAL:
    return
```

The cadence tests simulated the passage of that interval by *writing their own
stamps from the same clock the production code reads*:

```
repo._last_overflow_drain = time.monotonic()                       # "pretend the pass just ran"
repo._last_overflow_drain = time.monotonic() - (INTERVAL + 1)      # "due again"
```

That made the suite's throttle coverage a property of the host's monotonic
*domain* rather than of the comparison under test: whether `(now - stamp)`
landed above or below the interval depended on the host's uptime, not on the
code, and the two boundary edges (exactly-AT vs one-tick-SHORT-of the
interval) were indistinguishable — the strict `<` means equality is DUE, but a
wall-clock leg could land on either side on scheduler jitter alone. The
comparison is purely between two caller-domain values, so the real clock
contributed nothing except a 60-second wait to exercise the boundary.

This is the same defect class as ML-QA-009/010/011/012 (wall-clock magnitude
asserts / read drift in a push-gate module), in the monotonic domain.

## Remediation (minimal additive production seam)

The drain gained an **optional** argument — the idiom the repo already uses in
`storage/runtime.py::is_due`:

```python
def _drain_financial_overflow_due(self, conn, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
```

With `now is None` the method still reads the real clock, so the single
production caller (the audit worker idle pass) is byte-for-byte unchanged —
it passes no clock argument, pinned by a battery rule.

The cadence tests now drive ONE deterministic `_MonotonicClock` through the
argument (`advance(dt)` instead of sleeping or hand-stamping), so both edges
of the strict `<` boundary are reproducible to the nanosecond without waiting
out a real 60-second interval. The tests no longer write `_last_overflow_drain`
themselves at all — the production method owns that write from the injected
value.

## What was NOT weakened

All 12 durable contract tests are preserved with names + hard asserts
byte-identical:

1. `test_overflow_row_written_by_producer_is_replayed_into_the_ledger`
2. `test_replay_of_a_row_that_already_landed_is_a_noop`
3. `test_first_drain_is_always_due_none_sentinel` (BUG-273 None-sentinel proof)
4. `test_drain_throttled_to_one_pass_per_interval` (now drives the clock)
5. `test_drain_batch_is_bounded`
6. `test_unreplayable_overflow_is_dead_lettered_not_retried`
7. `test_replay_sql_error_dead_letters_and_preserves_other_rows`
8. `test_overflow_writer_cap_routes_row_to_dead_letter`
9. `test_drain_is_wired_into_the_audit_worker_idle_pass` (the #1 failure shape —
   a shipped method with no caller — stays pinned)
10. `test_writer_and_drainer_share_one_path_resolution`
11. `test_debug_snapshot_surfaces_recovery_counters`
12. `test_overflow_counter_semantics_are_exact`

The production seam is additive-only: the default argument preserves the exact
runtime arithmetic, and no test-only clock attribute was injected onto the
production class (a wider seam that could mask a real drift bug).

## Acceptance criteria

- [x] No live `time.monotonic()` / `time.perf_counter()` call and no
      `import time` in the module's executable code (docstring/comment mentions
      of the removed defect are permitted and distinguished explicitly).
- [x] One deterministic monotonic-domain clock drives the cadence arithmetic,
      reset per test by a fixture so one test's advance cannot leak into the
      next's boundary arithmetic.
- [x] Both cadence tests take the injected clock and never hand-write
      `_last_overflow_drain`.
- [x] The production seam is the optional-`now` idiom, defaulting to the real
      clock; the one production caller passes no argument; no test-only clock
      attribute was added to the production class.
- [x] Both edges of the strict `<` boundary are exercised (one tick SHORT of
      the interval = still throttled; exactly AT the interval = DUE), proven
      behaviorally on the real repository over a real sqlite file.
- [x] The default-argument path still reads the real clock (executed proof:
      first pass always due under the None sentinel; an immediate second call
      is an idempotent no-op).
- [x] All 12 durable tests preserved with names + hard asserts.
- [x] Contract battery `tests/unit/test_ml_qa_013_overflow_drain_clock_determinism.py`
      registered in `tests/critical_suite.txt`.

## Verification evidence

- `tests/unit/test_bug285_overflow_drain.py`: **16 passed** (slim venv;
  was 14 — the remediation added the boundary-edge test and one more contract
  leg, no test removed or renamed).
- `tests/unit/test_ml_qa_013_overflow_drain_clock_determinism.py`:
  **17 passed** (module + battery = 33 passed).
- Negative control (module reverted to `origin/main` text, 3 live sources):
  **5 failed / 11 passed** (`test_no_wall_clock_call_in_module_source`,
  `test_no_time_import_needed`, `test_deterministic_clock_defined_and_used`,
  `test_cadence_tests_take_the_injected_clock`,
  `test_cadence_tests_never_write_the_stamp_themselves`); restore confirmed by
  grep (live `time.monotonic` x0, `_last_overflow_drain =` x0,
  `now=drain_clock` x6).
- `ruff check`: All checks passed. `ruff format --check`: 3 files already
  formatted. `mypy`: Success: no issues found in 2 source files.
- `CRITICAL_SUITE_MANIFEST_OK: 250 paths all exist`.
- `Merge-marker residue clean`. `DUPLICATE_TASK_ROWS_OK: 0 duplicate rows`.
- `dependency drift check: OK - 100 pins` (pre-existing stale-lock pair
  reverted to `origin/main` byte-identical; not caused by this change).
- `DOCS_HEALTH = PASS` (`scripts/docs/check_docs.py`).
- Related suites unaffected: `test_audit_flush_contract` +
  ML-QA-011 battery (21) + ML-QA-012 battery (21) = **42 passed**.

## Ownership scope / forbidden changes

- OWNERSHIP_SCOPE: `src/nexus_scalp/adapters/database/audit_repository.py`
  (the optional-`now` argument only), `tests/unit/test_bug285_overflow_drain.py`,
  `tests/unit/test_ml_qa_013_overflow_drain_clock_determinism.py`,
  `tests/critical_suite.txt`, this task file, `docs/ml-system/TASK_BOARD.md`,
  `docs/ml-system/06_TASK_LEDGER.md`, `agents/taskboard.md`,
  `docs/agent_handoffs/`, `docs/ml-system/test_determinism_roster.md`.
- FORBIDDEN_CHANGES: any change to the production drain's throttle arithmetic
  beyond the optional argument; any change to the single production caller;
  `.github/workflows/*`, the RemoteMT5GatewayAdapter contract, frozen domain
  models, and any change to the 12 durable contract tests' hard asserts.
- STATUS: DONE (PR pending)
