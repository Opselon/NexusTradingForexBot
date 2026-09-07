"""Probe nasdaq calendar API variants (this is the classic JSON endpoint used by many OSS calendars)."""
from __future__ import annotations

import httpx

UA = "NexusScalpEngine/1.0 (news intelligence)"

variants = {
    "nasdaq_econ_v1": "https://api.nasdaq.com/api/calendar/economiccalendar?date=2026-09-07&limit=50",
    "nasdaq_econ_v2": "https://api.nasdaq.com/api/calendar/economiccalendar/",
    "nasdaq_header_test": "https://api.nasdaq.com/api/calendar/earnings?date=2026-09-07",
}

for name, url in variants.items():
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        with httpx.Client(timeout=20.0, follow_redirects=True) as c:
            r = c.get(url, headers=headers)
        print(name, r.status_code, len(r.content), r.headers.get("content-type", ""))
        if r.status_code == 200:
            print("   head:", r.text[:300].replace("\n", " "))
    except Exception as exc:
        print(name, "FAILED", type(exc).__name__, str(exc)[:80])
