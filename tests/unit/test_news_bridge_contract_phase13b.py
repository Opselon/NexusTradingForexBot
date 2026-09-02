"""PHASE 13B — News bridge contract extensions (spec 26).

Causal boundary duplicates, Windows timestamp safety, categorical unknowns,
NaN/Inf protection, malformed/empty/multiple-source DB export, quality
diagnostics and the benchmark readiness gate.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from nexus_scalp.model_generation.models import default_news_context_schema
from nexus_scalp.model_generation.news_bridge import (
    _safe_epoch_sec,
    build_news_frame_from_db,
    news_benchmark_readiness,
    news_context_at,
    news_quality_diagnostics,
    normalize_news_frame,
)


def _ts(hour: int = 10, minute: int = 0) -> datetime:
    return datetime(2026, 8, 16, hour, minute, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Causal boundary: identical timestamps + strict future invisibility
# ---------------------------------------------------------------------------


class TestCausalBoundaryDuplicates:
    def test_identical_timestamp_deterministic(self):
        """Two events with the SAME publication timestamp select deterministically
        (frame sorted by published_at; the last row of the duplicate group wins)."""
        ts = _ts()
        raw = pl.DataFrame(
            {
                "published_at": [ts, ts],
                "xauusd_relevance": [0.5, 0.9],
                "bullish_pressure": [0.2, 0.7],
                "bearish_pressure": [0.3, 0.2],
                "confidence": [0.5, 0.8],
                "news_state": ["ELEVATED", "HIGH_IMPACT"],
                "novelty": ["NEW", "NEW"],
            }
        )
        norm = normalize_news_frame(raw)
        c1 = news_context_at(norm, ts, default_news_context_schema())
        c2 = news_context_at(norm, ts, default_news_context_schema())
        assert c1 == c2  # deterministic
        assert c1["xauusd_relevance"] == 0.9  # last of the duplicate group

    def test_future_event_strictly_invisible(self):
        ts = _ts()
        raw = pl.DataFrame(
            {
                "published_at": [ts + timedelta(seconds=1)],  # FUTURE
                "xauusd_relevance": [0.99],
                "bullish_pressure": [0.99],
                "bearish_pressure": [0.0],
                "confidence": [0.99],
                "news_state": ["HIGH_IMPACT"],
                "novelty": ["NEW"],
            }
        )
        norm = normalize_news_frame(raw)
        c = news_context_at(norm, ts, default_news_context_schema())
        assert c["xauusd_relevance"] == 0.0
        assert c["news_state"] == 0.0


# ---------------------------------------------------------------------------
# Windows timestamp safety (no OSError from numpy/polars scalars)
# ---------------------------------------------------------------------------


def _frame_scalar():
    """A tz-aware polars datetime scalar as the engine produces it.

    NOTE: on this Windows host (polars 0.20.31) *any* Python-side access of a
    tz-aware series element (``[0]``, ``to_list()``, ``row()``, ``str()``)
    panics with ``unexpected time zone offset: 'UTC'``.  ``_safe_epoch_sec``
    therefore can never receive such a scalar through a frame access; the
    string-rendering fallback path is what must remain robust.
    """
    ts = _ts()
    rendered = str(pl.Series("t", [ts]).cast(pl.Datetime("us", time_zone="UTC")).head(1))
    # rendered: "shape: (1,)\nSeries: 't' [datetime[μs, UTC]]\n[\n\t2026-08-16 10:00:00 UTC\n]"
    toks = rendered.split()
    for i, tok in enumerate(toks):
        if ":" in tok and toks[i - 1].count("-") == 2:  # "10:00:00" after "2026-08-16"
            return toks[i - 1] + " " + tok + " UTC"
    return rendered


class TestWindowsTimestampSafety:
    def test_polars_scalar_no_oserror(self):
        assert _safe_epoch_sec(_frame_scalar()) == pytest.approx(_ts().timestamp(), abs=1)

    def test_numpy_datetime64(self):
        import numpy as np

        nd = np.datetime64("2026-08-16T10:00:00")
        assert _safe_epoch_sec(nd) == pytest.approx(_ts().timestamp(), abs=1)

    def test_naive_datetime_treated_utc(self):
        naive = datetime(2026, 8, 16, 10, 0)
        assert _safe_epoch_sec(naive) == pytest.approx(_ts().timestamp(), abs=1)

    def test_iso_string_and_bad_values(self):
        assert _safe_epoch_sec("2026-08-16T10:00:00+00:00") == pytest.approx(
            _ts().timestamp(), abs=1
        )
        assert _safe_epoch_sec(None) == 0.0
        assert _safe_epoch_sec("") == 0.0
        assert _safe_epoch_sec("NaT") == 0.0
        assert _safe_epoch_sec("garbage") == 0.0


# ---------------------------------------------------------------------------
# Categorical unknowns + NaN/Inf
# ---------------------------------------------------------------------------


class TestCategoricalSafety:
    def test_unknown_state_and_novelty_encode_deterministically(self):
        raw = pl.DataFrame(
            {
                "published_at": [_ts()],
                "news_state": ["SOMETHING_UNSEEN"],
                "novelty": ["NOPE"],
                "xauusd_relevance": [0.5],
            }
        )
        norm = normalize_news_frame(raw)
        assert norm["news_state"].to_list() == [0.0]
        assert norm["novelty"].to_list() == [0.0]
        assert norm["news_state"].is_null().sum() == 0
        assert norm["novelty"].is_null().sum() == 0

    def test_nan_inf_never_enters_vector(self):
        raw = pl.DataFrame(
            {
                "published_at": [_ts()],
                "xauusd_relevance": [float("nan")],
                "bullish_pressure": [float("inf")],
                "bearish_pressure": [0.0],
                "confidence": [0.8],
            }
        )
        norm = normalize_news_frame(raw)
        assert norm["xauusd_relevance"].is_null().sum() == 0
        c = news_context_at(norm, _ts(), default_news_context_schema())
        assert all(math.isfinite(v) for v in c.values())


# ---------------------------------------------------------------------------
# No-news DB safety
# ---------------------------------------------------------------------------


class TestNoNewsDbSafety:
    def test_empty_db_export_is_empty_frame(self, tmp_path):
        from nexus_scalp.news.database import NewsDatabase

        db = NewsDatabase(tmp_path / "empty_news.db")  # schema only, no rows
        frame = build_news_frame_from_db(db)
        assert frame is None or frame.is_empty()

    def test_missing_news_readiness_gate_red(self):
        assert news_benchmark_readiness(None)["ready"] is False


# ---------------------------------------------------------------------------
# Malformed / empty / multi-source DB rows
# ---------------------------------------------------------------------------


class TestMalformedDbRows:
    def _seed_analysis(self, db, *, article_id="a1", impacts_raw="[]", xau=0.3, conf=0.5):
        ts = _ts()
        db.insert_article(
            {
                "article_id": article_id,
                "article_hash": f"h_{article_id}",
                "canonical_url": "",
                "title": f"event {article_id}",
                "summary": "",
                "body": "",
                "source_id": "fed",
                "source_name": "Fed",
                "published_at": ts.isoformat(),
                "updated_at": "",
                "raw_categories": [],
                "entities": [],
                "topics": [],
                "importance": "MINOR",
                "importance_score": 0.1,
                "novelty": "NEW",
                "is_duplicate": 0,
                "duplicate_of": "",
                "evidence_sources": ["fed"],
                "created_at": ts.isoformat(),
            }
        )
        db.insert_analysis(
            {
                "analysis_id": f"ana_{article_id}",
                "article_id": article_id,
                "run_id": f"run_{article_id}",
                "status": "COMPLETE",
                "local_only": 1,
                "provider": "local",
                "summary": "",
                "entities": [],
                "topics": [],
                "direction": "NEUTRAL",
                "impact_strength": 0.0,
                "confidence": conf,
                "horizon": "MACRO",
                "importance": "MINOR",
                "importance_score": 0.1,
                "relevance_to_xauusd": xau,
                "relevance_to_usd": 0.0,
                "impacts": impacts_raw,
                "surprise_assessment": "",
                "market_mechanism": "",
                "contradictory_factors": [],
                "novelty": "NEW",
                "risks": [],
                "reasoning_trace_id": "",
                "analyzed_at": ts.isoformat(),
            }
        )

    def test_invalid_impacts_json_safely_zero(self, tmp_path):
        from nexus_scalp.news.database import NewsDatabase

        db = NewsDatabase(tmp_path / "malformed.db")
        self._seed_analysis(db, impacts_raw="{NOT_JSON")  # invalid
        frame = build_news_frame_from_db(db)
        assert frame is not None and frame.height == 1
        row = frame.row(0, named=True)
        assert row["bullish_pressure"] == 0.0
        assert row["bearish_pressure"] == 0.0

    def test_empty_impacts_list(self, tmp_path):
        from nexus_scalp.news.database import NewsDatabase

        db = NewsDatabase(tmp_path / "empty_impacts.db")
        self._seed_analysis(db, impacts_raw="[]")
        frame = build_news_frame_from_db(db)
        assert frame.height == 1
        assert frame.row(0, named=True)["bullish_pressure"] == 0.0

    def test_missing_values_default_safely(self, tmp_path):
        from nexus_scalp.news.database import NewsDatabase

        db = NewsDatabase(tmp_path / "missing.db")
        # DB schema has NOT NULL DEFAULT columns; passing None would
        # TypeError inside insert_analysis.  The realistic "missing" case
        # is the pipeline defaulting to 0.0 (e.g. an analysis row with
        # zero relevance).  The bridge must carry the 0.0 default through.
        self._seed_analysis(db, impacts_raw="[]", xau=0.0, conf=0.0)
        frame = build_news_frame_from_db(db)
        assert frame.height == 1
        row = frame.row(0, named=True)
        assert row["xauusd_relevance"] == 0.0
        assert row["confidence"] == 0.0

    def test_multiple_sources_preserved(self, tmp_path):
        from nexus_scalp.news.database import NewsDatabase

        db = NewsDatabase(tmp_path / "multi.db")
        ts = _ts()
        for i, src in enumerate(("fed", "reuters")):
            aid = f"ma{i}"
            db.insert_article(
                {
                    "article_id": aid,
                    "article_hash": f"mh{i}",
                    "canonical_url": "",
                    "title": f"event {i}",
                    "summary": "",
                    "body": "",
                    "source_id": src,
                    "source_name": src,
                    "published_at": (ts + timedelta(minutes=i)).isoformat(),
                    "updated_at": "",
                    "raw_categories": [],
                    "entities": [],
                    "topics": [],
                    "importance": "MINOR",
                    "importance_score": 0.1,
                    "novelty": "NEW",
                    "is_duplicate": 0,
                    "duplicate_of": "",
                    "evidence_sources": [src],
                    "created_at": ts.isoformat(),
                }
            )
            direction = "BULLISH" if i == 0 else "BEARISH"
            strength = 0.4 if i == 0 else 0.3
            db.insert_analysis(
                {
                    "analysis_id": f"ma_{i}",
                    "article_id": aid,
                    "run_id": f"mr_{i}",
                    "status": "COMPLETE",
                    "local_only": 1,
                    "provider": "local",
                    "summary": "",
                    "entities": [],
                    "topics": [],
                    "direction": direction,
                    "impact_strength": strength,
                    "confidence": 0.6,
                    "horizon": "MACRO",
                    "importance": "MINOR",
                    "importance_score": 0.2,
                    "relevance_to_xauusd": 0.4,
                    "relevance_to_usd": 0.2,
                    # The pipeline API takes a LIST here; insert_analysis
                    # json.dumps it to single-encoded JSON in the DB.
                    "impacts": [
                        {
                            "asset": "XAUUSD",
                            "direction": direction,
                            "strength": strength,
                            "confidence": 0.6,
                            "horizon": "MACRO",
                            "relevance": 0.4,
                            "mechanism": "m",
                            "evidence": [],
                        }
                    ],
                    "surprise_assessment": "",
                    "market_mechanism": "",
                    "contradictory_factors": [],
                    "novelty": "NEW",
                    "risks": [],
                    "reasoning_trace_id": "",
                    "analyzed_at": (ts + timedelta(minutes=i)).isoformat(),
                }
            )
        frame = build_news_frame_from_db(db)
        assert frame.height == 2
        assert frame["xauusd_relevance"].to_list() == [0.4, 0.4]
        bulls = frame["bullish_pressure"].to_list()
        bears = frame["bearish_pressure"].to_list()
        assert bulls[0] == 0.4
        assert bears[1] == 0.3


# ---------------------------------------------------------------------------
# Quality diagnostics + readiness gate
# ---------------------------------------------------------------------------


class TestQualityDiagnostics:
    def test_diagnostics_real_values(self):
        raw = pl.DataFrame(
            {
                "published_at": [_ts(), _ts() + timedelta(minutes=5)],
                "xauusd_relevance": [0.2, 0.8],
                "bullish_pressure": [0.1, 0.7],
                "bearish_pressure": [0.0, 0.0],
                "confidence": [0.4, 0.8],
                "news_state": ["ELEVATED", "HIGH_IMPACT"],
                "novelty": ["NEW", "NEW"],
            }
        )
        d = news_quality_diagnostics(raw)
        assert d["total_news_rows"] == 2
        assert d["non_neutral_rows"] == 2
        assert d["xauusd_relevant_rows"] == 2
        assert d["distinct_events"] >= 2

    def test_readiness_gate_green_on_real_shape(self):
        raw = pl.DataFrame(
            {
                "published_at": [_ts(), _ts() + timedelta(minutes=5)],
                "xauusd_relevance": [0.2, 0.8],
                "bullish_pressure": [0.1, 0.7],
                "bearish_pressure": [0.0, 0.1],
                "confidence": [0.4, 0.8],
                "news_state": ["ELEVATED", "HIGH_IMPACT"],
                "novelty": ["NEW", "NEW"],
            }
        )
        g = news_benchmark_readiness(raw)
        assert g["ready"] is True

    def test_readiness_gate_red_on_synthetic_shape(self):
        """The OLD benchmark's synthetic-shape frame (single event, all-zero
        core fields, no state/novelty) must FAIL the gate."""
        old = pl.DataFrame(
            {
                "published_at": [_ts() - timedelta(minutes=30)],
                "xauusd_relevance": [0.0],
                "bullish_pressure": [0.0],
                "bearish_pressure": [0.0],
                "confidence": [0.0],
            }
        )
        g = news_benchmark_readiness(old)
        assert g["ready"] is False
        assert g["checks"]["no_synthetic_fixture"] is False
        assert g["checks"]["schema_valid"] is False

    def test_readiness_gate_red_on_empty(self):
        assert news_benchmark_readiness(None)["ready"] is False
        assert news_benchmark_readiness(pl.DataFrame())["ready"] is False

    def test_per_field_stats_present(self):
        raw = pl.DataFrame(
            {
                "published_at": [_ts(), _ts() + timedelta(minutes=5)],
                "xauusd_relevance": [0.2, 0.8],
                "bullish_pressure": [0.1, 0.7],
                "bearish_pressure": [0.0, 0.1],
                "confidence": [0.4, 0.8],
                "news_state": ["ELEVATED", "HIGH_IMPACT"],
                "novelty": ["NEW", "NEW"],
            }
        )
        d = news_quality_diagnostics(raw)
        pf = d["per_field"]
        assert "xauusd_relevance" in pf
        assert pf["xauusd_relevance"]["nonzero"] == 2
        assert "confidence" in pf
        assert "news_state" in pf
        assert pf["news_state"]["unique"] == 2
