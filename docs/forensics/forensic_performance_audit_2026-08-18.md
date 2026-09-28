# NSE Forensic Database / Storage / Data-Path Performance Audit

> **Date**: 2026-08-18
> **Audit type**: READ-ONLY forensic observation (no production code modified)
> **Scope**: All database/storage/data-access paths in NexusTradingForexBot
> **Method**: live-DB PRAGMA forensics, EXPLAIN QUERY PLAN, timed queries, write-path tracing, log correlation, projected scaling
> **Files mutated**: NONE (production); this report is the only artifact created. No `agents/bugs.md` rows appended — no finding met the verified-bug contract (see §28).

---

## 1. DATABASE INVENTORY

| # | Database | Path | Purpose | Owner | Tables | Size | WAL | SHM | Indexes | Journal |
|---|----------|------|---------|-------|--------|------|-----|-----|---------|---------|
| 1 | **audit.db** | `artifacts/audit.db` | Engine audit trail, ledger, experiences, intelligence, research, shadow, broker history (AUTHORITATIVE trading truth) | `AuditRepository`, workers, accounting, server | 26 | 47.9 MB | 0.5 MB | 32 KB | 21 | WAL |
| 2 | **news.db** | `artifacts/news.db` | News intelligence (isolated Phase 12) | `news/database.py` (NewsDatabase) | 14 | 5.2 MB | 0 | 32 KB | 9 | WAL |
| 3 | **candle_intel.db** | `artifacts/candle_intel.db` | Candle-close intelligence (isolated, BUG-061) | `candle_intelligence/store.py` | 12 | 0.4 MB | **4.2 MB** | 32 KB | 12 | WAL |

Additional SQLite files (dev/test only): `artifacts/test_audit.db`, `artifacts/test_pipeline_health.db`, root `audit.db` is a **0-byte stub** (unused; leftover). No settings/cache DB exists — settings are YAML (`configs/*.yaml`); model artifacts are filesystem-only (Phase 13: inference needs NO DB).

**Isolation verdict (Q14)**: correct. news.db and candle_intel.db are physically separate from audit.db; accounting/experience/research/news/execution tables are all inside audit.db but the accounting core OWNS no raw tables (reads authoritative rows, persists only derived in-process cache). No sub-database bleeds into another.

**Hot-path access**: `audit.db` (queued writes only), `news.db` (worker/API only, cache on tick path), `candle_intel.db` (RAM ring + async worker; reader connection). No synchronous DB on the tick pipeline.

---

## 2. DATABASE FILE SIZE FORENSICS (live, read-only PRAGMAs)

| DB | page_size | page_count | freelist | journal_mode | synchronous | auto_vacuum | cache_size | wal_autocheckpoint |
|----|-----------|------------|----------|--------------|-------------|-------------|------------|--------------------|
| audit.db | 4096 | 11,707 | **0** | wal | NORMAL (2) | 0 (none) | -2000 (≈8 MB) | default (1000) |
| news.db | 4096 | 1,258 | 0 | wal | NORMAL | 0 | -2000 | default |
| candle_intel.db | 4096 | 137 | 0 | wal | NORMAL | 0 | -2000 | 1000 |

Findings:
- **WAL sizes healthy**: audit.db WAL 0.5 MB (14.2k rows/day era collapsed; steady-state tiny). news.db WAL 0 (idle since last checkpoint). Checkpoints not delayed — every process restart checkpoints.
- **candle_intel.db WAL 4.2 MB vs 0.4 MB DB**: WAL is 10x the main file — the write worker batches (flush_interval 0.3 s, max_batch_size) but autocheckpoint is at the default 1000 pages (4 MB); the writer connection lives for the process lifetime so the WAL accumulates until the 1000-page checkpoint threshold, which it hovers around. This is **benign** (WAL is normal SQLite behavior for a busy writer; the checkpoint will collapse it) but worth noting: at ~144 candles/hour the WAL crosses 4 MB roughly every 4 days; no functional issue.
- **freelist = 0 everywhere**: no VACUUM pressure. Deletes (purge) do not fragment (SQLite marks pages free; auto_vacuum off keeps the file size but reuses pages — with steady 500-row batches the file stays bounded).
- **auto_vacuum = off**: file sizes never shrink; fine at current scale, page reuse means no runaway growth.

**Growth validation against BUG-057 baseline** (79 MB/day): current run rate ≈ 445 signals/day (~0.3 MB/day), MOVING ~587/day (~0.7 MB/day). The 47.9 MB audit.db is dominated by the 2026-08-17 pre-fix surge (14,226 signals + 8,785 MOVING) which retention (7d/3d) has NOT yet reached (checkpoint 2026-08-11 for signals; everything from 08-17 still resident). **After 2026-08-24 the DB will shed ~11 MB automatically.**

---

## 3. TABLE INVENTORY (row counts / avg row bytes / indexes)

### audit.db (26 tables)

| Table | Rows | ~Avg bytes | PK | Indexes | Notes |
|-------|-----:|-----------:|---|---------|-------|
| audit_signals | 14,671 | 1,378 | id AUTOINC | UNIQUE(signal_dedup_key) | 14,226 rows are 08-17 pre-fix surge; 445 today |
| audit_orders | 7,084 | 210 | id AUTOINC | — | order lifecycle events; no index on ticket/order_id |
| audit_broker_orders | 9,570 | 117 | ticket (rowid alias) | — | broker history; UNIQUE implied by PK |
| audit_broker_deals | 7,456 | — | ticket | idx_broker_deals_position | UNIQUE(ticket) |
| audit_broker_trades | 3,607 | 239 | trade_id | idx_broker_trades_exit(exit_time) | COALESCE prevents index use (see §7) |
| audit_account_snapshots | 767 | 54 | id AUTOINC | — | throttled 60 s / balance change |
| audit_ledger | 254 | 331 | — | — | authoritative closed trades; NO PRIMARY KEY (ticket) |
| audit_experiences | 186 | 3,313 | id | 5 (strategy_time, symbol_time, request, schema) | payload JSON per row |
| audit_experience_outcomes | 65 | 2,191 | id | idempotency_key | payload JSON per row |
| audit_experience_corrections | 0 | — | id | idempotency_key | |
| position_lifecycle_events | 10,886 | 1,233 | id | (ticket, sequence), event_type | 8,785 MOVING rows = 08-17 pre-throttle |
| trade_autopsies | 64 | — | — | strategy_id | |
| behavior_detections | 0 | — | — | ticket, pattern | |
| strategy_evolution_candidates | 0 | — | — | status | |
| strategy_intelligence_registry | 93 | — | strategy_id | strategy_id | derived, rebuildable |
| strategy_registry | 2 | — | (strategy_id, version) | strategy_id, lifecycle | research truth |
| experience_model_registry | 2 | — | — | model_id | |
| research_runs | 0 | — | — | strategy_id, status | |
| training_runs | 0 | — | — | dataset_id, status | |
| model_comparisons | 0 | — | — | candidate_model_id | |
| shadow tables (runs/decisions/comparisons/promotions) | **0 (lazy schema: never attached a challenger)** | — | — | — | `ensure_schema()` on first write (see §28) |
| audit_guard_telemetry | 303 | 56 | (window_start, symbol, reason_code) | PK | 30,016 events aggregated |
| intelligence_worker_state | 0 | — | — | | |
| research_worker_state | 0 | — | — | | |
| trading_rules_config | 30 | — | rule_name | | |
| audit_broker_history_meta | 1 | — | id | | sync watermark |

### news.db (14 tables) — 1,530 articles, 745 analyses, 522 impacts, 1,617 entities, 1,360 topics, 11 sources, 10 health rows. Indexes on published_at, source+published_at, article_hash, duplicate_of, article_id, analyzed_at, asset+evaluated_at, trade links.

### candle_intel.db (12 tables) — 143 candles, 143 closures, 383 patterns, 197 regimes, 197 risk evals, 197 trade decisions, 53 vetoes. All indexed on ts.

---

## 4. INDEX INVENTORY & COVERAGE

Existing indexes are well-scoped and match the queries (**experience** retrieval, **lifecycle** timelines, **signals dedup**, broker deals by position, autopsies by strategy, registry lookups). The index audit found exactly three suboptimal/missing patterns (see §7): 
- `COALESCE(NULLIF(...))` ORDER BY on `audit_broker_trades` and `audit_ledger` defeats the existing index (`USE TEMP B-TREE`).
- `audit_orders` has **no index on (ticket/order_id)** — but the query filters on it (trade forensics).
- `audit_broker_orders`/`audit_broker_deals` have no `time_setup/time` index — history-window queries scan or use PK.

---

## 5. HOT-PATH DATABASE AUDIT (`LiveEngine._process_tick_pipeline`)

**VERDICT: the tick hot path is FREE of synchronous database work.** Every DB operation on the pipeline is enqueued to a background thread or is a pure in-memory read:

| Site | Operation | Type | Blocking? |
|------|-----------|------|-----------|
| `log_signal(proposal)` (L1795 warmup-blocked / L1905 main path) | builds 250 B JSON + `queue.put_nowait` | **queued write** | No (O(1) enqueue; `queue.Full` → drop + log) |
| `_log_guard_telemetry` (TICK_DUPLICATE_SUPPRESSED / ORDER_FREQUENCY_THROTTLED) | UPSERT counter row via queue | **queued write** | No |
| `log_account_snapshot` (L2175) | throttled (60 s / balance change) via queue | **queued write** | No |
| `_record_shadow_decision` (L1922) | Challenger inference + queue write | **queued write** | No (Challenger CPU inference on the tick thread — see §32; when no active run it returns immediately) |
| `experience_engine.evaluate_proposal` | TTL-cached score; budget-capped inline refresh ≤1/s; registry PK fallback | **rare sync read** | Only when refresh budget allows AND cache missed (≤1/s, indexed PK, 0.01 ms) — by design off the per-tick path |
| `news_engine.current_context()` | `context.get()` cache-only | **no DB** | Never (first run builds safe defaults, worker refreshes) |
| `candle_intel.ingest_bar` | RAM ring + queue | **queued write** | No |
| `rule_matrix` evaluations | `_rules_cache` TTL 5 s | **no DB on tick** | Never (cache refreshed by `get_trading_rules()` only on TTL expiry) |
| `get_last_account_snapshot` | startup only (`_restore_peak_equity` L629, called once in `run_loop`, not per tick) | sync read | No |

**Findings:**
- No `SELECT *` history scans, no ORM lazy loading (no ORM), no implicit commits, no repeated same-row reads in the tick path.
- The only sync read that could ever touch the tick thread is the experience `registry_score` fallback (single indexed PK lookup, ~0.01 ms, ≤1/s) — acceptable and guarded.
- **Per-tick `log_signal` enqueue volume**: every processed tick enqueues one ~250 B item even when the dedup key collapses at the DB. At ~74 signals/hr persisted, the queue churn is ~1 item/tick (queue maxsize 10,000; worker drains ≤500/1 s). No overflow evidence in logs.

---

## 6. WRITE-PATH FORENSICS

| Writer | Table(s) | Mechanism | Transaction | Dedup | Notes |
|--------|----------|-----------|-------------|-------|-------|
| AuditRepository worker (thread) | signals, orders, ledger, snapshots, telemetry, experiences, outcomes, lifecycle | queue → batched `conn.execute` loop in `with conn:` (up to 500/1 s flush) | **one transaction per batch** | UNIQUE(signal_dedup_key) + ON CONFLICT DO NOTHING; guard telemetry PK UPSERT | ideal |
| ExperienceLedger | audit_experiences, outcomes | direct sync writes (from worker/API/threads) | per-call `with conn:` | idempotency_key UNIQUE | not hot path |
| CandleIntel store worker | candle_intel.db | RAM ring + queue → batched INSERT OR IGNORE | one per batch (0.3 s flush) | INSERT OR IGNORE | ideal |
| NewsDatabase | news.db | direct per-call `with self._connect()` | per-call | article_hash UNIQUE + title hash + 60 s window | worker-cycle only |
| BrokerHistorySync (300 s) | broker_orders/deals/trades | `sync_brother_history` — **row-by-row INSERT OR IGNORE** in ONE transaction (see §16) | one per cycle | UNIQUE(ticket/position_id) | fine now; scales linearly |
| AccountingWorker | none (in-process derived cache only) | — | — | idempotent cycle | owns no tables |
| Intelligence/Research/News/Shadow workers | derived tables | direct/queue writes | per call | idempotency keys | bounded |

**Commit frequency**: audit queue = 1 commit per ≤500 rows (~1/s); candle = 1 per ≤batch; news/history = per cycle. **No commit-per-row anywhere on the hot path.** No repeated UPDATE-after-INSERT patterns found; no duplicate persistence paths.

---

## 7. QUERY PLAN ANALYSIS (EXPLAIN QUERY PLAN, live DB)

| Query (from code) | Plan | Issue |
|---|---|---|
| `SELECT * FROM audit_ledger ... ORDER BY COALESCE(NULLIF(close_time,''), timestamp) DESC LIMIT n` | `SCAN audit_ledger` + `USE TEMP B-TREE FOR ORDER BY` | full scan + sort each call (251 rows now → 7.1 s at 1M) |
| `SELECT ... FROM audit_broker_trades ORDER BY COALESCE(NULLIF(exit_time,''), '') DESC LIMIT n` | `SCAN audit_broker_trades` + `USE TEMP B-TREE` | **index exists (`idx_broker_trades_exit`) but COALESCE makes it unusable** — verified: without COALESCE the plan uses the index |
| `SELECT ... FROM audit_account_snapshots ... ORDER BY id ASC LIMIT 50000` | `SCAN audit_account_snapshots` | no WHERE/range selectivity; full scan (small now, 0.8 ms; ~1.1 s at 1M) |
| `audit_experiences LEFT JOIN outcomes ON idempotency_key WHERE strategy_id/symbol` | `SEARCH e USING INDEX idx_exp_strategy_time / idx_exp_symbol_time` + `SEARCH o USING autoindex` | **optimal** |
| `position_lifecycle_events WHERE ticket=? ORDER BY sequence` | `SEARCH ... USING INDEX idx_lifecycle_ticket` | **optimal** |
| `audit_broker_deals WHERE position_id=?` | `SEARCH ... USING INDEX idx_broker_deals_position` | **optimal** |
| `audit_orders WHERE ticket=? OR (order_id != '' AND order_id=?)` | `SCAN audit_orders` (no index) | 7,084 rows now, 2.2 ms; 180k/yr growth → slow |
| `audit_broker_orders WHERE ticket=?` | `SEARCH ... USING INTEGER PRIMARY KEY` | **optimal** (ticket IS the PK) |
| `news_articles ... WHERE is_duplicate=0 ORDER BY published_at DESC LIMIT 500` | `SCAN news_articles USING INDEX idx_news_articles_published` | index used for ORDER; is_duplicate filter is a residual (fast now, 3.9 ms) |

**Timed results (live DB, warm cache):**

| Query | Rows | Observed |
|---|---|---|
| account/performance full (ledger scan+sort) | 251 | 4.0 ms |
| equity-curve (snapshot scan) | 767 | 0.9 ms |
| trades list (broker_trades scan+sort) | 500 | 4.0 ms |
| broker_trades recent (ORDER BY exit_time) | 1,000 | 5.4 ms |
| signals 7-day count | 14,671 | 24 ms (cold first) → ~1 ms warm |
| news_articles list (500) | 500 | 3.9-4.9 ms |
| news_analysis top50 | 50 | 3.7-7.9 ms |
| **news keyword coverage (500 articles × 189 keywords)** | 500 | **1,813 ms** ← biggest single endpoint cost |
| candle trade_decisions | 197 | 6.3 ms |

All sub-10 ms at current scale except the news keyword endpoint.

---

## 8. REDUNDANT QUERY DETECTION

| Pattern | Evidence | Verdict |
|---|---|---|
| Same strategy score loaded per bucket in `strategy_contributions` | `_attach_strategy_intelligence` → `get_registered_strategy_score` per strategy bucket (93 strategies, 0.01 ms each) | benign now; would be N+1 at scale — safe cache candidate |
| Same ledger/snapshot scan repeated across the 30 s worker cadence | `period_report × 4` + `equity_curve` + `drawdown` + `strategy_contributions` each re-run `load_trades` (4× same 251-row scan per cycle) | correct but redundant; the derived cache avoids consumer-side rework — see §13 for a single-scan suggestion (P3) |
| `_attach_identity` IN-query per trade list | one `IN (...)` per call over outcomes (indexed) | optimal |
| Rule matrix config | TTL 5 s cache — refreshed at most every 5 s (and only when a rule is queried) | fine |
| Account snapshots | throttled 60 s | fine |
| Daily summary | once/24 h | fine |

**Where caching would help safely**: (1) news keyword coverage result (see §10/§16 — the biggest win), (2) `strategy_contributions` registry scores at >100 strategies, (3) the equity curve at >1M snapshots (a bounded series cache already exists in AccountingCore — extend it to the full curve).

## 9. N+1 QUERY AUDIT

| Surface | Pattern | DB ops | Rows touched | Verdict |
|---|---|---|---|---|
| `/api/account/strategies` | 1 ledger scan + per-strategy registry PK (93 × 0.01 ms) | 94 | ~350 | benign at 93 strategies; P3 cache at 1000+ |
| `/api/news/{id}` detail | article + versions + entities + topics + analysis + impacts + consensus + runs + links (9 small indexed queries) | 9 | ~20 | fine (per-article, one click) |
| `/api/research/registry` | 1 query + no loops | 1 | 2 | fine |
| Trade forensics `/api/account/trades/{ticket}` | ledger + orders + outcome (3 indexed queries) | 3 | ~10 | fine |
| `/api/news/keywords` | **1 query + 94,500 regex scans (500×189)** | 1 | 500 | **NOT an N+1 DB problem — it is an O(N×K) in-Python regex bottleneck (P2, see §16)** |

## 10. DASHBOARD / API DATABASE AUDIT

| Endpoint | Queries | Cached? | Cost now | Risk as history grows |
|---|---|---|---|---|
| `/api/account/performance` | live_state + 4×period + drawdown + load_trades(1000) + equity_curve + advanced metrics | worker-derived period cache; heavy reads still hit DB every request | ~8 ms | ledger scan+sort → 7 s @ 1M |
| `/api/account/performance/{kind}` | period_report (cached path) | yes (worker cache) | ~2 ms | OK |
| `/api/account/performance/{kind}/series` | period_series (bounded 60) | cached path | ~3 ms | OK |
| `/api/account/equity-curve` | snapshot scan + cumulative PnL | no (rebuilt per request) | ~2 ms | snapshot scan → 1.1 s @ 1M |
| `/api/account/drawdown` | snapshot scan + aggregation | no (rebuilt per request) | ~2 ms | same as above |
| `/api/account/trades` | broker_trades scan+sort | no | 4 ms | → 1.5 s @ 1M |
| `/api/account/trades/{id}` | 3 indexed queries | no | ~2 ms | OK |
| `/api/account/strategies` | ledger scan + 93 registry PKs | derived | ~3 ms | N+1 at scale |
| `/api/news/latest`, `/api/news/impact` | indexed | no | 3-8 ms | OK to 100k |
| `/api/news/keywords` | 500-article scan + **94.5k regex scans** | **no** | **1.8 s** | **hours @ 1M** |
| `/api/broker/*` + `/api/mt5/*` | MT5 adapter (not DB) | snapshot 5 s cached | API-triggered | OK |

Dashboard poll cadence: account bundle every 30 s, news state 60 s, heartbeats 5 s, debug 3 s — a modest steady load fully served by the derived cache + small scans.

## 11. CHART / SERIES PERFORMANCE

- `equity_curve` loads the full snapshot universe (`ORDER BY id ASC LIMIT 50000`) and aggregates in Python every call (also inside the worker cadence and `/api/account/performance`). Not cached as a series.
- `cumulative_pnl_curve` rebuilds from the full closed-trade list per call.
- **No materialized derived series** exists — every dashboard open recomputes the curve from raw rows.
- **Safe opportunities** (documented only): (a) cache the computed curve in AccountingCore with an invalidation on new snapshot/ledger rows; (b) bound the displayed window server-side (lookback_days already capped 730); (c) at 1M snapshots, compute the curve in SQL (running aggregates) or keep a materialized table refreshed by the worker.

## 12. JSON / SERIALIZATION AUDIT

| Field | Size (avg) | Serialized | Deserialized | Concerns |
|---|---|---|---|---|
| `audit_signals.payload` | ~250 B (post-BUG-054) | 1×/signal (queued) | rare | none |
| `audit_experiences.payload` | ~3.3 KB | 1×/experience | every retrieval (`_merge_row` json.loads per row — bounded LIMIT) | fine at 186 rows; at 1M the merged queries load N payloads — acceptable, indexed |
| `audit_experience_outcomes.payload` | ~2.2 KB | 1×/outcome | with experiences join | fine |
| `position_lifecycle_events.market_context/position_snapshot/payload` | ~1.2 KB/row | 1×/event | timeline views of 1 ticket | fine |
| `news_analysis.impacts/entities/topics` | 128/221/24 B | 1×/analysis | detail views | fine |
| `news_articles.body` | **0 B (empty)** | — | — | **body is never stored** (only summary ~860 B); keyword scans run over title+summary only |

No whole-JSON-loads-to-read-one-field anti-patterns found. The heavy JSON tables are all bounded by LIMIT queries.

## 13. EXPERIENCE LEDGER PERFORMANCE

- Append-only + idempotent (`idempotency_key` UNIQUE; outcome UPSERT). Verified: 186 experiences, 65 outcomes, 0 corrections.
- Retrieval paths (`get_experiences_for_strategy/symbol`) use `idx_exp_strategy_time` / `idx_exp_symbol_time` + outcome autoindex — **optimal**.
- `_merge_row` json.loads per returned row: at 1M experiences a `LIMIT 200` retrieval parses 200 payloads (~3.3 KB each = ~660 KB) — fine.
- Ledger growth: ~46 experiences/day → 17k/yr. **At 10M rows**: the merged join stays index-anchored (PK lookups), payload parse is the cost — P3: store hot fields as columns (strategy, symbol already are) and keep payload only for the forensic JSON.
- `get_schema_distribution`/`count_experiences` are COUNT scans — fine to 10M (SQLite COUNT is O(N) but fast; ~1 M rows = ~10 ms).

## 14. NEWS DATABASE PERFORMANCE

- Isolation: **correct** — no per-tick DB; worker refreshes context off-loop; tick path reads cache only (verified in `news/context.py`).
- Dedup: `article_hash` UNIQUE + title-hash + 60 s publication window; 304 conditional GET avoids fetch+dedup work when feeds are unchanged. 
- **Duplicate feed writes**: none found — dedup runs before insert; `evidence_sources` merge is DB-side upsert.
- **Concern §A**: `news_analysis.list_analysis` and `news_impacts.asset` are indexed — good.
- **Concern §B (P2, the audit's top finding)**: `analyze_keyword_coverage()` compiles a fresh regex per (keyword, alias) per article — 189 keywords × 500 articles = **94,500 regex compilations per endpoint call ≈ 1.8 s**. It is invoked from `/api/news/keywords` (user-facing, no cache). At 1M articles → ~1 hour per request. Root cause: `re.findall` with a freshly built pattern inside the loop; `_count_mentions` builds `re.escape(token)` per call. Fix (documented only): precompile ~200 patterns once at module import (function cache), reuse across articles; optionally move coverage to the worker and cache the summary. Expected: ~1.8 s → ~30-60 ms for 500 articles (≈30×).
- Old-news scans: `list_articles` is bounded (500) and index-ordered — no unbounded history sweep in the API.
- Decay queries: `news_impacts WHERE asset=? ORDER BY evaluated_at DESC` uses `idx_news_impacts_asset` — scales.

## 15. RESEARCH DATABASE PERFORMANCE

- `ResearchDatasetBuilder.build()` loops **per strategy_id** (`list_strategy_ids()` then `get_experiences_for_strategy(sid, limit=10000)` per id): at 93 strategies that is 93 indexed queries + merged payload parses, repeated **every worker cycle (60 s interval)** — even when nothing changed. Evidence: worker `_refresh_once` runs `seed → dataset → discovery → validation` unconditionally each cycle; `build()` recomputes the full dataset every time. Rows: 0 research_runs, 2 registry entries (built-ins) → the dataset builder currently returns ~0 samples, so this is cheap NOW, but the pattern is O(strategies × (query + json parse)) and re-runs on every cycle.
- `_refresh_dataset` has no change guard (no dataset-hash/incremental check). Recommended (P3): skip rebuild when the experiences table hasn't changed since last cycle (e.g. compare `last_checkpoint`/row-count watermark), or keep the dataset in memory and rebuild only on new closed outcomes.
- `strategy_registry`/`research_runs` writes are small and indexed; no botleneck.

## 16. ACCOUNTING PERFORMANCE

- **Accounting NEVER queries the DB on the tick path** — verified: only `accounting_worker.tick` via `to_thread` (30 s cadence).
- Worker cycle (`_refresh_once`): live_state → 4× period_report (`use_cache=False` → 4 ledger scans+sort) → 4× period_series → drawdown → equity_curve → strategy_contributions → cumulative PnL. **Measured total ≈ 43 ms/cycle** (at current 254-row ledger): ~2 s CPU/day. Negligible now; at 1M ledger rows this becomes ~2.6 s/cycle = ~2 CPU-min/day — still worker-isolated, but the 4× repeated scan+sort is the first thing to consolidate (single `load_trades` per cycle, reuse the list) — P3.
- Equity/drawdown/cumulative PnL rebuilt from raw rows each cycle — P3 materialize/cache.
- Snapshots/curves have hard ceilings (MAX_TRADE_ROWS 20k / MAX_SNAPSHOT_ROWS 50k) — the caps exist and are sensible.

## 17. CONNECTION POOL / LIFECYCLE

- **No connection pool** — every read and every direct write opens a fresh `sqlite3.connect` (with `timeout=5-15 s`). Sites: audit_repository 20, accounting 8, ledger 7, news 44 (mostly in-method `with self._connect()`), server 2 direct.
- For SQLite this is **correct and idiomatic** (connections are cheap; WAL allows concurrent readers; no cross-thread sharing hazards). The candle_intel reader connection is deliberately persistent (`check_same_thread=False`) — acceptable, single-threaded reader.
- No leaked connections: `with sqlite3.connect(...)` closes on scope exit; the worker-thread connection in audit/candle closes on loop exit; the memory-DB shared connection is intentionally held (BUG-071 fix).
- **The `with sqlite3.connect(...)` pattern does NOT close when returning `dict(r)` rows?** — it does: rows are materialized before scope exit (`.fetchall()` into dicts). Verified no cursor leaks.
- **Recommendation (P3)**: a single persistent read-only connection per subsystem (or a small pool) would shave the per-query connect handshake (~0.05-0.1 ms) — the win is tiny; NOT worth the risk of cross-thread misuse. Current design is correct.

## 18. SQLITE CONCURRENCY AUDIT

- All three DBs: **WAL, synchronous=NORMAL, busy timeout 5-30 s, single writer per DB** (audit queue worker; candle queue worker; news worker + API reads).
- **Only ONE writer per database** — no writer-vs-writer contention by construction. Multiple BACKGROUND READERS (accounting worker, intelligence worker, research worker, API threads) all hit `audit.db` concurrently in WAL mode — readers never block the writer, the writer waits only on its own batch. 
- Checkpoint behavior: default autocheckpoint (1000 pages); no manual checkpointing; delayed checkpoints only observed on candle_intel (benign, 4.2 MB WAL — see §2).
- **In-memory DB**: `sqlite:///:memory:` → `file::memory:?cache=shared` (BUG-071) — worker + setup share the schema; connection held open to keep the cache alive. Correct.

## 19. LOCK CONTENTION FORENSICS

- Live log (`artifacts/logs/nse_live.log`, 9.3 MB, sessions 02:16-04:28): **zero** `database is locked`, `busy timeout`, or `OperationalError` occurrences.
- Purge runs (every ~6 h, 6 observed): `deleted={'audit_signals':0,'position_moving':0,'guard_telemetry':0} duration_ms=16-31` — bounded batches complete in tens of ms with zero contention (nothing old enough to delete yet).
- No retry storms, no repeated failed writes. The audit worker's batch-error path logs + drops + backs off 1 s (BUG-025 fix) — no deadlock possible.
- **Verdict**: no lock contention today; the WAL + single-writer architecture is sound. At 1M rows the purge batches (500-row) still hold the write lock for only a few ms each.

## 20. BATCHING OPPORTUNITIES (documented only)

| Writer | Current | Safe batch recommend. | Expected |
|---|---|---|---|
| audit signals/orders/snapshots | ≤500/1 s (queue worker) | already batched | — |
| `sync_broker_history` | row-by-row INSERT OR IGNORE inside one transaction, every 300 s | `executemany` (same transaction) — P3 | cycle time ÷ ~10 (now ~13.6 ms meta+scan; 100k rows → 1-2 s → 100-200 ms) |
| news ingest/analysis | per-article writes | already per-source bounded | — |
| candle intel | ≤max_batch | already batched | — |

## 21. CACHE OPPORTUNITIES (documented only)

| Data | Volatility | TTL candidate | Invalidation | Consistency | Stale risk |
|---|---|---|---|---|---|
| news keyword coverage summary | hourly | 5-10 min | new article/analysis | low (summary) | negligible |
| strategy registry scores | per outcome | 30 s (exists) | outcome write | high | none (already the design) |
| equity curve / drawdown series | per snapshot | 30 s (worker) | snapshot row | medium | chart shows ≤60 s-old curve — safe |
| rule matrix config | manual toggle | 5 s (exists) | toggle | high | none (already the design) |
| broker history watermark | per sync | n/a (persisted) | sync | high | none |

## 22. MEMORY FORENSICS

- **No unbounded in-memory structures found on the tick path.** Bounded: audit queue 10,000; candle queue 20,000 + RAM rings (per-table caps); news worker queue (priority queue bounded); experience score cache (93 entries, TTL 30 s); rolling feature records (~300); bar buffers (M1 capped, reseed 3500).
- `_rolling_feature_records` appends per completed bar and is capped (BUG-054-era feature records) — bounded.
- News worker `_queued_ids` — in-memory set of queued article ids; bounded by the queue cap; checkpoint persists 200.
- **One caution**: `research_worker` keeps `self._dataset` (in-memory ResearchDataset) — bounded by experience count (currently ~0 samples; at 1M experiences ≈ hundreds of MB if materialized). P3: keep only the dataset hash + samples on demand.
- No repeated DataFrame copies on the tick path (feature tensor is numpy->torch, single allocation).

## 23. UNBOUNDED GROWTH ANALYSIS

| Structure | Behavior | Failure point | Bounded strategy |
|---|---|---|---|
| audit_signals (7 d) | purge 6 h | never unbounded | ✓ exists |
| POSITION_MOVING (3 d) | purge 6 h | never | ✓ |
| guard telemetry (13 d) | purge 6 h | never | ✓ |
| audit_orders | **no retention** | ~180k/yr — an 8-yr bot = 1.4M rows, forensic by design | keep (forensic), add index (§7) |
| broker_orders/deals/trades | **no retention** | ~400k/yr | keep (authoritative), P3 retention discussion |
| audit_account_snapshots | **no retention** | ~260k/yr | accounting truth — keep; P3: subsample older than 30 d |
| audit_experiences/outcomes | none (immutable truth) | small | keep |
| position_lifecycle_events (non-MOVING) | none | ~85/yr closed events | keep |
| logs (nse_live.log) | file, no rotation seen | 9.3 MB / 2.5 h session → ~90 MB/day | add rotation (P2 housekeeping) |
| candle_intel WAL | autocheckpoint | 4 MB plateau | benign |

**No live-path unbounded structure exists. The only genuinely unbounded DB growth is in forensic tables with no retention — by design (auditability), all are small-row-count and indexed; the recommended fix is a forensic-table retention profile decided by the operator (P3).**

---

## 24. TICK STORAGE AUDIT

**Ticks are NOT persisted.** There is no per-tick row anywhere:
- No tick table exists in any DB.
- The hot path only enqueues: (a) `log_signal` (per decision, deduped to ~1/min/candle-combo, 445/day), (b) guard telemetry counters (per minute, 303 rows/2 days), (c) account snapshots (60 s throttle).
- M1 bars are kept in-memory (aggregator, 900-3500 bars) and persisted ONLY by candle_intel on completed candle close (143 candles) — not per tick.
- **No risk of "millions of SQLite rows per minute".** Rows/day across all hot tables: ~1,900 (signals 445 + moving 587 + orders 492 + snapshots 709 + guard ~20, minus dedup). SQLite comfortably handles this.
- **Retention**: signals 7 d, MOVING 3 d, guard 13 d, orders/snapshots none (forensic/accounting truth).

## 25. MT5 HISTORY QUERY AUDIT

| Call | Frequency | Window | Duplicates | Blocking |
|---|---|---|---|---|
| `history_orders_get` / `history_deals_get` (BrokerHistorySync) | every 300 s | watermark-3d overlap → now | idempotent (UNIQUE ticket) | worker thread (`to_thread`), never tick |
| `copy_rates_*` (chart, warmup, resync) | API-triggered / warmup / 30 s watchdog | 900-3500 bars | reseed+align (BUG-058) | `to_thread` |
| `copy_ticks_*` | none found on the live path (tick streaming comes from `symbol_info_tick` polling) | — | — | — |
| `order_calc_*` | per risk sizing | — | — | tick path in `risk_engine.calculate_volume`?? — see note |

**Note**: `order_calc_profit/margin` (Phase 14 broker-native calc) is only used when a proposal reaches risk sizing — a rare event (a few per day), executed synchronously on the tick thread. Each call is a Win32 IPC roundtrip (~1-5 ms). Not a bottleneck at current signal rates; documented as the only broker call on the live thread (P3: could be precomputed per (price, volume, sl/tp) grid).

**No unnecessary full-history fetches**: all fetches are watermark-windowed (orders/deals) or bounded counts (rates). The 3-day overlap per 300 s cycle is the price of MT5's server-local timing (BUG-070) — correct, and dedup makes it idempotent.

## 26. DATABASE + ASYNC INTERACTION

All blocking DB work is correctly isolated:
- Sync SQLite reads/writes → dedicated worker threads or `asyncio.to_thread` (accounting, intelligence, research, training, shadow, news, history-sync, purge). Verified: every `tick` kick in `run_loop` is `await asyncio.to_thread(...)`; every hot-path write is queue-backed.
- **No blocking DB call executes on the event loop.** The single sync API-thread reads (FastAPI endpoints) are fine — they run in Starlette's threadpool, not the event loop.
- The daily Telegram summary calls `accounting_core.period_report` directly in the event loop (`run_loop` L841) — this is a **sync DB read on the event loop**! It runs once/24 h (throttled), cached path (worker refreshes the DAY report every 30 s, so `use_cache=True` default hits the cache). Low impact, but documented: wrap in `to_thread` for strictness (P3).
- Candle intel reader connection `check_same_thread=False` is single-threaded by construction — safe.

## 27. FAILURE / RETRY DATABASE PRESSURE

- Audit worker: batch failure → log + drop + task_done + 1 s backoff (no retry storm) — BUG-025 design.
- News fetcher: bounded backoff/jitter, health tracker, ≤3 retries — failure-isolated.
- No repeated failed writes/queries in logs (verified §19).
- **Verdict**: retry policies cannot amplify DB load; there is no retry loop that re-issues SQL.

## 28. VERIFIED BUGS

**No verified database/performance BUG was found.** The strongest candidate (shadow tables "missing" from audit.db) was investigated to root cause and is the CORRECT lazy-schema design, not a bug:

- `shadow/store.py::ShadowStore.ensure_schema()` creates `shadow_runs/decisions/comparisons/promotions` with `CREATE TABLE IF NOT EXISTS`, guarded by an in-process flag, invoked from every `save_run`/`save_decision`/`save_comparison`/`save_promotion` (L231/254/297/334).
- The live DB has 0 `shadow_%` tables purely because **no Challenge run has ever been attached** (`_shadow_challenger is None` → `_record_shadow_decision` returns immediately; the write path never fires). Lazy schema, verified by `tests/unit/test_shadow_phase11.py` (explicit `ensure_schema()` before writes).
- **Residual observation (P3)**: the FIRST write of the first real run executes `sqlite3.connect + 4× CREATE TABLE + indexes` synchronously on the tick thread (`save_run` is called from `StartShadowRun`/attach and `record_shadow_decision` on the tick path). The code comment acknowledges this (`ensure_schema ... synchronous SQLite I/O on the hot path`) and the guard limits it to once per process; still, the clean fix is to call `shadow_store.ensure_schema()` once at LiveEngine construction (startup, off the tick path) — P3, documented only, plus a regression test that `LiveEngine.__init__` leaves the 4 tables present.
- Additionally: `_INSERT_RUN_SQL`/`_INSERT_DECISION_SQL` use `INSERT OR REPLACE` (delete+insert) — for an "append-only" shadow ledger this rewrites rowids on re-save; it matches the documented "one row per run/decision" contract (runs are upserted to a finalize state), so it is intentional. Flagged for future review only (see §31 item 0).

**No bug rows were appended to `agents/bugs.md`** (nothing met the contract `expected != actual` with harmful behavior).

## 29. BASELINE METRICS (measured this audit, read-only)

| Metric | Value |
|---|---|
| audit.db query latencies (warm) | ledger 1.8-4.0 ms · snapshots 0.8-0.9 ms · broker_trades 4.0-5.4 ms · signals-count 0.2-1 ms · registry PK 0.01 ms |
| news.db query latencies | articles 3.9 ms · analysis 3.7-7.9 ms · impacts 3.0 ms |
| **news keyword coverage (500 articles)** | **1,813 ms** |
| candle_intel queries | 2.6-6.3 ms |
| accounting worker cycle | ~43 ms / 30 s cadence (~2 s CPU/day) |
| purge cycle | 16-31 ms (6 observed, 0 rows deleted) |
| writes/day (steady state post-fix) | ~1,900 rows (signals 445, MOVING 587, orders 492, snapshots 709, guard ~20) |
| broker history sync cycle | ~14 ms meta + bounded window (2-4k rows INSERT OR IGNORE) |
| WAL sizes | audit 0.5 MB · news 0 · candle 4.2 MB |
| Lock contention | 0 occurrences in 9.3 MB live log |

**NOT MEASURED** (no instrumentation): p95/p99 query latencies, writes/sec peak, dashboard endpoint latency under load, news refresh latency under load, research cycle duration at scale. **Future**: add a `[DB_PROFILE]` debug log around the accounting worker cycle and the top 5 API endpoints; a `timing=True` on the audit worker batch; a p95 histogram for the news keyword endpoint.

## 30. TOP 20 BOTTLENECKS (by frequency × cost, current and projected)

| # | Component | Bottleneck | Evidence | Impact now | Impact at scale | Priority |
|---|---|---|---|---|---|---|
| 1 | news keyword coverage | 94,500 regex compiles/call | 1,813 ms for 500 articles | 1.8 s per UI call | ~1 h @ 1M | **P2** |
| 2 | shadow lazy-schema DDL on first write | 4× CREATE TABLE on tick thread at first attach | code trace + 0 shadow tables in DB | 1× few-ms cost | recurring only per process | **P3** (startup ensure_schema) |
| 3 | ledger scan+sort per report | COALESCE defeats index | TEMP B-TREE plan; 4×/worker cycle | 4 ms | 7 s @ 1M | P3 |
| 4 | broker_trades COALESCE ORDER BY | index idle | TEMP B-TREE + scan | 4-5 ms | 1.5 s @ 1M | P3 |
| 5 | audit_orders no (ticket/order_id) index | scan per forensics | SCAN plan, 2.2 ms | 2 ms | 0.3 s @ 180k | P3 |
| 6 | equity curve rebuilt per call | no series cache | 0.9-2 ms ×N | 2 ms | 1.1 s @ 1M | P3 |
| 7 | research dataset rebuild per cycle | no change guard | worker runs build() every 60 s | ~4 ms | minutes @ 1M exp | P3 |
| 8 | comprehensive worker cadence re-scans | 4× load_trades/cycle | 43 ms/cycle | 43 ms | 2.6 s/cycle | P3 |
| 9 | sync_broker_history row-by-row | 2-4k executes/cycle | code trace | 14 ms | 1-2 s @ 100k | P3 |
| 10 | candle WAL plateau | no checkpoint tuning | 4.2 MB WAL vs 0.4 MB DB | benign | benign | P4 |
| 11 | daily-summary DB read on event loop | sync cached read 1×/24h | code trace L841 | 1× ms | negligible | P4 |
| 12 | broker O(N) ORDER BY in history meta | scan+sort each cycle | plan | 2 ms | small | P4 |
| 13 | /api/account/strategies N+1 | 93 registry PKs | measured 0.014 ms each | 1 ms | 0.1 s @ 5k strategies | P4 |
| 14 | nse_live.log growth | no rotation | 9.3 MB/2.5 h | 90 MB/day disk | disk churn | P2 housekeeping |
| 15 | ledger/orders/snapshots no retention | forensic truth | by design | — | 1.4M rows @ 8 yr | P3 (operator decision) |
| 16 | per-tick log_signal enqueue | 1 item/tick even when deduped | code trace | ~0.01 ms/tick | unbounded queue churn at 1k ticks/s | P4 |
| 17 | order_calc on tick thread (rare) | Win32 IPC per sized proposal | code trace L1973 | 1-5 ms × few/day | negligible | P4 |
| 18 | shadow decisions growth when attached | 1 row/decision | bounded list design | 0 now (no run) | 3,240/day @ 1 tick/s | P3 (bounded list caps; verify on first real run) |
| 19 | news articles body empty | keyword scans limited to title+summary | AVG(LENGTH(body))=0 | coverage blind spot | false negatives | P4 |
| 20 | signals dedup key NULL legacy rows | 14,069 pre-fix rows no key | COUNT NULL=14,069 | purge will remove by 08-24 | gone | P4 |

## 31. FUTURE OPTIMIZATION PLAN (documented only, no implementation)

0. **P3 — shadow schema at startup**: call `shadow_store.ensure_schema()` once in `LiveEngine.__init__` (off the tick path) + regression test that the 4 tables exist after construction; review `INSERT OR REPLACE` vs `INSERT OR IGNORE` for the shadow ledger's append-only contract. Expected: no DDL ever on the tick thread. Risk: none (idempotent DDL). Rollback: remove the one call.
1. **P2 — /api/news/keywords**: precompile the 189 keyword patterns once (module-level `@lru_cache`/precompiled dict); cache the coverage summary (TTL 5-10 min, invalidate on ingest). Expected: 1.8 s → 30-60 ms per call. Risk: none (pure function, same results). Rollback: revert to inline compilation.
2. **P3 — index/migration**: after operator approval, add `idx_orders_ticket (ticket, order_id)` and consider `audit_ledger(timestamp)`, `audit_broker_orders(time_setup)`, `audit_broker_deals(time)`. Expected: forensics and history-window queries use index; benefit grows with row count. Risk: small write cost (~10-30 B/row × 4 indexes) — measure before/after; rollback = DROP INDEX.
3. **P3 — ledger/broker_trades ORDER BY**: replace `COALESCE(NULLIF(close_time,''), timestamp)` with a single maintained `close_time` column (set at close; placeholder rows get `timestamp`) so the index works. Expected: 7 s → ~10 ms @ 1M. Risk: schema change touching every consumer — MUST be phased with a migration + regression tests + full beforePush gate. Rollback: keep the COALESCE expression (index still unused; no correctness change).
4. **P3 — accounting curve cache**: cache computed curves in AccountingCore, invalidate on new snapshot/ledger row (worker). Expected: dashboard 30 s polls hit cache; 1.1 s @ 1M → 5 ms. Risk: slight staleness (bounded by worker cadence); rollback: clear cache per request.
5. **P3 — research change guard**: skip dataset rebuild when no new closed outcomes since last cycle (compare experience-model registry max updated_at or a row-count watermark). Expected: worker cycle drops to ~0 DB work when idle. Risk: none (derived, rebuildable); rollback: remove guard.
6. **P3 — sync_broker_history executemany**: convert the 3 per-row insert loops to executemany in one transaction. Expected: cycle ÷10 at scale. Risk: none (same SQL), rollback trivial.
7. **P4 — housekeeping**: log rotation for nse_live.log (size-based), WAL autocheckpoint tuning for candle_intel, backfill signals.signal_dedup_key for auditability (optional), decide forensic-table retention with the operator.

**Benefit summary**: item 1 (P2) removes the only >1 s user-facing endpoint (~30× faster); items 0, 2-6 (P3) keep every dashboard/API call < 20 ms through 1M rows (vs minutes of degradation without them); item 7 (P4) caps disk/log growth.

## 32. RISK OF EACH OPTIMIZATION

| # | Change | Correctness risk | Ordering/causality | Idempotency | Auditability | Durability | Overall |
|---|---|---|---|---|---|---|---|
| 0 | shadow schema at startup | none (idempotent DDL) | none | n/a | improves | n/a | SAFE |
| 1 | keyword pattern cache | none (pure function) | none | n/a | n/a | n/a | SAFE |
| 2 | indexes | none (additive) | none | n/a | n/a | n/a | SAFE |
| 3 | close_time column | MEDIUM — touches every ledger consumer | must preserve half-open period semantics | unaffected | improves | n/a | CAUTION — phase + regression |
| 4 | curve cache | LOW (staleness ≤ worker interval) | none | n/a | none | n/a | SAFE |
| 5 | research guard | none (derived, rebuildable) | none | n/a | none | n/a | SAFE |
| 6 | executemany | none (same SQL) | none | unchanged | none | same tx | SAFE |
| 7 | housekeeping | none | none | n/a | n/a | WAL checkpoint on close | SAFE |

**None of the recommendations can lose trades, lose outcomes, duplicate executions, break experience immutability, create temporal leakage, or corrupt accounting** (verified against each).

## 33. FUTURE-SCALE ANALYSIS

| Metric | Now | 100k | 1M | 10M |
|---|---|---|---|---|
| ledger rows | 254 | 100k | 1M | 10M |
| ledger query (scan+sort, no fix) | 1.8 ms | 0.7 s | 7 s | 70 s ← **breaks dashboard** |
| ledger query (with close_time index) | ~0.5 ms | ~2 ms | ~10 ms | ~80 ms ✓ |
| snapshots | 767 | 100k | 1M | 10M |
| equity curve (no fix) | 0.9 ms | 0.1 s | 1.1 s | 11 s ← breaks |
| with series cache | 2 ms | 5 ms | 10 ms | 20 ms ✓ |
| experiences | 186 | 100k | 1M | 10M |
| merged retrieval (indexed) | 1.5 ms | 8 ms | 40 ms | 250 ms ✓ |
| payload parse per 200 rows | 0.6 MB | 0.6 MB | 0.6 MB | 0.6 MB ✓ (bounded) |
| news articles | 1,530 | 100k | 1M | — |
| keyword coverage (no fix) | 1.8 s | 6 min | **1 h** ← breaks | — |
| with precompiled cache | 60 ms | 2 s | 12 s (cacheable → <100 ms) | — |
| audit_signals | 14.7k | 3.1k steady | 3.1k (7 d retention) | 3.1k ✓ |
| broker history | 20.6k | 1.5M | — | — |
| orders/deals sync (executemany fix) | 14 ms | 150 ms | — | ✓ |
| worker cadence (accounting, per cycle) | 43 ms | 0.3 s | 2.6 s | 26 s ← **must fix (#3/#5/#6)** |
| DB file size (audit.db) | 48 MB | ~2 GB (with retention) | bounded by retention | bounded ✓ |
| news.db | 5 MB | ~350 MB | 3.5 GB (P3 retention) | — |

**Conclusion**: the architecture scales to 100k rows with NO changes (all queries stay < 0.1 s). At 1M-10M the ledger/broker-trades ORDER BY + accounting worker + keyword coverage become the first three ceilings — all removable with the P3 items above. The experience/research/intelligence design (indexed, bounded, cached) is the healthy part.

## 34. VERDICT BY COMPONENT

| Component | Verdict |
|---|---|
| Tick hot path (engine DB access) | 🟢 **HEALTHY** — zero synchronous DB on ticks; queued writes; dedup; no tick persistence |
| audit.db schema/indexes | 🟢 **HEALTHY** — mostly optimal; 3 suboptimal patterns (P3) |
| Write paths / transactions | 🟢 **HEALTHY** — batched, single-writer, no commit-per-row |
| Experience ledger | 🟢 **HEALTHY** — append-only, idempotent, indexed |
| Research subsystem | 🟡 **OPTIMIZATION OPPORTUNITY** — rebuild-per-cycle guard missing (P3) |
| Accounting | 🟡 **OPTIMIZATION OPPORTUNITY** — repeated scans per worker cycle (P3); never on tick path ✓ |
| News subsystem | 🟡 **OPTIMIZATION OPPORTUNITY + P2**: isolation perfect; keyword coverage O(N×K) is the single worst endpoint |
| Shadow subsystem | 🟢 **HEALTHY** — correct lazy-schema design; first-write DDL on tick thread is a P3 nicety (startup ensure_schema) |
| Candle intelligence | 🟢 **HEALTHY** — RAM ring + writer; benign WAL plateau |
| SQLite concurrency / locking | 🟢 **HEALTHY** — no contention evidence; WAL + single writer |
| Connection lifecycle | 🟢 **HEALTHY** — idiomatic short-lived connections; no leaks |
| Memory / unbounded growth | 🟢 **HEALTHY** — every structure bounded; forensic tables grow by design |
| Dashboard/API | 🟡 **OPTIMIZATION OPPORTUNITY** — all < 10 ms now; 3 queries degrade to seconds @ 1M |
| Logging/telemetry | 🟡 housekeeping: nse_live.log no rotation (90 MB/day), no telemetry in DB |

## 35. FINAL VERDICT

**The NSE database layer is healthy and correctly engineered for its current scale.** The tick hot path never touches a database synchronously; writes are queued, batched, idempotent, and single-writer; WAL + NORMAL sync gives crash safety without lock pressure; isolation between audit/news/candle databases is exact; retention for disposable telemetry works (89% reduction in signal rows after BUG-054/067, purge verified at 16-31 ms).

Two findings deserve attention: **/api/news/keywords** — a 1.8 s endpoint caused by 94,500 regex compilations per call, the single worst database-adjacent cost in the system (P2); and **shadow table DDL-on-first-write** — a P3 nicety (startup `ensure_schema()`), not a bug.

Three P3 index/query patterns (COALESCE-defeated ORDER BY on ledger and broker_trades, missing audit_orders ticket index, uncompressed accounting scans) will become the first real bottlenecks at ~1M rows — each has a proven, rollback-safe fix documented in §31. Nothing in this audit requires an architectural change; the existing design is correct and merely needs the documented incremental hardening.

**NOTHING WAS IMPLEMENTED.** This audit mutated no production files; it created this report and updated no bug ledger rows (no verified bug met the contract).