# Wave 2026-09-14 — Lane 06: News-Engine Intelligence Audit

**Repo:** `C:/Users/Capsizer/source/repos/NexusTradingForexBot` @ `nse/master-active-scalper-wave` (`9431edd2`)
**Method:** read-only. Code read under `src/nexus_scalp/{news,calendar,features,application,governance,model_*}`; all DB
queries opened `artifacts/news.db` / `artifacts/audit.db` with `sqlite3.connect("file:...?mode=ro")`; model artifacts
opened with `torch.load` / `numpy.load` (no writes). No git write commands executed. Helper scripts were written to
`%LOCALAPPDATA%/Temp/nse06/` (outside the repo) — this file is the only repo write.
**VERIFIED** below means "produced by a command I actually ran in this session".

---

## 0. Verdict (one line)

News is **ALIVE as a data pipeline and DEAD as a trading influence**: everything that looks like a news block is
either computed-and-discarded (`verdict.blocked`), hard-wired off (`is_macro_news_window=False`), or observed-only
(calendar event gate). The one news path that *does* reach the model — the 70D news block at indices 50..59 — is a
train-time constant zero and a serve-time `±5` scaler-saturation shock, i.e. noise, not signal.

---

## 1. Subsystem map

| Layer | Files | Table(s) | Live wiring |
|---|---|---|---|
| Ingest | `news/ingest/{fetcher,deduplicator}.py`, `news/sources/base.py` | `news_articles`, `news_sources`, `news_health` | `NewsWorker.tick` → `NewsEngine.ingest_cycle(max_sources=8)` (off event loop) |
| Analysis (local) | `news/analysis/{local,pipeline,decay,consensus}.py` | `news_analysis`, `news_impacts`, `news_entities`, `news_topics` | same worker, `run_pro_cycle(limit=200)` |
| Analysis (LLM) | `news/pro_auto.py`, `news/ai_service.py`, `news/budget.py` | `news_ai_analysis`, `news_analysis_runs` | worker + UI `POST /api/news/analyze` |
| Hygiene | `news/db_queries.py` (`auto_prune_irrelevant`), `hygiene/retention.py` | `news_junk_hashes`, `news_analyzed_hashes`, `news_prune_audit` | pro_auto cycle |
| Context cache | `news/context.py`, `news/models.CurrentNewsContext` | — (in-memory) | worker `refresh()`; tick path reads cache only (INV-001) |
| Gate (news) | `news/gate.py` (`NewsGate`) | — | `application/live/tick_pipeline.py:101-134` |
| Calendar (forward) | `calendar/{providers,classify,gate,worker,models}.py` | `calendar_events`, `calendar_worker_state` | lazily composed in `application/live/maintenance.py:465-481` |
| Feature family | `features/{schema_contract,features70}.py`, `shadow/shadow70/news_provider.py`, `governance/alignment.vectorize_news_context`, `model_generation/news_bridge.py` | — | `inference.build_live_feature_vector` (70D), `live_engine._build_retrain_record` |
| Drift | `news/drift.py` (PSI on slice 50..60) | — | **production caller: none** (only `model_lifecycle/feature_drift.py` reuses `_psi_1d`; `news_drift_check` callers are tests) |

Empty in production (`VERIFIED` counts): `news_consensus` 0, `news_event_links` 0, `news_trade_links` 0,
`news_article_versions` 0, `news_post_event` 0. Those five tables are schema + retention + read-API only.

---

## 2. Source list and credibility fields

`news_sources` = 13 rows, seed `2026-09-07-v3` (VERIFIED). Credibility is carried as **`tier` + `priority`**, with
`NewsSource.trust_weight` (TIER_1 1.0 / T2 0.8 / T3 0.55 / T4 0.25) derived from tier:

| tier | source | kind | enabled | priority | last health (VERIFIED) |
|---|---|---|---|---|---|
| 1 | fed | OFFICIAL | 1 | 1.00 | 200, healthy |
| 1 | ecb / boe | OFFICIAL | 1 | 0.95 / 0.90 | 200, healthy |
| 1 | ustreasury | API (JSON manifest) | 1 | 0.85 | 200, healthy |
| 1 | bls / bea / cftc | OFFICIAL | **0** | 1.0/0.95/0.9 | disabled (WAF 403 / 0-entry feed / no feed) |
| 2 | cnbc_business / fxstreet / marketwatch | RSS | 1 | 0.8/0.75/0.7 | fxstreet **403, 27 consecutive failures, unhealthy** |
| 2 | reuters | RSS | **0** | 0.8 | dead host, disabled |
| 3 | forexlive | RSS | 1 | 0.60 | 304 conditional-GET OK |
| 3 | zerohedge | RSS | 1 | 0.45 | 200, healthy |

Health: 8/9 enabled sources healthy → `CHECK-NWS-01 DEGRADED` (in `artifacts/forensics/forensic_health_snapshot.json`,
reproduced VERIFIED). Exponential backoff is capped at 3600 s, conditional GET (ETag/Last-Modified), 2 MB body cap,
`OFFICIAL` adapter treats HTTP-200-with-zero-items as a typed failure — all VERIFIED in code and in `news_health`
(`backoff_until`, `last_status`, `consecutive_failures` columns populated).

**Finding 2.1 (structural, medium):** priority/tier feeds *only* the local importance score
(`importance_score(...) += source_priority * 0.25`) — it never enters the context weighting `w`, the gate, or the
feature block. So source credibility cannot down-weight a noisy feed's *influence*; ZeroHedge (priority 0.45, TIER_3)
supplies the single largest directional weight in the live context window (VERIFIED: `w` totals over the newest-300
window — ZeroHedge 14.49, ForexLive 12.00, BoE 8.83, Fed 8.58, CNBC 4.16, ECB 1.04, MarketWatch 0.92). The tier
weight that *was* designed for this lives in `compute_consensus`, which is never called (Finding 11.1).

**Finding 2.2 (volume skew):** 11,086 of 28,446 articles (39%) are Bank of England — a feed with no XAUUSD content
and no rate-decision cadence matching its volume; `article_status='IRRELEVANT'` covers 8,081 rows. The registry
ingests quantity from official feeds whose item type (statistics notices, transcripts, speeches) is mostly noise for
a gold scalper, then relies on the prune layer to relabel it.

---

## 3. Timestamp normalization / timezones

Storage is uniformly ISO-8601 UTC: **all 28,446 `published_at` and all 190 `calendar_events.scheduled_at` end in
`+00:00`; zero naive values, zero non-UTC offsets (VERIFIED)**. `EconomicEvent` *rejects* naive datetimes
(`_utc` field_validator) and the FF provider normalizes the source's explicit `-04:00` offset at ingest
(`providers.py:164-168`, `timezone='UTC (normalized from provider offset)'`, 185 rows). The FOMC provider stamps a
documented approximation (18:00 UTC on the last meeting day, 5 rows, `timezone='UTC (decision ~14:00 ET …)'`).
`news/ingest/deduplicator._as_dt`, `news/context._parse_dt`, and `models.normalize_datetime` all coerce to UTC.
Phase 6E rule "never compare naive local timestamps" is honored in `calendar/gate.py:128-130`.

**Finding 3.1 (HIGH — data-quality defect, VERIFIED by direct execution):** the RSS adapter parses timestamps with
`datetime.fromisoformat(value.replace("Z","+00:00"))` only, and RSS 2.0 `pubDate` is **RFC-822, not ISO-8601**.
I ran the adapter's exact `_parse_dt` against real feed formats:

```
'Mon, 14 Sep 2026 00:05:00 GMT'   -> 2026-09-14T00:41:28+00:00   FABRICATED(now)
'Fri, 12 Sep 2026 18:30:00 +0000' -> 2026-09-14T00:41:28+00:00   FABRICATED(now)
'Wed, 09 Sep 2026 14:00:00 EST'   -> 2026-09-14T00:41:28+00:00   FABRICATED(now)
'2026-09-13T20:36:30.727139Z'     -> parsed correctly
```

Any parse failure silently returns `datetime.now(UTC)`. The DB confirms this is the dominant path: **25,047 / 28,446
articles (88.1%) have `published_at` within 1 s of `created_at`**, and 100% of the fabricated rows carry microseconds
(a wall-clock signature), per source: FXStreet 100%, MarketWatch 97.3%, CNBC 93.8%, Fed 92.7%, ECB 95.8%, ZeroHedge
86.4%, ForexLive 86.9%, BoE 84.4% — and **U.S. Treasury JSON manifest 0%** (the only adapter whose payload carries
ISO-8601 Z timestamps). `updated_at == published_at` for 28,404 rows, so `assess_novelty`'s UPDATED branch is
unreachable — consistent with `novelty='NEW'` for 100% of articles and analyses (VERIFIED).

Consequences (all downstream of a silently fabricated event time):
* `NewsDecayEngine.freshness` measures *ingest* age, not event age — the module docstring's promise ("a late-analyzed
  4h-old article must sit at 4h-ago") is violated for 88% of rows; re-polled/late-discovered stories look brand new.
* The context staleness test (`(now - newest published) > 3600 s`) degenerates into "did the worker ingest in the
  last hour" — true whenever the worker is alive, so the `STALE → gate IGNORE` fail-safe essentially cannot fire
  while running (VERIFIED: 704 `[NEWS] context built` log lines in `logs/info/2026/09/`, states
  **HIGH_IMPACT 650 / NORMAL 50 / ELEVATED 2 / STALE 2**).
* 178 articles are back-dated >30 days and 0 are future-dated (VERIFIED), so the fabrication is one-directional.

---

## 4. Duplicate detection: `news_junk_hashes` (8,535) and `news_analyzed_hashes` (22,280)

Identity design (VERIFIED in `deduplicator.py`): `article_hash = sha256(normalized_url | normalized_title |
source_id | 60 s publish-bucket | content_fingerprint)` plus a stopword-pruned `title_hash`. Tombstones:
`news_analyzed_hashes` (22,280) blocks re-analysis; `news_junk_hashes` (8,535) blocks re-ingest; the ingest path
checks both before the dedup search.

Real counts and real behavior (VERIFIED):
* Junk reasons: `LOW_IMPORTANCE_AND_LOW_XAUUSD_RELEVANCE` 7,742 (deterministic, rule `news-prune-v1`, actor
  `pro_auto`), plus LLM-derived `llm_junk:*` 362 and `LLM_JUNK_SOFT:*` 234, `backfill_irrelevant` 197.
* 7,861 hashes sit in **both** analyzed and junk sets, and 8,188 junk-hashed articles still have a `news_analysis`
  row; `news_prune_audit` = 8,745 rows, all `AUTO_PRUNE → IRRELEVANT`. 8,920 pruned articles were nevertheless
  (re-)analyzed. The tombstones suppress *re-ingest*, not the influence of rows already in the analysis window.
* `is_duplicate=1` for only **3 of 28,446** rows and `evidence_sources` has length 1 for **100%** of rows → the
  "one canonical event, multiple source evidence" design never materializes. Root causes: (a) the analyzed/junk hash
  tombstone returns first and counts the item as `duplicate` before `find_duplicate_title` is consulted; (b)
  `NewsDeduplicator._recent_by_title` is in-memory only and empty after every restart, so the syndication window
  (3,600 s on publication proximity) has almost nothing to match against.
* **The duplicate class that matters is unhandled inside the live window.** Replaying the exact context window
  (newest-300 analyses, deduped by article_id as `context.build` does) and normalizing titles with the same
  stopword logic the deduplicator uses: **300 rows collapse to 187 distinct titles → 113 extra rows (38% inflation),
  0 of them cross-source** — i.e. the *same* feed re-publishing the same headline (ZeroHedge re-posts, FXLive wraps)
  earns a new `article_hash` because URL and the 60-second publish bucket changed. Every duplicate of a high-importance
  headline contributes its full weight to `conf_sum`/`bull`/`bear` and to `max(importance)`.
* `mark_deterministic_high_impact` (the zero-LLM CPI/NFP/FOMC tag) is **inert: 0 `DETERMINISTIC_EVENT` entity rows**
  while 183 CPI/FOMC/payrolls-titled articles were ingested after the Sep-07 seed (VERIFIED). Mechanism: the tag is
  inserted at ingest, then `pipeline._persist → db.replace_entities(article_id, …)` DELETEs every entity row for the
  article and rewrites only the local analyzer's taxonomy, which never re-emits `DETERMINISTIC_EVENT`. Side effects:
  the LLM-cost skip and `_deterministic_ids` routing in `run_pro_cycle` are always empty sets.

---

## 5. Stale-event handling

* Per-item: exponential half-life decay per horizon — BREAKING 15 min, MACRO 4 h, POLICY 24 h, STRUCTURAL 5 d;
  rows with `freshness <= 0.02` are dropped from the window (`context.build`). `stale_after_sec=3600` flips the whole
  context to `NewsState.STALE`, and `NewsGate` returns `IGNORE / NEWS_UNAVAILABLE_OR_STALE` for stale or unavailable
  contexts — a correct "no fake influence" design.
* Worker-side job expiry: `JOB_EXPIRY_SEC=6 h`, retry cap 3, bounded priority queue 1000, persisted checkpoint
  (`news_worker_state.cycle_count=4158`, last cycle 2026-09-13T20:44Z; VERIFIED).
* Horizon mix is a silent defect: the local analyzer only ever emits MACRO or POLICY (VERIFIED: `news_analysis`
  horizons = MACRO 12,845 / POLICY 12,225, **no BREAKING**), and `NewsTopic` classification is the only input to
  `_horizon()`. So the 15-minute breaking half-life is dead in practice; every breaking story decays on a 4 h/24 h
  curve, and `any_breaking` (the `NewsState.BREAKING` trigger, the gate's other blocking state) can never be set by
  local analysis. **The gate's `BREAKING` blocked-state branch is unreachable on the production path.**
* Calendar staleness: `CALENDAR_MAX_STALE_SEC = 72 h`; `stale_mode` default `"observe"` → a stale/INVALID calendar
  reports `CLEAR` with reason `CALENDAR_STALE_OBSERVE`, and only `block_high_impact` reports `UNKNOWN_RISK`
  (VERIFIED by executing `evaluate_event_window` against the envelope reconstructed from `calendar_events`):

```
events=190 health=GOOD future_high_impact=30
now (2026-09-14 00:45Z)          -> CLEAR       NO_ACTIVE_WINDOW
2026-09-16 17:50Z (FOMC pre)     -> PRE_EVENT   Federal Funds Rate  (+10.0 min)
2026-09-16 18:20Z (FOMC post)    -> PRE_EVENT   FOMC Press Conference (+10.0 min)
2026-09-11 12:35Z (CPI post)     -> POST_EVENT  Core CPI m/m          (-5.0 min)
INVALID calendar, observe        -> CLEAR       CALENDAR_STALE_OBSERVE
INVALID calendar, block_high_... -> UNKNOWN_RISK CALENDAR_STALE_FAIL_CLOSED
```

* No pruning of past events: **90 of 190 `calendar_events` are in the past and still `status='SCHEDULED'`** (12 of them
  `High`), because `_persist`'s upsert only updates `status/actual/forecast/previous/retrieved_at` when the provider
  re-sends the row, and `ff_calendar` publishes "this week" only; `news_prune_audit`/retention do not cover
  `calendar_events` (VERIFIED: not in `hygiene/retention.py`). Harmless today only because `future_high_impact()`
  filters `scheduled_at > now` — the gate math is correct, the table just grows dead rows (and the 72 h stale window
  plus 900 s refresh means the envelope survives provider outages with an increasingly past-only event list).

---

## 6. `calendar_events` (190) vs `news_articles` (28,446) importance labeling

Two different, non-reconciled vocabularies:

| | calendar_events | news_articles | news_analysis |
|---|---|---|---|
| scale | `Low/Medium/High/Holiday` (provider + deterministic override) | `MINOR` … | `TRIVIAL/MINOR/MODERATE/HIGH/CRITICAL` + `importance_score` |
| distribution (VERIFIED) | Low 129, High 42, Medium 18, Holiday 1 | **MINOR 28,446 (100%), `importance_score=0.0` (100%), `novelty='NEW'` (100%), `entities='[]'`, `topics='[]'`** | MINOR 11,390 / MODERATE 9,268 / CRITICAL 2,075 / HIGH 1,426 … |
| graded by whom | FF provider importance, forced to HIGH for strong-identity USD/EUR/GBP/JPY/CHF events (`classify_country_calendar_event`) | nobody — insert-only placeholder | `LocalNewsAnalyzer.importance_score` (keyword mass + tier priority) |

So the article-level importance column is a **write-once placeholder that nothing ever updates** (`grep` for
`SET importance` in `news/*.py` → no matches; VERIFIED). The only meaningful news-importance label is per-analysis, and
it is a keyword-sum score, not the article-level one that any UI/API surface shows. `calendar_events.event_type` is
137/190 empty (the classifier only tags 53), and 5 `High` events carry no canonical `event_type`, i.e. the provider's
importance alone gates them. `news_event_links` (the table meant to bind an article to an event) has 0 rows, so
release-day news and the forward calendar are two unrelated silos — an article about the 2026-09-16 FOMC cannot be
attributed to that event.

**Finding 6.1 (label calibration):** `importance_score` is additive and saturates. In the live window, 12 rows score
`>= 0.75` and only 1 of those titles contains "gold" (VERIFIED); examples scoring `importance=1.00, relevance=1.00`:
`investingLive Americas FX news wrap 11 Sept`, `US stocks rebound sharply, but major indices still close lower`,
`Bitcoin trades at a major support ahead of the US CPI report`, `Warsh Faces an "Incredibly Difficult Dilemma"`. The
formula (0.25 priority + 0.12/high topic + 0.08/institution + 0.10/macro marker + relevance mass) reaches 1.0 for a
multi-topic FX wrap, which then satisfies the context's `max_importance >= 0.75 → HIGH_IMPACT` latch.

---

## 7. XAUUSD relevance filtering: how generic "high impact" flows into trading

Relevance is computed in `LocalNewsAnalyzer.xauusd_relevance`: `+0.20` for any gold token, per-topic driver weights
(USD .18, BOND_YIELDS/INFLATION .20, GEOPOLITICS/INTEREST_RATES .16, …), `+0.05` per strong driver keyword (capped
.25), `+0.15` per gold price-action phrase, plus a driver-only baseline `max(score, 0.25/0.40/0.55)` for
1/2/3 driver hits, finally `min(1.0, …)`. Negative handling is **inert on the live path**: `_GOLD_NEGATIVES` is
declared in `local.py`
and never read there (VERIFIED: `grep -rn _GOLD_NEGATIVES src/nexus_scalp` → the declaration plus one docstring mention
in `analysis/keywords.py`). Negatives are honored only by the separate keyword-dataset matcher
(`keywords._count_mentions`, used by the UI's `keyword_hits_for_article` / coverage endpoints), so the live score lets
"Golden State"/"gold medal" earn the +0.20 gold token (`has_gold = "XAUUSD" in entity_names or "GOLD" in text`).

Its only routes to trading:
1. `xauusd_relevance = MAX(relevance)` over the newest-300 analyses → `NewsGate` floor check
   `relevance = max(xauusd, usd) >= 0.25`. Because the max over 300 rows in a live macro window is 1.0 (VERIFIED from
   the replayed context: `xauusd_relevance=1.0, usd_relevance=1.0`), **the `LOW_NEWS_RELEVANCE` early-exit never
   fires** — generic "high impact" always clears the bar.
2. `importance_score >= 0.6` + `freshness > 0.2` → `active_event_count` (observed 21) and the
   `max_importance >= 0.75 → HIGH_IMPACT` state latch.
3. The 70D news family dims 51 (xauusd_relevance) and 52 (usd_relevance) — see §8.
4. UI surfaces (`debug_snapshot`, `/api/news/*`, market radar `news_state`) — display only.

There is no per-symbol mapping, no asset-impact profile use in the live path (`seed._ASSET_PROFILES` is consumed by
nothing at serve time — `get_asset_profile` has no production caller; VERIFIED), and no linkage from an article to the
trade it supposedly influenced (`news_trade_links` = 0 rows).

---

## 8. The 10D news feature projection — dead at train, saturated at serve

Contract (VERIFIED in `features/schema_contract.py`, `docs/70D_DATA_CONTRACT.md`): `scalp_v3` 70D =
base 0..49 | **news 50..59 = `news_context_v1` fields 0..8 + field 10 (`news_state`)** | liquidity 60..69. Not a blind
slice; `source_consensus` (idx 9) and `time_since_event_sec` (idx 11) are excluded by design; drift is fail-loud at
import (`canonical_feature_names()` guards). Live projection path: `news_engine.current_context()` →
`governance.alignment.vectorize_news_context` (12 fields, BUG-197 aggregate→flag, BUG-217 encodings clamped to 3.0)
→ `shadow70.news_provider.build_news_10` → `features.liquidity_runtime.build_70d_vector` →
`validate_70d_vector` (bounds ±3, hash). Missing/unavailable context → the documented `[0.0]*10` neutral block.
The same projection is used by the retrain-record builder (`live_engine.py:333-365`).

**Serving artifact is 70D (VERIFIED):** `configs/base.yaml` `model_artifact_path=artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt`,
`sha256(...)[:16] = bb1f0afe30f746da` — identical to `model_runtime_health.champion.artifact_hash` at
`2026-09-13T20:45Z`; logs show `feature_schema=scalp_v3` and `dimension=70`; `CHECK-MDL-03 PASS (input dim 70 matches
serving schema 70)`.

**Train-time status of the news dims: constant.**
* `model.scaler.npz` for the champion: `std == 0.001` at exactly 11 indices — **all ten news slots 50..59 plus
  `rapid_reversal_spike_val` (idx 9)** — with `mean == 0.0` there (VERIFIED). 0.001 is the
  `std = np.maximum(std, 1e-3)` floor in `training/walk_forward_trainer.py:1762-1763`, i.e. the trainer saw
  **zero variance** in every news slot.
* All 70D `scalp_v3` datasets in `artifacts/model_generation/datasets/` have `feat_50..feat_59` constant 0.0:
  `ds_70d_clean_m1_20260904` (99,946 rows, 10/10 news cols nuniq=1), `t70d_f1_full_m1` (99,946 rows, nuniq=1 and
  **`news_status='FEATURE_DISABLED'` for all 99,946 rows**), `ds_3cc0a9cc3cbda2ee`, `a2_lineage_probe_5k`, two 66-row
  probes (VERIFIED). The only dataset with a real, informative news block is
  `ds_9704ec379e18a08b` (2,892 rows, **M5**, schema `scalp_v2`, Aug-31: feat_50 nuniq 1970, feat_57 nuniq 2842,
  feat_59 nuniq 16). The bridge exists and works (`news_bridge.build_news_frame_from_db`, wired in `cli/doctor.py`),
  it is just not used by the M1 70D production builds.
* Independent corroboration of zero gradient on constant inputs, measured on a *trained* artifact: in
  `model.pt.pre_direct_bak` (and `_champion_backup_20260905_012408`), base columns have **164/6400** entries outside
  the Kaiming-init bound 1/√70 = 0.11952 while news columns have **0/1280** (max|w| 0.119457 < bound). Constant
  inputs cannot move their weights. (The current champion's news columns are byte-equal to a fresh seed-42 init, but
  so are all 31 tensors — `CHECK-MDL-02` says that artifact is untrained init, which I reproduced independently — so
  byte-equality is not news-specific evidence; the dataset + std-floor + out-of-bound evidence is.)

**Serve-time status: NOT constant — and outside the training distribution.** Replaying the real live context
(HIGH_IMPACT, `active_event_count=21`, bull .065 / bear .159 / conflict .045 / fresh .82 / conf .55, computed with
`context.build`'s own arithmetic against `news.db`) through the canonical projection (VERIFIED):

```
news_context_v1 (12): [1.0, 1.0, 1.0, 0.0648, 0.1594, 0.0454, 0.0, 0.8194, 0.5454, 1.0, 2.0, 0.0]
news10 (news_family_v1): [1.0, 1.0, 1.0, 0.0648, 0.1594, 0.0454, 0.0, 0.8194, 0.5454, 2.0]
after champion scaler : [5 5 5 5 5 5 0 5 5 5]     90% of slots pinned at the ±5 clip
```

Because `std=0.001`, the smallest non-trivial news value already saturates: raw `0.0005 → z 0.5`,
`0.001 → 1.0`, `0.005 → 5.0`, `0.05 → 5.0`. The 70D bound check (±3) passes, then the scaler multiplies by 1000 and
clips to ±5, so the model's news inputs are a binary "training-zero vs saturated-±5" switch with no intermediate
resolution. `mean=0` makes the *disabled* projection (all-zeros) exactly in-distribution — **turning news OFF gives
the model the vector it trained on; turning news ON pushes 9 of 70 inputs off-distribution.**

Measured effect on the serving artifact (VERIFIED, on 24,987 real dataset rows, swapping only the news family;
see the caveat below):

| metric | news = 0 (train dist.) | news = live HIGH_IMPACT |
|---|---|---|
| scaled news slots | `[0]*10` | `[5,5,5,5,5,5,0,5,5,5]` |
| argmax vs train | — | **46.18 % of rows flip** (mild-context block: 35.64 %) |
| entry rate (prob ≥ 0.35, class ≠ NO_TRADE) | 0.888 % | **0.000 %** (222 entry rows → NO_TRADE, 0 reverse) |
| mean label agreement with model argmax | 0.184 | 0.071 |
| mean \|Δp\| / max \|Δp\| | — | 0.0036 / 0.0189 |
| counterfactual with an *honest* news scaler (std 1.0, mean 0.5) | — | max \|Δp\| 0.0011, flip 2.50 % |

**Caveat, stated plainly:** the champion currently served is `CHECK-MDL-02 CRITICAL` (byte-identical to a fresh
seed-42 init; I reproduced 31/31 identical tensors) and `CHECK-MDL-04` degenerate (`logit_std 0.056`, `sensitivity
0.012`), so the absolute flip/entry numbers above measure an *untrained* net; they should not be quoted as live PnL
impact. What generalizes to any artifact trained on these 70D datasets is the mechanism: 10 slots with zero training
variance, a `1e-3` std floor, and a serve-time producer whose realistic values exceed that floor by 2–3 orders of
magnitude. News is therefore **a dead constant at train time and a saturation shock at serve time — informative in
neither direction.**

---

## 9. Does news block trades today? Where the gates actually are

| Candidate gate | Production consumer | Effect today |
|---|---|---|
| `NewsGate` confidence adjustment | `application/live/tick_pipeline.py:116-124` (`proposal.confidence += adjustment`) | **Real but near-zero.** Runs after the policy's `CONFIDENCE_GATE`, so it cannot re-gate; downstream only `risk_engine.py:433` re-reads confidence (≥0.95 relaxes min-RR) and `:551` (calibrated sizing). No calibration artifact exists (`confidence_calibration.json` missing → `NOT_CALIBRATED`, multiplier exactly 1.0, VERIFIED by loading it). Net: affects the RR gate only for proposals already ≥ 0.95. |
| `NewsGate` CAUTION/BLOCK (`verdict.blocked`, `news/gate.py:169-175`) | **none** — `grep -rn "\.blocked" src/nexus_scalp` outside `news/gate.py`/`hygiene` yields only unrelated `blocked_by` fields; `tick_pipeline` reads `confidence_adjustment` only | **Blocked is computed and thrown away.** Today's state yields `blocked=True, confidence_adjustment=0.0` for every entry with conf < 0.6 (replayed for 0.36/0.5/0.55/0.6/0.7/0.9, BUY and SELL) → zero effect on the proposal. `web/debug_snapshot.py:832-837` renders that unused flag as a gate row `NEWS status=BLOCKED` — observability claiming enforcement that does not exist. |
| `is_macro_news_window` → `MACRO_NEWS_FREEZE` regime → `FREEZE_ALL` → policy `GUARDIAN_GATE` (`policy.py:461`, `:2160-2185`, `rule_matrix.py:_eval_rule_news_spike_fade`) | both live call sites hard-code `is_macro_news_window=False` (`tick_pipeline.py:459`, `:470`) | **Unreachable.** VERIFIED from data: 1,469 `audit_signals` rows, guardian blocks = 356 with regime `HIGH_SPREAD_CHOP` only; `MACRO_NEWS_FREEZE` never appears. The engine's most powerful news lever (freeze all entries) is wired to a literal `False`. |
| `calendar/gate.evaluate_event_window` (PRE/POST event windows, `stale_mode`) | `calendar/worker.gate_verdict()` called only from `web/news_liquidity_mslie_routes.py:560` (health payload) | **Telemetry only.** No trading-path caller of `gate_verdict` / `evaluate_event_window` / `next_window_open`. Also `EventGatePolicy(calibrated=False, pre/post=15 min)` defaults are not wired to any config section (no `EventGatePolicy` construction outside `calendar/worker.py` and tests). |
| Order/risk layer | `grep -rn "news" src/nexus_scalp/execution/*.py src/nexus_scalp/risk/*.py` → **zero matches** | `order_manager`, `market_entry_gate` (weekend/session only) and `risk_engine` have no news awareness at all. |
| 70D assembly failure | `inference.py:190-212` raises → inference blocked for the tick | News can trigger this only indirectly (`vectorize_news_context` raises on non-finite producer values → caught → zero block). Historically real: BUG-197/BUG-217 (news value out of ±3) blocked *all* 70D inference; now fixed by encoding bounds. The news path itself is failure-isolated (`except: news10=[0]*10`). |

**Answer: no. Nothing in the news subsystem blocks, vetoes, or sizes a trade today.** The realized production
influence of news is (a) the ±5-saturated news block inside the 70D tensor (noise, §8), (b) a bounded ±0.05/0.10
confidence nudge that lands after the confidence gate and mostly on the RR relaxation at conf ≥ 0.95, and (c) UI
fields. In the state the engine has been in for 650 of the last 704 context builds (`HIGH_IMPACT`), the gate returns
`confidence_adjustment = 0.0` and an unused `blocked=True` — i.e. precisely the state that is supposed to be the
risk-off posture has *no* effect on decisions at all.

---

## 10. Pipeline assessment

```
13 sources (9 enabled, 1 unhealthy: fxstreet 403×27)
  → fetch (conditional GET, backoff, 2MB cap, typed OFFICIAL emptiness)
  → canonicalize (sha256 identity)  ⚠ 88% of published_at fabricated to ingest time (§3)
  → tombstones (analyzed 22,280 / junk 8,535) ⚠ pre-empt the title dedup; 38% duplicate-title inflation in-window (§4)
  → local rule analysis (25,070 rows, provider='local', local_only=1 for 100%)
      3,654 LLM rows exist in news_ai_analysis (claude-opus-5) but 3,030 are
      'completed_insufficient' and 401 AUTH_ERRORs recur in logs; the LLM verdict
      is written to a SIDE table and never merged into news_analysis
  → prune (8,745 AUTO_PRUNE→IRRELEVANT; 8,081 IRRELEVANT rows still analyzed)
  → context cache (newest-300 analyses, decay, MAX-relevance, latched state)
  → NewsGate (bounded ±0.05/0.10 confidence; blocked computed, unused)
  → features 70D news block (train-constant 0, serve-saturated ±5)
  → model / policy / risk / orders (no news awareness in execution or risk layers)
```

Correct-by-design parts worth keeping: a dedicated `news.db` (trading survives total news deletion), worker-side
DB reads only (INV-001 cache-only tick path), failure isolation at every seam, the "news can never force a direction"
hard rule in `NewsGate`, horizon-specific half-lives, `article_hash UNIQUE`, prune audit trail, and the honest
`FEATURE_UNAVAILABLE`/neutral-block contract (`assemble_70d` refuses to fabricate).

---

## 11. Over-/under-weighting evidence

**Over-weighted (louder than they should be)**
1. **`xauusd_relevance` as a MAX over a 300-row window.** One generic FX wrap with 3 driver keywords pins relevance
   1.0 and `max_importance` ≥ 0.75 for the whole window; the relevance floor at the gate (0.25) then never filters
   anything. 62 of 300 in-window rows are driver-only (no gold token) with relevance ≥ 0.5; 12 rows ≥ 0.75, only 1
   mentioning gold (§6.1, §7).
2. **Duplicate coverage masquerading as corroboration.** The 113/300 repeated-title rows each add weight, so a
   single story re-posted by one feed looks like a consensus of many. `source_consensus` is not a consensus at all:
   `consensus_sum/weights ≡ 1.0` (VERIFIED `source_consensus=1.0` in the replayed context) — a self-cancelling ratio,
   not independent-source agreement.
3. **`freshness` as a proxy for "available".** Fabricated publication times keep the window permanently fresh; the
   state machine reports `HIGH_IMPACT` 92% of the time, which trains operators (and the debug UI) to treat a red flag
   as background noise.
4. **`importance_score` scale saturation** (additive, cap 1.0) — 1,993 rows at CRITICAL, ~2,075 HIGH+CRITICAL of
   25,070; the ordinal bucket therefore has weak discriminating power at exactly the threshold (0.75) the gate latch uses.

**Under-weighted (real signal that never lands)**
1. **The whole LLM judgment layer.** 3,654 deep analyses (XAUUSD relevance prose, sentiment, insufficient-evidence
   flags) never enter `news_analysis`, the context, the gate, or the feature vector — the AI-vs-local direction
   disagreement table shows 163 rows where both are directional, of which **22 conflict outright** (AI `BULLISH` vs
   local `BEARISH` ×15, AI `BEARISH` vs local `BULLISH` ×7) plus 234 `MIXED`-vs-`NEUTRAL/BULLISH/BEARISH` gaps, and
   none of that disagreement is represented anywhere in a trading input.
2. **Forward calendar.** 30 future USD/EUR/GBP high-impact events including the 2026-09-16 18:00Z FOMC decision +
   18:30Z press conference, with a working PRE/POST window gate — zero trading-path consumers (§9). The engine is
   one of the few scalpers that *knows* FOMC is 2.5 days out and does nothing with it.
3. **Source credibility/tier** in influence terms (§2.1) — priority only nudges a local score by ≤0.25, and a TIER_3
   aggregator out-weights TIER_1 officialdom in the context window.
4. **The `blocked` verdict** — the single strongest thing the news gate computes is discarded (§9).
5. **`news_trade_links` / `news_post_event` / `news_event_links`** — all 0 rows: no attribution, no post-event
   outcome scoring, so news quality is unmeasured and cannot be tuned. `news/drift.py` PSI monitor has no production
   caller, so the (large) news-vs-train distribution shift in §8 is not alarmed on.

---

## 12. Fail-safe behavior on source disagreement

* **Designed:** `analysis/consensus.compute_consensus` — tier-weighted direction, `AGREEMENT_HIGH 0.66` /
  `CONFLICT_HIGH 0.34`, disagreement → `CONFLICTED`, confidence diluted, "source count alone is never certainty".
* **Actual (VERIFIED):** `compute_consensus`/`combine_consensus` have **no production callers** (only the
  `analysis/__init__` re-export and read-only API `get_consensus`), and `news_consensus` holds **0 rows**. Disagreement
  is therefore never evaluated per event.
* What remains in the live path is a crude in-window aggregate: MIXED/CONFLICTED rows add `conflict_sum`; state
  `CONFLICTED` only if `conflict_sum/weights > 0.1`. Observed conflict score 0.045 → the CONFLICTED branch is
  effectively dormant, and the `Junk-NEUTRAL guard` deliberately excludes low-signal NEUTRAL rows from the
  bull/bear denominator (so silent disagreement is dropped, not escalated).
* **Local-vs-AI disagreement:** local wins silently. Two independent reasons (both VERIFIED): (a)
  `pipeline._merge_external` — the only code that would overwrite direction/confidence/relevance from an API answer —
  is gated on `DefaultExternalAnalyzer.available()`, which needs `config.analysis.{api_base_url,model,api_key}`;
  `configs/base.yaml` ships all three empty, so the pipeline's own API path is inert. (b) the real LLM traffic goes
  through `news/pro_auto.py`, which writes `news_ai_analysis` and then calls `pipeline.analyze_article` anyway
  ("Deterministic analysis persistence — always accurate variables"). Result: 25,070/25,070 `news_analysis` rows carry
  `provider='local', local_only=1` (`select ... where provider!='local' or local_only!=1` → 0), while 3,654 AI rows
  (624 `completed`, 3,030 `completed_insufficient`) sit unused by every consumer except the UI. On any API failure (401/429/timeout/malformed) the rule is "fall back to
  local, never crash", with a per-UTC-day request/token budget (`daily_request_limit 120`, `daily_token_limit 1.5M`).
  That is the correct direction for a fail-safe (never let an LLM outage change trading behavior, never let it
  fabricate), but it means there is **no cross-source validation stage at all**: one feed's headline, keyword-matched
  and unchallenged, is sufficient to latch the system into `HIGH_IMPACT`.
* **Missing-data fail-safe is good:** `available=False`/`stale=True` → `IGNORE` (no fake influence); first tick before
  any worker cycle → `available=False`; a news failure inside the tick path is caught → `_last_news_gate=None` and
  trading continues; news construction failure disables the subsystem and logs. `stale_mode="observe"` for the
  calendar means a dead calendar provider also silently degrades to "no restriction" rather than fail-closed — safe
  for uptime, and a deliberate choice that is currently harmless because the calendar gate is advisory-only.

---

## 13. Ranked recommendations

1. **Fix RSS timestamp parsing** (`sources/base._parse_dt`, `deduplicator._as_dt`): use
   `email.utils.parsedate_to_datetime` / feedparser's `published_parsed` struct_time before falling back, and make
   the fallback *explicit* (store `published_at=None` + `published_at_source='INGEST_TIME'`) instead of silently
   stamping wall-clock. Everything in §4/§5/§6/§8 quality depends on this one function.
2. **Decide what news is allowed to do, then wire exactly that.** Either consume `verdict.blocked` in
   `tick_pipeline` (with a real conversion to `NO_TRADE` + `blocked_by='NEWS_GATE'`, and an audit counter), or delete
   the flag and the debug-UI claim. Today the code says "block weak setups during high-impact windows" and the runtime
   does nothing.
3. **Reconnect the calendar gate to the trading path** (or to the regime classifier's `is_macro_news_window`, which is
   currently a hard `False`). The provider, the deterministic classifier, the window policy and the fail-safe semantics
   are all built and tested — only the call site is missing.
4. **Make the news feature block real or remove it.** Either (a) build the 70D dataset with
   `news_frame=build_news_frame_from_db(...)` (already wired in `cli/doctor.py`) so the 10 dims have variance and the
   scaler gets honest `std`, or (b) drop news to a dedicated bounded/routed input (gate-only) and stop shipping 9 of 70
   inputs as an off-distribution `±5` shock. A ±3-bounded, std-floored family with a 1000× amplification factor at
   serve time is the worst of both.
5. **Wire the PSI drift monitor** (`news/drift.news_drift_check`) into a periodic check with an alert, and add a
   hard assertion in model load: if a family's scaler `std` equals the `1e-3` floor while the live producer is enabled,
   refuse the 70D contract (or log `FEATURE_DEAD_AT_TRAIN`) instead of feeding saturated values silently.
6. **Fix the dedup so one story is one event in-window:** check `find_duplicate_title` before the hash tombstone,
   persist `title_hash → article_id` (restart-safe), and populate `evidence_sources` + `news_consensus` so the tier
   weights (`trust_weight`) finally gate a real agreement/conflict score. Also delete or re-anchor the
   `IRRELEVANT`/junk-marked rows so `auto_prune` actually removes their influence on the context window.
7. **Restore `mark_deterministic_high_impact`** by making `replace_entities` preserve `DETERMINISTIC_EVENT` rows (or
   store the tag in a dedicated column), and populate `news_event_links` so article importance and calendar importance
   become one reconciled story; prune expired `calendar_events` (90 dead `SCHEDULED` rows today).
8. **Close the loop:** populate `news_trade_links`/`news_post_event` so news alignment can be scored against realized
   outcomes; today the subsystem has no ground truth and no falsifiable quality signal.

---

## 14. Verification appendix (executed this session)

| Check | How | Result |
|---|---|---|
| 23 tables + row counts | `sqlite3 file:artifacts/news.db?mode=ro` | `calendar_events 190`, `news_articles 28,446`, `news_analysis 25,070`, `news_analyzed_hashes 22,280`, `news_junk_hashes 8,535`, `news_ai_analysis 3,654`, `news_sources 13`, consensus/event/trade/version/post-event = 0 |
| Fabricated timestamps | julianday(published)−julianday(created) < 1 s, per source + microsecond signature | 25,047/28,446 (88.1%); Treasury JSON 0% |
| `_parse_dt` RFC-822 failure | executed the adapter's function verbatim on 5 formats | 3 formats → `now(UTC)` |
| Duplicate inflation in window | normalized-title grouping over newest-300 analyses | 300 rows / 187 titles / 113 extra / 0 cross-source |
| Context replay + gate verdict | re-implemented `context.build` arithmetic + called the real `NewsGate` | live: `state=HIGH_IMPACT, rel 1.0/1.0, conf .55, fresh .82, active 21` → `CAUTION, adj 0.0, blocked=True (conf<0.6)`; stale replay → `IGNORE` |
| Calendar gate verdicts | `evaluate_event_window` on envelope rebuilt from `calendar_events` | CLEAR now; PRE/POST_EVENT around CPI 09-11 and FOMC 09-16; observe vs block_high_impact both demonstrated |
| Champion scaler news slots | `numpy.load(...70d_liquidity/model.scaler.npz)` | `std==0.001` at idx 9 and 50..59; mean 0.0; 70 dims |
| Datasets' news block | `polars` read of every 70D dataset | constant 0.0 in all `scalp_v3` builds; `news_status='FEATURE_DISABLED'` (99,946/99,946) in `t70d_f1_full_m1`; real block only in M5 `ds_9704ec379e18a08b` |
| Projection + saturation | real `vectorize_news_context` → `build_news_10` → champion scaler | 9/10 slots at ±5; raw 0.005 already saturates |
| Serve-time influence | 24,987 real dataset rows, swap news family, run the champion | flip 46.18%, entry rate 0.888%→0.000%, counterfactual honest scaler 2.50%/max\|Δp\|0.0011 |
| Zero-gradient corroboration | `input_projection` vs Kaiming bound 1/√70 in `model.pt.pre_direct_bak` | news 0/1280 outside bound (max 0.119457) vs base 164/6400 |
| Serving artifact identity | `sha256(model.pt)[:16]` vs `model_runtime_health.payload` | `bb1f0afe30f746da` == champion hash; `dimension=70`, `feature_schema=scalp_v3` in `logs/info/2026/09/2026-09-13.log` |
| Untrained champion (context for §8 caveat) | fresh `ScalpNet(70,3)` @ seed 42 vs champion state_dict | 31/31 tensors byte-equal (reproduces `CHECK-MDL-02`) |
| Gate enforcement audit | `grep -rn "\.blocked"`, `grep news` in `execution/`, `risk/`; `is_macro_news_window` call sites | no consumers; `False` hard-coded ×2; 356 guardian blocks all `HIGH_SPREAD_CHOP` |
| Sizing path | `load_bound_calibrator(SERVING_CALIBRATION_PATH)` | artifact missing → `NOT_CALIBRATED` → multiplier 1.0 |
| Forensics baseline | `artifacts/forensics/forensic_health_snapshot.json` (2026-09-14T01:00Z) | News DEGRADED (fxstreet 403×27), worker PASS (4,158 cycles), CHECK-NWS-01 consensus-0 warning path active |
