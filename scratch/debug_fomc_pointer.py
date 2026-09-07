"""Simulate the FOMC panel pointer logic to find the 2026-11-* bug."""
import re
import sys

import httpx

sys.path.insert(0, "src")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 NexusScalp/1.0"
with httpx.Client(timeout=25.0, follow_redirects=True) as c:
    html = c.get(
        "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        headers={"User-Agent": UA},
    ).text

token_re = re.compile(
    r'<a id="(\d+)">(\d{4})(?:\s*FOMC Meetings)?\s*</a>'
    r"|fomc-meeting__month[^>]*><strong>([A-Za-z]+)</strong></div>\s*"
    r'<div class="fomc-meeting__date[^"]*">([\d\-–\s]+)</div>'
)
current_year = 2026
for m in token_re.finditer(html):
    if m.group(2):
        y = int(m.group(2))
        accepted = y >= current_year
        if accepted:
            current_year = y
        print("panel", y, "accepted" if accepted else "REJECTED->pointer stays", current_year)
    else:
        print(f"  meeting {current_year}: {m.group(3)} {m.group(4)}")
