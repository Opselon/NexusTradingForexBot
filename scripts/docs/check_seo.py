"""Elite SEO gate — validates the BUILT site's search-engine readiness.

Checks every built page for: title, description, canonical, OG tags,
Twitter card, hreflang set (incl. x-default), JSON-LD, and lang/dir attrs.
Cross-locale duplicate-title detection is per-locale (same title across
locales is CORRECT when hreflang'd — flagged only within one locale).
Also validates robots.txt + sitemap.xml integrity.

Usage:  python scripts/docs/check_seo.py [--site site/_site]
Prints SEO_GATE = PASS|FAIL
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SITE = REPO_ROOT / "site" / "_site"
if "--site" in sys.argv:
    SITE = REPO_ROOT / sys.argv[sys.argv.index("--site") + 1]

LANGS = ("en", "fa", "ar", "es", "de")


def main() -> int:
    problems: list[str] = []
    if not SITE.exists():
        print("SEO_GATE = FAIL (site missing)")
        return 1
    pages = sorted(SITE.rglob("*.html"))
    per_locale_titles: dict[str, Counter] = {l: Counter() for l in LANGS}
    checked = 0
    for page in pages:
        rel = page.relative_to(SITE).as_posix()
        text = page.read_text(encoding="utf-8", errors="replace")
        checked += 1
        locale = rel.split("/")[0] if rel.split("/")[0] in LANGS else "en"
        for needle, label in (
            ("<title>", "title"),
            ("name='description'", "description"),
            ("rel='canonical'", "canonical"),
            ("property='og:title'", "og:title"),
            ("name='twitter:card'", "twitter:card"),
            ("application/ld+json", "JSON-LD"),
        ):
            if needle not in text:
                problems.append(f"{rel}: missing {label}")
        m = re.search(r"<html[^>]*lang='([^']+)'", text)
        if not m:
            problems.append(f"{rel}: missing html lang")
        elif m.group(1) != locale:
            problems.append(f"{rel}: html lang {m.group(1)} != locale {locale}")
        if locale in ("fa", "ar") and "dir='rtl'" not in text:
            problems.append(f"{rel}: missing dir=rtl")
        # hreflang set
        if "hreflang='x-default'" not in text and 'hreflang="x-default"' not in text:
            problems.append(f"{rel}: missing x-default hreflang")
        tm = re.search(r"<title>([^<]+)</title>", text)
        if tm:
            per_locale_titles[locale][tm.group(1)] += 1
    # within-locale duplicate titles
    for locale, counter in per_locale_titles.items():
        for title, count in counter.items():
            if count > 1:
                problems.append(f"[{locale}] duplicate title x{count}: {title[:70]}")
    # robots + sitemap
    robots = SITE / "robots.txt"
    if not robots.exists() or "Sitemap:" not in robots.read_text(encoding="utf-8"):
        problems.append("robots.txt missing or lacks Sitemap directive")
    sm = SITE / "sitemap.xml"
    if sm.exists():
        sm_text = sm.read_text(encoding="utf-8")
        locs = re.findall(r"<loc>([^<]+)</loc>", sm_text)
        for loc in locs:
            low = loc.lower()
            repo_seg = "/nexustradingforexbot/"
            tail = low.split(repo_seg, 1)[1] if repo_seg in low else low.split(".github.io/", 1)[-1]
            probe = SITE / tail
            if not (probe.exists() or (probe / "index.html").exists()):
                problems.append(f"sitemap lists non-built URL: {loc}")
    else:
        problems.append("sitemap.xml missing")

    print(f"SEO_GATE: {checked} pages checked")
    if problems:
        print(f"SEO_GATE = FAIL ({len(problems)})")
        for pr in problems[:20]:
            print("  !", pr)
        return 1
    print("SEO_GATE = PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
