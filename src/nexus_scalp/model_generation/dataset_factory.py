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

from nexus_scalp.model_generation.artifact_store import (
    ArtifactConflictError,
    ArtifactStore,
    sha256_file,
)
from nexus_scalp.model_generation.dataset_manifest import (
    DatasetIntegrityError,
    DatasetLoadResult,
    DatasetManifest,
    compute_dataset_hash,
)
from nexus_scalp.model_generation.lineage import LabelOrigin, stamp_manifest
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
    content_digest: str | None = None,
) -> str:
    """Content-aware deterministic dataset identity (AGENT-14 QA wave 2,
    CHG-0061).

    The id binds: config (symbol/timeframe/schemas/strategy/hash) + news
    provenance + a ``content_digest`` over the SAMPLE CONTENT. Two builds
    with identical configs but different underlying bars MUST mint different
    ids - a one-cent close-price mutation re-identifies the dataset, so a
    downstream result can never silently reference different data under the
    same identity ("what exact bytes produced this result").
    """
    news_part = ""
    if news_digest is not None:
        news_part = "|" + json.dumps(news_digest, sort_keys=True, default=str)
    content_part = ""
    if content_digest:
        content_part = "|" + content_digest
    payload = (
        f"{symbol}|{timeframe}|{feature_schema_id}|{label_schema_id}|{strategy_id}|{config_hash}"
        f"{news_part}{content_part}"
    )
    return "ds_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def frame_content_digest_raw(df: pl.DataFrame, max_rows: int = 200_000) -> str:
    """Deterministic content hash over the RAW bar input (AGENT-14 wave 2)."""
    cols = [c for c in ("timestamp", "open", "high", "low", "close", "atr") if c in df.columns]
    proj = (
        df.sort("timestamp")[:max_rows].select(cols)
        if "timestamp" in df.columns
        else df[:max_rows].select(cols)
    )
    canonical = __import__("json").dumps(proj.to_dict(as_series=False), sort_keys=True, default=str)
    return __import__("hashlib").sha256(canonical.encode("utf-8")).hexdigest()[:16]


def frame_content_digest(frame: pl.DataFrame, max_rows: int = 200_000) -> str:
    """Deterministic content hash over the sample frame (AGENT-14 wave 2).

    Hashes a canonical projection (sample_id + timestamp + feature_vector +
    label metadata if present) so ANY content change re-identifies while
    irrelevant serialization differences do not. Bounded to ``max_rows`` for
    memory safety (first N rows in sorted-timestamp order).
    """
    cols: list[str] = [
        c for c in ("sample_id", "timestamp", "feature_vector") if c in frame.columns
    ]
    if not cols:
        cols = frame.columns[:8]
    proj = (
        frame.sort("timestamp")[:max_rows].select(cols)
        if "timestamp" in frame.columns
        else frame[:max_rows].select(cols)
    )
    canonical = json.dumps(proj.to_dict(as_series=False), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


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

        purge_bars = DEFAULT_SPLIT_PURGE_BARS if split_purge_bars is None else int(split_purge_bars)
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

        # AGENT-14 wave 2: content digest feeds the id. Hashing is over
        # the RAW bar input (timestamp + OHLC + per-bar ATR family) so a
        # one-cent mutation in ANY bar changes the identity - sample features
        # are synthetic/deterministic in tests and do not reflect the raw
        # close movement, so hashing only samples would miss the mutation.
        c_digest = frame_content_digest_raw(df)

        real_id = dataset_id or deterministic_dataset_id(
            symbol,
            timeframe,
            cfg["feature_schema_id"],
            cfg["label_schema_id"],
            strategy_id,
            c_hash,
            news_digest=news_digest,  # news content changes the dataset identity
            content_digest=c_digest,  # sample content changes the dataset identity
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

        # ML-DATA-002: Cryptographic array content checksum and exact split indices
        sha256_checksum = compute_dataset_hash(frame)
        split_indices: dict[str, list[int]] = {}
        if frame.height <= 50_000:
            split_indices = {
                "train": frame.with_row_index()
                .filter(pl.col("_split") == "train")["index"]
                .to_list(),
                "val": frame.with_row_index().filter(pl.col("_split") == "val")["index"].to_list(),
                "test": frame.with_row_index()
                .filter(pl.col("_split") == "test")["index"]
                .to_list(),
                "purged": frame.with_row_index()
                .filter(pl.col("_purged_split") == True)["index"]  # noqa: E712
                .to_list(),
            }

        manifest = DatasetManifest(
            dataset_id=real_id,
            dataset_version=generation_version,
            row_count=frame.height,
            row_counts=counts,
            temporal_range=temporal_range,
            date_range=temporal_range,
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
            split_indices=split_indices,
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
            sha256_checksum=sha256_checksum,
        )

        # MLFIX-T7: lineage stamp travels with the manifest (production
        # eligibility of any candidate trained on this dataset is decided
        # from this field, never inferred).
        manifest_payload = stamp_manifest(manifest.model_dump(mode="json"), label_origin)
        manifest_payload["sha256_checksum"] = sha256_checksum
        manifest_payload["row_count"] = frame.height
        if split_indices:
            manifest_payload["split_indices"] = split_indices
        # Content digest recorded in the manifest so a REBUILD with the same
        # deterministic id can prove content equality (idempotent reuse)
        # instead of tripping the immutability conflict (CHG-0061).
        manifest_payload["content_digest"] = c_digest

        # Idempotent rebuild: the CLI contract (deterministic identity, same
        # input -> same id -> exit OK on rebuild) meets the store's
        # immutability contract (never overwrite an existing identity).
        # When the artifact for THIS id already exists AND is intact AND its
        # recorded content digest matches, the rebuild is a no-op: return
        # the existing handle. Any other pre-existing state (different
        # content under the same id = digest collision, or a corrupt
        # artifact) still raises ArtifactConflictError — never silently
        # overwritten.
        existing_manifest = self.store.read_dataset_manifest(real_id)
        existing_parquet = self.store.dataset_path(real_id)
        if existing_manifest is not None and existing_parquet.exists():
            actual = hashlib.sha256(existing_parquet.read_bytes()).hexdigest()
            if actual != str(existing_manifest.get("dataset_hash") or ""):
                raise ArtifactConflictError(
                    f"dataset {real_id!r}: existing artifact is CORRUPT "
                    f"(manifest dataset_hash != parquet bytes) - refusing to "
                    "overwrite; mint a NEW dataset id"
                )
            recorded = str(existing_manifest.get("content_digest") or "")
            if recorded and recorded != c_digest:
                raise ArtifactConflictError(
                    f"dataset {real_id!r}: same id but DIFFERENT content "
                    "(content digest mismatch) - refusing to overwrite; "
                    "mint a NEW dataset id"
                )
            logger.info(
                "[DATASET] event=REUSED dataset_id=%s (deterministic rebuild, artifact intact)",
                real_id,
            )
            return {
                "path": str(existing_parquet),
                "hash": str(existing_manifest.get("dataset_hash") or ""),
                "dataset_id": real_id,
                "counts": existing_manifest.get("row_counts") or {},
                "config_hash": c_hash,
                "reused": True,
            }

        # P0-4 EVALUATION INTEGRITY (deep audit Section K): the split markers
        # MUST survive persistence, otherwise the benchmark/validation lane can
        # never PROVE the OOS population it scores (_split in {val, test}) and
        # every stored dataset fails closed with NO_SPLIT_MARKERS. _split and
        # _purged_split are row bookkeeping over the same samples; the
        # deterministic dataset id hashes the RAW bar input
        # (frame_content_digest_raw(df)), not the split frame, so keeping them
        # changes neither the dataset identity nor any existing row value.
        handle = self.store.save_dataset(
            real_id,
            frame,
            manifest_payload,
        )
        handle["dataset_id"] = real_id
        handle["counts"] = counts
        handle["config_hash"] = c_hash
        handle["row_count"] = frame.height
        handle["sha256_checksum"] = sha256_checksum
        logger.info(
            "[DATASET] event=BUILT dataset_id=%s rows=%d",
            real_id,
            frame.height,
        )
        return handle

    # ------------------------------------------------------------------
    # Explicit save & load with cryptographic integrity verification (ML-DATA-002)
    # ------------------------------------------------------------------

    def save(
        self,
        dataset_id: str,
        data: pl.DataFrame,
        manifest: DatasetManifest | dict[str, Any] | None = None,
        *,
        feature_schema_hash: str = "",
        symbol: str = "XAUUSD",
        timeframe: str = "M1",
        label_origin: str | LabelOrigin = LabelOrigin.CLEAN_HISTORICAL,
        allow_overwrite: bool = False,
    ) -> dict[str, Any]:
        """Saves a dataset artifact (parquet + dataset_manifest.json) with cryptographic checksum.

        Computes sha256_checksum over the raw features and labels, records
        split indices/ranges if present, and saves through ArtifactStore.
        """
        checksum = compute_dataset_hash(data)
        row_count = data.height

        split_indices: dict[str, list[int]] = {}
        split_counts: dict[str, int] = {"total": row_count}
        if "_split" in data.columns and row_count <= 50_000:
            for s in ("train", "val", "test", "purged"):
                indices = data.with_row_index().filter(pl.col("_split") == s)["index"].to_list()
                if indices:
                    split_indices[s] = indices
                    split_counts[s] = len(indices)

        ts_col = data["timestamp"] if "timestamp" in data.columns else None
        temporal_range = {
            "start": str(ts_col.min()) if ts_col is not None and not ts_col.is_empty() else "",
            "end": str(ts_col.max()) if ts_col is not None and not ts_col.is_empty() else "",
        }

        if manifest is None:
            manifest_obj = DatasetManifest(
                dataset_id=dataset_id,
                row_count=row_count,
                row_counts=split_counts,
                temporal_range=temporal_range,
                date_range=temporal_range,
                symbol=symbol,
                timeframe=timeframe,
                feature_schema_hash=feature_schema_hash,
                sha256_checksum=checksum,
                split_indices=split_indices,
            )
            manifest_payload = manifest_obj.to_dict()
        elif isinstance(manifest, DatasetManifest):
            manifest_payload = manifest.to_dict()
            manifest_payload["sha256_checksum"] = checksum
            manifest_payload["row_count"] = row_count
            if split_indices and not manifest_payload.get("split_indices"):
                manifest_payload["split_indices"] = split_indices
        else:
            manifest_payload = dict(manifest)
            manifest_payload["dataset_id"] = dataset_id
            manifest_payload["sha256_checksum"] = checksum
            manifest_payload["row_count"] = row_count
            if split_indices and not manifest_payload.get("split_indices"):
                manifest_payload["split_indices"] = split_indices

        manifest_payload = stamp_manifest(manifest_payload, label_origin)
        handle = self.store.save_dataset(
            dataset_id,
            data,
            manifest_payload,
            allow_overwrite=allow_overwrite,
        )
        handle["dataset_id"] = dataset_id
        handle["row_count"] = row_count
        handle["sha256_checksum"] = checksum
        handle["manifest"] = manifest_payload
        return handle

    def load(self, dataset_id: str) -> DatasetLoadResult:
        """Loads and cryptographically verifies a dataset artifact.

        Checks:
        1. Existence of parquet artifact and dataset_manifest.json.
        2. Parquet file checksum matches manifest `dataset_hash`.
        3. Array content hash matches manifest `sha256_checksum`.
        4. Row count matches manifest `row_count`.

        Raises `DatasetIntegrityError` if any value in the dataset array or
        manifest has been modified, tampered, or corrupted.
        """
        p = self.store.dataset_path(dataset_id)
        if not p.exists():
            raise FileNotFoundError(f"Dataset artifact not found: {p}")

        manifest_dict = self.store.read_dataset_manifest(dataset_id)
        if manifest_dict is None:
            raise DatasetIntegrityError(f"dataset {dataset_id!r}: missing manifest JSON")

        manifest = DatasetManifest.from_dict(manifest_dict)

        # 1. Parquet artifact file checksum check
        expected_file_hash = str(manifest.dataset_hash or "")
        if expected_file_hash:
            actual_file_hash = sha256_file(p)
            if actual_file_hash != expected_file_hash:
                raise DatasetIntegrityError(
                    f"dataset {dataset_id!r}: file hash mismatch "
                    f"(manifest {expected_file_hash[:12]} != actual {actual_file_hash[:12]})"
                )

        # 2. Read DataFrame
        frame = pl.read_parquet(p)

        # 3. Row count check
        if manifest.row_count > 0 and frame.height != manifest.row_count:
            raise DatasetIntegrityError(
                f"dataset {dataset_id!r}: row count mismatch "
                f"(manifest {manifest.row_count} != actual {frame.height})"
            )

        # 4. Content array hash check
        if manifest.sha256_checksum and manifest.sha256_checksum != manifest.dataset_hash:
            actual_content_hash = compute_dataset_hash(frame)
            if actual_content_hash != manifest.sha256_checksum:
                raise DatasetIntegrityError(
                    f"dataset {dataset_id!r}: array content hash mismatch "
                    f"(manifest {manifest.sha256_checksum[:12]} != actual {actual_content_hash[:12]})"
                )

        return DatasetLoadResult(frame, manifest)

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
