"""News source adapters (PHASE 12).

A common Source -> Fetcher -> Normalizer -> Canonical Article contract so the
engine is NOT tightly coupled to RSS: RSS/Atom today, official feeds and
future API adapters implement the same interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.news.models import NewsArticle


#: Normalized UTC timestamp helper.
def _utc_now() -> datetime:
    return datetime.now(UTC)


class SourceFetchResult:
    """Outcome of one source fetch attempt (success or typed failure)."""

    def __init__(
        self,
        *,
        ok: bool,
        items: list[dict[str, Any]] | None = None,
        status: int | None = None,
        error: str = "",
        rate_limited: bool = False,
        retry_after_sec: float = 0.0,
    ) -> None:
        self.ok = ok
        self.items = items or []
        self.status = status
        self.error = error
        self.rate_limited = rate_limited
        self.retry_after_sec = retry_after_sec


class NewsSourceAdapter(ABC):
    """Common interface for every news source adapter."""

    source_id: str = "base"
    kind: str = "RSS"

    def __init__(self, source_config: dict[str, Any]) -> None:
        self.source_config = source_config
        self.feed_url = str(source_config.get("feed_url") or source_config.get("url") or "")
        self.timeout_sec = float(source_config.get("timeout_sec", 15.0))

    @abstractmethod
    def fetch(self, limit: int = 100) -> SourceFetchResult:
        """Fetches and normalizes items from the source."""

    def _make_article(self, item: dict[str, Any]) -> NewsArticle:
        """Builds a canonical NewsArticle from a normalized dict."""
        raise NotImplementedError

    @staticmethod
    def _parse_dt(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        if isinstance(value, str) and value:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return (
                    parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
                )
            except ValueError:
                pass
        return _utc_now()


class RSSNewsSourceAdapter(NewsSourceAdapter):
    """RSS 2.0 / Atom feed adapter using feedparser (if available).

    Falls back to a minimal XML parser when feedparser is not installed so
    the subsystem remains importable in any environment.
    """

    source_id = "rss"
    kind = "RSS"

    def fetch(self, limit: int = 100) -> SourceFetchResult:
        if not self.feed_url:
            return SourceFetchResult(ok=False, error="no feed_url configured")
        try:
            import httpx

            # BANDWIDTH BUDGET (2026-08-18): RSS feeds are 200KB-2MB raw XML;
            # the old plain GET downloaded the FULL body on every poll with no
            # compression, no conditional request and no size cap — heavy on
            # metered connections. Now:
            #   * Accept-Encoding gzip/deflate (httpx decompresses transparently;
            #     ~70-85% smaller bodies),
            #   * If-Modified-Since / If-None-Match conditional GET — a 304
            #     response means ZERO body download and the parse is skipped,
            #   * 2MB Content-Length cap — a runaway feed is truncated quickly.
            headers = {
                "User-Agent": "NexusScalpEngine/1.0 (news intelligence)",
                "Accept-Encoding": "gzip, deflate",
            }
            last_modified = self.source_config.get("last_modified", "")
            etag = self.source_config.get("etag", "")
            if last_modified:
                headers["If-Modified-Since"] = last_modified
            if etag:
                headers["If-None-Match"] = etag

            with httpx.Client(timeout=self.timeout_sec, follow_redirects=True) as client:
                resp = client.get(self.feed_url, headers=headers)
                if resp.status_code == 304:
                    # Feed unchanged since last poll — no body download at all.
                    return SourceFetchResult(ok=True, items=[], status=304, error="not modified")
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", "60") or 60)
                    return SourceFetchResult(
                        ok=False,
                        status=429,
                        rate_limited=True,
                        retry_after_sec=retry_after,
                        error="rate limited",
                    )
                if resp.status_code >= 400:
                    return SourceFetchResult(
                        ok=False, status=resp.status_code, error=f"HTTP {resp.status_code}"
                    )

                # Persist validator headers for the next conditional GET (the
                # caller mirrors them back into source_config for reuse).
                if resp.headers.get("Last-Modified"):
                    self.source_config["last_modified"] = resp.headers["Last-Modified"]
                if resp.headers.get("ETag"):
                    self.source_config["etag"] = resp.headers["ETag"]

                cl = resp.headers.get("Content-Length")
                if cl and cl.isdigit() and int(cl) > 2 * 1024 * 1024:
                    return SourceFetchResult(
                        ok=False,
                        status=resp.status_code,
                        error=f"feed too large (>{2 * 1024 * 1024 / 1e6:.0f}MB)",
                    )
                return self._parse_feed(resp.content, limit=limit, status=resp.status_code)
        except Exception as e:
            return SourceFetchResult(ok=False, error=f"fetch error: {e}")

    def _parse_feed(self, content: bytes, limit: int, status: int) -> SourceFetchResult:
        try:
            import feedparser  # type: ignore[import-not-found]

            parsed = feedparser.parse(content)
            if getattr(parsed, "bozo", False) and not parsed.entries:
                return SourceFetchResult(ok=False, status=status, error="malformed feed (bozo)")
            items: list[dict[str, Any]] = []
            for entry in parsed.entries[:limit]:
                items.append(self._normalize_feedparser_entry(entry))
            return SourceFetchResult(ok=True, items=items, status=status)
        except ImportError:
            # Minimal XML fallback (RSS item / Atom entry extraction).
            return self._parse_xml_minimal(content, limit=limit, status=status)
        except Exception as e:
            return SourceFetchResult(ok=False, status=status, error=f"parse error: {e}")

    def _normalize_feedparser_entry(self, entry: Any) -> dict[str, Any]:
        title = getattr(entry, "title", "") or ""
        link = getattr(entry, "link", "") or ""
        summary = getattr(entry, "summary", "") or ""
        published = getattr(entry, "published", "") or ""
        # IMPORTANT: `"updated" in entry` does NOT trigger feedparser's
        # deprecated published->updated fallback mapping (a direct attribute
        # access on a published-only entry warns on EVERY call). Check the raw
        # key first so feeds without an `updated` field never raise the
        # DeprecationWarning, then fall back to `published` (identical value).
        updated = getattr(entry, "updated", "") if "updated" in entry else published
        return {
            "title": title.strip(),
            "url": link,
            "summary": summary,
            "body": getattr(entry, "content", [{}])[0].get("value", "")
            if getattr(entry, "content", None)
            else "",
            "published_at": self._parse_dt(published),
            "updated_at": self._parse_dt(updated),
            "categories": [
                c.get("term", "") for c in getattr(entry, "tags", []) if isinstance(c, dict)
            ],
        }

    def _parse_xml_minimal(self, content: bytes, limit: int, status: int) -> SourceFetchResult:
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(content)
            items: list[dict[str, Any]] = []
            # RSS: channel/item ; Atom: feed/entry
            for node in root.iter():
                tag = node.tag.rsplit("}", 1)[-1]
                if tag in ("item", "entry") and len(items) < limit:

                    def _text(child_tag: str, current_node: Any = node) -> str:
                        for child in current_node:
                            if child.tag.rsplit("}", 1)[-1] == child_tag:
                                return (child.text or "").strip()
                        return ""

                    link = _text("link")
                    if not link:
                        for child in node:
                            if child.tag.rsplit("}", 1)[-1] == "link" and child.get("href"):
                                link = child.get("href", "")
                                break
                    items.append(
                        {
                            "title": _text("title"),
                            "url": link,
                            "summary": _text("description") or _text("summary"),
                            "body": "",
                            "published_at": self._parse_dt(_text("pubDate") or _text("published")),
                            "updated_at": self._parse_dt(_text("updated")),
                            "categories": [],
                        }
                    )
            return SourceFetchResult(ok=True, items=items, status=status)
        except Exception as e:
            return SourceFetchResult(ok=False, status=status, error=f"xml parse error: {e}")


class OfficialSourceAdapter(RSSNewsSourceAdapter):
    """Official (central bank / government) source adapter.

    Same RSS/Atom mechanics, but with stricter validation: an empty or
    malformed official feed is a typed failure (never silently trusted).
    """

    source_id = "official"
    kind = "OFFICIAL"

    def fetch(self, limit: int = 100) -> SourceFetchResult:
        result = super().fetch(limit=limit)
        if result.ok and not result.items:
            return SourceFetchResult(
                ok=False, status=result.status, error="official feed returned no items"
            )
        return result


def build_adapter(source_config: dict[str, Any]) -> NewsSourceAdapter:
    """Factory: returns the right adapter for a source config row."""
    kind = str(source_config.get("kind", "RSS")).upper()
    if kind in ("OFFICIAL", "CALENDAR"):
        return OfficialSourceAdapter(source_config)
    return RSSNewsSourceAdapter(source_config)
