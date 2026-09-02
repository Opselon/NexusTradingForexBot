"""Documentation doctor — the documentation quality gate for Nexus Scalp Engine.

Nexus-Docs owns this tool. It validates the documentation surface offline:

  1. README         — exists, non-trivial, links resolve, no version drift
  2. Links          — all relative markdown links + anchors across docs/ + README + site/content
  3. Anchors        — heading anchors referenced by #fragment links exist
  4. Nav integrity  — every site/content page referenced by site_config NAV exists
  5. Translations   — site/content/<lang> tree parity + front-matter status fields
  6. RTL            — rtl languages declare dir in site_config and have [dir=rtl] CSS in the built site
  7. Mermaid        — ```mermaid fences parse at a structural level (header + body present)
  8. Build          — site build runs and emits index.html + search.json
  9. Assets         — images referenced by README/docs exist on disk
 10. Secrets        — secret-shaped strings absent from docs/site/scripts/docs
 11. Drift          — version strings in docs match pyproject.toml (or are absent)

Exit code 0 = DOCS_HEALTH PASS; 1 = FAIL (actionable diagnostics printed).
Usage:  python scripts/docs/check_docs.py [--quiet]
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import urllib.parse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "docs"))
import site_config as cfg  # noqa: E402

DOCS = REPO_ROOT / "docs"
SITE_CONTENT = REPO_ROOT / "site" / "content"

CHECKS: list[tuple[str, bool, list[str]]] = []
failures: list[str] = []


def record(name: str, ok: bool, details: list[str]) -> None:
    CHECKS.append((name, ok, details))
    if not ok:
        failures.extend(f"  [{name}] {d}" for d in details)


def iter_markdown(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        elif root.exists():
            files.extend(sorted(root.rglob("*.md")))
    return files


# ---------------------------------------------------------------- 1. README
def check_readme() -> None:
    readme = REPO_ROOT / "README.md"
    problems: list[str] = []
    if not readme.exists():
        record("README", False, ["README.md missing"])
        return
    text = readme.read_text(encoding="utf-8")
    if len(text) < 4000:
        problems.append("README suspiciously short (<4k chars) for a landing page")
    for required in ("Quickstart", "License", "Documentation"):
        if required.lower() not in text.lower():
            problems.append(f"README missing required section keyword: {required}")
    # version drift: README must not hard-code a version number that can rot
    hard_versions = re.findall(r"\bv?9\.0\.\d+\b", text)
    if hard_versions:
        problems.append(
            f"README hard-codes version strings (single source = pyproject.toml): {sorted(set(hard_versions))}"
        )
    record("README", not problems, problems or ["ok"])


# ------------------------------------------------------- link/anchor model
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.M)
MD_LINK_RE = re.compile(r"(?<!\!)\[[^\]]+\]\(([^)]+)\)")
IMG_LINK_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def slugify(heading: str) -> str:
    """GitHub-compatible slug: lowercase, spaces -> dashes; keep letters
    (incl. unicode), digits, '-', '_' and combining/variation marks (FE0F is
    category Mn, which GitHub preserves); drop other punctuation/symbols."""
    import unicodedata

    s = heading.strip().lower()
    out = []
    for ch in s:
        cat = unicodedata.category(ch)
        if ch == " ":
            out.append("-")
        elif ch in ("-", "_") or cat.startswith("L") or cat.startswith("N") or cat in ("Mn", "Mc"):
            out.append(ch)
    return "".join(out)


def anchors_of(text: str) -> set[str]:
    out = set()
    for m in HEADING_RE.finditer(text):
        h = m.group(2).strip().strip("`*_#")
        out.add(slugify(h))
    # HTML headings used in content cards
    for m in re.finditer(r'<h[1-6][^>]*id="([^"]+)"', text):
        out.add(m.group(1))
    for m in re.finditer(r"<h[1-6][^>]*>(.*?)</h[1-6]>", text, re.S):
        out.add(slugify(re.sub(r"<[^>]+>", "", m.group(1))))
    return out


# --------------------------------------------------------------- 2+3. links
def check_links() -> None:
    problems: list[str] = []
    anchors_cache: dict[Path, set[str]] = {}
    md_files = iter_markdown([REPO_ROOT / "README.md", DOCS, SITE_CONTENT])
    for md in md_files:
        rel_posix = md.relative_to(REPO_ROOT).as_posix()
        if rel_posix.startswith("docs/forensic-docs/"):
            continue  # preserved forensic artifacts: links are historical, not maintained
        text = md.read_text(encoding="utf-8")
        base = md.parent
        for m in MD_LINK_RE.finditer(text):
            target = m.group(1).strip()
            if target.startswith(("http://", "https://", "mailto:", "#")):
                if target.startswith("#"):
                    frag = urllib.parse.unquote(target[1:])
                    if frag and frag not in anchors_of(text):
                        problems.append(f"{md.relative_to(REPO_ROOT)}: broken anchor {target}")
                continue
            path_part, _, frag = target.partition("#")
            path_part = path_part.split("?")[0]
            if not path_part:
                if frag and frag not in anchors_of(text):
                    problems.append(f"{md.relative_to(REPO_ROOT)}: broken anchor #{frag}")
                continue
            if path_part.startswith("/"):
                # Site-root URL (Pages scheme). Validate against the built site.
                site_out = REPO_ROOT / "site" / "_site"
                probe = site_out / path_part.lstrip("/")
                for cand in (probe, probe / "index.html", probe.with_suffix(".html")):
                    if cand.exists():
                        break
                else:
                    problems.append(f"{md.relative_to(REPO_ROOT)}: dead site link -> {target}")
                continue
            resolved = (base / path_part).resolve()
            if not resolved.exists():
                problems.append(f"{md.relative_to(REPO_ROOT)}: dead link -> {target}")
            elif frag:
                if resolved not in anchors_cache:
                    try:
                        anchors_cache[resolved] = anchors_of(resolved.read_text(encoding="utf-8"))
                    except Exception:
                        anchors_cache[resolved] = set()
                if frag and frag not in anchors_cache[resolved]:
                    problems.append(f"{md.relative_to(REPO_ROOT)}: dead anchor {target}")
    record("Links+Anchors", not problems, problems[:20] or ["ok"])
    record(
        "Link count sane",
        len(md_files) > 10,
        [f"only {len(md_files)} md files found"] if len(md_files) <= 10 else ["ok"],
    )


# ------------------------------------------------------------ 5+6. translations
def fm_field(text: str, field: str) -> str | None:
    m = re.search(rf"^{field}:\s*(.+)$", text, re.M)
    return m.group(1).strip().strip("'\"") if m else None


def check_translations_min() -> None:
    problems: list[str] = []
    en_pages = {p.stem for p in (SITE_CONTENT / "en").glob("*.md")}
    for lang in cfg.LANGUAGES:
        if lang == cfg.SOURCE_LANG:
            continue
        ldir = SITE_CONTENT / lang
        if not ldir.exists():
            problems.append(f"language tree missing: site/content/{lang}")
            continue
        local = {p.stem for p in ldir.glob("*.md")}
        missing = en_pages - local
        if missing:
            problems.append(f"{lang}: missing pages (fallback covers them): {sorted(missing)}")
        for p in ldir.glob("*.md"):
            text = p.read_text(encoding="utf-8")
            if fm_field(text, "lang") != lang:
                problems.append(f"{p.name}({lang}): front-matter lang mismatch/missing")
            status = fm_field(text, "translation-status")
            if status not in {"complete", "partial", "stale"}:
                problems.append(f"{p.name}({lang}): translation-status invalid: {status!r}")
            if lang in ("fa", "ar") and fm_field(text, "lang") == lang:
                # rtl languages must be declared rtl in site_config
                if cfg.LANGUAGES[lang]["dir"] != "rtl":
                    problems.append(f"{lang}: configured dir is not rtl")
    record("Translations", not problems, problems[:20] or ["ok"])


def check_rtl_built_site() -> None:
    public = REPO_ROOT / "site" / "_site"
    problems: list[str] = []
    if public.exists():
        for lang in ("fa", "ar"):
            # the canonical builder renders translations under /<lang>/section/page/
            candidates = [
                public / lang / "project" / "status" / "index.html",
                public / lang / "getting-started" / "quickstart" / "index.html",
            ]
            page = next((c for c in candidates if c.exists()), None)
            if page is None:
                problems.append(f"built {lang} pages missing (run build_site.py)")
                continue
            html_text = page.read_text(encoding="utf-8")
            if "rtl" not in html_text.split(">")[1]:
                problems.append(f"built {lang} page lacks dir=rtl on <html>")
        root_page = public / "index.html"
        if root_page.exists():
            root_html = root_page.read_text(encoding="utf-8")
            if "langsel" not in root_html and "language" not in root_html.lower():
                # language switcher can be a <select> or nav links; accept either
                if "fa" not in root_html:
                    problems.append("language switcher missing on built page")
        css = public / "assets" / "styles.css"
        if css.exists():
            css_text = css.read_text(encoding="utf-8")
            if (
                "[dir='rtl']" not in css_text
                and '[dir="rtl"]' not in css_text
                and "dir='rtl'" not in css_text
            ):
                problems.append("built CSS lacks [dir=rtl] rules")
            if "unicode-bidi" not in css_text and "direction:ltr" not in css_text:
                problems.append("built CSS lacks LTR isolation for code blocks")
    else:
        problems.append("site/_site missing — run build_site.py before the doctor")
    record("RTL+Switcher (built)", not problems, problems[:10] or ["ok"])


def check_built_site_structure() -> None:
    """Structural gates on the generated site: assets exist, pages reference
    only relative paths, search index valid, mobile nav wired, version marker."""
    public = REPO_ROOT / "site" / "_site"
    problems: list[str] = []
    if not public.exists():
        record("Built-site structure", False, ["site/_site missing — run build_site.py"])
        return
    for required in (
        "index.html",
        "404.html",
        ".nojekyll",
        "sitemap.xml",
        "search-index.json",
        "site-meta.json",
        "assets/styles.css",
        "assets/search.js",
        "assets/favicon.svg",
        "releases/index.html",
    ):
        if not (public / required).exists():
            problems.append(f"missing built file: {required}")
    try:
        idx = json.loads((public / "search-index.json").read_text(encoding="utf-8"))
    except Exception as exc:
        idx = []
        problems.append(f"search-index.json invalid: {exc}")
    for entry in idx:
        url = entry.get("u", "")
        probe = public / url.lstrip("/")
        for cand in (probe, probe / "index.html", probe.with_suffix(".html")):
            if cand.exists():
                break
        else:
            problems.append(f"search index URL not built: {url}")
    sitemap = (public / "sitemap.xml").read_text(encoding="utf-8")
    repo_seg = f"/{cfg.REPO}/"
    repo_seg_low = repo_seg.lower()
    for loc in re.findall(r"<loc>([^<]+)</loc>", sitemap):
        low = loc.lower()
        # BUG-211 (case-sensitivity on CI): locate segments case-insensitively
        # but slice the path tail from the ORIGINAL url so the filesystem probe
        # preserves page-directory casing (e.g. architecture/QA_BLIND_SPOT_MATRIX/).
        # A fully lowercased tail passes on Windows but 404s on Linux runners.
        seg = low.find(repo_seg_low)
        if seg != -1:
            tail = loc[seg + len(repo_seg) :].lstrip("/").rstrip("/")
        else:
            io = low.find(".github.io/")
            tail = (loc[io + len(".github.io/") :] if io != -1 else loc).lstrip("/").rstrip("/")
        probe = public / tail
        if probe.exists() or (probe / "index.html").exists():
            continue
        # Case-insensitive fallback: source trees may differ in case from URLs.
        cursor = public
        ok = True
        for part in (p for p in tail.split("/") if p):
            if not cursor.is_dir():
                ok = False
                break
            matches = [c for c in cursor.iterdir() if c.name.lower() == part.lower()]
            if not matches:
                ok = False
                break
            cursor = matches[0]
        if not ok:
            problems.append(f"sitemap URL not built: {loc}")
    try:
        meta = json.loads((public / "site-meta.json").read_text(encoding="utf-8"))
        import tomllib

        pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        expected = pyproject.get("project", {}).get("version")
        if meta.get("version") != expected:
            problems.append(f"site-meta version {meta.get('version')} != pyproject {expected}")
    except Exception as exc:
        problems.append(f"site-meta.json invalid: {exc}")
    home = (public / "index.html").read_text(encoding="utf-8")
    if "nav-toggle" not in home:
        problems.append("mobile nav toggle missing")
    js = (public / "assets" / "search.js").read_text(encoding="utf-8")
    if "nav-open" not in js:
        problems.append("search.js does not wire mobile nav")
    # FULL LINK AUDIT: every internal href/src on every built page resolves
    bad_links: list[str] = []
    total_links = 0
    for page in public.rglob("*.html"):
        text = page.read_text(encoding="utf-8", errors="replace")
        from_dir = page.parent
        for m in re.finditer(r"""(?:href|src)='([^']+)'""", text):
            h = m.group(1)
            if h.startswith(("http://", "https://", "mailto:", "#")):
                continue
            total_links += 1
            resolved = (from_dir / h).resolve()
            if not (resolved.exists() or (resolved / "index.html").exists()):
                bad_links.append(f"{page.relative_to(public)}: {h}")
    if bad_links:
        problems.append(f"dead internal links ({len(bad_links)} of {total_links}): {bad_links[:6]}")
    record(
        "Built-site structure",
        not problems,
        problems[:12] or [f"ok — {total_links} links verified"],
    )


# ---------------------------------------------------------------- 7. mermaid
def check_localization_gate() -> None:
    """P0 localization correctness: FA/AR chrome localized, EN-leak detection."""
    import subprocess

    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "docs" / "check_localization.py")],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
        check=False,
    )
    ok = proc.returncode == 0
    detail = "ok" if ok else (proc.stdout.strip().splitlines()[-1] if proc.stdout else "failed")
    record("Localization gate (FA/AR)", ok, detail)


def check_mermaid() -> None:
    problems: list[str] = []
    for md in iter_markdown([DOCS, REPO_ROOT / "README.md"]):
        text = md.read_text(encoding="utf-8")
        for m in re.finditer(r"```mermaid\n(.*?)```", text, re.S):
            body = m.group(1).strip()
            if not body or not body.splitlines()[0].strip():
                problems.append(f"{md.name}: empty mermaid fence")
            # every mermaid diagram in this repo currently uses ASCII art instead;
            # structural check only: first line must declare a diagram type or a node
            first = body.splitlines()[0].split()[0].lower()
            if first not in {
                "graph",
                "flowchart",
                "sequencediagram",
                "classdiagram",
                "statediagram",
                "erdiagram",
                "gantt",
                "pie",
            } and not first.startswith(("graph", "flow")):
                problems.append(f"{md.name}: mermaid fence without diagram header: {first!r}")
    record(
        "Mermaid",
        not problems,
        problems[:10] or ["no mermaid fences (ASCII diagrams used — allowed)"],
    )


# ----------------------------------------------------------------- 8. build
def check_build() -> None:
    import subprocess

    problems: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "docs" / "build_site.py"), "--out", td],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            problems.append(f"site build failed: {proc.stderr.strip()[:300]}")
        else:
            out = Path(td)
            for required in ("index.html", "search-index.json", "404.html"):
                if not (out / required).exists():
                    problems.append(f"build output missing {required}")
            for lang in cfg.LANGUAGES:
                probe = out / lang / "getting-started" / "quickstart" / "index.html"
                if lang != cfg.SOURCE_LANG and not probe.exists():
                    problems.append(f"build output missing {lang} tree")
    record("Site build", not problems, problems[:10] or ["ok"])


# ----------------------------------------------------------------- 9. assets
def check_assets() -> None:
    problems: list[str] = []
    for md in iter_markdown([REPO_ROOT / "README.md", DOCS]):
        text = md.read_text(encoding="utf-8")
        for m in IMG_LINK_RE.finditer(text):
            src = m.group(1).strip()
            if src.startswith(("http://", "https://")):
                continue
            if not (md.parent / src).exists():
                problems.append(f"{md.name}: missing image {src}")
    record("Assets", not problems, problems[:10] or ["ok"])


# --------------------------------------------------------------- 10. secrets
SECRET_PATTERNS = [
    (r"gh[pousr]_[A-Za-z0-9]{20,}", "GitHub token"),
    (r"\b\d{8,10}:[A-Za-z0-9_-]{30,}", "Telegram bot token"),
    (r"BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY", "private key"),
    (
        r"(?i)(api[_-]?key|secret[_-]?key|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{12,}['\"]",
        "hard-coded credential",
    ),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key"),
]


def check_secrets() -> None:
    problems: list[str] = []
    for md in iter_markdown([REPO_ROOT / "README.md", DOCS, SITE_CONTENT]):
        text = md.read_text(encoding="utf-8")
        for pat, label in SECRET_PATTERNS:
            if re.search(pat, text):
                problems.append(f"{md.relative_to(REPO_ROOT)}: {label} pattern found")
    for py in (REPO_ROOT / "scripts" / "docs").glob("*.py"):
        text = py.read_text(encoding="utf-8")
        for pat, label in SECRET_PATTERNS:
            if re.search(pat, text):
                problems.append(f"scripts/docs/{py.name}: {label} pattern found")
    record("Secrets", not problems, problems[:10] or ["clean"])


# ----------------------------------------------------------------- 11. drift
def check_drift() -> None:
    import tomllib

    problems: list[str] = []
    pyproject = REPO_ROOT / "pyproject.toml"
    version = None
    if pyproject.exists():
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        version = data.get("project", {}).get("version")
    if not version:
        problems.append("pyproject.toml version not found")
    # build site config version injection must not conflict
    if (REPO_ROOT / "scripts" / "docs" / "build_site.py").exists():
        sys.path.insert(0, str(REPO_ROOT / "scripts" / "docs"))
        try:
            import site_config  # docs tooling module (local import)

            built_version = site_config.repo_version()
        except Exception:
            built_version = None
        if version and built_version and version != built_version:
            problems.append(f"version drift: pyproject={version} build_site={built_version}")
    record("Version drift", not problems, problems[:5] or [f"single source v{version}"])


# -------------------------------------------------------------------- main
def main() -> int:
    check_readme()
    check_links()
    check_translations_min()
    check_rtl_built_site()
    check_built_site_structure()
    check_localization_gate()
    check_mermaid()
    check_build()
    check_assets()
    check_secrets()
    check_drift()

    print("=" * 62)
    print("DOCS HEALTH")
    print("=" * 62)
    for name, ok, details in CHECKS:
        mark = "✓" if ok else "✗"
        first = details[0] if details else ""
        print(f"{mark} {name:<28} {first if ok else 'FAIL'}")
    print("=" * 62)
    if failures:
        print(f"DOCS_HEALTH = FAIL ({len(failures)} problem(s))")
        for f in failures:
            print(f)
        return 1
    print("DOCS_HEALTH = PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
