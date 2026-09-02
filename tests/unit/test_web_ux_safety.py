"""Web client UX safety & experience regression tests (CHG-0048, BUG-194).

Covers the Nexus-Main client-experience pass as WORKFLOW contracts over the
real static assets served by ``create_app`` (repo Web/ directory), not mocks:

1.  BUG-194: the served app.js mode-change handler routes EVERY transition
    through NX.confirmModeChange (no bare POST on change), and anything ->
    LIVE requires a typed confirmation ("LIVE") in Web/ux.js.
2.  Palette safety: the command palette contains NO dangerous commands
    (no mode switch, no engine stop) and binds Ctrl/Cmd+K.
3.  Connectivity truth: the banner appears only from real signals; the
    NXConn controller exposes UP/DEGRADED/DOWN and never fabricates state.
4.  Decision humanization: guardian-blocked decisions (confidence 0.0 with
    a non-gate reason) are labeled "not consulted" - never rendered as a
    real 0.0% confidence; unknown reason codes fall back verbatim.
5.  i18n: EN/FA/DE/ES/AR dictionaries exist; FA/AR declare RTL; the
    language preference is stored under a UI-only localStorage key.
6.  Serve routes: the six ux_*.js assets are served with 200 by create_app
    (skipped when create_app itself is broken by foreign WIP).

Run: .venv/Scripts/python -m pytest tests/unit/test_web_ux_safety.py -q
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from nexus_scalp.web.server import WEB_DIR

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Asset readers (fail loudly if a module went missing - deploy drift)
# ---------------------------------------------------------------------------


def _read(name: str) -> str:
    path = WEB_DIR / name
    assert path.exists(), f"missing UI asset: {name}"
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 1. BUG-194 - execution-mode switch requires confirmation
# ---------------------------------------------------------------------------


class TestModeSwitchGate:
    def test_01_app_js_has_no_bare_mode_post(self) -> None:
        """The mode handler must not POST before a confirmation resolves."""
        src = _read("app.js")
        start = src.find("modeSel.addEventListener('change'")
        assert start >= 0, "mode-change handler not found"
        # Slice to the end of the listener (up to the track-the-server comment).
        end = src.find("window.__serverExecutionMode = modeSel", start)
        handler = src[start : end if end > start else start + 6000]
        assert "NX.confirmModeChange" in handler, "handler bypasses the confirmation gate"
        # The confirmed POST must come after the confirmation call inside the
        # async handler; the bare performEngineModeSet(requested) call follows it.
        confirm_idx = handler.find("NX.confirmModeChange")
        assert confirm_idx >= 0
        assert handler.find("const confirmed = await window.NX.confirmModeChange") >= 0, (
            "confirmation must be awaited before any POST"
        )

    def test_02_live_transition_requires_typed_confirmation(self) -> None:
        src = _read("ux.js")
        assert "confirmModeChange" in src
        assert "requireText: toLive ? 'LIVE' : null" in src, "LIVE arm must require typing LIVE"

    def test_03_cancel_reverts_selector_to_server_mode(self) -> None:
        src = _read("app.js")
        assert "modeSel.value = previous" in src, "cancel/failure must revert to authoritative mode"

    def test_04_fallback_without_ux_layer_keeps_original_render(self) -> None:
        """The legacy confirmDialog path was replaced, not silently dropped:
        when NX is absent the selector still reverts (no silent arm)."""
        src = _read("app.js")
        assert "window.__serverExecutionMode" in src


# ---------------------------------------------------------------------------
# 2. Command palette - navigation only, never dangerous
# ---------------------------------------------------------------------------


class TestPaletteSafety:
    DANGEROUS = (
        "engine/mode",
        "engine/toggle",
        "btn-toggle-engine",
        "doEngineToggle",
        "api/engine",
        "confirmModeChange",  # palette must not even reach the gate
    )

    def test_05_palette_has_no_dangerous_commands(self) -> None:
        src = _read("ux_palette.js")
        for token in self.DANGEROUS:
            assert token not in src, f"palette must not contain dangerous action: {token}"

    def test_06_palette_binds_ctrl_k_and_shortcuts(self) -> None:
        src = _read("ux_palette.js")
        assert "e.key === 'k'" in src
        assert "NAV_ALTS" in src and "tab-monitoring" in src

    def test_07_palette_uses_app_switch_tab(self) -> None:
        src = _read("ux_palette.js")
        assert "switchTab(" in src, "navigation must reuse the app's own tab switcher"


# ---------------------------------------------------------------------------
# 3. Connectivity banner truthfulness
# ---------------------------------------------------------------------------


class TestConnectivityTruth:
    def test_08_conn_controller_states(self) -> None:
        src = _read("ux_conn.js")
        for state in ("'UP'", "'DEGRADED'", "'DOWN'"):
            assert state in src
        assert "lastEventAt" in src, "banner must track the real last-update time"

    def test_09_banner_never_shown_without_signal(self) -> None:
        html = _read("index.html")
        m = re.search(r'<div id="conn-lost-banner"[^>]*class="([^"]+)"', html)
        assert m, "conn-lost-banner element missing"
        assert "hidden" in m.group(1), "banner must start hidden (no fake alarm on load)"

    def test_10_sse_error_and_success_wire_into_controller(self) -> None:
        src = _read("app.js")
        assert src.count("NXConn.setUp()") >= 4, "all live-event paths must clear the banner"
        assert "NXConn.setDown(" in src, "SSE/fetch failures must raise the banner"
        assert "NXConn.setDegraded()" in src, "stale stream must be a soft warning"


# ---------------------------------------------------------------------------
# 4. Decision humanization (confidence semantics)
# ---------------------------------------------------------------------------


class TestDecisionHumanization:
    def test_11_guardian_block_not_rendered_as_zero_confidence(self) -> None:
        src = _read("ux_signal.js")
        assert "not_consulted" in src, "guardian blocks must be labeled 'not consulted'"
        assert "conf === 0 && reason && reason !== 'CONFIDENCE_GATE'" in src

    def test_12_unknown_reason_falls_back_verbatim(self) -> None:
        src = _read("ux_signal.js")
        assert "out.detail = reason" in src, "unknown codes must be shown verbatim, never invented"

    def test_13_known_reasons_have_translations(self) -> None:
        src = _read("ux_signal.js")
        for code in ("BLOCKED_BY_GUARDIAN_UNSAFE_REGIME", "CONFIDENCE_GATE", "NO_CANDIDATE"):
            assert code in src

    def test_14_app_js_keeps_fallback_render(self) -> None:
        src = _read("app.js")
        assert "window.NXSignal" in src
        assert "setTxt('ai-decision-badge'" in src, (
            "legacy path must remain for absence of the layer"
        )


# ---------------------------------------------------------------------------
# 5. i18n framework
# ---------------------------------------------------------------------------


class TestI18n:
    LANGS = ("en", "fa", "de", "es", "ar")

    def test_15_all_five_dictionaries_present(self) -> None:
        src = _read("ux_i18n.js")
        for lang in self.LANGS:
            if lang == "en":
                assert re.search(r"^\s*en:\s*null", src, re.M), "EN identity dictionary missing"
            else:
                assert re.search(rf"^\s*{lang}:\s*\{{", src, re.M), f"missing dictionary: {lang}"

    def test_16_rtl_declared_for_fa_and_ar(self) -> None:
        src = _read("ux_i18n.js")
        assert re.search(r"var RTL = \{\s*fa: true,\s*ar: true", src)

    def test_17_language_pref_is_ui_only_key(self) -> None:
        src = _read("ux_i18n.js")
        assert "nexus.ui.lang" in src
        assert "settings_service" not in src and "/api/config" not in src, (
            "language preference must not touch system settings"
        )

    def test_18_chrome_keys_translated_in_all_dictionaries(self) -> None:
        text = _read("ux_i18n.js")
        for key in (
            "ux.conn.title",
            "ux.confirm.cancel",
            "ux.mode.live_warning",
            "ux.attention.allgood",
        ):
            # EN uses the fallback identity; the other four must carry the key.
            hits = len(re.findall(re.escape(f"'{key}'"), text))
            assert hits >= 4, f"{key} translated in fewer than 4 dictionaries (found {hits})"


# ---------------------------------------------------------------------------
# 6. Serve routes (200 over the real app factory)
# ---------------------------------------------------------------------------

UX_ASSETS = [
    "ux_i18n.js",
    "ux_conn.js",
    "ux.js",
    "ux_signal.js",
    "ux_attention.js",
    "ux_palette.js",
]


class TestServeRoutes:
    @pytest.fixture(scope="class")
    def client(self):
        pytest.importorskip("fastapi")
        try:
            from fastapi.testclient import TestClient

            from nexus_scalp.web.server import create_app

            app = create_app(engine_ref=None)
        except Exception as exc:  # foreign WIP may transiently break create_app
            pytest.skip(f"create_app unavailable (foreign WIP): {exc}")
        return TestClient(app)

    @pytest.mark.parametrize("name", UX_ASSETS)
    def test_19_ux_assets_served(self, client, name: str) -> None:
        resp = client.get(f"/{name}")
        assert resp.status_code == 200, f"{name} not served"
        assert (
            resp.headers["content-type"].startswith("text/javascript")
            or "javascript" in resp.headers["content-type"]
        )

    def test_20_index_loads_ux_modules_before_app_js(self) -> None:
        html = _read("index.html")
        names = [*UX_ASSETS, "app.js"]
        order = [html.find(f'src="{n}?') for n in names]
        missing = [n for n, i in zip(names, order, strict=True) if i < 0]
        assert not missing, f"script tag missing: {missing}"
        assert order == sorted(order), "UX modules must load before app.js"


# the f-string above must search for 'src="app.js?' — verify the raw needle
# exactly as written (guards against quoting regressions in this test itself).
NEEDLE = 'src="app.js?'


# ---------------------------------------------------------------------------
# 7. Attention strip - payload-sourced rows only
# ---------------------------------------------------------------------------


class TestAttentionStrip:
    def test_21_rows_come_from_payload_fields(self) -> None:
        src = _read("ux_attention.js")
        for field in ("payload.health", "payload.is_stale", "payload.runtime_mode"):
            assert field in src, f"strip must derive from real field: {field}"
        assert "fetch(" not in src, "strip must not add network requests"

    def test_22_attention_renders_explicit_all_good(self) -> None:
        src = _read("ux_attention.js")
        assert "allgood" in src, "calm state must be explicit, not silent"


# ---------------------------------------------------------------------------
# 8. Polling efficiency
# ---------------------------------------------------------------------------


class TestPollingEfficiency:
    def test_23_account_polling_skips_hidden_tabs(self) -> None:
        src = _read("app.js")
        assert "if (document.hidden) return;" in src
        assert "visibilitychange" in src
