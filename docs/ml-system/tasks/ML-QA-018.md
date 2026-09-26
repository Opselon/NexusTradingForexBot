# ML-QA-018 — Benchmark Wall-Clock → CPU-Time Determinism (final two roster rows)

- **Status:** DONE
- **Agent role:** AGENT-QA (Stream L — CI/CD & Verification)
- **Task type:** test-only determinism remediation (zero production files changed)
- **PR:** #490
- **Started:** 2026-09-26
- **Depends on:** ML-QA-003 (determinism census), ML-QA-007..017 (the clock-swap
  remediation series this task closes out)

## Objective

Close the last two OPEN rows in the ML-QA-003 determinism-census recount
(`docs/ml-system/test_determinism_roster.md` section 6) by converting the two
remaining wall-clock benchmark asserts in the push gate from
`time.perf_counter()` (wall clock) to `time.process_time()` (CPU time) via the
shared `budget_cpu_ms` stopwatch, with budgets calibrated against measured
cost rather than ported from the wall-clock figures.

## Roster rows closed

| Module | Live sources | Defect |
| --- | --- | --- |
| `tests/unit/test_sample_weights.py` | 2 `time.perf_counter()` | 50k-row SLA asserted on wall clock |
| `tests/unit/test_research_edge_hardening_20260909.py` | 2 `time.perf_counter()` | sized-path linearity asserted on wall clock, with an additive waiver |

## The defect (both modules)

Both are *benchmarks*: one asserts a 50,000-sample uniqueness computation stays
inside a budget, the other asserts the sized economic re-valuation stays linear
in the sample count. Both measured `time.perf_counter()` (wall clock) around
the measured body.

Wall clock is the wrong clock for a budget on a shared CI runner. The suite
runs on 2-core runners alongside co-tenant jobs; a stalled runner slows the
wall clock with **zero change in the code under test**, and the budget trips.
This is the same defect class ML-QA-007 (`test_mt5_adapter_parity`),
ML-QA-008 (`test_experiment_registry`), ML-QA-009 (`test_audit_flush_contract`),
ML-QA-011 (`test_shadow70_safety`), ML-QA-012 (`test_outcome_flush_race`) and
ML-QA-014 (`test_causal_conv_invariants`) already fixed:
`time.perf_counter()` belongs to the *instrumentation* concern,
`time.process_time()` (CPU time) belongs to the *measurement* concern. A red on
only one OS of the matrix is the tell for this shape, not a platform bug.

A second, independent defect in the research module: the linearity assert was
`big < small * 8 + 1.0` — the `+ 1.0` additive pad on the small leg is a
**waiver** (the same class as the ML-QA-014 compound `assert A or B`). At
`n=1000` the whole measured leg costs ~10 ms, so a one-second pad swamps the
ratio term exactly where the O(n^2) regression it guards for (a per-trade scan
over the growing equity history) would show up first. The ratio was a dead
clause in the small-N region and the test carried no linearity guarantee there.

## Remediation

Test-only; zero production files changed (pinned by an executed `git diff
origin/main..HEAD` battery rule). Both measured bodies are wrapped in the
shared `budget_cpu_ms` stopwatch from `tests/e2e/chain_clock.py`, which reads
`time.process_time()`; the figure comes from `sw.consumed_ms`. Both modules'
`import time` is gone (the only consumers were the removed probes — a
surviving import is dead weight and a latent reintroduction; asserted by an
AST-level rule).

The budgets moved **with the clock**, calibrated against measured cost on this
2-core CPU-only host (`benchmark_margin_calibration_recipe`) rather than ported
from the wall-clock figures:

| Benchmark | Measured (CPU) | New budget | Margin | Old (wall) |
| --- | --- | --- | --- | --- |
| uniqueness 50k rows | 0.87 ms | 500 ms | ~570x | 2000 ms (~2300x) |
| sized path 4000/1000 ratio | 4.20x (per-sample 0.0108 ms — constant, i.e. genuinely linear) | 6.0 | ~1.4x over the true 4.20x factor | `* 8 + 1.0` |

The sized-path per-sample cost measured constant at n=1000/2000/4000/8000, so
the ratio bound is a real structural invariant (4x data costs ~4x CPU time by
construction) and the battery adds a per-unit-cost leg asserting it directly —
the sharpest form, since a ratio can absorb noise in the small leg and a
per-unit cost cannot.

The `+ 1.0` waiver is removed; the ratio is asserted unconditionally.

## Acceptance criteria

- [x] No live `time.perf_counter()` / `time.monotonic()` call remains in either
      module (token-typed call-shape rule over executable lines; docstrings
      naming the removed clock are permitted prose)
- [x] No `import time` remains in either module (AST-level rule; the only
      consumers were the removed probes)
- [x] No direct `time.process_time()` read — `budget_cpu_ms` is the single
      measurement seam
- [x] Both benchmarks read `sw.consumed_ms` from the shared stopwatch
- [x] Budget constants are CPU-time units, calibrated against measured cost
- [x] The `+ 1.0` additive waiver is gone from the linearity assert's
      executable body
- [x] All durable contract tests survive, by name (21 in the weights module,
      10 in the research module)
- [x] Zero production files changed (executed `git diff` rule)
- [x] Contract battery registered in `tests/critical_suite.txt` and verified by
      `scripts/ci/verify_critical_suite_manifest.py`

## Verification evidence

Interpreter: `/tmp/nse-venv/bin/python` (torch 2.14.0+cpu) with the slim
venv's site-packages on `PYTHONPATH` for polars — no single venv here has both
(the pre-existing `torch_polars_combo` environment fact). ruff/mypy/gate
scripts run under `/tmp/nse-slim/bin/python`.

- **Analysed modules:** `test_sample_weights.py` 22 passed +
  `test_research_edge_hardening_20260909.py` 11 passed = **33 passed**.
- **Contract battery `tests/unit/test_ml_qa_018_benchmark_cpu_budget.py`:**
  **53 passed** (parametrized: 10 wall-clock/stopwatch/import rules x2 modules
  + 2 stopwatch-shape rules + 2 budget-constant rules + 2 unconditional-ratio
  rules + 31 durable-name rules + 3 behavioral legs + manifest + no-diff).
- **Negative control** (both modules reverted to their `origin/main` text in
  the worktree): **13 failed / 40 passed** — the 10 textual rules fail on the
  pre-remediation source AND the 3 behavioral legs fail (they read the budget
  constants out of the analysed modules, which reverted with them). The 40
  passing are the durable-name rules + the chain-clock seam rule + the
  no-production-file rule (correct on either text — the remediation is
  test-only). Restore confirmed by grep: 0 `perf_counter` occurrences in both
  modules post-restore, `import time` absent.
- **Sibling batteries unaffected:** `test_ml_qa_014/016/017` = 0 failures.
- **Gates:** ruff check clean; ruff format clean (2 files auto-formatted, 1
  import-sort auto-fixed); mypy Success (3 files);
  `verify_critical_suite_manifest` OK (255 paths, 506 -> 507 manifest lines);
  `check_merge_marker_residue` clean (3754/3792);
  `check_duplicate_task_rows` OK; `check_dependency_drift` OK (100 pins);
  `scripts/docs/check_docs.py` PASS.

## Battery-authoring defects caught pre-commit

1. **A rule quoting a string the module never carries.** The weights module's
   docstring lists coverage by name, not by the AFML formula string; the rule
   `assert "u_i = (1 / L_i) * sum" in src` failed on the correct source. Fixed
   to assert the coverage-list markers + the durable-proof imports.
2. **A raw-substring rule matching its own explanation.** `assert "+ 1.0" not
   in body` matched the docstring that explains the removed waiver — the exact
   `raw_substring_rule_sees_prose` / ML-QA-014 shape. Fixed by scoping to
   `_code_lines(body)` (executable lines only). Note the companion tooling
   nuance: `_no_call` is token-typed (`NAME . NAME (` chains) so it is the
   right tool for clock reads but tautological for an operator string like
   `"+ 1.0"`; that case needs a plain substring over executable lines.
3. **Module-level constant import at collection time.** Reading the budget
   constants by `exec`-ing the analysed modules at BATTERY import time would
   fail collection on any interpreter lacking torch/polars (they import both).
   Made lazy (`_budget_constant()` resolves on first behavioural use, cached)
   so the textual rules collect and run anywhere.

## OWNERSHIP_SCOPE

- `tests/unit/test_sample_weights.py` (remediation)
- `tests/unit/test_research_edge_hardening_20260909.py` (remediation)
- `tests/unit/test_ml_qa_018_benchmark_cpu_budget.py` (new contract battery)

## FORBIDDEN_CHANGES

- Any file under `src/` (the benchmark asserts are properties of the
  *measurement*, not of production code — pinned by an executed diff rule).
- The durable contract tests in either module (names + hard asserts preserved).
- The measurement scale (50k rows; 1000/4000 samples) — the CPU-time figure
  must stay comparable to the wall-clock one it replaces.
