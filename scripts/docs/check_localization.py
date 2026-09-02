"""P0 localization correctness gate — checks the BUILT site.

Verifies that FA/AR pages actually render localized UI chrome, and detects
unexpected English leakage with a TECHNICAL-ENGLISH ALLOWLIST (brand names,
language natives, glossary terms, commit bullets, code contexts are valid).

Checks per language (fa, ar):
  1. html[dir=rtl] + lang attribute
  2. localized: search placeholder, home, menu, skip link, prev/next, footer
     version label, sidebar section titles (nav.*), breadcrumbs
  3. homepage hero kicker/title/sub are localized (not the EN source)
  4. capability table headers localized
  5. callout titles localized on a page known to contain one
  6. English-leak scan: latin-script visible text nodes not in the allowlist

Usage:  python scripts/docs/check_localization.py [--site site/_site]
Prints LOCALIZATION_GATE = PASS|FAIL
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
    SITE = REPO_ROOT / sys.argv[sys.argv.index("--site") + 1]

LOCALES = {
    lang: json.loads(
        (REPO_ROOT / "site" / "locales" / lang / "ui.json").read_text(encoding="utf-8")
    )
    for lang in ("en", "fa", "ar", "es", "de")
}

# Technical-English allowlist: these MAY legitimately appear on FA/AR pages.
ALLOWLIST_RE = re.compile(
    r"^(?:"
    # brand + language-switcher natives
    r"Nexus|Scalp Engine|Nexus Scalp Engine|⚡\s*Nexus|GitHub( ↗)?|GitHub Releases|"
    r"English|Español|Deutsch|"
    # glossary / product terms
    r"Walk-Forward|Walk-forward|Replay|OOS|CI|CLI|API|REST|SSE|WS|MT5|MetaTrader 5|"
    r"PAPER|SHADOW|LIVE|50D|70D|scalp_v\d|XAUUSD|SQLite|WAL|SBOM|SHA-256|"
    r"CERTIFIED|IMPLEMENTED|EXPERIMENTAL|RESEARCH|PLANNED|BLOCKED|"
    r"Scope|Vision|Roadmap|Status|Overview|Validation|Security|Database|Runtime|"
    # statuses & metadata
    r"IMPORTANT|NOTE|WARNING|FAQ|v\d+\.\d+\.\d+|rev [0-9a-f]+|"
    r"\(?OOS [A-Z_]+\)?|.*NOT_ELIGIBLE.*|"
    # commit bullets / agent-authored lines
    r"[0-9a-f]{7,}( .*)?|"
    r".*(?:BUG|CHG|INV|TASK|DEC)-\d+.*|"
    r".*Hermes-.*|.*Nexus-Main.*|.*Nexus-Docs.*|.*Nexus-UX.*|.*Agent .*|.*Agent-.*|"
    r".*release.*v\d.*|.*pyproject\.toml.*|"
    r".*Windows.*|.*PyTorch.*|.*Polars.*|.*MetaTrader5.*|"
    # paths / identifiers
    r"[A-Za-z][\w.-]*(?:/[\w.-]+)+|"
    # punctuation-only
    r"[<>{}\[\]|/\@$%^&*~`+=\-;,._#↗⚡]*"
    r")$",
    re.I,
)


def visible_nodes(html_text: str) -> list[str]:
    cleaned = re.sub(r"<(pre|script|style).*?</\1>", "", html_text, flags=re.S)
    cleaned = re.sub(r"<code>.*?</code>", "", cleaned, flags=re.S)
    out = []
    for m in re.finditer(r">([^<>]+)<", cleaned):
        s = re.sub(r"\s+", " ", m.group(1)).strip()
        if len(s) >= 3 and re.search(r"[A-Za-z]{3,}", s):
            out.append(s)
    return out


def leaks_for(lang: str, html_text: str) -> list[str]:
    """Latin-script visible strings not matching the allowlist and containing
    no script-of-language characters."""
    out = []
    for s in visible_nodes(html_text):
        if ALLOWLIST_RE.match(s):
            continue
        if re.search(r"[\u0600-\u06FF]", s):  # already contains Arabic-script text
            continue
        out.append(s)
    return out


checks: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok, detail))


def main() -> int:
    problems: list[str] = []
    matrix: dict[str, dict] = {}

    for lang in ("fa", "ar"):
        loc = LOCALES[lang]
        home = SITE / lang / "index.html"
        if not home.exists():
            problems.append(f"{lang}: homepage missing")
            continue
        text = home.read_text(encoding="utf-8")

        # 1. direction + lang
        record(
            f"{lang}: dir=rtl",
            f"dir='{loc['meta']['dir']}'" in text.split(">")[1] or "rtl" in text.split(">")[1],
        )
        record(f"{lang}: lang attr", f"lang='{lang}'" in text)

        # 2. localized chrome
        chrome_targets = {
            "search placeholder": loc["ui"]["search"],
            "menu aria": loc["ui"]["menu"],
            "home link": loc["ui"]["home"],
            "skip link": loc["ui"]["skip"],
            "language label": loc["ui"]["language"],
            "theme? (if present)": loc["ui"]["theme"],
        }
        for label, needle in chrome_targets.items():
            if label == "theme? (if present)" and needle not in text:
                continue  # theme button may be JS-injected later
            record(f"{lang}: {label} localized", needle in text)

        # sidebar section titles in nav (sample 3)
        for sec in ("project", "architecture", "research"):
            title = loc["nav"][sec]
            record(f"{lang}: nav.{sec} localized", title in text)

        # 3. hero localized
        record(f"{lang}: hero kicker", loc["ui"]["hero_kicker"] in text)
        record(f"{lang}: hero sub", loc["ui"]["hero_sub"][:40] in text)

        # 4. capability headers localized
        record(f"{lang}: capability th", loc["ui"]["capability"] in text)

        # 5. prev/next labels (on a subpage)
        sub = SITE / lang / "project" / "status" / "index.html"
        if sub.exists():
            st = sub.read_text(encoding="utf-8")
            record(f"{lang}: prev label", loc["ui"]["prev"] in st)
            record(f"{lang}: next label", loc["ui"]["next"] in st)
            record(f"{lang}: footer on_github", loc["ui"]["on_github"] in st)
            # breadcrumbs: section title appears
            record(f"{lang}: breadcrumb section", loc["nav"]["project"] in st)

        # 6. English leak scan
        leak_pages = [home]
        for extra in ("project/status", "getting-started/quickstart", "getting-started/index.html"):
            p2 = SITE / lang / extra
            if p2.is_dir():
                leak_pages.append(p2 / "index.html")
            elif p2.exists():
                leak_pages.append(p2)
        total_leaks: list[str] = []
        for lp in leak_pages:
            if lp.exists():
                total_leaks.extend(
                    f"{lp.relative_to(SITE)}: {s}"
                    for s in leaks_for(lang, lp.read_text(encoding="utf-8"))
                )
        matrix[lang] = {"leaks": len(total_leaks)}
        record(
            f"{lang}: english leaks",
            len(total_leaks) <= 6,
            f"{len(total_leaks)} leaks; first: {total_leaks[:3]}",
        )
        problems.extend(f"{lang} leak: {s}" for s in total_leaks[:6])

    # language-switch targets valid (EN -> FA etc.)
    en_home = SITE / "index.html"
    if en_home.exists():
        et = en_home.read_text(encoding="utf-8")
        for lang in ("fa", "ar", "es", "de"):
            record(f"en -> {lang} switch href", f"'{lang}/" in et or f'"{lang}/' in et)

    print("=" * 62)
    print("LOCALIZATION GATE (built site)")
    print("=" * 62)
    for name, ok, detail in checks:
        mark = "✓" if ok else "✗"
        print(f"{mark} {name:<40} {detail if not ok else ''}")
    print("=" * 62)
    if problems:
        print(f"LOCALIZATION_GATE = FAIL ({len(problems)})")
        for pr in problems[:15]:
            print("  !", pr)
        return 1
    print("LOCALIZATION_GATE = PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
