"""SEC-2/SEC-3 — XSS / HTML-injection hygiene gate for the alt console.

Source-level, dependency-free, deterministic CI gate over the React console in
``frontend/src`` (plus its ``frontend/index.html`` shell). It never imports the
UI, never touches the network, and completes in well under a second.

WHAT IT ENFORCES
================

1.  **No raw-HTML sinks.** ``dangerouslySetInnerHTML``, ``innerHTML``,
    ``outerHTML``, ``insertAdjacentHTML``, ``document.write``/``writeln``,
    ``createContextualFragment``, ``DOMParser`` and the ``srcdoc`` attribute
    are banned outright — the console parses no markup, so if a lane ever
    wants a real DOMParser that is a design review, not a whitelist entry.
    Text may reach the DOM only through React-managed text nodes, which
    escape by construction.
2.  **No code-from-string sinks.** ``eval(``, ``new Function``, string-first
    ``setTimeout``/``setInterval``/``requestAnimationFrame``, ``new Worker``
    with an inline string, and bare ``Function(``.
3.  **URL sinks must be guarded.** Every ``href``/``src``/``srcdoc`` JSX
    attribute, ``.href``/``.src`` assignment, ``setAttribute('href'|'src')``,
    ``window.open()`` and ``location.assign/replace`` whose value is not a
    static string literal must pass through one of the sanctioned guards in
    ``frontend/src/lib/safeHtml.ts`` — ``safeExternalUrl``, ``safeInternalPath``
    or ``isSafeGuardedUrl``. ``href="#"`` and quoted literals are whitelisted.
4.  **The sanitizer exists and behaves.** ``frontend/src/lib/safeHtml.ts`` must
    be present, export the two guards, stay erasable-TypeScript (no enums /
    parameter properties / namespaces), and — when ``node`` is on PATH — the
    guards are executed against a payload battery (``javascript:``,
    ``data:``, ``vbscript:``, obfuscated schemes, protocol-relative paths,
    backslash tricks). Without ``node`` that one behaviour test skips; the
    static assertions still run.
5.  **index.html shell.** No inline ``on*`` handlers, no inline script bodies,
    no external (cross-origin) ``<script src>``. A CSP ``<meta>`` is documented
    as recommended but deliberately NOT asserted (serving headers, not markup,
    are the real CSP authority — see the note in this docstring).

DESIGN NOTES / KNOWN LIMITS (read before "fixing" a red)
========================================================

*   Comments are stripped before matching, so documentation that *names* a
    banned API (the sanitizer's policy header does) is not a violation, while
    code inside a block comment cannot hide a violation either — the strip
    keeps line structure, and the self-test battery below pins both behaviour.
*   The scan is line-based over a regex heuristic, as specified. It therefore
    documents two deliberate exclusions:
    -   React Router ``to={...}`` is NOT gated: a line regex cannot tell the
        static ``NAV_SECTIONS`` literals (AppShell) from a payload-derived
        path, and forcing guards on every nav item buys nothing over the
        guarded ``href`` path. Adding a payload-derived route is the case to
        review by hand.
    -   ``new URL(...)`` / ``URLSearchParams`` construction is not a DOM sink
        and stays clean (client.ts uses both for token handling).
*   ``frontend/src/lib/safeHtml.ts`` is the ONLY file allowed to mention the
    guard names, and it is itself scanned for sinks — an exempt file cannot
    quietly grow an ``href={payload}``.
*   Failure messages list ``path:line: snippet`` for every offender, and an
    inline ``/* xss-gate:allow <reason> */`` marker on the offending line is
    the single auditable escape hatch (it shows up in ``grep 'xss-gate:allow'``
    — use it for a locally-constructed ``blob:`` object URL, never for
    server-supplied data).
*   Mid-flight sibling files are EXPECTED to be caught. A red on code another
    lane is still writing is a feature of this gate, not a false positive to
    negotiate away: fix the sink (route it through ``safeHtml``) or, when the
    value provably never leaves the browser (``URL.createObjectURL``), mark the
    line.

Repo-root resolution: the worktree that contains this test file (parents[2]).
Set ``NSE_FRONTEND_SRC_ROOT`` to point the scan at a different checkout — the
integrator uses that to run the gate against a sibling worktree without
copying files.

Run (repo / worktree root):
  ./.venv/Scripts/python.exe -m pytest tests/unit/test_alt_ui_xss_hygiene.py -p no:cacheprovider -q
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Overridable so the gate can audit another worktree's console without copying.
_SRC_OVERRIDE = os.environ.get("NSE_FRONTEND_SRC_ROOT", "").strip()
FRONTEND_DIR = Path(_SRC_OVERRIDE).resolve().parent if _SRC_OVERRIDE else REPO_ROOT / "frontend"
SRC_DIR = FRONTEND_DIR / "src"
INDEX_HTML = FRONTEND_DIR / "index.html"
SANITIZER = SRC_DIR / "lib" / "safeHtml.ts"

#: Extensions that can carry a DOM sink into the shipped bundle.
SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}

#: Directories that never contain first-party console source.
SKIP_DIR_NAMES = {"node_modules", "dist", "build", "coverage", ".vite", "__pycache__"}

#: The sanctioned URL guards (names are load-bearing: the sink scan whitelists
#: them, and safeHtml.ts exports them).
GUARD_NAMES = ("safeExternalUrl", "safeInternalPath", "isSafeGuardedUrl")

ALLOW_MARKER = "xss-gate:allow"


# ---------------------------------------------------------------------------
# Scanner primitives
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    """One banned construct, located by file/line for the CI report."""

    path: str
    lineno: int
    rule: str
    snippet: str

    def render(self) -> str:
        return f"{self.path}:{self.lineno}: [{self.rule}] {self.snippet}"


def _iter_source_files(root: Path) -> list[Path]:
    """First-party console sources, deterministic order, vendor dirs skipped."""
    if not root.is_dir():
        return []
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        if SKIP_DIR_NAMES.intersection(part.lower() for part in path.parts):
            continue
        found.append(path)
    return found


def _strip_comments(text: str) -> str:
    """Blank out ``//``/``/* */`` comments, preserving line and column shape.

    Quote-aware (``'``, ``"``, backtick), so a URL inside a string literal
    never opens a comment and a policy note in a comment never looks like
    code. Line breaks are kept so violation line numbers stay true.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    state = "code"  # code | line | block | sq | dq | tpl
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line"
                out.append("  ")
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block"
                out.append("  ")
                i += 2
                continue
            if ch == "'":
                state = "sq"
            elif ch == '"':
                state = "dq"
            elif ch == "`":
                state = "tpl"
            out.append(ch)
            i += 1
            continue
        if state == "line":
            if ch == "\n":
                state = "code"
                out.append("\n")
            else:
                out.append(" ")
            i += 1
            continue
        if state == "block":
            if ch == "*" and nxt == "/":
                state = "code"
                out.append("  ")
                i += 2
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue
        # inside a string literal: emit it verbatim, honouring escapes
        quote = {"sq": "'", "dq": '"', "tpl": "`"}[state]
        if ch == "\\":
            out.append(text[i : i + 2])
            i += 2
            continue
        out.append(ch)
        if ch == quote:
            state = "code"
        elif ch == "\n" and state != "tpl":
            state = "code"
        i += 1
    return "".join(out)


def _code_lines(text: str) -> list[str]:
    return _strip_comments(text).splitlines()


# --- rule 1: raw-HTML sinks -------------------------------------------------

RAW_HTML_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("dangerously-set-inner-html", re.compile(r"\bdangerouslySetInnerHTML\b")),
    ("inner-html", re.compile(r"\b(?:innerHTML|outerHTML)\b")),
    ("insert-adjacent-html", re.compile(r"\binsertAdjacentHTML\b")),
    ("document-write", re.compile(r"\bdocument\s*\.\s*(?:write|writeln)\s*\(")),
    ("contextual-fragment", re.compile(r"\bcreateContextualFragment\b")),
    # Bare ``DOMParser`` is banned, not only ``new DOMParser().parseFromString``:
    # the console has no legitimate HTML-parsing use, and a line-based rule that
    # required both tokens on one line would be evaded by a line break (the
    # pattern runs per line, not per file).
    ("domparser", re.compile(r"\bDOMParser\b")),
    ("srcdoc", re.compile(r"\bsrcdoc\s*=")),
)

# --- rule 2: code-from-string sinks ----------------------------------------

EVAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("eval", re.compile(r"\beval\s*\(")),
    ("new-function", re.compile(r"\bnew\s+Function\s*[\(\{]")),
    ("function-constructor", re.compile(r"\bFunction\s*\(\s*[`'\".]")),
    (
        "timer-string-payload",
        re.compile(r"\b(?:setTimeout|setInterval|requestAnimationFrame)\s*\(\s*[`'\"]"),
    ),
    ("worker-inline-string", re.compile(r"\bnew\s+Worker\s*\(\s*[`'\"]")),
)

# --- rule 3: URL sinks ------------------------------------------------------

#: ``href={"literal"}`` / ``src={'/x'}`` / ``{"`/y`"}`` — a static quoted
#: string is trusted markup, exactly as whitelisted by the gate contract.
_STATIC_VALUE = re.compile(r"""^\s*(?:"[^"\\]*"|'[^'\\]*'|`[^`\\$]*`)\s*$""")
_BARE_HASH = re.compile(r"""^\s*['"]#['"]\s*$""")

#: A *static* literal is markup, but an author-typed scriptable scheme in a
#: literal is still an XSS sink — the whitelist never covers that.
#: (``data:image/*`` and ``blob:`` stay allowed: inert, author-visible bytes.)
_LITERAL_SCRIPT_SCHEME = re.compile(
    r"""^\s*(?:javascript|vbscript|livescript|mocha|view-source)\s*:|^\s*data\s*:\s*text/html""",
    re.IGNORECASE,
)


def _static_literal_content(expr: str) -> str | None:
    """Inner text of a fully-static quoted string, else None."""
    e = expr.strip()
    if len(e) >= 2 and e[0] == e[-1] and e[0] in "\"'`" and _STATIC_VALUE.match(e):
        return e[1:-1]
    return None


SINK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # JSX attribute: href={expr} / src={expr} / srcdoc={expr}. Deliberately
    # lazy-terminated so an interpolated template (href={`/x/${payload}`}) is
    # still inspected instead of silently escaping the heuristic.
    (
        "url-sink-jsx",
        re.compile(r"\b(?:href|src|srcdoc)\s*=\s*\{(?P<expr>[^\n]*?)\}(?=[\s/>]|$)"),
    ),
    # JSX attribute written as a plain string: href="https://cdn/…"
    (
        "url-sink-static-attr",
        re.compile(r"\b(?:href|src|srcdoc)\s*=\s*(?P<expr>\"[^\"]*\"|'[^']*')"),
    ),
    # DOM property assignment: anchor.href = url
    (
        "url-sink-property-assign",
        re.compile(r"\.(?:href|src|srcdoc)\s*=(?!=)\s*(?P<expr>[^;,)\n]+)"),
    ),
    # DOM API assignment: el.setAttribute("href", v)
    (
        "url-sink-setattribute",
        re.compile(
            r"""\bsetAttribute\s*\(\s*["'](?:href|src|srcdoc)["']\s*,\s*(?P<expr>[^)]+)\)"""
        ),
    ),
    # navigation sinks: window.open(v) / location.assign(v) / location.replace(v)
    (
        "url-sink-navigation",
        re.compile(
            r"\b(?:window\s*\.\s*open|location\s*\.\s*(?:assign|replace))\s*\(\s*(?P<expr>[^,)\n]+)"
        ),
    ),
    # location.href = v (also covered by property-assign; kept for the report)
    (
        "url-sink-location-href",
        re.compile(r"\blocation\s*\.\s*href\s*=(?!=)\s*(?P<expr>[^;,)\n]+)"),
    ),
)


def _guarded_bindings(code_lines: list[str]) -> set[str]:
    """Identifiers whose assignment in this file provably yields a guarded value.

    Provenance is file-local and cheap: ``const x = safeExternalUrl(v)``,
    ``x = safeInternalPath(v)`` (any assignment whose right-hand side calls a
    sanctioned guard), or ``const x = "/static-literal"``. ``href={x}`` is then
    as safe as ``href={safeExternalUrl(x)}``, which keeps idiomatic code —
    ``const href = safeExternalUrl(link); <a href={href}>`` — out of the
    false-positive column while payload props (``href={item.url}``) still fail.
    """
    safe_set: set[str] = set()
    # The guard must be the OUTERMOST call of the initialiser — ``x = f(...)``
    # — so a ternary that only sometimes guards (``c ? safeExternalUrl(a) : b``)
    # does not launder an unsafe branch. Fail closed is the whole point.
    guard_assign = re.compile(
        r"""(?:\b(?:const|let|var)\s+)?([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*"""
        r"""\(?\s*(?:await\s+)?\s*(?:safeExternalUrl|safeInternalPath|isSafeGuardedUrl)\s*\("""
    )
    literal_assign = re.compile(
        r"""(?:\b(?:const|let|var)\s+)([A-Za-z_$][\w$]*)\s*=\s*("(?:[^"\\]*|\\.)*"|'(?:[^'\\]*|\\.)*'|`[^`$]*`)\s*(?:;|$)"""
    )
    # ``const url = URL.createObjectURL(new Blob([doc], { type: "text/csv" }))``
    # is a browser-issued ``blob:`` of bytes we generated: no attacker-chosen
    # scheme, and it cannot run as a document. Guarded ONLY while the blob's
    # declared MIME is not a document type — ``createObjectURL(new Blob(html,
    # { type: "text/html" }))`` in an href is a live XSS sink, so a line that
    # mentions html/xml/xhtml/svg MIME stays untrusted.
    blob_assign = re.compile(
        r"""(?:\b(?:const|let|var)\s+)?([A-Za-z_$][\w$]*)\s*=[^;\n]*\bURL\.createObjectURL\s*\("""
    )
    blob_document_mime = re.compile(r"""(?i)\b(?:text|application)/(?:html|xml|xhtml|svg)""")
    for line in code_lines:
        for m in guard_assign.finditer(line):
            safe_set.add(m.group(1))
        for m in blob_assign.finditer(line):
            if not blob_document_mime.search(line):
                safe_set.add(m.group(1))
        for m in literal_assign.finditer(line):
            if not m.group(2).startswith("`") or "${" not in m.group(2):
                safe_set.add(m.group(1))
    return safe_set


def _identifiers(expr: str) -> set[str]:
    """Value identifiers in a sink expression (no property access, no calls)."""
    out: set[str] = set()
    for m in re.finditer(r"(?<![.\w$])([A-Za-z_$][\w$]*)", expr):
        token = m.group(1)
        rest = expr[m.end() :]
        if re.match(r"\s*\(", rest):  # function call, not a value
            continue
        out.add(token)
    return out - {"true", "false", "null", "undefined", "String", "encodeURIComponent"}


def _is_guarded(expr: str, safe_vars: set[str]) -> bool:
    """True when the sink value is a static literal, '#', or guard-derived."""
    e = expr.strip()
    if not e:
        return False
    if _BARE_HASH.match(e):
        return True  # href="#" is inert markup
    if _STATIC_VALUE.match(e):
        # a template literal that interpolates is NOT static
        if "${" in e:
            return False
        inner = _static_literal_content(e)
        return inner is None or not _LITERAL_SCRIPT_SCHEME.search(inner)
    if any(re.search(rf"\b{re.escape(g)}\s*\(", e) for g in GUARD_NAMES):
        return True
    idents = _identifiers(e)
    return bool(idents) and idents.issubset(safe_vars)


def _scan_text(rel: str, text: str) -> list[Violation]:
    """All rule violations for one file body (comment-blanked lines)."""
    hits: list[Violation] = []
    raw_lines = text.splitlines()
    code_lines = _code_lines(text)
    safe_vars = _guarded_bindings(code_lines)
    for idx, line in enumerate(code_lines, start=1):
        source_line = raw_lines[idx - 1] if idx - 1 < len(raw_lines) else line
        if ALLOW_MARKER in source_line:
            continue
        for rule, pat in RAW_HTML_PATTERNS:
            if pat.search(line):
                hits.append(Violation(rel, idx, rule, source_line.strip()[:160]))
        for rule, pat in EVAL_PATTERNS:
            if pat.search(line):
                hits.append(Violation(rel, idx, rule, source_line.strip()[:160]))
        for rule, pat in SINK_PATTERNS:
            for m in pat.finditer(line):
                expr = m.groupdict().get("expr")
                if expr is None:  # pattern has no value group (report-only)
                    continue
                if _is_guarded(expr, safe_vars):
                    continue
                hits.append(Violation(rel, idx, rule, source_line.strip()[:160]))
    return hits


def _scan_tree(root: Path) -> list[Violation]:
    hits: list[Violation] = []
    for f in _iter_source_files(root):
        text = f.read_text(encoding="utf-8", errors="replace")
        hits.extend(_scan_text(f.relative_to(root).as_posix(), text))
    return hits


# ---------------------------------------------------------------------------
# index.html shell rules (rule 5)
# ---------------------------------------------------------------------------

_INLINE_HANDLER = re.compile(r"""(?i)\son[a-z]+\s*=\s*["']""")
_SCRIPT_TAG = re.compile(r"(?is)<script\b([^>]*)>(.*?)</script>")
_EXTERNAL_URL = re.compile(r"""(?i)\b(?:https?:)?//""")


def _index_html_violations(path: Path) -> list[Violation]:
    text = path.read_text(encoding="utf-8", errors="replace")
    hits: list[Violation] = []
    lines = text.splitlines()
    for i, line in enumerate(lines, start=1):
        if _INLINE_HANDLER.search(line):
            hits.append(Violation(path.name, i, "inline-event-handler", line.strip()[:160]))
    for m in _SCRIPT_TAG.finditer(text):
        attrs, body = m.group(1), m.group(2)
        lineno = text[: m.start()].count("\n") + 1
        src_match = re.search(r"""(?i)\bsrc\s*=\s*["']([^"']+)["']""", attrs)
        if src_match and (
            _EXTERNAL_URL.search(src_match.group(1)) or src_match.group(1).startswith("//")
        ):
            hits.append(
                Violation(path.name, lineno, "external-script", f"src={src_match.group(1)}")
            )
        if not src_match and body.strip():
            hits.append(Violation(path.name, lineno, "inline-script", body.strip()[:120]))
    return hits


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_00_scan_inventory() -> None:
    """Records what the gate scanned (visible with -q -rA / --tb=short)."""
    assert SRC_DIR.is_dir(), f"missing alt console source tree: {SRC_DIR}"
    files = _iter_source_files(SRC_DIR)
    assert len(files) >= 10, "console tree looks empty/truncated"
    print(f"[xss-gate] scanned {len(files)} sources under {SRC_DIR} @ {INDEX_HTML.name}")


def test_01_alt_console_source_tree_exists() -> None:
    assert SRC_DIR.is_dir(), f"missing alt console source tree: {SRC_DIR}"
    assert INDEX_HTML.is_file(), f"missing console shell: {INDEX_HTML}"
    assert SANITIZER.is_file(), f"missing sanitizer: {SANITIZER}"


def test_02_sanitizer_module_is_present_and_complete() -> None:
    assert SANITIZER.is_file(), (
        "frontend/src/lib/safeHtml.ts is required: it is the only sanctioned "
        "URL guard and the sink scan whitelists its exports by name"
    )
    text = SANITIZER.read_text(encoding="utf-8", errors="replace")
    for fn in ("safeExternalUrl", "safeInternalPath"):
        assert re.search(
            rf"\bexport\s+function\s+{fn}\s*\([^)]*\)\s*:\s*string\s*\|\s*null", text
        ), f"safeHtml.ts must export {fn}(value): string | null (fail-closed return type)"
    # erasable TypeScript: nothing that requires a TS emit step / decorator pass
    for banned_syntax, why in (
        (r"\benum\s+\w+\s*\{", "enums are not erasable syntax"),
        (r"\bnamespace\s+\w+\s*\{", "namespaces are not erasable syntax"),
        (r"\b(?:public|private|readonly)\s+#?\w+\s*[:=]", "parameter properties are not erasable"),
        (r"^\s*import\s+type\s", "explicit type imports should use erasable syntax"),
    ):
        assert not re.search(banned_syntax, text, re.MULTILINE), (
            f"safeHtml.ts uses non-erasable TypeScript ({why})"
        )
    # doc-comment contract: both guards document their rejection rules
    doc = "\n".join(re.findall(r"/\*\*[\s\S]*?\*/", text))
    for token in ("https", "javascript", "data", "vbscript"):
        assert token in doc.lower(), f"safeHtml.ts docs must explain the '{token}' rule"


def test_03_no_raw_html_sinks_in_console_source() -> None:
    """Only React-managed text nodes may write to the DOM (SEC-2)."""
    hits = [v for v in _scan_tree(SRC_DIR) if v.rule in {r for r, _ in RAW_HTML_PATTERNS}]
    assert not hits, "raw-HTML sinks found — render text through React instead:\n" + "\n".join(
        v.render() for v in hits
    )


def test_04_no_code_from_string_sinks_in_console_source() -> None:
    hits = [v for v in _scan_tree(SRC_DIR) if v.rule in {r for r, _ in EVAL_PATTERNS}]
    assert not hits, "code-from-string sinks found:\n" + "\n".join(v.render() for v in hits)


def test_05_url_sinks_are_guarded_or_static() -> None:
    """href/src/navigation values must be literals, '#', or safeHtml-guarded."""
    hits = [v for v in _scan_tree(SRC_DIR) if v.rule in {r for r, _ in SINK_PATTERNS}]
    assert not hits, (
        "unguarded URL sinks found — wrap the value in safeExternalUrl() or "
        "safeInternalPath() (frontend/src/lib/safeHtml.ts), or keep the "
        "attribute a static literal:\n" + "\n".join(v.render() for v in hits)
    )


def test_06_console_shell_has_no_injected_markup() -> None:
    assert INDEX_HTML.is_file(), f"missing {INDEX_HTML}"
    hits = _index_html_violations(INDEX_HTML)
    assert not hits, "index.html shell violations:\n" + "\n".join(v.render() for v in hits)


#: Audited whitelist for router ``to={...}`` sinks (see module docstring): the
#: only file allowed to hand a variable to ``to=`` today, because its routes
#: come from the module-level literal ``NAV_SECTIONS`` registry.
ROUTER_TO_SINK_FILES = {"app/AppShell.tsx"}


def test_06b_router_to_sinks_stay_in_the_audited_registry_files() -> None:
    """``to={variable}`` is the one sink this gate does not guard by value.

    A line regex cannot tell AppShell's static ``NAV_SECTIONS`` literals from a
    payload-derived path, so instead of guessing we pin WHICH files may do it.
    New router sink in a new file = red: either wrap the value in
    ``safeInternalPath()`` (then it belongs in the guarded href rule anyway) or
    add the file to ROUTER_TO_SINK_FILES with a written reason in the PR.
    """
    offenders: list[str] = []
    for f in _iter_source_files(SRC_DIR):
        rel = f.relative_to(SRC_DIR).as_posix()
        if rel in ROUTER_TO_SINK_FILES:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(_code_lines(text), start=1):
            if re.search(r"\bto\s*=\s*\{", line):
                offenders.append(f"{rel}:{i}: {line.strip()[:120]}")
    assert not offenders, (
        "unguarded router to={{...}} sink outside the audited nav registry — "
        "route it through safeInternalPath() or document the addition:\n" + "\n".join(offenders)
    )


def test_07_csp_meta_documented_not_asserted() -> None:
    """Deliberately non-asserting: records whether a CSP meta is present.

    The console is served by ``src/nexus_scalp/web/server.py`` at /alt, where
    response headers are the authoritative CSP surface. Asserting markup here
    would test the weaker of the two, and a `<meta http-equiv>` is ignored by
    modern browsers when the header disagrees — so this test only fails if a
    CSP meta tag appears WITHOUT the documented comment that explains it is
    advisory, which is the drift we actually care about.
    """
    text = INDEX_HTML.read_text(encoding="utf-8", errors="replace")
    if "Content-Security-Policy" not in text:
        return
    assert re.search(r"(?i)(<!--|//|\*).{0,200}advisory", text), (
        "index.html declares a CSP meta tag; add the adjacent comment marking it "
        "advisory (the served header is authoritative) or remove it"
    )


# ---------------------------------------------------------------------------
# Scanner self-test: proves the gate is RED on planted payloads and honest
# about comments (a gate nobody has seen fail is not a gate).
# ---------------------------------------------------------------------------

_PLANTED: dict[str, str] = {
    "Bad1.tsx": """
import React from "react";
export function Bad1({ payload }: { payload: string }) {
  return <div dangerouslySetInnerHTML={{ __html: payload }} />;
}
""",
    "Bad2.ts": """
export function Bad2(el: HTMLElement, html: string) {
  el.innerHTML = html;
  el.insertAdjacentHTML("beforeend", html);
}
""",
    "Bad3.ts": """
export function Bad3(src: string) {
  const a = document.createElement("a");
  a.href = src;               // unguarded payload
  document.write("<b>x</b>");
  setTimeout("doevil()", 10);
  const F = new Function("return 1");
  window.open(src);
  return [a, F];
}
""",
    "Bad4.tsx": """
export function Bad4({ link, icon }: { link: string; icon: string }) {
  return (
    <p>
      <a href={link}>x</a>
      <img src={icon} />
      <a href={link + "#more"}>y</a>
      <a href="javascript:alert(1)">static-but-deadly</a>
      <a href={`https://x/${link}`}>interpolated</a>
    </p>
  );
}
""",
    # A ternary that guards only ONE branch must not launder the identifier:
    # provenance is trusted only when the guard is the outermost call.
    "BadTernary.tsx": """
export function BadTernary({ link, fallback }: { link: string; fallback: string }) {
  const href = link ? safeExternalUrl(link) : fallback;
  return <a href={href}>x</a>;
}
""",
    "BadComment.ts": """
export function BadComment(v: string) {
  /* innerHTML = v;  hidden inside a block comment */
  return v; // document.write(v)
}
eval(v);
""",
    # A blob: object URL whose declared MIME is a DOCUMENT type is an XSS sink
    # even though the URL is browser-issued: the guard whitelist must not
    # launder it (createObjectURL(html) + anchor.href navigates to markup).
    "BadBlobHtml.ts": """
export function openHtml(markup: string) {
  const url = URL.createObjectURL(new Blob([markup], { type: "text/html" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  return anchor;
}
""",
}

_CLEAN: dict[str, str] = {
    "Good1.tsx": """
import { safeExternalUrl, safeInternalPath } from "@/lib/safeHtml";
export function Good1({ link, icon, path }: { link: string; icon: string; path: string }) {
  const href = safeExternalUrl(link);
  const to = safeInternalPath(path);
  return (
    <p>
      <a href={href}>docs</a>
      <a href="#">top</a>
      <a href={"/"}>home</a>
      <img src={"/badge.svg"} alt="" />
      <a href={to}>{to}</a>
      <span>{icon}</span>
    </p>
  );
}
""",
    # guard as the outermost initialiser, or guard sitting AT the sink
    "TernaryGood.tsx": """
export function TernaryGood({ link, icon }: { link: string; icon: string }) {
  const href = safeExternalUrl(link) ?? null;
  return (
    <p>
      <a href={href}>x</a>
      <a href={icon ? safeExternalUrl(icon) : null}>y</a>
    </p>
  );
}
""",
    "BlobExport.ts": """
export function download(doc: string, name: string) {
  const url = URL.createObjectURL(new Blob([doc], { type: "text/csv" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}
""",
    "PolicyNote.ts": """
/**
 * We never assign innerHTML, never call dangerouslySetInnerHTML, and never
 * reach for eval( or new Function — text goes through React only.
 */
export const POLICY = "react-text-only";
""",
}


def test_08_gate_fails_on_planted_payloads(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    for name, body in _PLANTED.items():
        (root / name).write_text(body, encoding="utf-8")
    hits = _scan_tree(root)
    by_rule = {v.rule for v in hits}
    located = {f"{v.path}:{v.lineno}" for v in hits}
    assert "dangerously-set-inner-html" in by_rule, hits
    assert "inner-html" in by_rule and "insert-adjacent-html" in by_rule, hits
    assert "document-write" in by_rule, hits
    assert "eval" in by_rule and "new-function" in by_rule, hits
    assert "timer-string-payload" in by_rule, hits
    assert "url-sink-jsx" in by_rule, hits
    assert "url-sink-property-assign" in by_rule, hits
    assert "url-sink-navigation" in by_rule, hits
    assert "Bad1.tsx:4" in located, located  # dangerouslySetInnerHTML line
    # a quoted literal is whitelisted ONLY when it is not a scriptable scheme
    assert any(v.rule == "url-sink-static-attr" and "javascript:" in v.snippet for v in hits), hits
    assert any("interpolated" in v.snippet for v in hits), hits
    assert any(v.path == "BadTernary.tsx" and v.rule == "url-sink-jsx" for v in hits), (
        "a half-guarded ternary must not launder the href provenance"
    )
    assert not any(v.path == "BlobExport.ts" for v in hits), (
        "browser-issued blob: object URLs are local bytes, not attacker schemes"
    )
    assert any(v.path == "BadBlobHtml.ts" and v.rule == "url-sink-property-assign" for v in hits), (
        "a text/html blob URL is an XSS sink even though it is blob:"
    )
    assert any(v.path == "BadComment.ts" and v.rule == "eval" for v in hits), hits
    assert not any(v.path == "BadComment.ts" and v.rule == "inner-html" for v in hits), (
        "code hidden in a comment must stay a comment-level non-finding, while "
        "real code on its own line must still be caught"
    )
    assert len(hits) >= 10, f"sink scan looks shallow: {len(hits)} hits"


def test_09_gate_accepts_guarded_code(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    for name, body in _CLEAN.items():
        (root / name).write_text(body, encoding="utf-8")
    hits = _scan_tree(root)
    assert not hits, "false positives on guarded/literal code:\n" + "\n".join(
        v.render() for v in hits
    )


def test_10_allow_marker_is_explicit_and_countable() -> None:
    """Escape hatch stays rare and auditable: every use must carry a reason."""
    offenders: list[str] = []
    for f in _iter_source_files(SRC_DIR):
        text = f.read_text(encoding="utf-8", errors="replace")
        rel = f.relative_to(SRC_DIR).as_posix()
        for i, line in enumerate(text.splitlines(), start=1):
            if ALLOW_MARKER in line:
                tail = line.split(ALLOW_MARKER, 1)[1]
                if re.sub(r"[\s/*:]", "", tail).startswith("*/") or len(tail.strip(" */")) < 4:
                    offenders.append(f"{rel}:{i}: {ALLOW_MARKER} without a reason")
    assert not offenders, "inline suppressions need a written reason:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# Behavioural pin for the sanitizer (runs the real TS through node when present)
# ---------------------------------------------------------------------------

_BEHAVIOUR_JS = r"""
import { safeExternalUrl, safeInternalPath, isSafeGuardedUrl } from %s;
const out = [];
const bad = (n, v) => { if (v !== null) out.push(`${n} expected null, got ${JSON.stringify(v)}`); };
const good = (n, v) => { if (v === null) out.push(`${n} rejected a safe value`); };

good("ext-https", safeExternalUrl("https://docs.example.com/a?b=1#c"));
bad("ext-http", safeExternalUrl("http://example.com"));
bad("ext-js", safeExternalUrl("javascript:alert(1)"));
bad("ext-js-ctrl", safeExternalUrl("java\tscript:alert(1)"));
bad("ext-data", safeExternalUrl("data:text/html;base64,PHNjcmlwdD4="));
bad("ext-vb", safeExternalUrl("vbscript:msgbox(1)"));
bad("ext-relative", safeExternalUrl("/not-external"));
bad("ext-junk", safeExternalUrl("not a url"));
bad("ext-no-host", safeExternalUrl("https://"));
bad("ext-nonstring", safeExternalUrl({ toString: () => "https://evil.example" }));
const ok = safeExternalUrl("https://docs.example.com/x");
if (ok !== null && !ok.startsWith("https://")) bad("ext-normalize", ok);

good("path-root", safeInternalPath("/trading"));
good("path-query", safeInternalPath("/audit?ticket=7"));
bad("path-proto-rel", safeInternalPath("//evil.example/x"));
bad("path-backslash", safeInternalPath("\\\\evil.example"));
bad("path-js", safeInternalPath("javascript:alert(1)"));
bad("path-js-mixed", safeInternalPath("JaVaScRiPt:alert(1)"));
bad("path-data", safeInternalPath("data:text/html,<script>1</script>"));
bad("path-vbs", safeInternalPath("vbscript:x"));
bad("path-relative", safeInternalPath("trading"));
bad("path-newline", safeInternalPath("/a\nb"));
bad("path-nonstring", safeInternalPath(42));
if (safeInternalPath("/x") !== "/x") bad("path-identity", safeInternalPath("/x"));

if (isSafeGuardedUrl("/a") !== true) out.push("predicate rejected internal path");
if (isSafeGuardedUrl("javascript:alert(1)") !== false) out.push("predicate lied");
if (out.length) { console.log(JSON.stringify(out)); process.exit(1); }
console.log(JSON.stringify({ ok: true }));
"""


def test_11_sanitizer_guards_reject_payload_battery(tmp_path: Path) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH — safeHtml behaviour not executed")
    tmp = Path(tmp_path) / "nse_xss_gate_safehtml_probe.mts"
    tmp.write_text(
        _BEHAVIOUR_JS % json.dumps(SANITIZER.resolve().as_uri()),
        encoding="utf-8",
    )
    try:
        proc = subprocess.run(
            [node, tmp.as_posix()],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,  # the assertion below turns a red run into a report
        )
    finally:
        tmp.unlink(missing_ok=True)
    tail = (proc.stdout or proc.stderr).strip().splitlines()[-8:]
    assert proc.returncode == 0, (
        f"safeHtml guards failed the payload battery (node {proc.returncode}):\n" + "\n".join(tail)
    )
    assert "ok" in proc.stdout


def test_12_guard_names_are_not_shadowed_elsewhere() -> None:
    """The sink whitelist trusts these names: only safeHtml.ts may define them."""
    offenders: list[str] = []
    for f in _iter_source_files(SRC_DIR):
        rel = f.relative_to(SRC_DIR).as_posix()
        if rel == "lib/safeHtml.ts":
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(_code_lines(text), start=1):
            if re.search(rf"\b(?:function|const|let|var|class)\s+{'|'.join(GUARD_NAMES)}\b", line):
                offenders.append(f"{rel}:{i}: {line.strip()[:120]}")
    assert not offenders, (
        "a local redefinition of a guard name would defeat the sink whitelist:\n"
        + "\n".join(offenders)
    )
