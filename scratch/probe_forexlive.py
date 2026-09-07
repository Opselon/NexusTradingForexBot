"""Final round: probe FF alternative mirrors + Fed JSON on rate decisions; check forexlive freshness."""
from __future__ import annotations

import httpx
import feedparser

UA = "NexusScalpEngine/1.0 (news intelligence)"

# forexlive feed freshness
with httpx.Client(timeout=20.0, follow_redirects=True) as c:
    fx = c.get("https://www.forexlive.com/feed/", headers={"User-Agent": UA}).content
p = feedparser.parse(fx)
print("forexlive entries:", len(p.entries))
if p.entries:
    e = p.entries[0]
    print("  newest:", e.title[:60], "|", getattr(e, "published", "")[:40])
