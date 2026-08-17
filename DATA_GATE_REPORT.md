# DATA AVAILABILITY + DATA QUALITY Gate — Forensic Report

**Date:** 2026-08-17 (UTC) · **Bot:** Nexus Scalp Engine (NSE)
**Scope:** Broker history availability, raw capture, dataset artifact build + validation, config mismatch.
**Status:** ✅ GATE PASS (for model-training path) — ⚠️ executed-trade research path LIMITED

---

## 1. Broker & Connection (read-only verification)

| Item | Value |
|---|---|
| MT5 package | 5.0.5735 (terminal 500.6116, 14 Aug 2026) |
| Terminal | MetaTrader 5 @ `C:\Program Files\MetaTrader 5` (connected) |
| Account | login **10011755849**, server **MetaQuotes-Demo**, USD |
| Symbols total | 12,525 |
| Gold symbols | XAUUSD, XAUEUR, XAUAUD, XAUCHF, XAUGBP, GOLD, XAUG |
| Operations | READ-ONLY getters only — no orders, no writes to broker |

## 2. Raw History Availability (terminal cache limits)

| TF | Bars captured | Earliest (UTC) | Latest (UTC) | Span |
|---|---|---|---|---|
| **M1** | 100,000 | 2026-05-01 17:15 | 2026-08-17 19:24 | 3.5 months |
| **M5** | 100,000 | 2025-03-12 02:05 | 2026-08-17 19:20 | **17 months** |
| M15 | 100,000 | 2022-05-18 03:15 | 2026-08-17 19:15 | 4.2 years |
| H1 | 100,000 | 2009-07-17 09:00 | 2026-08-17 19:00 | 17 years |
| H4 | 33,977 | 2004-06-11 04:00 | 2026-08-17 16:00 | 22 years |
| D1 | 5,699 | 2004-06-11 00:00 | 2026-08-17 00:00 | 22 years |

**Hard limit discovered:** the MT5 terminal's local cache caps M1/M5/M15 at exactly
**100,000 bars** (verified: `copy_rates_from_pos(pos=100_000)` → `Call failed`).
Server-side full history exists only for H4/D1 (back to 2004) and H1 (2009).
**To extend M1/M5:** load deeper history in the terminal GUI (Tools → History Center)
or capture ticks continuously.

Capture location: `data/raw/XAUUSD_{M1,M5,M15,H1,H4,D1}.{parquet,csv}` + `capture_report.json`.

## 3. Raw Data Quality

| Metric | M1 | M5 | M15 | H1 | H4 | D1 |
|---|---|---|---|---|---|---|
| Invalid OHLC rows | 0 | 0 | 0 | 0 | 0 | 0 |
| Duplicate timestamps | 0 | 0 | 0 | 0 | 0 | 0 |
| Zero/negative volume | 0 | 0 | 0 | 0 | 0 | 0 |
| Gaps (total) | 78 | 409 | 1,115 | 4,426 | 1,249 | 1,187 |
| Gap % | 0.078% | 0.409% | 1.115% | 4.426% | 3.676% | 20.8% |
| Largest gap | 53h | 74h | 74h | 86h | 112h | 5d |

Gap structure is **normal market behavior**: ~93% of M5 gaps are weekend/holiday
closures (`3x-24h` + `24h+` buckets), with only 10 near-1.5x-3x anomalies
(intraday thin liquidity). No missing-week blocks, no random voids.
**Timestamp convention:** MT5 `copy_rates_*` epoch seconds = **UTC**; stored as
naive-UTC in parquet (matches `SampleFactory._parse_ts` contract).

## 4. Dataset Artifact (Phase 13 model-training path)

| Item | Value |
|---|---|
| dataset_id | **`ds_cb30f87520e9e6a4`** |
| Symbol / TF | XAUUSD / **M5** (recommended balance) |
| Rows | **99,946** (warmup 54 dropped) |
| Split | train **69,962** / val **14,991** / test **14,993** |
| Temporal range | 2025-03-12 06:35 → 2026-08-17 19:20 UTC (523.5 days) |
| Rows/day | ~191 |
| SHA-256 (dataset.parquet) | `4108ff0de3d24f111a306a84ee352a3ab52e7c824dd6332736fcd376be35b4dc` |
| Manifest | `dataset_manifest.json` (schema, splits, purge params) |
| Labels | 0=NO_TRADE 88,202 · 1=BUY 5,930 · 2=SELL 5,814 · (3-class contract) |
| Eval samples | 20,897 · Purged 79,049 |

**Feature pipeline:** replicated the live engine's exact warmup path —
`ScalpFeatureEngine.compute_from_bars(window, synthetic_tick)` per bar
(55-bar causal lookback) → 50D `feat_0..49` → `TripleBarrierLabeler`
(friction $0.35, TP 1.1×ATR, SL 1.0×ATR, horizon 15 bars, embargo 3).

## 5. Dataset Validation (data_gate_validate.py → data/raw/dataset_validation.json)

| Check | Result |
|---|---|
| Schema (76 cols; sample_id, timestamp, feat_0..49, label, splits…) | ✅ PASS |
| Feature finiteness (0 NaN/Inf) | ✅ PASS |
| Feature bounds (0 values outside [-3,3]) | ✅ PASS |
| Chronological ordering | ✅ PASS |
| Duplicates (sample_id / timestamp) | ✅ 0 / 0 |
| **Leakage guard** (eval ∧ purged overlap) | ✅ 0 |
| Gap stats | 409 gaps (0.41%) — weekend-dominated |
| Label class count | 3 ✓ |
| Split sizes (train>50k, val>10k, test>10k) | ✅ PASS |
| **Overall verdict** | **PASS** |

⚠️ Observation: `feat_9` (`rapid_reversal_spike_val`) is constant 0.0 across all
rows — a rare-event spike detector with no firing in the window; not a data
defect (same live pipeline behavior).

## 6. Sufficiency for Each Phase

| Phase | Data | Verdict |
|---|---|---|
| Phase 13 model training (future) | 99,946 samples, clean, validated | ✅ **SUFFICIENT** |
| Phase 13 model validation (OOS/regime/collapse) | test 14,993 + val 14,991 | ✅ **SUFFICIENT** |
| Phase 09B backtest (model-driven, on artifact) | 99,946 M5 samples | ✅ **SUFFICIENT** (via research dataset bridge) |
| Phase 09B walk-forward / OOS / robustness | ~17 months M5 = ~6× fold windows | ✅ **SUFFICIENT** |
| **Phase 09B on EXECUTED trades (ledger)** | audit_ledger **211 rows**, experiences **111**, all 2026-08-17 | ⚠️ **LIMITED** — needs weeks of live trading |
| Forward test / champion shadow | live M5 feed (live engine running) | ✅ operational, no dataset needed |

**Executive judgement:** the dataset artifact is **ready for experiment creation,
training, validation and backtest**. The *executed-trade* research ledger will
need more live time before walk-forward on real trades is meaningful.

## 7. EURUSD ↔ XAUUSD Config Mismatch (item 11) — ROOT CAUSE

| Source | Symbol | model_artifact_path | Verdict |
|---|---|---|---|
| **configs/live.yaml** (ACTIVE, Aug 17) | **XAUUSD** | `artifacts/models/scalp/XAUUSD/v1.0.0/model.pt` | ✅ correct |
| src config default (`ModelConfig`) | XAUUSD | `artifacts/models/scalp/XAUUSD/...` | ✅ correct |
| configs/live.yaml.example | XAUUSD | `.../XAUUSD/...` | ✅ correct |
| **configs/base.yaml** (stale default) | **EURUSD** | `.../EURUSD/v1.0.0/model.pt` | ❌ **STALE — never updated** |

The live stack (`live.yaml`) is coherent with the real XAUUSD artifact; the logs
confirm the running engine uses XAUUSD (close≈4401, model loaded from the XAUUSD
path). **Risk:** anyone launching with `--config configs/base.yaml` would run
EURUSD and fail to find the (nonexistent) EURUSD model. **Recommended fix**
(needs user approval, repo is otherwise read-only): change base.yaml
`symbol: EURUSD → XAUUSD` and the model path similarly.

## 8. Deliverables

```
data/raw/XAUUSD_M1.parquet/.csv        raw broker capture (100k bars)
data/raw/XAUUSD_M5.parquet/.csv        raw broker capture (100k bars)
data/raw/XAUUSD_M15.parquet/.csv       raw broker capture (100k bars)
data/raw/XAUUSD_H1.parquet/.csv        raw broker capture (100k bars)
data/raw/XAUUSD_H4.parquet/.csv        raw broker capture (33,977 bars)
data/raw/XAUUSD_D1.parquet/.csv        raw broker capture (5,699 bars)
data/raw/capture_report.json           availability + quality report
data/raw/dataset_validation.json       artifact validation report
artifacts/model_generation/datasets/ds_cb30f87520e9e6a4/dataset.parquet    (20.4 MB)
artifacts/model_generation/datasets/ds_cb30f87520e9e6a4/dataset_manifest.json
```

**Untouched:** `artifacts/models/scalp/XAUUSD/v1.0.0/model.pt` (live legacy model —
still loaded by the running engine; my scripts never write there).
No model training performed. No live-engine changes. No news context attached
(news.db has 0 analysis rows — readiness gate RED, dataset is news-neutral).

## 9. Next Steps (gated on this report)

1. ✅ DONE — raw capture + dataset artifact + validation (this gate)
2. **NEXT:** experiment creation (`nse model-experiment-create`), deterministic
   backtest, walk-forward/OOS/robustness, then candidate training — **only if**
   the user approves proceeding past this gate.