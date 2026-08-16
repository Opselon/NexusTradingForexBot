"""Check which verifier patterns fire on the redacted telegram line."""

from __future__ import annotations

import re

LINE = '  bot_token: "7233738325:***"   # token placeholder'

patterns = [
    re.compile(r"(?i)api[_-]?key\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)bot[_-]?token\s*[=:]\s*['\"]?\d{6,}:[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?i)password\s*[=:]\s*['\"]?(?!changeme)(?!password)[^\s'\"]{6,}"),
]
for i, p in enumerate(patterns):
    m = p.search(LINE)
    print(f"pat{i} match:", m.group(0) if m else None)
