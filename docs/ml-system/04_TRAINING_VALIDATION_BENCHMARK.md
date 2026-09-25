# 04 — Training, Validation & Benchmarking

> **Status:** READ-ONLY forensic classification of NSE at HEAD `d9a3a829`.
> Source code wins over documentation. Every training loop, gate threshold, and split is verified.

---

## 1. The Three Training Engines

NSE implements three distinct trainers in `src/nexus_scalp/`:

| Dimension | `WalkForwardTrainer` | `CandidateTrainer` | `SequenceCandidateTrainer` |
|---|---|---|---|
| **Location** | `training/walk_forward_trainer.py:156` | `model_generation/training.py:101` | `model_generation/sequence_training.py:50` |
| **Role** | Production-compatible training path | Offline research candidate generator | 3D Sequence candidate trainer |
| **Split Strategy** | Purged Walk-Forward (default 34 folds) | Single train/val split (`80/20` or by `_split`) | Sequence windowed split ($L=32$) |
| **Input Shape** | 2D $(N, 50)$ or $(N, 70)$ | 2D $(N, 50)$ or $(N, 72)$ (with news) | 3D $(N, 32, 50)$ or $(N, 32, 70)$ |
| **Class Balancing** | `_balance_oversample_dataset` | Majority-dominant oversample | Class weights + sequence stride |
| **Output Artifact** | Candidate bundle (`.pt`, `.scaler.npz`, `.meta.json`) | Candidate artifact store record | Sequence candidate artifact |
| **Promotable?** | YES (after 12 gates & promotion) | YES (after promotion review) | Research-only; 3D not live |

---

## 2. Purged Walk-Forward Cross-Validation

The walk-forward cross-validation algorithm is implemented in `WalkForwardTrainer.train_and_validate` (`src/nexus_scalp/training/walk_forward_trainer.py:570-900`).

### 2.1 Multi-Fold Execution Geometry
```
Fold 0: [=== Train Window ===]──[Purge 15]──[Embargo 15]──[Val Window]
Fold 1: [====== Expanding Train Window ======]──[Purge 15]──[Embargo 15]──[Val Window]
...
Fold 33: [================ Full History Train ================]──[Purge]──[Val]
```

- **Default Fold Count:** 34 folds (configured via `num_folds=34` in CLI and `ChallengerTrainer`).
- **Purge Gap:** 15 bars (`purge_gap_bars = 15`) immediately preceding each test window. Eliminates label horizon lookahead from triple-barrier labels ($H=15$).
- **Embargo:** 15 bars (`embargo_bars = 15`) immediately following each test window.
- **Scaler Isolation:** Scaler mean and standard deviation are re-fit inside each fold using ONLY the training indices of that fold. Validation indices are transformed using that fold's scaler.

### 2.2 Fold Evaluation Metrics
For each fold, the trainer calculates:
1. `fold_sharpe_proxy`: $\frac{\text{mean}(\text{returns})}{\text{std}(\text{returns}) \times \sqrt{252}}$
2. `fold_economics`: realized R-multiples, profit factor, win rate, expectancy.
3. Global performance across all folds must show positive mean fold Sharpe proxy and $> 50\%$ profitable folds to pass `GATE6_WALK_FORWARD`.

---

## 3. Out-of-Sample (OOS) Validation Gate

Out-of-sample testing is governed by `OOSGate` in `src/nexus_scalp/research/oos.py:34-120`.

### 3.1 Strict Economic Integrity
- A candidate model is not evaluated on classification accuracy alone; it is evaluated on simulated trading outcomes.
- **Threshold Constants:**
  * `MIN_ECONOMIC_OOS_EXPECTANCY_R = 0.02` (Candidate must yield at least $+0.02\text{R}$ net expectancy after full friction).
  * `MAX_ALLOWABLE_DEGRADATION = 0.50` (OOS performance must not degrade by more than $50\%$ relative to in-sample metrics).
  * `MIN_OOS_TRADE_COUNT = 30` (Statistical significance requirement).

### 3.2 Gate Failure Action
If `OOSGate.evaluate()` fails:
- Candidate status is set to `REJECTED`.
- Detailed rejection reasons are logged to `audit.db` (`research_gates` table).
- Candidate cannot advance to Shadow or Champion consideration.

---

## 4. Robustness Stress Testing (BUG-299 Verified)

Implemented in `src/nexus_scalp/research/robustness.py` and evaluated via `GATE8_ROBUSTNESS`.

### 4.1 Stress Scenarios
Candidates are subjected to simulated execution friction shocks:
1. **Spread Widening:** Baseline spread perturbed by $+1.0$ and $+2.0$ ticks.
2. **Slippage Expansion:** Slippage perturbed by $+1.0$ and $+2.0$ ticks.
3. **Execution Latency:** Execution delay of 1–3 bars simulated.

### 4.2 BUG-299 Friction Cap Correction
Prior to BUG-299, stress scenarios were clamped to an uncalibrated default cap ($5.0$ ticks), causing stressed expectancies to be identical to baseline ($0.0$ degradation) and silently passing fragile candidates. Under the corrected code:
- The friction cap is dynamically derived: `cap = spread + slip + 4` (floor $5.0$).
- If no scenario moves effective friction, the gate fails closed with `ROBUSTNESS_NOT_SIMULABLE`.
- Maximum allowable degradation under stress is $50\%$.

---

## 5. Model Factory & Benchmarking Suite

Implemented in `src/nexus_scalp/model_generation/benchmark.py` and `model_factory.py`.

### 5.1 Supported Architectures in Factory
- `SCALPNET`: Dual-path MLP + Causal TCN / Attention (production).
- `MLP_RESNET`: Pure 2D multi-layer perceptron with residual skips.
- `TCN_ATTENTION_V1`: Pure sequence temporal convolutional network with multi-head attention.
- `LIGHTGBM_BASELINE`: Tabular gradient boosting baseline (research benchmark).

### 5.2 Reproducibility Protocol
- Random seeds are explicitly injected (`torch.manual_seed(seed)`, `np.random.seed(seed)`).
- Model parameters and dataset manifests are hashed with SHA-256 before training commences to ensure byte-reproducible model runs.
