# Post-Remediation Global Rescan — the new baseline

Scope: rescan of both databases after the remediation wave (#566 payload value
gate + archival lifecycle + provider-split read plane, #575 shadow mirror
reclaim, #576 R-1/R-9, #577/#578/#580 post-merge evidence + dead-index sweep).
This is the post-merge baseline and the new "BEFORE" for the next wave.

## PostgreSQL

### Top tables

| Table | Total | Heap | Rows | seq_scan | idx_scan |
|---|---|---|---|---|---|
| `shadow_decisions` | 277 MB | 265 MB | 55,182 | 131 | 112,625 |
| `news_articles` | 82 MB | 19 MB | 26,145 | 202 | 143,778 |
| `research_gates` | 51 MB | 24 MB | 27,984 | 123 | 60,053 |
| `news_analysis` | 45 MB | 16 MB | 20,375 | 438 | 496,667 |
| `research_evidence` | 44 MB | 19 MB | 19,228 | 21 | 37,924 |
| `model_governance_events` | 35 MB | 27 MB | 59,180 | 22 | 118,422 |

### The counters are historical, not live

The headline ``audit_signals`` numbers — 3.22 M seq scans, 30.5 **billion**
tuples read on 7,453 rows — are cumulative since the last stats reset and no
longer reflect the live workload. A 60-second re-probe of every high-scan
table showed **zero new sequential scans and zero new tuples read**:

| Table | scans/min | tuples/min |
|---|---|---|
| `audit_signals` | 0.0 | 0 |
| `audit_experience_outcomes` | 0.0 | 0 |
| `news_analysis` | 0.0 | 0 |
| `news_articles` | 0.0 | 0 |
| `audit_experiences` | 0.0 | 0 |
| `news_junk_hashes` | 0.0 | 0 |

This is the R-1 memoization (#576) holding: the 5 Hz health probe no longer
re-scans the table. The cumulative counter should not be read as an active
cost.

### `shadow_decisions`: 7.6× heap bloat, understood and intentionally retained

`shadow_decisions` is now the largest table (265 MB heap for 34.9 MB of real
data, 55,182 rows averaging 661 bytes — a 7.6× bloat factor).

This is a **direct artifact of this wave's own win**, not a new pathology: the
payload reclaim (#575) shrank every row from ~2.7 KB to 661 B *in place*.
PostgreSQL never compacts a heap on its own — the shrunken rows stay in their
original pages, leaving ~230 MB of reusable but unreleased space. The TOAST
relation is fully reclaimed (0 chunk rows, 8 KB).

The repo forbids ``VACUUM FULL`` on active tables by explicit convention
(``database/lifecycle.py:391``: "Standard safe VACUUM and ANALYZE on active
tables (never VACUUM FULL)"), and `shadow_decisions` is `never_delete=True`
promotion evidence. So the correct action is to let ordinary VACUUM + new
inserts reuse the space. Measured, understood, intentionally retained — the
heap is a reservoir, not waste.

### Index write-cost sweep

Removed in this wave (cumulative on this ledger):

| Index | Size | Scans | Why |
|---|---|---|---|
| `idx_gov_events_model` | 5.5 MB | 0 | query exists, no caller ever passes `model_id` (#578) |
| `idx_factory_cand_hash` | 552 kB | 0 | indexed column is never a lookup key anywhere (#580) |

Audited and **kept** — each has a real consumer despite low scan counts:

| Index | Size | Scans | Consumer |
|---|---|---|---|
| `idx_news_analyzed_hashes_hash` | 1,424 kB | 0 | dedup lookup |
| `idx_gates_strategy` | 1,368 kB | 0 | `observability.py:1171` (`strategy_id` + `order_index`) |
| `idx_news_junk_hashes_hash` | 912 kB | 0 | junk dedup lookup |
| `idx_exp_request` | 704 kB | 0 | `decision_evidence.py:130` (`request_id`) |
| `idx_evidence_strategy_created_id` | 2,136 kB | 1 | evidence vault history |
| `idx_news_articles_source` | 688 kB | 1 | source-scoped listing |

A low scan count on a cold-cache index with a genuine query is not evidence of
dead weight; only a column that is *never* filtered is.

## SQLite

| DB | Size | Note |
|---|---|---|
| `artifacts/audit.db` | 416.4 MB | primary ledger (shadow mirror still present here — see below) |
| `artifacts/news.db` | 230.2 MB | |
| `artifacts/strategies.db` | 29.1 MB | |
| `artifacts/candle_intel.db` | 10.5 MB | |

### Backup retention

Three stale `audit_backup_20260924-*` copies remain at 416.4 MB each
(1.25 GB). R-9 (#576) added a real retention policy
(`hygiene/backup_retention.py: prune_backups`), but it only expires snapshots
past `max_age_days=30`; these are 5 days old, so they are within policy and
pruned-by-design. They will age out automatically. The policy exists, is
tested, and is scheduled — no manual cleanup is warranted.

### `audit.db` shadow mirror

The SQLite audit ledger is in the same state the PostgreSQL ledger was before
#575: 55,342 rows still carry 108,272,886 bytes of mirror payload, and the
eight additive L1 columns (`champion_probabilities`, `challenger_probabilities`,
the hypothetical trio, `shadow_mae_r`, `shadow_mfe_r`, `shadow_outcome`) **do
not exist** on the table at all — `#568`'s SQLite backfill never ran against
this file either.

This is the single largest remaining measured reclamation on the SQLite
surface, and #575 already made the machinery provider-portable
(`_add_missing_columns` reads either engine's catalog; the backfill emits
either dialect's JSON operators). It needs the same run against this file.

## What this wave changed, measured on the live PostgreSQL ledger

| Metric | Before | After |
|---|---|---|
| `shadow_decisions` mirror bytes | 108,272,886 | **0** |
| `shadow_decisions` TOAST chunk rows | (payload mirror) | **0** |
| `news_articles` duplicate body bytes | 44,090,347 | **0** (write path: 0 pre-gate bytes) |
| `idx_gov_events_model` | 5.5 MB, 0 scans | **dropped** |
| `idx_factory_cand_hash` | 552 kB, 0 scans | **dropped** |
| `audit_signals` live scan rate | 5 Hz probe | **0 scans/min** (R-1) |
| tests on merged main | 803 | 823 + new lanes |

## Next targets, ranked

1. ~~**SQLite `audit.db` shadow mirror reclaim**~~ — **done.** The same
   provider-portable path #575 made (`_add_missing_columns` reads either
   engine's catalog; the backfill emits either dialect's JSON operators) was
   run against the SQLite ledger: 108,272,886 bytes reclaimed across 55,342
   rows, then `VACUUM` compacted the file from **416.4 MB to 223.7 MB** (192.7
   MB returned), `integrity_check` `ok`, all data intact.
2. `shadow_decisions` 230 MB heap reservoir on PostgreSQL — intentionally not
   compacted (repo forbids VACUUM FULL; `never_delete=True`). Reclaimed
   organically as new inserts reuse the pages.

Everything else is either flat under load, protected, or has a real consumer.
The cumulative `audit_signals` counter should be reset-aware in any future
comparison.
