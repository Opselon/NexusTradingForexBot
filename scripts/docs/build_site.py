"""Build the Nexus Scalp Engine documentation site (GitHub Pages).

Nexus-Docs owns this script. It renders `site/content/<lang>/*.md` (English
source + translations) into a self-contained static site under `site/public/`:

- pure HTML/CSS/vanilla-JS output (no framework, no external runtime deps)
- per-language trees with a global language switcher
- full RTL support for fa/ar (content RTL; code/CLI/paths stay LTR)
- client-side search over a generated JSON index
- responsive layout (sidebar -> mobile drawer), dark/light via prefers-color-scheme
- version + capability status injected from pyproject.toml (no duplicated versions)

Usage:  python scripts/docs/build_site.py [--out site/public]
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "docs"))
import site_config as cfg  # noqa: E402

try:
    import markdown as _markdown  # repo venv has Markdown 3.x
except ImportError:  # pragma: no cover
    _markdown = None

OUT_DEFAULT = REPO_ROOT / "site" / "public"
CONTENT = REPO_ROOT / "site" / "content"

MD_EXT = ["extra", "toc", "sane_lists", "admonition"]
MD_EXT_CFG = {"toc": {"permalink": False}}

# Landing-page id per nav entry -> source content file (per language tree)
NAV_SOURCES = {
    "start": "start",
    "status": "status",
    "architecture": "architecture",
    "research": "research",
    "validation": "validation",
    "roadmap": "roadmap",
    "reference": "reference",
    "contributing": "contributing",
}


def parse_front_matter(text: str) -> tuple[dict, str]:
    """Parse a minimal YAML front-matter block (key: value lines)."""
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
        if m:
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip().strip("'\"")
            body = text[m.end():]
    return meta, body


def md_to_html(text: str) -> str:
    if _markdown is not None:
        return _markdown.markdown(text, extensions=MD_EXT, extension_configs=MD_EXT_CFG)
    raise SystemExit("The 'markdown' package is required to build the site (pip install markdown).")


def repo_version() -> str:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    return m.group(1) if m else "unknown"


CSS = """:root{
  --bg:#0d1117;--bg2:#161b22;--card:#161b22;--fg:#e6edf3;--muted:#8b949e;
  --accent:#4c8dff;--accent2:#8a63d2;--border:#21262d;--ok:#3fb950;--warn:#d29922;
  --code-bg:#0a0d12;--maxw:1180px;
}
@media (prefers-color-scheme: light){
  :root{--bg:#ffffff;--bg2:#f6f8fa;--card:#f6f8fa;--fg:#1f2328;--muted:#59636e;
        --accent:#0969da;--accent2:#8250df;--border:#d0d7de;--ok:#1a7f37;--warn:#9a6700;
        --code-bg:#f6f8fa;}
}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;font-family:-apple-system,'Segoe UI','Noto Sans','Noto Naskh Arabic','Vazirmatn',Roboto,Helvetica,Arial,sans-serif;
     background:var(--bg);color:var(--fg);line-height:1.65;font-size:16px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
code,pre,kbd,samp{font-family:ui-monospace,SFMono-Regular,'Cascadia Code',Consolas,monospace;
     direction:ltr;unicode-bidi:embed;text-align:left}
pre{background:var(--code-bg);border:1px solid var(--border);border-radius:8px;
    padding:14px 16px;overflow-x:auto;font-size:.875rem;line-height:1.55}
code{background:var(--code-bg);border:1px solid var(--border);border-radius:5px;
     padding:.12em .35em;font-size:.875em}
pre code{border:0;padding:0;background:transparent}
table{border-collapse:collapse;width:100%;margin:1.1em 0;font-size:.93rem;display:block;overflow-x:auto}
th,td{border:1px solid var(--border);padding:.5em .7em;text-align:start;vertical-align:top}
th{background:var(--bg2)}
blockquote{margin:1em 0;padding:.7em 1.1em;border-inline-start:4px solid var(--accent);
           background:var(--bg2);border-radius:0 8px 8px 0;color:var(--fg)}
blockquote p{margin:.3em 0}
/* layout */
.topbar{position:sticky;top:0;z-index:50;display:flex;align-items:center;gap:14px;
        padding:10px 20px;background:var(--bg);border-bottom:1px solid var(--border)}
.topbar .brand{font-weight:700;font-size:1.02rem;color:var(--fg);letter-spacing:.3px}
.topbar .brand span{color:var(--accent)}
.topbar .tagline{color:var(--muted);font-size:.82rem;display:none}
@media(min-width:900px){.topbar .tagline{display:inline}}
.topbar .spacer{flex:1}
.langsel{background:var(--bg2);color:var(--fg);border:1px solid var(--border);
         border-radius:7px;padding:5px 8px;font-size:.86rem}
.ghlink{color:var(--fg);font-size:.9rem;border:1px solid var(--border);
        border-radius:7px;padding:5px 10px}
.ghlink:hover{text-decoration:none;border-color:var(--accent)}
.layout{display:flex;max-width:var(--maxw);margin:0 auto;padding:0 16px;gap:28px}
.sidebar{display:none;width:220px;flex-shrink:0;padding:22px 0}
@media(min-width:960px){.sidebar{display:block}}
.sidebar h4{margin:1.2em 0 .4em;font-size:.72rem;letter-spacing:.08em;
            text-transform:uppercase;color:var(--muted)}
.sidebar a{display:block;color:var(--fg);padding:3px 8px;border-radius:6px;font-size:.9rem}
.sidebar a:hover{background:var(--bg2);text-decoration:none}
.content{flex:1;min-width:0;padding:26px 0 80px}
/* mobile nav */
.menubtn{display:inline-flex;background:var(--bg2);border:1px solid var(--border);
         color:var(--fg);border-radius:7px;padding:5px 11px;font-size:.95rem}
@media(min-width:960px){.menubtn{display:none}}
.drawer{display:none;position:fixed;inset:0;z-index:100;background:rgba(0,0,0,.5)}
.drawer.open{display:block}
.drawer nav{position:absolute;top:0;bottom:0;inset-inline-start:0;width:270px;
            background:var(--bg);border-inline-end:1px solid var(--border);
            padding:18px;overflow-y:auto}
.drawer a{display:block;color:var(--fg);padding:9px 10px;border-radius:7px}
.drawer a:hover{background:var(--bg2);text-decoration:none}
/* hero + cards */
.hero{border:1px solid var(--border);border-radius:12px;padding:26px 26px 20px;
      background:linear-gradient(180deg,var(--bg2),var(--bg));margin:18px 0}
.hero h1{margin:.1em 0 .2em;font-size:1.7rem;letter-spacing:.4px}
.hero p.sub{color:var(--muted);margin:.2em 0;font-size:1.02rem}
.badges{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.badge{font-size:.72rem;border:1px solid var(--border);border-radius:20px;
       padding:2px 10px;color:var(--muted);background:var(--bg)}
.badge.ok{color:var(--ok);border-color:var(--ok)}
.badge.warn{color:var(--warn);border-color:var(--warn)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;margin:16px 0}
.card{border:1px solid var(--border);border-radius:10px;padding:14px 16px;background:var(--card)}
.card h3{margin:.1em 0 .3em;font-size:.98rem}
.card p{margin:.2em 0;color:var(--muted);font-size:.88rem}
.card a{font-weight:600}
.callout{border:1px solid var(--border);border-inline-start:4px solid var(--warn);
         background:var(--bg2);border-radius:8px;padding:12px 16px;margin:16px 0}
.callout p{margin:.25em 0}
.statuschip{display:inline-block;font-size:.74rem;border-radius:14px;padding:1px 9px;
            border:1px solid var(--border);margin:1px}
.statuschip.certified,.statuschip.implemented{color:var(--ok);border-color:var(--ok)}
.statuschip.experimental,.statuschip.research{color:var(--accent2);border-color:var(--accent2)}
.statuschip.planned{color:var(--muted)}
/* search */
.searchbox{width:100%;padding:8px 12px;border:1px solid var(--border);border-radius:8px;
           background:var(--bg2);color:var(--fg);font-size:.95rem;margin:8px 0}
#search-results{border:1px solid var(--border);border-radius:8px;margin-bottom:14px;display:none}
#search-results a{display:block;padding:8px 12px;border-bottom:1px solid var(--border)}
#search-results a:last-child{border-bottom:0}
#search-results a:hover{background:var(--bg2);text-decoration:none}
.sr{font-size:.8rem;color:var(--muted)}
footer{border-top:1px solid var(--border);margin-top:40px;padding:18px 20px 40px;
       color:var(--muted);font-size:.85rem}
footer .fl{display:flex;flex-wrap:wrap;gap:14px}
/* RTL */
html[dir="rtl"] body{font-family:'Vazirmatn','Noto Naskh Arabic','Segoe UI',Tahoma,sans-serif}
html[dir="rtl"] pre,html[dir="rtl"] code,html[dir="rtl"] kbd{direction:ltr;unicode-bidi:embed}
html[dir="rtl"] td,html[dir="rtl"] th{text-align:right}
html[dir="rtl"] blockquote{border-inline-start:4px solid var(--accent)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media print{.topbar,.sidebar,.drawer,footer{display:none}}
"""

JS = """
(function(){
  var drawer=document.getElementById('drawer');
  var btn=document.getElementById('menubtn');
  if(btn&&drawer){btn.addEventListener('click',function(){drawer.classList.toggle('open');});
    drawer.addEventListener('click',function(e){if(e.target===drawer)drawer.classList.remove('open');});}
  // copy buttons on code blocks
  document.querySelectorAll('pre').forEach(function(pre){
    var btn=document.createElement('button');
    btn.className='copybtn';btn.type='button';btn.setAttribute('aria-label','Copy code');
    btn.textContent='⧉';
    btn.style.cssText='float:right;margin:6px;padding:2px 8px;font-size:.8rem;border:1px solid var(--border);'+
      'border-radius:6px;background:var(--bg2);color:var(--muted);cursor:pointer';
    btn.addEventListener('click',function(){
      navigator.clipboard.writeText(pre.innerText.replace(/^\\s*⧉\\s*/,'')).then(function(){
        btn.textContent='✓';setTimeout(function(){btn.textContent='⧉';},1200);});
    });
    pre.style.position='relative';pre.appendChild(btn);
  });
  // client-side search
  var input=document.getElementById('searchbox'),res=document.getElementById('search-results'),IDX=null;
  function esc(s){return s.replace(/[.*+?^${}()|[\\]\\\\]/g,'\\\\$&');}
  if(input){
    fetch(REL+'search.json').then(function(r){return r.json();}).then(function(idx){IDX=idx;});
    input.addEventListener('input',function(){
      var q=input.value.trim().toLowerCase();
      if(!res)return;
      if(q.length<2){res.style.display='none';res.innerHTML='';return;}
      if(!IDX)return;
      var hits=[];
      for(var i=0;i<IDX.length&&hits.length<8;i++){
        var p=IDX[i];
        var t=(p.title+' '+p.text).toLowerCase();
        if(q.split(/\\s+/).every(function(w){return t.indexOf(w)>=0;})){
          var pos=t.indexOf(q.split(/\\s+/)[0]);
          var snip=p.text.substr(Math.max(0,pos-40),110);
          hits.push('<a href="'+REL+p.href+'"><div>'+p.title+'</div><div class="sr">…'+snip+'…</div></a>');
        }
      }
      res.innerHTML=hits.join('')||'<div class="sr" style="padding:8px 12px">—</div>';
      res.style.display='block';
    });
  }
})();
"""


def lang_switcher(current: str) -> str:
    opts = []
    for code, meta in cfg.LANGUAGES.items():
        sel = " selected" if code == current else ""
        opts.append(
            f'<option value="{code}"{sel}>{meta["flag"]} {meta["name"]}</option>'
        )
    return (
        '<select class="langsel" id="langsel" aria-label="Language">'
        + "".join(opts)
        + "</select>"
    )


def page_shell(
    *,
    lang: str,
    title: str,
    description: str,
    body_html: str,
    nav_html: str,
    drawer_html: str,
    rel: str,
    extra_head: str = "",
) -> str:
    meta = cfg.LANGUAGES[lang]
    dirattr = f' dir="{meta["dir"]}" lang="{lang}"' if meta["dir"] == "rtl" else f' lang="{lang}"'
    tagline = html.escape(cfg.SITE_TAGLINE.get(lang, cfg.SITE_TAGLINE["en"]))
    js = JS.replace("REL", json.dumps(rel))
    return f"""<!DOCTYPE html>
<html{dirattr}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{html.escape(description)}">
<title>{html.escape(title)} — {cfg.SITE_NAME}</title>
<style>{CSS}</style>
{extra_head}
</head>
<body>
<header class="topbar">
  <button class="menubtn" id="menubtn" aria-label="Menu" aria-expanded="false">☰</button>
  <a class="brand" href="{rel}{lang}/index.html">{cfg.SITE_NAME.replace(" ", "<span> </span>")}</a>
  <span class="tagline">{tagline}</span>
  <span class="spacer"></span>
  {lang_switcher(lang)}
  <a class="ghlink" href="{cfg.REPO_URL}" title="GitHub repository">GitHub ↗</a>
</header>
<div class="drawer" id="drawer" role="dialog" aria-label="Navigation"><nav>{drawer_html}</nav></div>
<div class="layout">
  <aside class="sidebar" aria-label="Documentation navigation">{nav_html}</aside>
  <main class="content" id="main">
    <input class="searchbox" id="searchbox" type="search"
           placeholder="{'جستجو…' if lang == 'fa' else 'Buscar…' if lang == 'es' else 'بحث…' if lang == 'ar' else 'Suche…' if lang == 'de' else 'Search docs…'}"
           aria-label="Search documentation">
    <div id="search-results"></div>
{body_html}
  </main>
</div>
<footer>
  <div class="fl">
    <a href="{cfg.REPO_URL}">GitHub</a>
    <a href="{cfg.REPO_URL}/releases">Releases</a>
    <a href="{cfg.REPO_URL}/issues">Issues</a>
    <a href="{rel}{lang}/roadmap.html">Roadmap</a>
    <a href="{rel}{lang}/status.html">Project status</a>
    <span>v{repo_version()} · documentation built from source — statuses are evidence-graded</span>
  </div>
</footer>
<script>{js}</script>
</body>
</html>"""


def render_nav(lang: str, active: str, pages: dict) -> tuple[str, str]:
    """Sidebar + drawer navigation; nav label falls back to English."""
    items = []
    drawer = ['<h4 style="margin-top:0">NEXUS</h4>']
    for pid, labels in cfg.NAV:
        label = labels.get(lang) or labels["en"]
        href = pages.get(pid) or pages.get(f"{pid}@en") or "#"
        cur = ' aria-current="page" style="font-weight:700"' if pid == active else ""
        items.append(f'<a href="{href}"{cur}>{html.escape(label)}</a>')
        drawer.append(f'<a href="{href}">{html.escape(label)}</a>')
    drawer.append('<h4>Repository</h4><a href="' + cfg.REPO_URL + '/releases">Releases ↗</a>'
                  '<a href="' + cfg.REPO_URL + '/issues">Issues ↗</a>')
    return "\n".join(items), "\n".join(drawer)


def build_language_tree(pages_by_lang: dict[str, dict[str, dict]]) -> None:
    pass  # placeholder replaced in main flow


def main() -> int:
    out_dir = OUT_DEFAULT
    argv = sys.argv[1:]
    if "--out" in argv:
        out_dir = Path(argv[argv.index("--out") + 1])

    # 1. Load all content: site/content/<lang>/<page>.md
    trees: dict[str, dict[str, dict]] = {}
    for lang in cfg.LANGUAGES:
        trees[lang] = {}
        ldir = CONTENT / lang
        if not ldir.exists():
            continue
        for md in sorted(ldir.glob("*.md")):
            pid = md.stem
            meta, body = parse_front_matter(md.read_text(encoding="utf-8"))
            trees[lang][pid] = {
                "meta": meta,
                "body": body,
                "title": meta.get("title", pid),
                "desc": meta.get("description", cfg.SITE_TAGLINE.get(lang, "")),
            }

    en = trees.get(cfg.SOURCE_LANG, {})
    if "start" not in en:
        print("ERROR: site/content/en/start.md missing — nothing to build", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    search_index: list[dict] = []
    pages_by_lang: dict[str, dict[str, str]] = {lang: {} for lang in cfg.LANGUAGES}

    # First pass: resolve URLs for cross-links (nav + language switcher)
    for lang in cfg.LANGUAGES:
        for pid in trees[lang]:
            pages_by_lang[lang][pid] = f"../{lang}/{pid}.html"

    def resolve(page_id: str, lang: str) -> str:
        """URL of page_id in lang, falling back to English with the same rel depth."""
        if page_id in trees[lang]:
            return f"{page_id}.html"
        if page_id in en:
            return f"../en/{page_id}.html"
        return "index.html"

    # Second pass: render every page in every language
    for lang, pages in trees.items():
        ldir = out_dir / lang
        ldir.mkdir(parents=True, exist_ok=True)
        meta_cfg = cfg.LANGUAGES[lang]
        for pid, page in pages.items():
            # Translation fallback: missing pages in non-source languages render
            # the English body with a bilingual notice (never a 404).
            notice = ""
            body = page["body"]
            if lang != cfg.SOURCE_LANG and pid not in en:
                notice = ""
            if lang != cfg.SOURCE_LANG and pid in en and page["meta"].get("translation-status") != "complete":
                en_title = en[pid]["title"]
                notice = f'<div class="callout"><p>🇬🇧 This page shows the English source — {meta_cfg["name"]} translation in progress. <a href="../en/{pid}.html">Open English original</a>.</p></div>'
            nav_html, drawer_html = render_nav(lang, pid, pages_by_lang[lang])
            body_html = (
                f'<section class="hero"><h1>{html.escape(page["title"])}</h1>'
                f'<p class="sub">{html.escape(page["desc"])}</p>'
                f'<div class="badges"><span class="badge ok">v{repo_version()}</span>'
                f'<span class="badge">{lang.upper()} · {meta_cfg["dir"].upper()}</span>'
                f'<span class="badge">evidence-graded documentation</span></div></section>'
                + notice
                + md_to_html(body)
            )
            html_text = page_shell(
                lang=lang,
                title=page["title"],
                description=page["desc"],
                body_html=body_html,
                nav_html=nav_html,
                drawer_html=drawer_html,
                rel="../",
            )
            (ldir / f"{pid}.html").write_text(html_text, encoding="utf-8")
            search_index.append(
                {
                    "href": f"{lang}/{pid}.html",
                    "title": f"{page['title']} ({meta_cfg['name']})",
                    "text": re.sub(r"\s+", " ", re.sub(r"[#*`>|{}\[\]]", " ", body))[:1200],
                }
            )

    # Root redirect -> English start page
    (out_dir / "index.html").write_text(
        f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0; url=en/start.html">'
        f'<link rel="canonical" href="{cfg.PAGES_URL}/en/start.html">'
        f"<title>{cfg.SITE_NAME}</title></head><body>"
        f'<p>Redirecting to <a href="en/start.html">the documentation</a>…</p></body></html>',
        encoding="utf-8",
    )

    (out_dir / "search.json").write_text(
        json.dumps(search_index, ensure_ascii=False), encoding="utf-8"
    )
    # nojekyll: serve everything as plain static files
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    total_pages = sum(len(p) for p in trees.values())
    print(f"Site built: {total_pages} pages across {len(trees)} languages -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
