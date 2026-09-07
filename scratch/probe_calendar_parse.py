"""Extract FOMC 2026/2027 meeting dates + statement release times from the official page, and BEA schedule rows."""
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
    # each meeting: <div class="row fomc-meeting"> <div month><strong>January</strong></div> <div date>27-28</div> ... Statement: ...
    meetings = re.findall(
        r'fomc-meeting__month[^>]*><strong>(\w+)</strong></div>\s*<div class="fomc-meeting__date[^"]*">([\d\-–\s]+)</div>(.*?)(?=fomc-meeting__month|</div>\s*</div>\s*</div>)',
        html,
        re.S,
    )
    print("meetings parsed:", len(meetings))
    for month, days, tail in meetings[:20]:
        stmt = re.search(r"Statement[^<]*</strong>", tail)
        conf = re.search(r"press conference", tail, re.I)
        print(f"  {month:10s} {days.strip():8s} statement={'Y' if stmt else 'N'} conf={'Y' if conf else 'N'}")
    # find 2:00 PM statement times
    times = re.findall(r"(\d{1,2}:\d{2}\s*[ap]\.m\.)", html)
    from collections import Counter

    print("time mentions:", Counter(times).most_common(6))

    print("=" * 60)
    html2 = get("https://www.bea.gov/news/schedule")
    # BEA rows: find table rows with date + release name
    text = re.sub(r"<script.*?</script>", " ", html2, flags=re.S)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S)
    print("BEA tr rows:", len(rows))
    for r in rows[:14]:
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)]
        cells = [re.sub(r"\s+", " ", c) for c in cells if c]
        if cells:
            print("  ", cells[:6])


if __name__ == "__main__":
    main()
