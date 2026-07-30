"""
Tick Storage & Parquet Lake Engine
===================================
High-throughput columnar storage pipeline converting real-time tick streams
into partitioned Parquet files for training and backtesting.
"""

from datetime import datetime
from pathlib import Path

import polars as pl

from nexus_scalp.domain.models import TickData
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.market_data.tick_storage")


class ParquetTickStorage:
    """
    Appends tick batches to partitioned Parquet files on disk.
    """

    def __init__(self, base_directory: Path = Path("data/raw")) -> None:
        self._base_dir = base_directory
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def write_batch(self, symbol: str, ticks: list[TickData]) -> Path:
        """
        Converts a list of TickData objects into a Polars DataFrame and flushes to Parquet.

        Args:
            symbol: Financial instrument symbol.
            ticks: List of validated domain TickData objects.

        Returns:
            Path: Destination Parquet file path.
        """
        if not ticks:
            raise ValueError("Cannot write empty tick batch to storage.")

        data = {
            "symbol": [t.symbol for t in ticks],
            "timestamp": [t.timestamp for t in ticks],
            "bid": [t.bid for t in ticks],
            "ask": [t.ask for t in ticks],
            "last": [t.last for t in ticks],
            "volume": [t.volume for t in ticks],
            "flags": [t.flags for t in ticks],
        }

        df = pl.DataFrame(data)

        sample_time: datetime = ticks[0].timestamp
        year_str = sample_time.strftime("%Y")
        month_str = sample_time.strftime("%m")
        day_str = sample_time.strftime("%d")

        target_dir = self._base_dir / symbol / year_str / month_str
        target_dir.mkdir(parents=True, exist_ok=True)

        target_file = target_dir / f"{day_str}.parquet"

        if target_file.exists():
            existing_df = pl.read_parquet(target_file)
            df = pl.concat([existing_df, df])

        df.write_parquet(target_file, compression="zstd")
        logger.debug(
            "Flushed tick batch to Parquet",
            symbol=symbol,
            count=len(ticks),
            file=str(target_file),
        )
        return target_file
