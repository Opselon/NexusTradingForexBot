# 02 — Data Contract (Current State)

> **Status:** READ-ONLY forensic classification at HEAD
> `bb9ce84ddd22bd193ff419c309181f67e8b3164d`. Source wins over docs. Evidence
> labels: [V] = verified from source; [NVE] = not verified from executed data; [UC] = usage confirmed; [?]= unknown.

## Current Evidence

**Schema Contract**
- ACTIVE_SCHEMA_ID = "scalp_v1" (src/nexus_scalp/features/schema_contract.py:1)
- Feature dimension = 50 (production). 70D exists but is research-only.

**Feature Generation Pipeline**
- Entry point: `compute_from_bars(current_tick, completed_bars)` (src/nexus_scalp/application/live/tick_pipeline.py:398-400)
- Core feature generation: `ScalpFeatureEngine` in `src/nexus_scalp/features/scalp_features.py` (lines 1-200, 350-1129)
- Feature types (samples): Order Flow Imbalance, ICT FVGs, Smart Money Concepts, Ichimoku Kumo, Wick Anatomy, Cross-Asset Z-Scores, Volume Profile, Price Action, Order Book Depth, Time-based cycles.
- Normalization: Unknown in source; manual inspection needed.
- Output shape: (50) per bar.

**Labeling**
- Classes: 3 trained (NO_TRADE/BUY/SELL). 4 legacy (WAIT added) exists via LEGACY_HEAD_CLASSES=4 (model_class_contract.py:50-65).
- Labeling pipeline: not explicitly shown; likely inference output.

**Datasets**
- Training dataset shape: (N, 50) -> ScalpNet input after optional unsqueeze(1).
- Window creation: Use sliding window of 1 timestep (sequence length = 1) in production training.
- Data splits: Unknown; need investigation.

**Scalers / Normalization**
- Unknown; investigation required.

## Key Findings

- Production uses 50D only (scalp_v1). 70D is research-only (`features/features70.py`).
- No explicit normalization functions found; must investigate runtime70.py and features/runtime70.py.
- Feature generation pipeline traced but normalization missing.
- Class contract: 3-class production, 4-class legacy.

## Remaining Investigation

1. Normalization implementation for 50 features.
2. Exact dataset creation pipeline (sliding window size, batch format).
3. Data splits for training/validation/OOS across trainers.
4. Purge/embargo implementations.
5. Feature normalization location (pre-training, dataset, inference).

**Next Phase:** Create detailed task specs for these investigations.