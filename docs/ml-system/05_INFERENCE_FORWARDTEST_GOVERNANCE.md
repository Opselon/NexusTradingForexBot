# 05 — Inference, Forward-Testing & Model Governance

> **Status:** READ-ONLY forensic classification of NSE at HEAD `d9a3a829`.
> Source code wins over documentation. Every execution gate, promotion check, and governance state is verified from source.

---

## 1. Live Inference Pipeline

The live inference cycle is orchestrated by `InferenceService` (`src/nexus_scalp/application/live/inference.py:52-320`) and called by `LiveEngine._process_tick_pipeline`.

```
                    Incoming Market Tick (MT5 / Gateway)
                                     ↓
                     Historical Completed Bars (M1)
                                     ↓
              ScalpFeatureEngine.compute_from_bars()
                                     ↓
                    Raw 50D Vector: [x0, x1, ..., x49]
                                     ↓
                   validate_feature_vector() (checks shape)
                                     ↓
                 Scaler Transformation: (x - mean) / std
                                     ↓
                     ScalpNet.forward() (2D MLP Path)
                                     ↓
                         Raw Logits: [z0, z1, z2]
                       (4th logit masked to -10000.0)
                                     ↓
                             masked_softmax()
                                     ↓
                 Probabilities: [P_NO_TRADE, P_BUY, P_SELL]
```

### 1.1 Invariant Guarantees
- **Causal Integrity (INV-008):** Features are derived exclusively from completed historical bars and the current bid/ask tick. Zero lookahead is mathematically guaranteed.
- **Scaler Consistency:** Features are normalized using the EXACT mean and standard deviation vectors fitted during the candidate's training fold (`model.scaler.npz`).
- **Dead Logit Suppression:** The legacy WAIT logit (index 3) is explicitly set to $-10000.0$ before softmax, ensuring zero probability mass is allocated to the dead class.

---

## 2. Policy & Signal Gate Hierarchy

Model probabilities are passed to `SignalPolicy.evaluate_probabilities()` (`src/nexus_scalp/signals/policy.py:171-1545`). The policy applies an ordered sequence of filters before an order proposal is emitted:

1. **Guardian Emergency Gate:** Blocks all new proposals if circuit breakers or daily loss limits are tripped.
2. **Symbol & Market State Filter:** Ensures symbol is active and spread is within allowable bounds.
3. **Duplicate Tick Filter:** Drops redundant quotes.
4. **AI Reversal Gate:** Blocks counter-trend entries against strong established directional flow.
5. **Frequency Throttle:** Prevents trade spamming within minimum inter-trade bar intervals.
6. **Higher-Timeframe (HTF) Alignment:** Requires M5/M15 trend alignment with proposed signal direction.
7. **Support / Resistance (SR) Margin Filter:** Requires sufficient profit headroom before encountering major S/R levels.
8. **Confidence Threshold Gate:** Directional confidence must satisfy threshold (default $0.35$ for baseline, $0.60$ for high-conviction zones).
9. **Zone Quality Gate:** Validates market structural zone.
10. **Anti-Flip Lockout:** Prevents rapid alternating BUY/SELL whipsaws.

### 2.1 SMC God Mode Override
Implemented in `src/nexus_scalp/signals/policy.py:710-721`:
- **Conditions:** Confidence $\ge 0.60$ AND Break of Structure (`has_bos`) AND Valid Order Block (`valid_ob`) AND Liquidity Sweep (`sweep`) AND Fair Value Gap (`fvg`).
- **Behavior:** Bypasses standard HTF trend and SR margin filters (`override_reason = "HTF_BYPASSED"`).
- **Penalty:** Imposes an automatic $15\%$ confidence haircut (`signals/policy.py:1136-1141`) to account for counter-trend risk.

---

## 3. Forward-Testing & Shadow Mode

NSE implements forward testing via two modes: **SHADOW** and **PAPER**.

### 3.1 SHADOW Mode Execution Boundary
- Controlled in `DecisionExecutor.execute_decision_stage` (`src/nexus_scalp/application/live/decision_executor.py:129-150`).
- If `config.execution.mode == "SHADOW"`, any BUY or SELL proposal is downgraded to `NO_TRADE` with reason:
  ```python
  "SHADOW_OBSERVATION_ONLY"
  ```
- **Zero Order Authority:** No orders are transmitted to MT5 or paper broker under any circumstance.
- Intelligent hedges are also suppressed (`live_engine.py:3524-3533`).

### 3.2 Shadow Recording vs Outcome Resolution
- **Recording (Live):** On every tick, `ShadowRecorder.record_shadow_decision` (`application/live/shadow_recorder.py:39`) records Champion and Challenger predictions into `audit.db` (`shadow_decisions` table).
- **Resolution (Replay Only):** The realized outcome resolver (`shadow/outcomes.py:resolve_paired`) calculates realized R-multiples on paired market paths. **In live trading, outcome resolution is NOT invoked.** Live records remain in status `NOT_RECORDED` or `PENDING` until evaluated via offline replay (`shadow/_replay_evidence.py`).

---

## 4. Model Governance & Promotion Lifecycle

Promotion of a candidate model to production Champion is strictly governed by `ModelGovernanceEngine` (`src/nexus_scalp/governance/engine.py`) and executed through `web/model_governance_routes.py:1270`.

### 4.1 The Single Promotion Path: `POST /api/models/promotion/execute`
There is **NO UI BUTTON** in the web dashboard for promotion. Promotion is API-only.

```
                  Operator Authenticated Request
               POST /api/models/promotion/execute
                                ↓
                 Validate Actor & Approval Token
                                ↓
           Acquire Exclusive Lock (governance/lock.py)
                                ↓
             Verify Emergency Freeze (promotion_frozen == False)
                                ↓
             Verify Old Champion SHA-256 Hash
                                ↓
             Verify Candidate Bundle Integrity (inspect_artifact)
                                ↓
             Atomic Filesystem Swap (with Rollback Journal)
                                ↓
               Commit New Champion to Governance Ledger
                                ↓
                      Release Promotion Lock
```

### 4.2 Mandatory Promotion Prerequisites
1. **Approval Token:** Valid cryptographic operator token.
2. **Unfrozen Governance:** `promotion_frozen` must be `False`.
3. **Cross-Process Mutex:** `PromotionLock` (`governance/lock.py`) acquired via atomic file descriptor creation.
4. **Old Champion Match:** SHA-256 of active `model.pt` must match governance record to prevent race conditions.
5. **Candidate Coherence:** Candidate `model.pt`, `model.scaler.npz`, and `model.meta.json` must be present, valid, and non-corrupt.
6. **Rollback Journal:** Previous champion files are journaled before overwrite; any filesystem failure triggers automatic instant rollback.

### 4.3 In-Memory Freeze Behavior
`ModelGovernanceEngine.freeze_promotions(actor, reason)` sets:
```python
self.promotion_frozen = True
```
- **Storage:** Stored in Python process memory (`self.promotion_frozen: bool`).
- **Restart Impact:** An audit record is written to SQLite, but the boolean flag itself is **NOT restored on engine reboot**. A process restart automatically unfreezes promotions back to `False`.

---

## 5. Online Fine-Tuning & Learning Loop Status

| Aspect | Current Reality | Source Citation |
|---|---|---|
| **Code Exists?** | YES (`fine_tune_online`) | `training/walk_forward_trainer.py:1041` |
| **Wired to Tick Path?** | YES (via bar handler) | `application/live/bar_handler.py:325` |
| **Enabled by Default?** | **NO (DISABLED)** | `bar_handler.py:311` (`learning.online_finetune.enabled: False`) |
| **Can Overwrite Champion?** | Config-gated & PersistDecision-gated | `live_engine.py:3822` |
| **Default Live Impact:** | **ZERO** (Weights remain completely immutable) | `learning_config.py:56` |

**Conclusion:** Online fine-tuning is an implemented architectural capability that is safely locked by configuration defaults. In production, live trading never automatically updates neural network weights.
