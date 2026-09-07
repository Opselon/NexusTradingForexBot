"""Probe ForexFactory weekly calendar JSON (nfs.faireconomy.media) — the classic OSS calendar endpoint."""
from __future__ import annotations

import json

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"

targets = {
    "ff_thisweek_json": "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "ff_nextweek_json": "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
    "ff_thisweek_xml": "https://nfs.faireconomy.media/ff_calendar_thisweek.xml",
}

for name, url in targets.items():
    try:
        with httpx.Client(timeout=25.0, follow_redirects=True) as c:
            r = c.get(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
        print(name, r.status_code, len(r.content), r.headers.get("content-type", ""))
        if r.status_code == 200 and "json" in (r.headers.get("content-type") or ""):
            data = r.json()
            print("  events:", len(data))
            for ev in data[:6]:
                print("  ", {k: ev.get(k) for k in ("title", "country", "date", "impact", "forecast", "previous")})
    except Exception as exc:
        print(name, "FAILED", type(exc).__name__, str(exc)[:90])
