"""Probe Treasury press-release page + BEA news page row structure for HTML adapters."""
from __future__ import annotations

import re

import httpx

UA = "NexusScalpEngine/1.0 (news intelligence)"


def get(url: str) -> str:
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        resp = client.get(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
        resp.raise_for_status()
        return resp.text


def main() -> None:
    print("=== Treasury press releases ===")
    html = get("https://home.treasury.gov/news/press-releases")
    rows = re.findall(r'<a href="(/news/press-releases/[^"]+)"[^>]*>([^<]{10,220})</a>', html)
    print("press links:", len(rows))
    for href, title in rows[:6]:
        print("  ", href[:70], "|", title.strip()[:70])
    dates = re.findall(r"(?:datetime=\"([^\"]+)\"|>([A-Z][a-z]+ \d{1,2}, \d{4})<)", html)
    print("date hits:", len(dates), dates[:6])

    print()
    print("=== BEA news page ===")
    html2 = get("https://www.bea.gov/news")
    rows2 = re.findall(r"<tr[^>]*>(.*?)</tr>", html2, re.S)
    print("tr rows:", len(rows2))
    for r in rows2[:8]:
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)]
        cells = [c for c in cells if c]
        if cells:
            print("  ", cells[:5])
    links = re.findall(r'href="(/news/[^"]+)"[^>]*>([^<]{8,120})', html2)
    print("bea news links:", len(links), links[:4])


if __name__ == "__main__":
    main()
