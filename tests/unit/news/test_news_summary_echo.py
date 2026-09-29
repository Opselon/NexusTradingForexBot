"""DEEP-OPT L2 — news AI summary echo value gate (Part 5 rules 9/14/85).

What was measured: on production ``news.db`` the ``news_ai_analysis.summary``
column holds 45.0 MB across 20,939 rows, and a sampled join against
``news_articles`` shows the value is byte-identical to the article's own
``summary`` — the model restates the source text and the row stores it a second
time. The deterministic ingest already persists that text; the second copy has
no downstream consumer (the UI renders the deterministic summary for the article
list and the AI columns for analysis detail).

The gate is applied to the model RESULT before persistence, so the *prompt* is
untouched (the model still receives the full article) and only the duplicate
storage is removed.
"""

from __future__ import annotations

from nexus_scalp.news.ai_service import _strip_summary_echo
from nexus_scalp.news.models import NewsArticle


def _article(summary: str = "Source summary text.", **kw: object) -> NewsArticle:
    base: dict[str, object] = {
        "article_id": "a1",
        "article_hash": "",
        "canonical_url": "",
        "title": "T",
        "summary": summary,
        "body": "",
        "source_id": "s",
        "source_name": "s",
    }
    base.update(kw)
    return NewsArticle(**base)  # type: ignore[arg-type]


# --- the echo is removed -----------------------------------------------------


def test_exact_echo_removed() -> None:
    """The model restating the source verbatim is not stored a second time."""
    assert _strip_summary_echo("Source summary text.", _article()) == ""


def test_prefix_echo_removed() -> None:
    """A restatement that appends nothing meaningful is still the echo."""
    assert _strip_summary_echo("Source summary text. ", _article()) == ""


def test_echo_with_whitespace_padding_removed() -> None:
    assert _strip_summary_echo("  Source summary text.  ", _article()) == ""


# --- authored analysis survives (LOSSLESS, rule 85) --------------------------


def test_authored_summary_kept() -> None:
    """A summary the model actually wrote differs from the source and stays."""
    art = _article(summary="Source summary text.")
    authored = "The PRA's proposal narrows Part VIII scope for ring-fenced banks."
    assert _strip_summary_echo(authored, art) == authored


def test_authored_summary_with_shared_prefix_kept() -> None:
    """Echo detection is exact/prefix on the SOURCE, not a fuzzy overlap.

    A real analysis may legitimately open with the article's first clause and
    then add substance. ``startswith`` on the source would strip that; the gate
    compares the other way (does the summary extend the source?), so an
    authored summary is never truncated.
    """
    art = _article(summary="Bank of England holds rates.")
    authored = "Bank of England holds rates, but the vote split widened."
    assert _strip_summary_echo(authored, art) == authored


def test_empty_summary_stays_empty() -> None:
    assert _strip_summary_echo("", _article()) == ""


def test_summary_with_no_source_summary_kept() -> None:
    """An article with no source summary cannot produce an echo."""
    art = _article(summary="")
    assert _strip_summary_echo("Some analysis.", art) == "Some analysis."


def test_summary_with_null_source_summary_kept() -> None:
    art = _article(summary="")
    assert _strip_summary_echo("Some analysis.", art) == "Some analysis."


# --- round-trip through the persistence path ---------------------------------


def test_persist_path_strips_echo(monkeypatch: object) -> None:
    """``analyze_article_with_ai`` gates the result before it is persisted."""
    import json
    from types import SimpleNamespace

    from nexus_scalp.news.ai_service import (
        NewsAIAnalysisResult,
        _persist_ai_analysis,
        analyze_article_with_ai,
    )

    article_row = {
        "article_id": "a1",
        "article_hash": "",
        "canonical_url": "",
        "title": "T",
        "summary": "Source summary text.",
        "body": "Source summary text.",
        "source_id": "s",
        "source_name": "s",
        "published_at": None,
    }

    class FakeDB:
        def __init__(self) -> None:
            self.inserted: dict[str, object] = {}

        def get_article(self, _aid: str) -> dict[str, object]:
            return article_row

        def get_ai_analysis(self, _aid: str) -> None:
            return None

        def insert_ai_analysis(self, row: dict[str, object], **kw: object) -> None:
            self.inserted = row

    db = FakeDB()

    class FakeProvider:
        provider_name = "fake"
        model = "fake-1"

        def complete_json(self, **kw: object) -> object:
            # The model restates the source summary verbatim.
            return {
                "summary": "Source summary text.",
                "market_relevance": "Direct rate-path relevance for XAUUSD.",
                "xauusd_relevance": "Rate decisions drive gold.",
                "sentiment": "NEUTRAL",
                "importance_assessment": "Minor",
                "potential_market_impact": "Limited.",
                "key_facts": ["rates held"],
                "uncertainties": [],
                "insufficient_evidence": False,
            }

    def fake_resolve(_engine: object, _settings: object) -> FakeProvider:
        return FakeProvider()

    monkeypatch.setattr("nexus_scalp.news.ai_service.resolve_factory_provider", fake_resolve)
    monkeypatch.setattr(
        "nexus_scalp.news.ai_service._persist_ai_analysis",
        lambda d, r: d.insert_ai_analysis(
            {
                "ai_analysis_id": r.ai_analysis_id,
                "article_id": r.article_id,
                "run_id": "run",
                "provider": r.provider,
                "model": r.model,
                "analysis_version": r.analysis_version,
                "prompt_version": r.prompt_version,
                "status": "COMPLETED",
                "summary": r.summary,
                "market_relevance": r.market_relevance,
                "xauusd_relevance": r.xauusd_relevance,
                "sentiment": r.sentiment,
                "importance_assessment": r.importance_assessment,
                "key_facts": json.dumps(r.key_facts),
                "potential_market_impact": r.potential_market_impact,
                "uncertainties": json.dumps(r.uncertainties),
                "analysis_status": r.analysis_status,
                "insufficient_evidence": int(r.insufficient_evidence),
                "error_detail": r.error_detail,
                "analyzed_at": "2026-09-29T00:00:00+00:00",
            }
        ),
    )

    result = analyze_article_with_ai(db, "a1", engine=SimpleNamespace())
    assert result.summary == "", "the echo must be stripped from the result"
    assert db.inserted["summary"] == "", "the echo must not reach the DB row"
    # The rest of the analysis is intact — the gate removed ONE duplicate field.
    assert db.inserted["market_relevance"] == "Direct rate-path relevance for XAUUSD."
    assert json.loads(str(db.inserted["key_facts"])) == ["rates held"]


def test_persist_path_keeps_authored_summary(monkeypatch: object) -> None:
    """The same path preserves a genuinely authored summary (lossless)."""
    from types import SimpleNamespace

    from nexus_scalp.news.ai_service import analyze_article_with_ai

    article_row = {
        "article_id": "a1",
        "article_hash": "",
        "canonical_url": "",
        "title": "T",
        "summary": "Source summary text.",
        "body": "Source summary text.",
        "source_id": "s",
        "source_name": "s",
        "published_at": None,
    }
    captured: dict[str, object] = {}

    class FakeDB:
        def get_article(self, _aid: str) -> dict[str, object]:
            return article_row

        def get_ai_analysis(self, _aid: str) -> None:
            return None

        def insert_ai_analysis(self, row: dict[str, object], **kw: object) -> None:
            captured.update(row)

    class FakeProvider:
        provider_name = "fake"
        model = "fake-1"

        def complete_json(self, **kw: object) -> object:
            return {
                "summary": "Narrower Part VIII scope is credit-positive for RFBs.",
                "market_relevance": "m",
                "xauusd_relevance": "g",
                "sentiment": "BULLISH",
                "importance_assessment": "Minor",
                "potential_market_impact": "Limited.",
                "key_facts": [],
                "uncertainties": [],
                "insufficient_evidence": False,
            }

    monkeypatch.setattr(
        "nexus_scalp.news.ai_service.resolve_factory_provider",
        lambda *_a: FakeProvider(),
    )
    monkeypatch.setattr(
        "nexus_scalp.news.ai_service._persist_ai_analysis",
        lambda d, r: d.insert_ai_analysis({"summary": r.summary}),
    )

    analyze_article_with_ai(FakeDB(), "a1", engine=SimpleNamespace())
    assert captured["summary"] == "Narrower Part VIII scope is credit-positive for RFBs."


# --- the CLI reclaim is bounded and idempotent -------------------------------


def test_cli_reclaim_dry_run_is_read_only(tmp_path: object) -> None:
    """The reclaim reports bytes without changing the database (rule 43)."""
    import json as json_mod
    import sqlite3

    from nexus_scalp.cli.db_commands import make_db_app

    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)  # type: ignore[operator]
    db = tmp_path / "artifacts" / "news.db"  # type: ignore[operator]
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE news_articles (article_id TEXT PRIMARY KEY, summary TEXT NOT NULL);
        CREATE TABLE news_ai_analysis (ai_analysis_id TEXT PRIMARY KEY, article_id TEXT,
                                       summary TEXT);
        """
    )
    con.executemany(
        "INSERT INTO news_articles (article_id, summary) VALUES (?, ?)",
        [("a1", "source"), ("a2", "other")],
    )
    con.executemany(
        "INSERT INTO news_ai_analysis (ai_analysis_id, article_id, summary) VALUES (?, ?, ?)",
        [("x1", "a1", "source"), ("x2", "a2", "authored analysis")],
    )
    con.commit()
    con.close()

    app = make_db_app(workspace=tmp_path)  # type: ignore[arg-type]
    from typer.testing import CliRunner

    res = CliRunner().invoke(app, ["compact-summary-echo", "--dry-run", "--json"])
    assert res.exit_code == 0, res.output
    out = json_mod.loads(res.output)
    assert out["echo_rows"] == 1
    assert out["echo_bytes"] == len(b"source")
    # Dry run: the row is still there.
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    assert (
        con.execute("SELECT summary FROM news_ai_analysis WHERE ai_analysis_id='x1'").fetchone()[0]
        == "source"
    )
    con.close()


def test_cli_reclaim_clears_only_echoes(tmp_path: object) -> None:
    """The clear touches echo rows only; authored summaries are untouched."""
    import json as json_mod
    import sqlite3

    from typer.testing import CliRunner

    from nexus_scalp.cli.db_commands import make_db_app

    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)  # type: ignore[operator]
    db = tmp_path / "artifacts" / "news.db"  # type: ignore[operator]
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE news_articles (article_id TEXT PRIMARY KEY, summary TEXT NOT NULL);
        CREATE TABLE news_ai_analysis (ai_analysis_id TEXT PRIMARY KEY, article_id TEXT,
                                       summary TEXT);
        """
    )
    con.executemany(
        "INSERT INTO news_articles (article_id, summary) VALUES (?, ?)",
        [("a1", "source"), ("a2", "other")],
    )
    con.executemany(
        "INSERT INTO news_ai_analysis (ai_analysis_id, article_id, summary) VALUES (?, ?, ?)",
        [("x1", "a1", "source"), ("x2", "a2", "authored analysis")],
    )
    con.commit()
    con.close()

    app = make_db_app(workspace=tmp_path)  # type: ignore[arg-type]
    res = CliRunner().invoke(app, ["compact-summary-echo", "--json"])
    assert res.exit_code == 0, res.output
    out = json_mod.loads(res.output)
    assert out["cleared_rows"] == 1

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = dict(con.execute("SELECT ai_analysis_id, summary FROM news_ai_analysis").fetchall())
    con.close()
    assert rows["x1"] == "", "echo cleared"
    assert rows["x2"] == "authored analysis", "authored summary preserved"
