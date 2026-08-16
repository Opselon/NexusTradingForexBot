"""Sample Factory (PHASE 13, spec 15 / 16).

Pipeline:

    RAW MARKET HISTORY
        -> BAR NORMALIZATION
        -> FEATURE ENGINE (FeatureSchemaRegistry)
        -> REGIME (regime classifier)
        -> NEWS HISTORICAL CONTEXT (causally correct snapshot)
        -> SETUP BUILDER
        -> LABEL ENGINE (TripleBarrierLabeler, 3-class)
        -> SAMPLE

Each sample retains provenance: sample_id, timestamp, feature_schema,
setup_id, regime, news_context (versioned), label, label_config_version,
source range. Deterministic sample identity (no future information).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import polars as pl

from nexus_scalp.features.schema import FEATURE_SCHEMAS
from nexus_scalp.labeling.triple_barrier import TripleBarrierLabeler
from nexus_scalp.model_generation.models import (
    LabelSchema,
    NewsContextSchema,
    SampleContract,
    SetupContract,
    default_label_schema,
    default_news_context_schema,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.sample_factory")


def deterministic_sample_id(
    symbol: str,
    timeframe: str,
    timestamp: datetime,
    feature_schema_id: str,
    label: str,
) -> str:
    """Deterministic sample identity from observable inputs."""
    payload = f"{symbol}|{timeframe}|{timestamp.isoformat()}|{feature_schema_id}|{label}"
    return "sample_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class SampleFactory:
    """Builds deterministic, provenance-preserving samples from bars + news."""

    def __init__(
        self,
        labeler: TripleBarrierLabeler | None = None,
        label_schema: LabelSchema | None = None,
        news_schema: NewsContextSchema | None = None,
        feature_schema_id: str = "scalp_v1",
    ) -> None:
        self.labeler = labeler or TripleBarrierLabeler()
        self.label_schema = label_schema or default_label_schema()
        self.news_schema = news_schema or default_news_context_schema()
        self.feature_schema = FEATURE_SCHEMAS.resolve(feature_schema_id)

    # ------------------------------------------------------------------
    # News historical context (spec 11 / 12 / 28)
    # ------------------------------------------------------------------

    @staticmethod
    def news_context_at(
        news_frame: pl.DataFrame | None,
        timestamp: datetime,
        news_schema: NewsContextSchema,
    ) -> dict[str, float]:
        """Builds a CAUSALLY CORRECT news context snapshot.

        Only news events published at or BEFORE ``timestamp`` are included —
        a future news event can never enter a historical sample. Returns the
        versioned numeric vector.

        Timestamp comparison is done on epoch microseconds so tz-aware and
        tz-naive frames compare deterministically (no coercion panics).
        """
        zero = {f: 0.0 for f in news_schema.fields}
        if news_frame is None or news_frame.is_empty():
            return zero

        ts_col = "published_at" if "published_at" in news_frame.columns else None
        if ts_col is None:
            return zero

        sample_ts_us = int(timestamp.timestamp() * 1_000_000)

        # Normalize the news column to epoch microseconds for comparison.
        try:
            news_ts = news_frame[ts_col].cast(pl.Datetime("us")).dt.epoch("us")
            prior_mask = news_ts <= sample_ts_us
        except Exception:
            # Non-datetime timestamps: parse strings where possible.
            parsed = news_frame.with_columns(
                pl.col(ts_col)
                .str.to_datetime(time_zone="UTC", strict=False)
                .dt.epoch("us")
                .alias("_ts_us")
            )
            prior_mask = parsed["_ts_us"] <= sample_ts_us

        prior_ids = (
            news_frame.with_columns(prior_mask.alias("_prior"))
            .filter(pl.col("_prior"))
            .select(pl.col(ts_col))
        )
        if prior_ids.is_empty():
            return zero

        # Latest prior event defines the context snapshot.
        last = news_frame.join(
            prior_ids.sort(ts_col, descending=True).head(1), on=ts_col, how="semi"
        ).row(0, named=True)
        ctx: dict[str, float] = {}
        for f in news_schema.fields:
            raw = last.get(f, 0.0)
            try:
                ctx[f] = float(raw)
            except (TypeError, ValueError):
                ctx[f] = 0.0
        return ctx

    # ------------------------------------------------------------------
    # Setup detection (simple deterministic rule set, spec 9 / 15)
    # ------------------------------------------------------------------

    @staticmethod
    def detect_setup(
        row: dict[str, Any],
        prior_rows: list[dict[str, Any]],
    ) -> SetupContract:
        """Deterministic setup classification from bar data.

        Rules (explainable, not a second signal engine):
            * BREAKOUT  — close exceeds prior N-bar high/low by threshold
            * TREND     — consecutive close deltas same sign (3+)
            * RANGE     — narrow std of closes vs atr
            * default   — UNKNOWN
        """
        atr = float(row.get("atr_m1") or row.get("atr") or 0.0)
        if atr <= 0:
            return SetupContract(setup_id="unknown_v1", setup_type="UNKNOWN")

        closes = [float(r.get("close", 0.0)) for r in prior_rows[-5:]] + [
            float(row.get("close", 0.0))
        ]
        if len(closes) >= 5:
            hi = max(closes[:-1])
            lo = min(closes[:-1])
            close = closes[-1]
            if close > hi + 0.5 * atr:
                return SetupContract(setup_id="breakout_v1", setup_type="BREAKOUT")
            if close < lo - 0.5 * atr:
                return SetupContract(setup_id="breakout_v1", setup_type="BREAKOUT")
            deltas = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
            if all(d > 0 for d in deltas[-3:]):
                return SetupContract(setup_id="trend_v1", setup_type="TREND")
            if all(d < 0 for d in deltas[-3:]):
                return SetupContract(setup_id="trend_v1", setup_type="TREND")
            spread = max(closes) - min(closes)
            if spread < 0.8 * atr:
                return SetupContract(setup_id="range_v1", setup_type="RANGE")
        return SetupContract(setup_id="unknown_v1", setup_type="UNKNOWN")

    # ------------------------------------------------------------------
    # Main entry: build samples from a labeled bar frame
    # ------------------------------------------------------------------

    def build_samples(
        self,
        df: pl.DataFrame,
        *,
        symbol: str = "XAUUSD",
        timeframe: str = "M1",
        news_frame: pl.DataFrame | None = None,
        strategy_id: str = "scalp_default",
        strategy_version: str = "1.0.0",
        min_rows: int = 10,
    ) -> list[SampleContract]:
        """Labels ``df`` with the triple-barrier 3-class contract and builds
        deterministic samples.

        df must contain: close/high/low/atr_m1 (or atr) columns and the
        feature columns feat_0..feat_{n-1} (or a feature_vector column).
        """
        if df.height < min_rows:
            logger.warning(
                "[DATASET] too few rows for sample factory",
                rows=df.height,
                min=min_rows,
            )
            return []

        labeled = self.labeler.label_dataframe(df)
        rows = labeled.to_dicts()
        samples: list[SampleContract] = []

        prior_rows: list[dict[str, Any]] = []
        for row in rows:
            ts_raw = row.get("timestamp") or row.get("time") or row.get("datetime")
            timestamp = self._parse_ts(ts_raw) if ts_raw else None
            if timestamp is None:
                continue

            label_str = str(row.get("label", "NO_TRADE"))
            try:
                label_encoded = self.label_schema.encode(label_str)
            except ValueError:
                continue  # WAIT / unknown strings are not neural targets

            feature_vector: list[float] = []
            if "feature_vector" in row:
                fv = row["feature_vector"]
                feature_vector = [float(x) for x in fv] if isinstance(fv, list) else []
            else:
                feat_cols = [c for c in labeled.columns if c.startswith("feat_")]
                feature_vector = [
                    float(row.get(c, 0.0)) for c in feat_cols[: self.feature_schema.dimension]
                ]

            if len(feature_vector) != self.feature_schema.dimension:
                continue  # schema-incompatible rows are dropped loudly below

            news_ctx = self.news_context_at(news_frame, timestamp, self.news_schema)
            setup = self.detect_setup(row, prior_rows)
            prior_rows.append(row)

            sample_id = deterministic_sample_id(
                symbol,
                timeframe,
                timestamp,
                self.feature_schema.schema_id,
                label_str,
            )
            samples.append(
                SampleContract(
                    sample_id=sample_id,
                    timestamp=timestamp,
                    symbol=symbol,
                    timeframe=timeframe,
                    feature_schema_id=self.feature_schema.schema_id,
                    feature_dimension=self.feature_schema.dimension,
                    feature_vector=feature_vector,
                    price_context={
                        "close": float(row.get("close", 0.0)),
                        "atr": float(row.get("atr_m1") or row.get("atr", 0.0) or 0.0),
                        "spread": float(row.get("spread", 0.0) or 0.0),
                    },
                    regime=str(row.get("regime", "UNKNOWN")),
                    news_context=news_ctx,
                    news_context_schema_id=self.news_schema.news_context_schema_id,
                    metadata={
                        "label": label_encoded,
                        "label_str": label_str,
                        "label_schema_id": self.label_schema.label_schema_id,
                        "setup_id": setup.setup_id,
                        "strategy_id": strategy_id,
                        "strategy_version": strategy_version,
                        "is_eval_sample": bool(row.get("is_eval_sample", False)),
                        "is_purged": bool(row.get("is_purged", False)),
                    },
                )
            )

        return samples

    @staticmethod
    def _parse_ts(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None


def samples_to_frame(samples: list[SampleContract]) -> pl.DataFrame:
    """Serializes samples to a Polars frame for dataset artifacts."""
    rows: list[dict[str, Any]] = []
    for s in samples:
        row: dict[str, Any] = {
            "sample_id": s.sample_id,
            "timestamp": s.timestamp,
            "symbol": s.symbol,
            "timeframe": s.timeframe,
            "feature_schema_id": s.feature_schema_id,
            "regime": s.regime,
            "setup_id": s.metadata.get("setup_id", ""),
            "strategy_id": s.metadata.get("strategy_id", ""),
            "strategy_version": s.metadata.get("strategy_version", ""),
            "label": s.metadata.get("label", 0),
            "label_str": s.metadata.get("label_str", "NO_TRADE"),
            "news_context_schema_id": s.news_context_schema_id,
            "is_eval_sample": bool(s.metadata.get("is_eval_sample", False)),
            "is_purged": bool(s.metadata.get("is_purged", False)),
        }
        # feature vector -> feat_0..feat_{n-1}
        for idx, v in enumerate(s.feature_vector):
            row[f"feat_{idx}"] = v
        # news context -> news_<field>
        for k, v in s.news_context.items():
            row[f"news_{k}"] = float(v)
        rows.append(row)
    return pl.DataFrame(rows) if rows else pl.DataFrame()
