"""PHASE 13B — News pipeline bridge tests.

Behavioral verification of ``model_generation.news_bridge``:
    * 12-field schema completeness (no dead-zero columns),
    * categorical encoding (state/novelty),
    * strict historical causality at T-1 / T / T+1,
    * DB -> frame export integration,
    * full-vector differentiation (not 11 synthetic vectors).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from nexus_scalp.model_generation.models import default_news_context_schema
from nexus_scalp.model_generation.news_bridge import (
    build_news_frame_from_db,
    news_context_at,
    normalize_news_frame,
)


def _ts(hour: int = 10, minute: int = 0) -> datetime:
    return datetime(2026, 8, 16, hour, minute, tzinfo=UTC)


def _raw_news() -> pl.DataFrame:
    ts = _ts()
    return pl.DataFrame(
        {
            "published_at": [
                ts - timedelta(minutes=10),
                ts,
                ts + timedelta(minutes=10),
            ],
            "xauusd_relevance": [0.1, 0.9, 0.5],
            "usd_relevance": [0.4, 0.6, 0.2],
            "bullish_pressure": [0.1, 0.8, 0.2],
            "bearish_pressure": [0.7, 0.1, 0.3],
            "confidence": [0.6, 0.8, 0.5],
            "news_state": ["ELEVATED", "HIGH_IMPACT", "NORMAL"],
            "novelty": ["NEW", "NEW", "CONFIRMATION"],
        }
    )


class TestNormalizeNewsFrame:
    def test_all_12_fields_present_and_numeric(self):
        norm = normalize_news_frame(_raw_news())
        schema = default_news_context_schema()
        assert norm is not None
        for field in schema.fields:
            assert field in norm.columns, f"missing schema field {field}"
            assert str(norm[field].dtype).lower() in ("f64", "float64")

    def test_no_dead_zero_columns_with_informative_input(self):
        raw = _raw_news().with_columns(
            pl.Series("active_event_count", [2, 1, 0], dtype=pl.Int64),
            pl.Series("conflict_score", [0.0, 0.1, 0.0]),
            pl.Series("source_consensus", [0.8, 0.9, 0.5]),
            pl.Series("freshness", [0.1, 0.9, 1.0]),
        )
        norm = normalize_news_frame(raw)
        schema = default_news_context_schema()
        for field in schema.fields:
            if field == "time_since_event_sec":
                # REFERENCE-0 in the frame; derived per-sample in
                # news_context_at (the causal snapshot).
                assert (norm[field] == 0).all()
                continue
            nz = (norm[field] != 0).sum()
            assert nz > 0, f"schema field {field} is dead-zero on informative input"

    def test_state_and_novelty_encoded_numerically(self):
        norm = normalize_news_frame(_raw_news())
        assert norm["news_state"].to_list() == [1.0, 2.0, 0.0]
        assert norm["novelty"].to_list() == [0.0, 0.0, 2.0]

    def test_bullish_bearish_aliased_from_scores(self):
        raw = (
            _raw_news()
            .drop("bullish_pressure")
            .drop("bearish_pressure")
            .with_columns(
                pl.Series("bullish_score", [0.1, 0.8, 0.2]),
                pl.Series("bearish_score", [0.7, 0.1, 0.3]),
            )
        )
        norm = normalize_news_frame(raw)
        assert norm["bullish_pressure"].to_list() == [0.1, 0.8, 0.2]
        assert norm["bearish_pressure"].to_list() == [0.7, 0.1, 0.3]

    def test_empty_frame_returns_none(self):
        assert normalize_news_frame(pl.DataFrame()) is None
        assert normalize_news_frame(None) is None


class TestNewsContextAt:
    def test_causal_boundaries_exact(self):
        """The latest event published at-or-before T defines the snapshot:
        T-1s -> first event; T -> event at T; T+1s -> STILL event at T
        (the T+10min event has not been published yet)."""
        norm = normalize_news_frame(_raw_news())
        ts = _ts()
        schema = default_news_context_schema()

        c_before = news_context_at(norm, ts - timedelta(seconds=1), schema)
        assert c_before["xauusd_relevance"] == 0.1
        assert c_before["news_state"] == 1.0
        assert 590.0 <= c_before["time_since_event_sec"] <= 600.0

        c_at = news_context_at(norm, ts, schema)
        assert c_at["xauusd_relevance"] == 0.9
        assert c_at["news_state"] == 2.0
        assert c_at["time_since_event_sec"] == 0.0

        c_after = news_context_at(norm, ts + timedelta(seconds=1), schema)
        assert c_after["xauusd_relevance"] == 0.9  # event at T still latest
        assert c_after["news_state"] == 2.0
        assert c_after["time_since_event_sec"] == 1.0

    def test_future_event_never_visible(self):
        """An event published strictly AFTER T can never appear at T."""
        norm = normalize_news_frame(_raw_news())
        c = news_context_at(norm, _ts() - timedelta(seconds=1))
        assert c["xauusd_relevance"] != 0.5  # the 10:10 event

    def test_no_news_returns_zero_vector(self):
        c = news_context_at(None, _ts())
        schema = default_news_context_schema()
        assert all(c[f] == 0.0 for f in schema.fields)

    def test_output_matches_schema_order(self):
        norm = normalize_news_frame(_raw_news())
        c = news_context_at(norm, _ts())
        schema = default_news_context_schema()
        assert list(c.keys()) == schema.fields


class TestBuildFrameFromDb:
    def test_export_roundtrip(self, tmp_path):
        from nexus_scalp.news.database import NewsDatabase

        db = NewsDatabase(tmp_path / "news_bridge.db")
        ts = _ts()
        from nexus_scalp.news.models import NewsArticle

        article = NewsArticle(
            article_id="news_test1",
            article_hash="h1",
            title="Fed cuts rates",
            source_id="fed",
            source_name="Fed",
            published_at=ts,
            summary="FOMC cuts 25bp",
        )
        db.insert_article(
            {
                "article_id": article.article_id,
                "article_hash": article.article_hash,
                "canonical_url": "",
                "title": article.title,
                "summary": article.summary,
                "body": "",
                "source_id": article.source_id,
                "source_name": article.source_name,
                "published_at": article.published_at.isoformat(),
                "updated_at": "",
                "raw_categories": [],
                "entities": [],
                "topics": [],
                "importance": "HIGH",
                "importance_score": 0.8,
                "novelty": "NEW",
                "is_duplicate": 0,
                "duplicate_of": "",
                "evidence_sources": ["fed"],
                "created_at": ts.isoformat(),
            }
        )
        db.insert_analysis(
            {
                "analysis_id": "ana_test1",
                "article_id": "news_test1",
                "run_id": "run_test1",
                "status": "COMPLETE",
                "local_only": 1,
                "provider": "local",
                "summary": "FOMC cuts 25bp",
                "entities": [],
                "topics": [],
                "direction": "BULLISH",
                "impact_strength": 0.6,
                "confidence": 0.8,
                "horizon": "POLICY",
                "importance": "HIGH",
                "importance_score": 0.8,
                "relevance_to_xauusd": 0.9,
                "relevance_to_usd": 0.6,
                "impacts": [
                    {
                        "asset": "XAUUSD",
                        "direction": "BULLISH",
                        "strength": 0.6,
                        "confidence": 0.8,
                        "horizon": "POLICY",
                        "relevance": 0.9,
                        "mechanism": "rate cut",
                        "evidence": [],
                    }
                ],
                "surprise_assessment": "",
                "market_mechanism": "",
                "contradictory_factors": [],
                "novelty": "NEW",
                "risks": [],
                "reasoning_trace_id": "",
                "analyzed_at": ts.isoformat(),
            }
        )
        frame = build_news_frame_from_db(db)
        assert frame is not None
        assert not frame.is_empty()
        row = frame.row(0, named=True)
        assert row["xauusd_relevance"] == 0.9
        assert row["bullish_pressure"] == 0.6  # derived from impacts
        assert row["news_state"] == 2.0  # HIGH_IMPACT (real derivation)
        assert row["novelty"] == 0.0  # NEW

    def test_export_bounds(self, tmp_path):
        from nexus_scalp.news.database import NewsDatabase

        db = NewsDatabase(tmp_path / "news_bounds.db")
        ts = _ts()
        # Insert analysis rows at 30 / 60 minutes in the past.
        for i, minutes in enumerate((30, 60)):
            article_id = f"a{i}"
            db.insert_article(
                {
                    "article_id": article_id,
                    "article_hash": f"h{i}",
                    "canonical_url": "",
                    "title": f"event {i}",
                    "summary": "",
                    "body": "",
                    "source_id": "fed",
                    "source_name": "Fed",
                    "published_at": (ts - timedelta(minutes=minutes)).isoformat(),
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
                    "analysis_id": f"ana{i}",
                    "article_id": article_id,
                    "run_id": f"run{i}",
                    "status": "COMPLETE",
                    "local_only": 1,
                    "provider": "local",
                    "summary": "",
                    "entities": [],
                    "topics": [],
                    "direction": "NEUTRAL",
                    "impact_strength": 0.0,
                    "confidence": 0.3,
                    "horizon": "MACRO",
                    "importance": "MINOR",
                    "importance_score": 0.1,
                    "relevance_to_xauusd": 0.1,
                    "relevance_to_usd": 0.0,
                    "impacts": [],
                    "surprise_assessment": "",
                    "market_mechanism": "",
                    "contradictory_factors": [],
                    "novelty": "NEW",
                    "risks": [],
                    "reasoning_trace_id": "",
                    "analyzed_at": (ts - timedelta(minutes=minutes)).isoformat(),
                }
            )
        frame_all = build_news_frame_from_db(db)
        assert frame_all.height == 2
        # bounds: only the analysis >= 40m ago and <= 10m ago survives
        frame = build_news_frame_from_db(
            db, start=ts - timedelta(minutes=40), end=ts - timedelta(minutes=10)
        )
        assert frame.height == 1
