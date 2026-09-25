# ML-QA-014 — Push-Gate CPU-Time Determinism: Causal-TCN Latency Suite

- **Status:** DONE (PR pending)
- **Agent:** AGENT-QA
- **Stream:** L — CI/CD & Verification
- **Priority:** P2
- **Dependencies:** ML-QA-004, ML-QA-007, ML-QA-008, ML-QA-009, ML-QA-012
- **Human decision required:** NO
- **Parallel-safe:** YES (test-only; the only touched module is owned by this task)

## Objective

Remove the four live `time.perf_counter()` (wall-clock) reads from
`tests/unit/test_causal_conv_invariants.py` — roster recount candidate #4 by
live source count (`docs/ml-system/test_determinism_roster.md` §6) — and make
the two latency benchmark tests measure CPU time via the repo's shared
`budget_cpu_ms` stopwatch, so a co-tenant-loaded 2-core CI runner cannot trip a
budget the runner's scheduler is responsible for rather than the model.

## Background — the defect class

`TestLatencyAcrossReceptiveFields` is a benchmark: it asserts a per-forward-pass
budget and that cost does not blow up as the receptive field deepens (a
structural guard against a padding defect that grows the sequence instead of
dilating). Both measured loops used `time.perf_counter()`.

Wall clock is the wrong clock for a *measurement* on a shared runner: the suite
runs on 2-core runners alongside co-tenant jobs, and a stalled runner slows the
wall clock with **zero change in the code under test**, so the budget trips on
the scheduler rather than the model. This is the same class ML-QA-007
(`test_mt5_adapter_parity`), ML-QA-008 (`test_experiment_registry`), ML-QA-009
(`test_audit_flush_contract`), ML-QA-011 (`test_shadow70_safety`) and ML-QA-012
(`test_outcome_flush_race_bug140`) already fixed: `perf_counter` belongs to
*instrumentation*, `process_time` (CPU time) belongs to *measurement*. A red on
only one OS of the matrix is the tell for this shape, not a platform bug.

Two further defects in the original depth bound:

1. **Compound assert waived itself.** The structural bound was
   `assert worst <= best * RATIO or worst < BUDGET` — a compound where the
   second clause silently waives the first, i.e. the very condition the test
   exists to detect (latency blowing up across depths) was dismissible by
   staying under the absolute budget.
2. **Inflated ratio with no honest margin data.** The 25x ratio and 50 ms
   budget were wall-clock figures uncalibrated against actual cost, so neither
   carried a defensible margin.

## Changes

- `tests/unit/test_causal_conv_invariants.py`:
  - `import time` removed; `from tests.e2e.chain_clock import budget_cpu_ms` added.
  - Both measurement loops wrapped in `budget_cpu_ms(...)`; the per-pass figure
    is now `sw.consumed_ms / 20.0` (CPU time), not a `perf_counter` subtraction.
  - `_LATENCY_BUDGET_S` (50e-3 s wall) → `_LATENCY_BUDGET_CPU_MS` (50.0 ms CPU).
  - Ratio `_WORST_BEST_RATIO` 25.0 → 15.0.
  - Warm-up extended 3 → 5 passes (conv kernels + thread pool must be hot
    before the measured leg; otherwise the first passes pay one-time cost the
    budget does not intend to bound).
  - The depth bound is asserted unconditionally (`or` fallback removed).
  - The stability test now uses the class ratio constant, not a magic `20.0`.
- `tests/unit/test_ml_qa_014_causal_conv_cpu_budget.py` (new, 18 tests) —
  contract battery pinning every point above.
- `tests/critical_suite.txt` — battery registered (manifest 250 → 251 paths).

## Why the margins are honest

Measured on a 2-core CPU-only host with torch 2.14.0+cpu over
blocks ∈ {3,4,5,6}:

| schedule | worst/best | worst-case per pass | budget | ratio bound |
|---|---|---|---|---|
| geometric | 1.52x | 0.87 ms | 50 ms (58x) | 15x |
| linear | 1.48x | 0.84 ms | 50 ms (60x) | 15x |
| fibonacci | 1.47x | 0.85 ms | 50 ms (59x) | 15x |

Cost grows ~linearly with block count (dilation skips taps; the work is O(blocks)
at fixed sequence length), so 15x is a ~10x margin over the observed 1.5x while
still orders of magnitude below what a padding defect would cost. The 50 ms CPU
budget is ~60x the observed worst case — enough margin that a loaded runner
cannot trip it, tight enough that the structural blow-up it guards for is
unmissable.

## Acceptance criteria

- [x] No live `time.perf_counter()` / `time.monotonic()` call remains in the
      module (prose references in the class docstring and one comment are
      permitted — the contract is about executed code).
- [x] `import time` removed (no remaining consumer).
- [x] Every latency test measures through `budget_cpu_ms` and reads
      `sw.consumed_ms`.
- [x] Budgets are CPU-time milliseconds with documented, measured margins.
- [x] The depth ratio is asserted unconditionally (no `or` waiver).
- [x] A 5-pass warm-up precedes every measured leg.
- [x] All 29 durable contract tests survive, by name and by hard assert
      (causality Jacobians, receptive-field formula, bit-identical legacy
      compatibility, reference configurations).
- [x] No production file changed (`git diff origin/main..HEAD` on src/ empty).
- [x] Battery registered in `tests/critical_suite.txt` and
      `verify_critical_suite_manifest.py` reports OK.
- [x] Battery fails on the pre-remediation text (negative control:
      7 failed / 11 passed) and passes on the remediated text (18/18).

## Verification evidence

- Module `test_causal_conv_invariants.py`: 131 passed (slim-polars + torch env).
- Battery `test_ml_qa_014_causal_conv_cpu_budget.py`: 18 passed.
- Combined: **149 passed**.
- Negative control: 7 battery rules fail on the pre-remediation file
  (`test_no_wall_clock_call_in_module_source`,
  `test_no_time_import_needed`, `test_cpu_stopwatch_imported_from_shared_helper`,
  `test_latency_tests_use_the_stopwatch`, `test_budgets_are_cpu_time_units`,
  `test_ratio_bound_is_unconditional`, `test_warmup_precedes_every_measured_leg`);
  11 pass. Restore confirmed by grep: live `perf_counter` calls 0 (the 2
  remaining hits are the class docstring at line 491 and a comment at 539).
- `ruff check` clean; `ruff format --check` clean; `mypy` Success (2 files).
- `verify_critical_suite_manifest.py`: 251 paths, OK.
- `check_merge_marker_residue.py`: clean (3758 tracked files scanned).
- `check_duplicate_task_rows.py`: OK. `check_dependency_drift.py`: OK (100 pins).

## FORBIDDEN_CHANGES (respected)

- No production file under `src/` modified.
- No change to `tests/e2e/chain_clock.py` (shared helper, owned by ML-QA-004).
- Causality / receptive-field / compatibility tests untouched in substance
  (all 29 names and hard asserts preserved byte-identically).
- No change to `RemoteMT5GatewayAdapter`, workflows, or Telegram secrets.
