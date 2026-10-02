"""Live-log remediation: news ingest + slow-query observability regressions.

Pins defects observed in the 2026-10-01 live session log:

1. ``fed`` (OfficialSourceAdapter): a 304 conditional-GET success was
   converted into ``official feed returned no items`` FAILURE — punishing
   every healthy official source once it went quiet.
2. Sources with no feed_url cycled WARNINGS + exponential backoff forever
   (``failures=14, backoff=3600s``) — a config error treated as a transient
   network failure. Now reported ONCE and never re-polled.
3. The slow-query WARNING contract the "pg-read 1735ms" warnings depend on:
   ``log_slow_query`` must WARN above threshold, stay silent below it, and
   ``QueryTimer`` must never swallow the caller's result or exception.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from nexus_scalp.database.query_logging import (
    PG_SLOW_QUERY_THRESHOLD_MS,
    QueryTimer,
    log_slow_query,
)
from nexus_scalp.news.ingest import NewsFetcher
from nexus_scalp.news.sources.base import OfficialSourceAdapter, SourceFetchResult

# ---------------------------------------------------------------------------
# 1. Official 304 must stay a SUCCESS (the fed-feed defect)
# ---------------------------------------------------------------------------


class TestOfficial304:
    def test_304_is_success_not_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A 304 with zero items must return ok=True unchanged — NOT the
        'official feed returned no items' failure."""
        from nexus_scalp.news.sources import base as base_mod

        result_304 = SourceFetchResult(ok=True, items=[], status=304, error="not modified")
        monkeypatch.setattr(
            base_mod.RSSNewsSourceAdapter,
            "fetch",
            lambda self, limit=100: result_304,
        )
        adapter = OfficialSourceAdapter(source_config={"feed_url": "https://example.test/rss"})
        out = adapter.fetch()
        assert out.ok is True
        assert out.status == 304

    def test_empty_200_still_fails_strict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Only a genuine 200 body that parsed to zero items is the typed
        failure — strict validation is preserved for the real case."""
        from nexus_scalp.news.sources import base as base_mod

        result_200_empty = SourceFetchResult(ok=True, items=[], status=200)
        monkeypatch.setattr(
            base_mod.RSSNewsSourceAdapter,
            "fetch",
            lambda self, limit=100: result_200_empty,
        )
        adapter = OfficialSourceAdapter(source_config={"feed_url": "https://example.test/rss"})
        out = adapter.fetch()
        assert out.ok is False
        assert out.error == "official feed returned no items"


# ---------------------------------------------------------------------------
# 2. Misconfigured source: reported once, never re-polled
# ---------------------------------------------------------------------------


class _FakeNewsDB:
    """Minimal NewsDatabase surface the fetcher touches."""

    def __init__(self) -> None:
        self.health_rows: dict[str, dict[str, Any]] = {}

    def get_health(self, source_id: str) -> dict[str, Any] | None:
        return self.health_rows.get(source_id)

    def update_health(self, source_id: str, health: dict[str, Any]) -> None:
        self.health_rows[source_id] = dict(health)


class TestMisconfiguredSource:
    def _fetcher(self) -> NewsFetcher:
        f = NewsFetcher.__new__(NewsFetcher)
        f.db = _FakeNewsDB()
        f.config = None
        f._health = {}
        return f

    def test_misconfigured_reported_once_no_backoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """First poll: one WARNING, no failure counter, no backoff armed.
        Second poll: no new warning, still no backoff."""
        from nexus_scalp.news.ingest import fetcher as fetcher_mod

        records: list[tuple[str, tuple, dict]] = []

        def _rec(level: str) -> Any:
            def _fn(*args: Any, **kwargs: Any) -> None:
                records.append((level, args, kwargs))

            return _fn

        monkeypatch.setattr(
            fetcher_mod,
            "logger",
            SimpleNamespace(
                critical=_rec("critical"),
                error=_rec("error"),
                warning=_rec("warning"),
                info=_rec("info"),
                debug=_rec("debug"),
            ),
        )

        f = self._fetcher()
        src = {"source_id": "aggregator_test", "feed_url": ""}
        r1 = f.fetch_source(dict(src))
        f.fetch_source(dict(src))
        assert r1.ok is False
        assert "no feed_url configured" in r1.error
        health = f._health["aggregator_test"]
        assert health.get("misconfigured")
        assert health.get("consecutive_failures", 0) == 0
        assert not health.get("backoff_until")
        # Exactly ONE warning for the lifetime of the process.
        warnings = [r for r in records if r[0] == "warning"]
        assert len(warnings) == 1

    def test_misconfigured_source_skipped_on_next_cycle(self) -> None:
        """After the first misconfiguration, the ingest cycle must NOT re-poll
        the source (no more warnings, no counter churn)."""
        from nexus_scalp.news.engine import NewsEngine

        f = self._fetcher()
        engine = NewsEngine.__new__(NewsEngine)
        engine.fetcher = f

        # Prime: the fetcher has flagged the source.
        f.fetch_source({"source_id": "boe_test", "feed_url": ""})

        class _FakeEngineDB:
            def list_sources(self, enabled_only: bool = True) -> list[dict[str, Any]]:
                return [
                    {"source_id": "boe_test", "feed_url": "", "poll_interval_sec": 1},
                    {"source_id": "reuters", "feed_url": "https://r/rss", "poll_interval_sec": 1},
                ]

        engine.db = _FakeEngineDB()

        # Real scheduler over real (unpolled) sources: both due without the
        # filter; only reuters survives it.
        scheduler_hits: list[str] = []

        class _SpyScheduler:
            def due_sources(self, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
                scheduler_hits.extend(s["source_id"] for s in sources)
                return []

        engine.scheduler = _SpyScheduler()
        engine._stats = {}
        engine.ingest_cycle()
        assert scheduler_hits == ["reuters"], "misconfigured source must be filtered pre-scheduler"


# ---------------------------------------------------------------------------
# 3. Slow-query observability contract (the 'pg-read 1735ms' attribution)
# ---------------------------------------------------------------------------


class TestSlowQueryObservability:
    def test_under_threshold_is_silent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A fast query must never emit a WARNING (the fast path is one
        comparison and must never format a log line)."""
        from nexus_scalp.database import query_logging as ql

        emitted: list[tuple[tuple, dict]] = []
        monkeypatch.setattr(ql.logger, "warning", lambda *a, **k: emitted.append((a, k)))
        log_slow_query(operation="query", duration_ms=1.0, sql="SELECT 1")
        assert emitted == []

    def test_over_threshold_warns_with_attribution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Above threshold the WARNING carries the operation, the measured
        duration, the threshold and the (masked) statement — the attribution
        the 'pg-read 1735ms' warnings exist to provide."""
        from nexus_scalp.database import query_logging as ql

        emitted: list[dict] = []
        monkeypatch.setattr(
            ql.logger, "warning", lambda *a, **k: emitted.append({"args": a, "kwargs": k})
        )
        log_slow_query(
            operation="query",
            duration_ms=PG_SLOW_QUERY_THRESHOLD_MS + 100.0,
            sql="SELECT * FROM audit_ledger",
        )
        assert emitted, "a query over threshold must emit a warning"
        ctx = emitted[-1]["kwargs"]
        assert ctx["operation"] == "query"
        assert float(ctx["duration_ms"]) >= PG_SLOW_QUERY_THRESHOLD_MS
        assert "audit_ledger" in str(ctx["sql"])

    def test_query_timer_records_duration(self) -> None:
        with QueryTimer("query", "SELECT 1") as t:
            pass
        assert isinstance(t.duration_ms, float)

    def test_query_timer_propagates_exceptions(self) -> None:
        """The timer must never swallow the wrapped exception."""

        class _BoomError(Exception):
            pass

        with pytest.raises(_BoomError):
            with QueryTimer("query", "SELECT 1"):
                raise _BoomError("propagates unchanged")
