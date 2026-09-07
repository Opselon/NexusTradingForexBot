"""Find working nasdaq economiccalendar path variants."""
from __future__ import annotations

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"

variants = {
    "econcalendar": "https://api.nasdaq.com/api/calendar/economiccalendar?date=2026-09-07&limit=50&offset=0",
    "econ_underscore": "https://api.nasdaq.com/api/calendar/economic_calendar?date=2026-09-07",
    "economicCalendar": "https://api.nasdaq.com/api/calendar/economicCalendar?date=2026-09-07",
    "tradingecon": "https://api.nasdaq.com/api/quote/NDAQ/info?assetclass=stocks",
}

for name, url in variants.items():
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as c:
            r = c.get(url, headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9"})
        print(name, r.status_code, len(r.content))
        if r.status_code == 200 and len(r.content) > 500:
            print("   head:", r.text[:400].replace("\n", " "))
    except Exception as exc:
        print(name, "FAILED", type(exc).__name__, str(exc)[:80])
