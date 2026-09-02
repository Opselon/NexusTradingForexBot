"""P0.6 localization coverage matrix — machine-readable, from build data.

Reports per language: total pages built, translated vs fallback pages,
locale-key completeness vs EN, and RTL status. Output: JSON to stdout and
a human table. Numbers come from actual build inputs — never hard-coded.

Usage: python scripts/docs/coverage_report.py [--json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "docs"))
import site_config as cfg  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "scripts" / "docs"))
import build_site as builder  # noqa: E402

DOCS = REPO_ROOT / "docs"
CONTENT = REPO_ROOT / "site" / "content"


def keys(d: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for k, v in d.items():
        if isinstance(v, dict):
            out |= keys(v, prefix + k + ".")
        else:
            out.add(prefix + k)
    return out


def main() -> int:
    en_pages = builder.md_pages_in(DOCS)
    ia_prefixes = tuple(f"{sec}/" for sec, _ in builder.NAV_SECTIONS)
    en_pages = [
        p
        for p in en_pages
        if p.name == "index.md" or p.relative_to(DOCS).as_posix().startswith(ia_prefixes)
    ]
    all_rels = [
        builder.strip_md_ext(p, DOCS) if builder.strip_md_ext(p, DOCS) != "index" else "docs-hub"
        for p in en_pages
    ]
    total_pages = len(all_rels)

    en_keys = keys(builder.LOCALES["en"])
    report: dict[str, dict] = {}

    for lang in cfg.LANGUAGES:
        translated = 0
        fallback = 0
        for rel in all_rels:
            if lang == "en":
                translated += 1
                continue
            if rel == "docs-hub":
                # hub uses EN source by design for every language
                translated += 1 if (CONTENT / lang / "index.md").exists() else 0
                fallback += 1
                continue
            if builder.find_translation(lang, rel) is not None:
                translated += 1
            else:
                fallback += 1
        missing_keys = sorted(en_keys - keys(builder.LOCALES[lang]))
        report[lang] = {
            "total_pages": total_pages,
            "translated_pages": translated,
            "fallback_pages": fallback,
            "ui_keys": len(en_keys),
            "missing_keys": missing_keys,
            "rtl": cfg.LANGUAGES[lang]["dir"] == "rtl",
        }

    print("LOCALIZATION COVERAGE MATRIX (from build data)")
    print("=" * 74)
    print(
        f"{'LANG':<5} {'PAGES':>6} {'TRANSLATED':>11} {'FALLBACK':>9} {'MISSING KEYS':>13} {'RTL':>5}"
    )
    for lang, r in report.items():
        print(
            f"{lang:<5} {r['total_pages']:>6} {r['translated_pages']:>11} "
            f"{r['fallback_pages']:>9} {len(r['missing_keys']):>13} {r['rtl']!s:>5}"
        )
    print("=" * 74)
    if "--json" in sys.argv:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    out = REPO_ROOT / "site" / "cache" / "coverage.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
