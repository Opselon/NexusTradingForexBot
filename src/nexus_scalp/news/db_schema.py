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


class SchemaMixin(_NewsDbCoreProto):
    """SchemaMixin — verbatim method cluster from NewsDatabase."""

    def initialize_schema(self) -> None:
        """Creates the news schema + indexes (idempotent)."""
        try:
            conn = self._connect()
            try:
                if self._config.is_sqlite:
                    conn.execute("PRAGMA journal_mode=WAL;")
                    conn.execute("PRAGMA synchronous=NORMAL;")
                for ddl in _SCHEMA_SQL:
                    conn.execute(ddl)
                # Migration-safe: add recoverable article_status column BEFORE
                # building indexes that reference it. Existing rows default to
                # ACTIVE (never auto-classified).
                self._ensure_article_status_column(conn)
                for idx in _INDEX_SQL:
                    conn.execute(idx)
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error("[NEWS_DB] schema init failed", error=str(e))

    def _ensure_article_status_column(self, conn: Any) -> None:
        """Idempotently add the recoverable article_status column (ACTIVE/IRRELEVANT).

        Safe default 'ACTIVE' so existing records are never silently reclassified.
        """
        cols = [r[1] for r in conn.execute("PRAGMA table_info(news_articles)").fetchall()]
        if "article_status" not in cols:
            conn.execute(
                "ALTER TABLE news_articles ADD COLUMN article_status TEXT NOT NULL DEFAULT 'ACTIVE'"
            )
