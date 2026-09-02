"""Live deployment smoke test — verifies the DEPLOYED GitHub Pages site.

Nexus-Docs owns this tool. The deployed site is the source of truth; a green
local build alone is not acceptance. Checks (all against the live URL):

  1. homepage + required homepage elements (hero, What's New, nav)
  2. stylesheet / JS / favicon resolve (relative base-path discipline)
  3. one page per language (fa/ar RTL markers, es/de lang attrs)
  4. search index fetchable + contains key terms
  5. sitemap fetchable, all locs fetchable (sampled)
  6. 404 page renders for an invalid URL
  7. version marker present and equal to pyproject version

Usage:
  python scripts/docs/live_smoke.py                       # uses site_config PAGES_URL
  python scripts/docs/live_smoke.py --url https://...     # explicit base

Prints DOCS_LIVE_SMOKE = PASS|FAIL and exits 0/1.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "docs"))
import site_config as cfg  # noqa: E402

UA = {"User-Agent": "nexus-docs-live-smoke", "Accept": "*/*"}
results: list[tuple[str, bool, str]] = []


def get(base: str, path: str) -> tuple[int, str]:
    url = base.rstrip("/") + "/" + path.lstrip("/")
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


def main() -> int:
    base = cfg.PAGES_URL
    argv = sys.argv[1:]
    if "--url" in argv:
        base = argv[argv.index("--url") + 1]
    base = base.rstrip("/") + "/"

    # --- 1. homepage + elements
    code, home = get(base, "")
    record("homepage 200", code == 200, f"HTTP {code}")
    for needle, label in (
        ("hero-kicker", "hero"),
        ("whats-new", "What's New"),
        ("nav-toggle", "mobile nav"),
        ("lang-picker", "language switcher"),
        ("assets/styles.css", "stylesheet link"),
        ("assets/search.js", "search script link"),
    ):
        record(f"homepage has {label}", needle in home)

    # --- 2. assets resolve relative to the SUBPATH (the base-path defect class)
    code, css = get(base, "assets/styles.css")
    record("styles.css 200", code == 200, f"HTTP {code}")
    record("styles.css non-trivial", code == 200 and len(css) > 4000, f"{len(css)} bytes")
    code, _js = get(base, "assets/search.js")
    record("search.js 200", code == 200, f"HTTP {code}")
    code, _ = get(base, "assets/favicon.svg")
    record("favicon 200", code == 200, f"HTTP {code}")
    # root-absolute reference must NOT appear in any served page (base-path bug)
    record(
        "no root-absolute asset refs", "href='/assets/" not in home and 'href="/assets/' not in home
    )

    # --- 3. languages
    for lang, marker in (
        ("fa", "dir='rtl'"),
        ("ar", "dir='rtl'"),
        ("es", "lang='es'"),
        ("de", "lang='de'"),
    ):
        code, page = get(base, f"{lang}/")
        record(f"{lang} landing 200", code == 200, f"HTTP {code}")
        if code == 200 and marker:
            record(f"{lang} carries marker", marker in page)
    code, fa_page = get(base, "fa/getting-started/quickstart/")
    record("fa quickstart 200", code == 200, f"HTTP {code}")
    if code == 200:
        record("fa quickstart RTL", "dir='rtl'" in fa_page)
        record("fa has Persian text", bool(re.search(r"[\u0600-\u06FF]", fa_page)))

    # --- 4. search index
    code, idx_raw = get(base, "search-index.json")
    record("search-index 200", code == 200, f"HTTP {code}")
    entries = []
    if code == 200:
        try:
            entries = json.loads(idx_raw)
        except Exception:
            pass
    record("search index non-empty", len(entries) >= 40, f"{len(entries)} entries")
    hay = " ".join((e.get("t", "") + " " + e.get("x", "")).lower() for e in entries)
    for term in ("70d", "replay", "roadmap", "no_trade", "architecture"):
        record(f"search index has '{term}'", term in hay)

    # --- 5. releases + version marker
    code, _rel = get(base, "releases/")
    record("releases page 200", code == 200, f"HTTP {code}")
    expected_version = cfg.repo_version()
    record(
        f"homepage version v{expected_version}",
        f"v{expected_version}" in home,
        "version marker missing/stale",
    )

    # --- 6. sitemap + sample of its URLs
    code, sm = get(base, "sitemap.xml")
    record("sitemap 200", code == 200, f"HTTP {code}")
    locs = re.findall(r"<loc>([^<]+)</loc>", sm)
    record("sitemap non-trivial", len(locs) >= 40, f"{len(locs)} urls")
    ok_urls = 0
    for loc in locs[:15]:
        c, _ = get(loc, "")
        ok_urls += 1 if c == 200 else 0
    record("sampled sitemap urls 200", ok_urls == min(15, len(locs)), f"{ok_urls}/15")

    # --- 7. 404 behavior
    code, _nf = get(base, "this-page-does-not-exist-xyz/")
    record("invalid URL returns 404", code == 404, f"HTTP {code}")

    # ------------------------------ verdict ------------------------------
    print("=" * 60)
    print("DOCS LIVE SMOKE —", base)
    print("=" * 60)
    failures = []
    for name, ok, detail in results:
        mark = "✓" if ok else "✗"
        print(f"{mark} {name:<38} {detail if not ok else ''}")
        if not ok:
            failures.append(name)
    print("=" * 60)
    if failures:
        print(f"DOCS_LIVE_SMOKE = FAIL ({len(failures)})")
        for f in failures:
            print("  !", f)
        return 1
    print("DOCS_LIVE_SMOKE = PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
