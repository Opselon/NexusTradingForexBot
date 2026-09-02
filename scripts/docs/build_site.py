"""NEXUS documentation site generator — v2 (portable base paths, real homepage,
release-aware What's New).

Builds the static multilingual GitHub Pages site from docs/ + site/content/
into site/_site/ (the deploy root uploaded by docs.yml).

v2 design (fixes live-site defects D1-D7):

- D1 base paths: every asset/reference URL is RELATIVE to the page depth
  (e.g. ../../assets/styles.css), never root-absolute. This is the canonical
  base-path mechanism -- no hard-coded repo name in pages, portable to any
  subpath. One constant PAGES_URL (site_config) describes the live root
  for sitemap/OG only.
- D2 search: search.js fetches the index relative to the page (rel_base +
  search-index.json) with graceful no-JS degradation; results link to real
  page URLs built with the same relative scheme.
- D3 mobile nav: builder emits a hamburger button wired by search.js
  (progressive enhancement; nav stays readable without JS via CSS fallback).
- D4 homepage: a real generated homepage (hero, pillars, capability highlights,
  What's New, version timeline) instead of a raw markdown dump. The markdown
  hub content remains under /docs-hub/.
- D5 language switch preserves the current page path when a translation of
  that page exists; otherwise it lands on the language home (flagged).
- D6 prev/next navigation within each section + titled breadcrumbs.
- D7 release awareness: What's New + Releases page generated from GitHub
  release metadata (site/cache/releases.json, refreshed by fetch_releases.py
  at build time; falls back to the cached copy offline). Version/revision come
  from pyproject.toml + git at build time -- never hand-written in pages.

Zero external dependencies (stdlib only). Deterministic output.
Never modify anything under src/ or Web/ -- docs-only surface (Nexus-Docs role).
"""

# ruff: noqa: RUF001  (Persian/Arabic UI strings are intentionally non-Latin)
from __future__ import annotations

import html
import json
import re
import shutil
import sys
from pathlib import Path

import site_config as _cfg

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"
SITE_DIR = REPO_ROOT / "site"
CONTENT_DIR = SITE_DIR / "content"
CACHE_DIR = SITE_DIR / "cache"
OUT_DIR = SITE_DIR / "_site"
PROJECT_VERSION = _cfg.repo_version()
REVISION = _cfg.repo_revision()
REPO_URL = _cfg.REPO_URL
PAGES_URL = _cfg.PAGES_URL

LANGUAGES: dict[str, dict[str, str]] = {
    "en": {"name": "English", "dir": "ltr", "native": "English"},
    "fa": {"name": "فارسی", "dir": "rtl", "native": "فارسی"},
    "es": {"name": "Español", "dir": "ltr", "native": "Español"},
    "ar": {"name": "العربية", "dir": "rtl", "native": "العربية"},
    "de": {"name": "Deutsch", "dir": "ltr", "native": "Deutsch"},
}

UI: dict[str, dict[str, str]] = {
    "en": {
        "search": "Search docs…",
        "home": "Home",
        "repo": "GitHub",
        "skip": "Skip to content",
        "language": "Language",
        "partial": "English source shown — translation pending",
        "version": "Version",
        "next": "Next",
        "prev": "Previous",
        "on_github": "Edit this page on GitHub",
        "menu": "Menu",
        "whats_new": "What's New",
        "all_releases": "All releases",
        "get_started": "Get Started",
        "view_architecture": "View Architecture",
        "view_roadmap": "View Roadmap",
    },
    "fa": {
        "search": "جستجو در مستندات…",
        "home": "خانه",
        "repo": "گیت‌هاب",
        "skip": "پرش به محتوا",
        "language": "زبان",
        "partial": "متن انگلیسی نمایش داده می‌شود — ترجمه در انتظار",
        "version": "نسخه",
        "next": "بعدی",
        "prev": "قبلی",
        "on_github": "ویرایش این صفحه در گیت‌هاب",
        "menu": "منو",
        "whats_new": "تازه‌ها",
        "all_releases": "همه انتشارها",
        "get_started": "شروع کنید",
        "view_architecture": "معماری",
        "view_roadmap": "نقشه راه",
    },
    "es": {
        "search": "Buscar…",
        "home": "Inicio",
        "repo": "GitHub",
        "skip": "Ir al contenido",
        "language": "Idioma",
        "partial": "Fuente en inglés — traducción pendiente",
        "version": "Versión",
        "next": "Siguiente",
        "prev": "Anterior",
        "on_github": "Editar esta página en GitHub",
        "menu": "Menú",
        "whats_new": "Novedades",
        "all_releases": "Todas las versiones",
        "get_started": "Comenzar",
        "view_architecture": "Arquitectura",
        "view_roadmap": "Hoja de ruta",
    },
    "ar": {
        "search": "ابحث في التوثيق…",
        "home": "الرئيسية",
        "repo": "جيت هب",
        "skip": "الانتقال إلى المحتوى",
        "language": "اللغة",
        "partial": "المصدر الإنجليزي — الترجمة قيد الانتظار",
        "version": "الإصدار",
        "next": "التالي",
        "prev": "السابق",
        "on_github": "تعديل هذه الصفحة على جيت هب",
        "menu": "القائمة",
        "whats_new": "الجديد",
        "all_releases": "كل الإصدارات",
        "get_started": "ابدأ",
        "view_architecture": "البنية",
        "view_roadmap": "خارطة الطريق",
    },
    "de": {
        "search": "Doku durchsuchen…",
        "home": "Start",
        "repo": "GitHub",
        "skip": "Zum Inhalt springen",
        "language": "Sprache",
        "partial": "Englische Quelle — Übersetzung ausstehend",
        "version": "Version",
        "next": "Weiter",
        "prev": "Zurück",
        "on_github": "Diese Seite auf GitHub bearbeiten",
        "menu": "Menü",
        "whats_new": "Neuigkeiten",
        "all_releases": "Alle Releases",
        "get_started": "Erste Schritte",
        "view_architecture": "Architektur",
        "view_roadmap": "Roadmap",
    },
}

SECTION_TITLES: dict[str, dict[str, str]] = {
    "getting-started": {
        "en": "Getting Started",
        "fa": "شروع",
        "es": "Inicio",
        "ar": "البداية",
        "de": "Einstieg",
    },
    "project": {"en": "Project", "fa": "پروژه", "es": "Proyecto", "ar": "المشروع", "de": "Projekt"},
    "architecture": {
        "en": "Architecture",
        "fa": "معماری",
        "es": "Arquitectura",
        "ar": "البنية",
        "de": "Architektur",
    },
    "research": {
        "en": "Research",
        "fa": "پژوهش",
        "es": "Investigación",
        "ar": "البحث",
        "de": "Forschung",
    },
    "engineering": {
        "en": "Engineering",
        "fa": "مهندسی",
        "es": "Ingeniería",
        "ar": "الهندسة",
        "de": "Engineering",
    },
    "guides": {"en": "Guides", "fa": "راهنما", "es": "Guías", "ar": "أدلة", "de": "Anleitungen"},
    "contributing": {
        "en": "Contributing",
        "fa": "مشارکت",
        "es": "Contribuir",
        "ar": "المساهمة",
        "de": "Mitwirken",
    },
    "reference": {
        "en": "Reference",
        "fa": "مرجع",
        "es": "Referencia",
        "ar": "مرجع",
        "de": "Referenz",
    },
    "releases": {
        "en": "Releases",
        "fa": "انتشارها",
        "es": "Versiones",
        "ar": "الإصدارات",
        "de": "Releases",
    },
}

NAV_SECTIONS: list[tuple[str, list[str]]] = [
    ("getting-started", ["installation", "quickstart", "first-run", "configuration"]),
    ("project", ["vision", "scope", "status", "capabilities", "roadmap", "milestones"]),
    (
        "architecture",
        [
            "overview",
            "system-map",
            "data-flow",
            "runtime",
            "research-stack",
            "model-pipeline",
            "execution-pipeline",
            "observability",
            "database",
        ],
    ),
    (
        "research",
        [
            "methodology",
            "datasets",
            "backtesting",
            "walk-forward",
            "out-of-sample",
            "replay",
            "counterfactuals",
            "validation",
            "reproducibility",
        ],
    ),
    ("engineering", ["quality", "ci", "release-process", "security"]),
    ("guides", ["cli", "api", "troubleshooting", "common-workflows"]),
    ("contributing", ["contribution-guide", "documentation", "add-language"]),
    ("reference", ["cli-reference", "glossary", "terminology", "faq"]),
]

FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    fm: dict[str, str] = {}
    body = text
    m = FM_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                fm[key.strip()] = val.strip().strip('"')
        body = text[m.end() :]
    return fm, body


def rel_base(rel: str) -> str:
    """Relative prefix from a page back to the site root ('', '../', '../../')."""
    depth = len([p for p in rel.split("/") if p])
    return "../" * depth


def page_url(rel: str, lang: str) -> str:
    """Site-root URL of a page (absolute form for sitemap/search index)."""
    prefix = "" if lang == "en" else f"/{lang}"
    return f"{prefix}/{rel}/" if rel else f"{prefix}/"


def render_markdown(src: str) -> str:
    """Small deterministic markdown renderer (headings, code, tables, lists,
    callouts, links, emphasis). Output is embedded in the page shell."""
    out: list[str] = []
    lines = src.splitlines()
    i = 0
    in_code = False
    code_buf: list[str] = []
    list_stack: list[str] = []
    table_buf: list[str] = []

    def esc(s: str) -> str:
        return html.escape(s, quote=False)

    def inline(s: str) -> str:
        s = esc(s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"<em>\1</em>", s)
        s = re.sub(r"(https?://[^\s<)]+)", r'<a href="\1">\1</a>', s)
        return s

    def flush_table() -> None:
        nonlocal table_buf
        if not table_buf:
            return
        rows = [r for r in table_buf if not re.match(r"^\s*\|[\s:|-]+\|\s*$", r)]
        table_buf = []
        if not rows:
            return
        cells = lambda r: [c.strip() for c in r.strip().strip("|").split("|")]  # noqa: E731
        head = cells(rows[0])
        out.append("<div class='table-wrap'><table>")
        out.append(
            "<thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
        )
        for r in rows[1:]:
            out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells(r)) + "</tr>")
        out.append("</tbody></table></div>")

    def close_lists() -> None:
        while list_stack:
            out.append(f"</{list_stack.pop()}>")

    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            if in_code:
                out.append(
                    f"<pre dir='ltr' class='code-ltr'><code>{html.escape(chr(10).join(code_buf))}</code></pre>"
                )
                code_buf = []
                in_code = False
            else:
                flush_table()
                close_lists()
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            flush_table()
            close_lists()
            level = len(m.group(1))
            slug = re.sub(r"[^a-z0-9-]", "", m.group(2).lower().replace(" ", "-"))[:60]
            out.append(f"<h{level} id='{esc(slug)}'>{inline(m.group(2))}</h{level}>")
            i += 1
            continue
        if line.strip().startswith("> [!"):
            flush_table()
            close_lists()
            kind = "note"
            mm = re.match(r"^>\s*\[!(\w+)\]", line)
            if mm:
                kind = mm.group(1).lower()
            body_lines: list[str] = []
            i += 1
            while i < len(lines) and lines[i].startswith(">"):
                body_lines.append(lines[i].lstrip(">").strip())
                i += 1
            out.append(
                f"<div class='callout callout-{kind}' role='note'>"
                f"<div class='callout-title'>{esc(kind.upper())}</div>"
                + "".join(f"<p>{inline(b)}</p>" for b in body_lines if b)
                + "</div>"
            )
            continue
        if line.strip().startswith(">"):
            flush_table()
            close_lists()
            quote: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].lstrip(">").strip())
                i += 1
            out.append(
                "<blockquote>"
                + "".join(f"<p>{inline(q)}</p>" for q in quote if q)
                + "</blockquote>"
            )
            continue
        if line.strip().startswith("|"):
            table_buf.append(line)
            i += 1
            continue
        flush_table()
        ul_re = re.compile(r"^\s*[-*]\s+")
        ol_re = re.compile(r"^\s*\d+\.\s+")
        if ul_re.match(line):
            if not list_stack or list_stack[-1] != "ul":
                close_lists()
                list_stack.append("ul")
                out.append("<ul>")
            out.append("<li>" + inline(ul_re.sub("", line)) + "</li>")
            i += 1
            continue
        if ol_re.match(line):
            if not list_stack or list_stack[-1] != "ol":
                close_lists()
                list_stack.append("ol")
                out.append("<ol>")
            out.append("<li>" + inline(ol_re.sub("", line)) + "</li>")
            i += 1
            continue
        close_lists()
        if line.strip() == "---":
            out.append("<hr>")
        elif line.strip():
            out.append(f"<p>{inline(line.strip())}</p>")
        i += 1
    flush_table()
    close_lists()
    if in_code and code_buf:
        out.append(
            f"<pre dir='ltr' class='code-ltr'><code>{html.escape(chr(10).join(code_buf))}</code></pre>"
        )
    return "\n".join(out)


def page_title(fm: dict[str, str], body: str, fallback: str) -> str:
    if fm.get("title"):
        return fm["title"]
    m = TITLE_RE.search(body)
    return m.group(1).strip() if m else fallback


def md_pages_in(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.md") if p.is_file())


def strip_md_ext(p: Path, base: Path) -> str:
    return p.relative_to(base).with_suffix("").as_posix()


def find_translation(lang: str, rel: str) -> Path | None:
    """Locate the best translation for rel (docs/-relative page id)."""
    if lang == "en":
        return DOCS_DIR / f"{rel}.md"
    cand = CONTENT_DIR / lang / f"{rel}.md"
    if cand.exists():
        return cand
    # Flat core-page aliases (Nexus-Docs core set): section/page → flat name
    _FLAT_ALIASES = {
        "project/status": "status",
        "project/vision": "start",
        "project/roadmap": "roadmap",
        "architecture/overview": "architecture",
        "research/methodology": "research",
        "research/validation": "validation",
        "reference/faq": "reference",
        "contributing/contribution-guide": "contributing",
        "getting-started/quickstart": "start",
        "getting-started/installation": "start",
    }
    alias = _FLAT_ALIASES.get(rel)
    if alias:
        flat = CONTENT_DIR / lang / f"{alias}.md"
        if flat.exists():
            return flat
    return None


def lang_prefix(lang: str) -> str:
    return "" if lang == "en" else f"/{lang}"


def section_title(section: str, lang: str) -> str:
    entry = SECTION_TITLES.get(section, {})
    return entry.get(lang) or entry.get("en", section)


def translations_exist_for_page(lang: str, rel: str) -> bool:
    if lang == "en":
        return True
    if rel in ("releases", "docs-hub", "index"):
        return (CONTENT_DIR / lang / f"{rel}.md").exists() or find_translation(
            lang, rel
        ) is not None
    return find_translation(lang, rel) is not None


def build_nav(lang: str, active: str) -> str:
    parts = ["<nav class='sidebar' id='sidebar' aria-label='primary'>"]
    parts.append(
        f"<a class='nav-home' href='{lang_prefix(lang)}/'>{html.escape(UI[lang]['home'])}</a>"
    )
    for section, pages in NAV_SECTIONS:
        parts.append(f"<div class='nav-section'>{html.escape(section_title(section, lang))}</div>")
        parts.append("<ul class='nav-list'>")
        for pg in pages:
            rel = f"{section}/{pg}"
            available = find_translation(lang, rel) is not None
            cls = "active" if rel == active else ""
            flag = (
                ""
                if (available or lang == "en")
                else " <span class='fallback-tag' title='English source'>EN</span>"
            )
            parts.append(
                f"<li><a class='{cls}' href='{lang_prefix(lang)}/{rel}/'>{html.escape(pg.replace('-', ' '))}</a>{flag}</li>"
            )
        parts.append("</ul>")
    parts.append(f"<div class='nav-section'>{html.escape(section_title('releases', lang))}</div>")
    parts.append(
        f"<ul class='nav-list'><li><a class='{'active' if active == 'releases' else ''}' "
        f"href='{lang_prefix(lang)}/releases/'>v{PROJECT_VERSION} &amp; history</a></li></ul>"
    )
    parts.append("</nav>")
    return "\n".join(parts)


def lang_switcher(lang: str, rel: str) -> str:
    """Language switcher preserving the current page when a translation exists."""
    links = []
    for code in LANGUAGES:
        if code == lang:
            links.append(
                f"<span class='lang-current' lang='{code}'>{html.escape(LANGUAGES[code]['native'])} ✓</span>"
            )
            continue
        keep = translations_exist_for_page(code, rel)
        target = f"{lang_prefix(code)}/{rel}/" if (keep and rel) else f"{lang_prefix(code)}/"
        title = "" if keep else " title='Landing page — this page is not yet translated'"
        links.append(
            f"<a href='{target}' lang='{code}' hreflang='{code}'{title}>{html.escape(LANGUAGES[code]['native'])}</a>"
        )
    return "<div class='lang-menu'>" + " ".join(links) + "</div>"


def build_header(lang: str, rel: str) -> str:
    ui = UI[lang]
    return f"""<header class='site-header'>
  <button class='nav-toggle' id='nav-toggle' aria-label='{html.escape(ui["menu"])}' aria-expanded='false' aria-controls='sidebar'>
    <span></span><span></span><span></span>
  </button>
  <div class='brand'>
    <a href='{lang_prefix(lang)}/' class='brand-link'>⚡ Nexus <span class='brand-dim'>Scalp Engine</span></a>
    <span class='brand-badge'>v{PROJECT_VERSION}</span>
  </div>
  <div class='header-actions'>
    <input id='doc-search' class='search' type='search' placeholder='{html.escape(ui["search"])}' aria-label='{html.escape(ui["search"])}' autocomplete='off'>
    <details class='lang-picker'>
      <summary aria-label='{html.escape(ui["language"])}'>🌐 {html.escape(LANGUAGES[lang]["native"])}</summary>
      {lang_switcher(lang, rel)}
    </details>
    <a class='repo-link' href='{REPO_URL}'>{html.escape(ui["repo"])}</a>
  </div>
</header>"""


def prev_next(lang: str, rel: str) -> str:
    """Prev/next links within the section (section heads included)."""
    flat: list[str] = []
    for section, pages in NAV_SECTIONS:
        flat.append(f"{section}/")
        flat.extend(f"{section}/{pg}" for pg in pages)
    if rel not in flat:
        return ""
    idx = flat.index(rel)
    prev_rel = flat[idx - 1] if idx > 0 else None
    next_rel = flat[idx + 1] if idx + 1 < len(flat) else None

    def link(r: str | None, cls: str, label: str) -> str:
        if not r:
            return f"<span class='pn {cls} pn-empty'></span>"
        if r.endswith("/"):
            name = section_title(r.rstrip("/"), lang)
        else:
            name = r.rstrip("/").split("/")[-1].replace("-", " ")
        return (
            f"<a class='pn {cls}' href='{lang_prefix(lang)}/{r}'>"
            f"<span class='pn-label'>{html.escape(label)}</span>"
            f"<span class='pn-name'>{html.escape(name)}</span></a>"
        )

    return (
        "<nav class='prev-next' aria-label='pagination'>"
        + link(prev_rel, "pn-prev", UI[lang]["prev"])
        + link(next_rel, "pn-next", UI[lang]["next"])
        + "</nav>"
    )


def breadcrumbs(lang: str, rel: str) -> str:
    if not rel or rel == "index":
        return ""
    crumbs: list[str] = []
    acc: list[str] = []
    parts = rel.split("/")
    for i, part in enumerate(parts):
        acc.append(part)
        is_last = i == len(parts) - 1
        name = section_title(part, lang) if i == 0 else part.replace("-", " ")
        if is_last:
            crumbs.append(
                f"<span class='crumb-current' aria-current='page'>{html.escape(name)}</span>"
            )
        else:
            crumbs.append(f"<a href='{lang_prefix(lang)}/{'/'.join(acc)}/'>{html.escape(name)}</a>")
    return "<nav class='breadcrumbs' aria-label='breadcrumb'>" + " / ".join(crumbs) + "</nav>"


def shell(lang: str, title: str, desc: str, body: str, rel: str, translated: bool) -> str:
    ui = UI[lang]
    direction = LANGUAGES[lang]["dir"]
    base = rel_base(rel)
    if lang == "en":
        src_rel = f"docs/{rel}.md" if rel else "docs/index.md"
    else:
        src = find_translation(lang, rel) if rel else None
        src_rel = src.relative_to(REPO_ROOT).as_posix() if src else f"site/content/{lang}/{rel}.md"
    edit_url = f"{REPO_URL}/edit/main/{src_rel}"
    canonical = f"{PAGES_URL}{page_url(rel, lang)}"
    status_flag = (
        ""
        if (translated or lang == "en")
        else (
            f"<div class='translation-note'>{html.escape(ui['partial'])} — "
            f"<a href='{base}{rel}/'>English</a></div>"
        )
    )
    return f"""<!DOCTYPE html>
<html lang='{lang}' dir='{direction}'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>{html.escape(title)} · Nexus Scalp Engine</title>
<meta name='description' content='{html.escape(desc[:150])}'>
<link rel='canonical' href='{canonical}'>
<meta property='og:title' content='{html.escape(title)}'>
<meta property='og:type' content='website'>
<meta property='og:url' content='{canonical}'>
<meta property='og:site_name' content='Nexus Scalp Engine'>
<link rel='stylesheet' href='{base}assets/styles.css'>
<link rel='icon' href='{base}assets/favicon.svg' type='image/svg+xml'>
</head>
<body>
<a class='skip-link' href='#content'>{html.escape(ui["skip"])}</a>
{build_header(lang, rel)}
<div class='layout'>
{build_nav(lang, rel)}
<main id='content' class='content' tabindex='-1'>
{status_flag}
{body}
{prev_next(lang, rel)}
<footer class='page-footer'>
  <a href='{edit_url}'>{html.escape(ui["on_github"])}</a> ·
  {html.escape(ui["version"])} v{PROJECT_VERSION} · rev <a href='{REPO_URL}/commit/{REVISION}'>{REVISION}</a> ·
  <a href='{lang_prefix(lang)}/releases/'>{html.escape(ui["all_releases"])}</a>
</footer>
</main>
</div>
<script src='{base}assets/search.js' defer></script>
</body>
</html>"""


def build_404(lang: str = "en") -> str:
    body = (
        "<div class='nf404'><h1>404</h1>"
        "<p class='nf404-lead'>This documentation page does not exist.</p>"
        "<div class='nf404-actions'>"
        "<a class='btn btn-primary' href='/'>Home</a> "
        "<a class='btn' href='/getting-started/quickstart/'>Quickstart</a> "
        "<a class='btn' href='/project/status/'>Project status</a> "
        "<a class='btn' href='/reference/faq/'>FAQ</a>"
        "</div></div>"
    )
    return shell(lang, "404", "Not found", body, "", True)


def load_releases() -> list[dict]:
    """Load cached GitHub release metadata (deterministic, offline-safe)."""
    cache = CACHE_DIR / "releases.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def fmt_release_date(iso: str) -> str:
    return (iso or "")[:10]


def release_highlights(body: str, limit: int = 6) -> list[str]:
    """Extract bullet highlights from a GitHub release body (real data only)."""
    lines = []
    for ln in (body or "").splitlines():
        m = re.match(r"^[-*]\s+(.{20,240})$", ln.strip())
        if m:
            lines.append(m.group(1))
    return lines[:limit]


def homepage_html(lang: str = "en") -> str:
    """Real homepage: hero, pillars, capability highlights, What's New,
    version timeline. Data from pyproject + cached releases (real data only)."""
    ui = UI[lang]
    releases = [r for r in load_releases() if not r.get("draft")]
    latest = releases[0] if releases else None
    highlights = release_highlights(latest.get("body", "")) if latest else []
    timeline = "".join(
        f"<a class='tl-item' href='{lang_prefix(lang)}/releases/'>"
        f"<span class='tl-tag'>{html.escape(r['tag_name'])}</span>"
        f"<span class='tl-date'>{fmt_release_date(r.get('published_at', ''))}</span></a>"
        for r in reversed(releases[:5])
    )
    whats_new = ""
    if latest:
        items = "".join(f"<li>{html.escape(h)}</li>" for h in highlights) or "<li>—</li>"
        whats_new = (
            f"<section class='whats-new'><div class='section-head'><h2>⚡ {html.escape(ui['whats_new'])} — "
            f"{html.escape(latest['tag_name'])}</h2>"
            f"<span class='tl-date'>{fmt_release_date(latest.get('published_at', ''))}</span></div>"
            f"<ul class='wn-list'>{items}</ul>"
            f"<a class='wn-more' href='{lang_prefix(lang)}/releases/'>{html.escape(ui['all_releases'])} →</a></section>"
        )
    pillars = (
        "<section class='pillars'>"
        "<div class='pillar'><h3>🧭 Evidence before claims</h3>"
        "<p>Metrics without evidence render <code>n/a</code> — never fake zeros. Negative results are published: the flagship 70D research candidate was <strong>rejected</strong> by our own OOS gate.</p></div>"
        "<div class='pillar'><h3>🔒 Safety by construction</h3>"
        "<p>PAPER default · SHADOW with zero order authority · LIVE behind explicit confirmation · hard risk clamps (<code>HARD_MAX_LOTS</code>, margin ≤20%) enforced in the execution path.</p></div>"
        "<div class='pillar'><h3>🔬 Deterministic research</h3>"
        "<p>Purged + embargoed walk-forward, bit-exact replay, fingerprinted datasets/models, provenance on every run — the same feature contract for live, replay and training.</p></div>"
        "<div class='pillar'><h3>👁️ Runtime truth</h3>"
        "<p>Forensic observability: severity-split logs, incident correlation, deploy gate. Broker truth wins over stale state; gates are authorities, settings are intent.</p></div>"
        "</section>"
    )
    caps = (
        "<section class='cap-highlights'><div class='section-head'><h2>🧱 Capability highlights</h2>"
        f"<a class='wn-more' href='{lang_prefix(lang)}/project/capabilities/'>Full matrix →</a></div>"
        "<div class='table-wrap'><table><thead><tr><th>Capability</th><th>Status</th></tr></thead><tbody>"
        "<tr><td><a href='/architecture/data-flow/'>Causal 50D feature engine</a></td><td><span class='chip chip-cert'>CERTIFIED</span></td></tr>"
        "<tr><td><a href='/architecture/execution-pipeline/'>Risk engine + execution clamps</a></td><td><span class='chip chip-cert'>CERTIFIED</span></td></tr>"
        "<tr><td><a href='/research/validation/'>Walk-forward + hard OOS gate</a></td><td><span class='chip chip-cert'>CERTIFIED</span></td></tr>"
        "<tr><td><a href='/architecture/model-pipeline/'>Artifact-first Model Factory</a></td><td><span class='chip chip-impl'>IMPLEMENTED</span></td></tr>"
        "<tr><td><a href='/architecture/research-stack/'>70D contract (Base+News+Liquidity)</a></td><td><span class='chip chip-exp'>EXPERIMENTAL</span></td></tr>"
        "<tr><td><a href='/research/counterfactuals/'>Counterfactual engine (NO_TRADE walk)</a></td><td><span class='chip chip-res'>RESEARCH</span></td></tr>"
        "</tbody></table></div></section>"
    )
    hero_actions = (
        f"<div class='hero-actions'><a class='btn btn-primary' href='{lang_prefix(lang)}/getting-started/quickstart/'>{html.escape(ui['get_started'])}</a>"
        f"<a class='btn' href='{lang_prefix(lang)}/architecture/overview/'>{html.escape(ui['view_architecture'])}</a>"
        f"<a class='btn' href='{lang_prefix(lang)}/project/roadmap/'>{html.escape(ui['view_roadmap'])}</a>"
        f"<a class='btn btn-ghost' href='{REPO_URL}'>GitHub ↗</a></div>"
    )
    status_line = (
        f"<div class='hero-meta'><span class='chip chip-cert'>v{PROJECT_VERSION} released</span>"
        f"<span class='chip'>rev {REVISION}</span>"
        f"<span class='chip'>research: 50D live · 70D candidate</span></div>"
    )
    body = f"""
<section class='hero'>
  <div class='hero-kicker'>QUANTITATIVE TRADING RESEARCH &amp; EXECUTION PLATFORM</div>
  <h1>Nexus <span class='grad'>Scalp Engine</span></h1>
  <p class='hero-sub'>A research-driven, hexagonal, event-driven scalping platform for MetaTrader 5 —
  causal features, governed deep models, an invariant risk engine, deterministic research tooling
  and forensic observability in one auditable pipeline.</p>
  {status_line}
  {hero_actions}
</section>
{pillars}
{caps}
{whats_new}
<section class='timeline-block'><div class='section-head'><h2>🗓️ Release timeline</h2>
<a class='wn-more' href='{lang_prefix(lang)}/releases/'>{html.escape(ui["all_releases"])} →</a></div>
<div class='timeline'>{timeline}</div></section>
"""
    return shell(
        lang,
        "Nexus Scalp Engine Documentation",
        _cfg.SITE_TAGLINE.get(lang, _cfg.SITE_TAGLINE["en"]),
        body,
        "",
        True,
    )


def releases_page_html(lang: str = "en") -> str:
    releases = [r for r in load_releases() if not r.get("draft")]
    blocks = []
    for r in releases:
        highs = release_highlights(r.get("body", ""), limit=8)
        items = "".join(f"<li>{html.escape(h)}</li>" for h in highs) or "<li>—</li>"
        link = f"<a class='wn-more' href='{REPO_URL}/releases/tag/{r['tag_name']}'>GitHub release ↗</a>"
        blocks.append(
            f"<article class='release-card' id='{html.escape(r['tag_name'])}'>"
            f"<div class='section-head'><h2>{html.escape(r['tag_name'])}</h2>"
            f"<span class='tl-date'>{fmt_release_date(r.get('published_at', ''))}</span></div>"
            f"<ul class='wn-list'>{items}</ul>{link}</article>"
        )
    body = (
        f"<section class='hero'><div class='hero-kicker'>RELEASE HISTORY</div>"
        f"<h1>Releases</h1><p class='hero-sub'>Every release with its real highlights, derived from "
        f"GitHub release metadata at build time. Version single-source: <code>pyproject.toml</code> (v{PROJECT_VERSION}).</p></section>"
        + (
            "".join(blocks)
            if blocks
            else "<p class='translation-note'>No release metadata available.</p>"
        )
    )
    return shell(
        lang, "Releases", "Release history — Nexus Scalp Engine", body, "releases", lang == "en"
    )


def build_search_index(entries: list[dict[str, str]]) -> str:
    slim = [{"u": e["url"], "t": e["title"], "l": e["lang"], "x": e["text"][:900]} for e in entries]
    return json.dumps(slim, ensure_ascii=False)


def main() -> int:
    argv = sys.argv[1:]
    out_dir = OUT_DIR
    if "--out" in argv:  # doctor/CI use a temp output for validation builds
        out_dir = Path(argv[argv.index("--out") + 1])
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "assets").mkdir(parents=True)

    src_css = SITE_DIR / "assets" / "styles.css"
    src_js = SITE_DIR / "assets" / "search.js"
    src_icon = SITE_DIR / "assets" / "favicon.svg"
    for src, dst in ((src_css, "styles.css"), (src_js, "search.js"), (src_icon, "favicon.svg")):
        if src.exists():
            shutil.copyfile(src, out_dir / "assets" / dst)

    search_entries: list[dict[str, str]] = []
    built = 0

    en_pages = md_pages_in(DOCS_DIR)
    ia_prefixes = tuple(f"{sec}/" for sec, _ in NAV_SECTIONS)
    en_pages = [
        p
        for p in en_pages
        if p.name == "index.md" or p.relative_to(DOCS_DIR).as_posix().startswith(ia_prefixes)
    ]

    for lang in LANGUAGES:
        lang_root = out_dir if lang == "en" else out_dir / lang
        if lang != "en":
            lang_root.mkdir(parents=True, exist_ok=True)  # en renders at site root

        # Generated homepage + releases page for every language
        home = homepage_html(lang)
        (lang_root / "index.html").write_text(home, encoding="utf-8", newline="\n")
        built += 1
        search_entries.append(
            {
                "url": page_url("", lang),
                "title": f"Home [{lang}]",
                "lang": lang,
                "text": "Nexus Scalp Engine documentation homepage what's new capabilities architecture research",
            }
        )
        (lang_root / "releases").mkdir(parents=True, exist_ok=True)
        rel_page = releases_page_html(lang)
        (lang_root / "releases" / "index.html").write_text(rel_page, encoding="utf-8", newline="\n")
        built += 1
        search_entries.append(
            {
                "url": page_url("releases", lang),
                "title": f"Releases [{lang}]",
                "lang": lang,
                "text": f"releases v{PROJECT_VERSION} changelog what's new history {REVISION}",
            }
        )

        for page in en_pages:
            rel = strip_md_ext(page, DOCS_DIR)
            if rel == "index":
                rel = "docs-hub"  # root is the real homepage; hub content moves here
            translated = False
            src_path = page
            if lang != "en":
                tpath = find_translation(lang, rel)
                if tpath is None:
                    continue  # build only translated pages under /<lang>/; EN covers the rest
                src_path = tpath
                translated = True
            fm, body = parse_front_matter(src_path.read_text(encoding="utf-8"))
            title = page_title(fm, body, rel)
            desc = fm.get("description", f"{title} — Nexus Scalp Engine documentation")
            html_body = render_markdown(body)
            crumb = breadcrumbs(lang, rel)
            if crumb:
                html_body = crumb + "\n" + html_body
            outpath = lang_root / rel / "index.html"
            outpath.parent.mkdir(parents=True, exist_ok=True)
            page_html = shell(lang, title, desc, html_body, rel, translated or lang == "en")
            outpath.write_text(page_html, encoding="utf-8", newline="\n")
            built += 1
            search_entries.append(
                {
                    "url": page_url(rel, lang),
                    "title": f"{title} [{lang}]",
                    "lang": lang,
                    "text": re.sub(r"<[^>]+>", " ", html_body)[:2000],
                }
            )

    # Favicon fallback + 404 + metadata files
    if not (out_dir / "assets" / "favicon.svg").exists():
        (out_dir / "assets" / "favicon.svg").write_text(
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='24' font-size='24'>⚡</text></svg>",
            encoding="utf-8",
        )
    (out_dir / "404.html").write_text(build_404(), encoding="utf-8", newline="\n")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    (out_dir / "search-index.json").write_text(
        build_search_index(search_entries), encoding="utf-8", newline="\n"
    )
    (out_dir / "site-meta.json").write_text(
        json.dumps(
            {
                "version": PROJECT_VERSION,
                "revision": REVISION,
                "repo": REPO_URL,
                "pages": PAGES_URL,
            },
            indent=1,
        ),
        encoding="utf-8",
        newline="\n",
    )
    urls = [f"{PAGES_URL}/"]
    for entry in search_entries:
        urls.append(f"{PAGES_URL}{entry['url']}")
    (out_dir / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{u}</loc></url>\n" for u in urls)
        + "</urlset>",
        encoding="utf-8",
        newline="\n",
    )
    print(f"BUILT pages={built} langs={len(LANGUAGES)} out={out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
