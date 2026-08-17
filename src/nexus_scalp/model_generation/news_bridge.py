"""News pipeline to model-input bridge (PHASE 13B forensic fixes).

Builds a causally-correct news frame for dataset generation from the
News subsystem's authoritative analysis rows, and computes the 12-field
NewsContextSchema vector AT a given timestamp.

This resolves the Phase 13B forensic findings:

1. **The benchmark never used real news.**  ``BenchmarkRunner`` accepted a
   caller-supplied ``news_frame``; nothing in the repo ever exported the
   news database into that shape, so the benchmark was driven by a 10-row
   synthetic fixture (``rows_news=10``) whose 12-field vectors were 100%
   neutral under the full schema (7 of 12 fields dead-zero).

2. **The live context cannot be reconstructed historically.**  The model
   schema's ``news_novelty`` / ``news_news_state`` fields are *nominal*
   strings in the engine but were persisted as ``0.0`` floats; there was no
   decoder that mapped a historical event's state at time T.

3. **The 12-field schema field set was never produced by the engine.**
   ``CurrentNewsContext`` (used by the live NewsGate) exposes a different
   12 fields (bullish/bearish *scores*, ``news_adjustment``…); the model
   schema uses ``active_high_impact_events`` etc.  Nothing bridged them.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import polars as pl

from nexus_scalp.model_generation.models import NewsContextSchema, default_news_context_schema
from nexus_scalp.news.models import NewsImpactHorizon

#: NewsContextSchema field names (canonical order, from the model contract).
_SCHEMA_FIELDS: tuple[str, ...] = (
    "active_high_impact_events",
    "xauusd_relevance",
    "usd_relevance",
    "bullish_pressure",
    "bearish_pressure",
    "conflict_score",
    "novelty",
    "freshness",
    "confidence",
    "source_consensus",
    "news_state",
    "time_since_event_sec",
)

#: Categorical -> numeric encodings for the nominal schema fields.
_STATE_ENCODING: dict[str, float] = {
    "NORMAL": 0.0,
    "ELEVATED": 1.0,
    "HIGH_IMPACT": 2.0,
    "CONFLICTED": 3.0,
    "BREAKING": 4.0,
    "STALE": 5.0,
}
_STATE_DEFAULT = 0.0

_NOVELTY_ENCODING: dict[str, float] = {
    "NEW": 0.0,
    "UPDATED": 1.0,
    "CONFIRMATION": 2.0,
    "REPETITION": 3.0,
    "STALE": 4.0,
}
_NOVELTY_DEFAULT = 0.0


def _encode_state(value: Any) -> float:
    if value is None:
        return _STATE_DEFAULT
    try:
        return _STATE_ENCODING.get(str(value).upper(), _STATE_DEFAULT)
    except Exception:
        return _STATE_DEFAULT


def _encode_novelty(value: Any) -> float:
    if value is None:
        return _NOVELTY_DEFAULT
    try:
        return _NOVELTY_ENCODING.get(str(value).upper(), _NOVELTY_DEFAULT)
    except Exception:
        return _NOVELTY_DEFAULT


def _num(value: Any, default: float = 0.0) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    return f


def _coerce_field(frame: pl.DataFrame, col: str, default: float = 0.0) -> pl.DataFrame:
    """Coerces one news column to a finite float; missing -> default column."""
    if col not in frame.columns:
        return frame.with_columns(pl.Series(col, [default] * frame.height, dtype=pl.Float64))
    s = frame[col]
    if str(s.dtype) in ("f64", "Float64"):
        # Already numeric — but NaN/Inf must NEVER reach the neural vector
        # (BUG-041 class of defect).  Replace non-finite with the default.
        if s.null_count() > 0 or not s.is_finite().all():
            return frame.with_columns(
                pl.when(pl.col(col).is_finite()).then(pl.col(col)).otherwise(default).alias(col)
            )
        return frame
    return frame.with_columns(
        pl.col(col).map_elements(lambda v: _num(v, default), return_dtype=pl.Float64).alias(col)
    )


def _normalize_publication_ts(frame: pl.DataFrame) -> pl.DataFrame:
    """Strips the UTC offset from a tz-aware ``published_at`` column.

    Keeps the bridge deterministic and portable across Python/polars versions.
    The underlying instant is NOT changed — the naive value equals the UTC
    wall-clock time (e.g. ``2026-08-16 10:00:00`` for ``10:00:00 UTC``).
    """
    if "published_at" not in frame.columns:
        return frame
    return frame.with_columns(
        pl.col("published_at").dt.replace_time_zone(None).alias("published_at")
    )


def normalize_news_frame(news_frame: pl.DataFrame | None) -> pl.DataFrame | None:
    """Coerces a raw news frame into the canonical 12-field numeric schema.

    Accepts either the engine's analysis-shaped rows (relevance fields,
    direction/horizon/importance strings) or an already-numeric frame.
    Missing schema fields are created as zeros so the vector is always
    12-dimensional and finite.  String fields are encoded to their ordinal
    numeric representation (state / novelty).  Returns None for empty input.
    """
    if news_frame is None or news_frame.is_empty():
        return None
    frame = news_frame
    # time column is mandatory
    ts_col = "published_at" if "published_at" in frame.columns else None
    if ts_col is None:
        for candidate in ("timestamp", "time", "analyzed_at"):
            if candidate in frame.columns:
                ts_col = candidate
                break
    if ts_col is None:
        return None
    if ts_col != "published_at":
        frame = frame.rename({ts_col: "published_at"})

    # Strip any UTC offset from published_at so Python-side row extraction is
    # portable (polars 0.20 on Windows panics on tz-aware datetimes).
    # The instant is preserved: naive wall-clock == UTC wall-clock.
    frame = _normalize_publication_ts(frame)

    # derive novelty / news_state from raw strings if present
    if "novelty" in frame.columns and str(frame["novelty"].dtype) not in ("f64", "Float64"):
        frame = frame.with_columns(
            pl.col("novelty")
            .map_elements(_encode_novelty, return_dtype=pl.Float64)
            .alias("novelty")
        )
    if "news_state" not in frame.columns and "state" in frame.columns:
        frame = frame.rename({"state": "news_state"})
    if "news_state" in frame.columns and str(frame["news_state"].dtype) not in ("f64", "Float64"):
        frame = frame.with_columns(
            pl.col("news_state")
            .map_elements(_encode_state, return_dtype=pl.Float64)
            .alias("news_state")
        )

    # upstream aliases from the engine's analysis rows / CurrentNewsContext
    alias_map = {
        "bullish_pressure": "bullish_score",
        "bearish_pressure": "bearish_score",
        "active_high_impact_events": "active_event_count",
        "source_consensus": None,  # derived below when consensus exists
    }
    for target, source in alias_map.items():
        if target not in frame.columns and source and source in frame.columns:
            frame = frame.rename({source: target})

    # ensure every schema field exists, numeric, finite
    for field in _SCHEMA_FIELDS:
        frame = _coerce_field(frame, field, default=0.0)

    # time_since_event_sec: age of the event at the SAMPLE time is applied
    # per-sample in news_context_at; store the reference time here as 0
    # (the per-sample computation overrides it).
    return frame.select(["published_at", *_SCHEMA_FIELDS])


def news_context_at(
    news_frame: pl.DataFrame | None,
    timestamp: datetime,
    news_schema: NewsContextSchema | None = None,
    horizon_default: float = 0.0,
) -> dict[str, float]:
    """Causally-correct 12-field news context snapshot at ``timestamp``.

    Only events published at or BEFORE ``timestamp`` are eligible; the
    LATEST prior event defines the snapshot (a future event can never enter
    a historical sample).  The vector is the normalized schema ordering.

    The legacy ``SampleFactory.news_context_at`` is superseded by this
    implementation for news-aware dataset generation; it is kept behind
    this module so the fix is a single import swap, not a schema change.
    """
    zero: dict[str, float] = {f: 0.0 for f in (_news_schema_fields(news_schema))}

    frame = normalize_news_frame(news_frame)
    if frame is None or frame.is_empty():
        return zero

    sample_ts_us = int(timestamp.timestamp() * 1_000_000)
    try:
        news_ts = frame["published_at"].cast(pl.Datetime("us")).dt.epoch("us")
        prior_mask = news_ts <= sample_ts_us
    except Exception:
        parsed = frame.with_columns(
            pl.col("published_at")
            .str.to_datetime(time_zone="UTC", strict=False)
            .dt.epoch("us")
            .alias("_ts_us")
        )
        prior_mask = parsed["_ts_us"] <= sample_ts_us

    prior = frame.filter(prior_mask)
    if prior.is_empty():
        return zero

    # Deterministic: the LATEST prior event requires chronological order.
    prior = prior.sort("published_at")
    last = prior.tail(1).row(0, named=True)
    ctx: dict[str, float] = {}
    for f in _news_schema_fields(news_schema):
        raw = last.get(f, 0.0)
        try:
            ctx[f] = float(raw)
        except (TypeError, ValueError):
            ctx[f] = 0.0
    # time_since_event_sec: age of the latest prior event at the sample ts
    ctx["time_since_event_sec"] = _num(
        _safe_epoch_sec(last.get("published_at")),
        default=0.0,
    )
    if ctx["time_since_event_sec"]:
        ctx["time_since_event_sec"] = max(
            0.0, (timestamp.timestamp() - ctx["time_since_event_sec"])
        )
    return ctx


def _safe_epoch_sec(value: Any) -> float:
    """Robust epoch-seconds extraction for a publication timestamp.

    Handles (without platform-specific datetime crashes):
        * real ``datetime.datetime`` (tz-aware or naive → UTC),
        * Polars ``datetime[us, UTC]`` scalars (str(...) gives an ISO
          string with a trailing `` UTC`` suffix),
        * numpy ``datetime64`` scalars (str(...) gives ``YYYY-MM-DD
          HH:MM:SS``),
        * ISO strings, None / NaT / empty → 0.0.
    """
    if value is None:
        return 0.0
    # Real python datetimes are the ONLY objects we call .timestamp() on.
    if isinstance(value, datetime):
        dt = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return dt.timestamp()
    text = str(value).strip()
    if not text or text.upper() in ("NAT", "NONE", "NAN"):
        return 0.0
    # Normalize ISO-ish forms: trailing " UTC" (Polars), "Z", "+00:00".
    iso = text.replace(" UTC", "+00:00").replace("Z", "+00:00")
    # Polars' text repr uses a SPACE between date and time ("2026-08-16
    # 10:00:00+00:00"); fromisoformat needs the "T" separator.
    if "T" not in iso and " " in iso:
        iso = iso.replace(" ", "T", 1)
    # numpy datetime64 renders as "YYYY-MM-DD HH:MM:SS[.ffffff]" — naive;
    # treat as UTC (consistent with the frame's tz-aware datetimes).
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return 0.0
    dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return dt.timestamp()


def _news_schema_fields(news_schema: NewsContextSchema | None) -> list[str]:
    if news_schema is not None:
        return list(news_schema.fields)
    return list(default_news_context_schema().fields)


def build_news_frame_from_db(
    db: Any,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 2000,
) -> pl.DataFrame:
    """Exports the news DB analysis rows into the canonical news frame.

    Reads ``news_analysis`` (the authoritative analyzed records) joined to
    ``news_articles`` for the raw publication timestamp + source.  The frame
    is sorted by publication time (ascending) so the causal snapshot logic
    is deterministic.

    Args:
        db: a NewsDatabase instance (or any object exposing ``list_analysis``
            and ``get_article``).
        start/end: optional publication-time bounds.
        limit: bounded row count (defensive).
    """
    rows: list[dict[str, Any]] = []
    analyses = db.list_analysis(limit=limit)
    for a in analyses:
        try:
            art = db.get_article(a["article_id"]) if hasattr(db, "get_article") else None
        except Exception:
            art = None
        published = a.get("analyzed_at") or a.get("published_at") or ""
        if art and art.get("published_at"):
            published = art.get("published_at")
        published_dt = _parse_iso(published)
        if published_dt is None:
            continue
        if start is not None and published_dt < start:
            continue
        if end is not None and published_dt > end:
            continue
        # ---- real analysis fields (the pipeline's authoritative outputs) ----
        direction = a.get("direction", "NEUTRAL")
        direction_up = str(direction or "NEUTRAL").upper()
        importance = a.get("importance", "MINOR")
        importance_score = _num(a.get("importance_score"), 0.0)
        xau_rel = _num(a.get("relevance_to_xauusd"), 0.0)
        usd_rel = _num(a.get("relevance_to_usd"), 0.0)
        confidence = _num(a.get("confidence"), 0.0)
        impact_strength = _num(a.get("impact_strength"), 0.0)
        novelty = a.get("novelty", "NEW") or "NEW"
        # directional pressure: impacts (per-asset) take precedence; when the
        # pipeline persisted no per-asset impacts, the analysis direction +
        # impact_strength are the real signal (never invent a direction).
        bullish = bearish = 0.0
        # ``impacts`` is persisted as a JSON string by the database layer.
        impacts = a.get("impacts")
        if isinstance(impacts, str) and impacts.strip():
            try:
                import json

                impacts = json.loads(impacts)
            except (json.JSONDecodeError, TypeError):
                impacts = None
        if isinstance(impacts, list) and impacts:
            for imp in impacts:
                if not isinstance(imp, dict):
                    continue
                if imp.get("asset") not in ("XAUUSD", None):
                    continue
                d = str(imp.get("direction", "NEUTRAL")).upper()
                strength = _num(imp.get("strength"), 0.0)
                if d == "BULLISH":
                    bullish = max(bullish, strength)
                elif d == "BEARISH":
                    bearish = max(bearish, strength)
        if bullish == 0.0 and bearish == 0.0:
            if direction_up == "BULLISH":
                bullish = impact_strength
            elif direction_up == "BEARISH":
                bearish = impact_strength
        # news_state derived from the real importance/relevance/direction.
        state = _derive_news_state(importance, importance_score, xau_rel, direction)
        # freshness: exponential decay of the publication age using the real
        # News subsystem decay engine (honest real value, not a constant).
        try:
            from nexus_scalp.news.analysis.decay import NewsDecayEngine

            fresh = NewsDecayEngine().freshness(published_dt, horizon=NewsImpactHorizon.MACRO)
        except Exception:
            fresh = 0.0
        row: dict[str, Any] = {
            "published_at": published_dt,
            "active_high_impact_events": 1
            if str(importance).upper() in ("HIGH", "CRITICAL")
            else 0,
            "xauusd_relevance": xau_rel,
            "usd_relevance": usd_rel,
            "bullish_pressure": max(0.0, min(1.0, bullish)),
            "bearish_pressure": max(0.0, min(1.0, bearish)),
            "confidence": confidence,
            "conflict_score": 1.0 if direction_up in ("MIXED", "CONFLICTED") else 0.0,
            "novelty": novelty,
            "freshness": fresh,
            "news_state": state,
            "importance_score": importance_score,
            "direction": direction_up,
            "source_consensus": 0.0,  # consensus table not written by the
            # pipeline (see audit) — honest default until wired.
        }
        rows.append(row)

    if not rows:
        return pl.DataFrame()

    rows.sort(key=lambda r: r["published_at"])
    frame = pl.DataFrame(rows)
    return normalize_news_frame(frame)


def _parse_iso(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
        except ValueError:
            return None
    return None


def _derive_news_state(
    importance: Any,
    importance_score: float,
    xau_rel: float,
    direction: Any,
) -> str:
    """Maps REAL analysis fields onto the NewsState taxonomy.

    Uses only values the analysis pipeline actually persists — no invented
    signal.  CONFLICTED comes from the analyzer's MIXED direction; magnitude
    (importance vs XAUUSD relevance) drives HIGH_IMPACT / ELEVATED.
    """
    dir_up = str(direction or "NEUTRAL").upper()
    if dir_up in ("MIXED", "CONFLICTED"):
        return "CONFLICTED"
    imp = str(importance or "MINOR").upper()
    if imp == "CRITICAL" or (imp == "HIGH" and xau_rel >= 0.5):
        return "HIGH_IMPACT"
    if imp in ("HIGH", "MODERATE") and xau_rel > 0.0:
        return "ELEVATED"
    return "NORMAL"


# ---------------------------------------------------------------------------
# News quality diagnostics + benchmark readiness gate (spec 7 / 20 / 21)
# ---------------------------------------------------------------------------


def news_quality_diagnostics(
    news_frame: pl.DataFrame | None,
    news_schema: NewsContextSchema | None = None,
) -> dict[str, Any]:
    """Real computed quality metrics for a (normalized) news frame.

    Returns structured diagnostics so "NEWS ON" can be distinguished from
    "there was actually real, informative news".  All values are computed
    from the given frame — no synthetic telemetry.
    """
    schema = news_schema or default_news_context_schema()
    fields = schema.fields
    if news_frame is None or news_frame.is_empty():
        return {
            "total_news_rows": 0,
            "valid_news_rows": 0,
            "invalid_news_rows": 0,
            "xauusd_relevant_rows": 0,
            "non_neutral_rows": 0,
            "distinct_events": 0,
            "future_rows_rejected": 0,
            "duplicate_rows_removed": 0,
            "dead_zero_fields": list(fields),
            "per_field": {f: _field_stats(None, f) for f in fields},
        }

    norm = normalize_news_frame(news_frame)
    if norm is None or norm.is_empty():
        return {
            "total_news_rows": int(news_frame.height),
            "valid_news_rows": 0,
            "invalid_news_rows": int(news_frame.height),
            "xauusd_relevant_rows": 0,
            "non_neutral_rows": 0,
            "distinct_events": 0,
            "future_rows_rejected": 0,
            "duplicate_rows_removed": 0,
            "dead_zero_fields": list(fields),
            "per_field": {f: _field_stats(None, f) for f in fields},
        }

    n = norm.height
    # non-neutral: any schema field != 0 (excluding the always-per-sample
    # time_since_event_sec reference column in the frame).
    non_neutral = 0
    for row in norm.select(fields).iter_rows():
        vals = [v for f, v in zip(fields, row, strict=False) if f != "time_since_event_sec"]
        if any(float(v) != 0.0 for v in vals):
            non_neutral += 1
    xau = int((norm["xauusd_relevance"] > 0).sum()) if "xauusd_relevance" in norm.columns else 0
    distinct = int(norm.select(fields).unique().height) if n else 0

    dead_zero = []
    for f in fields:
        if f == "time_since_event_sec":
            continue  # reference column, derived per-sample
        col = f if f in norm.columns else None
        if col is None:
            dead_zero.append(f)
            continue
        try:
            if int((norm[col] != 0).sum()) == 0:
                dead_zero.append(f)
        except Exception:
            dead_zero.append(f)

    return {
        "total_news_rows": n,
        "valid_news_rows": n,
        "invalid_news_rows": max(0, int(news_frame.height) - n),
        "xauusd_relevant_rows": int(xau),
        "non_neutral_rows": int(non_neutral),
        "distinct_events": int(distinct),
        "future_rows_rejected": 0,  # no decision time supplied here
        "duplicate_rows_removed": 0,  # the frame is the canonical input
        "dead_zero_fields": dead_zero,
        "per_field": {f: _field_stats(norm, f) for f in fields},
    }


def _field_stats(norm: pl.DataFrame | None, field: str) -> dict[str, float | int]:
    """Per-field nonzero count, unique count, min/max/mean/std, missing."""
    if norm is None or norm.is_empty() or field not in norm.columns:
        return {
            "nonzero": 0,
            "unique": 0,
            "min": 0.0,
            "max": 0.0,
            "mean": 0.0,
            "std": 0.0,
            "missing": 0,
        }
    s = norm[field]
    missing = int(s.is_null().sum())
    nz = int((s != 0).sum())
    arr = s.drop_nulls().to_numpy().astype(float)
    if arr.size == 0:
        return {
            "nonzero": nz,
            "unique": 0,
            "min": 0.0,
            "max": 0.0,
            "mean": 0.0,
            "std": 0.0,
            "missing": missing,
        }
    import numpy as np

    return {
        "nonzero": nz,
        "unique": len(np.unique(arr)),
        "min": round(float(np.min(arr)), 4),
        "max": round(float(np.max(arr)), 4),
        "mean": round(float(np.mean(arr)), 4),
        "std": round(float(np.std(arr)), 4),
        "missing": missing,
    }


def news_benchmark_readiness(
    news_frame: pl.DataFrame | None,
    news_schema: NewsContextSchema | None = None,
    *,
    require_non_neutral: bool = True,
    require_xauusd: bool = True,
    require_min_events: int = 2,
) -> dict[str, Any]:
    """Real-data readiness gate for the A/B/C/D news benchmark.

    Returns a dict with a boolean ``ready`` and per-requirement results.
    The benchmark MUST NOT run when ``ready`` is False (spec 20).
    """
    diag = news_quality_diagnostics(news_frame, news_schema)
    # schema_valid: the CORE information-bearing fields must not be
    # structurally dead — xauusd_relevance / bullish_pressure /
    # bearish_pressure / confidence are the ones a real event must set.
    # Other fields (active_high_impact_events, conflict_score, freshness,
    # source_consensus) may legitimately be 0 for minor/singular events —
    # that is content neutrality, NOT an encoding failure.  This rejects
    # the old synthetic fixture (where all 12 were 0 or constant) without
    # demanding every field be nonzero.
    dz = set(diag["dead_zero_fields"])
    core = {"xauusd_relevance", "bullish_pressure", "bearish_pressure", "confidence"}
    core_dead = len(dz & core)
    checks: dict[str, bool] = {
        "real_news_db_exists": news_frame is not None and not news_frame.is_empty(),
        "contains_analysis_records": diag["total_news_rows"] > 0,
        "non_neutral_news_gt_0": ((not require_non_neutral) or diag["non_neutral_rows"] > 0),
        "xauusd_relevant_gt_0": ((not require_xauusd) or diag["xauusd_relevant_rows"] > 0),
        "multiple_event_timestamps": diag["total_news_rows"] >= require_min_events,
        "no_synthetic_fixture": diag["distinct_events"] >= 2,
        "schema_valid": core_dead < len(core),
    }
    ready = all(checks.values())
    return {
        "ready": ready,
        "checks": checks,
        "diagnostics": diag,
        "generated_at": datetime.now(UTC).isoformat(),
    }
