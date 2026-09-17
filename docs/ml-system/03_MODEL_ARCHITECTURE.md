# 03 — Model Architecture (Current State)

> **Status:** READ-ONLY forensic classification at HEAD
> `bb9ce84ddd22bd193ff419c309181f67e8b3164d`. Source wins over docs. Evidence
> labels: [V] = verified from source; [NVE] = not verified from executed data.

## Current Evidence

**Model Variants**
- **ScalpNet (v3 - 50D Vector Aligned)**
  - File: `src/nexus_scalp/models/scalp_net.py` (lines 1-241)
  - Primary production inference model.
  - **Dual-Path Architecture**: 2D Snapshot (MLP) & 3D Sequence (TCN + Attention)
  - Production uses 2D path (single-tick).

- **Legacy Support**
  - 4-class head (NO_TRADE/BUY/SELL/WAIT) via LEGACY_HEAD_CLASSES=4
  - See `src/nexus_scalp/model_lifecycle/model_class_contract.py` (lines 50-65)
  - Opted into, not default.

**Core Components**
- **CausalConv1d**: Left-padded 1D convolutions for causal temporal dependencies
- **SinusoidalPositionalEncoding**: Deterministic PE for sequence length up to 500
- **Multi-Head Attention**: 4 heads (embed_dim=128)
- **Dual Heads**: MLP ResNet (2D) + Causal TCN + Attention (3D)

**Production Path**
- Input shape: (Batch, 50) 2D, expanded to (Batch, 1, 50) for internal 3D treatment
- Output: (Batch, 3) logits or Softmax probabilities
- Training heads: 3 default via TRAINED_CLASS_COUNT=3

**Research Variants**
- 70D feature variants: `features/features70.py`, `features/runtime70.py`
- SequenceCandidateTrainer: Research-only for 70D 3D sequences
- No production path verification found.

**Key Design Invariants**
1. Zero future information leakage via left-side causal padding
2. Dual-path inference routing controlled by `is_2d_input` and `trained_mode`
3. Stable gradient dynamics via Pre-LayerNorm + GeLU

## Classification Summary

**PRODUCTION**
- ScalpNet (primary)
- 50D feature pipeline
- 2D single-tick inference path
- 3-class output (NO_TRADE/BUY/SELL)

**RESEARCH**
- 70D features & runtime
- LiveSequenceService (3D sequence buffer)
- SequenceCandidateTrainer
- 3D sequence inference path

**CAPABILITY ONLY**
- 3D sequence inference (via LiveSequenceService)
- LiveSequenceService (unusable in production due to trained_mode gating)

**LEGACY**
- 4-class head (WAIT) - opted into via explicit flag, not default

## Remaining Investigation

1. Exact normalization in feature pipeline (unknown currently)
2. Model artifacts (checkpoint files with proper class count)
3. Training pipeline normalization scope
4. Sequence model vs 2D path actual usage
5. Training dataset splits and class distribution

**Next Phase:** Create detailed task specs for architecture investigation.