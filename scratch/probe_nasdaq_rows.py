"""Dump nasdaq econ calendar table rows (62 tr rows found)."""
from __future__ import annotations

import re

import httpx

UA = "NexusScalpEngine/1.0 (news intelligence)"

with httpx.Client(timeout=25.0, follow_redirects=True) as c:
    r = c.get("https://www.nasdaq.com/market-activity/economic-calendar", headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
html = r.text
rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
for row in rows[:12]:
    cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
    cells = [c for c in cells if c]
    if cells:
        print("  ", cells[:8])
