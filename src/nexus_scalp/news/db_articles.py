"""News DB ingest-side write path: sources, articles, versions, dedup hashes.

Extracted VERBATIM from news/database.py (Agent-5 modularization,
CHG-0032-A1 program). Mixin over the shared connection base; every
method keeps its original SQL/semantics. USED BY: news/database.py.
DO-NOT-PUT-HERE: analysis payloads (db_analysis), read/ops (db_queries).
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from nexus_scalp.news._db_core_protocol import _NewsDbCoreProto
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.news.database")

#: BUG-258 hard memory cap for ``list_articles``. Explicit caller limits are
#: honored up to this bound; a request above it truncates (newest rows
#: first) and logs exactly one WARNING per call.
ARTICLES_LIST_HARD_CAP = 20_000


class ArticlesMixin(_NewsDbCoreProto):
    """ArticlesMixin — verbatim method cluster from NewsDatabase."""

    @staticmethod
    def _bounded_article_limit(limit: int, hard_cap: int | None = None) -> int:
        """BUG-258: honor explicit caller limits up to the hard cap.

        See ``AnalysisMixin._bounded_limit`` for the defect narrative
        (silently clamping every request to 500 narrowed the pro-cycle
        drain pool and the auto-prune sweep to the newest 500 rows).
        Truncation above the hard cap is newest-first and logs exactly
        one WARNING per clamping call. ``hard_cap`` defaults to the
        MODULE constant (read at call time so tests can monkeypatch it).
        """
        cap = ARTICLES_LIST_HARD_CAP if hard_cap is None else hard_cap
        value = max(1, int(limit))
        if value > cap:
            logger.warning(
                "[NEWS_DB] event=LIST_LIMIT_CLAMPED table=news_articles "
                "requested=%s hard_cap=%s (oldest rows beyond the cap are "
                "excluded; narrow the request or raise ARTICLES_LIST_HARD_CAP)",
                value,
                cap,
            )
            return cap
        return value

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
                with contextlib.suppress(Exception):
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

    def find_article_by_source_url(
        self, source_id: str, canonical_url: str
    ) -> dict[str, Any] | None:
        """Exact-URL guard: same story, same source, already stored.

        Re-polls of a feed re-fetch the identical URL with a re-fabricated
        ``published_at`` (no parseable time in ~88% of feeds), which re-mints
        the time-bucketed ``article_hash`` and lets the same article be stored
        again and again (231 copies of one Bank of England notice were seen in
        production). ``(source_id, canonical_url)`` is stable across polls, so
        this is the cheapest reliable guard against re-poll duplicates.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM news_articles WHERE source_id = ? AND canonical_url = ?;",
                (source_id, canonical_url),
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
                     language, source_id, source_name, published_at, published_at_source,
                     updated_at, raw_categories, entities, topics, importance,
                     importance_score, novelty, is_duplicate, duplicate_of,
                     evidence_sources, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    row.get("published_at_source", "UNKNOWN"),
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
        bounded = self._bounded_article_limit(limit)
        sql = "SELECT * FROM news_articles"
        where: list[str] = []
        args: list[Any] = []
        if not include_duplicates:
            where.append("is_duplicate = 0")
        if status_filter:
            where.append("article_status = ?")
            args.append(status_filter)
        else:
            # news-admission-gate: QUARANTINE rows are persisted for threshold
            # tuning but are NOT part of the primary feed — every default
            # listing (analysis queue, auto-prune, worker pool, web feeds)
            # skips them unless a caller asks for a status explicitly.
            where.append("article_status != 'QUARANTINE'")
        if asset_filter:
            # LIKE is case-INsensitive on SQLite and case-SENSITIVE on
            # PostgreSQL, so an uppercase search term silently returns zero
            # rows on the switched box. The pattern is lowered on BOTH sides
            # (LOWER(column) LIKE LOWER(?)) which is portable and keeps the
            # SQLite behaviour the only behaviour — the search finds the
            # same rows under either provider. This is the silent-wrong-result
            # class: no error, no log, just an empty result.
            where.append("(LOWER(title) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(body) LIKE ?)")
            args += [f"%{asset_filter.lower()}%"] * 3
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
    # DB-LIFECYCLE: cold-payload archival (existing rows)
    # ------------------------------------------------------------------

    #: Only an ANALYZED article's payload is archival-eligible. An unanalyzed
    #: article has not yet contributed its decision value (entities/topics/
    #: impacts are still absent), so its raw text is the only evidence that
    #: value can be derived from. Archiving it would destroy capability.
    _ARCHIVE_REQUIRES_ANALYSIS = True

    def archive_duplicate_payloads(
        self,
        *,
        batch_size: int = 500,
        max_rows: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, int]:
        """Reclaim the duplicate ``body`` already persisted on existing rows.

        This is the read-side twin of the pre-insert payload gate
        (``ingest.deduplicator._gate_duplicate_payload``): it applies the
        exact same ``body == summary`` contract retroactively, so rows that
        predate the gate stop paying for a body that duplicates their own
        summary.

        Semantics (lossless by construction):

        * only rows whose body carries NO information beyond the summary are
          touched — the same ``_payload_eq`` test the ingest gate uses, so
          the decision is one rule, not two;
        * only ANALYZED articles are eligible: the analysis (entities,
          topics, impacts) is the decision value, and it is already
          extracted — the raw body has become cold evidence;
        * ``summary`` is NEVER touched. It is the surviving canonical text
          and every read consumer already falls back to it, so the article
          remains fully readable and re-analyzable;
        * the row is marked ``payload_archived = 1`` with a timestamp, so the
          read side renders an honest ARCHIVED provenance marker rather than
          silently presenting an empty body.

        Lifecycle properties required by the purge contract:

        * **idempotent** — a second run reports 0 newly archived rows;
        * **bounded** — a single call processes at most ``batch_size`` rows
          per round and stops at ``max_rows``;
        * **resumable** — the eligible scan is ordered by ``article_id`` and
          only ever looks at un-archived rows, so an interrupted run resumes
          exactly where it stopped;
        * **observable** — the returned dict carries eligible/archived/skipped
          counters plus the bytes reclaimed;
        * **dry-run** — ``dry_run=True`` selects and measures candidates
          without mutating anything (shadow mode).

        Returns a dict with keys: ``eligible``, ``archived``, ``skipped``,
        ``bytes_before``, ``bytes_after``, ``rounds``.
        """
        from nexus_scalp.news.ingest.deduplicator import _payload_eq

        bsize = max(1, int(batch_size))
        max_rows = None if max_rows is None else max(0, int(max_rows))
        counters: dict[str, int] = {
            "eligible": 0,
            "archived": 0,
            "skipped": 0,
            "bytes_before": 0,
            "bytes_after": 0,
            "rounds": 0,
        }
        analyzed_clause = (
            "AND article_id IN (SELECT article_id FROM news_analysis) "
            if self._ARCHIVE_REQUIRES_ANALYSIS
            else ""
        )
        # Eligible: an un-archived row that still carries a non-empty body.
        # The normalized body==summary test itself runs in Python (SQL has no
        # cheap cross-column normalized equality), so the scan projects only
        # (article_id, summary, body) — never the whole row.
        #
        # KEYSET CURSOR, not OFFSET: a row whose body carries real content is
        # *skipped* (not archived), so it stays payload_archived=0 with a
        # non-empty body and would be re-selected by an OFFSET/LIMIT window
        # forever. The cursor advances strictly past every processed id, so
        # one pass terminates exactly once and an interrupted run resumes at
        # the id where it stopped (the high-water mark).
        sql = (
            "SELECT article_id, summary, body FROM news_articles "
            "WHERE payload_archived = 0 AND coalesce(body, '') <> '' "
            f"AND article_id > ? {analyzed_clause} "
            "ORDER BY article_id LIMIT ?"
        )
        last_id = ""
        while True:
            remaining = None if max_rows is None else max(0, max_rows - counters["eligible"])
            if remaining is not None and remaining == 0:
                break
            # The last partial round fetches only what the cap still allows, so
            # a bounded call never processes one row more than max_rows.
            fetch = bsize if remaining is None else min(bsize, remaining)
            with self._connect() as conn:
                rows = conn.execute(sql, (last_id, fetch)).fetchall()
                if not rows:
                    break
                to_archive: list[str] = []
                for r in rows:
                    aid = r["article_id"]
                    last_id = aid
                    summary = r["summary"] if "summary" in r.keys() else ""
                    body = r["body"] if "body" in r.keys() else ""
                    if not _payload_eq(str(body), str(summary)):
                        counters["skipped"] += 1
                        continue
                    counters["eligible"] += 1
                    counters["bytes_before"] += len(str(body).encode("utf-8"))
                    to_archive.append(aid)
                if dry_run:
                    counters["rounds"] += 1
                    continue
                for aid in to_archive:
                    conn.execute(
                        "UPDATE news_articles SET body = '', payload_archived = 1, "
                        "payload_archived_at = ? WHERE article_id = ?;",
                        (self._now(), aid),
                    )
                    counters["archived"] += 1
                counters["rounds"] += 1
            if max_rows is not None and counters["eligible"] >= max_rows:
                break
        return counters

    def count_payload_archived(self) -> int:
        """Rows whose duplicate body has been archived out of the table."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM news_articles WHERE payload_archived = 1;",
            ).fetchone()
            return int(row["c"]) if row else 0

    # ------------------------------------------------------------------
    # Entities / topics
    # ------------------------------------------------------------------
