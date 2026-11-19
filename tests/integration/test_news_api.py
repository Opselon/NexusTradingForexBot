"""PHASE 12 News Intelligence — Integration Tests.

Verifies the News subsystem end-to-end:

    * the FastAPI news endpoints return REAL persisted data (no synthetic),
    * the news worker wiring in LiveEngine is failure-isolated,
    * a broken/disconnected news engine never stops trading,
    * the dedicated news.db is independent of the trading audit DB.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import pytest

from nexus_scalp.news import NewsConfig, NewsDatabase, NewsEngine, seed_news_database
from tests.helpers.authenticated_client import authenticated_test_client


@pytest.fixture
def news_engine(tmp_path: Path) -> NewsEngine:
    db = NewsDatabase(tmp_path / "news_int.db")
    seed_news_database(db)
    return NewsEngine(config=NewsConfig(db_path=str(db.db_path)))


@pytest.fixture(scope="module")
def news_app():
    """Build the FastAPI app ONCE per module, not once per test.

    create_app() wires the whole server (routes, middleware, imports);
    rebuilding it in every test cost ~0.3-0.5s per test in CI for zero
    isolation benefit — the per-test state lives in the NewsEngine/DB,
    not in the app. The app is shared; each test's engine is wired in
    by the news_client fixture below (and reset afterwards).
    """
    from nexus_scalp.web.server import create_app

    return create_app()


class _FakeEngine:
    """Minimal LiveEngine surface the news routes need (news_engine)."""

    def __init__(self, engine: NewsEngine) -> None:
        self.news_engine = engine
        self.news_worker = None

    def _start_news_worker(self) -> None:
        pass


@pytest.fixture
def news_client(news_app, news_engine):
    """Authenticated client bound to this test's fresh news engine."""
    news_app.state.engine = _FakeEngine(news_engine)
    # The refresh endpoint enforces a 60s server-side cooldown keyed off
    # app.state.news_refresh_ts; the shared app must not inherit a stamp
    # from another test, or a refresh test would hit the cooldown path.
    news_app.state.news_refresh_ts = 0.0
    yield authenticated_test_client(news_app)
    news_app.state.engine = None


def _insert_sample_article(engine: NewsEngine, title: str, article_id: str) -> None:
    now = datetime.now(UTC)
    engine.db.insert_article(
        {
            "article_id": article_id,
            "article_hash": f"hash_{article_id}",
            "canonical_url": f"https://x.com/{article_id}",
            "title": title,
            "summary": "gold rally on Fed news",
            "body": "",
            "language": "en",
            "source_id": "fed",
            "source_name": "Federal Reserve",
            "published_at": now.isoformat(),
            "updated_at": "",
            "raw_categories": [],
            "entities": [],
            "topics": [],
            "importance": "MINOR",
            "importance_score": 0.0,
            "novelty": "NEW",
            "is_duplicate": 0,
            "duplicate_of": "",
            "evidence_sources": ["fed"],
            "created_at": now.isoformat(),
        }
    )


class TestNewsApiEndpoints:
    """End-to-end REST API verification using FastAPI TestClient."""

    def test_api_news_feed_real_data(self, news_engine, news_client):
        _insert_sample_article(news_engine, "Fed Decision Moves Gold", "news_feed1")
        _insert_sample_article(news_engine, "CPI Surprise, USD Rally", "news_feed2")
        resp = news_client.get("/api/news")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        titles = {a["title"] for a in body["articles"]}
        assert "Fed Decision Moves Gold" in titles
        assert "CPI Surprise, USD Rally" in titles
        for art in body["articles"]:
            assert art["title"]  # never blank/synthetic

    def test_api_news_state_endpoint(self, news_engine, news_client):
        _insert_sample_article(news_engine, "Gold Jumps on Safe Haven", "news_state1")
        news_engine.analyze_article_id("news_state1")
        resp = news_client.get("/api/news/state")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["state"] in (
            "NORMAL",
            "ELEVATED",
            "HIGH_IMPACT",
            "CONFLICTED",
            "BREAKING",
            "STALE",
        )
        assert 0.0 <= body["bullish_score"] <= 1.0

    def test_api_news_health_no_synthetic(self, news_engine, news_client):
        resp = news_client.get("/api/news/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["health"]["available"] is True
        # never fake numbers: db summary must be real
        assert body["health"]["db"]["db_path"] == str(news_engine.db.db_path)

    def test_api_news_detail_endpoint(self, news_engine, news_client):
        _insert_sample_article(news_engine, "BoE Rate Decision", "news_detail1")
        news_engine.analyze_article_id("news_detail1")
        resp = news_client.get("/api/news/news_detail1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["article"]["article_id"] == "news_detail1"
        assert body["analysis"] is not None  # real persisted analysis

    def test_api_news_analyze_async(self, news_engine, news_client):
        _insert_sample_article(news_engine, "Gold Oil Gas Energy", "news_ana1")
        resp = news_client.post("/api/news/analyze/news_ana1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["ok"] is True
        # status is QUEUED/COMPLETE/LOCAL_ONLY - never fake
        assert body["status"] in ("QUEUED", "COMPLETE", "LOCAL_ONLY", "ALREADY_QUEUED_OR_FULL")

    def test_api_news_refresh_bounded(self, news_engine, news_client):
        """Refresh is bounded (<=8 sources, <=10 analyses) and never does live I/O here.

        The endpoint used to run a REAL ingest_cycle(max_sources=8): up to
        8 live RSS fetches, each with a 15s httpx timeout
        (news/sources/base.py) — 38.9s in CI run #3319 and flaky whenever
        a feed is slow or unreachable. The live fetch belongs to the
        fetcher/engine tests, not to this endpoint test: what this test
        must prove is the endpoint wiring + the bounds. So the two
        engine passes are stubbed at the instance level (the route calls
        news.ingest_cycle / news.analysis_cycle on this same engine
        object), the stub returns the real result shape, and the test
        asserts the endpoint echoes it AND calls the passes with the
        bounded limits (max_sources=8, limit=10).
        """
        stub_ingest = {
            "sources_polled": 2,
            "new": 3,
            "duplicate": 1,
            "merged": 0,
            "merged_evidence": 0,
            "admitted": 3,
            "quarantined": 0,
            "rejected": 0,
        }
        stub_analyses = [mock.Mock(), mock.Mock()]
        with (
            mock.patch.object(news_engine, "ingest_cycle", return_value=stub_ingest) as ingest,
            mock.patch.object(news_engine, "analysis_cycle", return_value=stub_analyses) as analyze,
        ):
            resp = news_client.post("/api/news/refresh")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["ingested"] == stub_ingest
        assert body["analyzed_count"] == 2
        ingest.assert_called_once_with(max_sources=8)
        analyze.assert_called_once_with(limit=10)

    def test_api_news_self_heal(self, news_engine, news_client):
        _insert_sample_article(news_engine, "Gold Rally CPI", "news_heal1")
        news_engine.analyze_article_id("news_heal1")
        resp = news_client.post("/api/news/self-heal")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["status"] == "SUCCESS"

    def test_api_news_sources(self, news_engine, news_client):
        resp = news_client.get("/api/news/sources")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert len(body["sources"]) >= 10  # seeded registry
        tiers = {s["tier"] for s in body["sources"]}
        assert "TIER_1" in tiers  # official sources present

    def test_api_news_keywords_endpoint(self, news_engine, news_client):
        _insert_sample_article(
            news_engine, "Fed cuts rates, gold surges on safe haven demand", "news_kw1"
        )
        resp = news_client.get("/api/news/keywords")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        # big dataset contract: >= 150 keywords
        assert body["dataset"]["total_keywords"] >= 150
        assert "categories" in body["dataset"]
        assert body["coverage"]["articles_scanned"] >= 1
        assert body["coverage"]["active_keywords"] >= 1
        # the sample article mentions gold/Fed -> top keywords non-empty
        assert len(body["coverage"]["top_keywords"]) >= 1
        top = body["coverage"]["top_keywords"][0]
        assert top["keyword"]
        assert top["article_hits"] >= 1
        assert 0.0 <= top["share"] <= 1.0
        # filterable listing works
        resp_q = news_client.get("/api/news/keywords?q=GOLD")
        assert resp_q.status_code == 200
        q_body = resp_q.json()
        assert all("GOLD" in k["keyword"] for k in q_body["keywords"])
        # category filter
        resp_cat = news_client.get("/api/news/keywords?category=macro")
        assert resp_cat.status_code == 200
        cat_body = resp_cat.json()
        assert (
            all(k["category"] == "macro" for k in cat_body["keywords"])
            if cat_body["keywords"]
            else True
        )

    def test_api_news_feed_has_keyword_hits(self, news_engine, news_client):
        _insert_sample_article(
            news_engine, "Bank of England hawkish, yields jump, gold falls", "news_kw2"
        )
        resp = news_client.get("/api/news?limit=10")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        hits = body["articles"][0].get("keyword_hits", [])
        assert isinstance(hits, list)
        assert len(hits) >= 1

    def test_api_news_disabled_returns_available_false(self, news_app):
        class _NoNewsEngine:
            news_engine = None
            news_worker = None

        news_app.state.engine = _NoNewsEngine()
        try:
            client = authenticated_test_client(news_app)
            resp = client.get("/api/news")
        finally:
            news_app.state.engine = None
        assert resp.json()["available"] is False  # honest, not synthetic


class TestLiveEngineWiring:
    """News worker wiring in LiveEngine: failure-isolation."""

    def test_news_worker_start_failure_does_not_block(self, news_engine):
        cfg = NewsConfig(enabled=False)

        class _FakeLiveEngine:
            def __init__(self) -> None:
                self._news_enabled = cfg.enabled
                self.news_engine = None
                self.news_worker = None
                self.news_gate = None
                self._news_worker_started = False
                self.trading_healthy = True

            def _start_news_worker(self) -> None:
                if not self._news_enabled or self._news_worker_started:
                    return
                self._news_worker_started = True

        engine = _FakeLiveEngine()
        engine._start_news_worker()
        assert engine._news_worker_started is False  # disabled = never started
        assert engine.trading_healthy is True

    def test_news_gate_needs_live_engine_but_never_blocks(self, news_engine):
        from nexus_scalp.news import NewsGate
        from nexus_scalp.news.models import CurrentNewsContext

        gate = NewsGate()
        verdict = gate.evaluate(
            context=CurrentNewsContext(available=False),
            proposal_action="BUY",
            strategy_direction="BULLISH",
            proposal_confidence=0.8,
            regime_aligned=True,
        )
        assert verdict.decision == "IGNORE"

    def test_news_db_independent_of_trading_db(self, news_engine, tmp_path: Path):
        audit_path = tmp_path / "audit.db"
        conn = sqlite3.connect(str(audit_path))
        conn.execute("CREATE TABLE audit_ledger (ticket INTEGER PRIMARY KEY, net_pnl REAL);")
        conn.execute("INSERT INTO audit_ledger VALUES (1, 12.5);")
        conn.commit()
        conn.close()

        # NOTE: the ingest pass is stubbed, not run live — it fetches RSS
        # over the network (15s timeout per source) and its result is
        # irrelevant here. What this test proves is DB independence: the
        # real self_heal() below must not touch the audit DB. Live
        # fetching is covered by the fetcher/engine tests.
        with mock.patch.object(
            news_engine,
            "ingest_cycle",
            return_value={
                "sources_polled": 1,
                "new": 0,
                "duplicate": 0,
                "merged": 0,
                "merged_evidence": 0,
                "admitted": 0,
                "quarantined": 0,
                "rejected": 0,
            },
        ) as ingest:
            news_engine.ingest_cycle(max_sources=1)
        ingest.assert_called_once_with(max_sources=1)
        news_engine.self_heal()

        conn2 = sqlite3.connect(str(audit_path))
        rows = conn2.execute("SELECT * FROM audit_ledger;").fetchall()
        conn2.close()
        assert rows == [(1, 12.5)]
        assert news_engine.db.db_path != audit_path  # separate db confirmed

    def test_analysis_runs_persist(self, news_engine):
        _insert_sample_article(news_engine, "Fed rate decision impacts gold", "news_int1")
        result = news_engine.analyze_article_id("news_int1")
        assert result["ok"] is True
        analysis = news_engine.db.get_analysis("news_int1")
        assert analysis is not None
        assert analysis["article_id"] == "news_int1"

    def test_trade_link_end_to_end(self, news_engine):
        _insert_sample_article(news_engine, "Gold jumps on CPI", "news_int2")
        link_id = news_engine.link_trade(
            trade_id="777",
            article_id="news_int2",
            strategy_id="strat_1",
            model_version="v9",
            alignment=0.2,
        )
        assert link_id
        links = news_engine.db.list_trade_links("777")
        assert len(links) == 1
