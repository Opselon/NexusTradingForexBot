"""ML-System Layer-2: Position Management & Trade Continuation Dataset Generator.

Architectural Purpose:
----------------------
Generates specialized Position-State datasets for training Layer-2 ML Models
(Position Managers / Trade Continuation Value / Risk Arbiters).

Delegates to the canonical `PositionReplayPipeline` (ML-RISK-001) while preserving
backward compatibility with existing API routes, CLI commands, and test suites.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl
import torch

from nexus_scalp.model_generation.position_replay import (
    PositionDatasetResult,
    PositionReplayPipeline,
    ReplayExecutionConfig,
    TemporalSplitConfig,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.position_dataset_generator")

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class PositionSample:
    """A single observation of an active position state at bar T (backward compat)."""

    timestamp: str
    bar_index: int
    trade_id: int
    direction: str  # 'BUY' or 'SELL'
    entry_price: float
    current_price: float
    unrealized_pnl_r: float
    position_age_bars: int
    atr: float
    spread: float
    model_probability: float
    market_regime: str

    # Labels computed from future window [T+1 .. T+H]
    best_future_r: float
    worst_future_r: float
    continuation_value: float
    optimal_action: str  # 'KEEP', 'CLOSE', 'REDUCE'
    split: str  # 'train', 'val', 'oos', 'purge'


def generate_position_dataset(
    *,
    source_df: pl.DataFrame | None = None,
    source_path: Path | str | None = None,
    model: torch.nn.Module | None = None,
    model_path: Path | str | None = None,
    scaler_path: Path | str | None = None,
    dimension: int = 50,
    bars_limit: int = 5000,
    max_holding_bars: int = 30,
    target_atr_multiplier: float = 2.0,
    stop_loss_atr_multiplier: float = 1.5,
    friction_pips: float = 0.25,
    output_path: Path | str | None = None,
    purge_bars: int = 10,
    split_ratios: tuple[float, float, float] = (0.70, 0.15, 0.15),
    export_csv: bool = False,
) -> PositionDatasetResult:
    """Generates a Position Management Dataset using canonical Historical Replay.

    Simulates trade positions from base model decisions, tracks position states
    over time, and mathematically labels the economic value of continuing vs
    closing each position.
    """
    # 1. Acquire market data
    if source_df is not None:
        market_input: pl.DataFrame | Path = source_df
    elif source_path is not None:
        p = Path(source_path)
        if not p.is_absolute():
            p = REPO_ROOT / p
        if not p.exists():
            raise FileNotFoundError(f"Dataset file not found: {p}")
        market_input = p
    else:
        # Fallback to synthetic generation for autonomous zero-dependency runs
        from scripts.data.ingest_historical_candles import generate_synthetic_bars

        market_input = generate_synthetic_bars(
            symbol="XAUUSD", count=max(bars_limit, 1000), seed=42
        )

    exec_cfg = ReplayExecutionConfig(
        target_atr_multiplier=target_atr_multiplier,
        stop_loss_atr_multiplier=stop_loss_atr_multiplier,
        max_holding_bars=max_holding_bars,
        spread_usd=friction_pips,
    )

    split_cfg = TemporalSplitConfig(
        train_ratio=split_ratios[0],
        val_ratio=split_ratios[1],
        oos_ratio=split_ratios[2],
        purge_bars=max(purge_bars, max_holding_bars),
    )

    pipeline = PositionReplayPipeline(
        execution_config=exec_cfg,
        split_config=split_cfg,
        primary_model=model,
        primary_model_path=model_path,
        scaler_path=scaler_path,
        dimension=dimension,
    )

    _, result = pipeline.run(
        market_data=market_input,
        bars_limit=bars_limit,
        output_parquet_path=output_path,
        export_csv=export_csv,
    )

    return result
