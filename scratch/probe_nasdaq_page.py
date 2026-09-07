"""Check nasdaq economic-calendar page for embedded JSON data (NextJS/Redux state)."""
from __future__ import annotations

import json
import re

import httpx

UA = "NexusScalpEngine/1.0 (news intelligence)"

with httpx.Client(timeout=25.0, follow_redirects=True) as c:
    r = c.get("https://www.nasdaq.com/market-activity/economic-calendar", headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
html = r.text
print("page len:", len(html))

# look for redux state
m = re.search(r"window\.reduxState\s*=\s*(\{.*?\});\s*</script>", html, re.S)
print("redux found:", bool(m))

# look for JSON with economic calendar fields
for pat in ("economicCalendar", "EconomicCalendar", "eventTime", "impact"):
    hits = len(re.findall(pat, html))
    print(f"{pat}: {hits}")

# try to find any embedded JSON blob with 'events'
blobs = re.findall(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
print("next_data blobs:", len(blobs))
if blobs:
    try:
        data = json.loads(blobs[0])
        print("next_data keys:", list(data.keys())[:6])
    except Exception as e:
        print("next_data parse failed", e)

# check table markup
rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
print("tr rows:", len(rows))
for row in rows[1:6]:
    cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
    cells = [c for c in cells if c]
    if cells:
        print("  ", cells[:8])
