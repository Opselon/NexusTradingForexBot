"""News DB ingest-side write path: sources, articles, versions, dedup hashes.

Extracted VERBATIM from news/database.py (Agent-5 modularization,
CHG-0032-A1 program). Mixin over the shared connection base; every
method keeps its original SQL/semantics. USED BY: news/database.py.
DO-NOT-PUT-HERE: analysis payloads (db_analysis), read/ops (db_queries).
"""

from __future__ import annotations

import json
from typing import Any

from nexus_scalp.news._db_core_protocol import _NewsDbCoreProto


class ArticlesMixin(_NewsDbCoreProto):
    """ArticlesMixin — verbatim method cluster from NewsDatabase."""

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
