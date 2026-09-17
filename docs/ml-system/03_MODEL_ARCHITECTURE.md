# 03 — Model Architecture & Execution Anatomy

> **Status:** READ-ONLY forensic classification of NSE at HEAD `d9a3a829`.
> Source code wins over documentation. Every layer, tensor shape, and dispatch seam is verified from source.

---

## 1. ScalpNet Architecture Overview

The primary neural network architecture in NSE is `ScalpNet`, implemented in `src/nexus_scalp/models/scalp_net.py:104-241`.

### 1.1 Architectural Characteristics
- **Base Class:** `torch.nn.Module`
- **Design Paradigm:** Dual-Path Hybrid (2D MLP ResNet for snapshot inference; 3D Causal TCN + Multi-Head Self-Attention for sequence modeling).
- **Default Parameterization:**
  * `input_dim`: 50 (production) or 70 (research)
  * `hidden_dim`: 128
  * `num_classes`: 3 default (`TRAINED_CLASS_COUNT`, `model_class_contract.py:50`)
  * `dropout`: 0.15
  * `num_heads`: 4
  * `kernel_size`: 3
  * `dilation`: 2

---

## 2. Layer-by-Layer Anatomy

```
               Input Tensor (Batch, 50)
                          ↓
               unsqueeze(1) → (Batch, 1, 50)
                          ↓
             ┌────────────┴────────────┐
             │                         │
     [seq_len == 1]             [seq_len > 1]
             │                         │
      2D Snapshot Path          3D Sequence Path
             │                         │
    Linear(50, 128)            CausalConv1d(50, 128)
             ↓                         ↓
         LayerNorm                 LayerNorm
             ↓                         ↓
           GeLU                      GeLU
             ↓                         ↓
    2× Residual Blocks        Positional Encoding
     Linear(128, 128)                  ↓
         LayerNorm             Multi-Head Attention
           GeLU                (4 heads, embed=128)
             ↓                         ↓
          Dropout             AdaptiveAvgPool1d(1)
             └────────────┬────────────┘
                          ↓
               Linear(128, 3) Head
                          ↓
                Logits (Batch, 3)
                          ↓
               masked_softmax()
                          ↓
             Probabilities: [P0, P1, P2]
```

### 2.1 Detailed Component Breakdown

| Stage | Component | Input Shape | Output Shape | Parameters / Activation | Function & Purpose |
|---|---|---|---|---|---|
| **Input** | Tensor conversion | `(B, 50)` | `(B, 1, 50)` | None | Reshapes 2D row into 3D batch with sequence length $L=1$ |
| **2D Path** | `input_proj_2d` | `(B, 1, 50)` | `(B, 1, 128)` | Linear + LN + GeLU | Projects raw feature space into hidden embedding space |
| **2D Path** | `res_blocks_2d` | `(B, 1, 128)` | `(B, 1, 128)` | 2× Linear(128, 128) + Residual | Extracts non-linear feature interactions with skip connections |
| **3D Path** | `CausalConv1d` | `(B, 50, L)` | `(B, 128, L)` | Conv1d(kernel=3, dilation=2) | Temporal feature extraction with causal left-padding (zero lookahead) |
| **3D Path** | `SinusoidalPositionalEncoding` | `(B, L, 128)` | `(B, L, 128)` | Deterministic sine/cosine | Injects temporal bar position into transformer embeddings |
| **3D Path** | `MultiheadAttention` | `(B, L, 128)` | `(B, L, 128)` | 4 heads, $d_k=32$ | Computes self-attention across historical bar sequences |
| **3D Path** | `AdaptiveAvgPool1d` | `(B, 128, L)` | `(B, 128)` | Pooling | Compresses temporal dimension into fixed embedding vector |
| **Head** | `classifier` | `(B, 128)` | `(B, 3)` | Linear(128, 3) | Emits raw unnormalized logits for NO_TRADE, BUY, SELL |

---

## 3. Runtime Routing: 2D vs 3D Sequence Execution

The actual execution path is decided dynamically at runtime:

```python
# models/scalp_net.py:194-209
if x.ndim == 2:
    x = x.unsqueeze(1)  # (Batch, 1, Features)

B, L, F = x.shape
if L == 1:
    return self._forward_2d(x)  # <-- ACTIVE PRODUCTION PATH
else:
    return self._forward_3d(x)  # <-- RESEARCH / SEQUENCE CANDIDATE PATH
```

### 3.1 Live Inference Truth
1. In production, `LiveEngine` computes a 50D vector for the current tick.
2. `LiveSequenceService` (`src/nexus_scalp/application/live_sequence.py:98`) checks `state.trained_mode != "sequence"`.
3. Because the production Champion bundle metadata specifies `trained_mode = "2d"`, `maybe_build_live_sequence_tensor` returns `None`.
4. Inference falls back to single-tick vector `(1, 50)`.
5. `ScalpNet` receives `(1, 50)`, converts it to `(1, 1, 50)`, and routes to `_forward_2d`.
6. **Verdict:** The 3D sequence path (TCN + Attention) is **RESEARCH / CAPABILITY-ONLY**. Production live trading exclusively runs the 2D MLP ResNet path.

---

## 4. Model Output Contract & Calibration

### 4.1 Canonical 3-Class Contract
- Defined in `src/nexus_scalp/model_lifecycle/model_class_contract.py:50-52`:
  * Index 0: `NO_TRADE`
  * Index 1: `BUY_MARKET`
  * Index 2: `SELL_MARKET`

### 4.2 Legacy 4-Wide Head Handling
- Older checkpoints on disk carried 4 logits (`WAIT` at index 3).
- `mask_wait_logit()` (`model_class_contract.py:101-121`) masks index 3 with $-1\times 10^4$ ($-\infty$) before softmax:
  ```python
  logits[:, 3] = -10000.0
  ```
- Softmax over the masked tensor forces the probability of WAIT to 0.0, restoring exact 3-class probability distribution over `[NO_TRADE, BUY, SELL]`.

---

## 5. Model vs Strategy Hierarchy (Who Decides What?)

NSE enforces a strict separation between neural network prediction and execution authority:

```
                  Raw Market Data (MT5 Ticks)
                              ↓
                    ScalpFeatureEngine
                              ↓
                     50D Feature Vector
                              ↓
                          ScalpNet
                              ↓
                   Probabilities [P0, P1, P2]
                              ↓
                    SignalPolicy (SMC Matrix)
             [Confidence Gate, HTF Alignment, Anti-Flip]
                              ↓
                    PolicyProposal (BUY / SELL)
                              ↓
                         RiskEngine
              [Exposure Limits, Margin, Lot Sizing]
                              ↓
                      OrderManager / MT5
```

### 5.1 Absolute Authority Boundaries (from code)
1. **Can the model predict BUY while strategy says NO_TRADE?**
   **YES.** If model confidence is $0.55$ but `confidence_threshold` is $0.60$, `SignalPolicy` rejects the signal. Similarly, if the Higher-Timeframe (HTF) trend is bearish, the BUY signal is rejected (`BUY_REJECTED_HTF_TREND_CONFL_FAIL`).
2. **Can the strategy trade without the model?**
   **YES.** The `RuleMatrixEngine` can independently trigger entries based on pure price action / SMC structures without waiting for model inference.
3. **Can SMC override the model?**
   **YES.** `SMC_GOD_MODE` (`signals/policy.py:710-721`) activates when BOS + OB + Sweep + FVG are all confirmed with high confidence, bypassing standard HTF/SR alignment with a 15% confidence penalty.
4. **Can Risk Engine reject the model?**
   **YES.** `RiskEngine` can reject orders due to max drawdown breach, daily loss limit, max simultaneous open orders, or insufficient margin.
5. **Can the model place an MT5 order directly?**
   **NO.** `ScalpNet` has zero order authority. It is purely a mathematical function mapping $\mathbb{R}^{50} \to [0, 1]^3$.

---

## 6. PyTorch Training Infrastructure

| Functionality | Implementation | Code Location |
|---|---|---|
| Framework | PyTorch 2.x (CPU in production, GPU supported in training) | `src/nexus_scalp/models/scalp_net.py` |
| Optimizer | `torch.optim.Adam(lr=5e-4, weight_decay=1e-5)` | `training/walk_forward_trainer.py:2020` |
| Loss Function | `torch.nn.CrossEntropyLoss(weight=class_weights)` | `training/walk_forward_trainer.py:1938-1960` |
| Gradient Clipping | `torch.nn.utils.clip_grad_norm_(parameters, max_norm=1.0)` | `training/walk_forward_trainer.py:2038` |
| Early Stopping | Patience = 3 epochs on validation fold loss | `training/walk_forward_trainer.py:2045` |
| Checkpoint Serialization | `torch.save(model.state_dict(), path)` | `training/walk_forward_trainer.py:2264-2280` |
