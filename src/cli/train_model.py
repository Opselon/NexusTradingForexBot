"""
End-to-End Deep Learning Training Pipeline (ScalpNet Orchestrator - 50D Contract Aligned)
==========================================================================================
Orchestrates the entire Machine Learning lifecycle from raw tick data to production weights:
    1. Tick Data Ingestion (Polars Parquet Lake).
    2. Deterministic OHLC M1 Bar Reconstruction (Event Replay).
    3. Feature Matrix Generation (50D Microstructure, Price Action, SMC & Multi-Timeframe Anatomy).
    4. Purged Triple-Barrier Labeling (Friction & MAE Aware).
    5. Walk-Forward Deep Learning Training (OOS Validated ScalpNet v3).

Usage via CLI:
    python -m cli.train_model --symbol XAUUSD --folds 34
"""

import sys
from pathlib import Path
from typing import Annotated, Any

import polars as pl
import typer

# Add src directory to path for module resolution if executed directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import FeatureVector, ScalpFeatureEngine
from nexus_scalp.labeling.triple_barrier import TripleBarrierLabeler
from nexus_scalp.market_data.bar_aggregator import BarAggregator
from nexus_scalp.observability.logging import configure_logging, get_logger
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

logger = get_logger("nexus_scalp.cli.train_model")

app = typer.Typer(help="End-to-End ScalpNet Neural Network Training Orchestrator.")


def load_raw_ticks(data_dir: Path, symbol: str) -> pl.DataFrame:
    """Scans and concatenates all ZSTD Parquet tick files for the symbol."""
    target_path = data_dir / symbol
    if not target_path.exists():
        raise FileNotFoundError(f"Raw tick data directory not found: {target_path}")

    logger.info("Scanning data lake for Parquet tick files...", path=str(target_path))
    parquet_files = list(target_path.rglob("*.parquet"))

    if not parquet_files:
        raise ValueError(f"No parquet files found in {target_path}")

    lazy_frames = [pl.scan_parquet(str(f)) for f in parquet_files]
    df_raw = pl.concat(lazy_frames).collect()

    df_sorted = df_raw.sort("timestamp")
    logger.info("Raw tick ingestion complete", total_ticks=len(df_sorted))
    return df_sorted


def reconstruct_features_and_bars(df_ticks: pl.DataFrame, symbol: str) -> pl.DataFrame:
    """
    Replays raw ticks through the BarAggregator and ScalpFeatureEngine to build
    the 50D Feature Matrix ensuring 100% parity with live trading mechanics.
    """
    logger.info("Initiating Deterministic Tick Replay & Feature Engineering...")

    aggregator = BarAggregator(symbol=symbol, timeframe_minutes=1)
    feature_engine = ScalpFeatureEngine(symbol=symbol)

    feature_records: list[dict[str, Any]] = []

    for row in df_ticks.iter_rows(named=True):
        tick = TickData(
            symbol=row["symbol"],
            timestamp=row["timestamp"],
            bid=row["bid"],
            ask=row["ask"],
            last=row.get("last", 0.0),
            volume=row.get("volume", 0.0),
            flags=row.get("flags", 0),
        )

        is_new_bar = aggregator.process_tick(tick)
        completed_bars = aggregator.get_completed_bars()

        if is_new_bar and len(completed_bars) > 52:
            fv: FeatureVector = feature_engine.compute_from_bars(
                completed_bars=completed_bars,
                current_tick=tick,
            )

            # Map exact 50D sanitized tensor features (feat_0 .. feat_49)
            tensor_50d = fv.to_tensor_input()
            record: dict[str, Any] = {
                f"feat_{idx}": float(val) for idx, val in enumerate(tensor_50d)
            }

            last_bar = completed_bars[-1]
            record["close"] = last_bar.close
            record["high"] = last_bar.high
            record["low"] = last_bar.low
            record["open"] = last_bar.open
            record["spread"] = tick.ask - tick.bid
            record["atr_m1"] = fv.atr_m1

            feature_records.append(record)

    df_features = pl.DataFrame(feature_records)
    logger.info("Feature engineering complete", total_feature_snapshots=len(df_features))
    return df_features


@app.command()
def train(
    symbol: Annotated[
        str, typer.Option(help="Financial instrument symbol to train on.")
    ] = "XAUUSD",
    data_dir: Annotated[
        Path, typer.Option(help="Base directory of raw tick parquet files.")
    ] = Path("data/raw"),
    model_output: Annotated[Path, typer.Option(help="Path to save trained weights.")] = Path(
        "artifacts/models/scalp/XAUUSD/v1.0.0/model.pt"
    ),
    folds: Annotated[int, typer.Option(help="Number of Purged Walk-Forward rolling windows.")] = 34,
    epochs: Annotated[int, typer.Option(help="Epochs per Walk-Forward fold.")] = 10,
    batch_size: Annotated[int, typer.Option(help="Maximum batch size for dataloader.")] = 256,
    friction_usd: Annotated[
        float, typer.Option(help="Estimated friction per trade (Spread + Comm).")
    ] = 0.35,
) -> None:
    """Executes the complete End-to-End Training Pipeline."""
    configure_logging(
        log_level="INFO",
        json_format=False,
        log_to_file=True,
        log_file_path=Path("logs"),
    )

    logger.info(
        "Starting End-to-End ScalpNet Pipeline",
        symbol=symbol,
        folds=folds,
        friction_usd=f"${friction_usd:.2f}",
    )

    try:
        df_ticks = load_raw_ticks(data_dir=data_dir, symbol=symbol)
        df_features = reconstruct_features_and_bars(df_ticks=df_ticks, symbol=symbol)

        if len(df_features) < 1000:
            logger.critical(
                "Insufficient feature snapshots generated. Requires at least 1,000 bars."
            )
            raise typer.Exit(code=1)

        logger.info("Executing Purged Triple-Barrier Labeling...")
        labeler = TripleBarrierLabeler(
            take_profit_atr_mult=1.1,
            stop_loss_atr_mult=1.0,
            max_holding_bars=15,
            friction_usd=friction_usd,
            embargo_bars=3,
        )
        df_labeled = labeler.label_dataframe(df_features)

        # Select exact 50D feature columns (feat_0 .. feat_49)
        feature_cols = [f"feat_{idx}" for idx in range(WalkForwardTrainer.NUM_FEATURES)]

        logger.info("Initiating PyTorch Training Engine...")
        trainer = WalkForwardTrainer(
            num_folds=folds,
            train_ratio=0.70,
            batch_size=batch_size,
            learning_rate=5e-4,
            epochs_per_fold=epochs,
            early_stopping_patience=3,
            purge_gap_bars=15,
            artifact_save_path=model_output,
        )

        trainer.train_and_validate(df=df_labeled, feature_cols=feature_cols)

        logger.info(
            "End-to-End Training Pipeline completed successfully! Model is ready for Live Execution.",
            path=str(model_output),
        )

    except Exception as e:
        logger.error("Training pipeline crashed", error=str(e), exc_info=True)
        raise typer.Exit(code=1) from e


if __name__ == "__main__":
    app()
