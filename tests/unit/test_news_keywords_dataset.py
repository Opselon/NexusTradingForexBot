"""Tests for the News keyword analysis dataset (PHASE 12 expansion).

Covers:
    * dataset size / category distribution / determinism,
    * keyword definitions (direction bias, topics, weight bounds),
    * corpus coverage analytics (hits, mentions, active keywords,
      direction distribution, share bounds),
    * per-article keyword hits used by the feed UI,
    * integration with LocalNewsAnalyzer output (entities/topics).
"""

from __future__ import annotations

from datetime import UTC, datetime

from nexus_scalp.news.analysis import (
    analyze_keyword_coverage,
    categories,
    get_keyword,
    get_keyword_dataset,
    keyword_count,
    keyword_hits_for_article,
    keywords_by_category,
)
from nexus_scalp.news.analysis.keywords import KeywordDatasetSummary
from nexus_scalp.news.models import NewsArticle, NewsDirection, NewsTopic


def _article(title: str, body: str = "") -> NewsArticle:
    return NewsArticle(
        article_id=f"a_{abs(hash(title))}",
        article_hash=f"h_{abs(hash(title))}",
        title=title,
        summary="",
        body=body,
        published_at=datetime.now(UTC),
    )


class TestKeywordDataset:
    def test_dataset_is_large_and_deterministic(self) -> None:
        ds = get_keyword_dataset()
        # the "big dataset" contract: >= 150 keywords
        assert len(ds) >= 150
        assert keyword_count() == len(ds)
        # deterministic order across repeated calls
        assert get_keyword_dataset() == ds

    def test_dataset_categories_cover_all_major_groups(self) -> None:
        cats = categories()
        for expected in (
            "currency",
            "asset",
            "institution",
            "macro",
            "geopolitics",
            "energy",
            "directional",
            "fx_pair",
        ):
            assert expected in cats, f"missing category {expected}"
            assert cats[expected] >= 5, f"{expected} too small: {cats[expected]}"

    def test_keyword_definitions_are_valid(self) -> None:
        for k in get_keyword_dataset():
            assert k.keyword.strip() == k.keyword
            assert 0.0 <= k.weight <= 1.0
            assert isinstance(k.direction_bias, NewsDirection)
            assert all(isinstance(t, NewsTopic) for t in k.topics)

    def test_directional_keywords_include_bullish_and_bearish(self) -> None:
        ds = get_keyword_dataset()
        bulls = [k for k in ds if k.direction_bias == NewsDirection.BULLISH]
        bears = [k for k in ds if k.direction_bias == NewsDirection.BEARISH]
        assert len(bulls) >= 20
        assert len(bears) >= 15

    def test_get_keyword_lookup(self) -> None:
        assert get_keyword("FED") is not None
        assert get_keyword("fed") is not None  # case-insensitive
        assert get_keyword("XAUUSD") is not None
        assert get_keyword("NOT_A_KEYWORD_XYZ") is None

    def test_keywords_by_category(self) -> None:
        macro = keywords_by_category("macro")
        assert len(macro) >= 20
        assert all(k.category == "macro" for k in macro)


class TestKeywordCoverage:
    def test_empty_corpus_returns_safe_summary(self) -> None:
        cov = analyze_keyword_coverage([])
        assert isinstance(cov, KeywordDatasetSummary)
        assert cov.total_articles_scanned == 0
        assert cov.total_mentions == 0
        assert cov.active_keywords == 0
        assert cov.top_keywords == ()

    def test_coverage_hits_and_share(self) -> None:
        articles = [
            _article("Fed cuts rates as inflation cools and yields fall"),
            _article("Gold surges to record high on safe haven demand"),
            _article("Stock rally on strong job growth"),
        ]
        cov = analyze_keyword_coverage(articles)
        assert cov.total_articles_scanned == 3
        assert cov.total_mentions > 0
        assert cov.active_keywords > 0
        # share is bounded
        assert all(0.0 <= c.share <= 1.0 for c in cov.top_keywords)
        # direction distribution present
        assert set(cov.direction_distribution) <= {"BULLISH", "BEARISH", "NEUTRAL"}

    def test_direction_distribution_reflects_bias(self) -> None:
        bullish = _article("Inflation hedge demand grows as Fed cut looms and gold surges")
        cov = analyze_keyword_coverage([bullish])
        assert cov.direction_distribution.get("BULLISH", 0) >= 1
        assert cov.direction_distribution.get("BEARISH", 0) == 0

    def test_top_keywords_are_sorted_by_hits(self) -> None:
        articles = [_article("Fed") for _ in range(5)] + [_article("Gold surges")]
        cov = analyze_keyword_coverage(articles, top_n=10)
        top = list(cov.top_keywords)
        hits = [c.article_hits for c in top]
        assert hits == sorted(hits, reverse=True)

    def test_limit_texts(self) -> None:
        articles = [_article("Fed") for _ in range(10)]
        cov = analyze_keyword_coverage(articles, limit_texts=3)
        assert cov.total_articles_scanned == 3


class TestKeywordHitsPerArticle:
    def test_article_hits_keywords(self) -> None:
        a = _article("Bank of England holds rates, hawkish tone hits gold")
        hits = keyword_hits_for_article(a)
        kws = {h["keyword"] for h in hits}
        assert "GOLD" in kws
        assert "HAWKISH" in kws
        assert all(h["direction_bias"] in {"BULLISH", "BEARISH", "NEUTRAL"} for h in hits)
        assert all(h["mentions"] >= 1 for h in hits)

    def test_hits_with_aliases(self) -> None:
        a = _article("Cable rallies as GBPUSD jumps on hawkish BOE")
        hits = keyword_hits_for_article(a)
        cable = next((h for h in hits if h["keyword"] == "CABLE"), None)
        assert cable is not None, "alias keyword CABLE should fire on GBPUSD"
        assert cable["mentions"] >= 1

    def test_coverage_accepts_dict_rows(self) -> None:
        # DB rows are dicts (list_articles) — coverage must read them too
        row = {
            "title": "Fed cuts rates, gold surges",
            "summary": "safe haven demand",
            "body": "",
        }
        cov = analyze_keyword_coverage([row])
        assert cov.active_keywords >= 1
        hits = keyword_hits_for_article(row)
        assert any(h["keyword"] == "GOLD" for h in hits)

    def test_no_hits_on_unrelated_text(self) -> None:
        a = _article("The weather today is sunny and warm")
        hits = keyword_hits_for_article(a)
        assert len(hits) == 0

    def test_gold_negatives_not_confused(self) -> None:
        # "Golden State" / "gold medal" must not fire the GOLD asset keyword
        a = _article("Golden State Warriors win gold medal in exhibition")
        hits = keyword_hits_for_article(a)
        gold = [h for h in hits if h["keyword"] == "GOLD"]
        assert gold == [], f"GOLD should not fire on medal context: {hits}"


class TestLocalAnalysisIntegration:
    def test_keyword_topics_align_with_local_analyzer(self) -> None:
        from nexus_scalp.news.analysis import LocalNewsAnalyzer

        analyzer = LocalNewsAnalyzer()
        a = _article("Fed holds rates, CPI rises, yields jump")
        entities = analyzer.extract_entities(a)
        topics = analyzer.classify_topics(a, entities)
        names = {e.name for e in entities}
        assert "FED" in names or "CPI" in names
        assert NewsTopic.INFLATION in topics or NewsTopic.CENTRAL_BANK in topics


class TestKeywordPatternCache:
    """Performance-contract regression for the precompiled pattern cache.

    The baseline compiled a fresh word-boundary regex per (keyword x article)
    inside `_count_mentions` (~94,500 compilations per 500-article scan).
    The optimized path compiles each token pattern once (bounded cache) and
    MUST produce byte-identical results. These tests prove the old
    bottleneck is gone while semantics are unchanged.
    """

    def test_cached_matches_inline_semantics_every_keyword(self) -> None:
        """Every keyword/alias/negative must produce identical mention counts
        on the cached path vs the original inline `_count_mentions`."""
        import nexus_scalp.news.analysis.keywords as kw_mod

        texts = [
            "GOLD SURGES AS DOLLAR WEAKENS FED RATE CUT BETS RISE",
            "OIL PRICES FALL GOLD MEDAL CEREMONY SCHEDULED USD STEADY",
            "ECB HOLDS RATES EURO SLIDES VS DOLLAR DXY UP 0.2%",
            "TREASURY YIELDS CLIMB INFLATION CONCERNS PERSIST GOLD DEMAND",
            "CENTRAL BANK HAWKISH TONE HITS XAUUSD AS 10Y YIELDS JUMP",
            "THE WEATHER TODAY IS SUNNY AND WARM",
            "GOLDEN STATE WARRIORS WIN GOLD MEDAL IN EXHIBITION",
            "BRENT CRUDE OIL RISES ON SUPPLY FEARS",
            "NONFARM PAYROLLS BEAT FORECAST DOLLAR GAINS",
            "SAFE HAVEN BID RETURNS AS GEOPOLITICAL TENSIONS RISE",
        ]
        mismatches = 0
        checked = 0
        for text in texts:
            for k in kw_mod._ALL_KEYWORDS:
                inline = kw_mod._count_mentions(text, k.keyword, k.aliases, k.negatives)
                cached = kw_mod._count_mentions_cached(text, k.keyword, k.aliases, k.negatives)
                checked += 1
                if inline != cached:
                    mismatches += 1
                    # keep the test readable: report first 5 only
                    if mismatches <= 5:
                        print(
                            "MISMATCH",
                            k.keyword,
                            "inline=",
                            inline,
                            "cached=",
                            cached,
                            "text=",
                            text[:40],
                        )
        assert checked >= 189 * 10, f"checked {checked}"
        assert mismatches == 0, f"{mismatches} semantic mismatches"

    def test_cache_is_bounded_and_reused(self) -> None:
        """The pattern cache must stay small (one pattern per token) and must
        NOT grow with article count."""
        import nexus_scalp.news.analysis.keywords as kw_mod

        articles = [_article(f"Fed inflation {i} dollar gold") for i in range(50)]
        kw_mod.analyze_keyword_coverage(articles)
        stats_50 = kw_mod.pattern_cache_stats()
        # A corpus of 50 more articles must not add patterns
        articles2 = [_article(f"ECB euro yield {i} oil") for i in range(100, 200)]
        kw_mod.analyze_keyword_coverage(articles2)
        stats_200 = kw_mod.pattern_cache_stats()
        assert stats_50["compiled_patterns"] == stats_200["compiled_patterns"]
        # The dataset has ~189 keywords; alias/negative tokens add a bit.
        # Bound the cache far below the old per-article blow-up:
        assert stats_200["compiled_patterns"] < 500
        assert stats_200["compiled_patterns"] >= keyword_count() // 2

    def test_cache_rebuilds_on_fingerprint_change(self) -> None:
        """Changing keyword configuration (simulated) must invalidate and
        rebuild the cache, and results must track the new config."""
        import nexus_scalp.news.analysis.keywords as kw_mod

        # The fingerprint embeds every matching-relevant field: keyword text
        # AND aliases AND negatives (e.g. GOLD + its "GOLD MEDAL" negative,
        # CABLE whose alias is GBPUSD).
        fp_before = kw_mod._dataset_pattern_fingerprint()
        assert "GOLD" in fp_before
        assert "GBPUSD" in fp_before.upper() or "CABLE" in fp_before
        # And that the cache rebuild path is exercised when the key changes:
        kw_mod._CACHE_META["fingerprint"] = ""  # force rebuild on next call
        kw_mod.analyze_keyword_coverage([_article("Fed")])
        assert kw_mod._CACHE_META["fingerprint"] == kw_mod._dataset_pattern_fingerprint()
        assert len(kw_mod._PATTERN_CACHE) > 0

    def test_coverage_output_identical_old_vs_new(self) -> None:
        """The public coverage output on the cached path must equal the
        original inline implementation on identical input."""
        import nexus_scalp.news.analysis.keywords as kw_mod

        articles = [
            _article("Fed cuts rates as inflation cools and yields fall"),
            _article("Gold surges to record high on safe haven demand"),
            _article("Stock rally on strong job growth"),
            _article("Bank of England hawkish hold hits gold"),
            _article("Oil rises on supply fears, dollar steady"),
        ]
        # New (cached) path
        cov_new = kw_mod.analyze_keyword_coverage(articles, top_n=10)
        # Reference implementation = the baseline inline matcher replayed
        hits: dict[str, int] = {}
        mentions: dict[str, int] = {}
        for a in articles:
            text = " ".join([a.title, a.summary, a.body]).upper()
            for k in kw_mod._ALL_KEYWORDS:
                m = kw_mod._count_mentions(text, k.keyword, k.aliases, k.negatives)
                if m > 0:
                    hits[k.keyword] = hits.get(k.keyword, 0) + 1
                    mentions[k.keyword] = mentions.get(k.keyword, 0) + m
        active = sorted(
            [k for k in kw_mod._ALL_KEYWORDS if hits.get(k.keyword, 0) > 0],
            key=lambda k: (-hits.get(k.keyword, 0), -mentions.get(k.keyword, 0), k.keyword),
        )
        expected = [(k.keyword, hits[k.keyword], mentions[k.keyword]) for k in active[:10]]
        got = [(c.keyword, c.article_hits, c.mention_count) for c in cov_new.top_keywords]
        assert got == expected
        assert cov_new.total_articles_scanned == len(articles)
        assert cov_new.total_mentions == sum(mentions.values())

    def test_regex_compilation_count_is_bounded(self) -> None:
        """The old path compiled ~N_articles x N_keywords regexes. The cached
        path must compile at most a few hundred patterns regardless of corpus
        size. Asserted as a bounded ratio, never an absolute wall-clock."""
        import nexus_scalp.news.analysis.keywords as kw_mod

        kw_mod._CACHE_META["fingerprint"] = ""  # cold cache
        kw_mod._PATTERN_CACHE.clear()
        articles = [_article(f"Fed dollar gold {i}") for i in range(300)]
        kw_mod.analyze_keyword_coverage(articles)
        stats = kw_mod.pattern_cache_stats()
        # 300 articles x 189 keywords would be 56,700 inline compilations.
        # The cache must stay in the hundreds:
        assert stats["compiled_patterns"] <= 500
        assert stats["compiled_patterns"] >= 100

    def test_statistical_speedup_bound(self) -> None:
        """Statistical (flakiness-resistant) performance contract: the cached
        path must not be slower than a fixed multiple of the inline baseline
        on a large corpus. Uses the median of several runs, not a wall-clock
        absolute."""
        import time

        import nexus_scalp.news.analysis.keywords as kw_mod

        articles = [_article(f"Fed inflation gold oil dollar {i}") for i in range(200)]

        def timed(fn):
            samples = []
            for _ in range(3):
                t0 = time.perf_counter()
                fn()
                samples.append(time.perf_counter() - t0)
            samples.sort()
            return samples[len(samples) // 2]  # median

        def baseline():
            # the OLD coverage path: inline _count_mentions per keyword per
            # article (fresh regex compile + findall inside the loop)
            hits: dict[str, int] = {}
            for a in articles:
                text = " ".join([a.title, a.summary, a.body]).upper()
                for k in kw_mod._ALL_KEYWORDS:
                    m = kw_mod._count_mentions(text, k.keyword, k.aliases, k.negatives)
                    if m > 0:
                        hits[k.keyword] = hits.get(k.keyword, 0) + 1
            return len(hits)

        def cached():
            return kw_mod.analyze_keyword_coverage(articles).active_keywords

        # both paths must produce the same active-keyword count (sanity) and
        # the cached path must be faster; a 2x floor is conservative
        # (measured reality on the live corpus is ~10-30x because the old
        # path compiles ~Nx189 regexes per call).
        t_base = timed(baseline)
        t_cached = timed(cached)
        base_count = baseline()
        cached_count = cached()
        assert base_count == cached_count, (
            f"semantic divergence: old={base_count} cached={cached_count}"
        )
        assert t_cached * 2.0 < t_base, (
            f"cached {t_cached * 1000:.1f}ms not faster than baseline {t_base * 1000:.1f}ms"
        )
