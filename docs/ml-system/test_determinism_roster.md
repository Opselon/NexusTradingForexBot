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
7. **`tests/unit/test_audit_flush_contract.py`** — 4 `monotonic()` probes.
8. **`tests/unit/test_70d_bug106_incremental_phase19.py`** — 4
   `perf_counter()` probes.
9. **`tests/unit/test_runtime_config_hot_reload.py`** — 2 `getpid()` asserts
   + 1 `mkdtemp`; the hot-reload pid invariant is the right thing to assert,
   just not its literal value.
10. **`tests/unit/test_logging.py`** — 5 `datetime.now()` asserts (line 85:
    `assert parsed.year == datetime.now().year`) — the cleanest wall-clock
    fix in the tree (inject the year or freeze the clock).

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
