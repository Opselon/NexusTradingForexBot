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

from nexus_scalp.news.models import (
    PUBLISHED_AT_SOURCE_FEED,
    PUBLISHED_AT_SOURCE_INGEST,
)

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


def _payload_eq(a: str, b: str) -> bool:
    """Byte-identical-after-normalization payload comparison.

    Whitespace/case-insensitive so trailing whitespace, case folds, and
    paragraph re-wraps (common between ``<description>`` and
    ``<content:encoded>``) still count as the same content. This is the
    cheapest deterministic test that answers "does the body carry ANY
    information the summary does not?" — it costs two string passes, never
    a database query.
    """
    na = _WS_RE.sub(" ", (a or "").strip()).casefold()
    nb = _WS_RE.sub(" ", (b or "").strip()).casefold()
    return na == nb


#: The pre-insert payload value gate (V-class: DERIVED vs DECISION-CRITICAL).
#:
#: ``news_articles.body`` and ``news_articles.summary`` are written verbatim
#: from the RSS ``<content:encoded>`` and ``<description>`` fields. For feeds
#: with no full-text element the ingest layer *copies summary into body*
#: (``sources/base.py::_normalize_feedparser_entry`` and
#: ``_parse_xml_minimal``), so the same ~28.5 MB of text is stored twice per
#: article — measured on the production ledger: ``body`` 28,521,694 logical
#: bytes vs ``summary`` 28,517,874, ~54.4 MB of a 79.5 MB table, ~9% of the
#: whole 588 MB database. ``body`` is genuinely consumed (the analysis
#: pipeline's keyword/local/LLM paths read it), so the column cannot be
#: dropped — but a body that is a verbatim copy of the summary carries ZERO
#: additional information, and storing it doubles TOAST, WAL, index-free
#: heap width and backup size for no capability gain.
#:
#: The gate therefore answers one deterministic question BEFORE persistence:
#:
#:     "does this body carry information the summary does not?"
#:
#: If not, the canonical row records ``body = ""`` — the analysis consumers
#: already fall back ``body -> summary`` (``ai_service._build_user_prompt``,
#: ``analysis/keywords.py``, ``analysis/local.py`` join title+summary+body),
#: so no capability is lost and every existing consumer still sees the full
#: text via ``summary``. The dedup is lossless by construction: an empty body
#: is exactly "summary already holds this text".
_BODY_DUPLICATE_OF_SUMMARY = ""


def _gate_duplicate_payload(summary: str, body: str) -> str:
    """Pre-insert payload value gate: drop a body that duplicates summary.

    Returns the body value that should be PERSISTED. One of:

    * the original ``body`` — it carries real extra content (or the summary
      is empty, in which case the body is the only text and must survive);
    * ``""`` — the body is a verbatim duplicate of the summary and carries no
      information the summary does not. Downstream readers fall back to the
      summary, so no decision/risk/audit/model/replay capability is lost.

    Cost: two string normalizations — never a database query, never a remote
    call. The gate must stay cheaper than the persistence it prevents.
    """
    b = (body or "").strip()
    s = (summary or "").strip()
    if not b:
        # No body at all: nothing to gate, and the summary-only feeds keep
        # working exactly as before.
        return ""
    if not s:
        # A body with no summary: the body IS the text. Keep it (the
        # summary-first consumers would otherwise see nothing).
        return b
    if _payload_eq(b, s):
        # The duplicate case: body is the summary re-stored. Drop the copy.
        return _BODY_DUPLICATE_OF_SUMMARY
    return b


def _content_fingerprint(summary: str, body: str, title: str) -> str:
    """Fingerprint of the textual payload (normalized, first 2000 chars).

    DB-LIFECYCLE: fingerprints the RAW summary+body BEFORE the payload gate
    applies. The article identity must be stable across the gate's
    introduction: an article whose body duplicates its summary would
    otherwise mint a NEW article_hash after this change and re-enter the
    ledger as a "new" story on the next poll, defeating the very dedup this
    module exists to provide. The fingerprint is computed from the source
    text, the gate only decides what is persisted.
    """
    text = " ".join([normalize_title(title), summary or "", body or ""])
    return hashlib.sha256(text[:2000].encode("utf-8")).hexdigest()


def compute_title_hash(title: str) -> str:
    return hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()


def compute_article_hash(
    *,
    url: str,
    title: str,
    source_id: str,
    published_at: datetime | None,
    summary: str = "",
    body: str = "",
) -> str:
    """Deterministic canonical identity for one article occurrence.

    Published time is bucketed to 60s so identical stories published seconds
    apart still merge, while genuinely different coverage stays distinct.

    BUG-282: ``published_at=None`` means the feed carried NO parseable
    publication time. The time bucket is then omitted entirely, making the
    identity stable across re-polls. Previously the caller fabricated
    ``now()`` per poll, which re-minted a fresh hash every ~40 minutes for
    feeds without ISO timestamps (88% of rows) and defeated the
    article_hash/tombstone dedup guards structurally (26,161 byte-identical
    rows, ~80 MB in the production news.db — wave lane-04 RC2 / lane-06 §3).
    """
    fields = [normalize_url(url), normalize_title(title), source_id]
    if published_at is not None:
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=UTC)
        fields.append(str(int(published_at.timestamp()) // 60))
    fields.append(_content_fingerprint(summary, body, title))
    digest = hashlib.sha256()
    digest.update("|".join(fields).encode("utf-8"))
    return digest.hexdigest()


def canonicalize_item(item: dict[str, Any], source_id: str, source_name: str) -> dict[str, Any]:
    """Normalizes one raw feed item into the canonical article dict shape.

    ``published_at`` / ``updated_at`` are returned as datetime objects
    (UTC) or ``None`` when the feed carried no real publication time —
    BUG-282: never silently fabricated to wall clock. ``published_at_source``
    records the provenance ('FEED' vs 'INGEST_TIME') so decay/staleness
    consumers can distinguish a genuine event time from an ingest stamp.
    Consumers serialize for persistence.
    """
    title = (item.get("title") or "").strip()
    url = (item.get("url") or "").strip()
    summary = (item.get("summary") or "").strip()
    body = (item.get("body") or "").strip()
    published = item.get("published_at")
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
    # PRE-INSERT VALUE GATE (database-lifecycle remediation): the body is
    # only worth its TOAST/heap bytes when it is NOT a restatement of the
    # summary. The fingerprint above still sees the RAW body so article
    # identity is unaffected by the gate. See _gate_duplicate_payload.
    persisted_body = _gate_duplicate_payload(summary, body)
    return {
        "title": title,
        "url": url,
        "summary": summary,
        "body": persisted_body,
        "body_deduped": persisted_body != body,
        "published_at": published_dt,
        "published_at_source": PUBLISHED_AT_SOURCE_FEED
        if published_dt
        else PUBLISHED_AT_SOURCE_INGEST,
        "updated_at": updated_dt,
        "source_id": source_id,
        "source_name": source_name,
        "article_hash": article_hash,
        "title_hash": compute_title_hash(title),
        "raw_categories": item.get("categories", []),
    }


def _as_dt(value: Any) -> datetime | None:
    """Coerces a datetime | ISO string | None to a UTC datetime.

    BUG-282: returns None (instead of fabricating now()) when the value is
    absent/unparseable, so the article identity and the persisted provenance
    both record the missing event time honestly.
    """
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
        except ValueError:
            pass
        # RFC-822 (RSS pubDate) forms reaching the dedup layer from any
        # producer, not just the RSS adapter.
        try:
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
    return None


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
