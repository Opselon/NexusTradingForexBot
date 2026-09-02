"""
Candle Intelligence Configuration
==================================
Tunable thresholds for the candle-close gate, pattern scoring and decision
hierarchy (BUG-061). All defaults are conservative: when in doubt -> no trade.

The configuration is deliberately separate from AlgoConfig so the module stays
isolated and its safety knobs cannot be silently changed by unrelated
hot-reload paths. Loaded by the engine at construction; values are plain floats
with strict ranges.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CandleIntelligenceConfig(BaseModel):
    """Conservative defaults for the candle intelligence gate."""

    enabled: bool = Field(default=True)

    # --- close geometry thresholds ---
    min_candle_range: float = Field(default=1e-9, ge=0.0)  # below -> invalid
    strong_body_ratio: float = Field(default=0.60, ge=0.0, le=1.0)
    weak_body_ratio: float = Field(default=0.20, ge=0.0, le=1.0)
    long_wick_ratio: float = Field(default=0.45, ge=0.0, le=1.0)
    rejection_reversal_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    continuation_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    indecision_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    exhaustion_threshold: float = Field(default=0.60, ge=0.0, le=1.0)

    # --- decision gating (rule hierarchy levels) ---
    entry_min_confidence: float = Field(default=0.62, ge=0.0, le=1.0)
    hold_min_confidence: float = Field(default=0.35, ge=0.0, le=1.0)
    fast_exit_confidence: float = Field(default=0.60, ge=0.0, le=1.0)
    pattern_min_confidence: float = Field(default=0.45, ge=0.0, le=1.0)
    multi_factor_min_confirmations: int = Field(default=2, ge=1, le=5)

    # --- behavior ---
    weak_close_blocks_entry: bool = Field(default=True)
    false_breakout_reduces_confidence_by: float = Field(default=0.35, ge=0.0, le=1.0)
    trapped_breakout_reduces_confidence_by: float = Field(default=0.25, ge=0.0, le=1.0)
    fallback_conservative_no_trade: bool = Field(default=True)

    # --- db ---
    db_path: str = Field(default="artifacts/candle_intel.db")
    max_batch_size: int = Field(default=500, ge=1, le=5000)
