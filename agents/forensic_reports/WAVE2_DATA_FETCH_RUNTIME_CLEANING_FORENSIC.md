# WAVE 2 — DATA FETCH RUNTIME CLEANING FORENSIC (Agent-11 follow-up)

**Branch:** `main@69033c54` · **Date:** 2026-09-05 · **Scope:** every path where user-fetched data reaches model training  
**Author:** Wave-2 forensic subagent (read-only)

## Summary verdict

**User-fetched data is NOT cleaned at RUNTIME at the fetch boundary.** Every ingress (MT5 adapter, MT5TickDataset acquisition, file upload/CLI, Web API, live-engine aggregator) either (a) only *observes* dirtiness (logs), (b) cleans *after* the dirty values have already poisoned derived features / scaler statistics, or (c) has no gate at all. A remote user feeding a poisoned bar, a duplicated timestamp, or a NaN feature vector can land a dataset whose fingerprint is “valid” and whose rows survive all the way to `WalkForwardTrainer._fit_scaler` — the scaler is fit on the dirty population and then clipping hides the symptom but keeps the distortion. For a million-user ecosystem where datasets/artifacts may be shared or re-fetched from the same broker window, this is a systemic data-trust failure.

---

## 1. FETCH PATHS (every entry where external data enters)

| # | Fetch entry | Code | Online/offline | What it fetches | Training consumer(s) |
|---|-------------|------|----------------|-----------------|----------------------|
| **F1** | **MT5 adapter — `get_rate_history`** | `src/nexus_scalp/adapters/mt5/mt5_adapter.py:686` → `copy_rates_range` / `copy_rates_from_pos` | ONLINE (requires `HAS_NATIVE_MT5 && _connected`) | M1–D1 rate bars (`RateBarSnapshot`) | LiveEngine warmup/reseed (`live_engine.py:3503,3608`), `get_historical_bars` (`mt5_adapter.py:905`), Web `GET /api/chart/history` (`web/server.py:1899`), MT5TickDataset acquire_bars |
| **F2** | **MT5 adapter — `get_tick_history`** | `mt5_adapter.py:759` → `copy_ticks_range` / `copy_ticks_from` | ONLINE | ticks (`TickHistorySnapshot`) | MT5TickDataset acquire_ticks, diagnostics |
| **F3** | **MT5 adapter — `get_historical_bars` (legacy wrapper)** | `mt5_adapter.py:905` → delegates to F1 → `BarData` | ONLINE | `BarData` bars | `LiveEngine._cold_start_warmup`, `_resync_from_broker`, H1/H4 warmup, chart reseed |
| **F4** | **MT5TickDataset — `acquire_ticks`** | `research/mt5_tick_dataset.py:235` (`chunk_minutes`, `get_tick_history`) | ONLINE → writes `artifacts/datasets/replay/<id>.parquet` + `.meta.json` | tick records (chunked) | `dataset.load()` / `event_source()` → replay engine / offline research |
| **F5** | **MT5TickDataset — `acquire_bars`** | `research/mt5_tick_dataset.py:411` (`get_rate_history`) | ONLINE → same cache | bar records | same as F4 (bar event source) |
| **F6** | **File / CLI — `model-dataset-build`** | `cli/doctor.py:1129` (`model_dataset_build`) | OFFLINE (file) | parquet/csv with `feat_* + atr` columns | `DatasetFactory.build(df, ...)` → artifact store parquet + manifest |
| **F7** | **File / API — direct `DatasetFactory.build` / `SampleFactory.build_samples` / `three_model.build_feature_frame`** | `model_generation/dataset_factory.py:122`, `sample_factory.py:148`, `three_model.py:97` | OFFLINE (file / in-memory df) | already-featurized frame OR raw bars via `compute_70d_frame_fast` | `WalkForwardTrainer.train_and_validate`, `ExperimentFactory.train_experiment`, `three_model.train_variant`, `model_lab/dataset_lab.build_research_frame` |
| **F8** | **Web API — `GET /api/chart/history`** | `web/server.py:1858` | ONLINE (adapter) with ENGINE_STATE fallback | rate bars (same as F1) | Reseeds `LiveEngine.aggregator` + `ServerState` (`server.py:1920`+ reseeds aggregator) → rolling feature buffer |
| **F9** | **Web API — `POST /api/models/train` / worker** | `web/model_governance_routes.py:258` (`trigger_model_training`) → `orchestrator.build_training_dataset` → `WalkForwardTrainer` | OFFLINE (ledger-derived) but triggered remotely | ledger-derived `ResearchDataset` (experience ledger) | WalkForwardTrainer / model_lab trainer |
| **F10** | **Live-engine rolling buffer — aggregator + feature engine** | `live_engine.py:4963` (`_build_retrain_record` on every new closed bar), `market_data/bar_aggregator.py:139` (`reseed`) | ONLINE (every closed M1 bar) | `FeatureVector` → `feat_*` + `atr_m1` + bar OHLC into `_rolling_feature_records` | `trainer.fine_tune_online` (`live_engine.py:3724,5809`) |

Notes:
- `src/nexus_scalp/research/data acquisition` does **not** exist as a directory — the acquisition surface is F4/F5 (`mt5_tick_dataset.py`) per AGENT-14.
- F6/F7 are the dominant *training* ingresses in production: even broker-sourced bars are converted to `feat_* + atr_m1` via `compute_70d_frame_fast` before they reach `DatasetFactory`/`WalkForwardTrainer`. So the broker path still funnels through the feature gate, but after the raw OHLC has already been used.

---

## 2. CLEANING POINTS (where cleaning / normalization / dedupe / outlier handling actually lives)

### 2.1 At the fetch boundary (runtime, inline with the fetch) — ALMOST NONE

| Location | What it does | File:line | Drops / fixes? | Verdict |
|----------|--------------|-----------|----------------|---------|
| `validate_ohlc_bars` | counts duplicate/descending timestamps, non-finite OHLC, high/low violations, negative volume | `adapters/mt5/providers.py:777` | **Counts only; returns `report`**. Caller `get_rate_history:746` only `logger.warning` when `invalid>0`. Bars are returned **unfiltered**. | **Observe-only — NOT a cleaning gate** |
| `build_rate_bar_snapshot` / `build_tick_history_snapshot` | maps numpy record → typed snapshot, `float(row[name])` | `providers.py:728,753` | On exception: leaves field `None`; no clipping, no dedupe | Pass-through |
| MT5TickDataset containment | drops rows whose `time_utc` outside `[start,end)` and counts `out_of_window`; skips `None` tick fields but writes `_incomplete` row with `0.0` | `mt5_tick_dataset.py:316,322,471` | **Drops timebase-shifted rows (BUG-188 defense) — GOOD**; but fills missing `bid/ask` with `0.0` instead of dropping (creates a zero-price tick that can enter training via event_source) | Partial |
| MT5TickDataset symbol validation | rejects `../`, `:` etc. via regex `^[A-Za-z0-9_.-]{1,64}$` | `mt5_tick_dataset.py:84,116` | Raises `DatasetIdentityError` | **Real gate — path safety only** |
| MT5TickDataset integrity on `load()` | recomputes `dataset_fingerprint` and checks `manifest.records` count | `mt5_tick_dataset.py:598,607` | **Raises `DatasetCorruptionError` on tamper/swap/append/mutation — GOOD** but fingerprint is of the *dirty* content, so a dirty-but-unmutated dataset still passes | Integrity, not cleaning |
| `BarAggregator.reseed` dedupe | dedupes by `timestamp` (dict, last wins), filters `is_complete`, sorts ascending | `market_data/bar_aggregator.py:139-150` | Drops duplicate timestamps, sorts | **Real runtime dedupe — but ONLY for LiveEngine’s in-memory bars**; not shared with F4/F5/F6/F7 |

### 2.2 After fetch, during feature generation (still “runtime” for LiveEngine, but post-poison)

| Location | What it does | File:line | Verdict |
|----------|--------------|-----------|---------|
| `ScalpFeatureEngine.compute_from_bars` tail + `_cold_start_vector` | needs ≥55 bars else cold-start; computes 50D | `features/scalp_features.py:538,589` | Not cleaning |
| `FeatureVector.to_tensor_input` loop | `if isnan/isinf → 0.0 else clip [-3,3]`; asserts 50 elems | `features/scalp_features.py:465` | **Sanitizes final 50D vector only**; raw OHLC/high-low spike already consumed by `compute_from_bars` into ATR, swing distances, compression, etc. |
| `LiveEngine._validate_50d_tensor` | `len != FEATURE_DIM → raise`; per-element `float()→clip[-3,3]`, `non-finite→0` with `logger.warning` | `application/live_engine.py:6574` | Same: sanitizes final tensor, not source OHLC |
| `ScalpFeatureEngine.validate_and_fallback` | `if isnan/isinf(atr_m1) → 1.50` | `features/scalp_features.py:509` | Single-field patch |
| `ScalpFeatureEngine._compute_ema`, atr calc | divides by `safe_atr = max(atr,0.20)` | `features/scalp_features.py:562` | Lower-bounds ATR, no upper bound |
| `compute_70d_frame_fast` | builds 70D (`x50` + `news10` + `liq10`), `clamp_neutral_family` on news/liq (e.g. `liq10` via `_clip3`) | `model_generation/schema_v2_incremental.py:559,680,710` | **Clips liquidity/news to [-3,3]/families**; base 50D `x50` is already clipped by the engine. No OHLC clipping before feature calc |
| `liquidity_engine _clip3` | `max(-3,min(3,v))` — the *only* clamp for liquidity family | `features/liquidity_engine.py:350` | Final-value clip |
| `LiveEngine._build_retrain_record` | validates `base50` len, assembles `feat_0..feat_n-1` (news via canonical `build_news_10`, liquidity via `gov.last_snapshot` only when `causal==VALID` else **REFUSES** (`return None`), logs `FEATURE_CONTRACT_MISMATCH` if `len(rec)!=dim` | `live_engine.py:298,372` | **News 10D: correct — never fabricates.** **Liquidity 10D: correct — refuses when not VALID (INV-009).** **BUT** OHLC fields (`close/high/low/open/spread/atr_m1`) are copied verbatim (`rec.update(... 398)`) without outlier gate; `base50` validation does not check price reasonableness |

### 2.3 At labeling time (post-feature, pre-training)

| Location | What it does | File:line | Verdict |
|----------|--------------|-----------|---------|
| `TripleBarrierLabeler.label_dataframe` | skips `nan` or `atr <=0.20`, skips `tp_dist<=friction`, handles horizon-end, dual-TP spike → NO_TRADE | `labeling/triple_barrier.py:99,116` | **Drops low-quality ATR/ friction-infeasible rows — good**; does NOT validate OHLC spike, does NOT dedupe timestamps |

### 2.4 At training ingress (last line before weights move)

| Location | What it does | File:line | Verdict |
|----------|--------------|-----------|---------|
| `WalkForwardTrainer._validate_training_frame` | checks `feature_cols` ⊆ `df.columns`, `label` exists, labels ∈ `{NO_TRADE,BUY,SELL}∪{0,1,2}` | `training/walk_forward_trainer.py:1266` | Schema-presence only |
| `_filter_trainable_rows` | `label_evaluated` and `~is_purged` | `walk_forward_trainer.py:1291` | Removes purge/eval-flagged rows only |
| `_extract_X_y` | `X_raw = df.select(...).to_numpy().astype(float32)` → `nan_to_num(nan=0, +inf=1, -inf=-1)`; label mapping int vs str | `walk_forward_trainer.py:1305` | **Last-chance numeric sanitization** — but after `fit_scaler`’s *population* was already selected, so distortion stays in the scaler |
| `_fit_scaler` | `mean=np.mean(X_raw,axis=0)`, `std=np.std(...)` clipped to `≥1e-3` | `walk_forward_trainer.py:1322` | No outlier exclusion; a single `-100`/`+100` feature row shifts mean/std for the whole fold |
| `_transform_features` | `(X-mean)/std` → `clip[-5,5]` | `walk_forward_trainer.py:1331` | Hides poisoned tails but preserves poisoned centering/scale |
| `_split_fold_with_embargo` + `_apply_split` (DatasetFactory) | chronological split, purge `purge_gap=15`, embargo `embargo_bars` | `walk_forward_trainer.py:1355`, `dataset_factory.py:280` | Leakage defense — **not** a data-quality gate |
| `SequenceBuilder.build` | `max_gap_us` gap check (default 600s via `temporal_contract.CANONICAL_MAX_GAP_US`), boundary check (same symbol/timeframe) | `model_generation/sequence.py:243` | **Real sequence gate** — marks window `valid=False` on gap/boundary violation, never pads. But input is already `DatasetFactory` output, so upstream OHLC dirt is baked in |

### 2.5 Offline-only gates (never enforced at fetch time)

| Gate | File | Notes |
|------|------|-------|
| `model_lab/dataset_lab.integrity_report` | `model_lab/dataset_lab.py:64` | Counts `duplicate_timestamps`, `non_finite_cells`, `chronologically_ordered` — **report only, not enforced** |
| `model_generation/benchmark.py` |  | Runs on already-labeled frames — never cleans fetch input |
| `tests/unit/test_a2_data_lineage_bounded.py` (BUG-243 companion) |  | Validates healthy lineage — not a runtime gate |

**Net:** Every path that touches raw OHLC → features → scaler does a *feature-value clip* (`[-3,3]` / `[-5,5]`) and a *numeric* `nan_to_num`, but **zero** paths do fetch-time OHLC sanity (price spike, high<low, duplicate timestamp, negative volume, flat-line bar) before the OHLC is consumed by ATR/EMA/swing/liquidity computations.

---

## 3. GAPS — defects where dirty data can reach training (remote-user reachable)

### GAP-1 — P0 | MT5 fetch path has an observation-only OHLC validator; invalid bars are cached and trained on
- **Evidence:** `providers.py:777` (`validate_ohlc_bars` counts but never filters) → `mt5_adapter.py:746` only warns → `mt5_tick_dataset.py:477` appends `open/high/low/close` with `float(s.open or 0.0)` even when `high<low` or non-finite; zero check is `if ts is None or close is None: continue` only. A bar with `high=0`, `low=9999`, `close=NaN` (coercible to float?) or `high < max(open,close,low)` is stored. Then `meta.complete=True` (when not caught by whole-window-zero logic) and `dataset_fingerprint` covers the dirty rows, so `load()` at `mt5_tick_dataset.py:598` **passes**.
- **Remote reach:** any user calling `acquire_bars(symbol=XAUUSD,start,end)` or relying on `/api/chart/history` → `aggregator.reseed` (which dedupes but still applies `is_complete` only).
- **Training reach:** `three_model.build_feature_frame` → `compute_70d_frame_fast` consumes `high/low/close` directly for `fv = engine.compute_from_bars(...)` before any clip. A single `high=1e9` drives `atr_m1` to `~1e9 /14`, so `safe_atr` ~1e9; every subsequent feature normalized by ATR collapses to ~0; scaler `mean/std` are dominated by the spike window.
- **Severity:** **P0 — silent data poisoning with valid provenance**

### GAP-2 — P0 | No deduplication at the acquisition / dataset-factory boundary
- **Evidence:** Dedupe exists only in `bar_aggregator.py:141` (LiveEngine). `mt5_tick_dataset.py:acquire_bars` does `for s in snaps: records.append({...})` with no dedup. `dataset_factory.py:_apply_split` sorts but never dedupes. `sample_factory.py:174` iterates `rows = labeled.to_dicts()` verbatim. `walk_forward_trainer.py:_extract_X_y` never dedupes. `compute_70d_frame_fast:578` sorts by `time` but does `continue` only on unparsable ts, not duplicates.
- **Exploit:** broker `copy_rates_range` overlapping chunk (or `acquire_bars` legacy wall-clock identity collision) can emit the same timestamp twice. Or a user-supplied parquet can contain 10k duplicate rows with the same timestamp but alternating labels — doubles effective weight without `time_decay` knowing.
- **Severity:** **P0 — horizon purge accounting breaks; sample counts lie; leakage/overweight**

### GAP-3 — P1 | No runtime outlier / spike gate on OHLC before it enters feature math
- **Evidence:** No check anywhere for: `high < low`, `close` outside `[low,high]`, single-bar `range > K*ATR`, `tick_volume` negative/huge, `spread` negative, flat stale bar repeating. The only volume check is `validate_ohlc_bars:821` (`tick_volume<0`) counted not dropped. `validate_and_fallback:509` only patches `atr_m1`.
- **Training reach:** Live fine-tune (`live_engine.py:398`) stores `close/high/low/atr_m1/spread` verbatim into `_rolling_feature_records` even when they are e.g. `high=0` after a bad bridge row. `online_labeler.label_dataframe` then evaluates barriers on that row as if it were real price — can mint a false `BUY/SELL` label that survives `_filter_trainable_rows` (it only looks at `is_purged`/`label_evaluated`, not price sanity).
- **Severity:** **P1 — single poisoned bar poisons 55-bar window + label**

### GAP-4 — P0 | File/API ingress (`model-dataset-build`, direct `DatasetFactory.build`) has zero runtime cleaning
- **Evidence:** `cli/doctor.py:1180` reads `pl.read_parquet(csv)` then at `1191` only checks that `feat_*` columns *exist*, not that they are finite, clipped, or deduped. Missing-ATR error messages are the only hard gate. `dataset_factory.py:122` `build(df, ...)` does `samples = sample_factory.build_samples(df,...)` → `sample_factory.py:192` reads `feat_*` via `float(row.get(c,0.0))` — NaN/Inf accepted, then `195` length check only. No finiteness/clip/dedupe/drop. `walk_forward_trainer.py:1305` `nan_to_num` + `1331` clip are the only later sanitization (post-scaler-fit).
- **Remote reach:** for a million-user ecosystem, “bring your own parquet” is the intended multi-tenant story (CLI `--bars`, API dataset upload, shared artifact store). A remote user can craft `feat_37 = 1e9` and it reaches `_fit_scaler` as 1e9 before any clip.
- **Severity:** **P0 — attacker-controlled feature vector bypasses all fetch-time gates**

### GAP-5 — P1 | Missing-field rows in `acquire_ticks` are zero-filled with `bid=0.0/ask=0.0` and tagged `_incomplete=True`, then the tag is *stripped* before caching
- **Evidence:** `mt5_tick_dataset.py:326` `records.append({..., bid: 0.0, ask:0.0, _incomplete:True})` when `ts/bid/ask is None`. Then `_write_cache:560` does `clean = [{k:v for k,v in r.items() if not k.startswith("_")} for r in records]` and `pl.DataFrame(clean)` — the `_incomplete` marker is removed, but `bid=0.0` stays. `meta.incomplete` counts them but `complete` is still derived only from `chunk_rows_returned` (`388`). So a tick with no price becomes a `0.0` price row in the cached artifact with no per-row marker left, `load()` fingerprint covers it, and `event_source()` will emit a tick at `0.0`.
- **Severity:** **P1 — silent zero-price ticks enter replay/training; `_incomplete` stripping destroys auditability**

### GAP-6 — P1 | `/api/chart/history` re-seed propagates dirty broker bars without re-validating
- **Evidence:** `web/server.py:1899` `rate_bars = engine.adapter.get_rate_history(...)` → loop at `1903` only skips `r.time_utc is None`; then at `1920` path the same bars feed `engine.aggregator.reseed` (`live_engine.py:3525,3612`). `reseed` does dedup+suffix sort but no OHLC sanity. `BarData` is constructed with `float(row[open/high/...])` even if `high < low`.
- **Severity:** **P1 — every browser refresh can re-inject a bad bar into the live feature buffer**

### GAP-7 — P1 | Live online-labeling stores outlier OHLC verbatim and labels it
- **Evidence:** `live_engine.py:398` `rec.update(close=bar.close, high=bar.high, low=bar.low, ...)` unconditionally. No `high>=low` or `close∈[low,high]` check before `online_labeler.label_dataframe(df_hist)` at `3712`. Labeler at `labeling/triple_barrier.py:99` only guards `atr`, not price.
- **Severity:** **P1 — poisoned live label enters `fine_tune_online`’s `_extract_X_y` → scaler → weights**

### GAP-8 — P2 | `nan_to_num` / clip ordering hides distortion instead of rejecting it
- **Evidence:** `walk_forward_trainer.py:1306` `nan_to_num(nan=0, posinf=1, neginf=-1)` then `1322` fit scaler on that population, then `1331` clip to `[-5,5]`. A feature that was `inf` becomes `1.0` *before* the scaler sees it, shifting the mean. Clipping after scaling hides the tail but the mean/std remain poisoned for the whole fold. No per-feature outlier Winsorization or per-column non-finite rate threshold (e.g. “drop column if >1% non-finite”).
- **Severity:** **P2 — availability-preserving but model-quality destructive**

### GAP-9 — P1 | Timestamp monotonicity / gap exploited across the file path but only `SequenceBuilder` enforces it, too late
- **Evidence:** `model_generation/sequence.py:244` enforces `max_gap_us` (10 min) via `valid=False`. But `DatasetFactory` and the 2D `WalkForwardTrainer` path never check `max_gap_us`, and `live_engine.py`’s rolling buffer path does not enforce it before labeling. A 6-hour gap (e.g. weekend) between two consecutive rows yields a synthetic triple-barrier window that spans the weekend as if it were contiguous M1 — invalid holding horizon that still labels.
- **Severity:** **P1 — gap-contaminated labels for the gap-crossing window**

### GAP-10 — P2 | No ecosystem-wide “fetch-time manifest” proving which runtime gates ran
- **Evidence:** `mt5_tick_dataset.py:_base_meta` records `fingerprint`, `complete`, `out_of_window`, `chunk_rows_returned` but not `dropped_invalid_ohlc`, `dropped_duplicates`, `winsorized_outliers`, or `scaler_fit_excluded_rows`. `DatasetFactory` manifest (`model_generation/models.py:DatasetManifest`) records `purge/embargo` but not cleaning stats. So two users fetching the same window via different code versions cannot prove they cleaned identically.
- **Severity:** **P2 — evidence/observability gap, not a direct poison but blocks audit at scale**

---

## 4. ECOSYSTEM RISKS (million-user scale)

1. **Cross-tenant artifact poisoning.** If any cache artifact (`artifacts/datasets/replay/*.parquet`) is distributed (image, snapshot, or shared S3-style store), every tenant that `load()`s it inherits the dirty rows. The fingerprint check (`mt5_tick_dataset.py:599`) attests to *byte identity*, not *cleanliness* — “valid fingerprint” is misread as “clean data”.

2. **Broker-idiographic spikes amplify fleet-wide.** Gold (XAUUSD) has frequent spread widenings (news, rollovers). A `copy_rates_range` spike (e.g. `high = ask + 50 ATR`) that is rare on one terminal becomes certain across N=1e6 terminals and will be overrepresented in any pooled dataset.

3. **Adversarial “own parquet” tenants.** File ingress (GAP-4) is fully attacker-controlled for any tenant allowed to upload data. Without a fetch-time hard gate, a malicious tenant can degrade a shared foundation model (if training ever pools tenants) or at least waste compute and generate misleading benchmark evidence.

4. **Silent scaler drift.** P1/P2 gaps cause `mean/std` drift that is *clipped away* from any alert. The `model.meta.json` / `manifest.json` at `three_model.py:346` record the final scaler dims but not a pre/post cleaning row count, so drift is invisible to the `emission_gate` and `promotion` logic.

5. **Weekend/holiday gap leakage.** For a 24/7 API, gaps > `MAX_GAP_US` (10 min) between Friday close and Sunday open are guaranteed. The 2D training path scores windows that cross the gap, while the sequence path marks them `valid=False` — two different leakage contracts in the same repo (already noted in BUG-248 context) that tenants can hit depending on which trainer they invoke.

6. **Observability spoofing.** `validate_ohlc_bars`’s `issue` list is capped at 20 and only logged at `warning`. Fleet dashboards counting `CLEAN` vs `REJECTED` cannot distinguish “0 invalid (clean)” from “200 invalid but only first 20 logged”.

---

## 5. FIX PLAN (ranked, file:line-anchored, no code edits in this forensic)

### Fix A — P0 | Hard OHLC validation gate at the two fetch boundaries (adapter + acquisition)

**Goal:** dirty bars never become cached rows.

- **A1 — `providers.py:validate_ohlc_bars` must return the *filtered* set.**
  Keep the report counting, but add `validate_ohlc_bars(bars, drop_invalid=True) → (valid_bars, report, dropped_indices)` so callers can choose to drop. Alternatively add `filter_valid_bars(bars)`. File to patch: `src/nexus_scalp/adapters/mt5/providers.py:777`.

- **A2 — `mt5_adapter.py:get_rate_history:745` must drop invalid bars (configurable `on_invalid="drop|warn"` default `drop`).**
  Current: `bars = [build...]; report = validate_ohlc_bars(bars); if invalid>0: warn; return bars` → **return `valid_bars`**. Emit structured metric `history_ohlc_dropped={count}`. Lines `mt5_adapter.py:744-757`.

- **A3 — `research/mt5_tick_dataset.py:acquire_bars:465` must validate+dedup before caching.**
  Insert between `records` build and `fp = dataset_fingerprint(...)` (≈ `mt5_tick_dataset.py:495`):
  ```python
  records, dedupe_report, invalid_report = clean_bar_records(
      records
  )  # dedupe by timestamp (keep last), drop high<low, non-finite, volume<0; outlier spike check
  meta["dropped_invalid_ohlc"] = invalid_report
  meta["deduped_timestamps"] = dedupe_report
  ```
  Do NOT fingerprint before cleaning; fingerprint must attest to *clean* bytes.

- **A4 — `acquire_ticks:312` must DROP (not zero-fill) ticks with `bid/ask is None`.**
  Change `322` branch from `bid:0.0` with `_incomplete` to `out_of_window`-style counted drop (`dropped_incomplete`). Keep `_incomplete` counting in `meta` but never cache a `0.0` price row. Do not strip the signal via `clean = ... startswith("_")` in a way that loses the count — keep it in `meta`.

### Fix B — P0 | API/file ingress hard gate before `DatasetFactory` / `WalkForwardTrainer`

**Goal:** a remote user’s parquet cannot reach the scaler without proving it is clean *now* (runtime).

- **B1 — New module `src/nexus_scalp/hygiene/dataset_hygiene.py` (or extend `hygiene/hygiene_runtime.py`):**
  `validate_feature_frame(df, schema) → (clean_df, report)` that enforces at runtime: finite check (`non_finite_cells==0` else drop or quarantine), `|feat| ≤ 3.0` or Winsorize with count, `feat dim == schema.dim`, monotonic `timestamp`, `duplicate_timestamps==0`, `tick_volume≥0`. Returns `report` that is written into `DatasetManifest` / `model.meta.json`. Call it from:
  - `cli/doctor.py:model_dataset_build:1180` immediately after `pl.read_parquet` and **fail-closed** (`typer.Exit`) when `report.invalid>0` unless `--allow-dirty` (never for production).
  - `model_generation/dataset_factory.py:122` `DatasetFactory.build(df, ...)` first lines — refuse or drop (operator-chosen) before `sample_factory.build_samples`. This is the *runtime* enforcement the brief requires (million-user fleet cannot rely on offline pre-clean).
  - `model_generation/three_model.py:97` `build_feature_frame` post-`compute_70d_frame_fast` — assert the returned 70D frame satisfies hygiene (finite, clipped, deduped) before it reaches the labeler.

- **B2 — `model_generation/sample_factory.py:192` add per-row finiteness guard:**
  After `feature_vector = [float(row.get(c,0.0))...]` add `if not all(math.isfinite(v) for v in feature_vector): continue` + counted metric, so a single NaN feature never becomes a training sample.

- **B3 — `training/walk_forward_trainer.py:1305` `_extract_X_y` add pre-scaler outlier triage:**
  Before `fit_scaler`, compute per-column z-score / IQR and either (a) Winsorize to `[-5,5]` and count, or (b) drop rows whose `|z|>6` in any column when `drop_outliers=True` (default False for backward compat but True for externally-sourced datasets). Log `scaler_triage:{dropped_pre_scaler, winsorized}` into the training run store so the Web API can surface it.

### Fix C — P1 | Live-engine fetch-time gate for the rolling retrain buffer

- **C1 — `market_data/bar_aggregator.py:139` extend `reseed` to also validate OHLC:**
  After dedupe, iterate bars and drop those `high<low` or `close` outside `[low,high]` or non-finite, with `logger.warning(event=RESEED_DROPPED_INVALID)`. This keeps `/api/chart/history` reseed (F8) from re-injecting bad bars into `LiveEngine._rolling_feature_records`.

- **C2 — `application/live_engine.py:398` validate `bar` before `rec.update`:**
  Add `if not _is_valid_bar(bar): logger.warning(RECORD_SKIPPED …); return None` (same refusal contract as the liquidity `None` path). `_is_valid_bar` checks `high>=low`, `low<=close<=high` within tolerance, `is_finite`, `tick_volume>=0`. Prevents poisoned labeler input (GAP-7).

- **C3 — `live_engine.py:3712` online-labeling gap gate:**
  Before `online_labeler.label_dataframe(df_hist)`, run `df_hist = drop_gap_crossing_rows(df_hist, max_gap_us=600_000_000)` — reuse `temporal_contract.CANONICAL_MAX_GAP_US`. Keeps weekend-gap windows from labeling.

### Fix D — P1 | Sequence vs 2D gap-contract unification at the training entry

- **D1 — `training/walk_forward_trainer.py` and `model_generation/dataset_factory.py` must both honor `CANONICAL_MAX_GAP_US`.**
  Either drop gap-crossing rows up front (unified preprocessing) or record `gap_invalid_rows` in the manifest. Today only the sequence path honors it (`sequence.py:244`). File: `model_generation/temporal_contract.py` is the SSoT — import it in the 2D path.

### Fix E — P2 | Manifest/observability (prove which gates ran for every artifact)

- **E1 — Extend every persisted manifest/meta/report with `cleaning_report`:**
  `research/mt5_tick_dataset.py:_base_meta` already has `out_of_window`, `complete`, `chunk_rows_returned`; add `dropped_invalid_ohlc`, `deduped_timestamps`, `dropped_incomplete_ticks`, `gap_invalid_rows`, `winsorized_features` (cleaned counts). `DatasetFactory` / `WalkForwardTrainer` `_save_metadata` and `model_lab/dataset_lab.py:64` `integrity_report` should write the same struct so the artifact’s fingerprint is reproducible *and* auditable. This closes GAP-10 and supports fleet dashboards (“% rows dropped by gate X”).

### Fix F — P1 | Web chart path must surface dirtiness to the UI

- **F1 — `web/server.py:1858` `get_chart_history` include `invalid`/`dropped` in response (`source`, `requested/returned`, *and* `dropped_invalid`, `duplicate_timestamps`) and in diagnostics event, so the Command Center can render the health check instead of silently serving deduped data while claiming `source=BROKER_NATIVE`.

### Sequencing

1. **P0 first:** A2+A3 (fetcher drops invalid + dedupes), B1 (file/API ingress gate) — closes the attacker-reachable holes.
2. **P1 next:** A4 (tick zero-fill), C1/C2 (live rolling buffer), D1 (gap contract) — closes single-bar poison leakage into scaler/labeler.
3. **P2 last:** B2/B3 scaler triage, E1 manifest — improves evidence and fleet observability without changing training semantics.

### Tests to add (suggested)

- `tests/unit/test_runtime_cleaning_gate.py` (offline): synthetic parquet with one `high<low`, one `NaN`, one duplicate timestamp, one `inf`, one `feat=1e6` → `DatasetFactory.build` drops; `MT5TickDataset.acquire_bars` with mocked `get_rate_history` returning those bars drops and fingerprints clean bytes; `WalkForwardTrainer._extract_X_y` + `_fit_scaler` not shifted.
- `tests/unit/test_live_buffer_cleaning.py`: `BarAggregator.reseed` with dirty bars drops invalid; `_build_retrain_record` refusal when `bar.high<bar.low`.
- Wire both into `tests/critical_suite.txt`.

---

## Appendix — evidence anchors (quick grep map for reviewers)

- Adapter fetch: `mt5_adapter.py:686` `get_rate_history`, `mt5_adapter.py:759` `get_tick_history`, `mt5_adapter.py:905` `get_historical_bars`
- Validator that doesn’t filter: `providers.py:777` `validate_ohlc_bars`, `mt5_adapter.py:746` `if report["invalid"]>0: warn` (no drop)
- Acquisition without OHLC gate/dedupe: `mt5_tick_dataset.py:477` `records.append(open/high/low...)`, `mt5_tick_dataset.py:560` `_write_cache` strips `_incomplete`
- Integrity that attests to dirty bytes: `mt5_tick_dataset.py:598` fingerprint check passes for dirty-but-consistent artifacts
- Live aggregator dedupe (only place): `market_data/bar_aggregator.py:141` `deduped`
- Feature clipping (post-poison): `features/scalp_features.py:465` `to_tensor_input` clip, `live_engine.py:6574` `_validate_50d_tensor` clip, `features/liquidity_engine.py:350` `_clip3`
- Labeler gap: `labeling/triple_barrier.py:99` `nan or atr<=0.20` only
- Training last-chance: `training/walk_forward_trainer.py:1305` `nan_to_num`, `walk_forward_trainer.py:1322` `fit_scaler` on unfiltered `X_raw`, `walk_forward_trainer.py:1331` `clip[-5,5]`
- File ingress no gate: `cli/doctor.py:1180` `pl.read_parquet`, `cli/doctor.py:1191` only existence check, `dataset_factory.py:122`, `sample_factory.py:192`, `three_model.py:97`
- Rolling live buffer: `live_engine.py:298` `_build_retrain_record`, `live_engine.py:398` `rec.update(close=...)`, `live_engine.py:3712` `online_labeler.label_dataframe`
- Gap contract split: `model_generation/sequence.py:244` `max_gap_us`, `temporal_contract.py:CANONICAL_MAX_GAP_US` (uniform gap value missing from 2D path)

---

*No code was edited in this forensic; all findings are read-only and file:line-anchored. The fix plan above is the recommended commit sequence for the downstream implementation agent(s).*
