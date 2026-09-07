"""Test the trading-economics style free JSON on tradingeconomics.com + check weekly window of FF next week endpoint name."""
from __future__ import annotations

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"

targets = {
    "te_cal_json": "https://api.tradingeconomics.com/calendar?c=guest:guest",
    "ff_thisweek_v3": "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
}

for name, url in targets.items():
    try:
        with httpx.Client(timeout=25.0, follow_redirects=True) as c:
            r = c.get(url, headers={"User-Agent": UA})
        print(name, r.status_code, len(r.content))
        if r.status_code == 200 and len(r.content) > 200:
            print("  head:", r.text[:400].replace("\n", " "))
    except Exception as e:
        print(name, "FAILED", type(e).__name__, str(e)[:80])
