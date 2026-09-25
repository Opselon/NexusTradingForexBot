# CONTRACT — MT5 watchdog reconnect storm + boot DB-state ambiguity (NSE)

Single-contract multi-agent fix. One contract file, two file-disjoint lanes.
**Tests first**, then main code (TDD per skill). Every gate must be REAL.

## Worktree / environment (read this first, you have no other context)

- Worktree: `C:/c/tmp/nse-mt5fix`  (branch `agent/hermes/mt5-stall-and-db-state`, base `origin/main` tip `e04123d1`)
- Repo root used by the LIVE engine: `C:/Users/Capsizer/source/repos/nse-review-main`
- Python for tests: `C:/Users/Capsizer/source/repos/NexusTradingForexBot/.venv/Scripts/python.exe` (3.11, pytest 9.1.1).
  Its editable install pins `nexus_scalp` to the **NexusTradingForexBot checkout's `src/`** — so a worktree run
  MUST set `PYTHONPATH=src` (skill: "Worktree repro MUST set PYTHONPATH=src"). Verify with
  `python -c "import nexus_scalp;print(nexus_scalp.__file__)"` — it MUST print the worktree path.
- pytest on this box: `-p no:cacheprovider -o addopts= -q`, output REDIRECTED to a file, then read the file.
  Never trust the console. Do NOT run `-n auto` (xdist flakes on this loaded box; skill: xdist-only flakes).
- Gate order before pushing: `ruff format` → `ruff check` → `mypy src` → targeted pytest. CI gate is
  `Code Quality & Tests` (ci.yml); repo is SQUASH-merge only.

## Lane A — MT5 watchdog reconnect storm (RT-008)

**Evidence (live log, pasted 2026-09-25 04:11 IST):**

```
04:11:22 [WATCHDOG] Tick stream stalled while MT5 reports connected (is_connected=True). Forcing resubscribe...
04:11:28 [WATCHDOG] Tick stream stalled ... (again)
04:11:33 [WARNING] Tick stream stalled and MT5 disconnected. -> healthcheck & auto-reconnect
04:11:33 MetaTrader 5 IPC connection closed.
04:11:39 [MT5_CONNECT] RETRY attempt=1/3 retcode=(-10005,'IPC timeout') backoff_ms=250
04:11:44 [MT5_CONNECT] RETRY attempt=2/3 retcode=(-10005,'IPC timeout') backoff_ms=500
04:11:50 Failed to initialize ... after 3 attempts. Last retcode: (-10005, 'IPC timeout')
04:11:50 [RESYNC] SKIPPED reason=NO_BROKER_BARS
04:11:50 [error] Error in live loop ... RuntimeError: Failed to fetch tick 'XAUUSD' ... 'adapter not connected'
```

**Root cause (PROVEN against `src/nexus_scalp/application/live/runtime_loop.py:269` at HEAD):**
the watchdog fires on `_stall_age_sec > 15.0` where `_stall_age_sec = now - self.om._last_fresh_tick_at`.
After a **failed** reconnect the loop does `self.om._last_tick_processed_time = time.time()` (line 356)
but NEVER updates `self.om._last_fresh_tick_at` — only a NEW non-duplicate tick may stamp it (line 476,
correct BUG-279 semantics). So the stall clock stays old and the watchdog re-fires on essentially the
next 0.05s loop iteration, calling `adapter.disconnect() + connect()` on every pass. Each
`DirectMT5Adapter.connect()` calls `mt5.initialize()` (up to `_retries` attempts, 250/500/750ms backoff),
and a reconnect storm on the single process-global MT5 IPC handle produces IPC timeout `-10005`.

**Scope of the fix (fail-closed, minimal, no behavior change on the healthy path):**
introduce a bounded reconnect backoff in `RuntimeLoop`. On a watchdog-triggered reconnect that does NOT
restore ticks, suppress the next reconnect attempt until a backoff window has elapsed, escalating the
window per consecutive failed reconnect (e.g. base 5s, x2 per failure, capped 300s). A reconnect that
succeeds resets the counter. Never block the loop longer than the backoff; the loop keeps polling ticks so
a recovered feed is detected immediately. Do NOT change `_last_fresh_tick_at` semantics (BUG-279 invariant).
Do NOT silence or remove the stall watchdog. Add NO new state to `LiveEngine` beyond a small,
well-named set of attributes read/written only by this module; document them.

**Files (Lane A owns these exclusively):**
- `src/nexus_scalp/application/live/runtime_loop.py` (fix)
- `tests/unit/test_rt008_watchdog_reconnect_backoff.py` (NEW — tests first, then the fix)

**Test contract (tests must fail RED before the fix, GREEN after):**
1. A stalled feed with a FAILING `connect()` produces at most ONE reconnect attempt per backoff window
   (assert attempt count over a compressed simulated time window), not one per loop iteration.
2. Backoff ESCALATES on consecutive failures (window grows) and RESETS to the base after a successful reconnect.
3. The stall clock `_last_fresh_tick_at` is NEVER written by the watchdog/reconnect path (BUG-279 preserved).
4. A recovered feed (new non-duplicate tick) is still handled immediately — backoff never starves recovery.
5. A healthy ticking feed never triggers any reconnect (no regression).
Use `asyncio` fakes / a stub `om` with `adapter` doubles; never touch a real MT5 terminal. Use
`unittest.mock` or hand-rolled doubles; patch `time.time`/`asyncio.sleep` or inject a clock so the test is
deterministic and < 5s wall-clock. **No real sleeps, no real network, no real MT5.**

## Lane B — boot safety-state read ambiguity (RT-009)

**Evidence (live log):**

```
04:10:57.866 [DB-FABRIC] audit provider read degraded op=get_trading_rules ... provider_read_degraded_total=1
04:11:01.487 [error] runtime_risk_state persist FAILED state=RUNNING error=no such table: runtime_risk_state
04:11:01.490 [error] breaker anchor read FAILED (treated as absent) error=no such table: runtime_risk_state
```

**Root cause (PROVEN):** the persisted `database.provider` is `postgresql`
(`%LOCALAPPDATA%/NexusScalpEngine/databases/app_settings.db` → `('database.provider','postgresql','USER_SETTINGS')`),
so `AuditRepository._db_path == ""`. `set_runtime_risk_state` / `get_breaker_anchors` unconditionally used
`self._connect_sqlite(...)` (audit_repository.py:1202 / :1250 at the OLD base) → `sqlite3.connect("")`,
a throwaway temp DB destroyed on close: the write raised `no such table` and the breaker read returned None
and was logged "treated as absent".

**Already fixed upstream** — PRs #458 (RT-007) and #460 (PG-READ-PLANE-001) are ON `origin/main` (our base)
and are present in the worktree: safety writes now go through the pooled write backend
(`_atomic_provider_write`), learning-cycles path resolution uses a real file, and the read plane is
registered on provision. The live engine in the pasted log was running the STALE checkout, not main.

**Lane B scope (verification + a boot-honesty regression test, NOT a rewrite):**
1. On `origin/main`, prove the PG provider path persists + reads back `runtime_risk_state` and breaker
   anchors (extend `tests/unit/test_pg_safety_persistence.py` if a gap exists; do NOT weaken it).
2. Add a NEW regression test asserting the boot contract end-to-end at the seam that matters:
   `LiveEngine._restore_runtime_risk_state()` must decode a read failure as `DB_READ_UNCERTAIN`
   (fail-closed) and a persisted `HALTED` row as `HALTED` (trading refused) — for BOTH providers.
   Drive it through `AuditRepository.get_runtime_risk_state()` with a provider stub; never mock out the
   decision logic itself.
3. Do NOT re-implement the upstream fixes. If a sub-path is still wrong on main, fix ONLY that sub-path
   and say exactly why in the commit body.

**Files (Lane B owns these exclusively):**
- `tests/unit/test_rt009_boot_safety_state.py` (NEW)
- `tests/unit/test_pg_safety_persistence.py` (extend ONLY if a real gap exists)

## Shared invariants (both lanes — do not violate)

- **Tick hot path**: no blocking DB/network/IO in the per-tick path; watchdog work stays minimal.
- **Fail closed**: any uncertainty about persisted safety state → trading REFUSED, never RUNNING.
- **BUG-279**: only a NEW non-duplicate tick may stamp `_last_fresh_tick_at`.
- **SQLite is unchanged**: provider=sqlite keeps byte-for-byte behavior (`_is_sqlite` short-circuits first).
- **No new public API on LiveEngine** without documenting it; prefer module-local state in `RuntimeLoop`.
- **Never weaken a test, golden, or CI check to get green.** A red on a timing/log suite that also fails
  on origin/main serially is pre-existing — report it, do not "fix" it (skill: xdist-only flakes).

## Verification protocol (each lane, before pushing)

1. `ruff format` then `ruff check` then `mypy src` (all on the worktree).
2. Targeted pytest: your NEW test files + the suites you touched, run SERIALLY, output to a file.
3. A/B check: run the same NEW test on a pristine `origin/main` worktree — it MUST be RED there (proving
   the test is real, not a self-fulfilling mock).
4. `git diff --name-only` + `git diff --cached --name-only`: only your lane's files.
5. Commit coherently (`fix(runtime): ...`), conventional-commit scoped by domain.

## Integration lane (after both lanes land on this branch)

Run the exact gate CI runs, then push, open ONE PR for the contract, and drive CI to green:
`Code Quality & Tests` must pass. Merge style is SQUASH only. Report the real evidence.
