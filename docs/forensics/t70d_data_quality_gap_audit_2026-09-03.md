# 70D XAUUSD M1 Data Quality, Gap Census, Label Integrity & Training Lineage Audit Report
**Date:** 2026-09-03 | **Task ID:** MLFIX-T7 | **Auditor:** Nexus-AUDIT Subagent
**Scope:** `data/raw/XAUUSD_M1.csv`, `model_generation/sequence.py`, `labeling/triple_barrier.py`, leakage anti-suites, and model lineage governance.

---

## Part 1: Data Audit (`data/raw/XAUUSD_M1.csv`)
A dedicated automated probe (`scratch/ns_audit_data_quality.py`) executed against `data/raw/XAUUSD_M1.csv` produced the following empirical findings (persisted to `scratch/ns_audit_data_quality_out.json`):

1. **Volume & Temporal Range:**
   - Total Rows: Exactly **100,000** M1 bars.
   - Date Range: `2026-05-01 17:15:00 UTC` to `2026-08-17 19:24:00 UTC` (3.5 months).
   - Strict Chronological Ordering: `strictly_increasing = true` (0 out-of-order bars).
   - Duplicate Timestamps: **0** duplicate epoch timestamps.
   - Timezone Semantics: Epoch column is in Unix seconds; `time_utc` string parses identically with zero timezone mismatches (`epoch_vs_time_utc_mismatches = 0`).

2. **Gap Census:**
   - Total non-60s inter-bar deltas: **78 gaps** > 1 minute.
   - Largest Gap: **53.0 hours** (3,180 minutes), occurring across the weekend of July 3–6, 2026 (`2026-07-03 20:00:00` → `2026-07-06 01:00:00`).
   - Size Distribution:
     - 2–5 min: 2 gaps
     - 2h–24h: 60 gaps (daily rollover / maintenance breaks)
     - 1d–2.5d: 16 gaps (standard weekend market closures)
   - Weekend Alignment: All major gaps align with weekend market closures or verified holiday pauses.

3. **OHLC & Spread Validity:**
   - High ≥ Low: 100% compliant (`high_lt_low = 0`).
   - High/Low within Open/Close bounds: 100% compliant (`high_outside_open_close = 0`, `low_outside_open_close = 0`).
   - Negative Prices: 0.
   - Non-Finite values: 0.
   - Sane XAUUSD Price Range: Min `$4631.25`, Max `$5110.42` (within $1000–$6000 sane range, `sane_xauusd_range = true`).
   - Spread: Min `0.0`, Average `7.95` points, P95 `24.0` points, Max `622.0` points (capturing high-volatility news spikes), negative spreads = 0.

4. **Impact of Gaps & Gap-Safe Rule (`SequenceBuilder`):**
   - **Rolling Features:** `ScalpFeatureEngine` computes indicators over closed rolling windows (`-55` bars). Gaps greater than the lookback window reset historical memory, causing temporary transient indicator states unless bounded.
   - **HTF Features:** Multi-timeframe buckets (H1/H4) aggregate completed candles; weekend/holiday gaps naturally create missing historical buckets, handled by neutral fallbacks.
   - **Sequence Windows (`SequenceBuilder` in `model_generation/sequence.py`):**
     - *Verification:* `SequenceBuilder` enforces strict boundary checks: `boundary_ok` (same symbol and timeframe across all `seq_len` bars) and `gap_ok` (inter-bar delta $\le$ `max_gap_us`).
     - *Explicit Gap-Safe Rule:* **Any sequence window containing an inter-bar gap exceeding `max_gap_us` (default 10 minutes in research configs) or crossing a symbol/timeframe boundary is marked `valid=False` and strictly excluded from training tensors.** No imputation, interpolation, or foreign padding is permitted.

---

## Part 2: Label Audit (`labeling/triple_barrier.py`)
Audit of `src/nexus_scalp/labeling/triple_barrier.py` (`TripleBarrierLabeler` v3.6):

1. **Parameters & Semantics:**
   - Horizon: `max_holding_bars = 15`.
   - TP / SL Multipliers: `1.1 × ATR` (Take Profit) and `1.0 × ATR` (Stop Loss).
   - Friction: `$0.35` per oz (Gold real friction baseline).
   - Spread-Awareness: Spread dynamically evaluated per bar (`max(friction_usd, entry_spread)`), applying half-spread offsets for Buy (Ask) vs Sell (Bid).
   - Barrier Precedence & Same-Bar Collisions: Simultaneous dual TP/SL spikes or conflicting hits neutralize the sample (`label_code = 0`, i.e., `NO_TRADE`), eliminating bullish/bearish bias.
   - Tail Handling: Candles near the dataset end use an adaptive tail horizon (`min(self.max_holding, n - 1 - i)`). Vertical time barrier expiration applies MAE safeguards (`max_allowed_mae_ratio = 0.75`).
   - Purge & Embargo: Fold boundaries enforce a strict purge gap (15 bars) and embargo gap (15 bars) to prevent serial correlation and label leakage across cross-validation splits.

---

## Part 3: Leakage Audit & Anti-Leak Suites
1. **Existing Anti-Leak Suites Executed:**
   - `tests/unit/test_70d_bug106_incremental_phase19.py` (10 tests passed).
   - `tests/unit/test_liquidity_engine_causality.py` (17 tests passed).
   - `tests/unit/test_70d_replay_parity_task3.py` (9 tests passed).
   - *Total: 36/36 tests PASSED cleanly in 100.05s.*
2. **Normalization & Calibration:**
   - Scalers are fitted strictly on the training partition per fold (`walk_forward_trainer.py:278`), preventing test-set distribution leakage.
   - Temperature scaling for calibration is fitted on validation logits only.

---

## Part 4: Lineage Governance & Training-Data Hard Guard
To prevent degenerate-model feedback loops (where online fine-tuning on paper/live fills of a weak model corrupts production), we implement a strict lineage classification and hard guard:

1. **Lineage Classification (`model_generation/lineage.py`):**
   - `CLEAN_HISTORICAL_LABELS`: Generated offline from verified historical price action using `TripleBarrierLabeler`.
   - `PAPER_GENERATED_LABELS`: Derived from paper execution fills.
   - `LIVE_GENERATED_LABELS`: Derived from live order executions.
   - `SYNTHETIC`: Generated via simulation.
2. **Hard Guard:**
   - Any dataset manifest or retrain record carrying `PAPER_GENERATED_LABELS` or `LIVE_GENERATED_LABELS` is flagged `production_eligible = False`.
   - `ModelLifecycleOrchestrator` and `WalkForwardTrainer` enforce an explicit governance override token (`governance_override: bool = False`). Without it, training attempts abort with a `LineageGovernanceError`.

---

## Required Regression Tests Added
- `tests/unit/test_gap_safe_sequences.py`: Verifies `SequenceBuilder` correctly drops windows spanning gaps > max_gap_us.
- `tests/unit/test_label_integrity.py`: Verifies Triple-Barrier barrier precedence, friction deduction, and purge/embargo boundaries.
- `tests/unit/test_no_future_leakage.py`: Re-verifies causal feature assembly against future-bar mutation.
- `tests/unit/test_paper_live_training_lineage.py`: Verifies lineage tagging and hard guards against paper/live label pollution.

---

## MLFix.MD Update Text (2026-09-03)
```markdown
### MLFix.MD Addendum — MLFIX-T7 (Data Quality, Gaps, Leakage & Lineage Governance)
- **Data Quality:** Probed data/raw/XAUUSD_M1.csv (100k bars, 0 dupes, 78 verified gaps, largest 53h weekend closure).
- **Gap-Safe Rule:** SequenceBuilder strictly excludes any sequence window spanning inter-bar gaps > max_gap_us (default 10m) or crossing symbol/timeframe boundaries.
- **Leakage Re-Verification:** Anti-leak suites (36 tests) fully executed and passed.
- **Lineage Governance:** Introduced strict data lineage classification (CLEAN_HISTORICAL vs PAPER vs LIVE vs SYNTHETIC) with a hard training guard requiring explicit governance overrides for production candidates derived from live/paper rolling buffers.
```
