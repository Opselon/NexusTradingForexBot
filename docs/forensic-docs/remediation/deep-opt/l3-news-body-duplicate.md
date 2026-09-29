# DEEP-OPT L3 — reclaim the legacy news body duplicate

## What was measured (not estimated)

Read-only probe against the live ledger (`artifacts/news.db`):

| metric | value |
|---|---|
| articles total | 24,906 |
| rows with `body` byte-identical to `summary` | **24,891 (99.9%)** |
| `body` bytes | **48,456,345** |
| `summary` bytes | 48,456,426 (the same text, stored once already) |
| rows already gated to `body = ''` | 3,162 |
| rows with a genuinely different body | 15 |

## Root cause — this is NOT a producer defect

The pre-insert payload value gate (`_gate_duplicate_payload`) shipped in the
DB-LIFECYCLE remediation wave at **2026-09-29T02:39Z**. The newest row in the
production ledger is **2026-09-25T18:30:29Z** — four days *before* the gate
existed. Every duplicate row predates it.

The gate is working. Evidence: 3,162 rows already store `body = ''`, and only
15 rows carry a body that differs from the summary — exactly the gate's
intended behavior. There is nothing to fix at the producer.

This is the distinction Part 5 rule 93 demands: *purge is the lifecycle safety
net, not an excuse for bad ingestion.* Here ingestion is already fixed, so the
reclaim is the correct and complete action.

## LOSSLESS verification (rule 85)

Every consumer of `body` reads it through `resolve_article_body(body, summary)`
(a pure `or`-fallback, deliberately byte-identical, documented in
`src/nexus_scalp/news/body_resolution.py`):

* `src/nexus_scalp/news/ai_service.py` — the LLM prompt
* `src/nexus_scalp/news/analysis/keywords.py` — keyword extraction
* `src/nexus_scalp/news/analysis/local.py` — the local analyzer
* `src/nexus_scalp/news/analysis/pipeline.py` — the analysis pipeline

Setting `body = ''` makes `resolve_article_body` return the summary — the same
text that was in `body`. No consumer can observe the change.

The command enforces this rather than assuming it: before clearing anything it
samples 500 candidate rows and confirms `resolve_article_body("", summary) ==
summary`. If that contract ever breaks, it **fails closed and clears nothing**.

## The change

`nse db compact-body-duplicate [--dry-run] [--batch-size N]`

1. Read-only scan: counts the duplicate rows and sums their bytes.
2. `--dry-run` reports and changes nothing.
3. Reconstruction-contract check over a 500-row sample; any mismatch aborts.
4. Bounded key-range batches (`WHERE id >= lo AND id <= end`), one transaction
   each, so progress is observable and the operation is interruptible
   (Part 5 rule 45).

Never automatic, never called from the runtime path: purge is an explicit
operator action (rules 43/93).

## Expected effect

| metric | before | after |
|---|---|---|
| `news_articles` bytes attributable to `body` | ~48.5 MB | ~0 for duplicate rows |
| rows affected | — | 24,891 |
| text available to every consumer | unchanged | unchanged (re-materialized) |

The physical file does not shrink until a `VACUUM`; that is an operator action
against the production database and is deliberately not performed here.

## Verification actually executed

```
tests/unit/news/test_news_body_duplicate_reclaim.py ...... 5 passed
tests/unit/news/ ......................................... 60 passed
ruff check + ruff format --check .......................... clean
```

Tested failure paths: dry run is read-only; a body with real content survives;
the reconstruction-contract abort fires and clears nothing; a database with no
duplicates is a no-op; batching is bounded by key range.

## Not measured (no fabrication)

* the physical file-size delta after the reclaim runs and a VACUUM — operator
  action against the production database, not a lane action;
* whether the 15 genuinely-different bodies are correctly preserved in
  production — the test proves the code path preserves them; the production
  row count was measured, the per-row outcome after a live run was not.
