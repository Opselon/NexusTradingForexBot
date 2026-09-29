# DEEP-OPT L2 — evidence correction (supersedes the shipped estimate)

The L2 PR (#571) shipped with a **sampled** claim: "45.0 MB of a 45.0 MB column
is byte-identical to the source summary", based on a 5-row join. A full-corpus
read-only probe against the same production ledger gives the exact figure, and
it is different.

## Measured (full corpus, not sampled)

| metric | value |
|---|---|
| `news_ai_analysis` rows total | 20,939 |
| rows joinable to `news_articles` | 20,880 |
| rows where summary == article summary | **18,400 (87.9% of total)** |
| rows where summary differs from article summary | **0** |
| rows with no matching article (orphaned) | 59 |
| rows with empty summary already | 2,480 |

Correction to the shipped statement:

* The column holds 45.0 MB across **20,939** rows, not 18,400.
* The **addressable echo is 18,400 rows**, not all 20,939: 2,480 rows are
  already empty and 59 are orphaned (no article to compare against), so the
  reclaim command's WHERE clause cannot and does not touch them.
* Every row that *can* be compared is an echo (18,400/18,400, 100%). Zero rows
  carry an authored summary that differs. So the qualitative finding is
  unchanged and if anything stronger: **no analysis row in this ledger
  contains a summary the model actually wrote.**

## Why the shipped number was wrong

The probe used `LIMIT 5` on a join and extrapolated. The column total (45.0 MB)
was exact; the *composition* was inferred from five rows. The reclaim command
itself computes the correct set at runtime (`JOIN ... WHERE a.summary =
n.summary`), so the **code is correct** — only the evidence document's framing
was imprecise.

The `LIKE n.summary || '%'` prefix superset could not be measured: SQLite
rejects the pattern ("LIKE or GLOB pattern too complex") because the summaries
are multi-kilobyte HTML strings. The exact-equality set is the reliable number;
the prefix form remains a code-path guard, not a measured count.

## Effect on the change

None. The gate is LOSSLESS by construction (a summary that differs from the
source survives), and the measured corpus confirms there is nothing that
differs to preserve. The reclaim's addressable set is smaller than documented
and the command already handles it correctly.

This is an accuracy correction to the evidence record, not a revert.
