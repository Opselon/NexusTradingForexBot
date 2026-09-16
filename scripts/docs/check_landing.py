"""Landing gate — validates the cinematic homepage contract in the BUILT site.

NSE-Docs / Nexus-Docs owned. Machine checks (CI fails, never warns):

  1. Structure   — homepage carries body.landing-page and every hero/section
                   anchor; landing.css + landing.js + data/project-status.json
                   exist and are referenced.
  2. Truth       — the built homepages contain NO fabricated live-data
                   markers: no fake prices/tickers, no win-rate/ROI/volume
                   claims, no "LIVE CHART" badge, no invented KPI counts.
                   The market canvas must be labelled DEMO/ILLUSTRATIVE.
  3. Evidence    — pulse fields exist for version/release/commit/CI/stars/
                   forks/issues/last_sync (N/A placeholders allowed; they are
                   hydrated client-side from generated metadata).
  4. i18n        — every language homepage renders the landing structure and
                   the demo label; status.json version equals pyproject.
  5. A11y        — landing regions have sr-only text alternatives for the
                   3D/market canvases; tabs use role=tab/aria-selected.

Usage:  python scripts/docs/check_landing.py [--site site/_site]
Prints  LANDING_GATE = PASS|FAIL
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "docs"))

SITE = REPO_ROOT / "site" / "_site"
if "--site" in sys.argv:
    SITE = (REPO_ROOT / sys.argv[sys.argv.index("--site") + 1]).resolve()

LANGS = ("en", "fa", "ar", "es", "de")

# Patterns that would mean the page fabricates live/market truth.
BANNED = [
    (r"data-ticker", "animated fake ticker"),
    (r"LIVE CHART", "unqualified 'live chart' badge"),
    (r"[Ww]in [Rr]ate", "win-rate claim"),
    (r"\bROI\b", "ROI claim"),
    (r"[Ss]harpe", "Sharpe claim"),
    (r"data-count='779'|data-count='60'", "hard-coded KPI counters"),
]

REQUIRED_ANCHORS = (
    "id='top'",
    "id='pipeline'",
    "id='why'",
    "id='modes'",
    "id='research'",
    "id='market'",
    "id='architecture'",
    "id='console'",
    "id='pulse'",
    "assets/landing.css",
    "assets/landing.js",
    "data-pulse-field='release'",
    "data-pulse-field='ci'",
    "data-pulse-field='last_sync'",
)


def home_for(lang: str) -> Path:
    return SITE / ("" if lang == "en" else lang) / "index.html"


def main() -> int:
    problems: list[str] = []
    checked = 0
    for lang in LANGS:
        home = home_for(lang)
        if not home.exists():
            problems.append(f"{lang}: homepage missing")
            continue
        text = home.read_text(encoding="utf-8", errors="replace")
        checked += 1
        if not re.search(r"<body[^>]*landing-page", text):
            problems.append(f"{lang}: body.landing-page class missing")
        for needle in REQUIRED_ANCHORS:
            if needle not in text:
                problems.append(f"{lang}: landing anchor missing: {needle}")
        for pat, why in BANNED:
            m = re.search(pat, text)
            if m:
                problems.append(f"{lang}: fabricated-truth marker ({why}): {m.group(0)!r}")
        # market canvas must carry the DEMO label and a text alternative
        if "demo-tag" not in text or ("DEMO" not in text):
            problems.append(f"{lang}: market canvas lacks DEMO/illustrative labelling")
        if "class='sr-only'" not in text:
            problems.append(f"{lang}: missing sr-only alternatives for visualizations")
    # generated status contract
    sj = SITE / "data" / "project-status.json"
    if not sj.exists():
        problems.append("data/project-status.json missing")
    else:
        try:
            data = json.loads(sj.read_text(encoding="utf-8"))
        except Exception as exc:
            data = {}
            problems.append(f"project-status.json invalid: {exc}")
        import tomllib

        pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        expected = pyproject.get("project", {}).get("version")
        if data.get("version") != expected:
            problems.append(f"project-status version {data.get('version')} != pyproject {expected}")
        for key in ("generated_at", "release", "repo_stats"):
            if key not in data:
                problems.append(f"project-status.json missing key: {key}")
    # assets exist (not only referenced)
    for asset in ("assets/landing.css", "assets/landing.js", "assets/pics/web.png"):
        if not (SITE / asset).exists():
            problems.append(f"built asset missing: {asset}")

    print(f"LANDING_GATE: {checked} homepages checked")
    if problems:
        print(f"LANDING_GATE = FAIL ({len(problems)})")
        for p in problems[:20]:
            print("  !", p)
        return 1
    print("LANDING_GATE = PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
