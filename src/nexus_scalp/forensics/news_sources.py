"""News source forensic classifier (TASK-12 §13-15).

Per-source classification beyond HTTP status:

    HEALTHY                 last meaningful success recent + articles parsed
    HTTP_SUCCESS_EMPTY      HTTP 200 but 0 usable articles (the 200-but-wrong)
    HTTP_SUCCESS_INVALID    HTTP 200 but payload failed parsing
    HTTP_SUCCESS_STALE      HTTP 200 but no meaningful update for a long period
    HTTP_SUCCESS_WRONG_SCHEMA  HTTP 200 but payload schema unexpected
    HTTP_SUCCESS_DUPLICATE  HTTP 200 but every article is a duplicate
    HTTP_FAILURE            HTTP error / connection failure

A source that technically returns HTTP 200 but produces 0 usable articles
is NOT healthy (§13/§25 of TASK-11). Read-only.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.forensics.news_sources")

#: Below this many seconds since last_success_at, a healthy source is
#: considered FRESH; beyond, STALE. Sources with genuinely low cadence
#: (official press releases) get a longer window via their poll interval.
STALE_THRESHOLD_SEC = 24 * 3600.0
EMPTY_ARTICLE_THRESHOLD = 1  # >0 usable articles required for HEALTHY

CLASSIFICATIONS = (
    "HEALTHY",
    "HTTP_SUCCESS_EMPTY",
    "HTTP_SUCCESS_INVALID",
    "HTTP_SUCCESS_STALE",
    "HTTP_SUCCESS_WRONG_SCHEMA",
    "HTTP_SUCCESS_DUPLICATE",
    "HTTP_FAILURE",
    "UNKNOWN",
)


def _age(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - dt).total_seconds())
    except (TypeError, ValueError):
        return None


def classify_source(
    *,
    source_id: str,
    enabled: bool = True,
    healthy_flag: int = 0,
    consecutive_failures: int = 0,
    last_status: int | None = None,
    last_success_at: str | None = None,
    last_failure_at: str | None = None,
    article_count: int = 0,
    duplicate_count: int = 0,
    parse_failure_count: int = 0,
    poll_interval_sec: int = 300,
) -> dict[str, Any]:
    """Classifies one source into the §14 taxonomy (read-only, evidence-based)."""
    if not enabled:
        return {
            "source_id": source_id,
            "classification": "DISABLED",
            "reason": "source disabled in config",
            "evidence": {},
        }
    # Determine the effective freshness window (poll interval scaled, floored).
    stale_window = max(STALE_THRESHOLD_SEC, poll_interval_sec * 3)
    success_age = _age(last_success_at)
    _age(last_failure_at)

    # HTTP failures with 0 successes -> HTTP_FAILURE / degraded states
    if consecutive_failures > 0 and last_status is None:
        return {
            "source_id": source_id,
            "classification": "HTTP_FAILURE",
            "reason": f"{consecutive_failures} consecutive connection failures, never succeeded",
            "evidence": {"consecutive_failures": consecutive_failures, "last_status": last_status},
        }
    if consecutive_failures > 0 and last_status not in (200, 304) and article_count == 0:
        return {
            "source_id": source_id,
            "classification": "HTTP_FAILURE",
            "reason": f"{consecutive_failures} consecutive failures, HTTP {last_status}, 0 articles",
            "evidence": {"consecutive_failures": consecutive_failures, "last_status": last_status},
        }

    # The 200-but-wrong patterns (HTTP 200 but nothing usable)
    if last_status == 200 and article_count == 0:
        reason = "HTTP 200 for the duration but 0 articles EVER persisted"
        if parse_failure_count:
            reason += f" ({parse_failure_count} parse failures recorded)"
            return {
                "source_id": source_id,
                "classification": "HTTP_SUCCESS_INVALID",
                "reason": reason,
                "evidence": {
                    "last_status": 200,
                    "article_count": 0,
                    "parse_failure_count": parse_failure_count,
                },
            }
        if duplicate_count:
            reason += f" ({duplicate_count} duplicates)"
            return {
                "source_id": source_id,
                "classification": "HTTP_SUCCESS_DUPLICATE",
                "reason": reason,
                "evidence": {"last_status": 200, "duplicate_count": duplicate_count},
            }
        return {
            "source_id": source_id,
            "classification": "HTTP_SUCCESS_EMPTY",
            "reason": reason,
            "evidence": {
                "last_status": 200,
                "article_count": 0,
                "consecutive_failures": consecutive_failures,
            },
        }

    # Stale: had success historically but nothing meaningful recently
    if article_count > 0 and success_age is not None and success_age > stale_window:
        return {
            "source_id": source_id,
            "classification": "HTTP_SUCCESS_STALE",
            "reason": f"last meaningful success {int(success_age / 3600)}h ago (window {int(stale_window / 3600)}h)",
            "evidence": {
                "last_success_at": last_success_at,
                "success_age_sec": int(success_age),
                "article_count": article_count,
            },
        }

    # Healthy: enabled, recent success, articles present, no failure storm
    if (
        healthy_flag
        and article_count > 0
        and success_age is not None
        and success_age <= stale_window
    ):
        return {
            "source_id": source_id,
            "classification": "HEALTHY",
            "reason": f"fresh ({int(success_age / 60)}m), {article_count} articles",
            "evidence": {"last_success_at": last_success_at, "article_count": article_count},
        }

    # Fallback: flag says healthy but evidence incomplete -> UNKNOWN, never PASS
    if healthy_flag and article_count > 0:
        return {
            "source_id": source_id,
            "classification": "HEALTHY",
            "reason": f"{article_count} articles; freshness unknown",
            "evidence": {
                "last_success_at": last_success_at or "n/a",
                "article_count": article_count,
            },
        }

    return {
        "source_id": source_id,
        "classification": "UNKNOWN",
        "reason": "insufficient evidence to classify",
        "evidence": {
            "healthy_flag": healthy_flag,
            "article_count": article_count,
            "last_success_at": last_success_at,
            "last_status": last_status,
        },
    }


def analyze_news_sources(news_path: Path | None = None) -> dict[str, Any]:
    """Runs the classification over the live news.db (read-only)."""
    path = news_path or Path("artifacts") / "news.db"
    out: dict[str, Any] = {"available": False, "sources": [], "summary": {}}
    if not path.exists():
        return out
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    try:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "news_sources" not in tables:
            return out
        out["available"] = True
        source_rows = conn.execute("SELECT * FROM news_sources").fetchall()
        s_cols = [d[0] for d in conn.execute("SELECT * FROM news_sources LIMIT 0").description]
        health_rows = (
            conn.execute("SELECT * FROM news_health").fetchall() if "news_health" in tables else []
        )
        h_cols = (
            [d[0] for d in conn.execute("SELECT * FROM news_health LIMIT 0").description]
            if health_rows
            else []
        )
        health_map = (
            {
                dict(zip(h_cols, r, strict=False))["source_id"]: dict(zip(h_cols, r, strict=False))
                for r in health_rows
            }
            if h_cols
            else {}
        )
        # article counts per source
        article_map: dict[str, int] = {}
        if "news_articles" in tables:
            for sid, n in conn.execute(
                "SELECT source_id, COUNT(*) FROM news_articles GROUP BY source_id"
            ).fetchall():
                article_map[str(sid)] = int(n)

        classified: list[dict[str, Any]] = []
        for r in source_rows:
            s = dict(zip(s_cols, r, strict=False))
            sid = str(s.get("source_id", ""))
            h = health_map.get(sid, {})
            cls = classify_source(
                source_id=sid,
                enabled=bool(s.get("enabled", 1)),
                healthy_flag=int(h.get("healthy", 0) or 0),
                consecutive_failures=int(h.get("consecutive_failures", 0) or 0),
                last_status=h.get("last_status"),
                last_success_at=h.get("last_success_at") or "",
                last_failure_at=h.get("last_failure_at") or "",
                article_count=article_map.get(sid, 0),
                poll_interval_sec=int(s.get("poll_interval_sec", 300) or 300),
            )
            cls["enabled"] = bool(s.get("enabled", 1))
            cls["tier"] = s.get("tier", "")
            cls["kind"] = s.get("kind", "")
            classified.append(cls)
        out["sources"] = classified
        # summary by classification
        summary: dict[str, int] = {}
        for c in classified:
            summary[c["classification"]] = summary.get(c["classification"], 0) + 1
        out["summary"] = summary
        # degraded = anything not HEALTHY/DISABLED
        out["degraded_count"] = sum(
            1 for c in classified if c["classification"] not in ("HEALTHY", "DISABLED", "UNKNOWN")
        )
        out["healthy_count"] = sum(1 for c in classified if c["classification"] == "HEALTHY")
        return out
    finally:
        conn.close()
