"""
Runtime integration tests for the NSE Alternative UI (frontend/).

These exercise the REAL backend contract surfaces the UI consumes, using the
repo venv. They are Python-side because the UI's own transport behavior is
verified against live endpoints; TSX rendering is verified by `tsc -b` +
`vite build` (strict mode).

Run (repo root):
  ./.venv/Scripts/python.exe tests/unit/test_alt_ui_runtime_contract.py
  (or pytest -q tests/unit/test_alt_ui_runtime_contract.py)
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import urllib.error
import urllib.request
from typing import Any

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# AUTH-SESSION-ISOLATION (2026-09-12): this module used to run
# ``os.environ.setdefault("NSE_WEB_AUTH_DISABLE", "1")`` at IMPORT time.
# Collection imports every module before the first test runs, so that single
# line opted the WHOLE pytest session out of WEB-AUTH-P0 — and, because the
# opt-out is read by ``web.server._install_web_auth_if_enabled`` at
# ``create_app`` time, every later auth-contract test in the session
# (test_replay_toggle_guard, test_frontend_assets_phase14, ...) silently saw
# 200 where it asserted 401 (or 401 where it asserted 200): a red-inverted /
# fake-green artifact driven purely by collection order.
#
# The opt-out is never needed in-process here: the tests below either read
# frontend source files or talk to a backend the OPERATOR starts in a separate
# process (ALT_UI_LIVE_BACKEND; the live backend's own auth mode is that
# process's environment, not this interpreter's). Where an in-process app is
# ever built, the supported mechanism is a scoped ``monkeypatch.setenv``,
# which restores automatically — never import-time os.environ mutation.


def _base() -> str:
    return os.environ.get("ALT_UI_TEST_BASE", "http://127.0.0.1:8099")


def _get(path: str) -> tuple[int, Any]:
    req = urllib.request.Request(_base() + path, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body) if body else {}
        except json.JSONDecodeError:
            return e.code, {}


# ---------------------------------------------------------------------------
# Frontend source scanners (see TestUiSourceContract below).
# ---------------------------------------------------------------------------


def _rel(path: pathlib.Path) -> str:
    """Stable, OS-independent path key ('frontend/src/...') used by the allowlists."""
    return path.relative_to(REPO_ROOT).as_posix()


def _raw_fetch_call(text: str) -> bool:
    """True when `text` performs a fetch() CALL.

    Deliberately not a substring test: `query.refetch()` and prose identifiers
    are not network calls, so they must not be flagged.
    """
    return re.search(r"\bfetch\s*\(", text) is not None


#: Tokens that only appear in code that talks to the engine directly (PySQLite
#: handles, the MetaTrader5 package, engine database files).
FORBIDDEN_ENGINE_TOKENS = ("sqlite3", "mt5.", "MetaTrader5", "audit.db", ".db'")


def _engine_tokens(text: str) -> list[str]:
    """Forbidden engine-internal tokens present in `text` (case-insensitive).

    Both sides are case-folded: the original version lowercased the file text
    but compared mixed-case tokens against it, so its 'MetaTrader5' check could
    never fire.
    """
    lowered = text.lower()
    return [token for token in FORBIDDEN_ENGINE_TOKENS if token.lower() in lowered]


#: `fetch(` outside `src/api`, excused per exact file — never a glob.
#:
#: The rule exists so the UI has ONE network entry point that applies auth,
#: request-ids, timeouts, error normalization and the 401 cookie heal. These
#: two `core/` files ARE that entry point; they are not feature code reaching
#: for the network on its own.
FETCH_OUTSIDE_API_ALLOWLIST: dict[str, str] = {
    "frontend/src/core/middleware.ts": (
        "terminal fetch of the ONE REST pipeline (executeRequest). src/api/* and "
        "src/core/transport.ts reach the network only through it, which is exactly what "
        "this test defends; moving it into src/api would make the shim circular."
    ),
    "frontend/src/core/auth.ts": (
        "refreshBootstrapCookie() GETs the public cookie-issuing asset to re-derive the "
        "BUG-267 HttpOnly bootstrap cookie. The pipeline's cookieHeal middleware calls it, so "
        "routing it back through executeRequest() would recurse. Identity bootstrap, not "
        "feature traffic."
    ),
}

#: (exact path, token) -> why that occurrence is a NAME/COPY mention rather than
#: engine access. Keying on the token too means a *different* forbidden token
#: appearing later in an allowlisted file still fails the test.
ENGINE_TOKEN_ALLOWLIST: dict[tuple[str, str], str] = {
    ("frontend/src/pages/Dashboard/i18n.ts", "mt5."): (
        "translation catalog: the 'dash.mt5.*' keys are copy for the MT5 status panel; "
        "values are user-facing prose, nothing is imported or called."
    ),
    ("frontend/src/pages/Dashboard/DashboardPage.tsx", "mt5."): (
        'only t("dash.mt5.*") copy-key literals. The panel renders '
        "engineApi.mt5Status() (GET /api/mt5/status) through TanStack Query via the api "
        "layer — no engine handle, driver or file is touched."
    ),
    ("frontend/src/pages/Audit/i18n.ts", "audit.db"): (
        "translation catalog: the 'audit.db.*' keys name the audit database card in the UI; "
        "the copy itself states that all access goes through bounded backend endpoints."
    ),
    ("frontend/src/pages/Audit/AuditPage.tsx", "audit.db"): (
        'only t("audit.db.*") copy-key literals; the card\'s data comes from the backend '
        "audit read plane via the api layer (filename/provider/size already reported by it)."
    ),
    ("frontend/src/lib/errors/i18n.ts", "mt5."): (
        "user-facing error copy: the Arabic sentence ends with 'MT5.' (sentence-final "
        "period); it is a human-readable string, not an identifier."
    ),
    ("frontend/src/features/health/ui/HealthPage.tsx", "mt5."): (
        "`mt5` is the local react-query result of useMt5() (healthApi.mt5 -> "
        "GET /api/mt5/status); mt5.data / mt5.isError read a fetched payload, not the "
        "MetaTrader5 package."
    ),
    ("frontend/src/features/decision-trace/ui/TraceInspector.tsx", "mt5."): (
        "`mt5` is a local useMemo over mt5Reachability(events) — gateway-reach evidence "
        "extracted from backend trace events, rendered as fields; not the MetaTrader5 package."
    ),
}


@pytest.mark.skipif(
    not os.environ.get("ALT_UI_LIVE_BACKEND"),
    reason="live-backend contract test (start a test backend and set ALT_UI_LIVE_BACKEND=1)",
)
class TestLiveBackendContract:
    """Run against a live test backend (uvicorn create_app)."""

    def test_canonical_snapshot_contract(self) -> None:
        status, body = _get("/api/status")
        assert status == 200
        for key in (
            "state_version",
            "engine_running",
            "execution_mode",
            "runtime_mode",
            "data_source",
            "mode_source_mismatch",
            "tick_stale",
            "provenance",
            "timestamps",
            "bid",
            "ask",
            "spread",
            "account",
            "positions",
            "features",
            "probs",
            "model",
            "predictions",
            "health",
            "live_freshness",
            "is_stale",
            "diagnostics",
        ):
            assert key in body, f"canonical snapshot missing {key}"
        assert isinstance(body["health"], dict) and "overall" in body["health"]

    def test_sse_stream_emits_state_event(self) -> None:
        req = urllib.request.Request(
            _base() + "/api/ticks/stream", headers={"Accept": "text/event-stream"}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            assert r.status == 200
            assert "text/event-stream" in (r.headers.get("content-type") or "")
            # Read the first frames: must include `event: state`.
            buf = b""
            deadline = 8.0
            import time

            start = time.time()
            while time.time() - start < deadline:
                chunk = r.fp.readline()
                if not chunk:
                    break
                buf += chunk
                if b"event: state" in buf:
                    break
            assert b"event: state" in buf, "SSE must deliver a full state event first"

    def test_v1_envelope_error_semantics(self) -> None:
        status, body = _get("/api/v1/risk/status")
        # engine detached -> 503 ENGINE_UNAVAILABLE envelope with request_id
        assert status == 503
        err = body.get("error", {})
        assert err.get("code") == "ENGINE_UNAVAILABLE"
        assert "request_id" in err

    def test_legacy_available_false_semantics(self) -> None:
        status, body = _get("/api/news/state")
        assert status == 200
        assert body.get("available") is False

    def test_engine_toggle_is_backend_validated(self) -> None:
        # With no engine attached the toggle returns 400 (Trading Engine
        # reference not loaded) — the UI must surface this, never fake success.
        req = urllib.request.Request(
            _base() + "/api/engine/toggle",
            data=json.dumps({"active": True}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 400, "engine toggle without an engine must fail loudly"


class TestUiSourceContract:
    """Structural checks on the frontend source (no backend needed)."""

    FRONTEND = os.path.join(REPO_ROOT, "frontend")

    def test_no_scattered_fetch_outside_api_layer(self) -> None:
        offenders: list[str] = []
        for p in pathlib.Path(self.FRONTEND, "src").rglob("*.ts*"):
            rel = _rel(p)
            if "src/api" in rel:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            # Match actual fetch calls, not identifiers containing 'fetch'
            # (e.g. react-query's .refetch()).
            if not _raw_fetch_call(text):
                continue
            if rel in FETCH_OUTSIDE_API_ALLOWLIST:
                continue
            offenders.append(rel)
        assert not offenders, (
            "raw fetch() outside src/api — every call must go through the one "
            f"transport (@/core/transport: getLegacy/getV1/send/drop): {offenders}"
        )

    def test_no_direct_database_or_mt5_access(self) -> None:
        offenders: list[str] = []
        for p in pathlib.Path(self.FRONTEND, "src").rglob("*"):
            if p.suffix not in {".ts", ".tsx", ".css"}:
                continue
            rel = _rel(p)
            text = p.read_text(encoding="utf-8", errors="replace")
            for token in _engine_tokens(text):
                if (rel, token) in ENGINE_TOKEN_ALLOWLIST:
                    continue
                offenders.append(f"{rel}:{token}")
        assert not offenders, f"frontend must never touch engine internals: {offenders}"

    def test_engine_allowlist_entries_are_still_true(self) -> None:
        """An allowlist entry is only honest while the thing it excuses exists.

        A stale entry (token/fetch gone) must be deleted rather than left to
        quietly exempt the file from a future, real violation.
        """
        for rel, reason in FETCH_OUTSIDE_API_ALLOWLIST.items():
            path = pathlib.Path(REPO_ROOT, rel)
            assert path.is_file(), f"fetch allowlist names a missing file: {rel} ({reason})"
            assert _raw_fetch_call(path.read_text(encoding="utf-8", errors="replace")), (
                f"stale fetch allowlist entry {rel} — no fetch() call left, delete it ({reason})"
            )
        for (rel, token), reason in ENGINE_TOKEN_ALLOWLIST.items():
            path = pathlib.Path(REPO_ROOT, rel)
            assert path.is_file(), f"engine-token allowlist names a missing file: {rel} ({reason})"
            text = path.read_text(encoding="utf-8", errors="replace").lower()
            assert token.lower() in text, (
                f"stale engine-token allowlist entry {rel}:{token} — token gone, delete it ({reason})"
            )

    def test_scanners_still_detect_violations(self) -> None:
        """Anti-vacuity: the allowlist must not have hollowed out the scanners."""
        assert _raw_fetch_call('const r = await fetch("/api/status");')
        assert not _raw_fetch_call("decisionQuery.refetch();")
        assert "mt5." in _engine_tokens("mt5.order_send(request)")
        assert "MetaTrader5" in _engine_tokens("import MetaTrader5 as mt5")
        assert "sqlite3" in _engine_tokens("import sqlite3")
        assert "audit.db" in _engine_tokens("open('audit.db')")
        assert ".db'" in _engine_tokens("open('signals.db')")
        assert _engine_tokens('t("dash.broker.title", "Broker status")') == []

    def test_no_fabricated_state_defaults(self) -> None:
        """The UI must never hardcode LIVE/PAPER/running defaults."""
        import pathlib

        offenders: list[str] = []
        for p in pathlib.Path(self.FRONTEND, "src").rglob("*.ts*"):
            text = p.read_text(encoding="utf-8", errors="replace")
            # crude but effective: no `= true` defaults for authoritative flags
            for needle in ("engine_running = true", 'mode = "LIVE"', "LIVE = true"):
                if needle in text:
                    offenders.append(f"{p.name}:{needle}")
        assert not offenders, offenders

    def test_pages_exist(self) -> None:
        base = os.path.join(self.FRONTEND, "src", "pages")
        for page in ("Dashboard", "Trading", "Positions", "Risk", "ML", "Intelligence", "Audit"):
            assert os.path.isdir(os.path.join(base, page)), f"missing page {page}"

    def test_dist_build_artifact_present_after_build(self) -> None:
        # Only enforced when a build ran on this machine (CI builds separately).
        dist = os.path.join(self.FRONTEND, "dist", "index.html")
        if os.path.exists(os.path.join(self.FRONTEND, "dist")):
            assert os.path.exists(dist), "dist/index.html missing — run npm run build"
