"""Final calendar source probe: BLS with browser UA, DOL economic releases page."""
from __future__ import annotations

import re

import httpx

BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

for name, url, ua in (
    ("bls_browser_ua", "https://www.bls.gov/schedule/news_release/cpi.htm", BROWSER_UA),
    ("bls_default_ua", "https://www.bls.gov/schedule/news_release/cpi.htm", "NexusScalpEngine/1.0 (news intelligence)"),
    ("dol_releases", "https://www.dol.gov/newsroom/economicdata", BROWSER_UA),
    ("bls_feed_browser", "https://www.bls.gov/feed/news.releases.rss", BROWSER_UA),
):
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": ua, "Accept": "text/html,application/xhtml+xml,application/xml,*/*", "Accept-Encoding": "gzip, deflate"})
            body = resp.content or b""
            iso = re.findall(rb"\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}", body[:200000])
            print(f"{name}: status={resp.status_code} bytes={len(body)} dateish={len(iso)}")
            if resp.status_code == 200 and name == "bls_browser_ua":
                txt = re.sub(rb"<[^>]+>", b" ", body)
                m = re.findall(rb"(8:30 a\.m\.|10:00 a\.m\.|2:00 p\.m\.)", txt[:200000])
                print("   time mentions:", len(m))
    except Exception as exc:
        print(f"{name}: FAILED {type(exc).__name__}: {exc}")
