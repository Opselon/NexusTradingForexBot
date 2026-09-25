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
        import pathlib

        offenders: list[str] = []
        for p in pathlib.Path(self.FRONTEND, "src").rglob("*.ts*"):
            if "src\\api" in str(p) or "src/api" in str(p):
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            # Match actual fetch calls, not identifiers containing 'fetch'
            # (e.g. react-query's .refetch()).
            import re

            if re.search(r"\bfetch\s*\(", text):
                offenders.append(str(p))
        assert not offenders, f"raw fetch() outside src/api: {offenders}"

    def test_no_direct_database_or_mt5_access(self) -> None:
        import pathlib

        forbidden = ("sqlite3", "mt5.", "MetaTrader5", "audit.db", ".db'")
        offenders: list[str] = []
        for p in pathlib.Path(self.FRONTEND, "src").rglob("*"):
            if p.suffix not in {".ts", ".tsx", ".css"}:
                continue
            text = p.read_text(encoding="utf-8", errors="replace").lower()
            for token in forbidden:
                if token in text:
                    offenders.append(f"{p.name}:{token}")
        assert not offenders, f"frontend must never touch engine internals: {offenders}"

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
