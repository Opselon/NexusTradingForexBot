"""Dataset Factory (PHASE 13, spec 7 / 8 / 17).

Dataset artifacts are reproducible. The same inputs must generate the same
dataset identity.

    RAW HISTORY + NEWS HISTORY
        -> SampleFactory (features + labels + news context)
        -> temporal split (train/val/test)
        -> purge/embargo masks preserved
        -> DatasetManifest (hashes, provenance, splits, purge)
        -> parquet artifact + manifest

The dataset generator does NOT depend on the current live model (spec 17).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import polars as pl

from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.models import DatasetManifest
from nexus_scalp.model_generation.sample_factory import SampleFactory, samples_to_frame
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.dataset_factory")


def deterministic_dataset_id(
    symbol: str,
    timeframe: str,
    feature_schema_id: str,
    label_schema_id: str,
    strategy_id: str,
    config_hash: str,
) -> str:
    payload = (
        f"{symbol}|{timeframe}|{feature_schema_id}|{label_schema_id}|{strategy_id}|{config_hash}"
    )
    return "ds_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def config_blob(config: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class DatasetFactory:
    """Deterministic artifact-based dataset generator."""

    def __init__(
        self,
        store: ArtifactStore | None = None,
        sample_factory: SampleFactory | None = None,
    ) -> None:
        self.store = store or ArtifactStore()
        self.sample_factory = sample_factory or SampleFactory()

    def build(
        self,
        df: pl.DataFrame,
        *,
        symbol: str = "XAUUSD",
        timeframe: str = "M1",
        news_frame: pl.DataFrame | None = None,
        strategy_id: str = "scalp_default",
        strategy_version: str = "1.0.0",
        train_ratio: float = 0.7,
        val_ratio: float = 0.15,
        seed: int = 42,
        generation_version: str = "1.0.0",
        dataset_id: str | None = None,
    ) -> dict[str, Any]:
        """Builds + persists a dataset artifact. Returns the handle dict."""
        samples = self.sample_factory.build_samples(
            df,
            symbol=symbol,
            timeframe=timeframe,
            news_frame=news_frame,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
        )
        if not samples:
            raise ValueError(
                "DatasetFactory: no samples generated (check bars, labels, news frame)"
            )

        frame = samples_to_frame(samples)
        frame = self._apply_split(frame, train_ratio=train_ratio, val_ratio=val_ratio, seed=seed)

        cfg: dict[str, Any] = {
            "symbol": symbol,
            "timeframe": timeframe,
            "feature_schema_id": self.sample_factory.feature_schema.schema_id,
            "label_schema_id": self.sample_factory.label_schema.label_schema_id,
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "train_ratio": train_ratio,
            "val_ratio": val_ratio,
            "seed": seed,
            "generation_version": generation_version,
            "news_schema_id": self.sample_factory.news_schema.news_context_schema_id,
        }
        c_hash = config_blob(cfg)

        real_id = dataset_id or deterministic_dataset_id(
            symbol,
            timeframe,
            cfg["feature_schema_id"],
            cfg["label_schema_id"],
            strategy_id,
            c_hash,
        )

        # counts per split
        counts = {
            "total": frame.height,
            "train": int(frame.filter(pl.col("_split") == "train").height),
            "val": int(frame.filter(pl.col("_split") == "val").height),
            "test": int(frame.filter(pl.col("_split") == "test").height),
        }

        ts_series = frame["timestamp"]
        temporal_range = {
            "start": str(ts_series.min() or ""),
            "end": str(ts_series.max() or ""),
        }

        manifest = DatasetManifest(
            dataset_id=real_id,
            dataset_version=generation_version,
            row_counts=counts,
            temporal_range=temporal_range,
            symbol=symbol,
            timeframe=timeframe,
            feature_schema_id=cfg["feature_schema_id"],
            label_schema_id=cfg["label_schema_id"],
            label_config_hash=self.sample_factory.labeler.__dict__.copy().__repr__()[:64],
            split_config_hash=config_blob(
                {"train_ratio": train_ratio, "val_ratio": val_ratio, "seed": seed}
            ),
            purge_parameters={
                "purge_gap_bars": getattr(self.sample_factory.labeler, "embargo_bars", 3),
                "embargo_bars": getattr(self.sample_factory.labeler, "embargo_bars", 3),
            },
            embargo_parameters={
                "embargo_bars": getattr(self.sample_factory.labeler, "embargo_bars", 3),
            },
            generation_version=generation_version,
            news_schema_id=cfg["news_schema_id"],
            strategy_context_version=strategy_version,
        )

        handle = self.store.save_dataset(
            real_id,
            frame.drop("_split"),
            manifest.model_dump(mode="json"),
        )
        handle["dataset_id"] = real_id
        handle["counts"] = counts
        handle["config_hash"] = c_hash
        logger.info(
            "[DATASET] event=BUILT dataset_id=%s rows=%d",
            real_id,
            frame.height,
        )
        return handle

    # ------------------------------------------------------------------
    # Temporal split (chronological; purge/embargo preserved via labels)
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_split(
        frame: pl.DataFrame,
        train_ratio: float = 0.7,
        val_ratio: float = 0.15,
        seed: int = 42,
    ) -> pl.DataFrame:
        """Chronological temporal split: train = earliest, val = middle,
        test = latest. deterministic given seed."""
        if frame.is_empty():
            return frame
        frame = frame.sort("timestamp")
        n = frame.height
        train_n = int(n * train_ratio)
        val_n = int(n * val_ratio)
        splits = pl.Series(["train"] * train_n + ["val"] * val_n + ["test"] * (n - train_n - val_n))
        return frame.with_columns(splits.alias("_split"))
