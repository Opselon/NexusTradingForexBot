"""NEXUS documentation site generator — v3 (ALL page links depth-relative,
full per-language page trees, upgraded UI/UX).

Builds the static multilingual GitHub Pages site from docs/ + site/content/
into site/_site/ (the deploy root uploaded by docs.yml).

v3 design (fixes the live 404 defect class COMPLETELY):

- LINK LAW: GitHub project pages are served under /<repo>/. ANY link that
  starts with '/' resolves OUTSIDE the deployment and 404s. Therefore EVERY
  internal link on EVERY page — nav, language switcher, breadcrumbs,
  prev/next, homepage cards, capability rows, What's New, 404 actions, links
  inside translated markdown — is built DEPTH-RELATIVE via
  page_href(rel, lang, from_rel). Assets already followed this rule; v3
  applies it to all page URLs. Sitemap/canonical/OG keep absolute URLs (they
  are host-scoped by definition).
- FULL LANGUAGE TREES: every language builds EVERY page (a translated page
  uses its translation; a missing one uses the English source and shows a
  clear "English source" notice). No dead nav entries in any language.
- UPgraded UI/UX: section landing pages (each sidebar section head is a real
  page with cards), full multi-column section grid on the homepage, richer
  hero + What's New + timeline, card hover states, sticky header with
  backdrop blur, copy buttons on code blocks.

Zero external dependencies (stdlib only). Deterministic output.
Never modify anything under src/ or Web/ -- docs-only surface (Nexus-Docs role).
"""

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
LOCALES_DIR = SITE_DIR / "locales"


def _load_locales() -> dict[str, dict]:
    """P0.2 translation contract: site/locales/<lang>/ui.json is the SINGLE
    source of truth for every user-visible generated string."""
    locales: dict[str, dict] = {}
    for lang in ("en", "fa", "ar", "es", "de"):
        f = LOCALES_DIR / lang / "ui.json"
        locales[lang] = json.loads(f.read_text(encoding="utf-8"))
    return locales


LOCALES = _load_locales()


def t(lang: str, key: str) -> str:
    """Translation lookup with dotted keys. Fallback chain (explicit):
    lang value -> en value of the same key -> last path segment prettified.
    The final fallback keeps unlisted IA pages (e.g. forensic extras) from
    raising while still preferring real translations everywhere else."""

    def lookup(l: str) -> str | None:
        node: dict = LOCALES.get(l, LOCALES["en"])
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return str(node) if not isinstance(node, dict) else None

    value = lookup(lang)
    if value is None:
        value = lookup("en")
    if value is None:
        value = key.rsplit(".", maxsplit=1)[-1].replace("-", " ").replace("_", " ")
    return value


LANGUAGES: dict[str, dict[str, str]] = {
    lang: {
        "name": LOCALES[lang]["meta"]["name"],
        "dir": LOCALES[lang]["meta"]["dir"],
        "native": LOCALES[lang]["meta"]["native"],
    }
    for lang in ("en", "fa", "ar", "es", "de")
}


def UI(lang: str) -> dict[str, str]:
    """UI chrome strings resolved through the locale contract (P0.2)."""
    return LOCALES[lang]["ui"]


SECTION_TITLES_KEYS = {
    "getting-started": "nav.getting-started",
    "project": "nav.project",
    "architecture": "nav.architecture",
    "research": "nav.research",
    "engineering": "nav.engineering",
    "guides": "nav.guides",
    "contributing": "nav.contributing",
    "reference": "nav.reference",
    "releases": "nav.releases",
    "docs-hub": "pages.docs-hub",
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
    "docs-hub": {
        "en": "Docs Hub",
        "fa": "مرکز مستندات",
        "es": "Centro",
        "ar": "مركز التوثيق",
        "de": "Doku-Hub",
    },
}

SECTION_INTROS: dict[str, dict[str, str]] = {
    "getting-started": {
        "en": "Install, run and configure the engine. PAPER mode is the default; SHADOW gives you live data with zero order authority.",
        "fa": "نصب، اجرا و پیکربندی موتور. حالت PAPER پیش‌فرض است؛ SHADOW داده زنده با صفر اختیار سفارش می‌دهد.",
        "es": "Instala, ejecuta y configura el motor. PAPER es el modo por defecto; SHADOW da datos en vivo sin autoridad de órdenes.",
        "ar": "تثبيت المحرك وتشغيله وضبطه. وضع PAPER هو الافتراضي؛ وSHADOW يعطيك بيانات حية دون صلاحية أوامر.",
        "de": "Engine installieren, ausführen und konfigurieren. PAPER ist der Standard; SHADOW liefert Live-Daten ohne Order-Autorität.",
    },
    "project": {
        "en": "What Nexus is, what is certified vs experimental, where it is going — all evidence-graded.",
        "fa": "نکسوس چیست، چه چیزی گواهی‌شده و چه چیزی آزمایشی است، و به کجا می‌رود — همه با درجه‌بندی شواهد.",
        "es": "Qué es Nexus, qué está certificado vs experimental y hacia dónde va — todo graduado por evidencia.",
        "ar": "ما هو Nexus، وما هو مُصادق مقابل تجريبي، وإلى أين يتجه — كلها مدرجة بالأدلة.",
        "de": "Was Nexus ist, was zertifiziert vs. experimentell ist und wohin es geht — evidenzbasiert.",
    },
    "architecture": {
        "en": "How the engine is built: hexagonal layers, the tick-to-decision path, model governance, and the intelligence loop.",
        "fa": "موتور چگونه ساخته شده: لایه‌های شش‌ضلعی، مسیر تیک تا تصمیم، حکمرانی مدل و حلقه هوش.",
        "es": "Cómo está construido el motor: capas hexagonales, el camino del tick a la decisión, gobernanza de modelos y el bucle de inteligencia.",
        "ar": "كيف بُني المحرك: الطبقات السداسية، مسار التيك إلى القرار، حوكمة النماذج، وحلقة الذكاء.",
        "de": "Wie die Engine gebaut ist: hexagonale Schichten, der Tick-zu-Entscheidung-Pfad, Modell-Governance und die Intelligenzschleife.",
    },
    "research": {
        "en": "How historical data becomes falsifiable evidence — datasets, backtests, walk-forward, OOS, replay, counterfactuals.",
        "fa": "داده تاریخی چگونه به شواهد ابطال‌پذیر تبدیل می‌شود — دیتاست، بک‌تست، walk-forward، OOS، بازپخش، خلاف‌واقع.",
        "es": "Cómo los datos históricos se convierten en evidencia falsable — datasets, backtests, walk-forward, OOS, replay, contrafactuales.",
        "ar": "كيف تتحول البيانات التاريخية إلى أدلة قابلة للتكذيب — بيانات، اختبار رجعي، walk-forward، OOS، إعادة تشغيل، مضاد للواقع.",
        "de": "Wie historische Daten zu falsifizierbarer Evidenz werden — Datensätze, Backtests, Walk-Forward, OOS, Replay, Kontrafaktische.",
    },
    "engineering": {
        "en": "Quality gates, CI architecture, the release process, and the security posture.",
        "fa": "گیت‌های کیفیت، معماری CI، فرایند انتشار و وضعیت امنیتی.",
        "es": "Puertas de calidad, arquitectura de CI, proceso de release y postura de seguridad.",
        "ar": "بوابات الجودة، بنية CI، عملية الإصدار، ووضعية الأمان.",
        "de": "Qualitäts-Gates, CI-Architektur, Release-Prozess und Sicherheitspostur.",
    },
    "guides": {
        "en": "Operating the engine day to day: CLI, REST API, troubleshooting, common workflows.",
        "fa": "کار روزانه با موتور: CLI، REST API، عیب‌یابی، گردش‌کارهای رایج.",
        "es": "Operar el motor día a día: CLI, API REST, solución de problemas, flujos comunes.",
        "ar": "تشغيل المحرك يوميًا: CLI، واجهة REST API، استكشاف الأخطاء، سير العمل الشائعة.",
        "de": "Die Engine im Alltag bedienen: CLI, REST-API, Fehlerbehebung, typische Workflows.",
    },
    "contributing": {
        "en": "The engineering contract, documentation ownership, and how to add a language.",
        "fa": "قرارداد مهندسی، مالکیت مستندات و نحوه افزودن زبان جدید.",
        "es": "El contrato de ingeniería, la propiedad de la documentación y cómo añadir un idioma.",
        "ar": "العقد الهندسي، ملكية الوثائق، وكيفية إضافة لغة جديدة.",
        "de": "Der Ingenieursvertrag, die Dokumentationsverantwortung und wie man eine Sprache hinzufügt.",
    },
    "reference": {
        "en": "CLI reference, glossary, terminology, and honest answers in the FAQ.",
        "fa": "مرجع CLI، واژه‌نامه، اصطلاحات و پاسخ‌های صادقانه در پرسش‌های متداول.",
        "es": "Referencia CLI, glosario, terminología y respuestas honestas en las FAQ.",
        "ar": "مرجع CLI، المسرد، المصطلحات، وإجابات صادقة في الأسئلة الشائعة.",
        "de": "CLI-Referenz, Glossar, Terminologie und ehrliche Antworten in den FAQ.",
    },
    "releases": {
        "en": "Every release with real highlights, derived from GitHub release metadata.",
        "fa": "هر انتشار با هایلایت‌های واقعی، از فراداده انتشارهای گیت‌هاب.",
        "es": "Cada versión con sus novedades reales, derivadas de los metadatos de GitHub.",
        "ar": "كل إصدار مع أبرز ملامحه الحقيقية، مستمدة من بيانات إصدارات GitHub.",
        "de": "Jedes Release mit echten Highlights, abgeleitet aus GitHub-Release-Metadaten.",
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
PAGE_TITLES: dict[str, str] = {}  # filled during build for section landings


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


# --------------------------------------------------------------------------
# URL LAW: everything internal is depth-relative. These helpers are the ONLY
# sanctioned ways to build internal URLs.
# --------------------------------------------------------------------------
def rel_base(from_rel: str, lang: str = "en") -> str:
    """Relative prefix from a BUILT page back to the site root.
    The built path of a non-English page has ONE extra level (the /<lang>/
    prefix), which counts toward depth: fa/project/status -> '../../../'."""
    depth = len([p for p in from_rel.split("/") if p])
    if lang != "en":
        depth += 1
    return "../" * depth


def page_href(target_rel: str, lang: str, from_rel: str, from_lang: str = "") -> str:
    """Depth-relative href from page from_rel (built in from_lang) to
    target_rel (in lang). rel_base is computed from the CURRENT page's
    language (from_lang) — the /<lang>/ prefix of the SOURCE page determines
    its depth, never the target's.

    target_rel '' = the language landing page. The lang prefix is part of the
    target path, so fa/project/status reached from the EN homepage is
    'fa/project/status/'. Examples, from_rel='project/status' in EN targeting
    'architecture/overview': '../../architecture/overview/'. From a FA page at
    fa/project/status targeting fa/architecture/overview:
    '../../architecture/overview/'.
    """
    page_lang = from_lang or lang  # caller passes from_lang when switching langs
    if lang != "en":
        target_rel = f"{lang}/{target_rel}" if target_rel else lang
    if not target_rel:
        return rel_base(from_rel, page_lang)
    return rel_base(from_rel, page_lang) + target_rel.rstrip("/") + "/"


def asset_href(from_rel: str, name: str, lang: str = "en") -> str:
    return rel_base(from_rel, lang) + "assets/" + name


def abs_url(rel: str, lang: str) -> str:
    """Absolute URL (host-scoped: sitemap/canonical/OG/search index only)."""
    prefix = "" if lang == "en" else f"/{lang}"
    return f"{prefix}/{rel}/" if rel else f"{prefix}/"


def render_markdown(src: str, from_rel: str, lang: str) -> str:
    """Deterministic markdown renderer. Markdown links that start with '/' are
    treated as site-root page ids and REWRITTEN depth-relative (link law)."""
    out: list[str] = []
    lines = src.splitlines()
    i = 0
    in_code = False
    code_buf: list[str] = []
    list_stack: list[str] = []
    table_buf: list[str] = []

    def esc(s: str) -> str:
        return html.escape(s, quote=False)

    def fix_target(t: str) -> str:
        if t.startswith("/"):
            return page_href(t.strip("/"), lang, from_rel)
        return t

    def inline(s: str) -> str:
        s = esc(s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)

        def repl(m: re.Match) -> str:
            return f'<a href="{fix_target(m.group(2))}">{m.group(1)}</a>'

        s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", repl, s)
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
                cls = "code-ltr has-copy"
                out.append(
                    f"<div class='codeblock'><button class='copy-btn' type='button' aria-label='Copy code'>⧉</button>"
                    f"<pre dir='ltr' class='{cls}'><code>{html.escape(chr(10).join(code_buf))}</code></pre></div>"
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
                f"<div class='callout-title'>{esc(t(lang, 'ui.callout_' + kind) if LOCALES[lang]['ui'].get('callout_' + kind) else kind.upper())}</div>"
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
    """Locate the best translation source for rel; English is the fallback."""
    if lang == "en":
        return DOCS_DIR / f"{rel}.md"
    cand = CONTENT_DIR / lang / f"{rel}.md"
    if cand.exists():
        return cand
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


def section_title(section: str, lang: str) -> str:
    key = SECTION_TITLES_KEYS.get(section)
    return t(lang, key) if key else section


def page_title_key(page: str) -> str:
    return "pages." + page


def section_intro(section: str, lang: str) -> str:
    return t(lang, "sections." + section)


def build_nav(lang: str, active: str, from_rel: str) -> str:
    """Sidebar; every href depth-relative. Section heads link to section
    landing pages (built for every language)."""
    parts = ["<nav class='sidebar' id='sidebar' aria-label='primary'>"]
    parts.append(
        f"<a class='nav-home' href='{page_href('', lang, from_rel)}'>{html.escape(t(lang, 'ui.home'))}</a>"
    )
    for section, pages in NAV_SECTIONS:
        sec_cls = "active" if active.startswith(section) else ""
        parts.append(
            f"<div class='nav-section'><a class='{sec_cls}' href='{page_href(section + '/', lang, from_rel)}'>"
            f"{html.escape(section_title(section, lang))}</a></div>"
        )
        parts.append("<ul class='nav-list'>")
        for pg in pages:
            rel = f"{section}/{pg}"
            translated = find_translation(lang, rel) is not None
            cls = "active" if rel == active else ""
            flag = (
                ""
                if (translated or lang == "en")
                else " <span class='fallback-tag' title='English source'>EN</span>"
            )
            parts.append(
                f"<li><a class='{cls}' href='{page_href(rel, lang, from_rel)}'>{html.escape(t(lang, 'pages.' + pg))}</a>{flag}</li>"
            )
        parts.append("</ul>")
    parts.append(
        f"<div class='nav-section'><a class='{'active' if active == 'releases' else ''}' "
        f"href='{page_href('releases/', lang, from_rel)}'>{html.escape(section_title('releases', lang))}</a></div>"
    )
    parts.append(
        "<ul class='nav-list'><li><a href='"
        + page_href("releases/", lang, from_rel)
        + f"'>v{PROJECT_VERSION} &amp; {html.escape(t(lang, 'ui.released_history'))}</a></li></ul>"
    )
    parts.append("</nav>")
    return "\n".join(parts)


def lang_switcher(lang: str, rel: str, from_rel: str) -> str:
    """Language switcher keeping the current page (every language has every
    page in v3), falling back to the landing only if something is missing."""
    links = []
    for code in LANGUAGES:
        if code == lang:
            links.append(
                f"<span class='lang-current' lang='{code}'>{html.escape(LANGUAGES[code]['native'])} ✓</span>"
            )
            continue
        keep = find_translation(code, rel) is not None if rel else True
        # from_lang=lang: depth is computed from the CURRENT page's language
        target_rel = rel
        if code == "en" and rel in ("docs-hub", "index"):
            target_rel = ""  # EN hub content lives at the site root
        href = (
            page_href(target_rel, code, from_rel, from_lang=lang)
            if (keep and rel)
            else page_href("", code, from_rel, from_lang=lang)
        )
        title = "" if keep else " title='Landing page — translation not built'"
        links.append(
            f"<a href='{href}' lang='{code}' hreflang='{code}'{title}>{html.escape(LANGUAGES[code]['native'])}</a>"
        )
    return "<div class='lang-menu'>" + " ".join(links) + "</div>"


def build_header(lang: str, rel: str) -> str:
    ui = UI(lang)
    from_rel = rel
    return f"""<header class='site-header'>
  <button class='nav-toggle' id='nav-toggle' aria-label='{html.escape(ui["menu"])}' aria-expanded='false' aria-controls='sidebar'>
    <span></span><span></span><span></span>
  </button>
  <div class='brand'>
    <a href='{page_href("", lang, from_rel)}' class='brand-link'>⚡ Nexus <span class='brand-dim'>Scalp Engine</span></a>
    <span class='brand-badge'>v{PROJECT_VERSION}</span>
  </div>
  <div class='header-actions'>
    <input id='doc-search' class='search' type='search' placeholder='{html.escape(ui["search"])}' aria-label='{html.escape(ui["search"])}' autocomplete='off'>
    <details class='theme-picker'>
      <summary aria-label='{html.escape(ui["theme"])}' title='{html.escape(ui["theme"])}'>◐</summary>
      <div class='lang-menu theme-menu'>
        <button type='button' data-theme-set='light'>☀ {html.escape(ui["theme_light"])}</button>
        <button type='button' data-theme-set='dark'>☾ {html.escape(ui["theme_dark"])}</button>
        <button type='button' data-theme-set='system'>🖥 {html.escape(ui["theme_system"])}</button>
      </div>
    </details>
    <details class='lang-picker'>
      <summary aria-label='{html.escape(ui["language"])}'>🌐 {html.escape(LANGUAGES[lang]["native"])}</summary>
      {lang_switcher(lang, rel, from_rel)}
    </details>
    <a class='repo-link' href='{REPO_URL}'>{html.escape(ui["repo"])}</a>
  </div>
</header>"""


def prev_next(lang: str, rel: str, from_rel: str) -> str:
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
            name = t(lang, "pages." + r.rstrip("/").split("/")[-1])
        return (
            f"<a class='pn {cls}' href='{page_href(r, lang, from_rel)}'>"
            f"<span class='pn-label'>{html.escape(label)}</span>"
            f"<span class='pn-name'>{html.escape(name)}</span></a>"
        )

    return (
        "<nav class='prev-next' aria-label='pagination'>"
        + link(prev_rel, "pn-prev", t(lang, "ui.prev"))
        + link(next_rel, "pn-next", t(lang, "ui.next"))
        + "</nav>"
    )


def breadcrumbs(lang: str, rel: str, from_rel: str) -> str:
    if not rel or rel == "index":
        return ""
    crumbs: list[str] = []
    acc: list[str] = []
    parts = rel.split("/")
    for i, part in enumerate(parts):
        acc.append(part)
        is_last = i == len(parts) - 1
        name = section_title(part, lang) if i == 0 else t(lang, "pages." + part)
        if is_last:
            crumbs.append(
                f"<span class='crumb-current' aria-current='page'>{html.escape(name)}</span>"
            )
        else:
            crumbs.append(
                f"<a href='{page_href('/'.join(acc) + '/', lang, from_rel)}'>{html.escape(name)}</a>"
            )
    return "<nav class='breadcrumbs' aria-label='breadcrumb'>" + " / ".join(crumbs) + "</nav>"


def shell(lang: str, title: str, desc: str, body: str, rel: str, translated: bool) -> str:
    ui = UI(lang)
    direction = LANGUAGES[lang]["dir"]
    if lang == "en":
        src_rel = f"docs/{rel}.md" if rel else "docs/index.md"
    else:
        src = find_translation(lang, rel) if rel else None
        src_rel = src.relative_to(REPO_ROOT).as_posix() if src else f"site/content/{lang}/{rel}.md"
    edit_url = f"{REPO_URL}/edit/main/{src_rel}"
    canonical = f"{PAGES_URL}{abs_url(rel, lang)}"
    locale_json = json.dumps(LOCALES[lang]["ui"], ensure_ascii=False)
    lang_json = json.dumps(lang)
    hreflang_links = (
        " ".join(
            f"<link rel='alternate' hreflang='{code}' href='{PAGES_URL}{abs_url(rel, code)}'>"
            for code in LANGUAGES
        )
        + f"<link rel='alternate' hreflang='x-default' href='{PAGES_URL}{abs_url(rel, 'en')}'>"
    )
    jsonld = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "TechArticle" if rel not in ("", "docs-hub") else "WebSite",
            "headline": title,
            "description": desc[:200],
            "inLanguage": lang,
            "url": canonical,
            "author": {"@type": "Organization", "name": "Nexus Scalp Engine"},
            "publisher": {"@type": "Organization", "name": "Nexus Scalp Engine"},
        },
        ensure_ascii=False,
    )
    status_flag = (
        ""
        if (translated or lang == "en")
        else (
            f"<div class='translation-note'>{html.escape(ui['partial'])} — "
            f"<a href='{page_href(rel, 'en', rel)}'>{html.escape(ui['english'])}</a></div>"
        )
    )
    return f"""<!DOCTYPE html>
<html lang='{lang}' dir='{direction}'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>{html.escape(title)} · {html.escape(t(lang, "ui.brand"))} {html.escape(t(lang, "ui.brand_dim"))}</title>
<meta name='description' content='{html.escape(desc[:150])}'>
<link rel='canonical' href='{canonical}'>
<meta property='og:title' content='{html.escape(title)}'>
<meta property='og:type' content='website'>
<meta property='og:url' content='{canonical}'>
<meta property='og:site_name' content='Nexus Scalp Engine'>
<meta name='twitter:card' content='summary'>
<meta name='twitter:title' content='{html.escape(title)}'>
<meta name='twitter:description' content='{html.escape(desc[:150])}'>
{hreflang_links}
<script type='application/ld+json'>{jsonld}</script>
<link rel='stylesheet' href='{asset_href(rel, "styles.css", lang)}'>
<link rel='icon' href='{asset_href(rel, "favicon.svg", lang)}' type='image/svg+xml'>
</head>
<body>
<div class='reading-progress' id='reading-progress' aria-hidden='true'></div>
<a class='skip-link' href='#content'>{html.escape(ui["skip"])}</a>
{build_header(lang, rel)}
<div class='layout'>
{build_nav(lang, rel, rel)}
<main id='content' class='content' tabindex='-1'>
{status_flag}
{body}
{prev_next(lang, rel, rel)}
<footer class='page-footer'>
  <a href='{edit_url}'>{html.escape(ui["on_github"])}</a> ·
  {html.escape(ui["version"])} v{PROJECT_VERSION} · rev <a href='{REPO_URL}/commit/{REVISION}'>{REVISION}</a> ·
  <a href='{page_href("releases/", lang, rel)}'>{html.escape(ui["all_releases"])}</a>
</footer>
</main>
</div>
<script>
window.NEXUS_LOCALE = {locale_json};
window.NEXUS_LANG = {lang_json};
(function(){{  /* theme boot: persisted light/dark/system, no FOUC */
  try {{
    var saved = localStorage.getItem("nexus-theme");
    var theme = saved || "system";
    var dark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    document.documentElement.setAttribute("data-theme-pref", theme);
  }} catch (e) {{}}
}})();
</script>
<script src='{asset_href(rel, "search.js", lang)}' defer></script>
</body>
</html>"""


def build_404(lang: str = "en") -> str:
    body = (
        "<div class='nf404'><h1>404</h1>"
        "<p class='nf404-lead'>This documentation page does not exist.</p>"
        "<div class='nf404-actions'>"
        f"<a class='btn btn-primary' href='{page_href('', 'en', '')}'>{html.escape(t('en', 'ui.nf404_home'))}</a> "
        f"<a class='btn' href='{page_href('getting-started/quickstart/', 'en', '')}'>{html.escape(t('en', 'ui.quickstart_label'))}</a> "
        f"<a class='btn' href='{page_href('project/status/', 'en', '')}'>{html.escape(t('en', 'ui.status_label'))}</a> "
        f"<a class='btn' href='{page_href('reference/faq/', 'en', '')}'>{html.escape(t('en', 'ui.faq_label'))}</a>"
        "</div></div>"
    )
    return shell(lang, "404", "Not found", body, "", True)


def load_releases() -> list[dict]:
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
    lines = []
    for ln in (body or "").splitlines():
        m = re.match(r"^[-*]\s+(.{20,240})$", ln.strip())
        if m:
            lines.append(m.group(1))
    return lines[:limit]


def homepage_html(lang: str = "en") -> str:
    """Real homepage. from_rel='' so every href is site-root relative."""
    ui = UI(lang)
    from_rel = ""
    releases = [r for r in load_releases() if not r.get("draft")]
    latest = releases[0] if releases else None
    highlights = release_highlights(latest.get("body", "")) if latest else []
    timeline = "".join(
        f"<a class='tl-item' href='{page_href('releases/', lang, from_rel)}'>"
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
            f"<a class='wn-more' href='{page_href('releases/', lang, from_rel)}'>{html.escape(ui['all_releases'])} →</a></section>"
        )
    pillars = (
        "<section class='pillars'>"
        f"<div class='pillar'><h3>{html.escape(ui['pillar_evidence_t'])}</h3>"
        f"<p>{html.escape(ui['pillar_evidence_b'])}</p></div>"
        f"<div class='pillar'><h3>{html.escape(ui['pillar_safety_t'])}</h3>"
        f"<p>{html.escape(ui['pillar_safety_b'])}</p></div>"
        f"<div class='pillar'><h3>{html.escape(ui['pillar_research_t'])}</h3>"
        f"<p>{html.escape(ui['pillar_research_b'])}</p></div>"
        f"<div class='pillar'><h3>{html.escape(ui['pillar_truth_t'])}</h3>"
        f"<p>{html.escape(ui['pillar_truth_b'])}</p></div>"
        "</section>"
    )
    # capability highlight rows — hrefs via page_href (link law)
    cap_rows = [
        ("architecture/data-flow", ui["cap_engine"], "chip-cert", "CERTIFIED"),
        ("architecture/execution-pipeline", ui["cap_risk"], "chip-cert", "CERTIFIED"),
        ("research/validation", ui["cap_oos"], "chip-cert", "CERTIFIED"),
        ("architecture/model-pipeline", ui["cap_factory"], "chip-impl", "IMPLEMENTED"),
        ("architecture/research-stack", ui["cap_70d"], "chip-exp", "EXPERIMENTAL"),
        ("research/counterfactuals", ui["cap_cf"], "chip-res", "RESEARCH"),
    ]
    caps = (
        "<section class='cap-highlights'><div class='section-head'><h2>🧱 "
        + html.escape(t(lang, "ui.cap_highlights"))
        + "</h2>"
        + f"<a class='wn-more' href='{page_href('project/capabilities/', lang, from_rel)}'>{html.escape(ui['full_matrix'])} →</a></div>"
        + "<div class='table-wrap'><table><thead><tr><th>"
        + html.escape(t(lang, "ui.capability"))
        + "</th><th>"
        + html.escape(t(lang, "ui.status"))
        + "</th></tr></thead><tbody>"
        + "".join(
            f"<tr><td><a href='{page_href(rel, lang, from_rel)}'>{html.escape(name)}</a></td>"
            f"<td><span class='chip {chip}'>{html.escape(t(lang, 'ui.' + label))}</span></td></tr>"
            for rel, name, chip, label in cap_rows
        )
        + "</tbody></table></div></section>"
    )
    # section grid — every section as a card linking to its landing page
    section_icons = {
        "getting-started": "🚀",
        "project": "📌",
        "architecture": "🗺️",
        "research": "🔬",
        "engineering": "🧪",
        "guides": "🧭",
        "contributing": "🤝",
        "reference": "📚",
    }
    grid = "".join(
        f"<a class='sec-card' href='{page_href(sec + '/', lang, from_rel)}'>"
        f"<span class='sec-icon'>{icon}</span><h3>{html.escape(section_title(sec, lang))}</h3>"
        f"<p>{html.escape(section_intro(sec, lang))}</p></a>"
        for sec, icon in section_icons.items()
    )
    hero_actions = (
        f"<div class='hero-actions'><a class='btn btn-primary' href='{page_href('getting-started/quickstart/', lang, from_rel)}'>{html.escape(ui['get_started'])}</a>"
        f"<a class='btn' href='{page_href('architecture/overview/', lang, from_rel)}'>{html.escape(ui['view_architecture'])}</a>"
        f"<a class='btn' href='{page_href('project/roadmap/', lang, from_rel)}'>{html.escape(ui['view_roadmap'])}</a>"
        f"<a class='btn btn-ghost' href='{REPO_URL}'>GitHub ↗</a></div>"
    )
    status_line = (
        f"<div class='hero-meta'><span class='chip chip-cert'>v{PROJECT_VERSION} {html.escape(ui['chip_released'])}</span>"
        f"<span class='chip'>{html.escape(ui['chip_rev'])} {REVISION}</span>"
        f"<span class='chip'>{html.escape(ui['chip_research'])}</span></div>"
    )
    body = f"""
<section class='hero'>
  <div class='hero-kicker'>{html.escape(ui["hero_kicker"])}</div>
  <h1>{html.escape(ui["hero_title_a"])} <span class='grad'>{html.escape(ui["hero_title_b"])}</span></h1>
  <p class='hero-sub'>{html.escape(ui["hero_sub"])}</p>
  {status_line}
  {hero_actions}
</section>
{pillars}
{caps}
{whats_new}
<section class='secs'><div class='section-head'><h2>📚 {html.escape(ui["explore_docs"])}</h2></div>
<div class='sec-grid'>{grid}</div></section>
<section class='pipe-block'><div class='section-head'><h2>⚙️ {html.escape(t(lang, "ui.how_it_works"))}</h2></div>
<div class='pipe'>
<a class='pipe-node' href='{page_href("architecture/data-flow/", lang, from_rel)}'>DATA → FEATURES</a>
<a class='pipe-node' href='{page_href("architecture/model-pipeline/", lang, from_rel)}'>MODEL</a>
<a class='pipe-node' href='{page_href("guides/api/", lang, from_rel)}'>STRATEGY / API</a>
<a class='pipe-node' href='{page_href("architecture/execution-pipeline/", lang, from_rel)}'>RISK</a>
<a class='pipe-node' href='{page_href("architecture/runtime/", lang, from_rel)}'>EXECUTION</a>
<a class='pipe-node' href='{page_href("architecture/observability/", lang, from_rel)}'>OBSERVABILITY</a>
</div></section>
<section class='timeline-block'><div class='section-head'><h2>🗓️ {html.escape(ui["release_timeline"])}</h2>
<a class='wn-more' href='{page_href("releases/", lang, from_rel)}'>{html.escape(ui["all_releases"])} →</a></div>
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
    ui = UI(lang)
    releases = [r for r in load_releases() if not r.get("draft")]
    blocks = []
    for r in releases:
        highs = release_highlights(r.get("body", ""), limit=8)
        items = "".join(f"<li>{html.escape(h)}</li>" for h in highs) or "<li>—</li>"
        link = f"<a class='wn-more' href='{REPO_URL}/releases/tag/{r['tag_name']}'>{html.escape(ui['github_release'])} ↗</a>"
        blocks.append(
            f"<article class='release-card' id='{html.escape(r['tag_name'])}'>"
            f"<div class='section-head'><h2>{html.escape(r['tag_name'])}</h2>"
            f"<span class='tl-date'>{fmt_release_date(r.get('published_at', ''))}</span></div>"
            f"<ul class='wn-list'>{items}</ul>{link}</article>"
        )
    body = (
        f"<section class='hero'><div class='hero-kicker'>{html.escape(ui['release_history_kicker'])}</div>"
        f"<h1>{html.escape(section_title('releases', lang))}</h1>"
        f"<p class='hero-sub'>{html.escape(ui['releases_intro'])} <code>pyproject.toml</code> (v{PROJECT_VERSION}).</p></section>"
        + (
            "".join(blocks)
            if blocks
            else "<p class='translation-note'>No release metadata available.</p>"
        )
    )
    return shell(
        lang,
        section_title("releases", lang),
        "Release history — Nexus Scalp Engine",
        body,
        "releases",
        lang == "en",
    )


def section_landing_html(lang: str, section: str, pages: list[str]) -> str:
    """Landing page for a sidebar section: intro + card per page."""
    ui = UI(lang)
    from_rel = f"{section}/"
    cards = []
    for pg in pages:
        rel = f"{section}/{pg}"
        # localization order: locale pages.* key -> translated front-matter -> EN
        title = t(lang, "pages." + pg)
        desc = ""
        tpath = find_translation(lang, rel) if lang != "en" else None
        if tpath is not None:
            tfm, tbody = parse_front_matter(tpath.read_text(encoding="utf-8"))
            if tfm.get("description"):
                desc = tfm["description"]
            key_title = LOCALES[lang]["pages"].get(pg)
            title = key_title or page_title(tfm, tbody, title)
        else:
            desc = PAGE_DESCRIPTIONS.get(rel, "")
        translated = find_translation(lang, rel) is not None
        flag = "" if (translated or lang == "en") else " <span class='chip'>EN</span>"
        cards.append(
            f"<a class='sec-card' href='{page_href(rel, lang, from_rel)}'>"
            f"<h3>{html.escape(title)}{flag}</h3><p>{html.escape(desc)}</p></a>"
        )
    body = (
        f"<section class='hero'><div class='hero-kicker'>{html.escape(ui['section'])}</div>"
        f"<h1>{html.escape(section_title(section, lang))}</h1>"
        f"<p class='hero-sub'>{html.escape(section_intro(section, lang))}</p></section>"
        f"<div class='sec-grid sec-grid-wide'>{''.join(cards)}</div>"
    )
    return shell(
        lang,
        section_title(section, lang),
        f"{section_title(section, lang)} — Nexus Scalp Engine",
        body,
        f"{section}/",
        True,
    )


PAGE_DESCRIPTIONS: dict[str, str] = {}


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

    # Collect the EN page inventory (IA tree only) + titles/descriptions
    en_pages = md_pages_in(DOCS_DIR)
    ia_prefixes = tuple(f"{sec}/" for sec, _ in NAV_SECTIONS)
    en_pages = [
        p
        for p in en_pages
        if p.name == "index.md" or p.relative_to(DOCS_DIR).as_posix().startswith(ia_prefixes)
    ]
    for p in en_pages:
        rel = strip_md_ext(p, DOCS_DIR)
        if rel == "index":
            rel = "docs-hub"
        fm, body = parse_front_matter(p.read_text(encoding="utf-8"))
        PAGE_TITLES[rel] = page_title(fm, body, rel)
        PAGE_DESCRIPTIONS[rel] = fm.get("description", "")
    all_rels = [
        strip_md_ext(p, DOCS_DIR) if strip_md_ext(p, DOCS_DIR) != "index" else "docs-hub"
        for p in en_pages
    ]

    for lang in LANGUAGES:
        lang_root = out_dir if lang == "en" else out_dir / lang
        lang_root.mkdir(parents=True, exist_ok=True)

        # Homepage + releases for every language
        (lang_root / "index.html").write_text(homepage_html(lang), encoding="utf-8", newline="\n")
        built += 1
        search_entries.append(
            {
                "url": abs_url("", lang),
                "title": f"Home [{lang}]",
                "lang": lang,
                "text": "Nexus Scalp Engine documentation homepage what's new capabilities architecture research",
            }
        )
        (lang_root / "releases").mkdir(parents=True, exist_ok=True)
        (lang_root / "releases" / "index.html").write_text(
            releases_page_html(lang), encoding="utf-8", newline="\n"
        )
        built += 1
        search_entries.append(
            {
                "url": abs_url("releases", lang),
                "title": f"{section_title('releases', lang)} [{lang}]",
                "lang": lang,
                "text": f"releases v{PROJECT_VERSION} changelog what's new history {REVISION}",
            }
        )

        # EVERY page for EVERY language (translated or English-with-notice)
        # EN skips a separate /docs-hub/ — its hub content IS the root page.
        for rel in all_rels:
            if rel == "docs-hub" and lang == "en":
                continue
            translated = False
            if lang != "en":
                tpath = find_translation(lang, rel)
                if tpath is not None:
                    src_path = tpath
                    translated = True
                elif rel == "docs-hub":
                    src_path = DOCS_DIR / "index.md"  # hub fallback source
                else:
                    src_path = DOCS_DIR / f"{rel}.md"  # English fallback, flagged
            else:
                src_path = DOCS_DIR / f"{rel}.md"
                translated = True
            if not src_path.exists():
                continue
            fm, body = parse_front_matter(src_path.read_text(encoding="utf-8"))
            title = PAGE_TITLES.get(rel) or page_title(fm, body, rel)
            desc = fm.get("description", f"{title} — Nexus Scalp Engine documentation")
            html_body = render_markdown(body, rel, lang)
            crumb = breadcrumbs(lang, rel, rel)
            if crumb:
                html_body = crumb + "\n" + html_body
            outpath = lang_root / rel / "index.html"
            outpath.parent.mkdir(parents=True, exist_ok=True)
            page_html = shell(lang, title, desc, html_body, rel, translated or lang == "en")
            outpath.write_text(page_html, encoding="utf-8", newline="\n")
            built += 1
            search_entries.append(
                {
                    "url": abs_url(rel, lang),
                    "title": f"{title} [{lang}]",
                    "lang": lang,
                    "text": re.sub(r"<[^>]+>", " ", html_body)[:2000],
                }
            )

        # Section landing pages
        for section, pages in NAV_SECTIONS:
            spath = lang_root / section / "index.html"
            spath.parent.mkdir(parents=True, exist_ok=True)
            spath.write_text(
                section_landing_html(lang, section, pages), encoding="utf-8", newline="\n"
            )
            built += 1
            search_entries.append(
                {
                    "url": abs_url(section, lang),
                    "title": f"{section_title(section, lang)} [{lang}]",
                    "lang": lang,
                    "text": section_intro(section, "en"),
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
    (out_dir / "robots.txt").write_text(
        "User-agent: *\nAllow: /\n\nSitemap: " + PAGES_URL + "/sitemap.xml\n",
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
