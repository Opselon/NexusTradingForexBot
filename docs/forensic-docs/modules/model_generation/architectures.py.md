# src/nexus_scalp/model_generation/architectures.py

- **PURPOSE:** TCN_ATTENTION_V1 — the first NEW architecture benchmark candidate
  (PHASE 13B): a dedicated causal-temporal model that competes FAIRLY with the
  legacy ScalpNet baseline under identical data/labels/splits/purge/embargo/
  friction.
- **ARCHITECTURE LAYER:** Research/ML — model definition (torch), no order
  authority.
- **RESPONSIBILITY:** Implement the causal TCN + self-attention scalping model
  with a STRICT 3-logit head (NO_TRADE/BUY/SELL); WAIT is a policy state, never
  a neural output of the new architecture (unlike the legacy 4-head bridge).
- **DEPENDENCIES:** torch, torch.nn.functional. No nexus_scalp imports.
- **CONNECTS TO:** model_factory (build_tcn_attention_v1 registry entry),
  integrity.py head probes (head.3.weight / head.0.weight handled).

- **KEY CONCEPTS:**
  - `CausalConv1dBlock` (line 38): dilated causal conv + GELU + residual +
    LayerNorm. Left-side-only padding `F.pad(x, (padding, 0))` (line 67):
    output at t sees only inputs ≤ t — strict causality.
  - `TCNAttentionV1` (line 76): input (B, T, F) → Linear projection +
    LayerNorm → `blocks` dilated causal conv blocks (dilation = 2**i,
    lines 104-114) → MultiheadAttention (self-attention over the temporal
    representation, batch_first, need_weights=False) with residual + norm →
    positional embedding (learned, truncated for short sequences, line 145) →
    FINAL-STATE POOLING (h_last = h[:, -1, :], line 150 — causal, no leakage) →
    head (Linear(hidden→hidden//2)→GELU→Dropout→Linear(hidden//2→3), line 128).
  - Invariants (module docstring): strictly causal; deterministic init under a
    configured seed (caller must torch.manual_seed BEFORE construction);
    bounded params; configurable hidden_dim/blocks/heads/dropout with explicit
    architecture version; head ALWAYS 3 logits.
  - `build_tcn_attention_v1` (line 154): config-driven constructor for the
    ModelFactory registry; defaults hidden 128 / blocks 3 / kernel 3 / heads 4 /
    dropout 0.15 / max_seq_len 64.
- **HOT PATH / PERFORMANCE:** Inference on (1, T, F): conv blocks are O(T·H²)
    per block; attention O(T²·H) with T ≤ 64 (bounded by max_seq_len positional
    embedding) — small enough for the ~Hz inference path. Training batches
    (B, 16, F) typical in SequenceCandidateTrainer.
- **EDGE CASES & PITFALLS:**
  - Positional embedding width max_seq_len=64: sequences LONGER than 64 would
    raise in `self.pos_embedding[:, :t, :]` slicing? No — slicing truncates to
    64 silently, dropping position info beyond 64 rather than failing.
  - `padding = (kernel_size - 1) * dilation`: for kernel 3 dilation 2**i the
    receptive field grows exponentially (3 blocks ≈ 15 timesteps); sequences
    shorter than the receptive field still run (conv padded) but the earliest
    timesteps see zero-padding context — the "warm-up" rows are excluded by
    SequenceBuilder.valid for the tail only.
  - Head geometry: `head.0.weight` (hidden→hidden//2) is the FIRST head layer —
    integrity.py's class-head probe explicitly handles this (does not confuse
    hidden width with class count); head.3.weight is the class head only in the
    naming used by integrity probes — here the Sequential has exactly 4 modules
    (0..3) so "head.3.weight" is the final Linear's weights. Aligned with
    integrity.py's priority list.