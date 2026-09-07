"""Probe more RSS candidates for Treasury/BEA +ForexLive alt + investing/factory calendars + nasdaq."""
from __future__ import annotations

import httpx
import feedparser

UA = "NexusScalpEngine/1.0 (news intelligence)"

CANDIDATES = {
    # Treasury alternate paths
    "treasury_sanctions": "https://home.treasury.gov/feed/press-releases",
    "treasury_index": "https://home.treasury.gov/news/press-releases/feed",
    # BEA
    "bea_blog_rss": "https://www.bea.gov/blog/feed",
    "bea_wire": "https://www.bea.gov/news/blog/rss.xml",
    # Investing.com econ calendar endpoints (often JSON, likely blocked)
    "investing_cal": "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData",
    # Nasdaq econ calendar day API (public)
    "nasdaq_cal": "https://api.nasdaq.com/api/calendar/economiccalendar?date=2026-09-07",
    # forexlive alt
    "forexlive_alt": "https://www.forexlive.com/feed/news",
    # FXStreet news
    "fxstreet": "https://www.fxstreet.com/rss/news",
    # DailyFX
    "dailyfx": "https://www.dailyfx.com/feeds/market-news",
}


def main() -> None:
    for name, url in CANDIDATES.items():
        try:
            headers = {"User-Agent": UA, "Accept-Encoding": "gzip, deflate"}
            if "nasdaq" in name:
                headers["Accept"] = "application/json, text/plain, */*"
            with httpx.Client(timeout=15.0, follow_redirects=True) as c:
                r = c.get(url, headers=headers)
            body = r.content or b""
            if r.status_code != 200:
                print(f"{name:18s} status={r.status_code}")
                continue
            ctype = r.headers.get("content-type", "")
            if "json" in ctype or body[:1] in (b"{", b"["):
                print(f"{name:18s} status=200 JSON bytes={len(body)} head={body[:120]!r}")
                continue
            parsed = feedparser.parse(body)
            e0 = parsed.entries[0] if parsed.entries else None
            print(f"{name:18s} status=200 entries={len(parsed.entries)} bozo={parsed.bozo}"
                  f" first={getattr(e0, 'title', '')[:60]!r}")
        except Exception as exc:
            print(f"{name:18s} FAILED {type(exc).__name__}: {str(exc)[:70]}")


if __name__ == "__main__":
    main()
