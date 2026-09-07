"""Deeper look at FOMC + BEA calendar HTML: what the fetched pages actually contain."""
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
    html = get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")
    print("FOMC len:", len(html))
    # print year headings
    for pat in (r"2026", r"2027", r"January"):
        hits = [m.start() for m in re.finditer(pat, html)][:5]
        print(f"pat {pat!r} hits:", len([m for m in re.finditer(pat, html)]))
    # dump a window around '2026'
    idx = html.find("2026")
    print(html[idx - 200 : idx + 800] if idx != -1 else "no 2026")
    print("=" * 60)
    # look for the meeting list markup
    m = re.search(r"(FOMC.{0,200}?Meeting|Meeting calendars|Committee meetings)", html, re.S)
    print("meeting-phrase:", bool(m), m.group(0)[:200] if m else "")
    # find all "<time" or datetime= attrs
    print("time tags:", len(re.findall(r"<time", html)))
    print("datetime attrs:", re.findall(r'datetime="([^"]+)"', html)[:10])

    print("=" * 60)
    html2 = get("https://www.bea.gov/news/schedule")
    print("BEA len:", len(html2))
    rows = re.findall(r'datetime="([^"]+)"', html2)
    print("BEA datetime attrs:", len(rows), rows[:10])
    tbl = html2.lower().count("<table")
    print("BEA tables:", tbl)
    # find release rows: typically "Gross Domestic Product" + date
    gdp = [m.start() for m in re.finditer("Gross Domestic Product", html2)][:3]
    for g in gdp:
        print("---window---")
        print(re.sub(r"\s+", " ", html2[g - 400 : g + 200])[:600])


if __name__ == "__main__":
    main()
