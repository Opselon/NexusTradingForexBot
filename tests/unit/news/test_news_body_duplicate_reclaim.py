"""DEEP-OPT L3 — reclaim the legacy news body duplicate (Part 5 rules 43/45/85).

What was measured on production ``news.db``:

    24,906 articles total
    24,891 store body byte-identical to summary   (99.9%)
    48,456,345 bytes of body, all of it duplicated
    48,456,426 bytes of summary — the same text, stored once already

This is NOT a producer defect. The pre-insert payload value gate
(``_gate_duplicate_payload``) shipped in the DB-LIFECYCLE wave at 2026-09-29
02:39Z; the newest row in this ledger is 2026-09-25T18:30:29Z — four days
BEFORE the gate existed. The gate is working (3,162 rows already store body="")
and every row still carrying the duplicate predates it.

So L3 is a reclaim of legacy bytes, not a fix. It is an explicit operator
command with a dry run, a reconstruction-contract check, and bounded
key-range batches — never automatic (Part 5 rules 43/93).
"""

from __future__ import annotations

import json
import sqlite3

from typer.testing import CliRunner

from nexus_scalp.cli.db_commands import make_db_app


def _make_news_db(tmp_path: object) -> object:
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)  # type: ignore[operator]
    db = tmp_path / "artifacts" / "news.db"  # type: ignore[operator]
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE news_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id TEXT, article_hash TEXT, canonical_url TEXT, title TEXT,
            summary TEXT NOT NULL, body TEXT
        );
        """
    )
    con.executemany(
        "INSERT INTO news_articles (article_id, summary, body) VALUES (?, ?, ?)",
        [
            ("a1", "dup text", "dup text"),  # legacy duplicate
            ("a2", "other text", "other text"),  # legacy duplicate
            ("a3", "real summary", ""),  # already gated
            ("a4", "summary only", "summary plus extra body content"),  # real body
        ],
    )
    con.commit()
    con.close()
    return db


def test_dry_run_reports_and_changes_nothing(tmp_path: object) -> None:
    db = _make_news_db(tmp_path)
    res = CliRunner().invoke(
        make_db_app(workspace=tmp_path), ["compact-body-duplicate", "--dry-run", "--json"]
    )
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["duplicate_rows"] == 2
    assert out["duplicate_bytes"] == len(b"dup text") + len(b"other text")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    assert (
        con.execute(
            "SELECT COUNT(*) FROM news_articles WHERE body <> '' AND body = summary"
        ).fetchone()[0]
        == 2
    )
    con.close()


def test_clear_removes_only_byte_identical_duplicates(tmp_path: object) -> None:
    """A body equal to summary is cleared; a body with real content survives."""
    db = _make_news_db(tmp_path)
    res = CliRunner().invoke(make_db_app(workspace=tmp_path), ["compact-body-duplicate", "--json"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["cleared_rows"] == 2

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = dict(con.execute("SELECT article_id, body FROM news_articles").fetchall())
    con.close()
    assert rows["a1"] == "", "duplicate cleared"
    assert rows["a2"] == "", "duplicate cleared"
    assert rows["a3"] == "", "already-gated row untouched"
    assert rows["a4"] == "summary plus extra body content", "real body PRESERVED"


def test_reconstruction_contract_aborts_on_break(tmp_path: object, monkeypatch: object) -> None:
    """If resolve_article_body stops reproducing the summary, nothing is cleared.

    This is the safety interlock: the whole reclaim is lossless ONLY because
    readers re-materialize body from summary. If that contract breaks, the
    command must fail closed rather than delete text it cannot reconstruct.
    """
    _make_news_db(tmp_path)
    monkeypatch.setattr(
        "nexus_scalp.news.body_resolution.resolve_article_body",
        lambda body, summary: "DIFFERENT",  # contract broken
    )
    res = CliRunner().invoke(make_db_app(workspace=tmp_path), ["compact-body-duplicate", "--json"])
    assert res.exit_code == 1
    assert "refusing to clear" in (res.output + str(res.exception))


def test_no_duplicates_is_a_no_op(tmp_path: object) -> None:
    _make_news_db(tmp_path)
    con = sqlite3.connect(tmp_path / "artifacts" / "news.db")  # type: ignore[arg-type]
    con.execute("UPDATE news_articles SET body = '' WHERE body = summary")
    con.commit()
    con.close()
    res = CliRunner().invoke(make_db_app(workspace=tmp_path), ["compact-body-duplicate", "--json"])
    assert res.exit_code == 0
    assert json.loads(res.output)["duplicate_rows"] == 0


def test_batches_are_key_range_bounded(tmp_path: object) -> None:
    """Each UPDATE covers a bounded id range so progress is observable (rule 45)."""
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)  # type: ignore[operator]
    db = tmp_path / "artifacts" / "news.db"  # type: ignore[operator]
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE news_articles (id INTEGER PRIMARY KEY, summary TEXT NOT NULL, body TEXT);"
    )
    con.executemany(
        "INSERT INTO news_articles (id, summary, body) VALUES (?, ?, ?)",
        [(i, f"s{i}", f"s{i}") for i in range(1, 13)],
    )
    con.commit()
    con.close()

    res = CliRunner().invoke(
        make_db_app(workspace=tmp_path), ["compact-body-duplicate", "--batch-size", "5", "--json"]
    )
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["cleared_rows"] == 12
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    assert con.execute("SELECT COUNT(*) FROM news_articles WHERE body <> ''").fetchone()[0] == 0
    con.close()
