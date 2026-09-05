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

AGENT 2 / BUG-244 (split-boundary horizon purge)
------------------------------------------------
The chronological 70/15/15 split is positional: a sample whose triple-barrier
label horizon (max_holding_bars = 15) reaches ACROSS the train/val or
val/test boundary was previously split-adjacent. ``_apply_split`` now takes
``purge_bars`` (default = temporal_contract.CANONICAL_PURGE_BARS = 15,
mirroring WalkForwardTrainer._split_fold_with_embargo) and tags the
positional tail of the train and val blocks as ``_split="purged"`` with
``_purged_split=True``. Purged rows belong to NO scored block; downstream
trainers must exclude ``_split == "purged"`` from BOTH pools.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import polars as pl

from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.lineage import LabelOrigin, stamp_manifest
from nexus_scalp.model_generation.models import DatasetManifest
from nexus_scalp.model_generation.sample_factory import SampleFactory, samples_to_frame
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.dataset_factory")

#: Canonical split-boundary purge width: the triple-barrier label horizon.
#: Mirrors temporal_contract.CANONICAL_PURGE_BARS (= 15) and
#: WalkForwardTrainer's purge gap so EVERY temporal boundary in the training
#: pipeline enforces the same horizon separation (BUG-244).
DEFAULT_SPLIT_PURGE_BARS: int = 15


def deterministic_dataset_id(
    symbol: str,
    timeframe: str,
    feature_schema_id: str,
    label_schema_id: str,
    strategy_id: str,
    config_hash: str,
    news_digest: dict[str, Any] | None = None,
) -> str:
    news_part = ""
    if news_digest is not None:
        news_part = "|" + json.dumps(news_digest, sort_keys=True, default=str)
    payload = (
        f"{symbol}|{timeframe}|{feature_schema_id}|{label_schema_id}|{strategy_id}|{config_hash}"
        f"{news_part}"
    )
    return "ds_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _news_digest(news_frame: pl.DataFrame | None) -> dict[str, Any] | None:
    """Deterministic news provenance digest: None when no news frame, else
    {version, rows, range, content_hash}.  Content hash over the normalized
    12-field matrix + publication times so ANY news change re-identifies."""
    if news_frame is None or news_frame.is_empty():
        return None
    try:
        from nexus_scalp.model_generation.news_bridge import normalize_news_frame

        norm = normalize_news_frame(news_frame)
        if norm is None or norm.is_empty():
            return None
        rows = norm.height
        try:
            ts_col = norm["published_at"]
            t_start = str(ts_col.min())
            t_end = str(ts_col.max())
        except Exception:
            t_start = t_end = ""
        content_hash = hashlib.sha256(
            json.dumps(
                norm.select([c for c in norm.columns]).to_dict(as_series=False),
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:16]
        return {
            "version": "news_context_v1",
            "rows": rows,
            "range": {"start": t_start, "end": t_end},
            "content_hash": content_hash,
        }
    except Exception:
        return None


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
        label_origin: str | LabelOrigin = LabelOrigin.CLEAN_HISTORICAL,
        split_purge_bars: int | None = None,
    ) -> dict[str, Any]:
        """Builds + persists a dataset artifact. Returns the handle dict.

        MLFIX-T7 lineage: every persisted dataset manifest is stamped with
        its label_origin (default CLEAN_HISTORICAL for the offline bar path).
        Callers feeding paper/live-derived frames MUST pass the matching
        origin — the production hard guard (lineage.assert_production_eligible)
        refuses tainted manifests at candidate-mint time.
        """
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

        purge_bars = (
            DEFAULT_SPLIT_PURGE_BARS if split_purge_bars is None else int(split_purge_bars)
        )
        frame = samples_to_frame(samples)
        frame = self._apply_split(
            frame,
            train_ratio=train_ratio,
            val_ratio=val_ratio,
            seed=seed,
            purge_bars=purge_bars,
        )

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

        # News digest — provenance + deterministic identity contribution.
        # A dataset built from REAL news is distinguishable from a no-news
        # dataset (spec 17/18): the digest carries the row count, temporal
        # range and a content hash, and feeds the dataset id.
        news_digest = _news_digest(news_frame)

        real_id = dataset_id or deterministic_dataset_id(
            symbol,
            timeframe,
            cfg["feature_schema_id"],
            cfg["label_schema_id"],
            strategy_id,
            c_hash,
            news_digest=news_digest,  # news content changes the dataset identity
        )

        # counts per split (boundary-purged rows are tagged, never counted
        # into train/val/test pools - BUG-244)
        counts = {
            "total": frame.height,
            "train": int(frame.filter(pl.col("_split") == "train").height),
            "val": int(frame.filter(pl.col("_split") == "val").height),
            "test": int(frame.filter(pl.col("_split") == "test").height),
            "purged_boundary": int(
                frame.filter(pl.col("_purged_split") == True).height  # noqa: E712
            ),
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
                {
                    "train_ratio": train_ratio,
                    "val_ratio": val_ratio,
                    "seed": seed,
                    "split_purge_bars": purge_bars,
                }
            ),
            purge_parameters={
                "purge_gap_bars": getattr(self.sample_factory.labeler, "embargo_bars", 3),
                "embargo_bars": getattr(self.sample_factory.labeler, "embargo_bars", 3),
                "split_purge_bars": purge_bars,
            },
            embargo_parameters={
                "embargo_bars": getattr(self.sample_factory.labeler, "embargo_bars", 3),
                "split_purge_bars": purge_bars,
            },
            generation_version=generation_version,
            news_schema_id=cfg["news_schema_id"],
            news_version=news_digest.get("version", "")
            if (news_digest := _news_digest(news_frame)) is not None
            else "",
            news_data_range=news_digest.get("range", {}) if news_digest is not None else {},
            strategy_context_version=strategy_version,
        )

        # MLFIX-T7: lineage stamp travels with the manifest (production
        # eligibility of any candidate trained on this dataset is decided
        # from this field, never inferred).
        manifest_payload = stamp_manifest(manifest.model_dump(mode="json"), label_origin)

        handle = self.store.save_dataset(
            real_id,
            frame.drop("_split"),
            manifest_payload,
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
        purge_bars: int = DEFAULT_SPLIT_PURGE_BARS,
    ) -> pl.DataFrame:
        """Chronological temporal split: train = earliest, val = middle,
        test = latest. Deterministic given seed.

        BUG-244: the last ``purge_bars`` rows of the train block and of the
        val block are tagged ``_split="purged"`` + ``_purged_split=True``.
        A purged row belongs to NO scored block: its 15-bar triple-barrier
        horizon reaches into the NEXT block. Layout:

            [ train ][ purged | val ][ purged | test ]

        Purged rows stay in the frame (audit-visible) but are excluded from
        counts and MUST be filtered out of both train and validation pools
        by downstream trainers. ``seed`` is retained for API compatibility -
        the split is purely positional/chronological; there is no RNG.
        """
        del seed  # chronological split is deterministic; no RNG used
        if frame.is_empty():
            if "_purged_split" not in frame.columns:
                frame = frame.with_columns(pl.lit(False).alias("_purged_split"))
            return frame
        frame = frame.sort("timestamp")
        n = frame.height
        train_n = int(n * train_ratio)
        val_n = int(n * val_ratio)
        val_end = train_n + val_n
        purge = max(0, int(purge_bars))
        train_scored_end = max(0, train_n - purge)
        val_scored_end = max(train_n, val_end - purge)

        split_col: list[str] = []
        for i in range(n):
            if i < train_scored_end:
                split_col.append("train")
            elif i < train_n:
                split_col.append("purged")  # train tail: horizon reaches into val
            elif i < val_scored_end:
                split_col.append("val")
            elif i < val_end:
                split_col.append("purged")  # val tail: horizon reaches into test
            else:
                split_col.append("test")
        frame = frame.with_columns(
            pl.Series("_split", split_col, dtype=pl.String),
            pl.lit(False).alias("_purged_split"),
        )
        return frame.with_columns(
            pl.when(pl.col("_split") == "purged")
            .then(pl.lit(True))
            .otherwise(pl.lit(False))
            .alias("_purged_split")
        )
