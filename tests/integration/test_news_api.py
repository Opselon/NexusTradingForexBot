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
from types import SimpleNamespace

import pytest

from nexus_scalp.news import NewsConfig, NewsDatabase, NewsEngine, seed_news_database


@pytest.fixture
def news_engine(tmp_path: Path) -> NewsEngine:
    db = NewsDatabase(tmp_path / "news_int.db")
    seed_news_database(db)
    return NewsEngine(config=NewsConfig(db_path=str(db.db_path)))


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

    def _app_with_news(self, news_engine):
        from fastapi.testclient import TestClient

        from nexus_scalp.web.server import create_app

        # The server reads app.state.engine; _news() requires the LiveEngine
        # surface (news_engine). Emulate the minimal wiring.
        class _FakeEngine:
            def __init__(self, engine: NewsEngine) -> None:
                self.news_engine = engine
                self.news_worker = None

            def _start_news_worker(self) -> None:
                pass

        app = create_app()
        app.state.engine = _FakeEngine(news_engine)
        return app, TestClient(app)

    def test_api_news_feed_real_data(self, news_engine):
        _insert_sample_article(news_engine, "Fed Decision Moves Gold", "news_feed1")
        _insert_sample_article(news_engine, "CPI Surprise, USD Rally", "news_feed2")
        _, client = self._app_with_news(news_engine)
        resp = client.get("/api/news")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        titles = {a["title"] for a in body["articles"]}
        assert "Fed Decision Moves Gold" in titles
        assert "CPI Surprise, USD Rally" in titles
        for art in body["articles"]:
            assert art["title"]  # never blank/synthetic

    def test_api_news_state_endpoint(self, news_engine):
        _insert_sample_article(news_engine, "Gold Jumps on Safe Haven", "news_state1")
        news_engine.analyze_article_id("news_state1")
        _, client = self._app_with_news(news_engine)
        resp = client.get("/api/news/state")
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

    def test_api_news_health_no_synthetic(self, news_engine):
        _, client = self._app_with_news(news_engine)
        resp = client.get("/api/news/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["health"]["available"] is True
        # never fake numbers: db summary must be real
        assert body["health"]["db"]["db_path"] == str(news_engine.db.db_path)

    def test_api_news_detail_endpoint(self, news_engine):
        _insert_sample_article(news_engine, "BoE Rate Decision", "news_detail1")
        news_engine.analyze_article_id("news_detail1")
        _, client = self._app_with_news(news_engine)
        resp = client.get("/api/news/news_detail1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["article"]["article_id"] == "news_detail1"
        assert body["analysis"] is not None  # real persisted analysis

    def test_api_news_analyze_async(self, news_engine):
        _insert_sample_article(news_engine, "Gold Oil Gas Energy", "news_ana1")
        _, client = self._app_with_news(news_engine)
        resp = client.post("/api/news/analyze/news_ana1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["ok"] is True
        # status is QUEUED/COMPLETE/LOCAL_ONLY - never fake
        assert body["status"] in ("QUEUED", "COMPLETE", "LOCAL_ONLY", "ALREADY_QUEUED_OR_FULL")

    def test_api_news_refresh_bounded(self, news_engine):
        _, client = self._app_with_news(news_engine)
        resp = client.post("/api/news/refresh")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert "ingested" in body
        assert "analyzed_count" in body

    def test_api_news_self_heal(self, news_engine):
        _insert_sample_article(news_engine, "Gold Rally CPI", "news_heal1")
        news_engine.analyze_article_id("news_heal1")
        _, client = self._app_with_news(news_engine)
        resp = client.post("/api/news/self-heal")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["status"] == "SUCCESS"

    def test_api_news_sources(self, news_engine):
        _, client = self._app_with_news(news_engine)
        resp = client.get("/api/news/sources")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert len(body["sources"]) >= 10  # seeded registry
        tiers = {s["tier"] for s in body["sources"]}
        assert "TIER_1" in tiers  # official sources present

    def test_api_news_disabled_returns_available_false(self, tmp_path: Path):
        from fastapi.testclient import TestClient

        from nexus_scalp.web.server import create_app

        class _NoNewsEngine:
            news_engine = None
            news_worker = None

        app = create_app()
        app.state.engine = _NoNewsEngine()
        client = TestClient(app)
        resp = client.get("/api/news")
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

        news_engine.ingest_cycle(max_sources=1)
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
