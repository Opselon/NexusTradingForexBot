# ML-QA-008 — Push-Gate Latency Determinism: Experiment-Registry Benchmark (Roster Candidate #6)

STREAM: STREAM L — CI/CD & Verification
PRIORITY: P2
STATUS: DONE (2026-09-24, AGENT-QA)
DEPENDENCIES: ML-QA-004 (DONE, PR #402); ML-QA-003 census (DONE)
AGENT_ROLE: AGENT-QA
OWNERSHIP_SCOPE: tests/
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Remediate roster candidate #6 from `docs/ml-system/test_determinism_roster.md`
§6: `tests/unit/test_experiment_registry.py` carried 4 `time.perf_counter()`
probes inside the BENCHMARK_PLAN test
(`test_benchmark_1000_records_top10_under_50ms`, manifest line 357), which
gates every push under `pytest -n auto`. The test asserts the top-10 registry
query completes in < 50ms on the WALL CLOCK — under xdist saturation on a
2-core runner that bound measures the scheduler, not the query, and a
co-tenant load spike inflates either leg unevenly (the same shape that forced
9279f1ea to drop the parity-suite 1ms SLA, remediated in ML-QA-007).

## DELIVERED (test-only; ZERO production code)
- `tests/unit/test_experiment_registry.py`:
  - both benchmark legs (register-1000 and top-10 query) moved from
    `time.perf_counter()` to `time.process_time()` (CPU time — co-tenant
    preemption excluded by construction);
  - explicit warmup (register + finalize + `top_n`) runs BEFORE the first
    timed probe so sqlite connection/schema setup is not charged to leg 1;
  - load-bearing asserts kept hard and unweakened: `n = 1000`, every-3rd
    finalize stride, `len(top) == 10`, best-element check, monotone ordering,
    hard `query_ms < 50.0` CPU budget, `register_ms >= 0.0`;
  - the only weakening is `register_ms > 0.0` -> `>= 0.0`: with `process_time`
    an idle/preempted leg may round to exactly 0.0 and `> 0.0` would be a NEW
    wall-clock-class flake; registration throughput was never gated
    ("not gated — disk-dependent" per the original comment).
- `tests/unit/test_ml_qa_008_registry_cpu_budget.py` — 6-test contract
  battery (textual analysis, slim venv, no sqlite/torch import): no
  `time.perf_counter` remains in the module; both legs start/end on
  `process_time`; warmup precedes the first timed probe and covers all three
  operations; scale/result/budget asserts unweakened; failure message
  documents the CPU clock; original test name preserved (manifest/roster
  references stay valid). Registered in `tests/critical_suite.txt`
  (239 paths) — rides the required Code Quality & Tests / Pytest check
  (CHG-0049 gate parity; no workflow file edited).
- Roster §6 candidate #6 annotated REMEDIATED with evidence pointer.

## VERIFICATION (worktree branch `agent/qa/experiment-registry-cpu-budget`,
## Python 3.11.16, /tmp/nse-slim)
- `pytest tests/unit/test_experiment_registry.py tests/unit/test_ml_qa_008_
  registry_cpu_budget.py` -> **45 passed** (41.84s serial)
- Combined regression slice (+ `test_duplicate_task_rows`,
  `test_merge_marker_residue`, `test_ml_qa_004_determinism_remediation`)
  -> **99 passed**
- NEGATIVE CONTROL: reverted `test_experiment_registry.py` to the
  pre-remediation text -> battery **5 failed, 1 passed** (the 6th test only
  pins the unchanged test name), proving the assertions are live, not
  tautological; re-applied fix -> 6/6 pass.
- ruff check clean; ruff format --check clean (after reformatting the
  battery's quote-style line); mypy Success (2 source files)
- `verify_critical_suite_manifest.py` -> CRITICAL_SUITE_MANIFEST_OK: 239 paths
- `check_merge_marker_residue.py` clean (3495 files);
  `check_duplicate_task_rows.py` -> DUPLICATE_TASK_ROWS_OK (2 tables)

## NOT_CHANGED (deliberately)
- No `.github/workflows/*` touched (HARD CONSTRAINT).
- No production source touched (tests/ only).
- Roster candidate #8 (`test_70d_bug106_incremental_phase19.py` ratio assert)
  left for its own cycle: its speedup test is `skipif` on a data file absent
  from git, so a remediation there could not be exercised locally this run.
