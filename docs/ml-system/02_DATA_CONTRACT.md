# 02 — Data Contract (Current State & Evidence)

> **Status:** READ-ONLY forensic classification of NSE at HEAD `d9a3a829`.
> Source code wins over documentation. Every statement is backed by exact file:line citations.

---

## 1. Feature Schema Contracts (50D vs 70D)

NSE defines two feature schemas in source code, but only ONE is active on the live trading path.

### 1.1 Active Live Contract: 50D (`scalp_v1`)
- **Schema ID:** `"scalp_v1"` (`src/nexus_scalp/features/schema.py:12`)
- **Dimension:** Exactly 50 features (`NUM_FEATURES = 50` in `src/nexus_scalp/features/scalp_features.py:164-215`)
- **Runtime Enforce:** `if NUM_FEATURES != active_dimension(): raise RuntimeError(...)` (`scalp_features.py:221-226`)
- **Live Assertion:** `_validate_50d_tensor` checks tensor shape `(1, 50)` (`application/live_engine.py:4770`)

### 1.2 Research / Candidate Contract: 70D (`scalp_v3`)
- **Schema ID:** `"scalp_v3"` (`src/nexus_scalp/features/schema_contract.py:63`)
- **Dimension:** Exactly 70 features (`DIMENSION = 70`)
- **Hash Verification:** SHA-256 canonical schema hash computed over feature definitions (`schema_contract.py:195-203`)
- **Status:** Research / Candidate only. NOT wired to live order generation.

---

## 2. Feature Geometry & Layout

### 2.1 Complete 50D Feature Specification (`scalp_v1`)

Computed by `ScalpFeatureEngine.compute_from_bars()` (`src/nexus_scalp/features/scalp_features.py:481`):

| Index Range | Feature Family | Count | Key Features / Math | Lookback Context |
|---|---|---|---|---|
| **0 – 9** | Price Action & Wick Anatomy | 10 | Return (1, 3, 5 bars), Body ratio, Upper wick ratio, Lower wick ratio, Normalized spread, Volume delta | 5 bars |
| **10 – 19** | Swing Structure & Range | 10 | Distance to 20-bar Swing High, Distance to 20-bar Swing Low, 50-bar Range position, Higher-High / Lower-Low flags, Trend bias | 50 bars |
| **20 – 29** | Volatility & Momentum | 10 | ATR-14 normalized by close, Realized volatility, RSI-14, MACD line, MACD signal, MACD histogram, Bollinger %B | 20 bars |
| **30 – 39** | ICT / SMC Signals | 10 | Fair Value Gap (FVG) size & direction, Order Block (OB) distance, Break of Structure (BOS) age, Liquidity sweep score | 30 bars |
| **40 – 49** | Multi-Timeframe & Session | 10 | M5 EMA-20 alignment, M15 EMA-50 trend, London session open flag, NY session open flag, Asian session range distance | Multi-timeframe bar cache |

### 2.2 Complete 70D Feature Specification (`scalp_v3`)

Assembled by `assemble_70d()` (`src/nexus_scalp/features/features70.py:151-223`):

| Index Range | Subsystem | Source Component | Features Description |
|---|---|---|---|
| **0 – 49** | Base 50D Features | `ScalpFeatureEngine` | Identical to `scalp_v1` base features |
| **50 – 59** | News Intelligence (10D) | `news_context_v1` fields 0..8 + 10 (`src/nexus_scalp/model_generation/models.py`) | `active_high_impact_events`, `xauusd_relevance`, `usd_relevance`, `bullish_pressure`, `bearish_pressure`, `conflict_score`, `novelty`, `freshness`, `confidence`, `news_state` |
| **60 – 69** | Liquidity Intelligence (10D) | `LiquidityEngine.compute_liquidity_features()` (`src/nexus_scalp/features/liquidity_engine.py`) | `bsl_distance_atr`, `ssl_distance_atr`, `eqh_strength`, `eql_strength`, `htf_liquidity_score`, `internal_liquidity_distance`, `external_liquidity_distance`, `liquidity_confluence`, `liquidity_sweep_state`, `post_sweep_displacement` |

---

## 3. End-to-End Normalization & Scaling

Normalization is verified end-to-end across training, artifact persistence, and live inference:

```
[Raw Historical Bars]
         ↓
  ScalpFeatureEngine.compute_from_bars()
         ↓
  Raw Features X (N, 50)
         ↓
  WalkForwardTrainer._fit_scaler(X_train) ──> persists mean & std to model.scaler.npz
         ↓
  Normalized Features: X_norm = (X_train - mean) / std
         ↓
  PyTorch DataLoader / Model Training
         ↓
  [Live Tick Pipeline]
         ↓
  InferenceService loads model.scaler.npz
         ↓
  Live Vector: (x_live - mean) / std
         ↓
  ScalpNet.forward()
```

### 3.1 Scaling Implementation Details
- **Fitted Scope:** Fitted strictly on the `train_idx` of each fold in `WalkForwardTrainer._fit_scaler` (`src/nexus_scalp/training/walk_forward_trainer.py:1816-1825`):
  ```python
  mean = X_raw.mean(axis=0)
  std = X_raw.std(axis=0)
  std = np.where(std < 1e-8, 1.0, std)  # constant columns safe
  ```
- **Zero Leakage:** Mean and std are NEVER fitted on validation or OOS splits.
- **Sidecar Artifact:** Saved as `model.scaler.npz` containing arrays `mean` and `std` (`walk_forward_trainer.py:2283-2313`).
- **Live Loading:** `InferenceService` loads `model.scaler.npz` alongside `model.pt` (`application/live/inference.py`).
- **Live Transform:** Prior to inference, raw features are transformed using the persisted scaler. Constant features with zero standard deviation are scaled with unit variance (std=1.0).

---

## 4. Dataset Creation & Geometry

### 4.1 Input Shapes
- **Production Training Shape:** $(N, 50)$ 2D matrix of floating-point features.
- **Sequence Candidate Shape:** $(N, 32, 50)$ 3D tensor where sequence length $L=32$ (defined in `SEQUENCE_CONTRACT`, `src/nexus_scalp/model_generation/sequence.py:93`).
- **Live Inference Input:** $(1, 50)$ 2D single-tick feature vector.

### 4.2 Label Generation (Triple Barrier Method)
- Implemented in `TripleBarrierLabeler` (`src/nexus_scalp/labeling/triple_barrier.py:34-180`).
- Parameters:
  * `take_profit_atr_mult`: 1.1 × ATR
  * `stop_loss_atr_mult`: 1.0 × ATR
  * `max_holding_bars`: 15 bars (time barrier)
  * `friction_usd`: $0.35 per trade
- Label Mapping (`src/nexus_scalp/model_lifecycle/model_class_contract.py:50`):
  * `0`: `NO_TRADE` (time barrier hit without hitting TP/SL, or return below friction)
  * `1`: `BUY_MARKET` (upper profit barrier hit first)
  * `2`: `SELL_MARKET` (lower profit barrier hit first)
  * `3`: `WAIT` (Legacy dead logit, masked to $-\infty$ at inference)

### 4.3 Class Imbalance Management
- Financial market data exhibits severe `NO_TRADE` dominance (~70–80%).
- `_balance_oversample_dataset` (`walk_forward_trainer.py:2688`) oversamples BUY and SELL instances up to 85% of majority class count to prevent the neural network from collapsing into trivial abstention.

---

## 5. Purge and Embargo Semantics

To prevent temporal lookahead leakage, Walk-Forward splits enforce strict purging and embargo boundaries:

```
[ Train Fold Window ] ──[ Purge Gap: 15 bars ]── [ Embargo: 15 bars ] ── [ Validation Fold Window ]
```

- **Source Code:** `WalkForwardTrainer._split_fold_with_embargo` (`src/nexus_scalp/training/walk_forward_trainer.py:1853-1937`).
- **Purge Gap:** 15 bars discarded immediately preceding the validation window. This eliminates overlapping label horizons from triple-barrier labeling ($H=15$).
- **Embargo:** 15 bars discarded immediately following the test boundary to prevent autoregressive feature leakage.
- **DatasetFactory Boundary Purge:** `DatasetFactory` (`src/nexus_scalp/model_generation/dataset_factory.py:45`) stamps rows where the label horizon crosses a boundary as `_split = "purged"`.
- **CandidateTrainer Exclusion:** `CandidateTrainer` (`src/nexus_scalp/model_generation/training.py:191`) explicitly filters out `_split == "purged"` rows from both training and validation sets.

---

## 6. Dataset Reproducibility Verdict

| Question | Answer | Evidence |
|---|---|---|
| Are raw datasets committed to Git? | **NO** | Repository contains zero `.parquet` or `.csv` raw market files |
| Are dataset manifests committed? | **YES** | Manifest schema and table definitions exist in code |
| Can a clean checkout reproduce full training? | **NO** | Clean checkout lacks historical market candles; requires external ingest |
| How do unit tests run without data? | **Synthetic Frames** | Unit tests generate synthetic in-memory frames (`SMOKE_MIN_ROWS=3000`) |

### Missing Prerequisite for Training:
Historical M1 OHLCV candle data for XAUUSD (at least 6–12 months) must be fetched from MetaTrader 5 or imported via Parquet before running `python -m cli.train_model`.
