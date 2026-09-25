"""Translation audit — per-language coverage from ACTUAL inspection.

Nexus-Docs owns this tool. It reports, with numbers (never hand-written):

  coverage %   per-language: weighted by translated vs fallback sections
  missing      pages a language lacks entirely (site falls back to English)
  partial      pages whose front-matter says translation-status != complete
  stale        pages whose English source changed after source-revision date
  broken       front-matter problems (bad lang, missing status)
  terminology  glossary rows missing a language column (site/terminology/terms.csv)

Usage:  python scripts/docs/check_translations.py
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "docs"))
import site_config as cfg  # noqa: E402

CONTENT = REPO_ROOT / "site" / "content"
TERMS = REPO_ROOT / "site" / "terminology" / "terms.csv"

CORE_PAGES = [
    "start",
    "status",
    "architecture",
    "research",
    "validation",
    "roadmap",
    "reference",
    "contributing",
]


def fm_field(text: str, field: str) -> str | None:
    m = re.search(rf"^{field}:\s*(.+)$", text, re.M)
    return m.group(1).strip().strip("'\"") if m else None


def section_count(text: str) -> int:
    body = re.sub(r"^---.*?---\s*", "", text, flags=re.S)
    # count sections as h2 + paragraphs (a rough but consistent unit)
    h2 = len(re.findall(r"^##\s+", body, re.M))
    paras = len(
        [
            p
            for p in body.split("\n\n")
            if p.strip() and not p.strip().startswith(("#", "```", "|", "<"))
        ]
    )
    return h2 * 3 + paras  # sections weigh more than paragraphs


def main() -> int:
    en_dir = CONTENT / "en"
    en_pages = {p.stem: p for p in en_dir.glob("*.md")}
    en_sizes = {pid: section_count(p.read_text(encoding="utf-8")) for pid, p in en_pages.items()}

    report: list[str] = []
    problems: list[str] = []
    coverage: dict[str, float] = {}

    report.append("DOCUMENTATION TRANSLATION AUDIT")
    report.append("=" * 46)

    for lang, meta in cfg.LANGUAGES.items():
        if lang == cfg.SOURCE_LANG:
            coverage[lang] = 100.0
            continue
        ldir = CONTENT / lang
        translated_weight = 0
        total_weight = 0
        missing: list[str] = []
        partial: list[str] = []
        stale: list[str] = []
        broken: list[str] = []
        if not ldir.exists():
            coverage[lang] = 0.0
            problems.append(f"{lang}: entire language tree missing")
            continue
        for pid, en_path in en_pages.items():
            weight = max(en_sizes.get(pid, 1), 1)
            total_weight += weight
            lp = ldir / f"{pid}.md"
            if not lp.exists():
                missing.append(pid)
                continue
            text = lp.read_text(encoding="utf-8")
            status = fm_field(text, "translation-status")
            if fm_field(text, "lang") != lang:
                broken.append(f"{pid}: bad lang field")
            if status not in {"complete", "partial", "stale"}:
                broken.append(f"{pid}: bad translation-status {status!r}")
                continue
            if status == "complete":
                translated_weight += weight
                # staleness: source-revision date vs file mtime of the EN source
                rev = fm_field(text, "source-revision") or ""
                m = re.search(r"@(\d{4}-\d{2}-\d{2})", rev)
                if m:
                    try:
                        rev_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
                        import datetime as _dt

                        rev_ts = _dt.datetime.combine(
                            rev_date, _dt.time(0, 0), tzinfo=_dt.UTC
                        ).timestamp()
                        if en_path.stat().st_mtime > rev_ts + 86400 * 2:
                            stale.append(pid)
                    except ValueError:
                        pass
                else:
                    stale.append(f"{pid} (no source-revision)")
            else:
                partial.append(pid)
        coverage[lang] = round(100.0 * translated_weight / max(total_weight, 1), 1)
        report.append(
            f"{lang} ({meta['name']}, {meta['dir']}): coverage {coverage[lang]:>5}%"
            + (f"  missing={missing}" if missing else "")
            + (f"  partial={partial}" if partial else "")
            + (f"  stale={stale}" if stale else "")
            + (f"  BROKEN={broken}" if broken else "")
        )
        problems.extend(f"{lang}: {b}" for b in broken)

    # terminology table integrity (semicolon-separated, NOT comma CSV — terms
    # and translations may contain commas; the ; separator is the contract)
    if TERMS.exists():
        lines = [
            ln
            for ln in TERMS.read_text(encoding="utf-8").splitlines()
            if ln.strip()
            and not ln.strip().startswith("#")
            and not ln.strip().upper().startswith("DOC-")
        ]
        rows = [ln.split(";") for ln in lines]
        expected_cols = 5  # term;fa;es;ar;de
        short = [r[0] for r in rows if len(r) < expected_cols]
        if short:
            problems.append(f"terminology rows missing language columns: {short}")
        report.append(f"terminology: {len(rows)} terms across {expected_cols} language columns")
    else:
        problems.append("terminology table missing: site/terminology/terms.csv")

    report.append("=" * 46)
    print("\n".join(report))
    if problems:
        print(f"\nTRANSLATION AUDIT = FAIL ({len(problems)})")
        for p in problems:
            print(f"  ! {p}")
        return 1
    print("\nTRANSLATION AUDIT = PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
