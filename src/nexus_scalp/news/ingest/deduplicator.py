"""News deduplication engine (PHASE 12).

Deterministic article identity so the same story arriving through multiple
RSS feeds / syndication / rewritten headlines / different URLs collapses into
ONE canonical event with MULTIPLE source evidence.

Identity = sha256 over a meaningful combination:
    * canonical URL (normalized),
    * normalized title (lowercased, punctuation/stopword-squeezed),
    * source,
    * publication timestamp bucket,
    * content fingerprint (summary/body normalized).

Two different hashes are used:
    * ``title_hash``  - exact-title identity (fast reject),
    * ``article_hash`` - full canonical identity persisted on the article.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

_TITLE_CLEAN_RE = re.compile(r"[^a-z0-9\s]")
_WS_RE = re.compile(r"\s+")

#: Words that carry no identity signal in headlines.
_STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "but",
    "of",
    "for",
    "to",
    "in",
    "on",
    "at",
    "by",
    "with",
    "from",
    "as",
    "is",
    "are",
    "was",
    "were",
    "has",
    "have",
    "had",
    "s",
    "t",
    "it",
    "its",
    "this",
    "that",
    "these",
    "those",
    "up",
    "down",
    "over",
    "under",
    "after",
    "before",
    "vs",
    "versus",
    "new",
    "report",
    "reports",
    "say",
    "says",
    "said",
    "will",
    "can",
    "could",
    "would",
    "should",
    "may",
    "might",
}


def normalize_url(url: str) -> str:
    """Normalizes a URL for identity: strip scheme/www, trailing slashes,
    tracking query params, fragment, utm params."""
    url = (url or "").strip()
    url = re.sub(r"[?#].*$", "", url)  # drop query + fragment
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^www\.", "", url)
    url = url.rstrip("/")
    return url.lower()


def normalize_title(title: str) -> str:
    """Normalized title: lowercase, strip punctuation, squeeze whitespace,
    prune stopwords → identity tokens joined by single spaces."""
    t = unicodedata.normalize("NFKD", title or "")
    t = t.lower()
    t = _TITLE_CLEAN_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    tokens = [w for w in t.split(" ") if w and w not in _STOPWORDS]
    return " ".join(tokens)


def _content_fingerprint(summary: str, body: str, title: str) -> str:
    """Fingerprint of the textual payload (normalized, first 2000 chars)."""
    text = " ".join([normalize_title(title), summary or "", body or ""])
    return hashlib.sha256(text[:2000].encode("utf-8")).hexdigest()


def compute_title_hash(title: str) -> str:
    return hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()


def compute_article_hash(
    *,
    url: str,
    title: str,
    source_id: str,
    published_at: datetime,
    summary: str = "",
    body: str = "",
) -> str:
    """Deterministic canonical identity for one article occurrence.

    Published time is bucketed to 60s so identical stories published seconds
    apart still merge, while genuinely different coverage stays distinct.
    """
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=UTC)
    time_bucket = int(published_at.timestamp()) // 60
    digest = hashlib.sha256()
    payload = "|".join(
        [
            normalize_url(url),
            normalize_title(title),
            source_id,
            str(time_bucket),
            _content_fingerprint(summary, body, title),
        ]
    )
    digest.update(payload.encode("utf-8"))
    return digest.hexdigest()


def canonicalize_item(item: dict[str, Any], source_id: str, source_name: str) -> dict[str, Any]:
    """Normalizes one raw feed item into the canonical article dict shape.

    ``published_at`` / ``updated_at`` are returned as datetime objects
    (UTC); consumers serialize for persistence.
    """
    title = (item.get("title") or "").strip()
    url = (item.get("url") or "").strip()
    summary = (item.get("summary") or "").strip()
    body = (item.get("body") or "").strip()
    published = item.get("published_at") or datetime.now(UTC)
    updated = item.get("updated_at")
    published_dt = _as_dt(published)
    updated_dt = _as_dt(updated) if updated else None
    article_hash = compute_article_hash(
        url=url,
        title=title,
        source_id=source_id,
        published_at=published_dt,
        summary=summary,
        body=body,
    )
    return {
        "title": title,
        "url": url,
        "summary": summary,
        "body": body,
        "published_at": published_dt,
        "updated_at": updated_dt,
        "source_id": source_id,
        "source_name": source_name,
        "article_hash": article_hash,
        "title_hash": compute_title_hash(title),
        "raw_categories": item.get("categories", []),
    }


def _as_dt(value: Any):
    """Coerces a datetime | ISO string | None to a UTC datetime."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


class NewsDeduplicator:
    """Collapses duplicate occurrences onto one canonical event.

    Strategy:
        1. Exact article_hash hit        -> duplicate (add evidence source).
        2. normalized-title + source hit -> duplicate within merge window.
        3. normalized-title + ANY source hit within short window
           (syndication)                  -> duplicate (add evidence source).
        4. otherwise                       -> canonical NEW article.
    """

    def __init__(self, merge_window_sec: float = 3600.0) -> None:
        self.merge_window_sec = float(merge_window_sec)
        # title_hash -> list of (published_ts, article_id)
        self._recent_by_title: dict[str, list[tuple[float, str]]] = {}

    def register_canonical(
        self, article_hash: str, title_hash: str, published_ts: float, article_id: str
    ) -> None:
        self._recent_by_title.setdefault(title_hash, []).append((published_ts, article_id))

    def find_duplicate_title(
        self, title_hash: str, published_ts: float, now_ts: float
    ) -> str | None:
        """Returns an article_id when the same normalized title was seen with a
        publication time within the merge window.

        The window is measured on PUBLICATION time proximity (not ingestion
        wall-clock): a story published at 10:00 and syndicated at 10:02 is the
        same story whether we ingest it today or three days later.
        """
        for seen_ts, article_id in self._recent_by_title.get(title_hash, []):
            if abs(published_ts - seen_ts) <= self.merge_window_sec:
                return article_id
        return None
