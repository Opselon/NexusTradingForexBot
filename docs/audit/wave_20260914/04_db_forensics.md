# Lane 04 — Candle/Market DB Forensics (wave 2026-09-14)

Repo: `NexusTradingForexBot` @ `nse/master-active-scalper-wave` (base origin/main `9431edd2`).
Method: **all DB access `mode=ro` (URI); zero writes to `artifacts/`** — only file touched in the repo tree is this report. `dbstat` compiled in (SQLite 3.53.1 in `.venv`), so all byte attributions are real per-page sums, not estimates. **VERIFIED** = executed command against the live artifacts; **STATIC** = source-read.

Snapshots taken 2026-09-14 (live engine files; `audit.db` WAL was 453 KB and moving — all WAL-mode reads still consistent).

---

## 1. File-level truth (VERIFIED)

| DB | File | Pages×size | Freelist | WAL | Integrity |
|---|---:|---|---:|---:|---|
| `artifacts/candle_intel.db` | 10,969,088 B (10.97 MB) | 2,678×4096 | 0 | 0 B | `ok` |
| `artifacts/news.db` | 233,205,760 B (233.2 MB) | 56,935×4096 | 3 p (12 KB) | 0 B | checked at open |
| `artifacts/audit.db` | 132,112,384 B (132.1 MB) | 32,254×4096 | 2,207 p (**9.04 MB**) | 453 KB | — |
| (`artifacts/strategies.db`, context) | 27.8 MB | 6,800×4096 | — | — | — |

## 2. The "25 MB candle DB" — what actually grew, and what was pruned (VERIFIED)

**The 25 MB figure is unsupported anywhere in this repo.** `git grep -iE "25 ?MB"` over HEAD, filesystem grep over `docs/ agents/ src/ scripts/ configs/`, and `git log --all -S"25 MB"` → **zero matches**. Every measured historical figure for this file:

| Date (daily max) | candle_intel.db main file | Source |
|---|---:|---|
| 2026-08-18 (TASK-11 inventory) | 1.0 MB + **4.2 MB WAL** | `docs/agent_handoffs/TASK-11-database-hygiene.md` |
| 2026-08-19 / 08-20 | 1.21 / 2.02 MB | `artifacts/forensics/history.jsonl` size_bytes series (276 points) |
| 2026-08-31 / 09-01 / 09-02 | 4.85 / 6.18 / 8.69 MB | same |
| 2026-09-03 / 09-04 | 10.86 / **10.94 MB (max ever recorded)** | same — CHECK-GRW-01 fired "10.9 MB > 3× baseline 1.13 MB" on 09-04 |
| 2026-09-07 → today | ~10.97 MB — **flat since 09-04, last candle write 2026-09-07T00:58** | candles table max(ts) |

So the realistic peak was ~11 MB main + ~4 MB WAL peak (+32 KB shm) ≈ **15 MB**, never 25 MB. The claim most plausibly conflates: **33.5k rows** (the 2026-09-07 connect-or-disable handoff; VERIFIED: live row totals sum to exactly 33,549 = 3,988+3,988+10,758+4,510+4,505+4,505+1,295) or WAL×10 oddity from the 2026-08-18 performance audit (4.2 MB WAL vs 0.4 MB db).

**What grew:** the candle-intelligence runtime writing 5–6 derived rows per completed M1 bar (XAUUSD, plus 2 EURUSD rows) from 2026-08-17 until the Phase-3 disable (commit `ced18ba1`, default `enabled=false`, decision doc 2026-09-07 — engine had **zero consumers**; 4,505 computed trade_decisions: ENTRY 2,952 / NO_TRADE 1,295 / HOLD 180 / FAST_EXIT 78, none applied). Growth stopped with the disable; nothing new since 09-07.

**What was pruned historically: nothing was ever deleted.** Evidence:
- `sqlite_sequence` vs row counts: candles/closures seq=4,510 vs 3,988 rows → 522 consumed ids. But **id-gap forensics** shows 506 gaps inside the span with `candles` and `candle_closures` missing the **identical** id set, and no `DELETE FROM candle*` exists anywhere in `src/`/`scripts/` (git grep VERIFIED). The store writes via **`INSERT OR IGNORE`** onto tables with a **global `UNIQUE(bar_ts)`** (no symbol/timeframe in the key) and `id INTEGER PRIMARY KEY AUTOINCREMENT` — ignored inserts burn sequence values (reproduced in `:memory:` this lane: 5 conflicting inserts → 1 row, seq=5). The gaps cluster at restart-backfill times (09-01 06:xx, 09-02 00:39–10:29 alternating ids) = **replayed bars silently rejected**, i.e. 506 bar-writes lost as collisions, 16 tail ids burned post-09-07.
- The hygiene worker: 417 recorded cycles (`archive/_hygiene_state/hygiene_state.db`), **all AUDIT_ONLY/skipped, deleted=0, archived=0, bytes_freed=0 in every row** (the one SAFE_CLEAN opt-in on 08-31 had 0 candidates — nothing was yet >30 d). 0 rows in `candles` are older than 30 d today (max age 27.2 d) → the 30 d derived-rows purge (hygiene class 4) has never had eligible rows and is **moot while the store is disabled** — this DB will never self-shrink.

Verdict: candle_intel.db is a **frozen 11 MB forensic artifact**, not a growing liability. The "25 MB" alarm is noise; the real defect is the store's `UNIQUE(bar_ts)` global key (see §6 time-integrity + §7 classification).

## 3. news.db — 233 MB anatomy (VERIFIED, dbstat sums)

Per-object bytes (table / index split: **tables 213.2 MB + indexes 20.0 MB**):

| Object | Bytes | % file | Rows | Notes |
|---|---:|---:|---:|---|
| `news_articles` | 117.4 MB | 50.3% | 28,446 | + its 4 idx + 2 autoidx → **126.1 MB** |
| `news_analysis` | 68.0 MB | 29.2% | 25,070 | +idx → 70.3 MB |
| `news_ai_analysis` | 8.2 MB | 3.5% | 3,654 | |
| `news_analyzed_hashes` + `news_junk_hashes` (+4 idx) | 11.7 MB | 5.0% | 22,280 + 8,535 | tombstone caches |
| `news_impacts` (+idx) | 5.2 MB | 2.2% | 22,795 | |
| `news_analysis_runs` (+idx) | 4.0 MB | 1.7% | 26,160 | 1,189 runs/day |
| `news_entities` (+idx) | 3.0 MB | 1.3% | 58,599 | |
| `news_prune_audit` (+idx) | 2.3 MB | 1.0% | 8,745 | governance journal |
| `news_topics` | 1.7 MB | 0.7% | 44,689 | |
| `calendar_events`/`news_sources`/`news_health`/states | <0.1 MB | | 190/13/12/1+1 | |
| 6 zero-row tables (`news_consensus`, `news_trade_links`, `news_event_links`, `news_post_event`, `news_article_versions`) | ~0.03 MB | | 0 | lazy-created |

Page fill is healthy (news_articles 93.6%, freelist ≈ 0) — **this is real stored data, not fragmentation**. The bytes are a duplication problem, not a vacuum problem.

### 3.1 Three root causes of the 233 MB (all VERIFIED)

**RC1 — same article text stored three times (~97 MB):**
- `news_articles.body` avg 1,706 B + `summary` avg 1,706 B; **body is byte-equal to summary in 28,431/28,446 rows** (3,642 pairs empty) → the second copy alone costs **48.5 MB**.
- `news_analysis.summary` is **byte-equal to `news_articles.summary` in all 25,011 joined rows → a third copy, 45.9 MB**.
- Only ~2,120 distinct stories exist in the entire DB.

**RC2 — per-poll identity ⇒ mass re-ingest (~80 MB):**
- 28,446 article rows vs **2,120 distinct `canonical_url`**. Per source (rows/urls): Bank of England **11,086/53 (209×)**, ECB 3,424/28, FOMC 3,082/25, ForexLive 3,811/574, ZeroHedge 3,552/632, MarketWatch 2,176/295, CNBC 953/227.
- **1,592 groups are byte-identical on (url,title,summary,body) — 26,161 redundant rows, 80.4 MB of payload** recoverable keeping one per group (plus ~9 MB of their index share).
- Mechanism (STATIC, `src/nexus_scalp/news/ingest/deduplicator.py:119`): `compute_article_hash` mixes **`published_at` bucketed to 60 s** — and `published_at` is **fetch-time-stamped** for these feeds (VERIFIED: 28,237/28,446 rows have |published_at − created_at| < 60 s; the dup groups show one url with 230 rows, 230 distinct hashes, 230 distinct published_at). Every ~40-min RSS poll re-mints a fresh hash ⇒ the `article_hash UNIQUE` guard, `is_analyzed_hash`, and `is_junk_hash` tombstones **can never fire** for these sources. The title-hash dedup path exists but flagged only **3** rows (`is_duplicate=1`) — the tombstone tables still grow anyway (analyzed_hashes ∩ junk_hashes overlap = **7,861 double-tombstoned hashes**).

**RC3 — retention/prune is status-relabelling + dead rules; nothing deletes:**
- The **news prune implementation lives in `src/nexus_scalp/news/ai_service.py:695 auto_prune_irrelevant`** (TIER-4 "recoverable status" design): marks `ACTIVE→IRRELEVANT` + junk-hash tombstone; it **does run** — `news_prune_audit` holds 8,745 `AUTO_PRUNE` events (8,511 actor `pro_auto`, 234 `llm_purge`) from 08-22 → 09-13 (last one 3 s after a worker cycle). Called from `pro_auto.py:955` per cycle + `POST /api/news/auto-prune`. **It preserves bytes by design** — 8,081 IRRELEVANT articles still hold 11.5 MB.
- The only true article delete is `pro_auto.py:992 purge_irrelevant(hard_delete=True)` — **console-only** (`POST /api/news/pro/purge`, default soft=counts). Sequence-gap forensics: it has fired only a handful of times ever (news_entities 2,353 / news_topics 2,513 / news_impacts 1,176 child rows deleted across 19 days, leaving 59/62/9/62 dangling orphan rows pointing at removed articles). It is **not wired to any scheduler**.
- **Hygiene worker on news.db = fully inoperative:**
  - `NEWS_RETENTION` (`hygiene/retention.py:436`) marks *every* evidence table `never_delete=True`; only `news_health` (90 d) and `news_worker_state` (30 d) are deletable — and **both rules name a `ts_col="created_at"` that does not exist** in either table (PRAGMA VERIFIED: `news_health` has no created_at; `news_worker_state` has `last_cycle_at`), so `HygienePlanner.build_plan` silently `continue`s (`worker.py:191-193`). **0 % of news.db is hygiene-deletable even in SAFE_CLEAN — apply.**
  - The durable run log (`hygiene_run_history`) contains **0 news runs** (414 candle_intel, 1 audit; `SELECT DISTINCT database` VERIFIED) — news plans exist only inside the one-shot `initial_audit.json`.
  - The approved cleanup class #9 ("`is_duplicate=1` whose canonical is proven") is structurally unexploitable because of RC2: the duplicates aren't flagged (3 vs 26,161).
- Cross-check with hygiene's own initial report (`archive/_hygiene_state/initial_audit.json`, 09-13): news plan = 22 tables, **duplicates_found 3, retention_candidates 0, delete_candidates 0, rows_scanned 0** — while this lane measured **26,161 byte-identical duplicates, 80 MB**. The detector's canonical-identity-only policy is correct for money tables but blind to the one duplication mode news actually suffers (hash churn).

### 3.2 Data-quality checks (news)
No NULL/empty title/url/hash, no malformed hashes (all 64-hex), no future `published_at`, no >1 y stale stamps, no hash-duplicate rows (article_hash IS unique — that's the guard that's *too* unique), zero intra-group entities/topics duplication. `prompt_version` is still empty on **3,654/3,654** `news_ai_analysis` rows despite the market-context P0-2A provenance stamp (STATIC claim vs VERIFIED data: the fix post-dates the rows but no post-fix rows were written either — `analyzed_at` max 09-13 with version '' → the stamping leg never ran against this table's writes; flag for lane 06).

## 4. audit.db — 132 MB anatomy (VERIFIED, dbstat)

Tables 109.1 MB, indexes 14.0 MB, **freelist 9.04 MB (2,207 pages — real delete history: BUG-054 purge ran: `audit_signals` seq 1,321,386 vs 1,469 rows live ⇒ ~1.32 M signals purged; position_lifecycle 16,435; strategy_registry 12,359)**. WAL 453 KB.

| Object (bytes incl. own indexes) | MB | % | Rows | Dominance driver |
|---|---:|---:|---:|---|
| `research_gates` | 25.3 | 19% | 25,986 | `result` JSON 16.1 MB (avg 621 B) |
| `strategy_registry` | 22.9 | 17% | 4,082 | backtest/walkforward/oos/robustness/context cols = **17.2 MB on 4,007 REJECTED rows** |
| `research_evidence` | 22.0 | 17% | 18,228 | `content` 14.3 MB, content_hash-unique |
| `research_events` | 20.2 | 15% | 82,289 | table 12.7 MB + **`idx_events_strategy` 5.4 MB + autoindex 2.1 MB** (index = 42 % of cluster) |
| `audit_experiences`(+outcomes) | 8.2 | 6% | 1,373/1,118 | payload 4.1 MB |
| `position_lifecycle_events` | 4.0 | 3% | 2,397 | |
| `research_runs`(+snapshots) | 5.2 | 4% | 4,330+4,330 | |
| rest (broker truth, orders, ledger, signals, governance, 25 empty tables…) | ~14 | 11% | — | broker tables are *tiny* (~4.5 MB) |

**Headline: the "research campaign" cluster (gates+evidence+events+runs+snapshots+registry) = 98.3 MB = 74 % of audit.db**, and it is **frozen** — every table's max timestamp is **2026-08-27T19:12** (one 4,330-run / 4,080-strategy factory campaign, 3 promoted / 72 discovered / **4,007 REJECTED**; `strategy_registry.updated_at` still ticks for 2 keeper rows). It is neither garbage nor hot: it is the archive-first TIER-2 class the policy says "never delete by default" — and no archive job ever ran (`archived=0` in all 417 hygiene cycles, `artifacts/archive/` empty, no `_journal`).
Secondary duplication: `strategy_registry.backtest` is byte-equal to the corresponding BACKTEST `research_gates.result` for 1,128 strategies (1.6 MB measured exact-match on that column pair; the full eval-quad ≈ 12.5 MB semantically re-stored from `research_evidence`). IDs/hashes are clean: zero duplicate `event_id`/`gate_id`/`evidence_id`/`content_hash`; zero future timestamps; 4,331 gates have **empty `started_at`** (instant/static gates — benign but a schema smell). Consistency: hygiene's UNREAL-001 finding (3 ledger `OPENED` > 14 d) reproduced VERIFIED (3 rows).
Retention rules that work: `audit_signals` 7 d (1 row now eligible). **Dead rules (VERIFIED via PRAGMA):** `research_worker_state`/`intelligence_worker_state` `ts_col="updated_at"` — column is actually `last_cycle_at` ⇒ silently skipped; `position_lifecycle_events` purge is scoped to `event_type='POSITION_MOVING'` — **zero such rows exist ever** (vocabulary is CREATED/OPENED/DEGRADING/MFE_REACHED/PROFIT_GIVEBACK/EXPECTATION_CONFIRMED/RECOVERY_ATTEMPT/EXITED) ⇒ dead filter. So audit.db has been shrinking only via BUG-054's own purge path inside `audit_repository.py:3516`, not via the hygiene worker, and its largest 98 MB is untouched by policy design — pending an ARCHIVE activation that never arrived.
Related: `strategies.db` (27.8 MB) holds the factory engine's own `factory_events` 16.1 MB + `factory_candidates` 8.1 MB from the same closed campaign (its `factory_candidates`/`events`/… **shadow** same-named tables in audit.db, which hold only 0.45 MB there) — one data class living in two DBs.

## 5. Candle row validation (VERIFIED, full-table scans)

| Check | Result |
|---|---|
| Duplicate `(symbol,bar_ts)` — candles/closures | **0** (both 3,988 rows, 1:1 join on id, 0 bar_ts mismatches) |
| Invalid OHLC `high<low / high<open / high<close / low>open / low>close` | **0** structurally invalid |
| **Non-positive prices** | **13 rows** candles + same 13 in candle_closures — ids 2491…2800, **all XAUUSD 2026-09-02 00:42–03:54**, e.g. (open −5.67, high 1.38, low −5.67, close 1.38) |
| **Price-scale corruption (worse than the 13)** | **84 rows** labeled XAUUSD carry **EUR-scale prices** (avg close **1.30** vs gold-scale avg 4,025; broker truth in audit.db: XAUUSD deals 3,973–4,684). Contaminated window 08-21 08:53 → 09-02 03:54 (peak 09-02 00:00–04:00 = 13 of 240 bars). Downstream poison: 84 closures, **280 pattern rows, 130 trade_decisions, ~893 regime joins** ride on mis-scaled bars. `record_candle` only rejects non-finite — no plausibility band exists (STATIC). |
| Sub-second `bar_ts` (un-aggregated ticks leaked as bars) | **16 rows** (candles=closures, same ids) |
| Two bars within one minute | 8 minutes ×2 rows (aligned + sub-second variants) |
| Future timestamps (`bar_ts`/`ts` > now) | **0** |
| Event-vs-processing dialect | 3,850/3,988 aligned ±6 min; **91 rows with bar_ts exactly +179 min ahead of ts** — all in the 08-17 22:xx–08-18 02:xx seed block = **GMT+3-vs-UTC dialect** (documented third time-dialect in `observability-map.md`) |
| NULL/empty bar_ts, volume ≤ 0 | 0 / 0; `is_complete=1` on all 3,988 |
| Coverage (XAUUSD M1, weekday minutes 08-17 22:36 → 09-07 00:58) | 3,978 distinct minutes of 20,303 expected = **19.6 %**; 211 gap runs / 16,328 missing minutes (raw minute-grid incl. weekends: 24,965); 7 gaps ≥ 10 h (largest 30 h 08-21 20:00→08-24 22:06) — the DB is a **sparse live-session tape, not a continuous candle archive**; gaps = engine downtime, consistent with restart-cluster id-gaps in §2 |

## 6. Classification (per table / data class)

Scale: ESSENTIAL = money/decision truth, must never shrink below full | VALUABLE = wanted, bounded | REGENERABLE = rebuildable from TIER-0 truth | REDUNDANT = pure waste | CORRUPTED = fails validity | STALE = closed-source dead weight.

### candle_intel.db (frozen 10.97 MB)
| Class | Tables / rows | Verdict |
|---|---|---|
| REGENERABLE→**REDUNDANT (runtime)** | candles, candle_closures, candle_patterns, market_regimes, risk_evaluations, trade_decisions, rule_vetoes (all 29 k rows) | Rebuildable from broker history + audit.db (policy class 4); engine disabled with **zero consumers** → keep only as **VALUABLE forensics** until the 09-07 close-out is superseded; then ARCHIVE |
| **CORRUPTED** | 13 candles + 13 closures (≤0 prices); **84+84 XAUUSD/EUR-scale bars** + 280 patterns + 130 decisions derived from them; 16 sub-second-bar_ts rows; 91 +179 min seed-dialect rows | Quarantine-tag, never feed to any backfill/rebuild |
| REDUNDANT | 5 empty tables (audit_log, feature_vectors, trade_proposals, open_positions, exit_signals) | schema-only, 20 KB; harmless; leave (migration-owned) |
| ESSENTIAL | schema_meta / schema_migrations (CANDLE-0002) | invariant #3 |

### news.db (233.2 MB)
| Class | Object / rows | MB |
|---|---|---:|
| VALUABLE | the **2,120 canonical stories** (`news_articles` one-per-url) + their `news_analysis` (≤1 per article) + `news_entities` 58.6 k + `news_topics` 44.7 k + `news_impacts` 22.8 k + `news_ai_analysis` 3.65 k + `news_sources`/`calendar_events`/`news_worker_state` | ~55–70 |
| ESSENTIAL (governance) | `news_prune_audit` (8,745 — the only proof auto-prune ran) | 2.3 |
| **REDUNDANT** | **26,161 byte-identical re-ingest article rows** (RC2) | ~80 + ~9 idx share |
| **REDUNDANT** | duplicate **body** column copies (RC1a) + duplicate **news_analysis.summary** copies (RC1b) + 883 extra per-article re-analysis rows | 48.5 + 45.9 (overlapping w/ dedup: first-copy-of-dups portion) + 1.2 |
| REDUNDANT | `news_analyzed_hashes` ∩ `news_junk_hashes` double tombstones; hash-churn tombstones generally (they suppress nothing since keys never repeat) | 11.7 |
| STALE | `news_analysis_runs` 26,160 manifests (1,189/day, 26,153 COMPLETE — bookkeeping, regenerable from audit trail); 8,081 IRRELEVANT payloads past 90 d (DELETE-tier by policy intent) | 4.0 + 11.5 |
| CORRUPTED (minor) | 59 `news_analysis` + 62 `news_ai_analysis` + 9 entities + 62 topics orphan rows (dangling after console hard-purge); 3 `is_duplicate=1`→canonical rows (fine) | ~0.15 |

### audit.db (132.1 MB)
| Class | Object | MB |
|---|---:|---:|
| ESSENTIAL | broker truth (`audit_broker_*` 22.9 k rows), `audit_ledger`/experiences/outcomes/autopsies/executions_reconciled, account snapshots, `model_governance_events`, `schema_*`, `model_runtime_health`, recent `audit_signals`/guard telemetry | ~14 |
| **STALE (TIER-2, archive-first)** | research cluster `research_gates/evidence/events/runs/run_snapshots` + `strategy_registry` (4,007 REJECTED) — **one closed 08-21→08-27 campaign** | **98.3** |
| REDUNDANT | `strategy_registry` eval-quad re-stores gate/evidence JSON (12.5 MB semantic dup); `idx_events_strategy` 5.4 MB (4,080-key index over 82 k tiny events); 25 zero-row tables; audit.db's near-empty shadow tables of factory data whose real home is `strategies.db` (24 MB factory cluster there, same campaign) | ~18 + 9.0 freelist |
| CORRUPTED (findings, not garbage) | UNREAL-001: 3 ledger OPENED >14 d (lane-02 territory); 4,331 gates with empty `started_at` | — |

## 7. Tiered retention proposal → hygiene worker mapping

Tiers: **HOT** (write-bounded, always resident) → **WARM** (age-bounded, resident) → **COLD** (payload-trimmed, meta resident) → **ARCHIVE** (sha256 JSONL → `artifacts/archive/…`, row removed) → **DELETE** (proven-safe purge). Mapping into `src/nexus_scalp/hygiene/` (retention.py registry + worker.py SAFE_RETENTION_DELETES/Safe classes; archive.py + journal carry the ARCHIVE leg that currently has **zero** activations).

| Data class | Tier / window | Worker hook |
|---|---|---|
| audit TIER-0/1 (broker, ledger, experiences, governance) | **HOT→WARM permanent** — never shrink | (untouched — invariants 1/2 hold) |
| audit recent telemetry: `audit_signals` 7 d, guard 13 d, worker-state | **DELETE** (already coded) + **fix dead rules**: `research_worker_state`/`intelligence_worker_state` ts_col→`last_cycle_at`; drop or re-scope `POSITION_MOVING` filter to the real event vocabulary | `worker.py` SAFE_RETENTION_DELETES + `retention.py` (evidence: 1.32 M signals already purged by BUG-054 — windows proven) |
| audit research cluster (gates/evidence/events/runs/snapshots + REJECTED registry rows) | **ARCHIVE** campaign-closed: 30 d after a `research_runs` row's run reaches terminal state **and** the campaign made no writes for 30 d (first target: the entire 08-21→27 cluster, 98 MB → JSONL + journal, rows deleted; rehydratable by migration `nexus db archive restore`) — keeps INV "never delete final copy" by archive-before-delete | `retention.py` add `archive_after_days=30` to the four TIER-2 rules + activate `CleanupExecutor` archive path (budget max_rows_archived 5 k/cycle ⇒ ~20 cycles; then VACUUM audit.db in maintenance window to recover 9 MB freelist + 89 MB reclaimable pages) |
| `strategy_registry` eval-quad + `idx_events_strategy` | **REDUNDANT-schema**: gate result is source of truth; registry keeps `evidence_id` refs only; replace the 5.4 MB index with the autoindex or a covering `(strategy_id, occurred_at)` — TASK-10 migration, worker never drops schema (§13) | registry/matrix doc update |
| news canonical stories (≤90 d, ACTIVE) | **HOT** | `news_articles` cleanup_class extension: new SAFE class `BYTE_IDENTICAL_DUPLICATE` (identity = url+title+body-fingerprint, canonical = min(rowid) proven present — satisfies §5 confidence-1.0 rule) → archive-then-delete 26,161 rows / ~89 MB |
| news IRRELEVANT >90 d | **COLD→ARCHIVE→DELETE**: strip body/summary to '' (keep title/url/hash/importance meta), then at 180 d route to existing console `purge_irrelevant(hard_delete=True)` — now **scheduled** via a WARM rule `delete_after_days=180, cleanup_class=REBUILDABLE_DERIVED=false, actor=hygiene` (the audit journal rows already prove provenance) | `ai_service.auto_prune_irrelevant` stays; add `db.strip_article_payload()` used by worker |
| news re-analysis summaries | **REDUNDANT**: writer stops copying `summary` into `news_analysis` (points at `article_id`); 883 extra per-article analysis rows → keep latest, archive older | fetcher/analysis writer + one-time SAFE class `STALE_DERIVATIVE_VERSION` |
| `news_analysis_runs`, analyzed/junk hash tombstones | **WARM 30 d → DELETE** (pure bookkeeping; 1,189/day growth) — **fix the two dead news ts_col rules first** (`created_at`→`last_failure_at`/`last_cycle_at` or add a real `created_at` via migration) | worker.py news block + retention.py |
| candle_intel whole DB | **ARCHIVE (freeze)**: engine disabled; policy 30 d derived purge is inert (0 candidates). One-shot: archive all 5 row-tables to `artifacts/archive/candle_intel/…`, **quarantine-tag the 84 mis-scaled + 13 non-positive + 91 dialect rows first** so a future Option-A reconnect never relearns from poison; leave empty schema in place | worker one-shot `--database candle_intel --mode SAFE_CLEAN --apply` after adding `REGENERABLE_FROZEN` class; schema untouched |
| candle store defect guard (why not to trust a rebuild) | Fix `UNIQUE(bar_ts)` → `UNIQUE(symbol,timeframe,bar_ts)` via CANDLE-0003 migration; plausibility band in `record_candle` (reject price outside [0.5×,2×] symbol median) — prevents the 09-02 EUR-scale-on-XAU class silently | migration engine, not worker |
| strategies.db factory cluster | same **ARCHIVE** campaign-closed rule (24 MB, same 08-27 cutoff) — out of lane scope, flagged for the storage owner | `storage/policy.py` allowlist already protects it |

### Estimated reclaim (measured inputs, not guesses)
- news.db: 233 → **~95–110 MB** (dedup only), → **~55–70 MB** (+ body/summary/analysis-copy elimination), then flat at ~2–3 MB/mo growth once identity RC2 is fixed (ingest-side hash without poll-time bucket ⇒ tombstones finally work; that is the *real* fix — cleanup without it re-fills).
- audit.db: 132 → **~35–40 MB** (archive research cluster + vacuum).
- candle_intel.db: 11 → ~0.06 MB (archived) or simply **declare frozen** (zero runtime cost; engine off).
- Combined **~283 MB of the 376 MB footprint is REDUNDANT/STALE, ~0 MB requires deleting authoritative data.**

## 8. Bug ledger from this lane (candidates for agents/bugs.md, by coordinator)

1. **DBF-001 (P1, silent data loss):** candle store `UNIQUE(bar_ts)` omits symbol/timeframe → cross-symbol & replay collisions; 506 bar-writes burned as ignored ids in 21 days (VERIFIED id-gap + :memory: repro of INSERT-OR-IGNORE id burn).
2. **DBF-002 (P1, poison data):** 84 XAUUSD candles/closures with EUR-scale prices (avg close 1.30; 13 even ≤0), all 2026-09-02 00:42–03:54 (+08-21 EURUSD test rows) — no plausibility band in `record_candle`; 410 derived pattern/decision rows contaminated.
3. **DBF-003 (P1, 80 MB bloat):** news ingest identity mixes fetch-time `published_at` → per-poll re-ingest (26,161 byte-identical rows; BoE 209× per story); tombstone/dedup guards structurally unable to fire.
4. **DBF-004 (P1, policy inert):** 4 hygiene retention rules reference non-existent columns (`news_health.created_at`, `news_worker_state.created_at`, `research_/intelligence_worker_state.updated_at`) + 1 references a never-emitted `event_type` (`POSITION_MOVING`) → **all silently skipped**; 0 news runs in hygiene history; news.db 100 % undeletable via SAFE_CLEAN.
5. **DBF-005 (P2):** triple text storage: body==summary (28,431/28,446), analysis.summary==article.summary (25,011) — ~94 MB duplicate payload.
6. **DBF-006 (P2):** hard-purge console path leaves 59+62+9+62 orphan child rows and is un-scheduled/unordered with the tombstone tables (hashes survive, rows don't).
7. **DBF-007 (P3, measurement):** "25 MB candle DB" claim unsupported by any artifact/doc/git history (max measured 10.94 MB + 4.2 MB WAL); seed-block 91 rows carry +179 min GMT+3/UTC dialect; `news_ai_analysis.prompt_version` 100 % empty despite P0-2A claim (→ lane 06).

## 9. Evidence commands (reproducible, all ro)
`.venv/Scripts/python.exe -c "sqlite3.connect('file:artifacts/news.db?mode=ro',uri=True)…"` — sizes: `SELECT name,sum(pgsize) FROM dbstat GROUP BY name`; dup math: `GROUP BY canonical_url HAVING count(*)>1` (1,527 groups / 27,853 rows; byte-identical 1,592 groups / 26,161 rows / 80.4 MB); triple-copy: `WHERE body=summary` (28,431) / `na.summary=a.summary` (25,011); tombstone churn: 28,237/28,446 rows `|published−created|<60s`; hygiene inert: PRAGMA column checks vs `SAFE_RETENTION_DELETES` dicts + `hygiene_run_history` (417 cycles, `bytes_freed=0` everywhere, `DISTINCT database` = candle_intel/audit); candle validation: full §5 scan; id-gap/AUTOINCREMENT mechanism: `sqlite_sequence` vs counts + `:memory:` INSERT-OR-IGNORE reproduction; growth tape: `artifacts/forensics/history.jsonl` size_bytes series 08-19→09-14.
