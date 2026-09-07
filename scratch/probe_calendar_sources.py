"""Probe calendar candidate sources: FOMC page structure, BEA schedule HTML, FRED release calendar."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import httpx

UA = "NexusScalpEngine/1.0 (news intelligence)"

FOMC = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
BEA = "https://www.bea.gov/news/schedule"
FRED = "https://fred.stlouisfed.org/release/calendar?rid=250"  # CPI
FRED_ALL = "https://fred.stlouisfed.org/release/calendar"


def get(url: str) -> str:
    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        resp = client.get(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
        resp.raise_for_status()
        return resp.text


def main() -> None:
    print("=== FOMC calendars page: look for structured date rows ===")
    html = get(FOMC)
    # FOMC pages embed panels per year with rows like "January 28-29" and policy statement links
    years = re.findall(r"fomcmeetingcalendar_(\d{4})", html)
    print("years found:", sorted(set(years))[:8])
    # meeting month blocks
    months = re.findall(r">(January|February|March|April|May|June|July|August|September|October|November|December)[^<]{0,20}(\d{1,2})[-–](\d{1,2})", html)
    print("meeting blocks sample:", months[:12])
    # statement times
    stmt = re.findall(r"(2:00\s*[pa]\.m\.|2:30\s*p\.m\.)", html)
    print("statement time mentions:", len(stmt), stmt[:5])
    # check for JSON-LD or structured data
    print("json-ld blocks:", html.count("application/ld+json"))

    print()
    print("=== BEA schedule page ===")
    html2 = get(BEA)
    # look for date rows
    rows = re.findall(r"(\d{4})-(\d{2})-(\d{2})", html2)
    print("ISO date hits:", len(rows), rows[:12])
    # BEA releases often listed as "September 3, 2026" with time "8:30 A.M."
    txt = re.sub(r"<[^>]+>", " ", html2)
    txt = re.sub(r"\s+", " ", txt)
    m = re.findall(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+2026[^.]{0,80}", txt)
    print("human date rows sample:", m[:8])

    print()
    print("=== FRED release calendar ===")
    try:
        html3 = get(FRED_ALL)
        # FRED renders calendar server-side in a table; check for date cells
        iso = re.findall(r"\d{4}-\d{2}-\d{2}", html3)
        print("FRED iso dates:", len(iso), iso[:10])
        titles = re.findall(r'data-release-id="(\d+)"[^>]*>', html3)
        print("release ids:", len(titles), titles[:10])
    except Exception as exc:
        print("FRED failed:", type(exc).__name__, exc)


if __name__ == "__main__":
    main()
