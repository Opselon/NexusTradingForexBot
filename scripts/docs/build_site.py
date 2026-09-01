"""NEXUS documentation site generator.

Builds the static multilingual GitHub Pages site from docs/ + site/content/
into site/_site/ (the deploy root committed or uploaded by docs.yml).

Design:
- zero external dependencies (stdlib only) so docs CI needs no npm/pip installs
- deterministic output (no timestamps) so rebuilds are byte-stable and CI can
  cache cleanly
- client-side search over a tiny generated index (no external service)
- full RTL support for fa/ar via per-language <html dir>, with LTR code blocks
- language switcher on every page; missing translations fall back to English
  and are flagged by scripts/docs/check_translations.py

Never modify anything under src/ or Web/ — docs-only surface (Nexus-Docs role).
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"
SITE_DIR = REPO_ROOT / "site"
CONTENT_DIR = SITE_DIR / "content"
OUT_DIR = SITE_DIR / "_site"
PROJECT_VERSION = "9.0.6"
REPO_URL = "https://github.com/Opselon/NexusTradingForexBot"
PAGES_URL = "https://opselon.github.io/NexusTradingForexBot/"

LANGUAGES: dict[str, dict[str, str]] = {
    "en": {"name": "English", "dir": "ltr", "native": "English"},
    "fa": {"name": "فارسی", "dir": "rtl", "native": "فارسی"},
    "es": {"name": "Español", "dir": "ltr", "native": "Español"},
    "ar": {"name": "العربية", "dir": "rtl", "native": "العربية"},
    "de": {"name": "Deutsch", "dir": "ltr", "native": "Deutsch"},
}

# Shared section labels per language (nav + UI chrome).
UI: dict[str, dict[str, str]] = {
    "en": {
        "search": "Search docs…", "home": "Home", "docs": "Docs", "repo": "GitHub",
        "skip": "Skip to content", "language": "Language", "partial": "partially translated",
        "version": "Version", "status": "Project status", "next": "Next", "prev": "Previous",
        "on_github": "Edit this page on GitHub",
    },
    "fa": {
        "search": "جستجو در مستندات…", "home": "خانه", "docs": "مستندات", "repo": "گیت‌هاب",
        "skip": "پرش به محتوا", "language": "زبان", "partial": "ترجمه جزئی",
        "version": "نسخه", "status": "وضعیت پروژه", "next": "بعدی", "prev": "قبلی",
        "on_github": "ویرایش این صفحه در گیت‌هاب",
    },
    "es": {
        "search": "Buscar…", "home": "Inicio", "docs": "Docs", "repo": "GitHub",
        "skip": "Ir al contenido", "language": "Idioma", "partial": "traducción parcial",
        "version": "Versión", "status": "Estado del proyecto", "next": "Siguiente", "prev": "Anterior",
        "on_github": "Editar esta página en GitHub",
    },
    "ar": {
        "search": "ابحث في التوثيق…", "home": "الرئيسية", "docs": "التوثيق", "repo": "جيت هب",
        "skip": "الانتقال إلى المحتوى", "language": "اللغة", "partial": "ترجمة جزئية",
        "version": "الإصدار", "status": "حالة المشروع", "next": "التالي", "prev": "السابق",
        "on_github": "تعديل هذه الصفحة على جيت هب",
    },
    "de": {
        "search": "Dokumentation durchsuchen…", "home": "Start", "docs": "Doku", "repo": "GitHub",
        "skip": "Zum Inhalt springen", "language": "Sprache", "partial": "teilweise übersetzt",
        "version": "Version", "status": "Projektstatus", "next": "Weiter", "prev": "Zurück",
        "on_github": "Diese Seite auf GitHub bearbeiten",
    },
}

NAV_SECTIONS: list[tuple[str, list[str]]] = [
    ("getting-started", ["installation", "quickstart", "first-run", "configuration"]),
    ("project", ["vision", "scope", "status", "capabilities", "roadmap", "milestones"]),
    (
        "architecture",
        ["overview", "system-map", "data-flow", "runtime", "research-stack",
         "model-pipeline", "execution-pipeline", "observability", "database"],
    ),
    (
        "research",
        ["methodology", "datasets", "backtesting", "walk-forward", "out-of-sample",
         "replay", "counterfactuals", "validation", "reproducibility"],
    ),
    ("engineering", ["quality", "ci", "release-process", "security"]),
    ("guides", ["cli", "troubleshooting", "common-workflows"]),
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
        body = text[m.end():]
    return fm, body


def render_markdown(src: str) -> str:
    """Small deterministic markdown renderer (headings, code, tables, lists,
    callouts, links, emphasis). Output is embedded in the page shell."""
    out: list[str] = []
    lines = src.splitlines()
    i = 0
    in_code = False
    code_lang = ""
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
        out.append("<thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>")
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
                cls = "code-ltr" if code_lang else "code-ltr"
                out.append(
                    f"<pre dir='ltr' class='{cls}'><code>{html.escape(chr(10).join(code_buf))}</code></pre>"
                )
                code_buf = []
                in_code = False
                code_lang = ""
            else:
                flush_table()
                close_lists()
                in_code = True
                code_lang = line.strip()[3:].strip()
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
        if line.strip().startswith("> [!") :
            flush_table(); close_lists()
            kind = "note"
            mm = re.match(r"^>\s*\[!(\w+)\]", line)
            if mm:
                kind = mm.group(1).lower()
            body_lines: list[str] = []
            i += 1
            while i < len(lines) and lines[i].startswith(">"):
                body_lines.append(lines[i].lstrip(">").strip())
                i += 1
            out.append(f"<div class='callout callout-{kind}' role='note'>"
                       f"<div class='callout-title'>{esc(kind.upper())}</div>"
                       + "".join(f"<p>{inline(b)}</p>" for b in body_lines if b)
                       + "</div>")
            continue
        if line.strip().startswith(">"):
            flush_table(); close_lists()
            quote: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].lstrip(">").strip())
                i += 1
            out.append("<blockquote>" + "".join(f"<p>{inline(q)}</p>" for q in quote if q) + "</blockquote>")
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
        out.append(f"<pre dir='ltr' class='code-ltr'><code>{html.escape(chr(10).join(code_buf))}</code></pre>")
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
    # site/content/<lang>/... mirrors docs/ tree (index.md → index.md)
    cand = CONTENT_DIR / lang / f"{rel}.md"
    if cand.exists():
        return cand
    return None


def source_revision(rel: str) -> str:
    en = DOCS_DIR / f"{rel}.md"
    if en.exists():
        return f"en:{rel}@{PROJECT_VERSION}"
    return f"en:{rel}@unknown"


def lang_prefix(lang: str) -> str:
    return "" if lang == "en" else f"/{lang}"


def build_nav(lang: str, active: str) -> str:
    ui = UI[lang]
    parts = [f"<nav class='sidebar' aria-label='primary'>"]
    parts.append(
        f"<a class='nav-home' href='{lang_prefix(lang)}/'>{html.escape(ui['home'])}</a>"
    )
    for section, pages in NAV_SECTIONS:
        parts.append(f"<div class='nav-section'>{html.escape(section.replace('-', ' '))}</div>")
        parts.append("<ul class='nav-list'>")
        for pg in pages:
            rel = f"{section}/{pg}"
            available = find_translation(lang, rel) is not None
            cls = "active" if rel == active else ""
            flag = "" if (available or lang == "en") else f" <span class='fallback-tag' title='{html.escape(ui['partial'])}'>•</span>"
            parts.append(
                f"<li><a class='{cls}' href='{lang_prefix(lang)}/{rel}/'>{html.escape(pg.replace('-', ' '))}</a>{flag}</li>"
            )
        parts.append("</ul>")
    parts.append("</nav>")
    return "\n".join(parts)


def build_header(lang: str) -> str:
    ui = UI[lang]
    lang_links = " ".join(
        f"<a href='{lang_prefix(code)}/{'' if code == 'en' else ''}' lang='{code}' hreflang='{code}'>{html.escape(LANGUAGES[code]['native'])}</a>"
        for code in LANGUAGES
    )
    return f"""<header class='site-header'>
  <div class='brand'>
    <a href='{lang_prefix(lang)}/' class='brand-link'>⚡ Nexus <span class='brand-dim'>Scalp Engine</span></a>
    <span class='brand-badge'>v{PROJECT_VERSION}</span>
  </div>
  <div class='header-actions'>
    <input id='doc-search' class='search' type='search' placeholder='{html.escape(ui["search"])}' aria-label='{html.escape(ui["search"])}' autocomplete='off'>
    <details class='lang-picker'>
      <summary aria-label='{html.escape(ui["language"])}'>🌐 {html.escape(LANGUAGES[lang]["native"])}</summary>
      <div class='lang-menu'>{lang_links}</div>
    </details>
    <a class='repo-link' href='{REPO_URL}'>{html.escape(ui["repo"])}</a>
  </div>
</header>"""


def shell(lang: str, title: str, desc: str, body: str, rel: str, translated: bool) -> str:
    ui = UI[lang]
    direction = LANGUAGES[lang]["dir"]
    edit_url = f"{REPO_URL}/edit/main/docs/{rel}.md" if lang == "en" else (
        f"{REPO_URL}/edit/main/site/content/{lang}/{rel}.md"
    )
    status_flag = "" if (translated or lang == "en") else (
        f"<div class='translation-note'>{html.escape(ui['partial'])} — "
        f"<a href='/{rel}/'>English</a></div>"
    )
    return f"""<!DOCTYPE html>
<html lang='{lang}' dir='{direction}'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>{html.escape(title)} · Nexus Scalp Engine</title>
<meta name='description' content='{html.escape(desc[:150])}'>
<link rel='stylesheet' href='/assets/styles.css'>
<link rel='icon' href='/assets/favicon.svg' type='image/svg+xml'>
</head>
<body>
<a class='skip-link' href='#content'>{html.escape(ui["skip"])}</a>
{build_header(lang)}
<div class='layout'>
{build_nav(lang, rel)}
<main id='content' class='content' tabindex='-1'>
{status_flag}
{body}
<footer class='page-footer'>
  <a href='{edit_url}'>{html.escape(ui["on_github"])}</a> ·
  {html.escape(ui["version"])} {PROJECT_VERSION} ·
  <a href='{PAGES_URL}'>{PAGES_URL}</a>
</footer>
</main>
</div>
<script src='/assets/search.js' defer></script>
</body>
</html>"""


def breadcrumbs(lang: str, rel: str) -> str:
    crumbs: list[str] = []
    acc: list[str] = []
    for part in rel.split("/"):
        acc.append(part)
        crumbs.append(f"<a href='{lang_prefix(lang)}/{'/'.join(acc)}/'>{html.escape(part.replace('-', ' '))}</a>")
    return "<nav class='breadcrumbs' aria-label='breadcrumb'>🏠 / " + " / ".join(crumbs) + "</nav>" if crumbs else ""


def build_search_index(entries: list[dict[str, str]]) -> str:
    slim = [
        {"u": e["url"], "t": e["title"], "l": e["lang"], "x": e["text"][:1200]}
        for e in entries
    ]
    return "window.NEXUS_SEARCH=" + json.dumps(slim, ensure_ascii=False) + ";\n"


def build_404(lang: str = "en") -> str:
    ui = UI[lang]
    body = "<h1>404</h1><p>This documentation page does not exist.</p><p><a href='/'>← Home</a></p>"
    return shell(lang, "404", "Not found", body, "", True).replace(ui["search"], ui["search"])


def main() -> int:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    (OUT_DIR / "assets").mkdir(parents=True)
    (OUT_DIR / "en").mkdir(parents=True)

    src_css = SITE_DIR / "assets" / "styles.css"
    src_js = SITE_DIR / "assets" / "search.js"
    src_icon = SITE_DIR / "assets" / "favicon.svg"
    for src, dst in ((src_css, "styles.css"), (src_js, "search.js"), (src_icon, "favicon.svg")):
        if src.exists():
            shutil.copyfile(src, OUT_DIR / "assets" / dst)

    search_entries: list[dict[str, str]] = []
    built = 0

    en_pages = md_pages_in(DOCS_DIR)
    # Only build pages inside the docs IA tree (skip forensic archive noise).
    ia_prefixes = tuple(
        f"{sec}/" for sec, _ in NAV_SECTIONS
    )
    en_pages = [
        p for p in en_pages
        if p.name == "index.md"
        or p.relative_to(DOCS_DIR).as_posix().startswith(ia_prefixes)
    ]

    for lang in LANGUAGES:
        lang_root = OUT_DIR if lang == "en" else OUT_DIR / lang
        lang_root.mkdir(parents=True, exist_ok=True)
        for page in en_pages:
            rel = strip_md_ext(page, DOCS_DIR)
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
            fm_extra = (
                "" if (translated or lang == "en") else ""
            )
            page_html = shell(lang, title, desc, html_body, rel, translated or lang == "en")
            outpath.write_text(page_html, encoding="utf-8", newline="\n")
            built += 1
            search_entries.append({
                "url": f"{lang_prefix(lang)}/{rel}/",
                "title": f"{title} [{lang}]",
                "lang": lang,
                "text": re.sub(r"<[^>]+>", " ", html_body)[:2000],
            })

    # Root index: redirect-less landing built from EN index page.
    en_index = OUT_DIR / "index" / "index.html"
    if en_index.exists():
        (OUT_DIR / "index.html").write_bytes(en_index.read_bytes())

    # Favicon fallback + 404
    if not (OUT_DIR / "assets" / "favicon.svg").exists():
        (OUT_DIR / "assets" / "favicon.svg").write_text(
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='24' font-size='24'>⚡</text></svg>",
            encoding="utf-8",
        )
    (OUT_DIR / "404.html").write_text(build_404(), encoding="utf-8", newline="\n")
    (OUT_DIR / "search-index.json").write_text(
        json.dumps(search_entries, ensure_ascii=False), encoding="utf-8", newline="\n"
    )
    print(f"BUILT pages={built} langs={len(LANGUAGES)} out={OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
