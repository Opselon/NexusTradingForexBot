# src/nexus_scalp/models/scalp_net.py

- **PURPOSE:** The production neural network — ScalpNet v3: a dual-path
  architecture mapping the 50D feature vector (or 70D for candidates) to the
  4-class trading decision (0=NO_TRADE, 1=BUY_MARKET, 2=SELL_MARKET, 3=WAIT).
  One path is a fast 2D ResNet-MLP for the sub-millisecond single-tick hot
  path; the other is a 3D dilated causal TCN + sinusoidal positional encoding
  + multi-head self-attention for temporal pattern recognition.
- **ARCHITECTURE LAYER:** Models. Pure PyTorch `nn.Module` — no I/O, no
  adapter, no order authority (research-safety contract).
- **RESPONSIBILITY:** Convert (Batch, 50) or (Batch, Seq, 50) tensors into
  decision probabilities with guaranteed zero future leakage (strict causal
  padding) and stable gradient dynamics (pre-LayerNorm residual design).
- **DEPENDENCIES:** torch (nn, nn.functional), stdlib math. No repo deps.
- **CONNECTS TO:** LiveEngine inference (`_infer_probabilities`), trainer /
  challenger / model factory (training + fine-tune), model_lifecycle
  (champion artifact load), model_generation architectures (legacy baseline
  in the benchmark MATRIX vs TCN_ATTENTION_V1), tests.
- **KEY CONCEPTS:**
  - `CausalConv1d` — pads ONLY on the left (`F.pad(x, (padding, 0))`,
    conv `padding=0`): output at step t depends on inputs ≤ t, strictly.
    Dilation ladder (1, 2, 4) grows receptive field ~7 steps with 3 layers
    — enough for local microstructure context without a big kernel.
  - `SinusoidalPositionalEncoding` — deterministic sin/cos position table
    (max 500), registered as a non-trainable buffer so it ships with
    state_dict as a constant (no learned positions — keeps positions
    transferable across sequence lengths).
  - The **dual-path routing** is a single `forward` decision on `x.dim()`:
    2D snapshots expand to (B,1,F) then run the MLP residual path; 3D
    sequences run the TCN (transposed to (B,H,S)) + attention path, pooling
    only the LAST time step (h[:, -1, :]) — the decision uses the newest
    state, which is the correct causal read for an online scalper.
  - **Head depth:** Linear(128→64→32→4) with GELU + dropout(0.25) — three
    stages of compression before the 4-logit head.
  - `return_logits or self.training` → logits (for loss); live inference
    returns softmax probs. This is the single switch that keeps training and
    serving symmetric.
  - num_features=50 default (serving champion); candidates instantiate 70D.
- **HOT PATH / PERFORMANCE:** 2D path is a few matmuls (~µs on CPU for
  batch 1); `torch.inference_mode()` at the call site (live_engine) prevents
  autograd overhead. The 3D path is heavier — used for sequence research/
  training, not the per-tick hot path.
- **EDGE CASES & PITFALLS:**
  - `x.dim()` routing means a (B, 50) and a (B, 1, 50) input take DIFFERENT
    paths — a caller that accidentally unsqueezes gets the TCN path (slower,
    still correct). Tests pin the expected path per shape.
  - State dict portability: buffers (`pe`) + modules must match artifact
    creation exactly; the integrity layer (model_lifecycle/integrity.py)
    verifies hash+dims on load.
  - Softmax at inference + argmax at the policy → the 3rd class WAIT is
    advisory (policy may map model WAIT to NO_TRADE or hold depending on
    regime).