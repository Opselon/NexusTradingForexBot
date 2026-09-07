"""Inspect the raw rows content of nasdaq econ page (maybe thead/td without text or JS-rendered)."""
from __future__ import annotations

import re

import httpx

UA = "NexusScalpEngine/1.0 (news intelligence)"

with httpx.Client(timeout=25.0, follow_redirects=True) as c:
    r = c.get("https://www.nasdaq.com/market-activity/economic-calendar", headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
html = r.text
rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
print("rows:", len(rows))
for row in rows[:6]:
    print("RAW:", row[:400].replace("\n", " "))
    print("---")
# find 'economy' data markers
idx = html.find("Tentative")
print("Tentative idx:", idx)
m2 = re.findall(r'(\d{1,2}:\d{2}\s*[AP]M)', html)
print("times:", m2[:8])
