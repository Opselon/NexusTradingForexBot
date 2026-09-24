# ML-QA-009 — Push-Gate Determinism: Audit-Flush Bounded-Wait Assert (Roster Candidate #7)

STREAM: STREAM L — CI/CD & Verification
PRIORITY: P2
STATUS: DONE (2026-09-24, AGENT-QA)
DEPENDENCIES: ML-QA-004 (DONE, PR #402); ML-QA-003 census (DONE)
AGENT_ROLE: AGENT-QA
OWNERSHIP_SCOPE: tests/
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Remediate roster candidate #7 from `docs/ml-system/test_determinism_roster.md`
§6: `tests/unit/test_audit_flush_contract.py` was listed with 4
`time.monotonic()` probes. RECOUNT AT HEAD (cbabadae): only **2 calls
remain** (one start/elapsed probe pair, lines 125/127) — ML-QA-004 had
already moved the idle-flush bound onto `budget_cpu_ms`. The residual probe
was the bounded-wait assert in
`test_flush_returns_false_when_worker_stalled`:

```python
started = time.monotonic()
ok = repo.flush(timeout_sec=0.2)
elapsed = time.monotonic() - started
...
assert elapsed < 2.0, "flush must stay bounded (no deadlock)"
```

Wall-clock magnitude assert in a push-gate test — the exact flaky class the
roster catalogues (§3 "Timing assert"): the fixture wedges `flush()` into
its 0.2 s poll loop (`audit_repository.py:2364-2370`, 5 ms sleep until the
`time.monotonic() + timeout_sec` deadline), and `elapsed < 2.0` then measures
how long the OS gave this test. Under `-n auto` on a 2-core runner a
co-tenant scheduler stall >1.8 s trips it while `flush()` behaves perfectly
(same shape as the parity 1ms SLA dropped in 9279f1ea and the ML-QA-007/008
wall bounds).

## DELIVERED (test-only; ZERO production code)
- `tests/unit/test_audit_flush_contract.py`:
  - the probe pair replaced by the shared CPU-time helper:
    `with budget_cpu_ms(2000.0) as sw: ok = repo.flush(timeout_sec=0.2)` then
    `assert sw.consumed_ms < 2000.0` — CPU time, co-tenant preemption
    excluded by construction; bound keeps the same 10x magnitude as the
    removed 2.0 s wall bound;
  - `ok is False` kept HARD — that is the real contract (flush RETURNED
    instead of deadlocking the live path); a genuine hang is caught by the
    CI test timeout, never by a wall-clock magnitude assert;
  - `timeout_sec=0.2` (the production deadline path) and the unwedge
    (`repo._queue = real_queue`) + `repo.close()` teardown kept verbatim;
  - dead `import time` removed (only the two probe calls used it);
  - the other five durable-contract tests untouched (flush True + rows
    readable, idle CPU bound from ML-QA-004, close drains, non-SQLite
    short-circuit, batch salvage + dead-letter).
- `tests/unit/test_ml_qa_009_audit_flush_cpu_budget.py` — 8-test contract
  battery (textual analysis, slim venv, no AuditRepository/sqlite/torch
  import): no `time.monotonic`/`time.perf_counter` anywhere in the module;
  `import time` gone; `budget_cpu_ms` imported from `tests.e2e.chain_clock`;
  test name preserved; `assert ok is False` + unwedge + close kept; the CPU
  budget wraps the flush call with `sw.consumed_ms < 2000.0` and the failure
  message documents the clock; all five durable-contract tests and their hard
  asserts (row counts, `unfinished_tasks == 0`, salvage/dead-letter counters)
  unchanged. Registered in `tests/critical_suite.txt` (240 paths) — rides the
  required Code Quality & Tests / Pytest check (CHG-0049 gate parity; no
  workflow file edited).
- Roster §6 candidate #7 annotated REMEDIATED with evidence pointer and the
  head-recount note (4 listed → 1 probe pair actually remained).

## VERIFICATION (worktree branch `agent/qa/ml-qa-009`, base cbabadae,
## Python 3.11.16, /tmp/nse-slim)
- `pytest tests/unit/test_audit_flush_contract.py tests/unit/
  test_ml_qa_009_audit_flush_cpu_budget.py -rA` -> **14 passed** (2.56s),
  every test listed PASSED individually.
- NEGATIVE CONTROL: `git show HEAD:...` pre-remediation text restored ->
  battery rc=1 with **3 FAILED** (`test_module_has_no_wall_clock_probe`,
  `test_time_import_removed`, `test_stall_test_boundedness_uses_cpu_time_budget`)
  / 5 passed (the 5 only pin unchanged contract text); fixed text restored ->
  8/8 pass. Assertions are live, not tautological.
- ruff check clean; ruff format --check clean ("2 files already formatted");
  mypy "Success: no issues found in 2 source files".
- `verify_critical_suite_manifest.py` -> CRITICAL_SUITE_MANIFEST_OK: 240
  paths all exist (new battery present at manifest line 492);
  `check_merge_marker_residue.py` clean (3525/3563 files);
  `check_duplicate_task_rows.py` -> DUPLICATE_TASK_ROWS_OK (2 tables);
  `gate_parity.py` -> `"drifts": 0` rc=0;
  `check_dependency_drift.py` -> OK (99 pins).

## NOT_CHANGED (deliberately)
- No `.github/workflows/*` touched (HARD CONSTRAINT).
- No production source touched (`src/` untouched — flush() itself keeps its
  wall-clock deadline loop; that is production timing *logic*, not a test
  assert, and out of scope).
- Roster candidate #8 (`test_70d_bug106_incremental_phase19.py`) still
  deferred: speedup test is `skipif` on `data/raw/XAUUSD_M5.parquet` absent
  from git — cannot be exercised locally; decide synthetic fixture vs
  accepted-risk before touching.
- Next roster candidates: #9 `test_runtime_config_hot_reload.py` (2
  `getpid()` + 1 `mkdtemp`), #1 `tests/e2e/test_smoke_chain.py` (largest
  surface, needs injected-clock design), #10 `test_causal_conv_invariants.py`
  (4 probes, S-seeded), #13.
