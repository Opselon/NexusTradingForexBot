"""SEC-1 token/auth transport hygiene gate for the NSE Alternative UI.

Offline, source-level regression gate (mirror of test_alt_ui_runtime_contract.py
/ test_alt_ui_supply_chain.py style). It FAILS when the token hygiene policy is
broken:

  Policy (docs/alt-ui-auth-review.md):
    P1  The WEB-AUTH-P0 token is kept ONLY in sessionStorage under exactly
        ``nse.altui.token``. Never localStorage, never ``window.name``,
        never ``document.cookie``, never a non-allowlisted sessionStorage key.
    P2  ``?token=`` deep-link capture must store -> scrub the URL
        (history.replaceState) — the address bar must not keep the credential.
    P3  Resolved token material must never appear in console logs, toasts or
        UI text. Placeholder strings like ``?token=<TOKEN>`` (no interpolation
        of a resolved value) are operator instructions, allowed.
    P4  The SSE transport may carry the token ONLY in the same-origin query
        string, URL-encoded (EventSource cannot send headers). Nowhere else.
    P5  ApiError messages must never embed raw Authorization/X-NSE-Token
        values; the auth header is built in exactly one place.
    P6  The 401 path (client mapping + server body) must be static — the
        server's 401 body never echoes the supplied/expected token.
    P7  The structlog redaction pipeline scrubs ``token=`` from server logs,
        and the production uvicorn config keeps access logs quiet
        (log_level="warning") so the SSE ``?token=`` request line never lands
        in stdout access logs.

Every violation message names ``file:line``. Findings are shape-only — the
token VALUE is never reproduced into a failure message or the store.

Run (worktree root, repo venv):
  C:/Users/Capsizer/source/repos/NexusTradingForexBot/.venv/Scripts/python.exe \
      -m pytest -p no:cacheprovider -q tests/unit/test_alt_ui_token_hygiene.py
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_PY = REPO_ROOT / "src"
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"
API_CLIENT = FRONTEND_SRC / "api" / "client.ts"
REALTIME_SOCKET = FRONTEND_SRC / "websocket" / "realtimeSocket.ts"
TYPES_API = FRONTEND_SRC / "types" / "api.ts"
UI_STORE = FRONTEND_SRC / "stores" / "uiStore.ts"
I18N_LIB = FRONTEND_SRC / "lib" / "i18n.ts"
WEB_AUTH_PY = SRC_PY / "nexus_scalp" / "web" / "auth.py"
ENGINE_BOOT_PY = SRC_PY / "nexus_scalp" / "cli" / "engine_boot.py"

#: P1 allowlists -----------------------------------------------------------------
TOKEN_STORAGE_KEY = "nse.altui.token"
SESSION_STORAGE_ALLOWED_KEYS = {TOKEN_STORAGE_KEY}
#: localStorage is for VISUAL preferences only (same rule as the legacy
#: Web/ux_i18n.js which uses localStorage['nexus.ui.lang']).
VISUAL_LOCALSTORAGE_KEYS = {
    "nexus.ui.lang",  # shared lang pref with the legacy dashboard
    "nse.altui.sidebar",
    "nse.altui.dense",
}

#: Files allowed a dynamic storage-key variable, with the guard this test
#: re-checks in-source (uiStore readPref/writePref, SettingsPage clear-prefs).
DYNAMIC_KEY_EXEMPT = {
    "frontend/src/stores/uiStore.ts": {"key"},
    "frontend/src/pages/Settings/SettingsPage.tsx": {"k"},
}

SOURCE_SUFFIXES = {".ts", ".tsx"}

#: Identifier regex for "a resolved token value" in an interpolation position.
#: Mirrors resolveToken()/authHeaders()/sseUrl() locals: token, qp, AUTH_SCHEME
#: is NOT secret material (scheme name only) and is excluded from sinks here.
TOKEN_INTERP_RE = re.compile(r"\$\{\s*(?:token|qp|authToken|rawToken|tok|TOKEN)\b")
TOKEN_WORD_RE = re.compile(r"(?i)\btoken\b")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _rel(p: Path) -> str:
    try:
        return p.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def _fmt(p: Path, lineno: int, msg: str) -> str:
    return f"{_rel(p)}:{lineno}: {msg}"


def _read(path: Path) -> str:
    assert path.is_file(), (
        f"expected file missing: {_rel(path)} (renamed/deleted? re-audit SEC-1 lane)"
    )
    return path.read_text(encoding="utf-8", errors="replace")


def _blank_comments(text: str) -> str:
    """Blank out /* */ and full-line // comments, PRESERVING line numbers so
    findings report the real line. Doc-comment prose (e.g. "never lands in
    localStorage") must not trip the code scans."""
    text = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    return text


def _lines(path: Path) -> list[str]:
    return _read(path).splitlines()


def _code_lines(path: Path) -> list[str]:
    return _blank_comments(_read(path)).splitlines()


def _frontend_sources() -> list[Path]:
    if not FRONTEND_SRC.is_dir():
        return []
    return [
        p for p in sorted(FRONTEND_SRC.rglob("*")) if p.is_file() and p.suffix in SOURCE_SUFFIXES
    ]


def _fail(violations: list[str], header: str) -> None:
    assert not violations, header + ":\n  " + "\n  ".join(violations)


_CONST_STR_RE = re.compile(r"""const\s+([A-Za-z_$][\w$]*)\s*=\s*["']([^"']+)["']""")


def _file_consts(path: Path) -> dict[str, str]:
    """Map same-file `const NAME = "value"` string constants so a storage call
    like localStorage.getItem(LANG_KEY) is checked against its literal value."""
    return dict(_CONST_STR_RE.findall(_read(path)))


def _block(text: str, start_re: str) -> tuple[int, int]:
    """Return (start_line_1idx, end_line_1idx) of a top-of-line-indented block
    starting at the first line matching start_re and ending at the next line
    whose first non-space char is ``}`` at that function's indentation."""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if re.search(start_re, ln):
            start = i
            break
    if start is None:
        raise LookupError(start_re)
    indent = len(lines[start]) - len(lines[start].lstrip())
    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped.startswith("}") and (len(lines[j]) - len(lines[j].lstrip())) == indent:
            return start + 1, j + 1
    return start + 1, len(lines)


# ---------------------------------------------------------------------------
# P1 — storage policy
# ---------------------------------------------------------------------------


class TestTokenStoragePolicy:
    def test_token_storage_key_constant_is_pinned(self) -> None:
        """client.ts must define TOKEN_STORAGE_KEY with exactly the allowlisted
        value; sseUrl must use the same literal (single token sink identity)."""
        offenders: list[str] = []
        src = _lines(API_CLIENT)
        pinned = False
        for ln in src:
            if re.match(r'\s*const\s+TOKEN_STORAGE_KEY\s*=\s*"nse\.altui\.token"', ln):
                pinned = True
        if not pinned:
            offenders.append(
                _fmt(
                    API_CLIENT,
                    1,
                    'missing `const TOKEN_STORAGE_KEY = "nse.altui.token"` — token key drifted off the allowlist',
                )
            )
        for i, ln in enumerate(_code_lines(REALTIME_SOCKET), 1):
            if (
                "sessionStorage" in ln
                and "getItem" in ln
                and '"nse.altui.token"' not in ln
                and "TOKEN_STORAGE_KEY" not in ln
            ):
                offenders.append(
                    _fmt(
                        REALTIME_SOCKET,
                        i,
                        "sessionStorage read uses a key other than the allowlisted token key",
                    )
                )
        _fail(offenders, "P1 token key pin")

    def test_sessionstorage_keys_are_allowlisted(self) -> None:
        """Every sessionStorage setItem/getItem/removeItem call site in
        frontend/src must name a literal on the allowlist (or a same-file const
        that resolves to one, e.g. TOKEN_STORAGE_KEY). A new sessionStorage key
        = policy change = deliberate edit of SESSION_STORAGE_ALLOWED_KEYS."""
        offenders: list[str] = []
        call_re = re.compile(
            r"sessionStorage\s*\.\s*(?:setItem|getItem|removeItem)\s*\(\s*([\"'`][^\"'`]*[\"'`]|[A-Za-z_$][\w.$]*)"
        )
        seen_any = False
        for p in _frontend_sources():
            consts = _file_consts(p)
            for i, ln in enumerate(_code_lines(p), 1):
                for m in call_re.finditer(ln):
                    seen_any = True
                    arg = m.group(1)
                    if arg[:1] in "\"'`":
                        key = arg.strip("\"'`")
                    else:
                        key = consts.get(arg.split(".")[0] if "." in arg else arg, "")
                        if not key and arg == "TOKEN_STORAGE_KEY":
                            key = TOKEN_STORAGE_KEY  # canonical name; value re-pinned in test #1
                    if not key:
                        offenders.append(
                            _fmt(
                                p,
                                i,
                                f"dynamic sessionStorage key {arg!r} not resolvable to the allowlist — only TOKEN_STORAGE_KEY is permitted",
                            )
                        )
                    elif key not in SESSION_STORAGE_ALLOWED_KEYS:
                        offenders.append(
                            _fmt(
                                p,
                                i,
                                f"sessionStorage key {key!r} is not on the allowlist {sorted(SESSION_STORAGE_ALLOWED_KEYS)}",
                            )
                        )
        assert seen_any, (
            "no sessionStorage usage found at all — the ?token= capture path disappeared (P2 bootstrap broken)"
        )
        _fail(offenders, "P1 sessionStorage allowlist")

    def test_token_never_touches_localstorage(self) -> None:
        """localStorage must never see the token key OR a token-shaped value.
        Literal keys are checked against the visual-pref allowlist; same-file
        string consts resolve to their literal value (LANG_KEY etc.); dynamic
        keys are only allowed inside the two exempt helpers, whose guards are
        re-verified here."""
        offenders: list[str] = []
        call_re = re.compile(
            r"localStorage\s*\.\s*(?:setItem|getItem|removeItem)\s*\(\s*([\"'`][^\"'`]*[\"'`]|[A-Za-z_$][\w.$]*)"
        )
        relset = {_rel(p) for p in _frontend_sources()}
        for p in _frontend_sources():
            rel = _rel(p)
            exempt = DYNAMIC_KEY_EXEMPT.get(rel, set())
            consts = _file_consts(p)
            for i, ln in enumerate(_code_lines(p), 1):
                for m in call_re.finditer(ln):
                    arg = m.group(1)
                    if arg[:1] in "\"'`":
                        key = arg.strip("\"'`")
                    else:
                        head = arg.split(".")[0] if "." in arg else arg
                        key = consts.get(head, "")
                    if key:
                        if key == TOKEN_STORAGE_KEY:
                            offenders.append(
                                _fmt(
                                    p,
                                    i,
                                    "TOKEN key used with localStorage — P1 violation (token is sessionStorage-only)",
                                )
                            )
                        elif key not in VISUAL_LOCALSTORAGE_KEYS:
                            offenders.append(
                                _fmt(
                                    p,
                                    i,
                                    f"localStorage key {key!r} is not on the visual-preference allowlist",
                                )
                            )
                    elif arg not in exempt:
                        offenders.append(
                            _fmt(
                                p,
                                i,
                                f"dynamic localStorage key {arg!r} outside the exempt visual-pref helpers",
                            )
                        )
                    if TOKEN_INTERP_RE.search(ln):
                        offenders.append(
                            _fmt(
                                p,
                                i,
                                "token-shaped value interpolated into a localStorage call — P1 violation",
                            )
                        )
        # uiStore guard: every literal passed to readPref/writePref is visual-only
        if _rel(UI_STORE) in relset:
            for i, ln in enumerate(_code_lines(UI_STORE), 1):
                m = re.search(r'(?:readPref|writePref)\(\s*"([^"]+)"', ln)
                if m and m.group(1) not in VISUAL_LOCALSTORAGE_KEYS:
                    offenders.append(
                        _fmt(UI_STORE, i, f"pref helper called with non-visual key {m.group(1)!r}")
                    )
        # SettingsPage guard: clear-prefs stays scoped to the visual prefix + lang key
        sp = FRONTEND_SRC / "pages" / "Settings" / "SettingsPage.tsx"
        if sp.is_file():
            sptext = _blank_comments(_read(sp))
            if not re.search(r'PREF_PREFIX\s*=\s*"nse\.altui\."', sptext):
                offenders.append(
                    _fmt(
                        sp,
                        1,
                        'SettingsPage PREF_PREFIX guard ("nse.altui.") changed — re-audit clear-prefs scope',
                    )
                )
            if not re.search(r"startsWith\(PREF_PREFIX\)\s*\|\|\s*k === LANG_KEY", sptext):
                offenders.append(
                    _fmt(
                        sp,
                        1,
                        "SettingsPage key filter (startsWith(PREF_PREFIX) || k === LANG_KEY) changed — re-audit clear-prefs scope",
                    )
                )
        _fail(offenders, "P1 localStorage visual-only")

    def test_no_window_name_or_cookie_sinks(self) -> None:
        offenders: list[str] = []
        for p in _frontend_sources():
            for i, ln in enumerate(_code_lines(p), 1):
                if re.search(r"window\.name\s*[:=]", ln) or re.search(
                    r"window\[\s*[\"']name[\"']\s*\]", ln
                ):
                    offenders.append(
                        _fmt(p, i, "window.name sink — banned cross-navigation channel")
                    )
                if re.search(r"document\.cookie\s*=", ln):
                    offenders.append(
                        _fmt(
                            p,
                            i,
                            "document.cookie write — token must never enter cookie storage from JS",
                        )
                    )
        _fail(offenders, "P1 banned sinks")


# ---------------------------------------------------------------------------
# P2 — URL scrub contract
# ---------------------------------------------------------------------------


class TestUrlScrubContract:
    def test_capture_then_scrub_flow_present(self) -> None:
        """resolveToken() must: read ?token= -> sessionStorage.setItem ->
        searchParams.delete("token") -> history.replaceState, in that order.
        Any step removed = token persists in the address bar = FAIL."""
        lines = _code_lines(API_CLIENT)
        text = "\n".join(lines)

        def _line_of(pattern: str) -> int | None:
            rx = re.compile(pattern)
            for i, ln in enumerate(lines, 1):
                if rx.search(ln):
                    return i
            return None

        cap = _line_of(
            r"URLSearchParams\(\s*window\.location\.search\s*\)\s*\.get\(\s*\"token\"\s*\)"
        )
        store = _line_of(r"sessionStorage\.setItem\(\s*TOKEN_STORAGE_KEY\s*,\s*qp\s*\)")
        drop = _line_of(r"searchParams\.delete\(\s*\"token\"\s*\)")
        scrub = _line_of(r"history\.replaceState\(")
        offenders: list[str] = []
        if cap is None:
            offenders.append(
                _fmt(API_CLIENT, 1, "P2 ?token= capture path removed from client.ts (resolveToken)")
            )
        if store is None:
            offenders.append(
                _fmt(
                    API_CLIENT,
                    1,
                    "P2 sessionStorage.setItem(TOKEN_STORAGE_KEY, qp) removed — refresh would lose the token",
                )
            )
        if drop is None:
            offenders.append(
                _fmt(
                    API_CLIENT,
                    1,
                    'P2 url.searchParams.delete("token") removed — token would persist in the address bar',
                )
            )
        if scrub is None:
            offenders.append(
                _fmt(
                    API_CLIENT,
                    1,
                    "P2 history.replaceState scrub removed — token would persist in the address bar",
                )
            )
        if cap and store and drop and scrub:
            if not (cap < store < scrub and drop <= scrub):
                offenders.append(
                    _fmt(
                        API_CLIENT,
                        store,
                        f"P2 order broken: capture={cap} store={store} delete={drop} replaceState={scrub} (must store BEFORE scrubbing the URL)",
                    )
                )
            if (
                "window.location.hash" in text
                and "token" in text.split("window.location.hash")[1][:200]
            ):
                offenders.append(
                    _fmt(
                        API_CLIENT,
                        1,
                        "P2 token handling moved into the hash — hash survives replaceState and is never scrubbed",
                    )
                )
        _fail(offenders, "P2 URL scrub")

    def test_no_token_assignment_to_location_before_scrub(self) -> None:
        """Nobody may write the token into location (href/assign/replace) or
        pushState it — that would re-materialize the credential in history."""
        offenders: list[str] = []
        for p in _frontend_sources():
            for i, ln in enumerate(_code_lines(p), 1):
                if re.search(
                    r"(location\s*=\s*|location\.(href|assign|replace)\s*\(|pushState\s*\()", ln
                ) and TOKEN_INTERP_RE.search(ln):
                    offenders.append(
                        _fmt(
                            p, i, "token interpolated into a location/history write — P2 violation"
                        )
                    )
        _fail(offenders, "P2 location hygiene")


# ---------------------------------------------------------------------------
# P3 — token never in logs / toasts / UI text
# ---------------------------------------------------------------------------


class TestNoTokenInTextSinks:
    def test_console_never_prints_token(self) -> None:
        """Any console.* line mentioning token material fails — including a
        bare ``token`` word next to an interpolation (defense in depth: today
        the UI logs nothing at all)."""
        offenders: list[str] = []
        console_re = re.compile(
            r"console\s*\.\s*(log|info|warn|error|debug|trace|dir|table|assert)\s*\("
        )
        for p in _frontend_sources():
            for i, ln in enumerate(_code_lines(p), 1):
                if console_re.search(ln) and (
                    TOKEN_INTERP_RE.search(ln) or TOKEN_WORD_RE.search(ln)
                ):
                    offenders.append(
                        _fmt(p, i, "console call references token material — P3 violation")
                    )
        _fail(offenders, "P3 console")

    def test_toasts_never_print_token(self) -> None:
        offenders: list[str] = []
        toast_re = re.compile(r"pushToast\s*\(|toast[A-Za-z]*\s*\(")
        for p in _frontend_sources():
            for i, ln in enumerate(_code_lines(p), 1):
                if toast_re.search(ln) and TOKEN_INTERP_RE.search(ln):
                    offenders.append(
                        _fmt(
                            p, i, "toast text interpolates a resolved token variable — P3 violation"
                        )
                    )
        _fail(offenders, "P3 toasts")

    def test_ui_text_never_embeds_resolved_token(self) -> None:
        """``?token=`` may appear as an operator INSTRUCTION placeholder
        (…/?token=<NSE_WEB_AUTH_TOKEN>, "reopen with ?token=…") but a UI string
        containing ``token=`` must never interpolate a resolved value."""
        offenders: list[str] = []
        for p in _frontend_sources():
            if p == REALTIME_SOCKET:
                continue  # transport URL builder, checked by P4
            for i, ln in enumerate(_code_lines(p), 1):
                if "token=" in ln and "${" in ln:
                    offenders.append(
                        _fmt(
                            p,
                            i,
                            "UI/text string embeds an interpolated token value (token=…) — P3 violation",
                        )
                    )
                if TOKEN_INTERP_RE.search(ln) and re.search(
                    r"<[a-zA-Z]|className=|textContent|innerText|document\.title\s*=", ln
                ):
                    offenders.append(
                        _fmt(
                            p,
                            i,
                            "resolved token variable interpolated into rendered UI text — P3 violation",
                        )
                    )
        _fail(offenders, "P3 UI text")

    def test_no_token_literal_hardcoded_in_frontend(self) -> None:
        """Source must not carry a committed token literal (long urlsafe shape
        bound to a token-ish key)."""
        rx = re.compile(r"(?i)\b[a-z_]*token[a-z_]*\b\s*[:=]\s*[\"'][A-Za-z0-9+_\-]{24,}[\"']")
        offenders: list[str] = []
        for p in _frontend_sources():
            for i, ln in enumerate(_code_lines(p), 1):
                m = rx.search(ln)
                if m and "encodeURIComponent" not in ln and "TOKEN_STORAGE_KEY" not in ln:
                    offenders.append(
                        _fmt(
                            p,
                            i,
                            f"token-shaped string literal committed in source ({len(m.group(0))} chars, value not reproduced)",
                        )
                    )
        _fail(offenders, "P3 no committed token literals")


# ---------------------------------------------------------------------------
# P4 — SSE transport: token only in the same-origin query, URL-encoded
# ---------------------------------------------------------------------------


class TestSseTransport:
    def test_sse_url_keeps_token_only_encoded_in_query(self) -> None:
        lines = _code_lines(REALTIME_SOCKET)  # comment-blanked: prose must not count
        try:
            start, end = _block("\n".join(lines), r"function sseUrl\s*\(")
        except LookupError:
            pytest.fail(
                _fmt(REALTIME_SOCKET, 1, "sseUrl() builder gone — re-audit SSE transport (P4)")
            )
        block = "\n".join(lines[start - 1 : end])
        offenders: list[str] = []
        if "sessionStorage" not in block or "nse.altui.token" not in block:
            offenders.append(
                _fmt(
                    REALTIME_SOCKET,
                    start,
                    "sseUrl no longer reads the allowlisted sessionStorage token key",
                )
            )
        if not re.search(r"\?token=\$\{encodeURIComponent\(\s*token\s*\)\}", block):
            offenders.append(
                _fmt(
                    REALTIME_SOCKET,
                    start,
                    "SSE token query must be built as ?token=${encodeURIComponent(token)} (raw or double-interpolated token)",
                )
            )
        for off in range(start - 1, end):
            ln = lines[off]
            if "token=" in ln and "encodeURIComponent" not in ln:
                offenders.append(
                    _fmt(
                        REALTIME_SOCKET,
                        off + 1,
                        "token= appears in sseUrl without encodeURIComponent",
                    )
                )
            if re.search(r"https?://", ln):
                offenders.append(
                    _fmt(
                        REALTIME_SOCKET,
                        off + 1,
                        "absolute URL in sseUrl — the token must only ride the same-origin stream path",
                    )
                )
        if not re.search(r"[\"'`]/api/ticks/stream", block):
            offenders.append(
                _fmt(
                    REALTIME_SOCKET,
                    start,
                    "SSE path must stay the relative same-origin /api/ticks/stream",
                )
            )
        _fail(offenders, "P4 SSE query")

    def test_token_param_confined_to_sse_builder(self) -> None:
        """Outside sseUrl, realtimeSocket.ts must not produce any ``?token=``
        surface, and the EventSource must be constructed ONLY from sseUrl()."""
        offenders: list[str] = []
        lines = _code_lines(REALTIME_SOCKET)
        try:
            start, end = _block("\n".join(lines), r"function sseUrl\s*\(")
        except LookupError:
            offenders.append(
                _fmt(REALTIME_SOCKET, 1, "sseUrl() missing — cannot scope token= usage")
            )
            start = end = -1
        for i, ln in enumerate(lines, 1):
            if "?token=" in ln and not (start <= i <= end):
                offenders.append(
                    _fmt(
                        REALTIME_SOCKET,
                        i,
                        "token query built outside sseUrl() — unscoped SSE exposure (P4)",
                    )
                )
            if re.search(r"new\s+EventSource\s*\(", ln) and "sseUrl()" not in ln:
                offenders.append(
                    _fmt(
                        REALTIME_SOCKET,
                        i,
                        "EventSource constructed from something other than sseUrl() (P4)",
                    )
                )
        _fail(offenders, "P4 token scope")

    def test_token_never_stored_in_realtime_client_state(self) -> None:
        """The client class must not hold the token on an instance field that
        could be serialized/debugged; only the URL builder may touch it."""
        offenders: list[str] = []
        for i, ln in enumerate(_code_lines(REALTIME_SOCKET), 1):
            if re.search(r"this\.\w*[Tt]oken\w*\s*=", ln):
                offenders.append(
                    _fmt(
                        REALTIME_SOCKET,
                        i,
                        "realtime client stores the token on instance state — keep it in the URL builder only",
                    )
                )
        _fail(offenders, "P4 no token field")


# ---------------------------------------------------------------------------
# P5 — error surface must not embed credentials
# ---------------------------------------------------------------------------


class TestErrorSurfaceHygiene:
    def test_auth_header_built_in_exactly_one_place(self) -> None:
        """``X-NSE-Token`` may appear in client.ts only inside authHeaders();
        any other construction site risks leaking the header into messages."""
        offenders: list[str] = []
        lines = _code_lines(API_CLIENT)
        try:
            start, end = _block("\n".join(lines), r"function authHeaders\s*\(")
        except LookupError:
            offenders.append(
                _fmt(API_CLIENT, 1, "authHeaders() gone — re-audit header construction (P5)")
            )
            start = end = -1
        for i, ln in enumerate(lines, 1):
            if "X-NSE-Token" in ln and not (start <= i <= end):
                offenders.append(
                    _fmt(API_CLIENT, i, "X-NSE-Token referenced outside authHeaders() (P5)")
                )
        _fail(offenders, "P5 header construction scope")

    def test_apierror_messages_never_embed_credentials(self) -> None:
        """Every `new ApiError(...)` construction in client.ts: within its
        argument window there must be no resolved-token interpolation and no
        raw Authorization value (only static strings, HTTP status numbers,
        backend envelope fields, and the client correlation id)."""
        offenders: list[str] = []
        lines = _code_lines(API_CLIENT)
        for i, ln in enumerate(lines, 1):
            if "new ApiError(" in ln:
                window = "\n".join(lines[i - 1 : min(i + 8, len(lines))])
                if TOKEN_INTERP_RE.search(window):
                    offenders.append(
                        _fmt(
                            API_CLIENT,
                            i,
                            "ApiError message interpolates a resolved token variable (P5)",
                        )
                    )
                if re.search(r"Authorization|authHeaders\(\)|resolveToken\(\)", window):
                    offenders.append(
                        _fmt(
                            API_CLIENT,
                            i,
                            "ApiError construction references the Authorization value/header builder (P5)",
                        )
                    )
        _fail(offenders, "P5 ApiError")

    def test_401_client_mapping_is_static(self) -> None:
        """401 must map to code UNAUTHORIZED with the fixed message
        'Missing or invalid web auth token.' — no echo of anything dynamic."""
        offenders: list[str] = []
        lines = _code_lines(API_CLIENT)
        joined = "\n".join(lines)
        if not re.search(r"401\s*\?\s*\"UNAUTHORIZED\"", joined):
            offenders.append(
                _fmt(API_CLIENT, 1, "401 -> UNAUTHORIZED mapping missing in client.ts (P6)")
            )
        msg_line = None
        for i, ln in enumerate(lines, 1):
            if "Missing or invalid web auth token" in ln:
                msg_line = i
                if "${" in ln:
                    offenders.append(
                        _fmt(
                            API_CLIENT,
                            i,
                            "401 message string is interpolated — must stay static (P6)",
                        )
                    )
        if msg_line is None:
            offenders.append(
                _fmt(
                    API_CLIENT,
                    1,
                    "static 401 message 'Missing or invalid web auth token.' missing (P6)",
                )
            )
        if not TYPES_API.is_file():
            offenders.append(_fmt(TYPES_API, 1, "types/api.ts missing (P6 isAuthError contract)"))
        else:
            tl = _code_lines(TYPES_API)
            ok = any("isAuthError" in ln and "401" in ln for ln in tl) or any(
                "this.status === 401" in ln for ln in tl
            )
            if not ok:
                offenders.append(
                    _fmt(TYPES_API, 1, "ApiError.isAuthError no longer classifies 401 (P6)")
                )
        _fail(offenders, "P6 client 401 path")

    def test_clear_auth_token_removes_the_session_key(self) -> None:
        lines = _code_lines(API_CLIENT)
        try:
            start, end = _block("\n".join(lines), r"export function clearAuthToken")
        except LookupError:
            pytest.fail(
                _fmt(
                    API_CLIENT, 1, "clearAuthToken() removed — 401 re-auth flow lost its purge seam"
                )
            )
        block = "\n".join(lines[start - 1 : end])
        if not re.search(r"sessionStorage\.removeItem\(\s*TOKEN_STORAGE_KEY\s*\)", block):
            pytest.fail(
                _fmt(
                    API_CLIENT,
                    start,
                    "clearAuthToken() no longer removes the sessionStorage token key",
                )
            )


# ---------------------------------------------------------------------------
# P6/P7 — server-side 401 body + log redaction (backend facts, read-only)
# ---------------------------------------------------------------------------


class TestServerAuthTransportFacts:
    def test_backend_401_body_is_static_never_echoes_token(self) -> None:
        """auth.py's fast 401 body and the middleware JSON 401 must be static
        literals; no supplied/expected token may be interpolated into them."""
        offenders: list[str] = []
        lines = _code_lines(WEB_AUTH_PY)
        has_static_asgi_401 = False
        for i, ln in enumerate(lines, 1):
            if "missing or invalid web auth token" in ln:
                if "${" in ln or 'f"' in ln or "f'" in ln or "{" in ln.split("b'")[-1][:12]:
                    offenders.append(
                        _fmt(
                            WEB_AUTH_PY,
                            i,
                            "401 body message line looks interpolated — must stay static",
                        )
                    )
                has_static_asgi_401 = True
            if re.search(r"logger\.\w+\([^)]*f[\"'][^\"']*\{\s*(token|self\._token|supplied)", ln):
                offenders.append(
                    _fmt(
                        WEB_AUTH_PY,
                        i,
                        "logger call formats the token VALUE into the message — P7 violation",
                    )
                )
        if not has_static_asgi_401:
            offenders.append(
                _fmt(
                    WEB_AUTH_PY,
                    1,
                    'static 401 message "missing or invalid web auth token" not found — re-audit (P6)',
                )
            )
        _fail(offenders, "P6 server 401 body")

    def test_bootstrap_cookie_is_httponly_samesite_strict(self) -> None:
        """WEB-UI-BOOTSTRAP: the cookie that carries the token must stay
        HttpOnly + SameSite=strict (auth.py set_cookie block)."""
        text = _blank_comments(_read(WEB_AUTH_PY))
        offenders: list[str] = []
        for m in re.finditer(r"set_cookie\(([^)]*)\)", text, flags=re.S):
            args = m.group(1)
            line_no = text[: m.start()].count("\n") + 1
            if WEB_AUTH_KEYISH(args):
                if "httponly=True" not in args.replace(" ", ""):
                    offenders.append(
                        _fmt(WEB_AUTH_PY, line_no, "token cookie set without httponly=True")
                    )
                if 'samesite="strict"' not in args:
                    offenders.append(
                        _fmt(WEB_AUTH_PY, line_no, 'token cookie set without samesite="strict"')
                    )
        _fail(offenders, "P6 cookie transport")

    def test_uvicorn_production_config_keeps_access_logs_quiet(self) -> None:
        """engine_boot.py's uvicorn.Config must keep log_level at warning (or
        lower the volume further). At info/debug, uvicorn.access logs the full
        request line — including the SSE ``?token=`` — to stdout, OUTSIDE the
        structlog redactor (uvicorn.access does not propagate)."""
        lines = _lines(ENGINE_BOOT_PY)
        start = None
        for i, ln in enumerate(lines, 1):
            if "uvicorn_config = uvicorn.Config(" in ln:
                start = i
                break
        if start is None:
            pytest.fail(
                _fmt(
                    ENGINE_BOOT_PY,
                    1,
                    "uvicorn.Config block not found — re-audit access-log posture (P7)",
                )
            )
        end = start
        depth = 0
        for i in range(start - 1, len(lines)):
            depth += lines[i].count("(") - lines[i].count(")")
            end = i + 1
            if depth <= 0:
                break
        block = "\n".join(lines[start - 1 : end])
        offenders: list[str] = []
        if re.search(r'log_level\s*=\s*["\'](debug|info)["\']', block):
            offenders.append(
                _fmt(
                    ENGINE_BOOT_PY,
                    start,
                    "production uvicorn log_level is info/debug — request lines (incl. SSE ?token=) would land in access logs (P7)",
                )
            )
        if not re.search(r'log_level\s*=\s*["\'](warning|error|critical)["\']', block):
            offenders.append(
                _fmt(
                    ENGINE_BOOT_PY,
                    start,
                    "production uvicorn Config no longer pins a quiet log_level (P7)",
                )
            )
        _fail(offenders, "P7 access-log volume")


def WEB_AUTH_KEYISH(cookie_args: str) -> bool:
    return "WEB_AUTH_COOKIE_NAME" in cookie_args


class TestStructlogRedaction:
    """Behavioral proof (offline, no network): the central redactor scrubs
    token= assignments and token-keyed fields, so structured server logs never
    carry the credential — the guarantee docs/alt-ui-auth-review.md cites for
    the ?token= exposure assessment."""

    SECRET = (
        "abcDEF12" + "3xyzGHI" + "456jklMN" + "O789pq"
    )  # 38-char synthetic, never a real credential

    def _tree_module(self, dotted: str):
        """Import ``dotted`` from THIS worktree's src/, temporarily shadowing
        the dev venv's editable .pth pin to the main checkout (BUG-231-class
        harness issue), then restore sys.path/sys.modules so sibling tests are
        unaffected. Skips (never guesses) when the import fails outright."""
        import importlib

        src = str(SRC_PY)
        saved_path = list(sys.path)
        saved_mods = {
            k: v
            for k, v in sys.modules.items()
            if k == "nexus_scalp" or k.startswith("nexus_scalp.")
        }
        sys.path.insert(0, src)
        for name in list(saved_mods):
            del sys.modules[name]
        try:
            mod = importlib.import_module(dotted)
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"{dotted} not importable from this tree: {exc}")
        finally:
            sys.path[:] = saved_path
        origin = str(Path(getattr(mod, "__file__", "") or "").resolve())
        tree = str(REPO_ROOT.resolve())
        # restore the prior module pin so sibling tests are unaffected either way
        prev = {
            k: v
            for k, v in sys.modules.items()
            if k == "nexus_scalp" or k.startswith("nexus_scalp.")
        }
        for name in prev:
            del sys.modules[name]
        sys.modules.update(saved_mods)
        if not origin.startswith(tree):
            pytest.skip(f"imported {dotted} from a different tree: {origin}")
        return mod

    def test_value_scrubber_redacts_token_query(self) -> None:
        nlog = self._tree_module("nexus_scalp.observability.logging")
        line = f'INFO: 127.0.0.1 - "GET /api/ticks/stream?token={self.SECRET} HTTP/1.1" 200 OK'
        out = nlog._redact_value(line)
        assert self.SECRET not in out, f"structlog value scrubber leaked the SSE token: {out[:120]}"
        assert "[REDACTED_SECRET]" in out

    def test_key_based_redaction_masks_token_fields(self) -> None:
        nlog = self._tree_module("nexus_scalp.observability.logging")
        ev = {"event": "request", "token": self.SECRET, "web_auth_token": self.SECRET}
        out = nlog._redact_sensitive_fields(None, "info", ev)
        assert out["token"] == "[REDACTED_SECRET]"
        assert out["web_auth_token"] == "[REDACTED_SECRET]"

    @pytest.mark.xfail(
        strict=False,
        reason=(
            "KNOWN GAP (SEC-1 audit 2026-09-13): key 'authorization' contains the benign "
            "fragment 'author' from _NON_SECRET_KEY_FRAGMENTS, so _redact_sensitive_fields "
            "continues BEFORE key- or value-based scrubbing — a logged authorization header "
            "would retain its Bearer value verbatim. Today no web code logs request headers, "
            "so it is latent, not live. Patch suggestion in docs/alt-ui-auth-review.md §7.1 "
            "(match exact credential keys before the exemption scan). Re-enable strict when fixed."
        ),
    )
    def test_authorization_field_redaction_known_gap(self) -> None:
        nlog = self._tree_module("nexus_scalp.observability.logging")
        ev = {"event": "request", "authorization": f"Bearer {self.SECRET}"}
        out = nlog._redact_sensitive_fields(None, "info", ev)
        assert out["authorization"] == "[REDACTED_SECRET]", (
            "authorization field not masked: 'author' exemption short-circuits "
            "src/nexus_scalp/observability/logging.py _redact_sensitive_fields"
        )

    def test_webauth_middleware_401_body_carries_no_token(self) -> None:
        """Live-ASGI (offline) proof of P6: unauthenticated and wrong-token
        requests get the static 401; neither body nor headers echo the
        expected or supplied token."""
        web_auth = self._tree_module("nexus_scalp.web.auth")

        expected = "exp3ct3d-" + "Sup3rSecret-" + "0123456789abcdef"
        old = os.environ.get("NSE_WEB_AUTH_TOKEN")
        os.environ["NSE_WEB_AUTH_TOKEN"] = expected
        try:
            calls: list = []

            async def inner(scope, receive, send):
                calls.append(scope["path"])
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"ok"})

            mw = web_auth.WebAuthMiddleware(inner)

            async def _run(scope):
                sent: list = []
                body = bytearray()
                status = None

                async def receive():
                    return {"type": "http.request", "body": b"", "more_body": False}

                async def send(msg):
                    nonlocal status
                    sent.append(msg)
                    if msg["type"] == "http.response.start":
                        status = msg["status"]
                    elif msg["type"] == "http.response.body":
                        body.extend(msg.get("body", b""))

                await mw(scope, receive, send)
                return status, bytes(body), sent

            # 1) no token at all
            st, bd, sent = asyncio.run(
                _run({"type": "http", "path": "/api/status", "headers": [], "query_string": b""})
            )
            assert st == 401, "missing token must 401 (fail-closed)"
            assert expected.encode() not in bd, "401 body echoed the expected token"
            hdrs = {k.lower(): v for k, v in dict(sent[0]).get("headers", [])}
            assert hdrs.get(b"www-authenticate", b"").lower() == b"bearer", (
                "401 must advertise Bearer"
            )
            # 2) wrong token supplied — supplied value must not be echoed back
            wrong = "wr0ng-" + "Sup3rSecret-" + "ffffffffffff"
            st, bd, _ = asyncio.run(
                _run(
                    {
                        "type": "http",
                        "path": "/api/status",
                        "headers": [(b"authorization", f"Bearer {wrong}".encode())],
                        "query_string": b"",
                    }
                )
            )
            assert st == 401, "wrong token must 401"
            assert wrong.encode() not in bd and expected.encode() not in bd, (
                "401 body echoed a token value"
            )
            # 3) correct query token passes (SSE-friendly path the UI depends on)
            st, bd, _ = asyncio.run(
                _run(
                    {
                        "type": "http",
                        "path": "/api/status",
                        "headers": [],
                        "query_string": f"token={expected}".encode(),
                    }
                )
            )
            assert st == 200, "?token= must authenticate the SSE path (P4 contract)"
            assert calls, "inner app never reached"
        finally:
            if old is None:
                os.environ.pop("NSE_WEB_AUTH_TOKEN", None)
            else:
                os.environ["NSE_WEB_AUTH_TOKEN"] = old
