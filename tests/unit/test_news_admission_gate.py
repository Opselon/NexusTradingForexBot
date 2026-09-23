"""Pre-DB News Admission Gateway — unit + integration tests (news-admission-gate).

Covers:
    * STAGE 0 hard safety (garbage title reject, no DB calls),
    * source-quality tiering (tier weights honored, unknown = neutral),
    * market-relevance gating via the REAL LocalNewsAnalyzer,
    * deterministic high-impact recall protection (CPI/FOMC titles admit),
    * tier decisions ADMIT / QUARANTINE / REJECT with reason codes,
    * tombstone-on-reject (recoverable junk hash — same path as auto-prune),
    * quarantine rows persist OUT of the active feed (article_status),
    * ingest-path integration (fetch result -> gateway -> DB write shape),
    * metrics funnel counters,
    * fail-open on gateway internal error (ingest never breaks),
    * backward compat: gateway=None keeps the legacy ingest behavior.

Run:  ./.venv/Scripts/python.exe -m pytest tests/unit/test_news_admission_gate.py -p no:cacheprovider
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from nexus_scalp.news.admission import (
    AdmissionDecision,
    AdmissionVerdict,
    NewsAdmissionConfig,
    NewsAdmissionGateway,
)
from nexus_scalp.news.database import NewsDatabase
from nexus_scalp.news.ingest import NewsIngestor, canonicalize_item
from nexus_scalp.news.sources import SourceFetchResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def news_db(tmp_path):
    return NewsDatabase(tmp_path / "news_admission_test.db")


@pytest.fixture()
def seeded_source(news_db: NewsDatabase):
    news_db.upsert_source(
        {
            "source_id": "reuters_test",
            "name": "Reuters Test",
            "kind": "RSS",
            "tier": "TIER_1",
            "enabled": True,
        }
    )
    news_db.upsert_source(
        {
            "source_id": "aggregator_test",
            "name": "Low Tier Aggregator",
            "kind": "RSS",
            "tier": "TIER_4",
            "enabled": True,
        }
    )
    return news_db


def _item(
    title: str = "Federal Reserve cuts interest rates by 25 basis points",
    summary: str = "The Federal Open Market Committee lowered the benchmark rate "
    "citing inflation progress. Gold rose and the dollar fell on the decision.",
    url: str = "https://example.com/fed-cuts-rates",
    published: datetime | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "title": title,
        "url": url,
        "summary": summary,
        "body": "",
        "published_at": published or datetime.now(UTC),
        "updated_at": None,
        "categories": [],
        **extra,
    }


GOLD_TITLE = "Federal Reserve cuts interest rates by 25 basis points"
GOLD_SUMMARY = (
    "The Federal Open Market Committee lowered the benchmark rate citing "
    "inflation progress. Gold rose and the dollar fell as real yields drop."
)
JUNK_TITLE = "Best warehouse club deals on patio furniture this weekend"
JUNK_SUMMARY = "Shoppers find discounted patio sets and summer grills at the local retailer. Lifestyle roundup."


# ---------------------------------------------------------------------------
# Unit: gateway stages
# ---------------------------------------------------------------------------


class TestStage0HardSafety:
    def test_empty_title_rejected_without_db_or_analysis(self, news_db):
        gw = NewsAdmissionGateway(news_db)
        verdict = gw.evaluate({"title": "", "url": "https://x.com/a"})
        assert verdict.decision == AdmissionDecision.REJECT
        assert "INVALID_TITLE" in verdict.reason_codes
        assert verdict.processing_cost == "STAGE0"
        assert gw.metrics.rejected_before_scoring == 1

    def test_short_garbage_title_rejected(self, news_db):
        gw = NewsAdmissionGateway(news_db)
        verdict = gw.evaluate({"title": "ok fine", "url": "https://x.com/a"})
        assert verdict.decision == AdmissionDecision.REJECT
        assert verdict.processing_cost == "STAGE0"


class TestSourceQuality:
    def test_tier_weights_reused(self, seeded_source):
        gw = NewsAdmissionGateway(seeded_source)
        assert gw._source_quality("reuters_test", {}) == 1.0
        assert gw._source_quality("aggregator_test", {}) == 0.25
        assert gw._source_quality("unknown_src", {}) == 0.5  # neutral, never reject

    def test_quality_cached(self, seeded_source):
        gw = NewsAdmissionGateway(seeded_source)
        gw._source_quality("reuters_test", {})
        seeded_source.set_source_enabled("reuters_test", False)
        # cached value survives (bounded lookups per source)
        assert gw._source_quality("reuters_test", {}) == 1.0


class TestRelevanceAndTiers:
    def test_gold_macro_event_admits(self, seeded_source):
        gw = NewsAdmissionGateway(seeded_source)
        verdict = gw.evaluate(
            canonicalize_item(
                _item(title=GOLD_TITLE, summary=GOLD_SUMMARY), "reuters_test", "Reuters"
            )
        )
        assert verdict.decision == AdmissionDecision.ADMIT
        assert verdict.relevance_score > 0.25
        assert "HIGH_INFORMATION_VALUE" in verdict.reason_codes

    def test_deterministic_scheduled_release_recalled(self, seeded_source):
        """A CPI title is classified by the deterministic calendar classifier
        and admits on RELEVANT_MACRO_EVENT even with a low tier source."""
        gw = NewsAdmissionGateway(seeded_source)
        verdict = gw.evaluate(
            canonicalize_item(
                _item(title="US CPI inflation data released", summary="Consumer prices rose"),
                "aggregator_test",
                "Low Tier",
            )
        )
        assert verdict.decision == AdmissionDecision.ADMIT
        assert "MARKET_MOVING" in verdict.reason_codes
        assert "RELEVANT_MACRO_EVENT" in verdict.reason_codes

    def test_junk_rejected_with_reason_codes(self, seeded_source):
        gw = NewsAdmissionGateway(seeded_source)
        verdict = gw.evaluate(
            canonicalize_item(
                _item(title=JUNK_TITLE, summary=JUNK_SUMMARY), "reuters_test", "Reuters"
            )
        )
        assert verdict.decision == AdmissionDecision.REJECT
        assert any(
            r in verdict.reason_codes
            for r in ("LOW_MARKET_RELEVANCE", "TRIVIAL_IMPORTANCE", "LOW_INFORMATION_VALUE")
        )

    def test_quarantine_band(self, seeded_source):
        """Mid-score items land QUARANTINE, not silent admit/reject."""
        cfg = NewsAdmissionConfig(admit_score=0.99, review_score=0.05)
        gw = NewsAdmissionGateway(seeded_source, config=cfg)
        verdict = gw.evaluate(
            canonicalize_item(
                _item(title=GOLD_TITLE, summary=GOLD_SUMMARY), "reuters_test", "Reuters"
            )
        )
        assert verdict.decision == AdmissionDecision.QUARANTINE
        assert "REVIEW_REQUIRED" in verdict.reason_codes

    def test_staleness_is_a_signal_not_a_rejector(self, seeded_source):
        """Article age alone never rejects — feeds legitimately carry older
        items; low VALUE is decided by the content floors, not by age."""
        gw = NewsAdmissionGateway(seeded_source)
        old = datetime.now(UTC) - timedelta(hours=100)

        # stale junk still rejects — on its CONTENT merits, with STALE_CONTENT
        # recorded alongside as a signal
        verdict = gw.evaluate(
            canonicalize_item(
                _item(title=JUNK_TITLE, summary=JUNK_SUMMARY, published=old),
                "reuters_test",
                "Reuters",
            )
        )
        assert verdict.decision == AdmissionDecision.REJECT
        assert "STALE_CONTENT" in verdict.reason_codes

        # stale market-relevant content is NOT age-rejected
        verdict_mm = gw.evaluate(
            canonicalize_item(
                _item(
                    title="US CPI inflation data released",
                    summary="Consumer prices rose",
                    published=old,
                ),
                "reuters_test",
                "Reuters",
            )
        )
        assert verdict_mm.decision in (AdmissionDecision.ADMIT, AdmissionDecision.QUARANTINE)

        # fresh content carries no STALE_CONTENT signal
        fresh = gw.evaluate(
            canonicalize_item(
                _item(title=GOLD_TITLE, summary=GOLD_SUMMARY), "reuters_test", "Reuters"
            )
        )
        assert "STALE_CONTENT" not in fresh.reason_codes

    def test_verdict_is_explainable_dict(self, seeded_source):
        gw = NewsAdmissionGateway(seeded_source)
        v = gw.evaluate(
            canonicalize_item(
                _item(title=GOLD_TITLE, summary=GOLD_SUMMARY), "reuters_test", "Reuters"
            )
        )
        d = v.to_dict()
        assert set(d) >= {
            "decision",
            "score",
            "reason_codes",
            "source_quality",
            "relevance_score",
            "impact_score",
            "confidence",
            "duplicate_status",
            "processing_cost",
            "timestamp",
        }
        assert all(isinstance(r, str) and r for r in d["reason_codes"])


class TestFailOpen:
    def test_internal_error_never_breaks_ingest(self, news_db):
        class _BoomDB:
            def get_source(self, source_id):
                raise RuntimeError("boom")

        gw = NewsAdmissionGateway(_BoomDB())
        # get_source raises but is suppressed inside _source_quality —
        # if the suppression regresses, the fail-open verdict still ADMITs.
        verdict = gw.evaluate(
            {
                "title": GOLD_TITLE,
                "summary": GOLD_SUMMARY,
                "body": "",
                "source_id": "x",
                "published_at": datetime.now(UTC),
            }
        )
        assert verdict.decision in (AdmissionDecision.ADMIT, AdmissionDecision.QUARANTINE)


# ---------------------------------------------------------------------------
# Integration: ingest path
# ---------------------------------------------------------------------------


class _FakeResult(SourceFetchResult):
    pass


def _fetch_result(items: list[dict[str, Any]]) -> SourceFetchResult:
    return SourceFetchResult(ok=True, items=items, status=200)


class TestIngestPath:
    def test_rejected_item_never_reaches_db_and_is_tombstoned(self, seeded_source):
        gw = NewsAdmissionGateway(seeded_source)
        ingestor = NewsIngestor(seeded_source, admission_gateway=gw)
        stats = ingestor.ingest_source_items(
            {"source_id": "reuters_test", "name": "Reuters Test", "tier": "TIER_1"},
            _fetch_result([_item(title=JUNK_TITLE, summary=JUNK_SUMMARY)]),
        )
        assert stats["rejected"] == 1
        assert stats["new"] == 0
        assert seeded_source.count_articles() == 0
        # tombstoned: the same story can never re-enter on re-poll
        canon = canonicalize_item(
            _item(title=JUNK_TITLE, summary=JUNK_SUMMARY), "reuters_test", "r"
        )
        assert seeded_source.is_junk_hash(canon["article_hash"])

    def test_admitted_item_persists_active(self, seeded_source):
        gw = NewsAdmissionGateway(seeded_source)
        ingestor = NewsIngestor(seeded_source, admission_gateway=gw)
        stats = ingestor.ingest_source_items(
            {"source_id": "reuters_test", "name": "Reuters Test", "tier": "TIER_1"},
            _fetch_result([_item(title=GOLD_TITLE, summary=GOLD_SUMMARY)]),
        )
        assert stats["new"] == 1
        assert stats["admitted"] == 1
        rows = seeded_source.list_articles(include_duplicates=True)
        assert len(rows) == 1
        assert rows[0]["article_status"] == "ACTIVE"

    def test_quarantined_item_persists_out_of_active_feed(self, seeded_source):
        cfg = NewsAdmissionConfig(admit_score=0.99, review_score=0.05)
        gw = NewsAdmissionGateway(seeded_source, config=cfg)
        ingestor = NewsIngestor(seeded_source, admission_gateway=gw)
        stats = ingestor.ingest_source_items(
            {"source_id": "reuters_test", "name": "Reuters Test", "tier": "TIER_1"},
            _fetch_result([_item(title=GOLD_TITLE, summary=GOLD_SUMMARY)]),
        )
        assert stats["quarantined"] == 1
        # persisted... but NOT in the active feed
        assert seeded_source.list_articles(include_duplicates=False) == []
        rows = seeded_source.list_articles(include_duplicates=True, status_filter="QUARANTINE")
        assert len(rows) == 1
        assert rows[0]["article_status"] == "QUARANTINE"
        # quarantine carries an explainable audit row
        audits = seeded_source.db if False else None  # noqa: F841
        with seeded_source._connect() as conn:
            audit = conn.execute(
                "SELECT operation, actor, reason FROM news_prune_audit WHERE article_id = ?;",
                (rows[0]["article_id"],),
            ).fetchone()
        assert audit is not None
        assert audit["operation"] == "ADMISSION_QUARANTINE"
        assert audit["actor"] == "admission_gateway"

    def test_no_gateway_preserves_legacy_behavior(self, seeded_source):
        """Backward compat: gateway=None ingests everything (legacy path)."""
        ingestor = NewsIngestor(seeded_source)
        stats = ingestor.ingest_source_items(
            {"source_id": "reuters_test", "name": "Reuters Test", "tier": "TIER_1"},
            _fetch_result([_item(title=JUNK_TITLE, summary=JUNK_SUMMARY)]),
        )
        assert stats["new"] == 1  # legacy: everything persisted
        assert "rejected" in stats  # new counters still reported honestly

    def test_metrics_funnel(self, seeded_source):
        gw = NewsAdmissionGateway(seeded_source)
        ingestor = NewsIngestor(seeded_source, admission_gateway=gw)
        ingestor.ingest_source_items(
            {"source_id": "reuters_test", "name": "Reuters Test", "tier": "TIER_1"},
            _fetch_result(
                [
                    _item(title=GOLD_TITLE, summary=GOLD_SUMMARY),
                    _item(
                        title=JUNK_TITLE,
                        summary=JUNK_SUMMARY,
                        url="https://example.com/junk-distinct",
                    ),
                ]
            ),
        )
        snap = gw.metrics.snapshot()
        assert snap["articles_seen"] == 2
        assert snap["admitted"] == 1
        assert snap["rejected_after_scoring"] + snap["rejected_before_scoring"] == 1
        assert snap["db_writes_avoided"] >= 1
        assert snap["avg_latency_ms"] >= 0.0

    def test_ingest_stats_surface_in_cycle_shape(self, seeded_source):
        """ingest_source_items exposes the admission funnel for the engine/health."""
        gw = NewsAdmissionGateway(seeded_source)
        ingestor = NewsIngestor(seeded_source, admission_gateway=gw)
        stats = ingestor.ingest_source_items(
            {"source_id": "reuters_test", "name": "Reuters Test", "tier": "TIER_1"},
            _fetch_result([]),
        )
        assert set(stats) >= {
            "new",
            "duplicate",
            "merged_evidence",
            "admitted",
            "quarantined",
            "rejected",
        }


# ---------------------------------------------------------------------------
# Config discipline (§20: thresholds centralized)
# ---------------------------------------------------------------------------


class TestRepollUrlGuard:
    """Regression: a feed re-poll must never store the same (source, url) twice.

    The production news.db carries 21,771 of 24,588 rows under a repeated
    (source_id, canonical_url) because feeds with no parseable published time
    get a fabricated stamp per poll, re-minting the time-bucketed
    article_hash so the hash dedup misses it.
    """

    def test_repoll_with_reminted_hash_collapses_to_duplicate(self, seeded_source):
        ingestor = NewsIngestor(seeded_source)

        first = _item(
            title=GOLD_TITLE,
            summary=GOLD_SUMMARY,
            published=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        )
        stats1 = ingestor.ingest_source_items(
            {"source_id": "reuters_test", "name": "Reuters Test", "tier": "TIER_1"},
            _fetch_result([first]),
        )
        assert stats1["new"] == 1
        assert seeded_source.count_articles() == 1

        # Re-poll: same URL, same story, DIFFERENT fabricated published time.
        # canonicalize_item() buckets to 60s so the article_hash changes,
        # which is exactly what defeats the existing hash dedup in prod.
        repoll = _item(
            title=GOLD_TITLE,
            summary=GOLD_SUMMARY,
            url=first["url"],
            published=datetime(2026, 2, 15, 9, 30, tzinfo=UTC),  # different bucket
        )
        stats2 = ingestor.ingest_source_items(
            {"source_id": "reuters_test", "name": "Reuters Test", "tier": "TIER_1"},
            _fetch_result([repoll]),
        )
        assert stats2["new"] == 0
        assert stats2["duplicate"] == 1
        # one row only, with the original identity preserved
        rows = seeded_source.list_articles(include_duplicates=True)
        assert len(rows) == 1
        assert rows[0]["published_at"].startswith("2026-01-01")


class TestRepollEventClustering:
    def test_same_event_from_second_source_keeps_both_as_evidence(self, seeded_source):
        """Different source, same URL host+path family, same event: the second
        item still stores, because cross-source coverage is real evidence
        (never delete secondary sources, §8)."""
        ingestor = NewsIngestor(seeded_source)
        a = _item(title=GOLD_TITLE, summary=GOLD_SUMMARY, url="https://a.com/x")
        b = _item(
            title="Fed lowers interest rates",
            summary=GOLD_SUMMARY,
            url="https://b.com/x",
        )
        ingestor.ingest_source_items(
            {"source_id": "reuters_test", "name": "Reuters", "tier": "TIER_1"},
            _fetch_result([a]),
        )
        ingestor.ingest_source_items(
            {"source_id": "aggregator_test", "name": "Low", "tier": "TIER_4"},
            _fetch_result([b]),
        )
        rows = seeded_source.list_articles(include_duplicates=True)
        assert len(rows) == 2  # both kept; cross-source = evidence, not duplicate


class TestConfig:
    def test_defaults_align_with_auto_prune_thresholds(self):
        cfg = NewsAdmissionConfig()
        from nexus_scalp.news.ai_service import (
            NEWS_IRRELEVANCE_IMPORTANCE_THRESHOLD,
            NEWS_XAUUSD_RELEVANCE_THRESHOLD,
        )

        # the pre-DB gate must never be STRICTER than the post-hoc prune on
        # the floors (otherwise the two layers contradict each other)
        assert cfg.importance_floor <= NEWS_IRRELEVANCE_IMPORTANCE_THRESHOLD
        assert cfg.relevance_floor <= NEWS_XAUUSD_RELEVANCE_THRESHOLD

    def test_thresholds_configurable(self):
        cfg = NewsAdmissionConfig(admit_score=0.9, review_score=0.8, max_age_hours=1.0)
        gw = NewsAdmissionGateway(None, config=cfg)
        assert gw.config.admit_score == 0.9
        assert gw.config.max_age_hours == 1.0
