# NSE Test Determinism Roster — Flaky-Source Census & Push-Gate Exposure

> **Task:** ML-QA-003 · **Owner:** AGENT-QA · **Status:** DONE
> **Census taken at:** `229dfeae` (origin/main, 2026-09-23)
> **Scanner:** stdlib-only (`re` + `pathlib`), runs in the slim Linux venv —
> no torch, no polars, no repo import. Reproduce with the scanner recorded
> in the ML-QA-003 handoff report.

---

## 1. Why this roster exists

Triaging a red `Code Quality` gate on this repo is expensive and the failure
text is artifact-only: `gh run view <RUN_ID> --log-failed` returns **empty**
(the job log records only `CHECK pytest rc=1`). The real output lives in the
`ci-results-*` artifact → `pytest/pytest.txt`. That round trip costs a cycle.

This roster converts the triage from a search into a lookup: it lists every
test file that carries a *known flaky shape*, split by whether the file is in
the push gate (`tests/critical_suite.txt`) or runs only in the
nightly/extended lanes. A red on an out-of-gate file never blocks a push; a
red on an in-gate flaky shape needs the artifact read.

The 30-task ML board is complete (24 DONE, 6 held at human-decision or
upstream-blocked gates). Census work is the unblocked hardening work that
remains for Stream L.

---

## 2. Census method

A stdlib scanner walks `tests/**/*.py` (611 files) and matches the canonical
non-determinism source patterns (§3). Per file it records each shape's hit
count and the first `file:line` occurrence, then cross-references the file
path against `tests/critical_suite.txt` (the push-gate manifest — the
critical suite is a *path manifest*, so an entry covers the whole module and
every test function in it).

A file counts as flagged when it carries ≥1 genuine source. A file with a
seed anchor (`random.seed(`, `SEED`, `fake_`, `monkeypatch.*now`,
`freeze_time`, `torch.manual_seed`, `np.random.seed`) is marked SEEDED — the
author already pinned the randomness, so the remaining sources are
timing/thread, not sampling.

### Synchronization primitives are NOT nondeterminism sources

`set()` (a Python set), `threading.Event`, `Event.set()`, `cancel.set()`,
`release.set()` are the *deterministic synchronizers* a test uses to make a
threaded scenario reproducible. The scanner matches the `set(...)`-call shape
because that is also how a `random.sample(population, k)` or a
`set(global_order)` race-source looks at the AST surface, so the roster
reports the shape but **the reader must discount `Event`/`set()` hits on
synchronization objects**. The `dict_order_set` column in the totals is
therefore an upper bound, not a defect count: e.g.
`tests/unit/test_training_env_worker.py:189 cancel.set()` is correct
deterministic synchronization, not a flaky source.

---

## 3. Canonical shape taxonomy

Each shape is a proven CI-red class on this repo (see the audit-method
reference and the swarm skill's Pitfalls).

| Shape | Source | Why it flakes | Preferred fix |
|---|---|---|---|
| **Timing assert** | `time.perf_counter()` / `time.monotonic()` | Compares wall-clock between two legs; trips on co-tenant CI runners (the observed shape: `3.61x < 3.5` scaling assert) | Inject the clock (`now` callable / monotonic knob) and assert the *relationship* the code guarantees (ordering, sentinel, expiry), not elapsed magnitude. For a genuine scaling bound, `time.process_time()` (CPU time, load-insensitive) or a bound above the true factor plus margin |
| **Wall-clock date assert** | `datetime.now()` / `datetime.utcnow()` | Breaks at timezone/date boundaries | Inject or patch the clock (`monkeypatch`/freezegun) |
| **Process-identity assert** | `os.getpid()` | Breaks across process boundaries (fork, subprocess, hot-reload) | Assert the invariant the pid *proves* (liveness, single-ownership) rather than its literal value |
| **Unseeded random** | `random.shuffle/choice/sample/randint/random/uniform/gauss` | Non-reproducible sample generation | Seed (`random.seed`) or use a seeded fixture |
| **Temp path** | `tempfile.mkdtemp` / `NamedTemporaryFile` | Untracked cleanup; on macOS the temp path can be a random symlink target | Use the `tmp_path` pytest fixture |
| **Threaded / pool** | `threading.Thread(target=...)` / `ProcessPoolExecutor` / `multiprocessing.Pool` | Timing-sensitive joins, start-order races | Synchronize with `Event`/`Barrier` and assert on the shared state, not on completion timing |
| **Instance-identity assert** | `uuid.uuid4()` / `uuid.uuid1()` | Random ids | Inject the id factory in tests |
| **Filesystem order** | `glob.glob` / `os.walk` | FS-dependent iteration order | Sort the result before asserting |

**Note on `perf_counter` vs `monotonic`:** the skill's gotcha (HOLD_AGE_FALLBACK / BUG-273) is about the *production* clock, not the test clock — a `self._last = 0.0` sentinel compared against `time.monotonic()` silently skips the first pass on a fresh boot. The test-side shape is the mirror: a test that asserts an elapsed magnitude instead of the sentinel/ordering invariant. Both are the same defect class; the roster counts the test side.

---

## 4. Census results

**Totals** — 611 test files scanned.

| Metric | Count |
|---|---|
| Files with ≥1 non-determinism source | **123** (20.1% of test tree) |
| of which in the push gate (`critical_suite.txt`) | **53** |
| of which unseeded (no seed anchor anywhere in the file) | **87** |
| of which seeded (author already pinned randomness) | **36** |

**Per-shape source counts across the flagged files** (upper bound — includes
the `dict_order_set` synchronization false positives):

| Shape | Source hits |
|---|---|
| Timing (`perf_counter` + `monotonic`) | 93 (72 + 21) |
| Threaded/pool | 58 |
| Synchronizer-shaped (`set()`/`Event.set()`) | 35 † |
| Temp path (`mkdtemp`/`NamedTemporaryFile`) | 34 |
| Process identity (`getpid`) | 30 |
| Instance identity (`uuid4`/`uuid1`) | 12 |
| Wall-clock date (`datetime.now/utcnow`) | 10 |
| Unseeded random | 5 |

† *upper bound; discount `Event`/`set()` on synchronization objects (§2).*

**Headline exposure:** the push gate's flaky surface is concentrated in a
handful of files — the top 12 in-gate files carry **61%** of the in-gate source
hits (103 of 169). The single largest in-gate exposure is
`tests/e2e/test_smoke_chain.py` (44 sources, all unseeded), dominated by 42
`time.monotonic()` timing probes.

---

## 5. In-gate flagged files (top 20 by source count)

Rank = remediation priority (in-gate × source count). "S" = seeded,
"U" = unseeded.

| # | Sources | Seeded | File |
|---|---|---|---|
| 1 | 44 | U | `tests/e2e/test_smoke_chain.py` |
| 2 | 8 | U | `tests/unit/test_training_env_worker.py` |
| 3 | 8 | U | `tests/unit/test_outcome_flush_race_bug140.py` |
| 4 | 7 | U | `tests/integration/test_mt5_adapter_parity.py` |
| 5 | 6 | S | `tests/unit/test_user_hunt_bug170_171.py` |
| 6 | 5 | U | `tests/unit/test_perf_r4_runtime_loop_offload.py` |
| 7 | 5 | S | `tests/unit/test_bug262_close_time_evidence.py` |
| 8 | 4 | U | `tests/unit/test_qa_deep_provider_gate_chaos.py` |
| 9 | 4 | U | `tests/unit/test_experiment_registry.py` |
| 10 | 4 | S | `tests/unit/test_causal_conv_invariants.py` |
| 11 | 4 | U | `tests/unit/test_audit_flush_contract.py` |
| 12 | 4 | U | `tests/unit/test_70d_bug106_incremental_phase19.py` |
| 13 | 3 | U | `tests/unit/test_runtime_config_hot_reload.py` |
| 14 | 3 | U | `tests/unit/test_bug285_overflow_drain.py` |
| 15 | 3 | U | `tests/unit/test_bug275_hygiene_cadence_clock.py` |
| 16 | 2 | U | `tests/unit/test_strategy_factory_phase22.py` |
| 17 | 2 | U | `tests/unit/test_storage_policy.py` |
| 18 | 2 | U | `tests/unit/test_shadow70_safety.py` |
| 19 | 2 | U | `tests/unit/test_sample_weights.py` |
| 20 | 2 | U | `tests/unit/test_research_edge_hardening_20260909.py` |

Full per-file detail (every shape's hit count and first `file:line`
occurrence) is in the ML-QA-003 handoff report and regenerable by the
scanner in seconds.

---

## 6. Top remediation candidates

Ranked by in-gate exposure × source count. Each is a small, contained change
with no production-code impact — suitable for one cycle each, or a batch.

1. **`tests/e2e/test_smoke_chain.py`** — 42 `time.monotonic()` probes + 2
   `uuid4()`. Largest single flaky surface in the push gate. Convert the
   timing probes to injected-clock asserts (assert the ordering/expiry
   invariant, not the elapsed magnitude) and inject the id factory.
   **ALREADY REMEDIATED (ML-QA-004, PR #402):** recount at `03d2fede` shows
   **0** `time.monotonic()`/`time.perf_counter()` probes — `ChainClock`
   (`tests/e2e/chain_clock.py`) is wired at line 109 and every stage line reads
   the deterministic clock. The only residual sources are 2 `uuid4()` proposal
   `request_id` stamps that no assertion reads. The 7 `datetime.now` hits the
   raw grep reports are docstring/prose references, not executable calls.
2. **`tests/unit/test_training_env_worker.py`** — 4 synchronizer-shaped hits
   (real: 2 `getpid()` asserts at line 47 context + 2 `monotonic()` probes);
   the `cancel.set()`/`release.set()` hits are *correct* synchronization.
3. **`tests/unit/test_outcome_flush_race_bug140.py`** — 4 `monotonic()`
   probes + 4 synchronizer hits. The race is deterministic; only the timing
   asserts are flaky.
4. **`tests/integration/test_mt5_adapter_parity.py`** — 6 `perf_counter()`
   probes. Cross-OS matrix exposure: a timing red on one OS of the matrix is
   the tell for this shape, not a platform bug.
   **REMEDIATED (ML-QA-007):** all three latency tests now measure
   `time.process_time()` (CPU time, co-tenant-scheduler-insensitive) after an
   explicit warmup loop, keep the deterministic invariants (call count,
   action identity, percentile ordering) hard, and re-attach the <1ms
   serialization SLA as a CPU-time mean bound. The loopback round-trip bound
   moved onto the shared `budget_cpu_ms` helper. Contract pinned by
   `tests/unit/test_ml_qa_007_parity_latency_determinism.py` (19 tests),
   which fails on the pre-remediation text (verified: 3 `perf_counter`
   anchors, no warmup, no `calls.clear()`).
5. **`tests/unit/test_perf_r4_runtime_loop_offload.py`** — 5 synchronizer
   hits; verify none are genuine race sources before discounting.
6. **`tests/unit/test_experiment_registry.py`** — 4 `perf_counter()` probes
   in the registry benchmark path.
   **REMEDIATED (ML-QA-008):** both benchmark legs (register-1000, top-10
   query) now measure `time.process_time()` (CPU time, co-tenant-scheduler-
   insensitive) after an explicit warmup register/finalize/query; the hard
   `query_ms < 50.0` budget, `n = 1000` scale, ordering and best-element
   asserts are all kept (only `register_ms > 0.0` -> `>= 0.0`, which would be
   a new wall-clock-class flake on CPU time). Contract pinned by
   `tests/unit/test_ml_qa_008_registry_cpu_budget.py` (6 tests), which fails
   on the pre-remediation text (negative control: 5 failed / 1 passed).
7. **`tests/unit/test_audit_flush_contract.py`** — 4 `monotonic()` probes.
   **REMEDIATED (ML-QA-009, PR #422):** the remaining wall-clock pair moved to
   the shared `budget_cpu_ms` CPU-time helper; the hard no-deadlock contract
   (`ok is False` around a wedged flush) is kept. Residual 2 `monotonic` hits
   are docstring references.
8. **`tests/unit/test_70d_bug106_incremental_phase19.py`** — 4
   `perf_counter()` probes.
   **REMEDIATED (ML-QA-019):** the module's two tests were guarded by a
   `skipif` on a data file git never carries (`data/raw/XAUUSD_M5.parquet`
   is download-only), so both were silently UNCOLLECTED in CI — the module
   is in the push gate, so the gate reported a green module exercising
   nothing, and the BUG-106 canonical-vs-incremental byte-identity contract
   had NO live in-gate coverage at all. The real-data dependency is replaced
   by the deterministic synthetic bar generator the gate already uses
   (`generate_synthetic_bars`, the same importer/seed pattern as
   `test_position_replay_pipeline.py`), the speedup leg now measures CPU
   time through the shared `budget_cpu_ms` stopwatch, and a new precondition
   test pins the synthetic frame's schema/tz/sort/bar-count so a generator
   change cannot degrade the coverage to a trivial frame. 20-test contract
   battery `tests/unit/test_ml_qa_019_parity_coverage_cpu_budget.py` pins it
   (negative control: 10 rules fail on the pre-remediation file, incl. an
   EXECUTED proof that the old shape collects 2 skipped / 0 passed).
9. **`tests/unit/test_runtime_config_hot_reload.py`** — 2 `getpid()` asserts
   + 1 `mkdtemp`; the hot-reload pid invariant is the right thing to assert,
   just not its literal value.
10. **`tests/unit/test_logging.py`** (out-of-gate) — 5 `datetime.now()` asserts
    (line 85: `assert parsed.year == datetime.now().year`) — the cleanest
    wall-clock fix in the tree (inject the year or freeze the clock).

### Recount at `03d2fede` (ML-QA-010, 2026-09-24)

The census counts above were taken at `229dfeae` and are stale: ML-QA-004
already remediated candidate #1 (0 timing probes remain — `ChainClock` wired at
`tests/e2e/test_smoke_chain.py:109`), ML-QA-007 candidate #4, ML-QA-008
candidate #6, ML-QA-009 candidate #7. Rescanning the §5 top-20 with the same
stdlib scanner gives the current in-gate exposure, ordered by live source count:

| Sources | File | Status |
|---|---|---|
| 15 | `tests/unit/test_bug262_close_time_evidence.py` | **REMEDIATED (ML-QA-010, PR #432)** — 11 `datetime.now` + 4 `mkdtemp` removed via a fixed injected clock + `tmp_path`; 19-test contract battery `tests/unit/test_ml_qa_010_clock_determinism.py` pins it (merged as squash `6a7e2e7e`) |
| 8 | `tests/unit/test_shadow70_safety.py` | **REMEDIATED (ML-QA-011, PR #457, squash 33205de2)** — 6 `datetime.now` + 1 `mkdtemp` removed via ONE module-level frozen instant (`_FIXED_NOW`) replayed through `_now()` + `tmp_path`; the persistence wait gained a `budget_cpu_ms` CPU-time bound; spec 13/14 retry idempotency is now *provable* (it was unprovable under six independent wall-clock reads). 23-test contract battery `tests/unit/test_ml_qa_011_shadow70_clock_determinism.py` pins it (negative control: 8 rules fail on the pre-remediation file) |
| 7 | `tests/unit/test_outcome_flush_race_bug140.py` | **REMEDIATED (ML-QA-012, PR pending)** — 4 `monotonic()` probes + 3 `datetime.now` removed: ONE module-level frozen instant (`_FIXED_NOW`) through a single `_now()` supplier for all 3 timestamp sites (the causality guard is strict `<`, so an equal decision/outcome pair is causal — independent reads made that equality a coin flip across a tick); both poll loops now bounded on CPU time (`budget_cpu_ms` + `time.process_time()` inner fail-fast + `consumed_ms` assert), the `time.sleep()` sleeps gone. 15-test contract battery `tests/unit/test_ml_qa_012_outcome_flush_clock_determinism.py` pins it (negative control: 9 rules fail on the pre-remediation file) |
| 4 | `tests/unit/test_causal_conv_invariants.py` | **REMEDIATED (ML-QA-014, PR pending)** — all 4 `perf_counter()` reads in the two latency benchmark tests replaced with the shared `budget_cpu_ms` CPU-time stopwatch (`sw.consumed_ms / 20` per pass); budgets recalibrated against measured cost (worst/best 1.5x and worst case 0.87 ms observed -> ratio 15, budget 50 ms CPU, ~58x margin); warm-up 3 -> 5 passes; the depth ratio bound lost its `or worst < BUDGET` waiver (a compound assert that silently waived the structural invariant it exists to prove). Test-only: zero production files changed, pinned by a battery rule. 18-test contract battery `tests/unit/test_ml_qa_014_causal_conv_cpu_budget.py` pins it (negative control: 7 rules fail on the pre-remediation file) |
| 4 | `tests/unit/test_70d_bug106_incremental_phase19.py` | **REMEDIATED (ML-QA-019, PR pending)** — the two tests were guarded by a `skipif` on a data file git never carries (`data/raw/XAUUSD_M5.parquet`), so both were silently UNCOLLECTED in CI: the module is in the push gate, so the gate reported a green module exercising nothing, and the BUG-106 canonical-vs-incremental byte-identity contract had NO live in-gate coverage. The real-data dependency is replaced by the deterministic synthetic bar generator the gate already uses (`generate_synthetic_bars`, the same importer/seed pattern as `test_position_replay_pipeline.py`); the speedup leg's two `perf_counter()` reads moved to the shared `budget_cpu_ms` CPU-time stopwatch; a new precondition test pins the synthetic frame's schema/tz/sort/bar-count. Test-only: zero production files changed (pinned by a battery rule). 20-test contract battery `tests/unit/test_ml_qa_019_parity_coverage_cpu_budget.py` pins it, including an EXECUTED negative control that reconstructs the old shape and proves it collects 2 skipped / 0 passed (negative control: 10 rules fail on the pre-remediation file) |
| 3 | `tests/unit/test_bug285_overflow_drain.py` | **REMEDIATED (ML-QA-013, PR pending)** — 2 `time.monotonic()` reads + 1 hand-written stamp removed: the production drain gained an optional `now: float \| None = None` argument (the `storage/runtime.py::is_due` idiom; `now is None` still reads the real clock, so the runtime path is byte-for-byte unchanged) and the cadence tests now drive ONE deterministic `_MonotonicClock` through it, exercising BOTH edges of the strict `<` boundary (one tick short of the interval = still throttled; exactly at the interval = DUE) reproducibly to the nanosecond without waiting out a real 60s interval. 17-test contract battery `tests/unit/test_ml_qa_013_overflow_drain_clock_determinism.py` pins it (negative control: 5 rules fail on the pre-remediation file) |
| 3 | `tests/unit/test_bug275_hygiene_cadence_clock.py` | **REMEDIATED (ML-QA-015, PR pending)** — 3 `time.time()` reads removed (the stamps the tests compared against were fresh reads of the same wall clock the code stamped, so the cadence coverage was a property of when the run happened). One deterministic WALL clock (`_WALL_CLOCK`, epoch 1.9e9 — the production domain is wall, so a monotonic-domain regression still shows up as an out-of-range stamp rather than coincidentally passing) swapped in through the production module's own `time` reference via a `hygiene_clock` monkeypatch fixture; both edges of the inclusive `>=` cadence boundary are now exercised to the nanosecond for all three gates (one tick short = NOT due; exactly at the interval = DUE) — pre-ML-QA-015 the deep 6h interval's due-edge had never been reached because reaching it needed a real 6-hour wait. Test-only: zero production files changed (pinned by a battery rule that greps the production contract verbatim). 15-test contract battery `tests/unit/test_ml_qa_015_hygiene_cadence_clock_determinism.py` pins it (negative control: 9 rules fail on the pre-remediation file) |
| 3 | `tests/unit/test_runtime_config_hot_reload.py` | **REMEDIATED (ML-QA-016, PR pending)** — 2 `os.getpid()` asserts + 1 `mkdtemp` removed. A pid is a process identity, not a contract value: the §65 test captured it at the top and re-asserted the same literal at the bottom, and no production path reads it. Worse, the invariant it was a proxy for (the same store object serves the snapshot before and after the apply — a hot reload is an in-object atomic swap) is something a pid comparison *cannot* prove: `fork()` keeps the parent's pid in the child, so a regression that copied the store into a new process would pass the old assert. Now asserted by OBJECT IDENTITY (`store is store_before`) plus a new second-reference leg (`test_second_reference_observes_the_swap`) — together proving the swap happens in the object; one injected `_pid()` supplier keeps the semantic "one process identity throughout" pin, so a fork still fails it while a passing run no longer depends on which pid the OS assigned. `mkdtemp` -> `tmp_path`. Test-only: zero production files changed. 12-test contract battery `tests/unit/test_ml_qa_016_hot_reload_identity_determinism.py` pins it (negative control: 7 rules fail on the pre-remediation file) |
| 2 | `tests/unit/test_training_env_worker.py` | **REMEDIATED (ML-QA-017, PR pending)** — 2 `os.getpid()` sources removed. The parent read its OWN pid (an `import os` inside the test body) and asserted the worker's differed from it, which no production path compares, so the magnitude carried no information about the transport contract. Worse it was a proxy with a blind spot: it stood in for "the transport delivers ONE worker identity across the run" (the pipe protocol stamps the identity at the progress stage AND the result stage), but a regression that routed the result line through a different process than the progress line still yields a worker pid different from the parent's — the old assert passed while the continuity invariant it pretended to prove broke. Now the parent's read is ONE injected `_pid()` supplier (the semantic 'not the parent's own' pin survives, asserting a stable identity rather than an OS-assigned number) and the continuity is asserted between the transport's OWN two stamp sites (`metrics['pid'] == result['second_pid']`), driven twice: a real-pid leg keeping the isolation property pinned and a fixed-identity leg (`_FIXED_ID`) making the continuity reproducible to the digit, the two cross-checking so neither passes alone. Both tests source their worker from ONE `_identity_fixture(root, fixed)` helper stamping ONE expression on both lines. Test-only: zero production files changed. 13-test contract battery `tests/unit/test_ml_qa_017_worker_identity_determinism.py` pins it, including an EXECUTED negative control whose split-identity fixture stamps the parent's own pid on the result line — the old assert still passes on the progress line alone, and only the cross-stamp comparison catches it (negative control: 9 rules fail on the pre-remediation file) |
| 2 | `tests/unit/test_audit_flush_contract.py` | residual docstring refs only |
| 2 | `tests/unit/test_sample_weights.py` | **REMEDIATED (ML-QA-018, PR pending)** — 2 `perf_counter()` reads removed from the 50k-row SLA benchmark: the measured body now runs inside the shared `budget_cpu_ms` CPU-time stopwatch reading `sw.consumed_ms`, with the budget recalibrated against measured cost (0.87 ms CPU observed for 50k rows -> 500 ms budget, ~570x margin; the old bound was 2000 ms of WALL clock). `import time` gone (the probes were its only consumers). Test-only: zero production files changed. 53-test contract battery `tests/unit/test_ml_qa_018_benchmark_cpu_budget.py` pins BOTH final roster rows (negative control: 13 rules fail on the pre-remediation files) |
| 2 | `tests/unit/test_research_edge_hardening_20260909.py` | **REMEDIATED (ML-QA-018, PR pending)** — 2 `perf_counter()` reads removed from the sized-path linearity benchmark: both legs measured through `budget_cpu_ms`, and the assert lost its `+ 1.0` additive pad on the small leg — a waiver of the same class as the ML-QA-014 compound `or` (at n=1000 the whole leg costs ~10 ms CPU, so a one-second pad made the ratio term irrelevant exactly where an O(n^2) regression would show up first). The ratio bound is now unconditional at 6.0 against a measured 4.20x (per-sample cost 0.0108 ms, constant at n=1000..8000, so linearity is structural). `import time` was function-local and is gone. Test-only: zero production files changed |

Already at 0 sources: `test_smoke_chain.py` (was 44), `test_perf_r4_runtime_loop_offload.py`
(was 5), `test_experiment_registry.py` (was 4), `test_strategy_factory_phase22.py`
(was 2), `test_mt5_adapter_parity.py` (was 7).

**ML-QA-019 closes the recount table ENTIRELY: every in-gate benchmark/timing row is now REMEDIATED or residual-docstring-only, including the one DEFERRED entry.** ML-QA-018 closed the last two OPEN timing rows; ML-QA-019 then closed the deferred `test_70d_bug106_incremental_phase19.py` row, which turned out to be the highest-severity entry in the whole table — not a timing defect at all but a COVERAGE defect (a `skipif` on a data file git never carries made both tests silently uncollected in the push gate, so the BUG-106 parity contract had zero live in-gate coverage). The next workstream for this roster is a full re-scan of `tests/` (the scanner is regenerable in seconds) to catch shapes the original census did not classify — e.g. the `skipif`-on-a-download-only-data-file shape, which the timing-oriented §3 taxonomy does not currently match and is worth adding there.

---

## 7. Triage recipe for a red `Code Quality` gate

```
1. gh run view <RUN_ID> --log-failed
   └─ EMPTY output ⇒ the evidence is artifact-only (the job log
      records only `CHECK pytest rc=1`). Do not report "no failure text".
2. gh run download <RUN_ID> --dir <dir>
   └─ read pytest/pytest.txt (the `short test summary info` block),
      pytest/junit.xml, mypy/mypy.txt, ruff/.
3. Identify the failing test's file. Look it up in this roster (§5/§6).
   └─ In-gate + a §3 shape ⇒ prime hypothesis is flaky, not a regression.
4. Confirm locally, SERIALLY (the host dies on xdist under memory pressure):
      PYTHONPATH=src:. .venv-linux/bin/python -m pytest <file> -v -p no:xdist
   └─ Passes serially ⇒ flaky under parallel load. Do not declare the PR broken.
5. Only if it fails serially at HEAD is it a real regression.
```

Related invariants that make a red triageable in seconds:

- `pytest` rc=5 (`pytest=errored` in the CI aggregate) = **no tests
  collected** — almost always a manifest entry whose file is missing at
  HEAD. Verify every `tests/critical_suite.txt` path exists before pushing
  or tagging (`scripts/ci/verify_critical_suite_manifest.py`).
- `no checks reported on the '<branch>' branch` = no workflow ever ran for
  that PR (branch predates it or CI never triggered). A missing gate, never
  a green signal.
- A CI alert whose branch reads `<n>/merge` is the auto-merge ref: its sha
  is the merge commit, not the PR head. Resolve parents to find the head.
- `mergeable: CONFLICTING` / `mergeStateStatus: DIRTY` is a stale
  computation, not a verdict. Recompute with
  `git merge-tree --write-tree --name-only origin/main origin/<branch>`
  (rc=0 = clean) before reporting a blocker.

---

## 8. Out-of-gate flagged files (first 20)

These run only in nightly/extended lanes — a flaky fail there costs a cycle
but never blocks a push. (The out-of-gate files that also appear in §5's
remediation candidates are in-gate; this list excludes them.)

`tests/unit/test_provider_gate_hardening.py` (11), `test_bug304_warm_shutdown.py` (10),
`test_trading_metrics.py` (8), `test_post70d_monitoring_activation.py` (8),
`test_alt_ui_standalone_server.py` (7, seeded), `test_runtime_engine_hot_reload.py` (6),
`test_70d_model_validation_task4.py` (6, seeded), `tests/slow/test_70d_incremental.py` (6),
`test_download_ready_hotswitch_hotreload.py` (6, seeded), `test_shadow70_runtime.py` (5, seeded),
`test_logging.py` (5), `test_provider_lifecycle_hardening.py` (3),
`test_official_model_install.py` (3), `test_observability_guardrails.py` (3),
`test_missing_outcome_backfill_bug174.py` (3), `test_db_status_latency_fixes.py` (3),
`test_accounting_hedging.py` (3), `tests/integration/test_runtime_config_api.py` (3),
`test_hold_time_fallback_g3.py` (2), `test_model_generation_phase13.py` (2).

---

## 9. Maintenance

- Regenerate after adding a test file that uses a §3 source: run the scanner
  (see ML-QA-003 handoff), update the §4 totals and the §5 table if the top
  20 changed, and re-check whether the new file is in the push gate.
- When a §6 candidate is remediated, move its row to a "Remediated" section
  with the fix commit so the exposure number only ever goes down.
- This roster covers `tests/`. Production-clock sentinel defects
  (`_last = 0.0` vs `time.monotonic()`, the HOLD_AGE_FALLBACK / BUG-273
  class) are a separate audit target — grep
  `getattr(self, "_.*_at", 0.0)` for the residual shape.
