# NSE Database & Data-Lifecycle Remediation — Retention & Data-Value Matrix

Wave: `agent/hermes/db-lifecycle-remediation`
Base: `origin/main` `0db0c23e` (the brief cited `b9248e97`; verified ancestor of `origin/main`, main had advanced)

This is deliverable #4 (RETENTION MATRIX), #5 (PURGE ARCHITECTURE) and #7
(SQLITE PHYSICAL DESIGN) for the news domain, restricted to what this lane
actually changed and actually measured. It is not a whole-repo inventory.

## The law this wave enforces

> No data should enter persistent storage merely because it is available.
> A persistent record must have a documented reason.

Applied to `news_articles`, the largest table in the news domain:

| metric (read-only probe 2026-09-28) | value |
|---|---|
| rows | 25,030 |
| total relation size | 79.5 MB |
| heap | 17.5 MB |
| TOAST | 55.2 MB |
| `body` logical bytes | 28,521,694 |
| `summary` logical bytes | 28,517,874 |
| share of table that is `body`+`summary` | 68% |
| share of the whole 588 MB database | ~9% |

`body` and `summary` were the same text, stored twice, per article.

## Root cause (MEASURED, not inferred)

`sources/base.py` copies the RSS `<description>` INTO `body` for feeds that
carry no `<content:encoded>` (`_normalize_feedparser_entry`,
`_parse_xml_minimal`). The duplicate is created by the producer, on purpose,
to give the analysis pipeline a single text field to read. It was never a
storage decision — it was a convenience that became a 54.4 MB cost.

`body` IS genuinely consumed:

| consumer | where | what it needs |
|---|---|---|
| keyword extractor | `analysis/keywords.py::_iter_texts` | title+summary+body |
| local analyzer | `analysis/local.py::_title_and_text` | title+summary+body |
| LLM prompt | `ai_service._build_user_prompt` | title+summary+body |
| payload builder | `analysis/pipeline.py::_build_payload` | title+summary+body |

So the column cannot be dropped. The duplicate is the target.

## Value class of the duplicated payload

V6 (DERIVED AGGREGATE) is the wrong class — the text is not an aggregate.
The correct reading is: the duplicated `body` is **DROP / EPHEMERAL** by the
value law, because it carries no information the same row's `summary` does
not already hold. The *unduplicated* `body` (real full-text) is
V3 (MODEL/RESEARCH VALUABLE) — it is the only text richer than the summary
and the analysis pipeline consumes it.

## Fix the source, not the symptom

Two halves, one contract. Neither half is a read-side filter or a
post-hoc purge of "old" data — old data was never the problem, duplicated
data was.

### 1. Pre-insert value gate (prevents new waste)

`ingest/deduplicator._gate_duplicate_payload(summary, body)` answers one
deterministic question before persistence:

> does this body carry information the summary does not?

If not, the canonical row records `body = ""`. The analysis consumers
already fall back `body -> summary`, so no capability is lost.

Identity continuity is preserved: `_content_fingerprint` hashes the RAW
summary+body (pre-gate), so `article_hash` is stable across the change and
a re-poll cannot mint the same article as new.

Gate cost (measured): **11.3 µs/call**, against a 100 µs write floor —
8.8× cheaper than the persistence it prevents. The gate is two string
normalizations, never a DB query, never a remote call.

### 2. Bounded archival (reclaims existing waste)

`NewsDatabase.archive_duplicate_payloads()` applies the same contract
retroactively to the 25,030 existing rows. Properties, each pinned by a
test:

| property | how | test |
|---|---|---|
| bounded | `max_rows` caps a call; the last round fetches only the remainder | `test_max_rows_bounds_a_single_call` |
| resumable | keyset cursor (`article_id > ?`), not OFFSET; resumes at the id it stopped at | `test_resumable_after_max_rows` |
| idempotent | a second run reports 0 newly archived and terminates immediately | `test_idempotent_second_run_is_noop` |
| concurrency-safe | two racing workers leave exactly 40 archived rows, never double-counted | `test_concurrent_runs_do_not_corrupt` |
| observable | counters: eligible / archived / skipped / bytes_before / rounds | every test |
| dry-run (shadow) | selects and measures without mutating | `test_dry_run_selects_but_does_not_delete` |
| lossless | `summary` is never touched; the surviving canonical text | `test_duplicate_payload_reclaimed_and_summary_intact` |

The keyset cursor is load-bearing, not cosmetic: a row whose body carries
real content is *skipped*, so it stays `payload_archived=0` with a
non-empty body and would be re-selected by an OFFSET/LIMIT window forever.
The cursor advances past every processed id — one pass terminates exactly
once.

### Eligibility (what is protected)

| row | archived? | why |
|---|---|---|
| `body` == `summary`, analyzed | yes | duplicate payload; decision value already extracted |
| `body` ≠ `summary` | no | the body is the only source of richer text (V3) |
| not yet analyzed | no | the raw text is the only evidence its value can be derived from |
| `summary` | never | it is the surviving canonical text |

The unanalyzed guard is `_ARCHIVE_REQUIRES_ANALYSIS` and is the reason the
archival is a *storage-tier* decision rather than an age decision: no row
is archived for being old, only for being duplicated AND already consumed.

## Retention matrix — news payload

| table | tier | row lifecycle | payload lifecycle | owner |
|---|---|---|---|---|
| `news_articles` | TIER_4_NEWS_INTELLIGENCE | `never_delete=True` — canonical evidence, row is never deleted | `archive_after_days=7.0` — the duplicate payload is reclaimed once the article is analyzed and 7 days old | `NewsDatabase` |

`never_delete` and `archive_after_days` are deliberately independent. The
retention engine previously short-circuited `is_archive_candidate` on
`never_delete`, which conflated "this row must never be deleted" with
"this row's duplicate payload must never be reclaimed." They are different
statements about different data. `classify()` and `is_archive_candidate()`
now honour that distinction.

Provenance is honest rather than silent: an archived row carries
`payload_archived = 1` and `payload_archived_at`, so a reader renders an
ARCHIVED marker instead of a mysteriously empty body.

## SQLite physical design

* the two new marker columns are added by the idempotent
  `_ensure_article_status_column` heal (the same migration-safe path as
  `article_status` / `published_at_source`), so a legacy database heals on
  boot and a fresh install gets them in the base DDL;
* the schema snapshot extractor emits them (verified), so the PostgreSQL
  translation path receives the same columns — one schema, two engines, no
  second spelling;
* the archival scan projects `(article_id, summary, body)` only, never
  `SELECT *` — the TOAST/page cost of the scan is bounded by the payload
  columns it actually needs;
* the archival writes are single-row `UPDATE`s inside the batch connection,
  so no long transaction and no WAL spike.

## Before / after

| metric | before | after | evidence |
|---|---|---|---|
| duplicate `body` bytes entering the table per 25,030-row ingest cycle | 28,521,694 | 0 (gate) | benchmark: `duplicate-fraction=0.971`, `projected_body_bytes_saved=27,705,002` on the production mix |
| duplicate `body` bytes already on disk | 28,521,694 | 0 after archival runs | `archive_duplicate_payloads` counters |
| gate cost per event | n/a | 11.3 µs | benchmark, 100k-call floor |
| columns consumed by the hot analysis join | body+summary | summary (+ body when it is real) | `resolve_article_body` in 4 consumers |
| read-side fallback rules | 2 (inline in `ai_service`, plus implicit) | 1 (`resolve_article_body`) | single owner of the contract |

Projection (benchmark fixture, production mix): **27,705,002 body bytes
avoided** on the 25,030-row ledger.

## What this lane did NOT do

* did not delete any row — `news_articles` is canonical evidence and the
  row lifecycle is unchanged;
* did not drop the `body` column — it is consumed by the analysis pipeline
  and real full-text bodies are preserved;
* did not change `article_hash` identity — the fingerprint hashes the raw
  payload pre-gate;
* did not add a cache — the correct fix was to stop writing the duplicate,
  not to cache a read of it;
* did not touch the audit ledger, which is `never_delete` and immutable.

## Follow-ups this lane deliberately leaves open

1. The archival is exposed as a bounded operation and wired into the
   retention engine's `archive_eligible_payloads`, but the periodic
   scheduler call is not yet on a timer — the operator runs it explicitly
   (dry-run first). Scheduling it belongs to the purge-scheduler lane.
2. A PostgreSQL-side `VACUUM`/bloat re-measurement after archival belongs
   to the vacuum lane; this lane measured logical bytes, not bloat.
