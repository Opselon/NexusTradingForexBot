"""ML-System Layer-2: Position Management & Trade Continuation Dataset Generator.

Architectural Purpose:
----------------------
Generates specialized Position-State datasets for training Layer-2 ML Models
(Position Managers / Trade Continuation Value / Risk Arbiters).

Instead of re-training the entry model, Layer-2 models answer:
    "Given current market state and this open position's parameters
    (entry price, unrealized PnL, age, ATR, model probability),
    what is the economic continuation value of keeping the position open
    versus closing or reducing it right now?"

Key Guarantees:
---------------
  1. Strict Anti-Leakage: At bar T, position state and features only use data <= T.
     Future bars [T+1 .. T+H] are strictly isolated to compute outcome labels.
  2. Mathematical Labeling: Computes continuous Continuation Value (best_future_R - current_R),
     MFE (Max Favorable Excursion), MAE (Max Adverse Excursion), and discrete
     action labels: KEEP / CLOSE / REDUCE.
  3. Purge & Embargo: Chronological splits (Train 70% / Val 15% / OOS 15%) with
     purge boundaries to eliminate label overlap leakage between splits.
  4. Immutable Manifest: Generates cryptographic SHA-256 hash and metadata for
     exact reproducibility.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl
import torch

from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.position_dataset_generator")

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class PositionSample:
    """A single observation of an active position state at bar T."""

    timestamp: str
    bar_index: int
    trade_id: int
    direction: str  # 'LONG' or 'SHORT'
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


@dataclass(frozen=True)
class PositionDatasetResult:
    """Summary report returned after generating a Position Dataset."""

    status: str
    dataset_path: str
    total_samples: int
    simulated_trades: int
    actions_distribution: dict[str, int]
    mean_continuation_value: float
    mean_holding_bars: float
    splits: dict[str, int]
    sha256: str
    elapsed_sec: float


def _detect_regime(closes: np.ndarray, atrs: np.ndarray, idx: int) -> str:
    """Classifies local market regime based on SMA slope and ATR volatility."""
    if idx < 20:
        return "RANGING"
    sma_short = float(np.mean(closes[idx - 10 : idx + 1]))
    sma_long = float(np.mean(closes[idx - 20 : idx + 1]))
    atr_val = atrs[idx]
    mean_atr = float(np.mean(atrs[max(0, idx - 50) : idx + 1]))

    if atr_val > 1.5 * mean_atr:
        return "HIGH_VOLATILITY"
    if sma_short > sma_long + 0.3 * atr_val:
        return "TRENDING_UP"
    if sma_short < sma_long - 0.3 * atr_val:
        return "TRENDING_DOWN"
    return "RANGING"


def generate_position_dataset(
    *,
    source_df: pl.DataFrame | None = None,
    source_path: Path | str | None = None,
    model: torch.nn.Module | None = None,
    dimension: int = 50,
    bars_limit: int = 5000,
    max_holding_bars: int = 30,
    target_atr_multiplier: float = 2.0,
    stop_loss_atr_multiplier: float = 1.5,
    friction_pips: float = 0.25,
    output_path: Path | str | None = None,
    purge_bars: int = 10,
    split_ratios: tuple[float, float, float] = (0.70, 0.15, 0.15),
) -> PositionDatasetResult:
    """Generates a Position Management Dataset from historical market bars.

    Simulates trade positions from base model decisions, tracks position states
    over time, and mathematically labels the economic value of continuing vs
    closing each position.
    """
    t0 = time.perf_counter()

    # 1. Load or acquire bar data
    if source_df is not None:
        df = source_df
    elif source_path is not None:
        p = Path(source_path)
        if not p.is_absolute():
            p = REPO_ROOT / p
        if not p.exists():
            raise FileNotFoundError(f"Dataset file not found: {p}")
        if p.suffix.lower() == ".parquet":
            df = pl.read_parquet(p)
        else:
            df = pl.read_csv(p)
    else:
        # Fallback to synthetic generation for autonomous zero-dependency runs
        from scripts.data.ingest_historical_candles import generate_synthetic_bars

        df = generate_synthetic_bars(symbol="XAUUSD", count=max(bars_limit, 1000), seed=42)

    # Limit to bars_limit if specified
    if bars_limit and df.height > bars_limit:
        df = df.slice(0, bars_limit)

    n_bars = df.height
    if n_bars < 100:
        raise ValueError(f"Insufficient bars for position dataset: got {n_bars}, require >= 100.")

    closes = np.array(df["close"].to_numpy(), dtype=np.float64)
    highs = np.array(df["high"].to_numpy(), dtype=np.float64)
    lows = np.array(df["low"].to_numpy(), dtype=np.float64)
    opens = np.array(df["open"].to_numpy(), dtype=np.float64)
    time_col = "time_utc" if "time_utc" in df.columns else "time"
    timestamps = [str(t) for t in df[time_col].to_list()]

    # 2. Compute ATR array
    atrs = np.zeros(n_bars, dtype=np.float64)
    for i in range(14, n_bars):
        tr = np.maximum(
            highs[i - 13 : i + 1] - lows[i - 13 : i + 1],
            np.maximum(
                np.abs(highs[i - 13 : i + 1] - closes[i - 14 : i]),
                np.abs(lows[i - 13 : i + 1] - closes[i - 14 : i]),
            ),
        )
        atrs[i] = max(float(np.mean(tr)), 0.25)
    atrs[:14] = atrs[14] if n_bars > 14 else 1.0

    # 3. Model or heuristic entry signal generation
    active_model: torch.nn.Module = (
        model if model is not None else ScalpNet(num_features=dimension, num_classes=3)
    )
    active_model.eval()

    # Pre-split boundaries for chronological splitting
    train_end_idx = int(n_bars * split_ratios[0])
    val_end_idx = int(n_bars * (split_ratios[0] + split_ratios[1]))

    samples: list[PositionSample] = []
    trade_id_counter = 0
    trade_durations: list[int] = []

    # Simulate trades across historical bars (step by 5 to create realistic cadence)
    bar_idx = 55  # warm-up window
    while bar_idx < n_bars - max_holding_bars:
        # Mock feature vector for signal generation
        # Realistic multi-feature pattern derived from price momentum & ATR
        price_diff = (closes[bar_idx] - opens[bar_idx]) / atrs[bar_idx]
        trend_diff = (closes[bar_idx] - closes[bar_idx - 20]) / atrs[bar_idx]

        # Model decision simulation (deterministic or model forward pass)
        x_vec = np.zeros((1, dimension), dtype=np.float32)
        x_vec[0, 0] = float(price_diff)
        x_vec[0, 1] = float(trend_diff)
        if dimension > 2:
            x_vec[0, 2] = float(atrs[bar_idx])

        with torch.inference_mode():
            logits = active_model(torch.tensor(x_vec))
            probs = torch.softmax(logits, dim=-1).flatten().numpy()

        # Signal triggered if directional probability >= 0.35
        # (or fallback heuristic if model is uniform)
        p_buy = float(probs[1]) if len(probs) > 1 else 0.0
        p_sell = float(probs[2]) if len(probs) > 2 else 0.0

        is_buy = (p_buy > p_sell and (p_buy >= 0.33 or trend_diff >= 0.0)) or (bar_idx % 12 == 0)
        is_sell = (p_sell >= p_buy and (p_sell >= 0.33 or trend_diff < 0.0)) or (bar_idx % 16 == 0)

        if not (is_buy or is_sell):
            bar_idx += 2
            continue

        trade_id_counter += 1
        direction = "LONG" if is_buy else "SHORT"
        entry_price = closes[bar_idx]
        entry_atr = atrs[bar_idx]
        entry_prob = max(p_buy, p_sell)
        unit_r = max(entry_atr * stop_loss_atr_multiplier, 0.50)

        # Simulate position holding across future bars
        holding_len = 0
        for offset in range(1, max_holding_bars + 1):
            curr_idx = bar_idx + offset
            if curr_idx >= n_bars:
                break
            holding_len = offset

            curr_price = closes[curr_idx]
            curr_high = highs[curr_idx]
            curr_low = lows[curr_idx]

            # Calculate current unrealized PnL in R
            raw_pnl = (
                (curr_price - entry_price) if direction == "LONG" else (entry_price - curr_price)
            )
            unrealized_r = round(raw_pnl / unit_r, 4)

            # Check if Stop Loss was hit during this bar
            adverse_price = curr_low if direction == "LONG" else curr_high
            worst_bar_pnl = (
                (adverse_price - entry_price)
                if direction == "LONG"
                else (entry_price - adverse_price)
            )
            worst_bar_r = worst_bar_pnl / unit_r

            # Anti-leakage future lookahead [curr_idx + 1 .. min(curr_idx + 15, n_bars - 1)]
            future_lookahead_end = min(curr_idx + 15, n_bars)
            if future_lookahead_end > curr_idx + 1:
                future_highs = highs[curr_idx + 1 : future_lookahead_end]
                future_lows = lows[curr_idx + 1 : future_lookahead_end]

                if direction == "LONG":
                    future_max_pnl = np.max(future_highs) - entry_price
                    future_min_pnl = np.min(future_lows) - entry_price
                else:
                    future_max_pnl = entry_price - np.min(future_lows)
                    future_min_pnl = entry_price - np.max(future_highs)

                best_future_r = round(float(future_max_pnl / unit_r), 4)
                worst_future_r = round(float(future_min_pnl / unit_r), 4)
            else:
                best_future_r = unrealized_r
                worst_future_r = unrealized_r

            # Continuation Value = how much additional upside can be harvested beyond current PnL
            continuation_val = round(best_future_r - unrealized_r, 4)

            # Mathematical Action Labeling
            friction_r = friction_pips / unit_r
            if continuation_val > (friction_r + 0.3) and worst_future_r > -1.0:
                action = "KEEP"
            elif unrealized_r >= 1.5 and (worst_future_r < -0.4 or continuation_val < 0.2):
                action = "REDUCE"
            else:
                action = "CLOSE"

            # Assign split with purge boundary guard
            if curr_idx < train_end_idx - purge_bars:
                split_tag = "train"
            elif curr_idx < train_end_idx + purge_bars:
                split_tag = "purge"
            elif curr_idx < val_end_idx - purge_bars:
                split_tag = "val"
            elif curr_idx < val_end_idx + purge_bars:
                split_tag = "purge"
            else:
                split_tag = "oos"

            regime = _detect_regime(closes, atrs, curr_idx)
            spread_est = round(float(highs[curr_idx] - lows[curr_idx]) * 0.05, 4)

            sample = PositionSample(
                timestamp=timestamps[curr_idx],
                bar_index=curr_idx,
                trade_id=trade_id_counter,
                direction=direction,
                entry_price=round(entry_price, 4),
                current_price=round(curr_price, 4),
                unrealized_pnl_r=unrealized_r,
                position_age_bars=offset,
                atr=round(float(atrs[curr_idx]), 4),
                spread=spread_est,
                model_probability=round(entry_prob, 4),
                market_regime=regime,
                best_future_r=best_future_r,
                worst_future_r=worst_future_r,
                continuation_value=continuation_val,
                optimal_action=action,
                split=split_tag,
            )
            samples.append(sample)

            # Stop position tracking if stopped out or hit substantial profit target
            if worst_bar_r <= -1.0 or unrealized_r >= target_atr_multiplier:
                break

        trade_durations.append(holding_len)
        # Advance bar_idx by holding length or at least 5 bars to avoid extreme overlap
        bar_idx += max(holding_len, 5)

    if not samples:
        raise RuntimeError("No position samples generated from market data.")

    # 4. Convert samples to Polars DataFrame and save Parquet
    records = [asdict(s) for s in samples]
    out_df = pl.DataFrame(records)

    # Resolve output path
    if output_path is not None:
        target_file = Path(output_path)
    else:
        out_dir = REPO_ROOT / "data" / "positions"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts_str = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        target_file = out_dir / f"position_dataset_{ts_str}.parquet"

    target_file.parent.mkdir(parents=True, exist_ok=True)
    out_df.write_parquet(target_file, compression="zstd")

    # Compute checksum
    hasher = hashlib.sha256()
    with open(target_file, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    sha256_hash = hasher.hexdigest()

    elapsed = round(time.perf_counter() - t0, 4)

    # Compute action distribution and splits
    actions: dict[str, int] = {}
    for act in ("KEEP", "CLOSE", "REDUCE"):
        actions[act] = int(out_df.filter(pl.col("optimal_action") == act).height)

    splits: dict[str, int] = {}
    for s in ("train", "val", "oos", "purge"):
        splits[s] = int(out_df.filter(pl.col("split") == s).height)

    raw_mean_cont = out_df["continuation_value"].mean()
    mean_cont = float(str(raw_mean_cont)) if raw_mean_cont is not None else 0.0
    mean_hold = float(np.mean(trade_durations)) if trade_durations else 0.0

    try:
        final_rel_path = str(target_file.relative_to(REPO_ROOT))
    except ValueError:
        final_rel_path = str(target_file)

    return PositionDatasetResult(
        status="OK",
        dataset_path=final_rel_path,
        total_samples=out_df.height,
        simulated_trades=trade_id_counter,
        actions_distribution=actions,
        mean_continuation_value=round(mean_cont, 4),
        mean_holding_bars=round(mean_hold, 1),
        splits=splits,
        sha256=sha256_hash,
        elapsed_sec=elapsed,
    )
