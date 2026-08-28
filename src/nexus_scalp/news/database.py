"""Dedicated News database (PHASE 12).

A SEPARATE SQLite database (``artifacts/news.db``) so news rows, article
bodies, analysis payloads and AI traces NEVER mix with the core trading
ledger tables. Trading/accounting data survives complete News subsystem
deletion.

Schema design principles:
    * normalised but practical (13 tables, no blind table creation),
    * deterministic article identity (article_hash UNIQUE) for dedup,
    * append-only versioning (news_article_versions),
    * analysis/impact/consensus history (news_analysis, news_impacts,
      news_consensus, news_analysis_runs),
    * trade linkage (news_trade_links),
    * worker state + health (news_worker_state, news_health),
    * rebuildability: derived state is recomputable from raw articles.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from nexus_scalp.database.config import DatabaseConfig, load_database_config
from nexus_scalp.database.drivers import get_driver
from nexus_scalp.database.drivers.proxy import PortableConnection
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.news.database")

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

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


class NewsDatabase:
    """Dedicated SQLite persistence for the News Intelligence subsystem.

    The trading ``AuditRepository`` is intentionally NOT used: news must be
    independently initialised, backed up, migrated, queried and deleted
    without touching trading history.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        config: DatabaseConfig | None = None,
    ) -> None:
        """Provider-aware news store (DATABASE PORTABILITY).

        `db_path` remains for backward compatibility (SQLite).  `config`
        selects the provider explicitly (PostgreSQL support).  The store talks
        to the database through the portable connection proxy so all existing
        SQL call sites run unchanged on both providers.
        """
        self.db_path = Path(db_path) if db_path else None
        if config is not None:
            self._config = config
        elif self.db_path is not None:
            self._config = DatabaseConfig.for_sqlite("news", path=str(self.db_path))
        else:
            self._config = load_database_config("news")
            self.db_path = (
                Path(self._config.sqlite_connect_path) if self._config.is_sqlite else None
            )
        self._driver = get_driver(self._config)
        if self._config.is_sqlite and self.db_path is not None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

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

    def is_junk_hash(self, article_hash: str) -> bool:
        """True if this article_hash was tombstoned as junk (never re-ingest)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM news_junk_hashes WHERE article_hash = ?;", (article_hash,)
            ).fetchone()
            return row is not None

    def remember_junk_hash(
        self, article_hash: str, title: str = "", reason: str = "junk", analysis_id: str = ""
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO news_junk_hashes (article_hash, title, reason, pruned_at, analysis_id) VALUES (?, ?, ?, ?, ?);",
                (article_hash, title, reason, self._now(), analysis_id),
            )

    def remember_junk_hashes(self, hashes: list[dict[str, str]]) -> int:
        if not hashes:
            return 0
        with self._connect() as conn:
            n = 0
            for h in hashes:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO news_junk_hashes (article_hash, title, reason, pruned_at, analysis_id) VALUES (?, ?, ?, ?, ?);",
                        (
                            h.get("article_hash", ""),
                            h.get("title", ""),
                            h.get("reason", "junk"),
                            self._now(),
                            h.get("analysis_id", ""),
                        ),
                    )
                    n += 1
                except Exception:
                    pass
            return n

    def count_junk_hashes(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM news_junk_hashes;").fetchone()
            return int(row["c"]) if row else 0

    def is_analyzed_hash(self, article_hash: str) -> bool:
        """True if this article_hash was already analyzed (idempotent guard)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM news_analyzed_hashes WHERE article_hash = ?;", (article_hash,)
            ).fetchone()
            return row is not None

    def remember_analyzed_hash(
        self, article_hash: str, title: str = "", analysis_id: str = ""
    ) -> None:
        """Remember that article_hash has been analyzed — suppresses re-ingest + re-analysis."""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO news_analyzed_hashes (article_hash, title, analysis_id, analyzed_at) VALUES (?, ?, ?, ?);",
                (article_hash, title, analysis_id, self._now()),
            )

    def count_analyzed_hashes(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM news_analyzed_hashes;").fetchone()
            return int(row["c"]) if row else 0

    def close(self) -> None:
        """No persistent connection to close (connections are per-call);
        provided for parity with AuditRepository lifecycle used by the
        release/repair tooling."""
        return

    def _connect(self, timeout: float = 5.0) -> Any:
        """Portable connection (SQLite native; PostgreSQL proxied)."""
        if self._config.is_sqlite:
            conn = self._driver.connect(timeout=timeout)
            conn.row_factory = sqlite3.Row
            return conn
        return PortableConnection(self._driver, timeout=timeout)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------

    def upsert_source(self, row: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO news_sources
                    (source_id, name, kind, tier, url, feed_url, enabled,
                     poll_interval_sec, language, priority, seed_version, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    name=excluded.name, kind=excluded.kind, tier=excluded.tier,
                    url=excluded.url, feed_url=excluded.feed_url,
                    enabled=excluded.enabled,
                    poll_interval_sec=excluded.poll_interval_sec,
                    language=excluded.language, priority=excluded.priority,
                    seed_version=excluded.seed_version
                """,
                (
                    row["source_id"],
                    row["name"],
                    row.get("kind", "RSS"),
                    row.get("tier", "TIER_3"),
                    row.get("url", ""),
                    row.get("feed_url", ""),
                    int(row.get("enabled", True)),
                    int(row.get("poll_interval_sec", 300)),
                    row.get("language", "en"),
                    float(row.get("priority", 0.5)),
                    row.get("seed_version", ""),
                    self._now(),
                ),
            )

    def list_sources(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM news_sources"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY priority DESC, source_id;"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql).fetchall()]

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM news_sources WHERE source_id = ?;", (source_id,)
            ).fetchone()
            return dict(row) if row else None

    def set_source_enabled(self, source_id: str, enabled: bool) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE news_sources SET enabled = ? WHERE source_id = ?;",
                (int(enabled), source_id),
            )
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Articles (canonical)
    # ------------------------------------------------------------------

    def article_exists(self, article_hash: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM news_articles WHERE article_hash = ?;", (article_hash,)
            ).fetchone()
            return row is not None

    def get_article_by_hash(self, article_hash: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM news_articles WHERE article_hash = ?;", (article_hash,)
            ).fetchone()
            return dict(row) if row else None

    def get_article(self, article_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM news_articles WHERE article_id = ?;", (article_id,)
            ).fetchone()
            return dict(row) if row else None

    def insert_article(self, row: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO news_articles
                    (article_id, article_hash, canonical_url, title, summary, body,
                     language, source_id, source_name, published_at, updated_at,
                     raw_categories, entities, topics, importance, importance_score,
                     novelty, is_duplicate, duplicate_of, evidence_sources, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["article_id"],
                    row["article_hash"],
                    row.get("canonical_url", ""),
                    row.get("title", ""),
                    row.get("summary", ""),
                    row.get("body", ""),
                    row.get("language", "en"),
                    row.get("source_id", ""),
                    row.get("source_name", ""),
                    row.get("published_at", self._now()),
                    row.get("updated_at", ""),
                    json.dumps(row.get("raw_categories", [])),
                    json.dumps(row.get("entities", []), default=str),
                    json.dumps(
                        [t.value if hasattr(t, "value") else t for t in row.get("topics", [])]
                    ),
                    row.get("importance", "MINOR"),
                    float(row.get("importance_score", 0.0)),
                    row.get("novelty", "NEW"),
                    int(row.get("is_duplicate", 0)),
                    row.get("duplicate_of", ""),
                    json.dumps(row.get("evidence_sources", [])),
                    row.get("created_at", self._now()),
                ),
            )

    def mark_duplicate(self, article_hash: str, duplicate_of: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE news_articles SET is_duplicate = 1, duplicate_of = ? WHERE article_hash = ?;",
                (duplicate_of, article_hash),
            )

    def add_evidence_source(self, article_id: str, source_id: str) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT evidence_sources FROM news_articles WHERE article_id = ?;",
                (article_id,),
            ).fetchone()
            if not row:
                return
            sources = json.loads(row["evidence_sources"] or "[]")
            if source_id not in sources:
                sources.append(source_id)
                conn.execute(
                    "UPDATE news_articles SET evidence_sources = ? WHERE article_id = ?;",
                    (json.dumps(sources), article_id),
                )

    def list_articles(
        self,
        limit: int = 50,
        include_duplicates: bool = False,
        asset_filter: str | None = None,
        status_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 500))
        sql = "SELECT * FROM news_articles"
        where: list[str] = []
        args: list[Any] = []
        if not include_duplicates:
            where.append("is_duplicate = 0")
        if status_filter:
            where.append("article_status = ?")
            args.append(status_filter)
        if asset_filter:
            where.append("(title LIKE ? OR summary LIKE ? OR body LIKE ?)")
            args += [f"%{asset_filter}%"] * 3
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY published_at DESC LIMIT ?;"
        args.append(bounded)
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def list_related(self, article_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Duplicate / related articles of one canonical article."""
        bounded = max(1, min(int(limit), 50))
        with self._connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM news_articles WHERE duplicate_of = ? "
                    "OR article_id = ? ORDER BY published_at DESC LIMIT ?;",
                    (article_id, article_id, bounded),
                ).fetchall()
            ]

    def count_articles(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM news_articles;").fetchone()
            return int(row["c"]) if row else 0

    def count_pending_analysis(self) -> int:
        """Unbounded pending count (articles with no deterministic analysis row)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM news_articles a "
                "WHERE a.is_duplicate = 0 AND NOT EXISTS "
                "(SELECT 1 FROM news_analysis n WHERE n.article_id = a.article_id);"
            ).fetchone()
            return int(row["c"]) if row else 0

    # ------------------------------------------------------------------
    # Article versions (append-only)
    # ------------------------------------------------------------------

    def insert_version(self, row: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO news_article_versions
                    (article_id, article_hash, revision, title, summary, body,
                     source_id, updated_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["article_id"],
                    row["article_hash"],
                    int(row.get("revision", 1)),
                    row.get("title", ""),
                    row.get("summary", ""),
                    row.get("body", ""),
                    row.get("source_id", ""),
                    row.get("updated_at", self._now()),
                    json.dumps(row.get("payload", {}), default=str),
                ),
            )

    def latest_version(self, article_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM news_article_versions WHERE article_id = ? "
                "ORDER BY revision DESC LIMIT 1;",
                (article_id,),
            ).fetchone()
            return dict(row) if row else None

    def count_versions(self, article_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM news_article_versions WHERE article_id = ?;",
                (article_id,),
            ).fetchone()
            return int(row["c"]) if row else 0

    # ------------------------------------------------------------------
    # Entities / topics
    # ------------------------------------------------------------------

    def replace_entities(self, article_id: str, entities: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM news_entities WHERE article_id = ?;", (article_id,))
            for e in entities:
                conn.execute(
                    """
                    INSERT INTO news_entities
                        (article_id, name, entity_type, relevance, mentions, is_primary)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        article_id,
                        e.get("name", ""),
                        e.get("entity_type", "GENERIC"),
                        float(e.get("relevance", 0.0)),
                        int(e.get("mentions", 1)),
                        int(e.get("is_primary", 0)),
                    ),
                )

    def replace_topics(self, article_id: str, topics: list[str]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM news_topics WHERE article_id = ?;", (article_id,))
            for t in topics:
                conn.execute(
                    "INSERT INTO news_topics (article_id, topic) VALUES (?, ?);",
                    (article_id, t),
                )

    def get_entities(self, article_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM news_entities WHERE article_id = ? ORDER BY relevance DESC;",
                    (article_id,),
                ).fetchall()
            ]

    def get_topics(self, article_id: str) -> list[str]:
        with self._connect() as conn:
            return [
                str(r["topic"])
                for r in conn.execute(
                    "SELECT topic FROM news_topics WHERE article_id = ?;", (article_id,)
                ).fetchall()
            ]

    # ------------------------------------------------------------------
    # Analysis / impacts / consensus / runs
    # ------------------------------------------------------------------

    def insert_analysis(self, row: dict[str, Any], *, allow_overwrite: bool = False) -> None:
        """Idempotent: by default refuses to re-analyze an already-analyzed article (confusion guard).
        Pass allow_overwrite=True only for explicit user re-analysis (API force flag)."""
        if not allow_overwrite:
            with self._connect() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM news_analysis WHERE article_id = ? LIMIT 1;",
                    (row.get("article_id", ""),),
                ).fetchone()
                if exists is not None:
                    return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO news_analysis
                    (analysis_id, article_id, run_id, status, local_only, provider,
                     summary, entities, topics, direction, impact_strength, confidence,
                     horizon, importance, importance_score, relevance_to_xauusd,
                     relevance_to_usd, impacts, surprise_assessment, market_mechanism,
                     contradictory_factors, novelty, risks, reasoning_trace_id, analyzed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["analysis_id"],
                    row["article_id"],
                    row.get("run_id", ""),
                    row.get("status", "COMPLETE"),
                    int(row.get("local_only", 1)),
                    row.get("provider", ""),
                    row.get("summary", ""),
                    json.dumps(row.get("entities", []), default=str),
                    json.dumps(
                        [t.value if hasattr(t, "value") else t for t in row.get("topics", [])]
                    ),
                    row.get("direction", "NEUTRAL"),
                    float(row.get("impact_strength", 0.0)),
                    float(row.get("confidence", 0.0)),
                    row.get("horizon", "MACRO"),
                    row.get("importance", "MINOR"),
                    float(row.get("importance_score", 0.0)),
                    float(row.get("relevance_to_xauusd", 0.0)),
                    float(row.get("relevance_to_usd", 0.0)),
                    json.dumps(row.get("impacts", []), default=str),
                    row.get("surprise_assessment", ""),
                    row.get("market_mechanism", ""),
                    json.dumps(row.get("contradictory_factors", [])),
                    row.get("novelty", "NEW"),
                    json.dumps(row.get("risks", [])),
                    row.get("reasoning_trace_id", ""),
                    row.get("analyzed_at", self._now()),
                ),
            )
        # Tombstone this hash so future re-ingest of same story (even if DB row deleted) stays suppressed
        try:
            ah = None
            with self._connect() as _c2:
                r = _c2.execute(
                    "SELECT article_hash, title FROM news_articles WHERE article_id = ?;",
                    (row.get("article_id", ""),),
                ).fetchone()
                if r:
                    ah = str(r["article_hash"] or "")
                    ttl = str(r["title"] or "")
                    if ah:
                        _c2.execute(
                            "INSERT OR IGNORE INTO news_analyzed_hashes (article_hash, title, analysis_id, analyzed_at) VALUES (?, ?, ?, ?);",
                            (ah, ttl, row.get("analysis_id", ""), self._now()),
                        )
        except Exception:
            pass

    def get_analysis(self, article_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM news_analysis WHERE article_id = ? "
                "ORDER BY analyzed_at DESC LIMIT 1;",
                (article_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_analysis(self, limit: int = 50) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 500))
        with self._connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM news_analysis ORDER BY analyzed_at DESC LIMIT ?;",
                    (bounded,),
                ).fetchall()
            ]

    def replace_impacts(self, article_id: str, impacts: list[dict[str, Any]]) -> None:
        # Anchor impacts to the article's real publication time (not analysis time)
        # so a 4h-old article analyzed now appears at 4h-ago on the timeline.
        published_at: str | None = None
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT published_at FROM news_articles WHERE article_id = ?;",
                    (article_id,),
                ).fetchone()
                if row and row["published_at"]:
                    published_at = str(row["published_at"])
        except Exception:
            published_at = None
        anchor = published_at or self._now()
        with self._connect() as conn:
            conn.execute("DELETE FROM news_impacts WHERE article_id = ?;", (article_id,))
            for imp in impacts:
                ts = imp.get("evaluated_at") or imp.get("published_at") or anchor
                conn.execute(
                    """
                    INSERT INTO news_impacts
                        (article_id, asset, direction, strength, confidence, horizon,
                         relevance, mechanism, evidence, evaluated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        article_id,
                        imp.get("asset", "XAUUSD"),
                        imp.get("direction", "NEUTRAL"),
                        float(imp.get("strength", 0.0)),
                        float(imp.get("confidence", 0.0)),
                        imp.get("horizon", "MACRO"),
                        float(imp.get("relevance", 0.0)),
                        imp.get("mechanism", ""),
                        json.dumps(imp.get("evidence", [])),
                        ts,
                    ),
                )

    def get_impacts(self, article_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM news_impacts WHERE article_id = ? ORDER BY relevance DESC;",
                    (article_id,),
                ).fetchall()
            ]

    def list_recent_impacts(self, asset: str = "XAUUSD", limit: int = 50) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 500))
        with self._connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM news_impacts WHERE asset = ? "
                    "ORDER BY evaluated_at DESC LIMIT ?;",
                    (asset, bounded),
                ).fetchall()
            ]

    def _backfill_impact_anchors(self) -> int:
        """One-shot backfill: set news_impacts.evaluated_at to the article's
        true published_at when the stored evaluated_at equals the analysis
        time (analysis-at-insert). Idempotent; safe to call on every
        impact_timeline invocation. Returns rows updated."""
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    UPDATE news_impacts
                    SET evaluated_at = (
                        SELECT a.published_at FROM news_articles a
                        WHERE a.article_id = news_impacts.article_id
                    )
                    WHERE EXISTS (
                        SELECT 1 FROM news_articles a2
                        WHERE a2.article_id = news_impacts.article_id
                          AND a2.published_at IS NOT NULL
                          AND a2.published_at != ''
                          AND news_impacts.evaluated_at != a2.published_at
                          AND (
                              -- evaluated_at is within a few seconds of analyzed_at,
                              -- which indicates the old analysis-time anchoring
                              EXISTS (
                                  SELECT 1 FROM news_analysis na
                                  WHERE na.article_id = news_impacts.article_id
                                    AND ABS(
                                        CAST(strftime('%s', na.analyzed_at) AS INTEGER)
                                      - CAST(strftime('%s', news_impacts.evaluated_at) AS INTEGER)
                                    ) <= 5
                              )
                          )
                    );
                    """
                )
                return int(cur.rowcount or 0)
        except Exception:
            return 0

    def impact_timeline(
        self,
        bucket_sec: int = 900,
        hours_back: int = 24,
        asset: str = "XAUUSD",
    ) -> list[dict[str, Any]]:
        """Aggregates impact strength into time buckets for charting.

        Buckets are anchored to the article's real publication time
        (news_articles.published_at), NOT the analysis/evaluated_at time,
        so a 4h-old article analyzed now lands at 4h-ago on the chart.
        Falls back to evaluated_at when published_at is missing.

        Each bucket: bucket_start (ISO), bucket_ts (epoch), bullish (signed
        sum of bullish strength*relevance), bearish (signed sum of bearish),
        neutral (unsigned sum), article_count, top_title (highest-relevance
        article title in the bucket).
        """
        # Keep historical rows anchored to true publication time (one-shot, idempotent)
        try:
            self._backfill_impact_anchors()
        except Exception:
            pass
        bucket_sec = max(60, min(int(bucket_sec), 86400))
        hours_back = max(1, min(int(hours_back), 24 * 7))
        cutoff = datetime.now(UTC) - timedelta(hours=hours_back)
        with self._connect() as conn:
            rows_all = conn.execute(
                """
                SELECT COALESCE(a.published_at, i.evaluated_at) AS anchor_ts,
                       i.direction, i.strength, i.relevance, a.title
                FROM news_impacts i
                LEFT JOIN news_articles a ON a.article_id = i.article_id
                WHERE i.asset = ?
                  AND COALESCE(a.published_at, i.evaluated_at) IS NOT NULL
                  AND COALESCE(a.published_at, i.evaluated_at) != ''
                ORDER BY anchor_ts ASC;
                """,
                (asset,),
            ).fetchall()
        # Exact time-window filtering in Python (SQLite datetime('now') string
        # format does not match ISO 'T' timestamps, so lexicographic compare
        # would misplace hourly buckets — e.g. 4h-old would appear as now)
        rows: list = []
        for r in rows_all:
            try:
                ts = datetime.fromisoformat(str(r[0]).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                else:
                    ts = ts.astimezone(UTC)
                if ts >= cutoff:
                    rows.append(r)
            except Exception:
                continue

        buckets: dict[int, dict[str, Any]] = {}
        for anchor_ts, direction, strength, relevance, title in rows:
            try:
                _ts_f = datetime.fromisoformat(str(anchor_ts).replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
            bucket = int(_ts_f // bucket_sec) * bucket_sec
            b = buckets.setdefault(
                bucket,
                {
                    "bucket_start": "",
                    "bucket_ts": bucket,
                    "bullish": 0.0,
                    "bearish": 0.0,
                    "neutral": 0.0,
                    "article_count": 0,
                    "top_title": "",
                    "top_relevance": 0.0,
                },
            )
            s = float(strength or 0.0) * float(relevance or 0.0)
            d = str(direction or "NEUTRAL").upper()
            if d == "BULLISH":
                b["bullish"] += s
            elif d == "BEARISH":
                b["bearish"] += s
            else:
                b["neutral"] += s
            b["article_count"] += 1
            rel = float(relevance or 0.0)
            if rel > b["top_relevance"]:
                b["top_relevance"] = rel
                b["top_title"] = str(title or "")

        out: list[dict[str, Any]] = []
        for bucket, b in sorted(buckets.items()):
            b["bucket_start"] = datetime.fromtimestamp(bucket, tz=UTC).isoformat()
            b["bullish"] = round(b["bullish"], 4)
            b["bearish"] = round(b["bearish"], 4)
            b["neutral"] = round(b["neutral"], 4)
            out.append(b)
        return out

    def upsert_consensus(self, row: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO news_consensus
                    (article_id, source_count, independent_count, agreement, conflict,
                     directions, weighted_direction, confidence, evaluated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["article_id"],
                    int(row.get("source_count", 0)),
                    int(row.get("independent_count", 0)),
                    float(row.get("agreement", 0.0)),
                    float(row.get("conflict", 0.0)),
                    json.dumps(
                        [d.value if hasattr(d, "value") else d for d in row.get("directions", [])]
                    ),
                    row.get("weighted_direction", "NEUTRAL"),
                    float(row.get("confidence", 0.0)),
                    row.get("evaluated_at", self._now()),
                ),
            )

    def get_consensus(self, article_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM news_consensus WHERE article_id = ?;", (article_id,)
            ).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Article status (ACTIVE / IRRELEVANT) + prune audit (recoverable)
    # ------------------------------------------------------------------

    def get_article_status(self, article_id: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT article_status FROM news_articles WHERE article_id = ?;", (article_id,)
            ).fetchone()
            return str(row["article_status"]) if row else "ACTIVE"

    def set_article_status(
        self,
        article_id: str,
        status: str,
        *,
        reason: str = "",
        actor: str = "system",
        rule_version: str = "",
        operation: str = "AUTO_PRUNE",
    ) -> bool:
        """Recoverably transition an article's status; record an audit row.

        Returns True when the status actually changed. Idempotent: no-op
        (False) when the article is already in the target state.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT article_status FROM news_articles WHERE article_id = ?;", (article_id,)
            ).fetchone()
            if row is None:
                return False
            previous = str(row["article_status"])
            if previous == status:
                return False
            conn.execute(
                "UPDATE news_articles SET article_status = ? WHERE article_id = ?;",
                (status, article_id),
            )
            conn.execute(
                """
                INSERT INTO news_prune_audit
                    (audit_id, article_id, operation, previous_state, new_state,
                     rule_version, actor, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"pau_{uuid.uuid4().hex[:12]}",
                    article_id,
                    operation,
                    previous,
                    status,
                    rule_version,
                    actor,
                    reason,
                    self._now(),
                ),
            )
            return True

    def count_articles_by_status(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT article_status, COUNT(*) AS c FROM news_articles "
                "WHERE is_duplicate = 0 GROUP BY article_status;"
            ).fetchall()
        return {str(r["article_status"]): int(r["c"]) for r in rows}

    # ------------------------------------------------------------------
    # AI analysis (separate from deterministic news_analysis — AI interpretation layer)
    # ------------------------------------------------------------------

    def insert_ai_analysis(self, row: dict[str, Any], *, allow_overwrite: bool = False) -> None:
        """Idempotent: skips if article already has a completed AI analysis (prevents duplicate AI noise)."""
        if not allow_overwrite:
            with self._connect() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM news_ai_analysis WHERE article_id = ? AND analysis_status IN ('completed','completed_insufficient') LIMIT 1;",
                    (row.get("article_id", ""),),
                ).fetchone()
                if exists is not None:
                    return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO news_ai_analysis
                    (ai_analysis_id, article_id, run_id, provider, model, analysis_version,
                     prompt_version, status, summary, market_relevance, xauusd_relevance,
                     sentiment, importance_assessment, key_facts, potential_market_impact,
                     uncertainties, analysis_status, insufficient_evidence, error_detail, analyzed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["ai_analysis_id"],
                    row["article_id"],
                    row.get("run_id", ""),
                    row.get("provider", ""),
                    row.get("model", ""),
                    row.get("analysis_version", ""),
                    row.get("prompt_version", ""),
                    row.get("status", "COMPLETED"),
                    row.get("summary", ""),
                    row.get("market_relevance", ""),
                    row.get("xauusd_relevance", ""),
                    row.get("sentiment", ""),
                    row.get("importance_assessment", ""),
                    json.dumps(row.get("key_facts", []), default=str),
                    row.get("potential_market_impact", ""),
                    json.dumps(row.get("uncertainties", []), default=str),
                    row.get("analysis_status", "completed"),
                    int(row.get("insufficient_evidence", 0)),
                    row.get("error_detail", ""),
                    row.get("analyzed_at", self._now()),
                ),
            )

    def get_ai_analysis(self, article_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM news_ai_analysis WHERE article_id = ? "
                "ORDER BY analyzed_at DESC LIMIT 1;",
                (article_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_ai_analysis_by_id(self, ai_analysis_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM news_ai_analysis WHERE ai_analysis_id = ?;", (ai_analysis_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_ai_analysis(self, limit: int = 50) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 500))
        with self._connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM news_ai_analysis ORDER BY analyzed_at DESC LIMIT ?;",
                    (bounded,),
                ).fetchall()
            ]

    def start_run(self, run_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO news_analysis_runs (run_id, started_at, status) VALUES (?, ?, 'QUEUED');",
                (run_id, self._now()),
            )

    def finish_run(self, run_id: str, status: str, article_ids: list[str], error: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE news_analysis_runs SET status = ?, finished_at = ?, article_ids = ?, error = ? "
                "WHERE run_id = ?;",
                (status, self._now(), json.dumps(article_ids), error, run_id),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM news_analysis_runs WHERE run_id = ?;", (run_id,)
            ).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Trade links / event links
    # ------------------------------------------------------------------

    def insert_trade_link(self, row: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO news_trade_links
                    (link_id, trade_id, article_id, strategy_id, model_version,
                     news_alignment, linked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["link_id"],
                    row["trade_id"],
                    row["article_id"],
                    row.get("strategy_id", ""),
                    row.get("model_version", ""),
                    float(row.get("news_alignment", 0.0)),
                    row.get("linked_at", self._now()),
                ),
            )

    def list_trade_links(self, trade_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM news_trade_links WHERE trade_id = ? ORDER BY linked_at;",
                    (trade_id,),
                ).fetchall()
            ]

    def list_article_trade_links(self, article_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM news_trade_links WHERE article_id = ? ORDER BY linked_at;",
                    (article_id,),
                ).fetchall()
            ]

    # ------------------------------------------------------------------
    # Source health
    # ------------------------------------------------------------------

    def update_health(self, source_id: str, health: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO news_health
                    (source_id, last_success_at, last_failure_at, last_status,
                     consecutive_failures, rate_limited, retry_after_sec,
                     backoff_until, healthy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    health.get("last_success_at", ""),
                    health.get("last_failure_at", ""),
                    health.get("last_status"),
                    int(health.get("consecutive_failures", 0)),
                    int(health.get("rate_limited", 0)),
                    float(health.get("retry_after_sec", 0.0)),
                    health.get("backoff_until", ""),
                    int(health.get("healthy", 1)),
                ),
            )

    def get_health(self, source_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM news_health WHERE source_id = ?;", (source_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_health(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM news_health;").fetchall()]

    # ------------------------------------------------------------------
    # Worker state
    # ------------------------------------------------------------------

    def save_worker_state(self, state: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO news_worker_state
                    (scope, cycle_count, last_cycle_at, last_error, last_checkpoint)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    state.get("scope", "news"),
                    int(state.get("cycle_count", 0)),
                    state.get("last_cycle_at", ""),
                    state.get("last_error", ""),
                    state.get("last_checkpoint", ""),
                ),
            )

    def load_worker_state(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM news_worker_state WHERE scope = 'news';").fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Self-heal
    # ------------------------------------------------------------------

    def rebuild_derived(self) -> dict[str, int]:
        """Rebuilds derived tables (impacts/consensus/entities/topics) from the
        analysis payloads. Never alters raw article history.

        Returns counts of rebuilt rows for observability.
        """
        rebuilt = {"analysis": 0, "impacts": 0, "consensus": 0, "entities": 0, "topics": 0}
        with self._connect() as conn:
            analyses = conn.execute("SELECT * FROM news_analysis;").fetchall()
            for a in analyses:
                article_id = a["article_id"]
                try:
                    impacts = json.loads(a["impacts"] or "[]")
                    conn.execute("DELETE FROM news_impacts WHERE article_id = ?;", (article_id,))
                    for imp in impacts:
                        conn.execute(
                            """
                            INSERT INTO news_impacts
                                (article_id, asset, direction, strength, confidence,
                                 horizon, relevance, mechanism, evidence, evaluated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                article_id,
                                imp.get("asset", "XAUUSD"),
                                imp.get("direction", "NEUTRAL"),
                                float(imp.get("strength", 0.0)),
                                float(imp.get("confidence", 0.0)),
                                imp.get("horizon", "MACRO"),
                                float(imp.get("relevance", 0.0)),
                                imp.get("mechanism", ""),
                                json.dumps(imp.get("evidence", [])),
                                a["analyzed_at"],
                            ),
                        )
                    rebuilt["impacts"] += len(impacts)

                    entities = json.loads(a["entities"] or "[]")
                    conn.execute("DELETE FROM news_entities WHERE article_id = ?;", (article_id,))
                    for e in entities:
                        conn.execute(
                            """
                            INSERT INTO news_entities
                                (article_id, name, entity_type, relevance, mentions, is_primary)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                article_id,
                                e.get("name", ""),
                                e.get("entity_type", "GENERIC"),
                                float(e.get("relevance", 0.0)),
                                int(e.get("mentions", 1)),
                                int(e.get("is_primary", 0)),
                            ),
                        )
                    rebuilt["entities"] += len(entities)

                    topics = json.loads(a["topics"] or "[]")
                    conn.execute("DELETE FROM news_topics WHERE article_id = ?;", (article_id,))
                    for t in topics:
                        conn.execute(
                            "INSERT INTO news_topics (article_id, topic) VALUES (?, ?);",
                            (article_id, str(t)),
                        )
                    rebuilt["topics"] += len(topics)
                    rebuilt["analysis"] += 1
                except Exception:
                    continue
            conn.commit()
        return rebuilt

    # ------------------------------------------------------------------
    # Health / summary
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            articles = conn.execute("SELECT COUNT(*) AS c FROM news_articles;").fetchone()["c"]
            sources = conn.execute("SELECT COUNT(*) AS c FROM news_sources;").fetchone()["c"]
            analyses = conn.execute("SELECT COUNT(*) AS c FROM news_analysis;").fetchone()["c"]
            links = conn.execute("SELECT COUNT(*) AS c FROM news_trade_links;").fetchone()["c"]
            return {
                "articles": int(articles),
                "sources": int(sources),
                "analyses": int(analyses),
                "trade_links": int(links),
                "db_path": str(self.db_path),
            }
