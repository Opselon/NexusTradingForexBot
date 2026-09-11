"""BUG-258 — news DB list caps silently truncated explicit caller limits.

Pre-fix contract: ``AnalysisMixin.list_analysis`` and
``ArticlesMixin.list_articles`` clamped EVERY request to 500 rows
(``bounded = max(1, min(int(limit), 500))``). Callers that explicitly
requested more ran on a silently narrowed NEWEST-500 slice:

  * ``model_generation.news_bridge.build_news_frame_from_db`` (limit=2000)
    — the dataset news-frame export: the OLDEST analyzed rows were dropped
    first, so news context for older history silently became zero and a
    news-aware retrain (the documented >=1y data prerequisite) could never
    be met honestly. With >500 stored analyses the window export also
    silently lost the OLDEST rows inside the requested window
    (probe: 300 rows stored in window, 295 exported).
  * ``news.pro_auto.run_pro_cycle`` gold-first drain pool (2000)
  * ``news.ai_service.auto_prune_irrelevant`` (2000)

Post-fix contract (pinned here):
  1. caller limits above 500 are HONORED up to the module hard caps;
  2. the hard caps still bound memory (a small monkeypatched cap truncates);
  3. cap truncation keeps the NEWEST rows (deterministic, documented);
  4. a clamping call logs exactly ONE WARNING via the module's stdlib
     logger (stderr-safe; BUG-112/118 discipline: never caplog/root);
  5. ``build_news_frame_from_db`` defaults to exporting EVERY analysis in
     the requested window (limit=None) — the dataset money path must never
     silently narrow history.

All timestamps are injected, deterministic UTC strings (no wall clock).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

import nexus_scalp.news.db_analysis as db_analysis_module
import nexus_scalp.news.db_articles as db_articles_module
from nexus_scalp.model_generation.news_bridge import build_news_frame_from_db
from nexus_scalp.news.database import NewsDatabase

_N = 505  # > old 500 cap
_BASE = datetime(2026, 8, 10, 0, 0, 0, tzinfo=UTC)


def _pub_iso(i: int) -> str:
    """Deterministic unique publication time: i minutes after BASE."""
    return (_BASE + timedelta(minutes=i)).isoformat()


def _analyzed_iso(i: int) -> str:
    """Deterministic unique analyzed_at: publication + 1h (ascending in i)."""
    return (_BASE + timedelta(minutes=i) + timedelta(hours=1)).isoformat()


@pytest.fixture(scope="module")
def seeded_db(tmp_path_factory: pytest.TempPathFactory) -> NewsDatabase:
    """One DB with 505 analyzed articles, unique ascending timestamps."""
    db = NewsDatabase(tmp_path_factory.mktemp("bug258") / "news.db")
    for i in range(_N):
        db.insert_article(
            {
                "article_id": f"news_{i:012d}",
                "article_hash": f"hash_{i:012d}",
                "title": f"CPI print {i} shakes gold",
                "source_id": "probe",
                "source_name": "Probe",
                "published_at": _pub_iso(i),
            }
        )
        db.insert_analysis(
            {
                "analysis_id": f"ana_{i:012d}",
                "article_id": f"news_{i:012d}",
                "run_id": "run_bug258",
                "direction": "BULLISH" if i % 2 else "BEARISH",
                "impact_strength": 0.5,
                "confidence": 0.6,
                "importance": "HIGH",
                "importance_score": 0.7,
                "relevance_to_xauusd": 0.8,
                "relevance_to_usd": 0.5,
                "novelty": "NEW",
                "analyzed_at": _analyzed_iso(i),
            }
        )
    return db


class TestListAnalysisHonesty:
    def test_caller_limit_above_old_cap_is_honored(self, seeded_db: NewsDatabase) -> None:
        rows = seeded_db.list_analysis(limit=2000)
        assert len(rows) == _N, (
            "BUG-258: explicit limit=2000 must not be clamped to the old 500 cap"
        )

    def test_small_requests_unchanged(self, seeded_db: NewsDatabase) -> None:
        assert len(seeded_db.list_analysis(limit=5)) == 5
        assert len(seeded_db.list_analysis()) == 50  # documented default

    def test_truncation_keeps_newest_rows(
        self, seeded_db: NewsDatabase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(db_analysis_module, "ANALYSIS_LIST_HARD_CAP", 2)
        rows = seeded_db.list_analysis(limit=10)
        ids = {r["article_id"] for r in rows}
        assert ids == {f"news_{_N - 1:012d}", f"news_{_N - 2:012d}"}, (
            "cap truncation must keep the NEWEST analyzed rows"
        )


class TestListArticlesHonesty:
    def test_caller_limit_above_old_cap_is_honored(self, seeded_db: NewsDatabase) -> None:
        rows = seeded_db.list_articles(limit=2000, include_duplicates=False)
        assert len(rows) == _N, (
            "BUG-258: explicit limit=2000 must not be clamped to the old 500 cap"
        )


class TestTruncationWarning:
    def test_clamping_call_warns_exactly_once(
        self, seeded_db: NewsDatabase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        probe = Mock()
        monkeypatch.setattr(db_analysis_module, "logger", probe)
        seeded_db.list_analysis(limit=10**9)
        assert probe.warning.call_count == 1, (
            "a request above the hard cap must log exactly one WARNING"
        )

    def test_non_clamping_call_is_silent(
        self, seeded_db: NewsDatabase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        probe = Mock()
        monkeypatch.setattr(db_analysis_module, "logger", probe)
        seeded_db.list_analysis(limit=2000)
        assert probe.warning.call_count == 0

    def test_articles_clamping_call_warns(
        self, seeded_db: NewsDatabase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        probe = Mock()
        monkeypatch.setattr(db_articles_module, "logger", probe)
        seeded_db.list_articles(limit=10**9, include_duplicates=False)
        assert probe.warning.call_count == 1


class TestNewsFrameExport:
    def test_full_window_export_is_complete(self, seeded_db: NewsDatabase) -> None:
        start = _BASE - timedelta(minutes=1)
        end = _BASE + timedelta(minutes=_N * 2)
        frame = build_news_frame_from_db(seeded_db, start=start, end=end)
        assert frame is not None
        assert frame.height == _N, (
            "BUG-258: the dataset news-frame export must cover the WHOLE "
            "requested window, not the newest 500 analyses"
        )

    def test_window_filter_still_applies(self, seeded_db: NewsDatabase) -> None:
        # Window over the first 300 publications (i=0..299).
        start = _BASE - timedelta(minutes=1)
        end = _BASE + timedelta(minutes=299)
        frame = build_news_frame_from_db(seeded_db, start=start, end=end)
        assert frame is not None
        assert frame.height == 300, (
            "pre-fix this silently exported 295 of 300 (oldest window rows "
            "dropped by the newest-500 cap before the window filter)"
        )

    def test_default_export_is_unbounded(self, seeded_db: NewsDatabase) -> None:
        frame = build_news_frame_from_db(seeded_db)
        assert frame is not None
        assert frame.height == _N, "limit=None default must export every analysis (no silent cap)"
