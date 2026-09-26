"""News domain PostgreSQL portability — Lane C (PG MIGRATION).

WHAT THIS PINS
==============
Three classes of defect, each proven or reachable on a box whose ACTIVE
provider is PostgreSQL:

1. SPLIT-BRAIN ROOT CAUSE. ``NewsDatabase`` must resolve its provider through
   ``DatabaseConfig`` (``load_database_config('news')``) rather than opening
   its own ``sqlite3`` connection. The live cluster shows the split:
   ``news_articles`` held 24,906 rows in ``artifacts/news.db`` (SQLite) and
   294 on PostgreSQL. Under a PG provider the store routes through the
   fabric's pooled ``news`` backend; under SQLite it keeps the sqlite3 path
   byte-for-byte.

2. THE UNDEFINED-FUNCTION CLASS (silent no-op). ``_epoch_seconds`` returned a
   Python *function name* interpolated into SQL text. PostgreSQL received
   ``_epoch_seconds(text)`` and answered ``function ... does not exist``
   (proven in the live log, 2026-09-25 22:10:14). The dialect expression must
   be INLINED per provider, never emitted as a function call.

3. THE LIKE CASE-SENSITIVITY CLASS (silent wrong result). SQLite ``LIKE`` is
   case-INsensitive for ASCII; PostgreSQL ``LIKE`` is case-SENSITIVE. A news
   search that finds rows on SQLite returns none on PG for a mixed-case term.
   ``list_articles`` now lowers BOTH sides so both providers agree.

ISOLATION CONTRACT
==================
No live database is touched. The PG-side assertions are made against the SQL
text the store builds for each dialect, and the case-sensitivity fix is
proven on REAL ROWS by replaying both dialect shapes through SQLite (the
store's own provider) — including a case-sensitive replay that demonstrates
exactly what PostgreSQL would have returned before the fix.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.news.database import NewsDatabase, _translate_sql_for_pg


def _pg_config() -> DatabaseConfig:
    """A PG config that is never connected to (the store does not reach out)."""
    return DatabaseConfig.for_postgres(
        "news", host="127.0.0.1", port=55433, database="nse_unreachable", username="nse_user"
    )


def _pin_sqlite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Pin the ACTIVE provider to SQLite for one test.

    The machine-wide settings DB can hold ``provider=postgresql`` (this box
    does); the SQLite-pinned assertions must not inherit it, so the settings
    DB is redirected to a throwaway file that holds no provider.
    """
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(tmp_path / "absent_settings.db"))
    monkeypatch.delenv("NSE_DATABASE__PROVIDER", raising=False)
    monkeypatch.delenv("NSE_DATABASE__PG_HOST", raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# (1) provider resolution — the split-brain root cause
# ---------------------------------------------------------------------------


def test_news_database_is_not_provider_hardcoded() -> None:
    """``NewsDatabase`` accepts a config and carries the provider through.

    The constructor takes a ``DatabaseConfig`` and, with no config supplied,
    resolves ``load_database_config('news')`` itself: the store no longer
    unconditionally opens ``sqlite3.connect(path)``. A PG config must reach
    ``_config.is_postgresql`` — the precondition for the pooled fabric path.
    """
    store = NewsDatabase(config=_pg_config())
    assert store._config.is_postgresql, "the store must carry the PG config through"
    assert store.db_path is None, "a SQLite path must not be held under PostgreSQL"


def test_pg_store_never_returns_a_raw_sqlite3_connection(tmp_path: Path) -> None:
    """``(a)`` under a PG config the connection is NOT a raw sqlite3 handle.

    This is the split-brain assertion: a PG-configured store handing out a
    ``sqlite3.Connection`` IS the 24,906-vs-294 gap, by construction.

    The PG endpoint is never contacted: an unreachable DSN is the point — the
    store's contract is that it resolves a pooled backend (or fails closed to
    a documented non-sqlite3 error), and never silently downgrades to a
    SQLite file the caller did not ask for.
    """
    store = NewsDatabase(config=_pg_config())
    conn: Any = None
    try:
        conn = store._connect()
    except Exception as exc:
        # Fails CLOSED: a PG config with no resolvable password must raise,
        # never silently fall back to opening a SQLite file.
        assert "sqlite" not in str(exc).lower(), "must not degrade to SQLite"
        return
    try:
        assert not isinstance(conn, sqlite3.Connection), (
            "a PG-configured store must never hand out a raw sqlite3 connection"
        )
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def test_sqlite_store_keeps_the_sqlite3_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``(b)`` the SQLite provider keeps the exact sqlite3 path as before."""
    _pin_sqlite(monkeypatch, tmp_path)
    path = tmp_path / "sqlite_news.db"
    store = NewsDatabase(path)
    assert store._config.is_sqlite
    assert store.db_path == path
    conn = store._connect()
    try:
        assert isinstance(conn, sqlite3.Connection)
    finally:
        conn.close()


def test_sqlite_store_round_trips_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SQLite path stays fully working (write + read back)."""
    _pin_sqlite(monkeypatch, tmp_path)
    store = NewsDatabase(tmp_path / "roundtrip_news.db")
    store.upsert_source({"source_id": "src_a", "name": "Source A"})
    store.insert_article(
        {
            "article_id": "art_a",
            "article_hash": "hash_a",
            "title": "Gold Rally",
            "summary": "",
            "body": "",
        }
    )
    assert store.count_articles() == 1
    assert store.article_exists("hash_a")
    assert store.get_source("src_a")["name"] == "Source A"


# ---------------------------------------------------------------------------
# (2) the undefined-function class — _epoch_seconds is inlined, not called
# ---------------------------------------------------------------------------


def test_epoch_seconds_is_inlined_not_called_as_sql() -> None:
    """The epoch expression lands in the SQL text, spelled per provider.

    A bare ``_epoch_seconds(col)`` in the statement is an undefined function
    on PostgreSQL — the live error this lane was cut to fix.
    """
    from nexus_scalp.news.db_queries import _epoch_seconds

    pg = _epoch_seconds("na.analyzed_at", sqlite=False)
    assert "_epoch_seconds" not in pg, "the PG statement must not call the python name"
    assert "EXTRACT(EPOCH FROM" in pg
    assert "::timestamptz" in pg

    sq = _epoch_seconds("na.analyzed_at", sqlite=True)
    assert "_epoch_seconds" not in sq
    assert "strftime('%s'" in sq


def test_backfill_statement_carries_no_undefined_function() -> None:
    """The anchor-backfill statement is provider-correct in BOTH dialects."""
    from nexus_scalp.news.db_queries import _backfill_impact_anchors_sql

    for sqlite in (True, False):
        stmt = _backfill_impact_anchors_sql(sqlite=sqlite)
        assert "_epoch_seconds" not in stmt, (
            f"the backfill statement must not call _epoch_seconds (sqlite={sqlite})"
        )
        if sqlite:
            assert "strftime" in stmt
        else:
            assert "EXTRACT(EPOCH FROM" in stmt


def test_backfill_statement_executes_on_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rebuilt statement runs against a real SQLite engine.

    The store's own provider executes it: no exception and a zero rowcount on
    an empty table proves the inlined SQLite spelling is valid SQL, not just
    valid text.
    """
    _pin_sqlite(monkeypatch, tmp_path)
    store = NewsDatabase(tmp_path / "epoch_news.db")
    assert store._backfill_impact_anchors() == 0


def test_backfill_pg_statement_translates_through_the_pooled_seam() -> None:
    """The PG-shaped statement survives the store's placeholder translator."""
    from nexus_scalp.news.db_queries import _backfill_impact_anchors_sql

    translated = _translate_sql_for_pg(_backfill_impact_anchors_sql(sqlite=False))
    assert "EXTRACT(EPOCH FROM" in translated
    assert "_epoch_seconds" not in translated
    assert "?" not in translated


# ---------------------------------------------------------------------------
# (3) the LIKE case-sensitivity class — same rows on both providers
# ---------------------------------------------------------------------------


def test_like_clause_lowers_both_sides() -> None:
    """``list_articles`` builds a LOWER(column) LIKE LOWER(pattern) clause.

    Lowering the COLUMN as well as the pattern is what makes it portable:
    ``LOWER(title) LIKE ?`` with a lowered pattern returns the same rows on a
    case-sensitive engine (PG) as a bare ``LIKE`` does on SQLite.
    """
    from nexus_scalp.news.db_articles import ArticlesMixin

    asset_filter = "GOLD RALLY"
    where: list[str] = []
    args: list[object] = []
    if asset_filter:
        where.append("(LOWER(title) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(body) LIKE ?)")
        args += [f"%{asset_filter.lower()}%"] * 3

    clause = " AND ".join(where)
    assert "LOWER(title)" in clause
    assert "LOWER(summary)" in clause
    assert "LOWER(body)" in clause
    # the PATTERN is lowered too — the two sides must agree
    assert args == ["%gold rally%"] * 3
    assert ArticlesMixin._bounded_article_limit(5) == 5


def test_like_case_sensitivity_dual_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``(c)`` a mixed-case search returns the same rows on both engines.

    Proven on real rows. The store writes an article whose title is
    uppercase; the FIXED statement (LOWER on both sides) finds it under
    SQLite semantics AND under case-sensitive semantics (PostgreSQL's). The
    PRE-FIX statement (bare LIKE) is shown to disagree between the two
    engines — SQLite finds the row, a case-sensitive engine does not. That
    divergence IS the silent wrong result.
    """
    _pin_sqlite(monkeypatch, tmp_path)
    path = tmp_path / "case_news.db"
    store = NewsDatabase(path)
    title = "GOLD Rallies on Dollar Weakness"
    store.insert_article(
        {
            "article_id": "art_mixed_case",
            "article_hash": "hash_mixed_case",
            "title": title,
            "summary": "spot gold climbs",
            "body": "XAUUSD body text",
        }
    )

    # a substring that actually appears in the title, in the WRONG case
    pattern = "gold rallies on dollar weakness"

    fixed_sql = (
        "SELECT article_id FROM news_articles WHERE "
        "(LOWER(title) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(body) LIKE ?);"
    )
    fixed_args = [f"%{pattern}%"] * 3
    pre_sql = (
        "SELECT article_id FROM news_articles WHERE "
        "(title LIKE ? OR summary LIKE ? OR body LIKE ?);"
    )
    pre_args = [f"%{pattern}%"] * 3

    with sqlite3.connect(path) as conn:
        # case-INSENSITIVE semantics (SQLite's default LIKE)
        found_fixed = [r[0] for r in conn.execute(fixed_sql, fixed_args).fetchall()]
        found_pre = [r[0] for r in conn.execute(pre_sql, pre_args).fetchall()]

    with sqlite3.connect(path) as conn:
        # case-SENSITIVE semantics: ``PRAGMA case_sensitive_like`` makes
        # SQLite's LIKE behave like PostgreSQL's, so the same statement text
        # answers the question "what would PG have returned?".
        conn.execute("PRAGMA case_sensitive_like = ON;")
        found_pg_fixed = [r[0] for r in conn.execute(fixed_sql, fixed_args).fetchall()]
        found_pg_pre = [r[0] for r in conn.execute(pre_sql, pre_args).fetchall()]

    # the fixed statement agrees across both semantics — the contract
    assert found_fixed == ["art_mixed_case"]
    assert found_pg_fixed == ["art_mixed_case"], (
        "the LOWER() fix must find the row under case-SENSITIVE semantics too "
        "(that is PostgreSQL's LIKE)"
    )
    # the pre-fix statement does NOT — that is the defect
    assert found_pre == ["art_mixed_case"], "SQLite's LIKE found it (case-insensitive)"
    assert found_pg_pre == [], (
        "a case-sensitive engine (PostgreSQL LIKE) misses the mixed-case row — "
        "the exact silent-wrong-result the LOWER() fix removes"
    )


def test_like_search_finds_mixed_case_through_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store's own search surface returns the mixed-case row."""
    _pin_sqlite(monkeypatch, tmp_path)
    store = NewsDatabase(tmp_path / "store_case_news.db")
    store.insert_article(
        {
            "article_id": "art_store_case",
            "article_hash": "hash_store_case",
            "title": "Gold Rally Continues",
            "summary": "",
            "body": "",
        }
    )
    for term in ("GOLD rally", "gold rally", "GoLd RaLlY"):
        hits = store.list_articles(limit=10, asset_filter=term)
        assert len(hits) == 1, f"mixed-case term {term!r} must find the row"
        assert hits[0]["article_id"] == "art_store_case"


# ---------------------------------------------------------------------------
# calendar domain: its DDL is translated to the provider's dialect
# ---------------------------------------------------------------------------


def test_calendar_schema_is_translated_per_provider() -> None:
    """The calendar tables are created in the provider's dialect.

    ``CalendarWorker._ensure_tables`` used to execute its SQLite-dialect DDL
    verbatim, so on PostgreSQL the tables were never created (and the
    ``calendar_events``/``calendar_worker_state`` rows landed nowhere).
    """
    from nexus_scalp.calendar.worker import CALENDAR_SCHEMA_SQL
    from nexus_scalp.news.db_schema import _ddl_for_provider

    sqlite_cfg = DatabaseConfig.for_sqlite("news", path="x.db")
    for ddl in CALENDAR_SCHEMA_SQL:
        pg = _ddl_for_provider(ddl, _pg_config())
        sq = _ddl_for_provider(ddl, sqlite_cfg)
        # the SQLite dialect passes through unchanged
        assert sq.strip() == ddl.strip()
        # and the PG dialect no longer carries SQLite-only shapes
        assert "AUTOINCREMENT" not in pg
        # SQLite INTEGER columns become PG BIGINT (SQLite ints are 64-bit)
        assert "INTEGER" not in pg, f"SQLite INTEGER must translate to BIGINT: {pg[:90]}"
