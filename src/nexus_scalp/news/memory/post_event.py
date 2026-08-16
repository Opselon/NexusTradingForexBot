"""News memory: post-event validation + impact feedback (PHASE 12).

After a news event the engine compares PREDICTED IMPACT vs ACTUAL MARKET
RESPONSE and stores the feedback - creating NEWS EXPERIENCE MEMORY:

    * direction accuracy
    * magnitude error
    * time-to-response
    * persistence
    * regime dependency

This feedback is historical evidence consumed by future research/model
phases; it NEVER directly modifies the production model.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.news.database import NewsDatabase
from nexus_scalp.news.models import NewsDirection
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.news.memory")

#: Minimum market-response samples before a feedback row is meaningful.
MIN_RESPONSE_SAMPLES = 3


#: A direction call is "correct" when the actual move matches the prediction.
def direction_correct(predicted: NewsDirection, actual_move_pct: float) -> bool:
    if predicted == NewsDirection.BULLISH:
        return actual_move_pct > 0.0
    if predicted == NewsDirection.BEARISH:
        return actual_move_pct < 0.0
    return False  # NEUTRAL/MIXED/CONFLICTED are not "correct/incorrect" calls


class PostEventValidator:
    """Records + evaluates predicted-vs-actual news impact."""

    def __init__(self, db: NewsDatabase) -> None:
        self.db = db
        self._ensure_table()

    def _ensure_table(self) -> None:
        """Creates the news_post_event table (idempotent, additive)."""
        try:
            conn = sqlite3.connect(str(self.db.db_path), timeout=5.0)
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS news_post_event (
                        record_id TEXT PRIMARY KEY,
                        article_id TEXT NOT NULL,
                        predicted_direction TEXT NOT NULL,
                        predicted_strength REAL NOT NULL DEFAULT 0.0,
                        predicted_horizon TEXT NOT NULL DEFAULT 'MACRO',
                        actual_move_pct REAL NOT NULL DEFAULT 0.0,
                        actual_volatility REAL NOT NULL DEFAULT 0.0,
                        direction_accuracy REAL NOT NULL DEFAULT 0.0,
                        magnitude_error REAL NOT NULL DEFAULT 0.0,
                        timing_error_sec REAL NOT NULL DEFAULT 0.0,
                        persistence_sec REAL NOT NULL DEFAULT 0.0,
                        regime TEXT DEFAULT '',
                        evaluated_at TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_post_event_article ON news_post_event(article_id);"
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error("[NEWS_MEMORY] post_event schema init failed", error=str(e))

    def record_response(
        self,
        *,
        article_id: str,
        predicted_direction: NewsDirection,
        predicted_strength: float,
        predicted_horizon: str,
        response_samples: list[tuple[datetime, float]],  # (ts, move_pct)
        regime: str = "",
    ) -> dict[str, Any]:
        """Computes and stores one prediction-vs-actual evaluation.

        response_samples: time-ordered (timestamp, cumulative move %) samples
        measured after the article's publication.
        """
        record_id = f"pev_{uuid.uuid4().hex[:12]}"
        eval_ts = datetime.now(UTC)

        if len(response_samples) < MIN_RESPONSE_SAMPLES:
            row = {
                "record_id": record_id,
                "article_id": article_id,
                "predicted_direction": predicted_direction.value,
                "predicted_strength": round(predicted_strength, 4),
                "predicted_horizon": predicted_horizon,
                "actual_move_pct": 0.0,
                "actual_volatility": 0.0,
                "direction_accuracy": 0.0,
                "magnitude_error": 0.0,
                "timing_error_sec": 0.0,
                "persistence_sec": 0.0,
                "regime": regime,
                "evaluated_at": eval_ts.isoformat(),
            }
            self._insert(row)
            return row

        final_move = response_samples[-1][1]
        acc = 1.0 if direction_correct(predicted_direction, final_move) else 0.0
        mag_err = abs(final_move) - predicted_strength  # in % units
        moves = [m for _, m in response_samples]
        vol = max(moves) - min(moves)
        # time-to-first-significant-move (>= 0.05%)
        timing = 0.0
        first_sig = next(((ts, m) for ts, m in response_samples if abs(m) >= 0.05), None)
        if first_sig:
            timing = max(0.0, (first_sig[0] - response_samples[0][0]).total_seconds())
        persistence = max(0.0, (response_samples[-1][0] - response_samples[0][0]).total_seconds())

        row = {
            "record_id": record_id,
            "article_id": article_id,
            "predicted_direction": predicted_direction.value,
            "predicted_strength": round(predicted_strength, 4),
            "predicted_horizon": predicted_horizon,
            "actual_move_pct": round(final_move, 4),
            "actual_volatility": round(vol, 4),
            "direction_accuracy": round(acc, 4),
            "magnitude_error": round(mag_err, 4),
            "timing_error_sec": round(timing, 2),
            "persistence_sec": round(persistence, 2),
            "regime": regime,
            "evaluated_at": eval_ts.isoformat(),
        }
        self._insert(row)
        logger.info(
            "[NEWS_MEMORY] event=POST_EVENT article_id=%s direction_acc=%.2f mag_err=%.3f",
            article_id,
            acc,
            mag_err,
        )
        return row

    def _insert(self, row: dict[str, Any]) -> None:
        try:
            conn = sqlite3.connect(str(self.db.db_path), timeout=5.0)
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO news_post_event
                        (record_id, article_id, predicted_direction, predicted_strength,
                         predicted_horizon, actual_move_pct, actual_volatility,
                         direction_accuracy, magnitude_error, timing_error_sec,
                         persistence_sec, regime, evaluated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["record_id"],
                        row["article_id"],
                        row["predicted_direction"],
                        row["predicted_strength"],
                        row["predicted_horizon"],
                        row["actual_move_pct"],
                        row["actual_volatility"],
                        row["direction_accuracy"],
                        row["magnitude_error"],
                        row["timing_error_sec"],
                        row["persistence_sec"],
                        row["regime"],
                        row["evaluated_at"],
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error("[NEWS_MEMORY] post_event insert failed", error=str(e))

    def list_records(self, article_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 500))
        sql = "SELECT * FROM news_post_event"
        args: list[Any] = []
        if article_id:
            sql += " WHERE article_id = ?"
            args.append(article_id)
        sql += " ORDER BY evaluated_at DESC LIMIT ?;"
        args.append(bounded)
        try:
            conn = sqlite3.connect(str(self.db.db_path), timeout=5.0)
            try:
                conn.row_factory = sqlite3.Row
                return [dict(r) for r in conn.execute(sql, args).fetchall()]
            finally:
                conn.close()
        except Exception:
            return []

    def accuracy_summary(self, limit: int = 200) -> dict[str, Any]:
        """Aggregate accuracy of recent predictions (historical evidence)."""
        records = self.list_records(limit=limit)
        if not records:
            return {"records": 0, "direction_accuracy": None, "avg_magnitude_error": None}
        accs = [r["direction_accuracy"] for r in records]
        mags = [abs(r["magnitude_error"]) for r in records]
        return {
            "records": len(records),
            "direction_accuracy": round(sum(accs) / len(accs), 4),
            "avg_magnitude_error": round(sum(mags) / len(mags), 4),
        }
