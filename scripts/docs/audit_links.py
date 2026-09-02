"""Full link audit for the BUILT site — every internal href/src on every page
must resolve to a real file. Used by the doctor and CI.

Usage:  python scripts/docs/audit_links.py [--site site/_site]
Prints  BUILT_LINK_AUDIT = PASS|FAIL
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SITE = REPO_ROOT / "site" / "_site"
if "--site" in sys.argv:
    SITE = REPO_ROOT / sys.argv[sys.argv.index("--site") + 1]


def main() -> int:
    if not SITE.exists():
        print(f"BUILT_LINK_AUDIT = FAIL (site missing: {SITE})")
        return 1
    pages = sorted(SITE.rglob("*.html"))
    total = 0
    bad: list[str] = []
    for page in pages:
        html_text = page.read_text(encoding="utf-8", errors="replace")
        from_dir = page.parent
        for m in re.finditer(r"""(?:href|src)='([^']+)'""", html_text):
            h = m.group(1)
            if h.startswith(("http://", "https://", "mailto:", "#")):
                continue
            total += 1
            resolved = (from_dir / h).resolve()
            ok = resolved.exists() or (resolved / "index.html").exists()
            if not ok:
                bad.append(f"{page.relative_to(SITE)}: {h}")
    print(f"checked {total} internal links across {len(pages)} pages")
    if bad:
        print(f"BUILT_LINK_AUDIT = FAIL ({len(bad)})")
        for b in bad[:40]:
            print("  !", b)
        return 1
    print("BUILT_LINK_AUDIT = PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
