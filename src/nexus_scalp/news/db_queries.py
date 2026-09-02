"""News DB read/ops surface: counts, impact timeline, status, runs, health,
worker state, rebuild, summary.

Extracted VERBATIM from news/database.py (Agent-5 modularization,
CHG-0032-A1 program). Mixin over the shared connection base; close() keeps
connection-lifetime semantics unchanged. USED BY: news/database.py.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from nexus_scalp.news._db_core_protocol import _NewsDbCoreProto


class QueriesMixin(_NewsDbCoreProto):
    """QueriesMixin — verbatim method cluster from NewsDatabase."""

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

    def close(self) -> None:
        """No persistent connection to close (connections are per-call);
        provided for parity with AuditRepository lifecycle used by the
        release/repair tooling."""
        return
