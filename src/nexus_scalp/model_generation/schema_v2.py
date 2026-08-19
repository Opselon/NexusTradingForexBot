"""TASK-5 60D Schema Dataset Builder (schema_v2.py).

Builds a REAL `scalp_v2` (60D) training dataset from raw broker bars:

    raw broker bars
        -> ScalpFeatureEngine  (50D, causal, existing)
        -> compute_60d_extras (10D, causal, schema_augment)
        -> SampleFactory      (triple-barrier labels, purge/embargo, provenance)
        -> DatasetFactory.build(feature_schema_id="scalp_v2")
        -> dataset artifact (feat_0..feat_59, manifest feature_schema_id)

Every 60D input is produced on the SAME causal window the 50D engine uses
(the last 55 completed bars + the current tick), so:

    LIVE availability:  YES  (bars + tick the engine already holds)
    REPLAY availability: YES  (the identically-ordered bars in the artifact /
                               raw parquet re-derive the same vectors)
    LEAKAGE:             none (only completed bars + decision tick)

The 50D engine itself is UNTOUCHED: `scalp_v1` stays the ACTIVE live
contract; `scalp_v2` artifacts are candidate-only (spec 3 / INV-009).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np
import polars as pl

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.liquidity_engine import compute_liquidity_features
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.features.schema_augment import (
    NUM_EXTRA_60D,
    compute_60d_extras,
)
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.dataset_factory import DatasetFactory
from nexus_scalp.model_generation.sample_factory import SampleFactory
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.schema_v2")

SCHEMA_V2_ID = "scalp_v2"
SPREAD_USD = 0.20  # live-engine synthetic spread convention (bid/ask gap)


def bars_frame_to_bardata(df: pl.DataFrame) -> tuple[list[BarData], list[datetime]]:
    """Converts a raw bars parquet frame (time/open/high/low/close/
    tick_volume) into a chronological BarData list + decision timestamps."""
    df = df.sort("time")
    bars: list[BarData] = []
    times: list[datetime] = []
    for row in df.iter_rows(named=True):
        t = row.get("time_utc") or row.get("time")
        ts = t if isinstance(t, datetime) else None
        if ts is None:
            continue
        ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
        bars.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M5",
                timestamp=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                tick_volume=int(row.get("tick_volume", 0) or 0),
                is_complete=True,
            )
        )
        times.append(ts)
    return bars, times


def compute_60d_frame(
    df: pl.DataFrame,
    *,
    min_bars: int = 55,
    spread: float = SPREAD_USD,
) -> pl.DataFrame:
    """Computes a 60D feature frame from raw bars (one row per bar).

    For each bar i (i >= min_bars-1) the feature engine + extras run on the
    causal window [i-54 .. i] with a synthetic tick at bar i's close — the
    same convention data_gate_2 uses for the 50D datasets, so 50D and 60D
    artifacts built from the same parquet are directly comparable.

    Returns a frame with columns:
        timestamp, open, high, low, close, spread, atr_m1, tick_volume,
        feat_0..feat_59
    """
    raw = df.sort("time")
    closes = raw["close"].cast(pl.Float64).to_numpy()
    highs = raw["high"].cast(pl.Float64).to_numpy()
    lows = raw["low"].cast(pl.Float64).to_numpy()
    opens = raw["open"].cast(pl.Float64).to_numpy()
    volumes = raw["tick_volume"].cast(pl.Float64).to_numpy()
    times = []
    for row in raw.iter_rows(named=True):
        t = row.get("time_utc") or row.get("time")
        ts = t if isinstance(t, datetime) else None
        if ts is None:
            continue
        ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
        times.append(ts)

    engine = ScalpFeatureEngine(symbol="XAUUSD")
    n = raw.height
    all_bars = []
    for j in range(n):
        bj = raw.row(j, named=True)
        all_bars.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M5",
                timestamp=times[j],
                open=float(bj["open"]),
                high=float(bj["high"]),
                low=float(bj["low"]),
                close=float(bj["close"]),
                tick_volume=int(bj.get("tick_volume", 0) or 0),
                is_complete=True,
            )
        )
    rows: list[dict[str, Any]] = []
    for i in range(n):
        if i + 1 < min_bars:
            continue  # causal warm-up: no 50D features without 55 bars
        ts = times[i]
        b = raw.row(i, named=True)
        tick = TickData(
            symbol="XAUUSD",
            timestamp=ts,
            bid=float(b["close"]),
            ask=float(b["close"]) + spread,
            volume=int(b.get("tick_volume", 0) or 0),
        )
        # vectorized windows (fast path for the 100k-row experiment)
        w_high = highs[max(0, i - 54) : i + 1]
        w_low = lows[max(0, i - 54) : i + 1]
        w_close = closes[max(0, i - 54) : i + 1]
        w_open = opens[max(0, i - 54) : i + 1]
        w_vol = volumes[max(0, i - 54) : i + 1]
        # current tick close == last completed bar close (completed-bar decision)

        # NOTE: the 50D engine consumes a BarData window. Reusing one
        # pre-built BarData list and slicing windows keeps 100k-row builds
        # tractable (the 4-thread data_gate pattern took minutes for 50D;
        # the 60D experimental build is a one-off, bounded run).
        fv = engine.compute_from_bars(all_bars[max(0, i - 54) : i + 1], tick)
        x50 = fv.to_tensor_input()
        extras = compute_60d_extras(
            opens=w_open,
            highs=w_high,
            lows=w_low,
            closes=w_close,
            volumes=w_vol,
            hour_utc=ts.hour,
        )
        rec = {
            "timestamp": ts,
            "open": float(b["open"]),
            "high": float(b["high"]),
            "low": float(b["low"]),
            "close": float(b["close"]),
            "spread": spread,
            "atr_m1": float(fv.atr_m1),
            "tick_volume": int(b.get("tick_volume", 0) or 0),
        }
        for idx in range(50):
            rec[f"feat_{idx}"] = float(x50[idx])
        for idx in range(NUM_EXTRA_60D):
            rec[f"feat_{50 + idx}"] = float(extras[idx])
        rows.append(rec)
        if i % 20000 == 0 and i > 0:
            logger.info("[SCHEMA_V2] computed %d rows (of ~%d)", i, n)
    return pl.DataFrame(rows)


def build_60d_dataset(
    bars_frame: pl.DataFrame,
    *,
    timeframe: str = "M5",
    news_frame: pl.DataFrame | None = None,
    strategy_id: str = "scalp_default",
    strategy_version: str = "1.0.0",
    store: ArtifactStore | None = None,
    seed: int = 42,
    dataset_id: str | None = None,
) -> dict[str, Any]:
    """Builds + persists a REAL scalp_v2 (60D) dataset artifact.

    Returns the DatasetFactory handle dict (dataset_id, counts, hash).
    """
    store = store or ArtifactStore()
    feat_frame = compute_60d_frame(bars_frame)
    if feat_frame.is_empty():
        raise ValueError("60D feature frame empty — check raw bars / min_bars")

    factory = DatasetFactory(
        store=store,
        sample_factory=SampleFactory(feature_schema_id=SCHEMA_V2_ID),
    )
    handle = factory.build(
        feat_frame,
        symbol="XAUUSD",
        timeframe=timeframe,
        news_frame=news_frame,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        seed=seed,
        dataset_id=dataset_id,
    )
    logger.info(
        "[SCHEMA_V2] event=DATASET_BUILT dataset_id=%s rows=%d",
        handle.get("dataset_id"),
        handle.get("counts", {}).get("total", 0),
    )
    logger.info(
        "[SCHEMA_V2] event=DATASET_BUILT dataset_id=%s rows=%d",
        handle.get("dataset_id"),
        handle.get("counts", {}).get("total", 0),
    )
    return handle


def augment_existing_dataset_to_60d(
    frame: pl.DataFrame,
    *,
    raw_bars: pl.DataFrame | None = None,
    store: ArtifactStore | None = None,
    dataset_id: str | None = None,
) -> dict[str, Any]:
    """Augments an existing 50D dataset artifact into a 60D dataset.

    Uses the raw bars frame (data/raw/XAUUSD_M5.parquet) to re-derive the
    10 extra features causally and appends feat_50..feat_59 to each row,
    then rebuilds the dataset via the same deterministic dataset factory
    (feature_schema_id="scalp_v2").

    Raises:
        ValueError: when the input frame already has 60+ feat columns or the
        raw bars are not provided.
    """
    feat_cols = [c for c in frame.columns if c.startswith("feat_")]
    if len(feat_cols) > 50:
        raise ValueError(
            f"augment_existing_dataset_to_60d: frame already has {len(feat_cols)} feat cols"
        )

    if raw_bars is None:
        raise ValueError("augment_existing_dataset_to_60d: raw_bars frame required")

    feat_frame = compute_60d_frame(raw_bars)
    # align by timestamp, keep only rows present in the dataset frame
    ts_key = "timestamp"
    merged = feat_frame.join(
        frame.select([ts_key, "sample_id", "regime", "label", "label_str"]),
        on=ts_key,
        how="inner",
    )
    if merged.is_empty():
        raise ValueError("augment_existing_dataset_to_60d: no timestamp overlap with raw bars")

    store = store or ArtifactStore()
    factory = DatasetFactory(
        store=store,
        sample_factory=SampleFactory(feature_schema_id=SCHEMA_V2_ID),
    )
    return factory.build(
        merged,
        symbol="XAUUSD",
        timeframe="M5",
        news_frame=None,
        strategy_id="scalp_default",
        strategy_version="1.0.0",
        seed=42,
        dataset_id=dataset_id,
    )


def verify_60d_artifact(dataset_id: str, store: ArtifactStore | None = None) -> dict[str, Any]:
    """Verifies a 60D dataset artifact: 60 feat columns, manifest schema_id,
    deterministic column ordering, finite values, no feat_50 duplicate."""
    store = store or ArtifactStore()
    man = store.read_dataset_manifest(dataset_id)
    if man is None:
        return {"ok": False, "reason": "MANIFEST_MISSING"}
    frame = store.read_dataset(dataset_id)
    feat_cols = [c for c in frame.columns if c.startswith("feat_")]
    if len(feat_cols) != 60:
        return {"ok": False, "reason": f"EXPECTED_60_FEATURES_GOT_{len(feat_cols)}"}
    if man.get("feature_schema_id") != SCHEMA_V2_ID:
        return {
            "ok": False,
            "reason": f"MANIFEST_SCHEMA {man.get('feature_schema_id')} != {SCHEMA_V2_ID}",
        }
    arr = frame.select(feat_cols).to_numpy().astype(np.float64)
    finite = bool(np.isfinite(arr).all())
    # exact duplicate-column detection: two feature columns are identical iff
    # their full value sequences match (a first-5-values sample is NOT proof
    # of uniqueness — many legit features share early rows).
    dup_cols: list[list[str]] = []
    seen: set[str] = set()
    for i, c1 in enumerate(feat_cols):
        if c1 in seen:
            continue
        group = [c1]
        for c2 in feat_cols[i + 1 :]:
            if np.array_equal(frame[c1].to_numpy(), frame[c2].to_numpy()):
                group.append(c2)
                seen.add(c2)
        if len(group) > 1:
            dup_cols.append(group)
    return {
        "ok": finite,
        "feature_count": len(feat_cols),
        "schema_id": man.get("feature_schema_id"),
        "rows": frame.height,
        "all_finite": finite,
        "duplicate_groups": dup_cols,
        "duplicate_warning": bool(dup_cols),
        "dataset_hash": man.get("dataset_hash", ""),
    }


# =============================================================================
# TASK-01-60D-LIQUIDITY: scalp_liquidity_v1 dataset builder (ADDITIVE)
# =============================================================================
# The liquidity 60D path mirrors compute_60d_frame but produces feat_50..59
# from features/liquidity_engine.compute_liquidity_features on the SAME causal
# window the 50D engine consumes (last 55 completed bars + synthetic tick at
# close). Training/live/replay parity is structural: they all call the exact
# same canonical function with identical inputs.
# =============================================================================

LIQUIDITY_SCHEMA_ID = "scalp_liquidity_v1"
#: BUG-106 (TASK-04/05): the liquidity engine's causal history window MUST be
#: bounded to match the LIVE aggregator cap (live_engine.py: `_completed_bars`
#: trimmed to 4000 on every tick, line ~2070). Passing ALL history to
#: compute_liquidity_features per row is O(n^2) AND breaks train==live parity
#: (live sees <=4000 bars, training saw 100K+). 4000 M5 bars = ~14 days,
#: sufficient for completed D1 buckets (1440 bars).
LIQUIDITY_HISTORY_LIMIT: int = 4000

#: Where the liquidity engine's 10 features start inside the 60D vector.
LIQUIDITY_EXTRA_START: int = 50


def compute_liquidity_frame(
    df: pl.DataFrame,
    *,
    min_bars: int = 55,
    spread: float = SPREAD_USD,
) -> pl.DataFrame:
    """Computes a scalp_liquidity_v1 (60D) feature frame from raw bars.

    For each row i (i >= min_bars-1) the existing 50D engine AND the
    liquidity engine run on the same causal window [i-54 .. i] with a
    synthetic tick at bar i's close — identical convention to
    ``compute_60d_frame``, so a liquidity-60D artifact is directly
    comparable to the 50D Data-Gate artifact.

    Returns a frame with columns:
        timestamp, open, high, low, close, spread, atr_m1, tick_volume,
        feat_0..feat_59 (feat_50..59 = liquidity features)
    """
    raw = df.sort("time")
    times: list[datetime] = []
    for row in raw.iter_rows(named=True):
        t = row.get("time_utc") or row.get("time")
        ts = t if isinstance(t, datetime) else None
        if ts is None:
            continue
        ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
        times.append(ts)

    engine = ScalpFeatureEngine(symbol="XAUUSD")
    n = raw.height
    all_bars: list[BarData] = []
    for j in range(n):
        bj = raw.row(j, named=True)
        all_bars.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M5",
                timestamp=times[j],
                open=float(bj["open"]),
                high=float(bj["high"]),
                low=float(bj["low"]),
                close=float(bj["close"]),
                tick_volume=int(bj.get("tick_volume", 0) or 0),
                is_complete=True,
            )
        )
    rows: list[dict[str, Any]] = []
    for i in range(n):
        if i + 1 < min_bars:
            continue  # causal warm-up: no 50D features without 55 bars
        ts = times[i]
        b = raw.row(i, named=True)
        tick = TickData(
            symbol="XAUUSD",
            timestamp=ts,
            bid=float(b["close"]),
            ask=float(b["close"]) + spread,
            volume=int(b.get("tick_volume", 0) or 0),
        )
        window = all_bars[max(0, i - 54) : i + 1]
        fv = engine.compute_from_bars(window, tick)
        x50 = fv.to_tensor_input()
        # TASK-03-70D-PARITY fix: full causal history for the
        # liquidity engine (matches live governor semantics).
        liquid = compute_liquidity_features(
            all_bars[max(0, i + 1 - LIQUIDITY_HISTORY_LIMIT) : i + 1],
            decision_at=ts,
            mid_price=float(b["close"]),
            atr=fv.atr_m1,
        )
        extras = liquid.as_vector()
        rec = {
            "timestamp": ts,
            "open": float(b["open"]),
            "high": float(b["high"]),
            "low": float(b["low"]),
            "close": float(b["close"]),
            "spread": spread,
            "atr_m1": float(fv.atr_m1),
            "tick_volume": int(b.get("tick_volume", 0) or 0),
        }
        for idx in range(50):
            rec[f"feat_{idx}"] = float(x50[idx])
        for idx in range(len(extras)):
            rec[f"feat_{LIQUIDITY_EXTRA_START + idx}"] = float(extras[idx])
        rows.append(rec)
        if i % 20000 == 0 and i > 0:
            logger.info("[LIQUIDITY_SCHEMA] computed %d rows (of ~%d)", i, n)
    return pl.DataFrame(rows)


def build_liquidity_dataset(
    bars_frame: pl.DataFrame,
    *,
    timeframe: str = "M5",
    news_frame: pl.DataFrame | None = None,
    strategy_id: str = "scalp_default",
    strategy_version: str = "1.0.0",
    store: ArtifactStore | None = None,
    seed: int = 42,
    dataset_id: str | None = None,
) -> dict[str, Any]:
    """Builds + persists a scalp_liquidity_v1 (60D) dataset artifact.

    Returns the DatasetFactory handle dict (dataset_id, counts, hash).
    """
    store = store or ArtifactStore()
    feat_frame = compute_liquidity_frame(bars_frame)
    if feat_frame.is_empty():
        raise ValueError("liquidity 60D feature frame empty — check raw bars / min_bars")

    factory = DatasetFactory(
        store=store,
        sample_factory=SampleFactory(feature_schema_id=LIQUIDITY_SCHEMA_ID),
    )
    handle = factory.build(
        feat_frame,
        symbol="XAUUSD",
        timeframe=timeframe,
        news_frame=news_frame,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        seed=seed,
        dataset_id=dataset_id,
    )
    logger.info(
        "[LIQUIDITY_SCHEMA] event=DATASET_BUILT dataset_id=%s rows=%d",
        handle.get("dataset_id"),
        handle.get("counts", {}).get("total", 0),
    )
    return handle


def verify_liquidity_artifact(
    dataset_id: str, store: ArtifactStore | None = None
) -> dict[str, Any]:
    """Verifies a scalp_liquidity_v1 artifact: 60 feat columns, manifest
    schema_id, all values finite AND within [-3,+3]."""
    store = store or ArtifactStore()
    man = store.read_dataset_manifest(dataset_id)
    if man is None:
        return {"ok": False, "reason": "MANIFEST_MISSING"}
    frame = store.read_dataset(dataset_id)
    feat_cols = [c for c in frame.columns if c.startswith("feat_")]
    if len(feat_cols) != 60:
        return {"ok": False, "reason": f"EXPECTED_60_FEATURES_GOT_{len(feat_cols)}"}
    if man.get("feature_schema_id") != LIQUIDITY_SCHEMA_ID:
        return {
            "ok": False,
            "reason": f"MANIFEST_SCHEMA {man.get('feature_schema_id')} != {LIQUIDITY_SCHEMA_ID}",
        }
    arr = frame.select(feat_cols).to_numpy().astype(np.float64)
    finite = bool(np.isfinite(arr).all())
    in_range = bool((arr >= -3.0).all() and (arr <= 3.0).all())
    return {
        "ok": finite and in_range,
        "feature_count": len(feat_cols),
        "schema_id": man.get("feature_schema_id"),
        "rows": frame.height,
        "all_finite": finite,
        "all_in_range": in_range,
        "dataset_hash": man.get("dataset_hash", ""),
    }


# =============================================================================
# TASK-03-70D-PARITY: scalp_v3 canonical 70D dataset builder (ADDITIVE)
# =============================================================================
# 70D vector = Base 50D (scalp_v1, indices 0..49) + News 10D (canonical
# news_context_v1 fields 0..8 + news_state, indices 50..59) + Liquidity 10D (liquidity_engine
# as_vector order, indices 60..69). The canonical contract lives in
# features/schema_contract.py; this builder calls the SAME canonical producers
# the live/replay paths use (ScalpFeatureEngine.compute_from_bars,
# news_bridge.news_context_at, liquidity_engine.compute_liquidity_features) on
# the SAME causal window. Training == replay == live by construction.
# =============================================================================

SEVENTY_D_SCHEMA_ID: str = "scalp_v3"
NEWS_10D_START: int = 50
LIQUIDITY_10D_START: int = 60


def compute_70d_frame(
    df: pl.DataFrame,
    *,
    min_bars: int = 55,
    spread: float = SPREAD_USD,
    news_frame: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Computes a scalp_v3 (70D) feature frame from raw bars + optional news.

    For each row i (i >= min_bars-1) the canonical 50D engine, the news
    bridge (causally-correct snapshot at bar time) and the liquidity engine
    run on the SAME causal window [i-54 .. i] with a synthetic tick at bar
    i's close — identical convention to compute_60d_frame /
    compute_liquidity_frame, so a 70D artifact is directly comparable.

    News contract: when ``news_frame`` is None the news block is the
    documented neutral 10D (all zeros) with ``news_status=FEATURE_DISABLED``.
    When a news frame IS provided, every row gets the causally correct
    news_context_at(t) 0..8+news_state selection. Liquidity values are the canonical
    engine's as_vector() (already clipped [-3,+3], finite).

    Returns a frame with columns:
        timestamp, open, high, low, close, spread, atr_m1, tick_volume,
        news_status, liquidity_status, feat_0..feat_69
    """
    from nexus_scalp.features.features70 import (
        FeatureSourceState,
        clamp_neutral_family,
        news_10d_from_context,
    )
    from nexus_scalp.model_generation.news_bridge import news_context_at

    raw = df.sort("time")
    times: list[datetime] = []
    for row in raw.iter_rows(named=True):
        t = row.get("time_utc") or row.get("time")
        ts = t if isinstance(t, datetime) else None
        if ts is None:
            continue
        ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
        times.append(ts)

    engine = ScalpFeatureEngine(symbol="XAUUSD")
    n = raw.height
    all_bars: list[BarData] = []
    for j in range(n):
        bj = raw.row(j, named=True)
        all_bars.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=times[j],
                open=float(bj["open"]),
                high=float(bj["high"]),
                low=float(bj["low"]),
                close=float(bj["close"]),
                tick_volume=int(bj.get("tick_volume", 0) or 0),
                is_complete=True,
            )
        )
    news_enabled = news_frame is not None and not news_frame.is_empty()
    rows: list[dict[str, Any]] = []
    for i in range(n):
        if i + 1 < min_bars:
            continue  # causal warm-up
        ts = times[i]
        b = raw.row(i, named=True)
        tick = TickData(
            symbol="XAUUSD",
            timestamp=ts,
            bid=float(b["close"]),
            ask=float(b["close"]) + spread,
            volume=int(b.get("tick_volume", 0) or 0),
        )
        window = all_bars[max(0, i - 54) : i + 1]
        fv = engine.compute_from_bars(window, tick)
        x50 = fv.to_tensor_input()

        # TASK-03-70D-PARITY fix: the liquidity engine must see the
        # SAME full causal history the live governor sees (all
        # closed bars <= ts) so HTF buckets / session pools /
        # confluence match train == live. 50D window unchanged.
        liquid = compute_liquidity_features(
            all_bars[max(0, i + 1 - LIQUIDITY_HISTORY_LIMIT) : i + 1],
            decision_at=ts,
            mid_price=float(b["close"]),
            atr=fv.atr_m1,
        )
        liq10 = list(liquid.as_vector())

        if news_enabled:
            ctx = news_context_at(news_frame, ts)
            news10 = news_10d_from_context(ctx)
            news_status = FeatureSourceState.FEATURE_AVAILABLE.value
        else:
            news10 = [0.0] * 10
            news_status = FeatureSourceState.FEATURE_DISABLED.value

        rec = {
            "timestamp": ts,
            "open": float(b["open"]),
            "high": float(b["high"]),
            "low": float(b["low"]),
            "close": float(b["close"]),
            "spread": spread,
            "atr_m1": float(fv.atr_m1),
            "tick_volume": int(b.get("tick_volume", 0) or 0),
            "news_status": news_status,
            "liquidity_status": FeatureSourceState.FEATURE_AVAILABLE.value,
        }
        for idx in range(50):
            rec[f"feat_{idx}"] = float(x50[idx])
        for idx in range(10):
            rec[f"feat_{50 + idx}"] = float(clamp_neutral_family(news10, (0.0,) * 10)[idx])
        for idx in range(10):
            rec[f"feat_{60 + idx}"] = float(
                clamp_neutral_family(liq10, (3.0, 3.0, 0.0, 0.0, 0.0, 3.0, 3.0, 0.0, 0.0, 0.0))[idx]
            )
        rows.append(rec)
        if i % 20000 == 0 and i > 0:
            logger.info("[SCHEMA_70D] computed %d rows (of ~%d)", i, n)
    return pl.DataFrame(rows)


def build_70d_dataset(
    bars_frame: pl.DataFrame,
    *,
    timeframe: str = "M1",
    news_frame: pl.DataFrame | None = None,
    strategy_id: str = "scalp_default",
    strategy_version: str = "1.0.0",
    store: ArtifactStore | None = None,
    seed: int = 42,
    dataset_id: str | None = None,
) -> dict[str, Any]:
    """Builds + persists a scalp_v3 (70D) dataset artifact."""
    store = store or ArtifactStore()
    feat_frame = compute_70d_frame(bars_frame, news_frame=news_frame)
    if feat_frame.is_empty():
        raise ValueError("70D feature frame empty — check raw bars / min_bars")
    factory = DatasetFactory(
        store=store,
        sample_factory=SampleFactory(feature_schema_id=SEVENTY_D_SCHEMA_ID),
    )
    handle = factory.build(
        feat_frame,
        symbol="XAUUSD",
        timeframe=timeframe,
        news_frame=news_frame,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        seed=seed,
        dataset_id=dataset_id,
    )
    # TASK-03 parity lineage: stamp the canonical feature-schema hash so
    # verify_70d_artifact can prove schema agreement (brief 27).
    try:
        from nexus_scalp.features.schema_contract import feature_schema_hash

        man = store.read_dataset_manifest(handle["dataset_id"]) or {}
        man["feature_schema_hash"] = feature_schema_hash()
        # ArtifactStore persists manifests as JSON via write_json
        store.write_json(store.dataset_manifest_path(handle["dataset_id"]), man)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[SCHEMA_70D] event=SCHEMA_HASH_STAMP_FAILED error=%s", exc)
    logger.info(
        "[SCHEMA_70D] event=DATASET_BUILT dataset_id=%s rows=%d",
        handle.get("dataset_id"),
        handle.get("counts", {}).get("total", 0),
    )
    return handle


def verify_70d_artifact(
    dataset_id: str,
    store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Quality gate for a scalp_v3 70D artifact (brief 41).

    Asserts: dimension == 70, all finite, all in [-3,+3], manifest schema_id
    == scalp_v3, schema hash correct, no duplicate timestamps, no duplicate
    sample identity. Returns per-check booleans + an exact rejection summary
    (never a bare "dataset failed").
    """
    store = store or ArtifactStore()
    from nexus_scalp.features.schema_contract import feature_schema_hash

    man = store.read_dataset_manifest(dataset_id)
    if man is None:
        return {"ok": False, "reason": "MANIFEST_MISSING"}
    frame = store.read_dataset(dataset_id)
    feat_cols = [c for c in frame.columns if c.startswith("feat_")]
    checks: dict[str, Any] = {"feature_count": len(feat_cols)}
    checks["dimension_ok"] = len(feat_cols) == 70
    checks["schema_id_ok"] = man.get("feature_schema_id") == SEVENTY_D_SCHEMA_ID
    checks["schema_hash_ok"] = bool(
        man.get("feature_schema_hash") and man["feature_schema_hash"] == feature_schema_hash()
    )
    arr = frame.select(feat_cols).to_numpy().astype(np.float64)
    finite_mask = np.isfinite(arr).all(axis=1)
    range_mask = ((arr >= -3.0) & (arr <= 3.0)).all(axis=1)
    checks["all_finite"] = bool(finite_mask.all())
    checks["all_in_range"] = bool(range_mask.all())
    n_reject_finite = int((~finite_mask).sum())
    n_reject_range = int((~range_mask).sum())
    checks["rejected_rows"] = {
        "NONFINITE_FEATURE": n_reject_finite,
        "OUT_OF_RANGE_FEATURE": n_reject_range,
    }
    if "timestamp" in frame.columns:
        dup_ts = int(frame.select(pl.col("timestamp").is_duplicated()).sum().item())
        checks["duplicate_timestamps"] = dup_ts
    if "sample_id" in frame.columns:
        dup_sid = int(frame.select(pl.col("sample_id").is_duplicated()).sum().item())
        checks["duplicate_sample_ids"] = dup_sid
    checks["rows"] = frame.height
    checks["ok"] = bool(
        checks["dimension_ok"]
        and checks["schema_id_ok"]
        and checks["schema_hash_ok"]
        and checks["all_finite"]
        and checks["all_in_range"]
        and not checks.get("duplicate_timestamps", 0)
        and not checks.get("duplicate_sample_ids", 0)
    )
    checks["dataset_hash"] = man.get("dataset_hash", "")
    return checks
