"""PHASE 13B — News bridge finalization tests (spec 26 extensions).

Covers the remaining contract requirements of the news bridge task:

    * real SQLite news DB export (multiple sources, malformed rows,
      JSON impacts, missing values) — spec 4 / 15,
    * no-news / empty DB safety through the CLI contract — spec 5 / 16,
    * provenance: real-news dataset is distinguishable from a no-news
      dataset and news content changes the dataset identity — spec 17 / 18,
    * synthetic-benchmark detection: the old 10-row fixture shape must be
      rejected by the readiness gate and the report labeled — spec 19,
    * schema invariant: the 12-field NewsContext is the ONLY news surface
      entering the neural matrix — spec 11,
    * categorical determinism + NaN/Inf protection at the pipeline level.

All fixtures conform to the REAL canonical 12-field schema; nothing here
replicates the old 4-event synthetic shortcut.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from nexus_scalp.model_generation.dataset_factory import DatasetFactory
from nexus_scalp.model_generation.models import default_news_context_schema
from nexus_scalp.model_generation.news_bridge import (
    build_news_frame_from_db,
    news_benchmark_readiness,
    news_context_at,
    news_quality_diagnostics,
    normalize_news_frame,
)


def _ts(hour: int = 10, minute: int = 0) -> datetime:
    return datetime(2026, 8, 17, hour, minute, tzinfo=UTC)


def _make_bars(n: int = 120, seed: int = 11) -> pl.DataFrame:
    np.random.seed(seed)
    feats = {f"feat_{i}": np.random.randn(n) for i in range(50)}
    ts = np.arange(0, n, dtype="int64").astype("datetime64[us]")
    return pl.DataFrame(
        {
            **feats,
            "close": 2000 + np.cumsum(np.random.randn(n) * 0.5),
            "high": 2000 + np.random.rand(n) * 2,
            "low": 2000 - np.random.rand(n) * 2,
            "atr_m1": 1.0 + np.random.rand(n),
            "timestamp": ts,
            "regime": np.where(np.random.rand(n) > 0.5, "TRENDING", "RANGING"),
        }
    )


def _real_news_frame(n_events: int = 5, start_sec: int = 10, step_sec: int = 40) -> pl.DataFrame:
    """A realistic 12-field news frame (categorical strings for state/novelty)."""
    ts = np.arange(start_sec, start_sec + n_events * step_sec, step_sec, dtype="int64").astype(
        "datetime64[us]"
    )
    return pl.DataFrame(
        {
            "published_at": ts,
            "active_high_impact_events": [1, 2, 0, 1, 2],
            "xauusd_relevance": [0.9, 0.2, 0.8, 0.6, 0.3],
            "usd_relevance": [0.6, 0.2, 0.5, 0.4, 0.1],
            "bullish_pressure": [0.1, 0.6, 0.2, 0.4, 0.7],
            "bearish_pressure": [0.8, 0.1, 0.7, 0.3, 0.2],
            "conflict_score": [0.1, 0.0, 0.2, 0.0, 0.1],
            "novelty": ["NEW", "UPDATED", "CONFIRMATION", "NEW", "REPETITION"],
            "freshness": [0.9, 0.5, 0.4, 0.3, 0.2],
            "confidence": [0.9, 0.5, 0.8, 0.6, 0.4],
            "source_consensus": [0.7, 0.3, 0.6, 0.4, 0.2],
            "news_state": ["HIGH_IMPACT", "ELEVATED", "NORMAL", "ELEVATED", "CONFLICTED"],
        }
    )


# ---------------------------------------------------------------------------
# spec 4 / 15 — build_news_frame_from_db against a REAL temp SQLite DB
# ---------------------------------------------------------------------------


def _seed_db(db, rows: list[dict], ts: datetime) -> None:
    for i, r in enumerate(rows):
        aid = f"art_{i}"
        db.insert_article(
            {
                "article_id": aid,
                "article_hash": f"h_{i}",
                "canonical_url": "",
                "title": r.get("title", f"event {i}"),
                "summary": "",
                "body": "",
                "source_id": r.get("source_id", "fed"),
                "source_name": r.get("source_name", "Fed"),
                "published_at": r.get("published_at", ts + timedelta(minutes=i)).isoformat(),
                "updated_at": "",
                "raw_categories": [],
                "entities": [],
                "topics": [],
                "importance": r.get("importance", "MINOR"),
                "importance_score": r.get("importance_score", 0.1),
                "novelty": r.get("novelty", "NEW"),
                "is_duplicate": 0,
                "duplicate_of": "",
                "evidence_sources": [r.get("source_id", "fed")],
                "created_at": ts.isoformat(),
            }
        )
        db.insert_analysis(
            {
                "analysis_id": f"ana_{i}",
                "article_id": aid,
                "run_id": f"run_{i}",
                "status": "COMPLETE",
                "local_only": 1,
                "provider": "local",
                "summary": "",
                "entities": [],
                "topics": [],
                "direction": r.get("direction", "NEUTRAL"),
                "impact_strength": r.get("impact_strength", 0.0),
                "confidence": r.get("confidence", 0.5),
                "horizon": "MACRO",
                "importance": r.get("importance", "MINOR"),
                "importance_score": r.get("importance_score", 0.1),
                "relevance_to_xauusd": r.get("relevance_to_xauusd", 0.2),
                "relevance_to_usd": r.get("relevance_to_usd", 0.1),
                "impacts": r.get("impacts", []),
                "surprise_assessment": "",
                "market_mechanism": "",
                "contradictory_factors": [],
                "novelty": r.get("novelty", "NEW"),
                "risks": [],
                "reasoning_trace_id": "",
                "analyzed_at": r.get("published_at", ts + timedelta(minutes=i)).isoformat(),
            }
        )


class TestRealDbExport:
    def test_multiple_sources_and_json_impacts(self, tmp_path):
        from nexus_scalp.news.database import NewsDatabase

        db = NewsDatabase(tmp_path / "real_export.db")
        ts = _ts()
        _seed_db(
            db,
            [
                {
                    "source_id": "fed",
                    "title": "FOMC hawkish",
                    "direction": "BULLISH",
                    "relevance_to_xauusd": 0.9,
                    "confidence": 0.8,
                    "impacts": [
                        {
                            "asset": "XAUUSD",
                            "direction": "BULLISH",
                            "strength": 0.6,
                            "confidence": 0.8,
                            "horizon": "POLICY",
                            "relevance": 0.9,
                            "mechanism": "rate",
                            "evidence": [],
                        }
                    ],
                },
                {
                    "source_id": "reuters",
                    "title": "Safe haven bid",
                    "direction": "BEARISH",
                    "relevance_to_xauusd": 0.4,
                    "confidence": 0.6,
                    "impacts": [
                        {
                            "asset": "XAUUSD",
                            "direction": "BEARISH",
                            "strength": 0.3,
                            "confidence": 0.6,
                            "horizon": "MACRO",
                            "relevance": 0.4,
                            "mechanism": "haven",
                            "evidence": [],
                        }
                    ],
                },
            ],
            ts,
        )
        frame = build_news_frame_from_db(db)
        assert frame is not None and frame.height == 2
        row0 = frame.row(0, named=True)
        row1 = frame.row(1, named=True)
        assert row0["xauusd_relevance"] == 0.9
        assert row0["bullish_pressure"] == 0.6
        assert row1["bearish_pressure"] == 0.3
        # canonical 12 fields + published_at ONLY
        assert set(frame.columns) == {"published_at", *default_news_context_schema().fields}

    def test_invalid_json_impacts_do_not_corrupt(self, tmp_path):
        from nexus_scalp.news.database import NewsDatabase

        db = NewsDatabase(tmp_path / "bad_json.db")
        _seed_db(db, [{"impacts": "{NOT_JSON"}], _ts())
        frame = build_news_frame_from_db(db)
        assert frame is not None and frame.height == 1
        row = frame.row(0, named=True)
        assert row["bullish_pressure"] == 0.0
        assert row["bearish_pressure"] == 0.0

    def test_missing_and_malformed_values_default_safely(self, tmp_path):
        from nexus_scalp.news.database import NewsDatabase

        db = NewsDatabase(tmp_path / "missing.db")
        # Seed via RAW SQL so the DB layer's float() coercion is bypassed —
        # this puts malformed values where the BRIDGE must handle them.
        _SQL_ART = (
            "INSERT INTO news_articles "
            "(article_id, article_hash, title, source_id, source_name, "
            "published_at, importance, novelty, created_at) "
            "VALUES ('a_mal', 'h_mal', 'malformed', 'fed', 'Fed', "
            "?, 'MINOR', 'NEW', ?)"
        )
        _SQL_ANA = (
            "INSERT INTO news_analysis "
            "(analysis_id, article_id, run_id, status, local_only, "
            "provider, summary, entities, topics, direction, "
            "impact_strength, confidence, horizon, importance, "
            "importance_score, relevance_to_xauusd, relevance_to_usd, "
            "impacts, novelty, analyzed_at) "
            "VALUES ('ana_mal', 'a_mal', 'run_mal', 'COMPLETE', 1, 'local', "
            "'', '[]', '[]', 'NEUTRAL', 0.0, ?, 'MACRO', 'MINOR', "
            "0.1, ?, 0.0, '[]', 'NEW', ?)"
        )
        with db._connect() as conn:
            conn.execute(_SQL_ART, (_ts().isoformat(), _ts().isoformat()))
            conn.execute(_SQL_ANA, ("not-a-number", 0.0, _ts().isoformat()))
        frame = build_news_frame_from_db(db)
        assert frame is not None and frame.height == 1
        row = frame.row(0, named=True)
        assert row["xauusd_relevance"] == 0.0  # 0.0 relevance default
        assert row["confidence"] == 0.0  # malformed confidence -> 0.0
        for f in default_news_context_schema().fields:
            assert np.isfinite(row[f])

    def test_categorical_fields_encoded_deterministically(self, tmp_path):
        from nexus_scalp.news.database import NewsDatabase

        db = NewsDatabase(tmp_path / "cats.db")
        _seed_db(
            db,
            [
                {"novelty": "UPDATED", "direction": "NEUTRAL", "impacts": []},
            ],
            _ts(),
        )
        frame = build_news_frame_from_db(db)
        assert frame is not None and frame.height == 1
        row = frame.row(0, named=True)
        # UPDATED -> 1.0, NORMAL default -> 0.0
        assert row["novelty"] == 1.0
        assert row["news_state"] == 0.0

    def test_empty_db_export(self, tmp_path):
        from nexus_scalp.news.database import NewsDatabase

        db = NewsDatabase(tmp_path / "empty.db")  # schema only
        frame = build_news_frame_from_db(db)
        assert frame is None or frame.is_empty()


# ---------------------------------------------------------------------------
# spec 5 / 16 — no-news DB must never pretend real news exists
# ---------------------------------------------------------------------------


class TestNoNewsDbCliContract:
    def test_no_news_frame_yields_zero_context_and_warning_path(self, tmp_path):
        """A dataset built WITHOUT a news frame must carry an all-zero news
        context — but the manifest must NOT claim real news (news_version empty,
        news_data_range empty).  The CLI warning path is exercised by the
        no-DB/no-file branch; the manifest is the testable surface."""
        from nexus_scalp.model_generation.artifact_store import ArtifactStore

        store = ArtifactStore(tmp_path / "no_news" / "artifacts")
        dh = DatasetFactory(store=store).build(_make_bars())
        man = store.read_dataset_manifest(dh["dataset_id"])
        assert man["news_schema_id"] == "news_context_v1"  # schema still declared
        assert man["news_version"] == ""  # NO real news provenance
        assert man["news_data_range"] == {}
        frame = store.read_dataset(dh["dataset_id"])
        news_cols = [
            c for c in frame.columns if c.startswith("news_") and c != "news_context_schema_id"
        ]
        assert len(news_cols) == 12
        # all-zero vectors (news ON == news OFF) — but provenance says no news
        for c in news_cols:
            assert (frame[c] == 0).all()

    def test_real_news_dataset_distinguishable_and_identity_changes(self, tmp_path):
        from nexus_scalp.model_generation.artifact_store import ArtifactStore

        s1 = ArtifactStore(tmp_path / "r1")
        s2 = ArtifactStore(tmp_path / "r2")
        d_no = DatasetFactory(store=s1).build(_make_bars())
        d_news = DatasetFactory(store=s2).build(_make_bars(), news_frame=_real_news_frame())
        # news content participates in the dataset identity
        assert d_no["dataset_id"] != d_news["dataset_id"]
        man_news = s2.read_dataset_manifest(d_news["dataset_id"])
        assert man_news["news_version"] == "news_context_v1"
        assert man_news["news_data_range"]  # non-empty temporal range
        # changing news CONTENT changes the identity deterministically
        s3 = ArtifactStore(tmp_path / "r3")
        d_news2 = DatasetFactory(store=s3).build(
            _make_bars(), news_frame=_real_news_frame(start_sec=11)
        )
        assert d_news["dataset_id"] != d_news2["dataset_id"]

    def test_news_changes_dataset_id_deterministically(self, tmp_path):
        from nexus_scalp.model_generation.artifact_store import ArtifactStore

        s1 = ArtifactStore(tmp_path / "d1")
        s2 = ArtifactStore(tmp_path / "d2")
        a = DatasetFactory(store=s1).build(_make_bars(), news_frame=_real_news_frame())
        b = DatasetFactory(store=s2).build(_make_bars(), news_frame=_real_news_frame())
        assert a["dataset_id"] == b["dataset_id"]  # deterministic


# ---------------------------------------------------------------------------
# spec 11 / 12 — schema invariant + categorical determinism
# ---------------------------------------------------------------------------


class TestSchemaInvariant:
    def test_only_12_news_fields_in_dataset(self, tmp_path):
        from nexus_scalp.model_generation.artifact_store import ArtifactStore

        store = ArtifactStore(tmp_path / "inv" / "artifacts")
        dh = DatasetFactory(store=store).build(_make_bars(), news_frame=_real_news_frame())
        frame = store.read_dataset(dh["dataset_id"])
        schema = default_news_context_schema()
        news_cols = [
            c for c in frame.columns if c.startswith("news_") and c != "news_context_schema_id"
        ]
        # exactly the 12 canonical fields (no metadata / DB / raw JSON columns)
        assert sorted(news_cols) == sorted(f"news_{f}" for f in schema.fields)

    def test_unknown_categorical_never_nan_or_random(self):
        raw = pl.DataFrame(
            {
                "published_at": [_ts()],
                "news_state": ["UNSEEN_STATE"],
                "novelty": ["UNSEEN_NOVELTY"],
                "xauusd_relevance": [0.5],
            }
        )
        norm = normalize_news_frame(raw)
        assert norm["news_state"].to_list() == [0.0]  # explicit UNKNOWN default
        assert norm["novelty"].to_list() == [0.0]
        assert norm["news_state"].is_null().sum() == 0

    def test_nan_inf_never_reach_dataset(self, tmp_path):
        from nexus_scalp.model_generation.artifact_store import ArtifactStore

        news = _real_news_frame().with_columns(
            pl.Series("xauusd_relevance", [float("nan"), 0.2, 0.8, 0.6, 0.3]),
            pl.Series("bullish_pressure", [0.1, float("inf"), 0.2, 0.4, 0.7]),
        )
        store = ArtifactStore(tmp_path / "finite" / "artifacts")
        dh = DatasetFactory(store=store).build(_make_bars(), news_frame=news)
        frame = store.read_dataset(dh["dataset_id"])
        for c in [
            c for c in frame.columns if c.startswith("news_") and c != "news_context_schema_id"
        ]:
            arr = frame[c].to_numpy()
            assert np.isfinite(arr.astype(float)).all()


# ---------------------------------------------------------------------------
# spec 3 / 13 — causal boundary through the canonical bridge
# ---------------------------------------------------------------------------


class TestCausalBoundaryPipeline:
    def test_T_minus_1_T_T_plus_1_exact(self):
        ts = _ts()
        news = pl.DataFrame(
            {
                "published_at": [ts - timedelta(seconds=1), ts, ts + timedelta(seconds=1)],
                "xauusd_relevance": [0.1, 0.5, 0.9],
                "bullish_pressure": [0.1, 0.5, 0.9],
                "bearish_pressure": [0.2, 0.2, 0.2],
                "confidence": [0.5, 0.5, 0.5],
                "news_state": ["NORMAL", "ELEVATED", "HIGH_IMPACT"],
                "novelty": ["NEW", "NEW", "NEW"],
            }
        )
        schema = default_news_context_schema()
        c_before = news_context_at(news, ts - timedelta(seconds=1), schema)
        c_at = news_context_at(news, ts, schema)
        c_after = news_context_at(news, ts + timedelta(seconds=1), schema)
        # T-1: only the event at T-1s is eligible
        assert c_before["xauusd_relevance"] == 0.1
        # T: the event AT T is visible (publication_timestamp <= T)
        assert c_at["xauusd_relevance"] == 0.5
        assert c_at["news_state"] == 1.0
        # T+1: the event at T+1s is NOW eligible too (publication <= T+1)
        assert c_after["xauusd_relevance"] == 0.9
        assert c_after["news_state"] == 2.0

    def test_time_since_event_semantics(self):
        ts = _ts()
        news = pl.DataFrame(
            {
                "published_at": [ts - timedelta(minutes=5), ts],
                "xauusd_relevance": [0.1, 0.5],
                "confidence": [0.4, 0.6],
            }
        )
        schema = default_news_context_schema()
        c = news_context_at(news, ts + timedelta(minutes=1), schema)
        assert c["time_since_event_sec"] == pytest.approx(60.0, abs=1)  # 1 min after the T event
        c_same = news_context_at(news, ts, schema)
        assert c_same["time_since_event_sec"] == 0.0  # canonical exact boundary

    def test_identical_timestamp_deterministic_selection(self):
        ts = _ts()
        news = pl.DataFrame(
            {
                "published_at": [ts, ts],
                "xauusd_relevance": [0.3, 0.7],
                "bullish_pressure": [0.2, 0.6],
                "bearish_pressure": [0.1, 0.1],
                "confidence": [0.5, 0.8],
                "news_state": ["NORMAL", "ELEVATED"],
                "novelty": ["NEW", "NEW"],
            }
        )
        schema = default_news_context_schema()
        c1 = news_context_at(news, ts, schema)
        c2 = news_context_at(news, ts, schema)
        assert c1 == c2
        assert c1["xauusd_relevance"] == 0.7  # deterministic: last of the group


# ---------------------------------------------------------------------------
# spec 19 — synthetic benchmark detection
# ---------------------------------------------------------------------------


class TestSyntheticBenchmarkDetection:
    def test_old_fixture_shape_rejected_by_gate(self):
        """The OLD 10-row synthetic fixture shape (4 events, all-zero core,
        no state/novelty) must FAIL the readiness gate."""
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

    def test_benchmark_runner_refuses_synthetic_news(self, tmp_path):
        """The BenchmarkRunner must REFUSE to run a news benchmark on a frame
        that fails the readiness gate (spec 20 / 22) — synthetic fixtures
        cannot be replayed through the runner by accident."""
        from nexus_scalp.model_generation.artifact_store import ArtifactStore
        from nexus_scalp.model_generation.benchmark import BenchmarkRunner

        old = pl.DataFrame(
            {
                "published_at": [_ts() - timedelta(minutes=30)],
                "xauusd_relevance": [0.0],
                "bullish_pressure": [0.0],
                "bearish_pressure": [0.0],
                "confidence": [0.0],
            }
        )
        runner = BenchmarkRunner(store=ArtifactStore(tmp_path / "b" / "artifacts"))
        with pytest.raises(ValueError, match="readiness gate FAILED"):
            runner.run(_make_bars(n=60), news_frame=old)

    def test_real_shape_passes_gate(self):
        g = news_benchmark_readiness(_real_news_frame())
        assert g["ready"] is True
        assert g["checks"]["no_synthetic_fixture"] is True
        assert g["checks"]["schema_valid"] is True

    def test_diagnostics_are_computed_not_faked(self):
        d = news_quality_diagnostics(_real_news_frame())
        assert d["total_news_rows"] == 5
        assert d["non_neutral_rows"] == 5
        assert d["xauusd_relevant_rows"] == 5
        assert d["distinct_events"] == 5
        assert d["dead_zero_fields"] == []
        pf = d["per_field"]
        assert pf["news_state"]["unique"] >= 3
        assert pf["novelty"]["unique"] >= 4
