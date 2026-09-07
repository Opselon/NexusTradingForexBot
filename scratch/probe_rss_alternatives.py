"""Probe official RSS alternatives for bea/treasury/cftc (must verify before seed change)."""
from __future__ import annotations

import sys

import httpx
import feedparser

UA = "NexusScalpEngine/1.0 (news intelligence)"

CANDIDATES = {
    "bea_rss": "https://www.bea.gov/news/rss/news.htm",
    "bea_rss2": "https://www.bea.gov/rss/news.xml",
    "treasury_rss": "https://home.treasury.gov/rss/press-releases",
    "treasury_rss2": "https://home.treasury.gov/rss/news",
    "cftc_rss": "https://www.cftc.gov/RSS/RSSGP/rssgp.xml",
    "cftc_press": "https://www.cftc.gov/RSS/pressrelease/rss.xml",
    "cnbc_econ": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
}


def main() -> None:
    for name, url in CANDIDATES.items():
        try:
            with httpx.Client(timeout=15.0, follow_redirects=True) as c:
                r = c.get(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
            if r.status_code != 200:
                print(f"{name:14s} status={r.status_code}")
                continue
            parsed = feedparser.parse(r.content)
            e0 = parsed.entries[0] if parsed.entries else None
            print(f"{name:14s} status=200 entries={len(parsed.entries)} bozo={parsed.bozo}"
                  f" first={getattr(e0, 'title', '')[:60]!r}")
        except Exception as exc:
            print(f"{name:14s} FAILED {type(exc).__name__}: {str(exc)[:70]}")


if __name__ == "__main__":
    main()
