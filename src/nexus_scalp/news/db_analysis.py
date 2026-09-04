"""News DB analysis write path: entities/topics/analysis/impacts/consensus/
AI analysis/trade links.

Extracted VERBATIM from news/database.py (Agent-5 modularization,
CHG-0032-A1 program). Mixin over the shared connection base. USED BY:
news/database.py. DO-NOT-PUT-HERE: article ingestion (db_articles).
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from nexus_scalp.news._db_core_protocol import _NewsDbCoreProto


class AnalysisMixin(_NewsDbCoreProto):
    """AnalysisMixin — verbatim method cluster from NewsDatabase."""

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
        with contextlib.suppress(Exception):
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
