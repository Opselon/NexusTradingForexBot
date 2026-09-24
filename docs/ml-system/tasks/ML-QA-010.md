# ML-QA-010 — Push-Gate Wall-Clock Determinism: BUG-262 Close-Time Evidence (Roster Recount #1)

STREAM: STREAM L — CI/CD & Verification
PRIORITY: P2
STATUS: DONE (2026-09-24, AGENT-QA)
DEPENDENCIES: ML-QA-004 (DONE, PR #402); ML-QA-003 census (DONE, PR #402)
AGENT_ROLE: AGENT-QA
OWNERSHIP_SCOPE: tests/
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE

Remediate the largest remaining wall-clock exposure in the push gate, found by
re-running the ML-QA-003 census scanner at HEAD `03d2fede` (the roster's counts
were taken at `229dfeae` and are stale: ML-QA-004 already converted roster #1).

The recount changed the target. Roster §6 candidate #1
(`tests/e2e/test_smoke_chain.py`, 44 sources) is **already remediated**: at HEAD
it carries **0** `time.monotonic()`/`time.perf_counter()` probes — ML-QA-004's
`tests/e2e/chain_clock.py` `ChainClock` is wired (line 109) and every stage line
reads the deterministic clock. The only residual sources there are 2 `uuid4()`
id stamps in proposal construction, which are not asserted on.

The new top in-gate exposure is `tests/unit/test_bug262_close_time_evidence.py`:
**15 sources** — 11 `datetime.now(UTC)` wall-clock reads + 4
`tempfile.mkdtemp()` temp paths. It regresses BUG-262 (broker-evidenced close
time on the reconcile / autopsy / outcome-repair paths), where every scenario
asserts a *relationship* against the detection instant: "the evidenced close is
2 h before detection", "a +3 h server-local stamp is refused", "the fallback
equals detection +/- 5 s". Those relationships are the fix's contract; reading
`datetime.now(UTC)` for the detection instant made each one host-clock-dependent:

  * **date boundary** — a scenario starting at 23:59:59 UTC computes
    `now - timedelta(hours=2)` in *yesterday*; a close expected in today's
    accounting bucket lands in a different day/week bucket;
  * **tolerance widening** — the `< now + 5 s` and `abs((close - now)) < 5.0`
    windows are real durations, but their *endpoints* moved with the wall
    clock, so a co-tenant-scheduled pause between the `now` read and the
    assertion shifted the window off the evidence it was measuring.

## DELIVERED (test-only; ZERO production code)

`tests/unit/test_bug262_close_time_evidence.py`:

  * **11 wall-clock reads removed.** A module-level fixed detection instant
    `_FIXED_NOW = datetime(2026, 9, 24, 14, 37, 12, tzinfo=UTC)` reached through
    `_now()`, replacing every `now = datetime.now(UTC)` in the 5 reconcile /
    autopsy scenarios and the 4 outcome-repair scenarios. Deliberately set
    mid-day and mid-minute and off the zero second so a future re-introduction
    of a wall clock fails loudly in *both* bucket directions rather than only
    at midnight.
  * **Injection at the seam production actually reads.** `reconcile_missed_closes`
    derives detection from `current_tick.timestamp`
    (`src/nexus_scalp/execution/lifecycle/reconciliation.py:106`), so a tick
    helper `_tick_ts(bid, ts=None)` injects the instant there. The classic
    `_tick(bid=1994.0)` signature is preserved as a thin wrapper so no call site
    changes. The `ts` parameter keeps the real-world pre-gap shape (a tick can
    carry a quote time before the detection sweep).
  * **Repair-path clock patched at its own module name.** `OutcomeRepairJob`
    calls `datetime.now(UTC)` in four places (candidate age, contamination
    `not_after`, fallback close stamp, `repaired_at` provenance) via a
    module-form import (`from datetime import UTC, datetime, timedelta`), which
    makes the `datetime` *name* patchable. A minimal `_FrozenClock` stand-in
    implements only that surface (`now()`, `UTC`, fall-through to the real class)
    and is installed with `monkeypatch.setattr` — auto-reverted per test, no
    global state.
  * **4 `tempfile.mkdtemp()` -> pytest `tmp_path`.** Auto-created, auto-removed,
    unique per test, and not a random symlink target on macOS. The dead `os`
    import (only there for `os.path.join`) is gone.
  * **No assertion weakened.** Every relationship assert is preserved
    byte-for-byte (`true_close = now - timedelta(hours=2)`,
    `contaminated = now + timedelta(hours=3)`, the exact `== true_close`
    equalities, the 5 s tolerance windows as real `timedelta` arithmetic). The
    tolerances are *durations* computed from the injected instant, never
    hard-coded constants.
  * **2 new invariant tests** that the injected clock makes possible:
    `test_detection_instant_is_fixed_and_aware` (the clock is a frozen UTC
    constant, stable across reads) and
    `test_reconciled_close_time_is_invariant_across_host_clocks` (the ledger
    close_time equals the evidential instant **exactly** — the assert the wall
    clock forced down to `close < now + 5 s`).

`tests/unit/test_ml_qa_010_clock_determinism.py` — new 19-test contract battery
(textual analysis via a stdlib `tokenize`-based comment/docstring stripper, so
it runs in the slim venv with no torch/sqlite import, plus 3 behavioral
parametrized legs). Pins: no live `datetime.now(` in executable code (docstrings
that document the removed defect are permitted and a dedicated test proves the
distinction), no `tempfile`/`mkdtemp`/`os`, the fixed instant is UTC-aware and
positioned away from every bucket boundary, `_now()` is stable and verbatim,
`_tick_ts` injects by default but still accepts an explicit instant, the
production reconciliation clock really is the tick timestamp, `_FrozenClock`
provides exactly the patchable surface, the repair module keeps the name-based
datetime import, every repair test declares `tmp_path` + `monkeypatch`, the
tolerance windows stayed `timedelta` arithmetic, the load-bearing relationship
asserts survived, and the exact-equality invariant is reachable at 2 h / 3 h /
12 h before detection. Registered in `tests/critical_suite.txt` (245 -> 246
paths) so it rides the required Code Quality & Tests check (CHG-0049 gate
parity; no workflow file edited).

Roster §6 candidate #1 annotated ALREADY-REMEDIATED (ML-QA-004) with the HEAD
recount evidence, and the module's own count corrected to its true residual
(2 `uuid4()`, not asserted on).

## VERIFICATION (worktree branch `agent/qa/ml-qa-010`, HEAD 03d2fede,
## Python 3.11.16, hermes venv — the only interpreter that collects this module)

  * **Baseline before any change:** 16/16 passed in 6.33 s (the module already
    green; this is a determinism remediation, not a bug fix).
  * **After remediation:** module **18 passed** (16 converted + 2 new invariants)
    in 12.27 s; module + battery **37 passed** in 16.29 s.
  * **NEGATIVE CONTROL:** reverted `tests/unit/test_bug262_close_time_evidence.py`
    to the pre-remediation `origin/main` text (10 live `datetime.now(UTC)` + 4
    `mkdtemp`) -> battery **16 failed / 3 passed** (the 3 that pass are the
    production-source pins, which do not depend on my changes — they assert on
    `reconciliation.py` / `outcome_repair.py`, unchanged by this task). Restored
    the fixed text -> **19 passed**. Proves every assertion is live, not
    tautological. Restore confirmed by grep (`_FIXED_NOW` x6 present,
    live `datetime.now(UTC)` 0) and by the green re-run.
  * **Regression slices, all green:**
    * ML-QA-004 + 007 + 008 + 010 batteries + module: **86 passed**.
    * Outcome/repair/reconcile domain (10 modules:
      `test_outcome_recovery_sweep_bug140`, `test_bug261_terminal_outcome_tick_domain`,
      `test_missing_outcome_backfill_bug174`, `test_forensic_repair_account_and_audit`,
      `test_recon_requote_and_reconcile`, `test_experience_provenance_contract`,
      `test_audit_ledger_export_w3`, `test_inv001_tick_path_ledger_enrichment`,
      + the two changed files): **106 passed** in 48.89 s.
  * `ruff check` clean; `ruff format --check` clean (2 files reformatted by
    `ruff format`, not by hand); `mypy` **Success: no issues found in 2 source
    files**.
  * `verify_critical_suite_manifest.py` -> **CRITICAL_SUITE_MANIFEST_OK: 246
    paths all exist**; `check_merge_marker_residue.py` clean (3539 tracked files);
    `check_duplicate_task_rows.py` -> **DUPLICATE_TASK_ROWS_OK** (2 tables);
    `check_dependency_drift.py` -> **OK (100 pins**, lock untouched).

## NOT_CHANGED (deliberately)

  * No `.github/workflows/*` touched (HARD CONSTRAINT) — CI enforcement rides
    the critical-suite test.
  * No production source touched (tests/ only). `_FrozenClock` patches the
    production module's `datetime` *name* from a test; the module itself is
    byte-identical to `origin/main`.
  * The 2 residual `uuid4()` stamps in `test_smoke_chain.py` left alone: they
    construct proposal `request_id`s that no assertion reads.
  * Roster candidate #2 (`test_training_env_worker.py`, 2 `getpid()` asserts)
    and #9 (`test_runtime_config_hot_reload.py`, 2 `getpid()` + 1 `mkdtemp`)
    deferred: both assert the *liveness/invariance* a pid proves (same process
    across a hot reload), not its literal value — correct in spirit, small, and
    a cleaner fit for a dedicated cycle than a batch.

## RECOUNT EVIDENCE (why the roster target moved)

Roster §5 top-20 rescanned at `03d2fede` with the ML-QA-003 stdlib scanner.
Already remediated (0 sources): `test_smoke_chain.py` (was 44),
`test_perf_r4_runtime_loop_offload.py` (was 5), `test_experiment_registry.py`
(was 4), `test_strategy_factory_phase22.py` (was 2). New top exposures:
`test_bug262_close_time_evidence.py` 15, `test_shadow70_safety.py` 8,
`test_outcome_flush_race_bug140.py` 7, `test_smoke_chain.py` 7 residual (2 uuid +
5 docstring-only `datetime.now`), `test_training_env_worker.py` 2.
