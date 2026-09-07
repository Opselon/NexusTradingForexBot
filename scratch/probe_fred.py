"""Retry FRED release calendar with generous timeout; also check the JSON-ish endpoints."""
from __future__ import annotations

import re

import httpx

UA = "NexusScalpEngine/1.0 (news intelligence)"

for url in (
    "https://fred.stlouisfed.org/release/calendar",
    "https://fred.stlouisfed.org/releases/calendar",
):
    try:
        with httpx.Client(timeout=45.0, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
            print(url, "->", resp.status_code, "bytes:", len(resp.content))
            if resp.status_code == 200:
                html = resp.text
                iso = re.findall(r"\d{4}-\d{2}-\d{2}", html)
                print("  iso dates:", len(iso), iso[:8])
                rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
                print("  tr rows:", len(rows))
                # look for CPI / payrolls rows
                for kw in ("Consumer Price Index", "Employment Situation", "Nonfarm"):
                    hit = [m.start() for m in re.finditer(kw, html)][:2]
                    print(f"  {kw!r} hits:", len(hit))
                    for h in hit[:1]:
                        seg = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " | ", html[h - 300 : h + 150]))
                        print("    ", seg[:400])
                break
    except Exception as exc:
        print(url, "FAILED:", type(exc).__name__, exc)
