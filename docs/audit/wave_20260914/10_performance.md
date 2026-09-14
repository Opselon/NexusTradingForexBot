# Wave 2026-09-14 — Lane 10: Performance / Latency Audit (with measurements)

- Repo: NexusTradingForexBot @ `nse/master-active-scalper-wave` (9431edd2), git READ-ONLY (no mutations).
- Scope: static sweep (WAL, background writer, INV-001 sync-DB grep, cache bounds, queue bounds, news cadence, repeated compute, log volume, DB growth, inference frequency) + dynamic measurements (import time, enqueue/flush throughput, feature/liquidity/inference micro-benchmarks, 2 fast perf unit-test files).
- Method labels: **VERIFIED** = executed command in this session; **STATIC** = code read at HEAD; GPU/RTX training untouched (all probes CPU-only, `CUDA_VISIBLE_DEVICES=""` where torch used).

---

## 1. SQLite WAL usage per adapter/store (STATIC + VERIFIED pragmas in code)

| Store | journal_mode | synchronous | busy_timeout | Notes |
|---|---|---|---|---|
| Audit DB (`adapters/database/audit_repository.py:251-255`) | WAL (MEMORY for `file::memory:?cache=shared`) | NORMAL | connect timeout 10s (`_connect_sqlite`) | `temp_store=MEMORY` too |
| Generic SQLite driver (`database/drivers/sqlite_driver.py:120-125`) | WAL | NORMAL | — | setup-only |
| Candle intelligence (`candle_intelligence/store.py:394-421`) | WAL | NORMAL | — | reader+writer conns |
| News DB (`news/db_schema.py:289-290`) | WAL | NORMAL | — | |
| Executions idempotency (`adapters/database/executions_idempotency.py:199-224`) | (inherits file WAL) | — | **10 000 ms** | `BEGIN IMMEDIATE` |
| Hygiene worker/runner (`hygiene/worker.py:431`, `worker_runner.py:182`) | inherits | — | 2 000 ms, "DEFER, never force" | |
| Learning cycle (`model_lifecycle/learning_cycle.py:111` etc.) | inherits | — | 5 000 ms | background threads |
| ~60 raw `sqlite3.connect` sites (experience/*, governance/*, incidents/*, research/*, settings, marketplace, web/*) | **no PRAGMAs of their own** | — | default | WAL is persistent per-file so they still ride WAL; settings/marketplace DBs have no WAL setup found |

Live WAL sidecars are 0 B (`artifacts/audit.db-wal`, `news.db-wal`) → checkpointing healthy, no WAL bloat. **No WAL violation found; the gap is per-call connection churn** (every raw connect opens/closes a connection — cheap under WAL but see risks R5/R6).

## 2. AuditRepository background writer (VERIFIED measurements)

Structure (`audit_repository.py`): single worker thread (`_start_background_worker:1994`, `_process_queue_worker:2002`), `queue.Queue(maxsize=10000)` (:169), batches ≤500 with `itertools.groupby` + `executemany` (bulk transactions), non-blocking drain + first-record blocking get (idle-wall fix), per-row salvage + durable dead-letter on batch failure (:2046-2096), 1s error backoff.
Criticality-aware enqueue (:2144-2250): financial rows — soft watermark qsize≥8000 (metric), **blocking put up to `max(flush_interval*2, 2.0)` = 2.0 s at qsize≥9000**, then durable overflow file `artifacts/audit_overflow/` (one JSON file per row, `_dead_letter_seq` name); telemetry — `put_nowait`, counted drop.

Measured (in-memory audit DB, `sqlite:///:memory:`, CPU):
- `log_signal` enqueue: **p50 = 71 µs, p95 = 161 µs, max 1.06 ms** over 2050 calls.
- Worker flush throughput: **≈185 500 rows/s** (2050 rows drained in 11 ms).
- Queue headroom: at ~1 signal/tick × ~12.5k financial events/day the 10k queue fills only if the writer thread stalls (locked DB / disk). But when it does, the **2 s blocking-put floor (`audit_repository.py:2184-2185`) is a hot-path stall the code comment ("worst case costs one flush interval", :2155) understates by 2×**, and `artifacts/audit_overflow/` has **no reader** — grepped: referenced only at `:2158` (dir constant) — overflowed rows never return to the DB and the directory grows unbounded (one file per event; currently absent = never exercised).

## 3. INV-001 — sync DB/IO in the tick hot path (grep sweep)

Tick path: `runtime_loop.py:376 get_last_tick` (sync adapter RPC) → `:425 _process_tick_pipeline` → `live_engine.py:3087` → `tick_pipeline.run_pre_policy_stages/run_post_policy_stages` → `decision_executor`. `_process_tick_pipeline` runs **on the event loop thread** (not `to_thread`) — by design orchestration-only, DB-free.

Violations / gray zones found (all STATIC at HEAD):
1. `experience/intelligence.py:475` → `evaluator.py:603-616`: tier-3 fallback opens a **sync `sqlite3.connect` + SELECT** on the pre-trade path when the ≤1/s inline-refresh budget is exhausted (genuine per-proposal DB read on the loop thread, rare but real; also per-call connect churn).
2. `signals/policy.py` C3 gate (b) → `live_engine.py:1627 _session_spread_percentile_provider`: **bounded read-only SELECT** inside policy evaluation when a candidate is live-spread-positive. Documented INV-001 exception (comment :1397-1405), but it is still a synchronous query on the decision path.
3. `audit_repository.py:2376`: `print(json.dumps(unknown_log))` — **sync stdout write per UNKNOWN-regime signal** on the hot path (a blocked console/pipe stalls the tick loop; `logger.warning` already covers it).
4. `audit_repository.py:2181`: the 2 s blocking financial put (§2) — bounded but 2× the documented worst case.
5. `runtime_loop.py:330/376`: `get_account_info` (5 s throttle) + `get_last_tick` are sync adapter calls on the event loop; with `RemoteMT5Adapter` each is an HTTP request (`remote_gateway.py:129`, `timeout_seconds=3.0` :49) — a gateway hiccup stalls the loop up to 3 s (tick polling architecture; the loop is single-task so maintenance/SSE also wait).
6. `audit.log_signal` / `log_order` / `log_execution` — queue puts only (measured 71 µs p50) ✔; incident telemetry queued ✔; `model_bundle_store.py:320-368` sync sqlite + full-file sha256 = **boot/hot-swap path only** (callers `:286-290`), not per-tick ✔; reconciliation `has_ledger_opened` (`reconciliation.py:114`) = resync path, not per-tick ✔.
7. Warmup-incomplete fail-closed path calls `audit.log_signal` per tick-blocked-tick — fine (queue put), but combined with #3 each such signal prints.

Rule matrix TTL 5 s (`rule_matrix.py:36`), experience score TTL + ≤1/s budget (`intelligence.py:129-130`) confirmed as documented in INV-001.

## 4. Cache bounds — unbounded dict/deque scan in `src/` (STATIC)

`deque(` without `maxlen`: **1 site** (`bar_handler.py:271`) — but it rebuilds with `maxlen=_before` (preserves the bounded rolling buffer) → not a leak.
Unbounded / weakly-bounded instance dicts found among 111 empty-dict initializers:

| Site | Bound | Verdict |
|---|---|---|
| `order_manager.py:510 _processed_orders` (written `dispatch.py:144/512/586`) | never pruned (grep: no del/pop for it; "stays manager-owned" :992; surfaced in debug as `processed_orders_count` `debug_snapshot.py:1113`) | **UNBOUNDED** (grows per dispatch, since-inception) |
| `accounting/core.py:90 _report_cache` | keyed `{kind}:{bounds.key}`, no eviction (:481/:528) | **UNBOUNDED key space** (dates churn) |
| `experience/intelligence.py:132 _score_cache` | TTL on read, no prune; bounded by strategy cardinality | soft-unbounded, low risk |
| `governance/engine.py:104 _health_cache`, `signals/rule_matrix.py:32 _rules_cache`, `features/scalp_features.py:508 _htf_cache_value` | overwritten single value | OK (test pins: `htf_cache_bounded_single_entry`) |
| `web/replay_routes.py:71 sessions` | `_MAX_SESSIONS` LRU-ish pop (:80-81) | OK |
| `incidents/correlator.py:252 _recent_keys` | capacity 20 000 + age prune (:395-397) | OK |
| per-ticket dicts (position_tracker, recovery_budget, hold_score_ledger, telemetry_throttle, ticket_state, position_state_machine, protection_ledger, pending_orders) | popped by `_cleanup_ticket_state` bundle (order_manager:4181-4238) | OK |
| `telegram_notifier.py:302-303 _recent_messages/_sent_timestamps` | dedup dict pruned (:722) | `_sent_timestamps: list[float]` shows no prune in grep — small leak class (rate-window list) |
| `news/worker.py:67-68 _retries/_enqueue_ts` | popped on success/exhaustion/drain | OK for drained jobs; entries for never-queued-again articles are removed on drain ✔ |

## 5. Queue bounds + overflow policy (STATIC)

| Queue | Bound | Overflow policy |
|---|---|---|
| Audit write (`audit_repository.py:169`) | 10 000 | financial: watermark 8k → block ≤2 s at 9k → durable overflow file (**no re-ingest**); telemetry: drop+count |
| Candle write (`candle_intelligence/store.py:328`) | 20 000 | `enqueue` returns False on Full (caller-visible drop; ring mirrors accepted) |
| News jobs (`news/worker.py:64`) | `max_queue_size` default 1 000 (config 10–10 000) | drop-with-warning + priority + `JOB_EXPIRY_SEC` stale-drop |
| Shadow70 (`shadow/shadow70/worker.py:60`) | `DEFAULT_MAX_QUEUE` | drop + `dropped` counter + telemetry |
| Telegram (`observability/telegram_notifier.py:291`) | 100 | bounded; dormant when disabled |
All bounded — no unbounded `queue.Queue()` found in `src/`.

## 6. News polling cadence (VERIFIED config + code)

Worker cycle 60 s (`configs/base.yaml:85`, `runtime_config.py:169`); per-source intervals fast 300 s / medium 900 s / slow 3600 s (`base.yaml:86-89`, `ingest/fetcher.py:46` default 300); `ingest_cycle(max_sources=8)`, analyze limit 200/cycle (`worker.py:184-201`); context rebuild via `context.refresh()` in worker, tick path cache-only (`news/context.py:52-62` ✔ INV-001). Cadence is conservative; no per-tick news I/O.

## 7. Repeated feature computation (VERIFIED micro-benchmarks, CPU)

- `ScalpFeatureEngine.compute_from_bars` @2000 bars, HTF memo cache HIT (same window): **p50 = 0.3 ms, p95 = 0.7 ms** (60 reps) — cache works.
- Growing window (HTF re-aggregation on each new bar): **p50 = 12.2 ms, max 26.2 ms** (40 reps) — paid once per M1 bar, acceptable.
- `get_completed_bars()` copy of 4000 bars: **p50 = 6 µs** ×2/tick — fine.
- Liquidity governor full compute (`compute_liquidity_features`, the per-new-bar call from `live_engine._warm_liquidity_from_bars`): **@1000 bars 66 ms / @2000 bars 125 ms / @4000 bars p50 228–311 ms**, measured on the loop thread cadence; code notes 20 k-bar window ≈1.1–1.4 s (`features/liquidity_runtime.py:537-543`). Per-bar 0.2–1.4 s spike on the bar-close tick = the largest remaining hot-path latency spike.
- Inference frequency: **once per tick, single reuse** — `tick_pipeline.py:502` computes `probs_for_mgmt` then reuses for policy (comment "model runs once per tick" :575). Forward pass (real 70D champion state_dict → `ScalpNet`, CPU, 2 threads): **p50 = 289 µs, p95 = 943 µs, max 3.4 ms** (200 reps); `torch.load` 11 ms. Not a bottleneck; GC/JIT tail (3.4 ms) acceptable.
- `_sync_runtime_config` per tick rebuilds `AlgoConfig` twice + copies `RiskConfig` (`live_engine.py:2917-2940`) — pydantic construction ×3 per tick; measurable (~10s of µs) candidate for memoizing on snapshot identity.

## 8. Logging volume (VERIFIED du + config read)

- `logs/` tree total **7.7 MB**: severity/day files (`observability/logging.py:489 DatedRotatingFileHandler`), 10 MB/part split, daily roll, retention info/warning 30 d, error 90 d, critical 365 d, gzip after 2 d, 500 MB/severity budget, hourly prune re-arm. Busiest day `logs/info/2026/09/2026-09-13.log` = **5.6 MB/day** — healthy after the EXEC_TRACE rate-limit (was 20 249 lines/15 h; `signals/policy.py:1435`).
- File writes are **synchronous appends on the emitting thread** (`logging.py:42-46`) — every hot-path INFO costs a locked `write()` syscall; at current volume fine, but see R9 print() and the UNKNOWN-regime path.
- `artifacts/engine_restart_f73c368.log` = **22 MB** (+ 1.4 MB sibling): no in-repo writer found (grep) — external launcher redirect, **no rotation**. Watch item, outside `logs/` retention.

## 9. DB growth (VERIFIED, read-only `mode=ro` probes)

| File | Size | Growth evidence |
|---|---|---|
| `artifacts/audit.db` | 126 MB (126.0 MB page_size×page_count) | `audit_signals` 1 469 rows / 4.84 d = **303/day**; `account_snapshots` 5 273 / 27.8 d = 190/day; `position_lifecycle_events` 2 397 / 22.6 d; `research_events` 82 289 rows but span ends 2026-08-27 (research-burst writes, **no purge path** — `purge_old_audit_data:3530-3533` deliberately never touches research/ledger/experience); freelist 2 207 pages ≈ 10 MB reclaimable |
| `artifacts/news.db` | **223 MB** | `news_articles` 28 446 rows / 23.5 d = **1 211/day**, ~9.5 MB/day; `news_entities` 58 600, `news_topics` 44 689, `news_analysis` 25 070, `news_analyzed_hashes` 22 280 — age-based retention **absent** unless pro-auto junk pruning runs (`news/pro_auto.py:712+` deletes only junk articles when auto-analysis enabled) |
| `artifacts/strategies.db` | 27 MB | factory store, no prune grep hits |
| WAL sidecars | 0 B | checkpointing OK |

`audit_ledger` spans 250 d at 371 rows — accounting-truth tables grow slowly and intentionally. The dominant growth risk is **news.db** (~350 MB/month) and the never-pruned `research_*` tables (~130 MB at burst rates).

## 10. Import-time / boot latency (VERIFIED)

- `import nexus_scalp` (package root): **1 ms** — no eager side effects ✔ (doctor comment `cli/doctor.py:67` warns of eager subsystems; root itself is clean).
- `import nexus_scalp.application.live_engine`: **3 073 ms cold / 3 194 ms warm** (−X importtime: torch cumulative **1.91 s** of it, `torch._meta_registrations` 0.46 s chain; `nexus_scalp.configuration` 0.22 s).
- `import nexus_scalp.adapters.database.audit_repository`: **379 ms**.
- Boot ≈ 3.1 s Python import before any engine work; acceptable for a long-lived engine, worth pinning in CI perf budget.

## 11. Dynamic test runs (VERIFIED, executed)

`./.venv/Scripts/python.exe -m pytest tests/unit/test_agent16_hotpath_perf.py tests/unit/test_perf_exec_trace_throttle.py -p no:cacheprovider` → **13 passed, EXIT=0**, slowest test 0.01 s (output redirected to file per repo convention). Hot-path perf regression net is green at HEAD.

---

## Top-10 hot-path / latency risks → bounded fixes

1. **R1 (P1) 2 s hot-path blocking put on audit backpressure** — `audit_repository.py:2184-2185` (`max(self._flush_interval*2, 2.0)` floor ignores flush_interval; comment at :2155 claims one flush interval). Fix: replace floor with `min(0.1 s, flush_interval)` blocking window, then overflow file + existing counter; keeps durability order, caps stall at 100 ms.
2. **R2 (P1) `artifacts/audit_overflow` is write-only** — `audit_repository.py:2158,2209` (no reader anywhere in `src/`). Financial rows stranded; per-file growth unbounded. Fix: boot-time drain job (parse `query+args`, re-enqueue, rename-then-delete, bounded batch) + file-count/alert cap.
3. **R3 (P1) Liquidity full-window compute on the loop thread at bar close — measured 0.23–0.31 s @4 k bars (≈1.1–1.4 s @20 k)** — `live_engine.py:3045 _warm_liquidity_from_bars` ← `tick_pipeline.py:408-433`, cost `features/liquidity_runtime.py:537-548`. Fix: compute in the existing worker-thread pattern (`_kick_worker`) with atomic snapshot swap (idempotent per bar ⇒ safe); or window-trim inputs to the causal horizon actually used.
4. **R4 (P2) Sync MT5/gateway RPCs on the event loop every iteration** — `runtime_loop.py:376` + `remote_gateway.py:129,49` (3 s timeouts). A slow gateway freezes tick+maintenance+SSE for up to 3 s. Fix: `asyncio.wait_for(..., 0.25 s)` wrapper via `to_thread` for poll-only calls, cached last-tick on timeout; keep ordering gate.
5. **R5 (P2) Sync sqlite in pre-trade tier-3 fallback** — `intelligence.py:475` → `evaluator.py:603-616` (connect+SELECT per entry-candidate when ≤1/s budget exhausted). Fix: TTL'd in-memory registry copy refreshed by the accounting worker; proposal path becomes dict read only.
6. **R6 (P2) C3 spread-percentile SELECT inside policy evaluation** — `live_engine.py:1627-1640` (documented, bounded, but still loop-thread I/O + per-call connect). Fix: maintain the same-day spread-percentile sketch incrementally off-loop (audit worker or maintenance tick), expose RAM read; refresh cadence ≤60 s.
7. **R7 (P3) Unbounded `_processed_orders` idempotency dict** — `order_manager.py:510`, writes `dispatch.py:144/512/586`, never pruned. Fix: TTL/LRU cap (e.g. 50 k entries or 7-day window; duplicate-dispatch threats are same-session) — or delegate to `executions_idempotency` which is already durable+indexed.
8. **R8 (P3) Unbounded `_report_cache`** — `accounting/core.py:90` keys `{kind}:{bounds.key}` with no eviction (:481/:528). Fix: `maxsize`-bounded LRU (≤128) + invalidate on ledger-open/close events.
9. **R9 (P3) `print(json.dumps(...))` per UNKNOWN-regime signal on the hot path** — `audit_repository.py:2376`. Blocking stdout + duplicate of the structured warning at :2374. Fix: delete the print (or route through a ≥10 s throttle); never write stdout from the tick path.
10. **R10 (P3) news.db ~9.5 MB/day with no age retention outside pro-auto** — `news.db` 223 MB/23.5 d (VERIFIED ro probe); prune DELETEs only in `pro_auto.py:712+`. Fix: daily bounded batched prune (articles/analysis/entities/topics older than N days, `news_prune_audit` already exists for bookkeeping; reuse `dead_letter_store` batch pattern). Same class: `research_events` (82 k rows) has no purge scope in `purge_old_audit_data` — add an opt-in research-burst retention lane.

Watchlist (not top-10): `telegram_notifier._sent_timestamps` list prune verification; `_sync_runtime_config` triple pydantic rebuild per tick (memoize on snapshot identity); 22 MB unrotated `artifacts/engine_restart_*.log` (external launcher, out of repo control); ~10 MB audit.db freelist (one-off `PRAGMA incremental_vacuum` via hygiene); per-call connect churn across ~60 raw `sqlite3.connect` sites (thread-local connection pool).

## Reproduction (all executed from repo root)

```
./.venv/Scripts/python.exe -m pytest tests/unit/test_agent16_hotpath_perf.py \
    tests/unit/test_perf_exec_trace_throttle.py -p no:cacheprovider -q   # 13 passed
./.venv/Scripts/python.exe -c "import time,sys;t=time.perf_counter();import nexus_scalp.application.live_engine;print((time.perf_counter()-t)*1000)"  # 3073/3194 ms
# micro-benchmarks: scratch/perf_tick_probe.py (deleted after run; scratch/ is gitignored)
#   compute_from_bars p50 0.3 ms @2000 bars cached / 12.2 ms re-aggregating
#   log_signal enqueue p50 71 µs; flush ≈185k rows/s; get_completed_bars copy 6 µs
#   compute_liquidity_features 66/125/228-311 ms @1k/2k/4k bars
#   ScalpNet(70D) forward p50 289 µs p95 943 µs max 3.4 ms (CPU, 2 threads)
# ro DB probes: sqlite3.connect("file:artifacts/…?mode=ro", uri=True) row counts + spans (§9)
```

Git: read-only throughout (`rev-parse` only). Only file written: this report.
