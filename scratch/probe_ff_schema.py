"""Inspect FF calendar JSON event schema fully + test more week endpoints."""
from __future__ import annotations

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"

with httpx.Client(timeout=25.0, follow_redirects=True) as c:
    r = c.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", headers={"User-Agent": UA})
    data = r.json()
    print("count:", len(data))
    print("full first event:", data[0])
    highs = [e for e in data if e.get("impact") == "High"]
    print("high impact count:", len(highs))
    for e in highs[:8]:
        print("  HIGH:", e.get("country"), e.get("title"), e.get("date"), "f=", e.get("forecast"), "p=", e.get("previous"))

for url in (
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json?v=2",
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json?v=2",
):
    try:
        with httpx.Client(timeout=25.0, follow_redirects=True) as c:
            r = c.get(url, headers={"User-Agent": UA})
        print(url.split("/")[-1], r.status_code, len(r.content))
    except Exception as e:
        print(url, "FAILED", e)
