# ML-QA-015 — Hygiene-cadence suite wall-clock determinism

- **Status**: DONE (this file records the completed work)
- **Agent**: AGENT-QA
- **Stream**: L (CI/CD & Verification)
- **Priority**: P2
- **Wave**: QA determinism roster continuation
- **Task type**: test-only determinism remediation (zero production files changed)

## Objective

`tests/unit/test_bug275_hygiene_cadence_clock.py` (push-gate module,
`tests/critical_suite.txt:314`) was listed by the ML-QA-003 determinism census
(`docs/ml-system/test_determinism_roster.md` section 6, recount row 15) as 3
live wall-clock sources. Remove the wall-clock dependence so the cadence
contract it pins is a property of the code, not of when the run happens.

## The defect class

The production cadence gates compare two caller-domain values:
`(now - stamp) >= interval`. The caller (`MaintenanceCycle.run_cycle`,
`src/nexus_scalp/application/live/maintenance.py:115`) supplies a wall
`now_t = time.time()`; `run_cycle` stamps `_last_light` / `_last_deep` /
`_last_telegram` from `time.time()`
(`src/nexus_scalp/hygiene/hygiene_runtime.py:235`, `:237`, `:397`).

The tests *drove* that same clock with three live `time.time()` reads, so every
stamp and every probe was a fresh real-clock read. Two consequences:

1. The boundary itself was untested. The deep interval is 6 hours; the suite
   could only ever observe the NOT-due side of it, because reaching the due
   side needed a real 6-hour wait. Whether the `>=` comparison was inclusive,
   off-by-one, or compared the wrong pair of values was invisible to the suite.
2. The coverage was time-dependent. The assertions compared freshly-stamped
   values against freshly-read ones, so a slow CI runner or a clock tick during
   the run could move which side of a boundary a test sat on.

## Remediation

One deterministic wall clock, injected through the production module's own
`time` reference: a `hygiene_clock` monkeypatch fixture swaps
`hygiene_runtime.time` for a stand-in whose `time` reads a controllable epoch
(`_WALL_CLOCK`, `_WALL_EPOCH = 1_900_000_000.0`), keeping `monotonic` real so
`run_cycle`'s duration timer still measures elapsed time.

Why the epoch rather than 0.0: the production domain is WALL (epoch ~1.79e9)
and the BUG-275 defect was specifically a monotonic stamp (~uptime, ~1e6)
compared against a wall caller. A 0.0-based fake would make a monotonic-domain
regression and a correct wall stamp both land "near zero" and pass; at 1.9e9
the two are distinguishable, so the domain invariant the suite exists to pin
stays observable.

The clock is swapped on the MODULE's `time` reference, not on a scheduler
instance attribute — `run_cycle` calls the module-global `time.time()`, so an
instance-level `sched._time` injection is silently inert (this exact authoring
mistake was caught by the first run of this task: the suite went green while
exercising nothing). Pinned by a textual battery rule.

The cadence tests then advance the clock instead of waiting, so BOTH edges of
the inclusive `>=` boundary are reproducible to the nanosecond for all three
gates: one tick short of the interval (NOT due) and exactly AT the interval
(DUE — equality satisfies `>=`).

## OWNERSHIP_SCOPE

- `tests/unit/test_bug275_hygiene_cadence_clock.py` (rewritten, 5 -> 12 tests)
- `tests/unit/test_ml_qa_015_hygiene_cadence_clock_determinism.py` (new, 15 tests)
- `tests/critical_suite.txt` (manifest entry for the new battery)
- `docs/ml-system/test_determinism_roster.md` (recount row update)

## FORBIDDEN_CHANGES

- No production file. The scheduler is clock-INJECTED, not patched in place;
  the cadence arithmetic compares caller-domain values either way. Pinned by a
  battery rule that greps the production contract verbatim
  (`is_light_due` / `is_deep_due` / `is_telegram_due` signatures, the stamp
  sites, and the absence of monotonic stamps).
- The five durable contract tests (names + their hard asserts) must survive the
  pass byte-for-byte, including the md7-class wiring pin
  (`test_maintenance_caller_uses_wall_now_for_hygiene_gates`), which pins the
  engine-side caller's `now_t = time.time()` + `is_deep_due(now_t)` wiring and
  the absence of monotonic stamps in the scheduler source.
- No `re` import in the textual battery (a shadowing `re` module ahead of
  stdlib on `sys.path` can drop a negative lookahead and INVERT a rule). The
  tokenize-based `_code_lines` extractor is used instead, which classifies a
  physical line by its first non-NL token and excludes string-literal interiors
  explicitly (the module docstrings name `time.time()` while documenting the
  removed defect — exactly how these regressions stay explained).

## ACCEPTANCE_CRITERIA

- [x] Zero live `time.time()` / `time.monotonic()` / `time.perf_counter()` calls
      in the cadence tests' executable lines
- [x] One deterministic wall clock drives every cadence test (no per-test reads)
- [x] Both edges of the inclusive `>=` boundary exercised for light, deep and
      telegram gates (NOT-due one tick short, DUE exactly at the interval)
- [x] All 5 durable contract tests preserved, names and hard asserts unchanged
- [x] Zero production files changed
- [x] 15-test contract battery pins the shape (manifest-registered)
- [x] Negative control: the battery FAILS on the pre-remediation module
      (9 failed / 6 passed; restore confirmed by grep — 10 `time.time()` text
      hits back, 31 `hygiene_clock` references gone)
- [x] ruff check + ruff format clean; mypy clean; manifest 251 -> 252 paths;
      merge-marker residue, duplicate task rows and dependency drift all PASS

## Verification evidence

- `tests/unit/test_bug275_hygiene_cadence_clock.py`: 12 passed
- `tests/unit/test_ml_qa_015_hygiene_cadence_clock_determinism.py`: 15 passed
- Combined 26 passed (slim venv: `/tmp/nse-slim/bin/python`,
  `PYTHONPATH=src:.`)
- Negative control on the pre-remediation text: 9 failed / 6 passed, then the
  fixed module was restored and re-verified green
- Related suites unaffected: ML-QA-013 battery + ML-QA-014 battery +
  `test_bug285_overflow_drain.py` = 49 passed, 2 skipped (torch-absent skips,
  a pre-existing environment fact — `test_causal_conv_invariants.py` needs the
  torch+polars combined interpreter)
- `ruff check`: All checks passed; `ruff format --check`: 2 files formatted;
  `mypy`: Success, no issues found in 2 source files
- `scripts/ci/verify_critical_suite_manifest.py`:
  CRITICAL_SUITE_MANIFEST_OK: 252 paths all exist
- `scripts/ci/check_merge_marker_residue.py`: clean
- `scripts/ci/check_duplicate_task_rows.py`: 0 duplicate rows
- `scripts/ci/check_dependency_drift.py`: OK, 100 pins
