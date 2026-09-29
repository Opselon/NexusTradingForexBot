# DEEP-OPT L2 — news AI summary echo

## What was measured (not estimated)

Read-only probe against the live ledger (`artifacts/news.db`):

| metric | value |
|---|---|
| `news_ai_analysis.summary` total | **45.0 MB** across 20,939 rows |
| rows whose summary == the article's `summary` | 5/5 sampled (byte-identical) |
| `news_articles.summary` | the same text, already persisted once |
| `news_articles.body` | also the same text (pre-existing L0 value gate) |

The column is a second copy of a text the deterministic ingest pipeline
already stores. It is not analysis output that happens to resemble the
source — it is the source.

## Root cause

`_build_user_prompt` (src/nexus_scalp/news/ai_service.py) asks the model to
"state the article's core fact base". A model with nothing to add restates the
source summary verbatim, and `_persist_ai_analysis` stored that restatement as
the analysis row's own `summary`. Contract Part 5 rule 9 (dedup) and rule 14
(column projection): the second copy answers no question the first does not.

The **input** side was already fixed by the earlier wave —
`resolve_article_body` re-materializes the body from the summary so the prompt
is byte-identical while the DB stores one copy. L2 is the **output** side of
the same leak: the gate existed on read, not on write.

## The change

`_strip_summary_echo(summary, article)` is applied to the model result before
persistence:

* `summary == article.summary` → dropped (pure restatement)
* `summary` starts with the source and adds nothing → dropped
* `summary` differs from the source → **kept** (that is analysis, not echo)

The prompt is untouched. The model still sees the full article.

## LOSSLESS verification (rule 85)

The gate removes only the restatement. Any summary the model actually authored
differs from the source and survives by construction. Tested directly:
`test_authored_summary_kept`, `test_authored_summary_with_shared_prefix_kept`
(the gate compares against the source, so an authored summary that opens with
the article's first clause and then adds substance is never truncated).

## Expected effect

| metric | before | after |
|---|---|---|
| bytes persisted per echoed analysis | ~2,148 B avg | **0** |
| bytes persisted per authored analysis | ~2,148 B avg | unchanged |
| reclaimable on the existing 20,939 rows | — | **~45 MB** |
| prompt sent to the provider | unchanged | unchanged |

## Rollback (rule 86)

* The reclaim is a separate opt-in CLI command, `nse db compact-summary-echo`,
  with a `--dry-run` that reports reclaimable bytes without writing. It is
  never called from the runtime path.
* Existing rows are untouched until an operator runs the command.
* The gate is one pure function with no state; removing the call restores the
  previous behavior exactly.

## Verification actually executed

```
tests/unit/news/test_news_summary_echo.py .................... 12 passed
tests/unit/news/ ............................................ 67 passed
ruff check + ruff format --check ............................. clean
mypy src/nexus_scalp/news/ai_service.py ...................... clean
```

## Not measured (no fabrication)

* the live 45.0 MB column after the reclaim runs — that is an operator action
  against the production database, not a lane action;
* how many of the 20,939 rows are echo vs authored. The sampled join showed
  echo, and the dry-run command reports the exact split before it changes
  anything, but the full-corpus number was not computed in this lane.
