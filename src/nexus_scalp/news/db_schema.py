"""News DB schema: DDL + idempotent schema initialization (PHASE 12).

Extracted VERBATIM from news/database.py (Agent-5 modularization,
CHG-0032-A1 program). Single source of the news schema DDL; the
NewsDatabase facade mixes this in. USED BY: news/database.py.
DO-NOT-PUT-HERE: row CRUD (db_articles/db_analysis/db_queries).
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.news._db_core_protocol import _NewsDbCoreProto
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.news.db_schema")


_SCHEMA_SQL: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS news_sources (
        source_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'RSS',
        tier TEXT NOT NULL DEFAULT 'TIER_3',
        url TEXT DEFAULT '',
        feed_url TEXT DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        poll_interval_sec INTEGER NOT NULL DEFAULT 300,
        language TEXT DEFAULT 'en',
        priority REAL NOT NULL DEFAULT 0.5,
        seed_version TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS news_articles (
        article_id TEXT PRIMARY KEY,
        article_hash TEXT UNIQUE NOT NULL,
        canonical_url TEXT DEFAULT '',
        title TEXT NOT NULL,
        summary TEXT DEFAULT '',
        body TEXT DEFAULT '',
        language TEXT DEFAULT 'en',
        source_id TEXT DEFAULT '',
        source_name TEXT DEFAULT '',
        published_at TEXT NOT NULL,
        published_at_source TEXT NOT NULL DEFAULT 'UNKNOWN',
        updated_at TEXT DEFAULT '',
        raw_categories TEXT DEFAULT '[]',
        entities TEXT DEFAULT '[]',
        topics TEXT DEFAULT '[]',
        importance TEXT DEFAULT 'MINOR',
        importance_score REAL NOT NULL DEFAULT 0.0,
        novelty TEXT DEFAULT 'NEW',
        is_duplicate INTEGER NOT NULL DEFAULT 0,
        duplicate_of TEXT DEFAULT '',
        evidence_sources TEXT DEFAULT '[]',
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS news_article_versions (
        version_id INTEGER PRIMARY KEY AUTOINCREMENT,
        article_id TEXT NOT NULL,
        article_hash TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1,
        title TEXT NOT NULL,
        summary TEXT DEFAULT '',
        body TEXT DEFAULT '',
        source_id TEXT DEFAULT '',
        updated_at TEXT NOT NULL,
        payload TEXT DEFAULT '{}'
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS news_entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        article_id TEXT NOT NULL,
        name TEXT NOT NULL,
        entity_type TEXT NOT NULL DEFAULT 'GENERIC',
        relevance REAL NOT NULL DEFAULT 0.0,
        mentions INTEGER NOT NULL DEFAULT 1,
        is_primary INTEGER NOT NULL DEFAULT 0
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS news_topics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        article_id TEXT NOT NULL,
        topic TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS news_analysis (
        analysis_id TEXT PRIMARY KEY,
        article_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'COMPLETE',
        local_only INTEGER NOT NULL DEFAULT 1,
        provider TEXT DEFAULT '',
        summary TEXT DEFAULT '',
        entities TEXT DEFAULT '[]',
        topics TEXT DEFAULT '[]',
        direction TEXT DEFAULT 'NEUTRAL',
        impact_strength REAL NOT NULL DEFAULT 0.0,
        confidence REAL NOT NULL DEFAULT 0.0,
        horizon TEXT DEFAULT 'MACRO',
        importance TEXT DEFAULT 'MINOR',
        importance_score REAL NOT NULL DEFAULT 0.0,
        relevance_to_xauusd REAL NOT NULL DEFAULT 0.0,
        relevance_to_usd REAL NOT NULL DEFAULT 0.0,
        impacts TEXT DEFAULT '[]',
        surprise_assessment TEXT DEFAULT '',
        market_mechanism TEXT DEFAULT '',
        contradictory_factors TEXT DEFAULT '[]',
        novelty TEXT DEFAULT 'NEW',
        risks TEXT DEFAULT '[]',
        reasoning_trace_id TEXT DEFAULT '',
        analyzed_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS news_impacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        article_id TEXT NOT NULL,
        asset TEXT NOT NULL DEFAULT 'XAUUSD',
        direction TEXT NOT NULL DEFAULT 'NEUTRAL',
        strength REAL NOT NULL DEFAULT 0.0,
        confidence REAL NOT NULL DEFAULT 0.0,
        horizon TEXT DEFAULT 'MACRO',
        relevance REAL NOT NULL DEFAULT 0.0,
        mechanism TEXT DEFAULT '',
        evidence TEXT DEFAULT '[]',
        evaluated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS news_consensus (
        article_id TEXT PRIMARY KEY,
        source_count INTEGER NOT NULL DEFAULT 0,
        independent_count INTEGER NOT NULL DEFAULT 0,
        agreement REAL NOT NULL DEFAULT 0.0,
        conflict REAL NOT NULL DEFAULT 0.0,
        directions TEXT DEFAULT '[]',
        weighted_direction TEXT DEFAULT 'NEUTRAL',
        confidence REAL NOT NULL DEFAULT 0.0,
        evaluated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS news_analysis_runs (
        run_id TEXT PRIMARY KEY,
        started_at TEXT NOT NULL,
        finished_at TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'QUEUED',
        article_ids TEXT DEFAULT '[]',
        provider TEXT DEFAULT '',
        error TEXT DEFAULT ''
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS news_worker_state (
        scope TEXT PRIMARY KEY,
        cycle_count INTEGER NOT NULL DEFAULT 0,
        last_cycle_at TEXT DEFAULT '',
        last_error TEXT DEFAULT '',
        last_checkpoint TEXT DEFAULT ''
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS news_event_links (
        link_id TEXT PRIMARY KEY,
        event_key TEXT NOT NULL,
        article_id TEXT NOT NULL,
        linked_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS news_trade_links (
        link_id TEXT PRIMARY KEY,
        trade_id TEXT NOT NULL,
        article_id TEXT NOT NULL,
        strategy_id TEXT DEFAULT '',
        model_version TEXT DEFAULT '',
        news_alignment REAL NOT NULL DEFAULT 0.0,
        linked_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS news_health (
        source_id TEXT PRIMARY KEY,
        last_success_at TEXT DEFAULT '',
        last_failure_at TEXT DEFAULT '',
        last_status INTEGER,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        rate_limited INTEGER NOT NULL DEFAULT 0,
        retry_after_sec REAL NOT NULL DEFAULT 0.0,
        backoff_until TEXT DEFAULT '',
        healthy INTEGER NOT NULL DEFAULT 1
    );
    """,
]

_SCHEMA_SQL.extend(
    [
        """
    CREATE TABLE IF NOT EXISTS news_ai_analysis (
        ai_analysis_id TEXT PRIMARY KEY,
        article_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        analysis_version TEXT NOT NULL,
        prompt_version TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'COMPLETED',
        summary TEXT DEFAULT '',
        market_relevance TEXT DEFAULT '',
        xauusd_relevance TEXT DEFAULT '',
        sentiment TEXT DEFAULT '',
        importance_assessment TEXT DEFAULT '',
        key_facts TEXT DEFAULT '[]',
        potential_market_impact TEXT DEFAULT '',
        uncertainties TEXT DEFAULT '[]',
        analysis_status TEXT NOT NULL DEFAULT 'completed',
        insufficient_evidence INTEGER NOT NULL DEFAULT 0,
        error_detail TEXT DEFAULT '',
        analyzed_at TEXT NOT NULL
    );
    """,
        """
    CREATE TABLE IF NOT EXISTS news_junk_hashes (
        article_hash TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL DEFAULT 'junk',
        pruned_at TEXT NOT NULL,
        analysis_id TEXT DEFAULT ''
    );
    """,
        """
    CREATE TABLE IF NOT EXISTS news_analyzed_hashes (
        article_hash TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        analysis_id TEXT NOT NULL DEFAULT '',
        analyzed_at TEXT NOT NULL
    );
    """,
        """
    CREATE TABLE IF NOT EXISTS news_prune_audit (
        audit_id TEXT PRIMARY KEY,
        article_id TEXT NOT NULL,
        operation TEXT NOT NULL,
        previous_state TEXT NOT NULL,
        new_state TEXT NOT NULL,
        rule_version TEXT DEFAULT '',
        actor TEXT DEFAULT 'system',
        reason TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );
    """,
    ]
)

_INDEX_SQL: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_news_articles_published ON news_articles(published_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_news_articles_source ON news_articles(source_id, published_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_news_articles_source_url ON news_articles(source_id, canonical_url);",
    "CREATE INDEX IF NOT EXISTS idx_news_articles_dup ON news_articles(duplicate_of, is_duplicate);",
    "CREATE INDEX IF NOT EXISTS idx_news_versions_article ON news_article_versions(article_id, revision);",
    "CREATE INDEX IF NOT EXISTS idx_news_analysis_article ON news_analysis(article_id, analyzed_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_news_impacts_asset ON news_impacts(asset, evaluated_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_news_trade_links_trade ON news_trade_links(trade_id);",
    "CREATE INDEX IF NOT EXISTS idx_news_trade_links_article ON news_trade_links(article_id);",
    "CREATE INDEX IF NOT EXISTS idx_news_articles_status ON news_articles(article_status);",
    "CREATE INDEX IF NOT EXISTS idx_news_ai_analysis_article ON news_ai_analysis(article_id, analyzed_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_news_junk_hashes_hash ON news_junk_hashes(article_hash);",
    "CREATE INDEX IF NOT EXISTS idx_news_analyzed_hashes_hash ON news_analyzed_hashes(article_hash);",
    "CREATE INDEX IF NOT EXISTS idx_news_prune_audit_article ON news_prune_audit(article_id, created_at DESC);",
]


def _ddl_for_provider(ddl: str, config: Any) -> str:
    """Return DDL in the dialect the active provider speaks.

    The news schema is authored once, in the SQLite dialect (the default
    provider), and translated to PostgreSQL through the repository's proven
    DDL translator (the same one ``provision_domain`` runs for the news
    domain — one translation path, no second spelling of the schema).
    """
    if getattr(config, "is_sqlite", True):
        return ddl
    from nexus_scalp.database.migration.pg_schema import translate_ddl

    return translate_ddl(ddl)


def _column_names(conn: Any, table: str) -> list[str]:
    """Column names of ``table`` on whichever provider ``conn`` speaks.

    SQLite: ``PRAGMA table_info``; PostgreSQL: ``information_schema.columns``
    (the portable ``current_schema()`` keeps it schema-agnostic).
    """
    if hasattr(conn, "execute") and not _is_pg_conn(conn):
        try:
            return [str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        except Exception:
            return []
    try:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s AND table_schema = current_schema() "
            "ORDER BY ordinal_position",
            (table,),
        ).fetchall()
        return [str(r[0]) for r in rows]
    except Exception:
        return []


def _is_pg_conn(conn: Any) -> bool:
    """True when ``conn`` is a psycopg connection (or the portable proxy)."""
    module = type(conn).__module__ or ""
    if module.startswith("psycopg"):
        return True
    # The news store's pooled connection (``_PooledNewsConnection``) wraps a
    # psycopg cursor: the statement builder has to see it as PostgreSQL, or
    # a provider-pinned statement would spell the SQLite dialect at a PG pool.
    if type(conn).__name__ == "_PooledNewsConnection":
        return True
    proxy = getattr(conn, "_pg", None)
    if proxy is not None:
        return True
    cursor = getattr(conn, "_cursor", None)
    if cursor is not None:
        inner = getattr(cursor, "_cursor", None)
        if inner is not None and (type(inner).__module__ or "").startswith("psycopg"):
            return True
        if cursor is not None and (type(cursor).__module__ or "").startswith("psycopg"):
            return True
    return False


def _add_column_sql(table: str, column: str, decl: str, *, sqlite: bool = True) -> str:
    """Provider-spelled idempotent ADD COLUMN.

    SQLite has no ``ADD COLUMN IF NOT EXISTS`` (the caller guards it with a
    PRAGMA pre-check in :meth:`SchemaMixin._ensure_article_status_column`);
    PostgreSQL spells the guard in the statement, which it needs — without it
    every re-initialization would fail on the columns it added last time.
    """
    if sqlite:
        return f"ALTER TABLE {table} ADD COLUMN {column} {decl}"
    return f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {decl}"


class SchemaMixin(_NewsDbCoreProto):
    """SchemaMixin — verbatim method cluster from NewsDatabase."""

    def initialize_schema(self) -> None:
        """Creates the news schema + indexes (idempotent, both providers)."""
        try:
            conn = self._connect()
            try:
                if self._config.is_sqlite:
                    conn.execute("PRAGMA journal_mode=WAL;")
                    conn.execute("PRAGMA synchronous=NORMAL;")
                for ddl in _SCHEMA_SQL:
                    conn.execute(_ddl_for_provider(ddl, self._config))
                # Migration-safe: add recoverable article_status column BEFORE
                # building indexes that reference it. Existing rows default to
                # ACTIVE (never auto-classified).
                self._ensure_article_status_column(conn)
                for idx in _INDEX_SQL:
                    conn.execute(_ddl_for_provider(idx, self._config))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error("[NEWS_DB] schema init failed", error=str(e))

    def _ensure_article_status_column(self, conn: Any) -> None:
        """Idempotently add the recoverable article_status column (ACTIVE/IRRELEVANT).

        Safe default 'ACTIVE' so existing records are never silently reclassified.
        """
        # ``self._config`` is absent when the mixin is replayed bare by the
        # schema snapshot extractor (it applies the column heal to a throwaway
        # SQLite connection); that path is always SQLite, so a missing config
        # means "spell it the SQLite way".
        config = getattr(self, "_config", None)
        is_sqlite = True if config is None else bool(config.is_sqlite)
        cols = _column_names(conn, "news_articles")
        if "article_status" not in cols:
            conn.execute(
                _add_column_sql(
                    "news_articles",
                    "article_status",
                    "TEXT NOT NULL DEFAULT 'ACTIVE'",
                    sqlite=is_sqlite,
                )
            )
        # BUG-282: publication-time provenance. Legacy rows were all written
        # by the ISO-only parser, so their published_at is overwhelmingly the
        # fabricated ingest stamp — but per-row certainty is impossible after
        # the fact, so existing rows are marked UNKNOWN (never retro-guessed).
        if "published_at_source" not in cols:
            conn.execute(
                _add_column_sql(
                    "news_articles",
                    "published_at_source",
                    "TEXT NOT NULL DEFAULT 'UNKNOWN'",
                    sqlite=is_sqlite,
                )
            )
