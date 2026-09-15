# Database Cleanup Report — NSE master wave (2026-09-15)

Machine input: `database_inventory_20260915.json` (this directory) — all sizes from
`dbstat` page sums against the live stores opened `mode=ro`, same method as lane-04
(which measured 376 MB total; today 404 MB — news keeps growing ~2.6 MB/day at the
pre-BUG-282 rate).

## Classification (per lane-04 + post-fix state)

| Store | MB | Class | Evidence / disposition |
|---|---:|---|---|
| news.db `news_articles` + `news_analysis` duplicate body/summary copies | ~126 + 68 | **REDUNDANT** (26,161 byte-identical rows measured; body==summary 28,431/28,446; analysis.summary==article.summary 25,011) | Dedup + payload-elimination cleanup RECOVERS ~120-180 MB. Mechanism: hygiene SAFE class `BYTE_IDENTICAL_DUPLICATE` (identity url+title+body-fingerprint, canonical = min(rowid), confidence 1.0) — **activation requires the ingest fix first (BUG-282 landed: tombstones can now fire); cleanup of historical rows is the remaining step, see procedure below** |
| news.db `news_analysis_runs` (26,160 rows) + analyzed/junk double-tombstones | ~15 | **REDUNDANT bookkeeping** | BUG-295 (in flight, PR pending) adds a working `news_analysis_runs` 30-day rule + heals the 4 dead ts_col rules that made news.db 100% undeletable (417 hygiene cycles, deleted=0 in every row) |
| audit.db research cluster (research_gates 24.6 + strategy_registry 22.6 + research_evidence 21.5 + research_events 12.7 + runs/snapshots) | **98.3** | **STALE (TIER-2, archive-first)** — one closed campaign, every max ts = 2026-08-27 | Policy says archive-before-delete; the ARCHIVE leg exists (`CleanupExecutor` budget 5k rows/cycle ≈ 20 cycles) but `archived=0` in all 417 cycles — activation is the documented follow-up; then VACUUM recovers 9 MB freelist + the archived pages (~35-40 MB steady state) |
| strategies.db factory cluster (factory_events 16.1 + factory_candidates 8.1) | 24 | **STALE**, same closed campaign, shadows of audit.db tables whose real home is here | Same archive-before-delete rule, flagged for the storage owner (lane-04 §7) |
| candle_intel.db (whole store, frozen since 2026-09-07T00:58) | 11.0 | **VALUABLE forensics / REGENERABLE runtime** — engine disabled by operator ruling, zero consumers; growth stopped | Do NOT truncate; one-shot ARCHIVE + quarantine-tag the 84 EUR-scale + 13 non-positive + 91 dialect rows BEFORE any future re-enable (DBF-002); the store-side guard (UNIQUE(symbol,timeframe,bar_ts) CANDLE-0003 + plausibility band in record_candle) is a tracked follow-up |
| ESSENTIAL (never shrink): audit broker truth (~4.5), ledger/experiences/outcomes/autopsies, governance, schema, news_sources/calendar, news_prune_audit | ~25 | KEEP | Invariants 1/2 hold; UNREAL-001 (3 ledger OPENED >14d) is lane-02 territory, not garbage |

## NOT executed on production — and why

A mass delete during this wave is destructive and the repo convention is
backup + verified-retention + loud audit first: (1) the retention engine that
would perform it safely only became non-dead with BUG-295 (in review);
(2) the news dedup writer fix (BUG-282) landed hours ago — historical-row
cleanup should run under the SAME identity semantics once, with the prune
journal; (3) the research-cluster archive leg needs a maintenance-window
VACUUM; (4) an engine may be live against these DBs (WAL was moving during
lane-04). Executing it silently would violate the wave's own
"backup before destructive cleanup / verify retained data supports
testing/replay/audit" gate.

## Safe cleanup procedure (ordered, gated)

1. Backup: `nexus db backup` (or `sqlite3 .backup`) of news.db + audit.db + strategies.db; verify digests.
2. After BUG-295 merges: run `nexus hygiene --mode SAFE_CLEAN --apply --database news` (bounded batches, archive-before-delete, verification after each batch — existing executor). Expected first-cycle effect on news: tombstone/analysis_runs/rules now fire.
3. News byte-identical dedup: add SAFE class `BYTE_IDENTICAL_DUPLICATE` per lane-04 §7 mapping (one canonical per url+title+fingerprint; archive the rest), then a second SAFE_CLEAN cycle.
4. Research-cluster ARCHIVE activation (`archive_after_days=30` on the four TIER-2 rules + CleanupExecutor archive path), 20 cycles, then VACUUM in a maintenance window.
5. candle_intel one-shot archive + quarantine tags (DBF-002 first, then freeze).
6. Re-measure against `database_inventory_20260915.json` as the baseline; append post-cleanup JSON beside it.

Projected steady state from measured inputs (no guesses): news ~55-70 MB (canonical stories only),
audit ~35-40 MB, candle_intel ~0.06 MB (archived), strategies ~4 MB → **total ~100-120 MB
from 404 MB, with ~0 MB of authoritative data deleted**.

## Prevention (landed this wave)

- **BUG-282** stops the dup engine at the source (RFC-822 parse + poll-stable timeless identity → tombstones finally fire).
- **BUG-295** makes misconfigured retention rules LOUD instead of silently inert (blocked-entry + warning on missing ts_col) — the 417-cycles-zero-deletions failure mode cannot recur invisibly.
