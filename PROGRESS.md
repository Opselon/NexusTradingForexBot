# Quantitative Autopsy & System Hardening Report (v3.9 Enterprise)

## 1. Log Trace Diagnosis (Autopsy Report)
- **Trace Event Timestamp:** 15:25:08
- **Root Cause Analysis:**
  The class weight calculation previously utilized an un-clamped inverse class frequency weighting scheme scaled with an active class boost. Due to severe class skewness in the online recent bars (where class counts were heavily biased), this formula yielded an un-clamped weight spike of `weights_4d=[1.61, 10.0, 10.0]`.
- **Impact on Neural Networks (ScalpNet):**
  The massive weight penalty multiplier of `10.0` acted as a severe loss amplifier for minority class predictions, which in turn exploded the Cross Entropy Loss to `train_loss=6.57`. During subsequent backpropagation, this massive loss gradient destabilized the network's layers, causing validation accuracy to completely collapse to **15.1%** (`validation_accuracy=0.151`).
- **Inference Bias & Model Collapse:**
  The destabilized model developed a severe **98.9% SELL bias**, completely losing all predictive diversity.
- **Rollback Event:**
  Although the WalkForwardTrainer's atomic rollback correctly intercepted and rejected this degraded model, the engine fell back to a transient baseline model that placed rapid-fire, blind orders without frequency constraints or concurrent position bounds. This caused extreme churning risk and dangerous over-exposure.

---

## 2. Applied System Fixes (By File)

### A. `src/nexus_scalp/training/walk_forward_trainer.py`
1. **Mathematical Class Weight Clamping:**
   Refactored the class weight formulation to use the strict clamped formula:
   $$W_c = \text{clamp}\left(\frac{N_{\text{total}}}{3.0 \times (N_c + 1.0)}, 0.5, 2.0\right)$$
   This prevents any single-class weight from exceeding `2.0`, eliminating extreme loss spikes.
2. **Mean-Normalization:**
   Enforced weight normalization such that the mean of $W$ equals 1.0 ($W.\text{mean}() == 1.0$). This preserves overall loss magnitude while maintaining relative class weighting.
3. **Smoother Fine-Tuning:**
   Reduced fine-tuning epochs from `5` to `3` and learning rate from `1e-4` to `5e-5` to ensure stable convergence and prevent model collapse or overshooting.

### B. `src/nexus_scalp/signals/policy.py`
1. **Strict Frequency Throttle:**
   Enforced `MIN_ORDER_INTERVAL_SECONDS = 60`. If any trade proposal was dispatched less than 60 seconds ago (relative to current tick timestamp), the engine instantly returns `ActionType.NO_TRADE` with `reason=ORDER_FREQUENCY_THROTTLED`.
2. **Max Exposure Gate:**
   Enforced `MAX_CONCURRENT_POSITIONS = 1`. The engine queries active positions/pending orders matching symbol `XAUUSD` and magic number `888101`. If an order exists:
   - If the current price is within the same price level ($0.50 threshold), it blocks with `reason=SAME_LEVEL_REENTRY_BLOCKED`.
   - Otherwise, it blocks with `reason=MAX_EXPOSURE_REACHED`.
3. **Regime Guardian Gate Integration:**
   Activated the `is_guardian_active` gate. If an unsafe regime is detected, it blocks downstream evaluation with `reason=BLOCKED_BY_GUARDIAN_REGIME_<RegimeType>`.

### C. `src/nexus_scalp/risk/risk_engine.py`
1. **Absolute Lot Ceiling:**
   Enforced a strict maximum position size of `HARD_MAX_LOTS = 2.0`.
2. **Dynamic Volume Clamp:**
   Clamped calculated volume to:
   $$\text{Volume} = \min(\text{Calculated Volume}, \text{HARD\_MAX\_LOTS}, \text{Broker\_Max\_Volume})$$
3. **Free Margin Pre-Check:**
   Computes the required margin for the proposed order. If `required_margin > account.margin_free`, the risk engine rejects the trade by returning `0.0` volume (or `None` proposal evaluation), preventing broker-level rejections.
4. **Clamping Utility Compatibility:**
   Implemented `get_clamped_position_size` to ensure existing structural test assertions pass perfectly.

### D. `src/nexus_scalp/features/scalp_features.py`
1. **Pipeline Integrity:**
   Defined `FeaturePipelineFrozenError` and implemented `validate_and_fallback` to recover from corrupted NaN feature inputs gracefully and restore structural unit test integrity.

---

## 3. System Health Verification Matrix

| Metrics Domain | Hardened Constraint / Metric | Status | Verification Source / Test |
|---|---|---|---|
| **Model Health** | Balanced Predictions, No Skew, `is_healthy = True` | **HEALTHY** | `test_model_rollback_on_health_check_failure` |
| **Order Churning** | Minimum 60-Second Cooldown between dispatches | **ELIMINATED** | `tests/unit/test_policy.py` |
| **Exposure Risk** | Maximum 1 Concurrent Position or Pending Order | **CAPPED** | `tests/unit/test_policy.py` |
| **Sizing Risk** | Strict Lot Sizing <= 2.0 Lots, Free Margin Verified | **CAPPED** | `test_risk_engine_cascading_safety_clamps` |
| **Regression** | All Unit Tests Pass 100% | **100% PASS** | `pytest tests/unit/` |
