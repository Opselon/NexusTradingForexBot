# (a) XAUUSD_M1 Data Audit — Task #8-10 Evidence (Read-Only, No Retrain)
**Date:** 2026-09-03  | **Source:** `data/raw/XAUUSD_M1.csv` (100,000 M1 bars), `artifacts/model_generation/datasets/t70d_f1_full_m1/` (F1 build), code refs.

## Raw inventory

| Fact | Evidence |
|---|---|
| Row count | **100,000** rows (header + 100,000 data) — `wc -l` + Polars `df.height == 100_000` |
| Date range | **2026-05-01 17:15 -> 2026-08-17 19:24 UTC** (time col epoch seconds 1777655700..1786994640; `time_utc` agrees) |
| Ordering | Strictly increasing `time` — `np.diff(time) > 0` all true |
| Duplicate timestamps | **0** (`df['time'].n_unique == height`) |
| Timezone / session | `time_utc` is UTC; market session daily **22:59 -> 01:00 UTC** close/open, **Mon-Fri** only (01:00->22:59 trading day; Saturdays/Sundays absent, Fridays end 22:59). First day 2026-05-01 starts mid-day 17:15 (mid-week acquisition start). |
| OHLC validity | **No violations** — every row `high >= max(open,close)` and `low <= min(open,close)` |
| Spread (points) | min 0, mean **~8**, p95 24, max 622 (fat tail = brief stale/auction bars) |
| Tick volume | mean ~345, max ~1945; `real_volume` always 0 (MT5 M1 aggregates) |

## Gap audit

* **Gaps > 60 s: 78.** Largest **53 h** (2026-07-03 20:00 UTC -> 2026-07-06 01:00 UTC — July 4th US holiday weekend).
* Breakdown: ~59 daily close->open breaks `22:59->01:00` (**7260 s = 2h01m**, expected), 14 weekend gaps (Friday 22:59 -> Monday 01:00, ~180060 s ~2 d 1h), + holiday (2026-05-25 21:30 -> 2026-05-26 01:00, 12600 s late-May Memorial Day gap), 1 extended weekend (2026-06-19 20:01 -> 2026-06-22 01:00, 190740 s), + 2 intraday **3-min** outliers (2026-06-04 11:31->11:34, 2026-07-24 15:59->16:02). No unexplained long gaps beyond session/holiday calendar.
* Distribution: `gap_start` hours at 11/15/20/21/22, weekdays balanced (Fri 17 / Mon 15 / others 15-16) — consistent with scheduled market breaks. No single-instrument outage.

## Gap-safe semantics (block #8-10 share one contract — this file is the canonical statement)

* **Rolling features (50D):** `scalp_features.compute_from_bars(completed_bars[-55:] + synthetic_tick at bar-t close)` — last 55 completed bars ending at **t**. Window never advances into the future; gaps produce valid rolling features on each closed bar (no straddle). Probable parity test `test_bug106_10_future_bars_cannot_alter_T` (0 diffs) pins this.
* **HTF / Liquidity (60..69):** `liquidity_engine.compute_liquidity_features(bars, decision_at=t, ...)` pools pass `t <= decision_at` causality; HTF ATR only from `bars <= decision`. The incremental 70D builder mirrors these lifecycle advances. Verified via `test_liq28_*_invariance`.
* **Sequence builder:** `SequenceBuilder(seq_len=L, max_gap_us=X)` marks any sequence window that straddles a `>X` inter-bar gap or a symbol/timeframe boundary as `valid=False` (never padded with foreign data). Gap-safe is the SELECTOR, not the data fix: training & OOS must consume ONLY `valid` rows (the F2/F2b harness already does). New regression `test_gap_safe_sequences` proves 5h gaps are trapped (gap-spanning windows `valid=False`) and single-row out-of-band rows do NOT produce false-pass.
* **Triple-Barrier labels:** `TripleBarrierLabeler`: per-bar barriers evaluated on `future_highs/lows[i+1:i+1+horizon]` only (horizon `min(15, n-1-i)`; tail beyond horizon **skipped**, no label fabricated). Simultaneous TP/SL, dual-TP, TP+SL collisions -> `NO_TRADE` (bias-free). Spread-adjusted entry (BUY ask, SELL bid) + step-dynamic future spread + friction `max($0.35, entry_spread)`. Feasibility `TP > friction` (otherwise stranded `NO_TRADE` strides). Data gaps are just regular OHLC discontinuities — the label window already handles them by horizon truncation.
* **OOS chronology:** chronological 70/15/15 split with **purge 15 + embargo 15** at fold boundaries (`_split_fold_with_embargo`: `[TRAIN][PURGE][VAL][EMBARGO]`). `SequenceBuilder` valid windows AND label evaluation are confined per-fold; regression `test_no_future_leakage` proves `features(t)` unchanged when future bars are appended and per-family causality.

## What this audit does NOT fix

* The F1 stride-2 dataset (78->26k eval rows) is the current production candidate; the stressed gap-aware logic above is verified, NOT widened into a different dataset in this read-only fix. The **34-fold retrain over full history remains the documented follow-up** before any HTF/temporal/class re-optimization (MLFix §8 F5).
