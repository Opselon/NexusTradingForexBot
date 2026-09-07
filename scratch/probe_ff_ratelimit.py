"""FF calendar rate-limit behavior + field stability (re-request after a pause; note 429 Retry-After)."""
from __future__ import annotations

import time

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"

with httpx.Client(timeout=25.0, follow_redirects=True) as c:
    r = c.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", headers={"User-Agent": UA})
    print("status:", r.status_code, "retry-after:", r.headers.get("retry-after"), "date:", r.headers.get("date"))
    if r.status_code == 429:
        print("head:", r.text[:200])
    time.sleep(3)
    r2 = c.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", headers={"User-Agent": UA})
    print("2nd call:", r2.status_code, len(r2.content))
