"""Probe FMP calendar (no key?), Finnhub (needs key), Trading Economics guest (blocked?), FRED v2, official pages."""
from __future__ import annotations

import httpx

UA = "NexusScalpEngine/1.0 (news intelligence)"

# 1. Federal Reserve official NEXT FOMC (has JSON? check)
# 2. BLS schedule via feeds.s schedule json?
# 3. EconCalendar from Nasdaq alternate path: https://www.nasdaq.com/market-activity/economic-calendar (page)
targets = {
    "nasdaq_econ_page": "https://www.nasdaq.com/market-activity/economic-calendar",
    "fomc_json": "https://www.federalreserve.gov/json/fomcmeetingcalendar.json",
    "fomc_json2": "https://www.federalreserve.gov/monetarypolicy/fomc_calendars.json",
    "fed_next": "https://www.federalreserve.gov/monetarypolicy/openmarket.htm",
    "bls_json": "https://www.bls.gov/schedule/news_release/2026_sched.pdf",
    "cpi_latest": "https://www.bls.gov/charts/consumer-price-index/consumer-price-index-by-category-line-chart.hjson",
}

for name, url in targets.items():
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as c:
            r = c.get(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
        print(name, r.status_code, len(r.content), r.headers.get("content-type", ""))
        if r.status_code == 200 and len(r.content) < 20000:
            print("   head:", r.text[:300].replace("\n", " "))
    except Exception as exc:
        print(name, "FAILED", type(exc).__name__, str(exc)[:80])
