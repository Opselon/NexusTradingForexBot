"""Local-model handshake contract tests (2026-11 CLI start wave).

Covers the operator directive: every CLI engine start (the desktop-shortcut
launch lands in ``nexus start``) renders the RED handshake — the active model
was trained locally and may not have verified edge/win-rate, and starring the
repository unlocks the Official Models (benchmark · edge · winrate). The first
start after setup renders the full variant (steps + one-time connect offer).

Contracts pinned here:
- compact + full copy (red, "trained on YOUR machine", edge/win-rate, star);
- OFFICIAL model + verified star → silence (no nag when everything is right);
- local model + unverified → panel + first-run marker written; the next start
  uses the compact variant (no repeated full takeover);
- JSON mode and non-TTY contexts render nothing;
- every seam is failure-isolated: a probe crash must never break the start.
"""

from __future__ import annotations

import types

import pytest
from rich.panel import Panel

from nexus_scalp.cli import handshake as hs
from nexus_scalp.cli import platform_commands as pc
from nexus_scalp.platform_link import LinkStatus, VerificationState


@pytest.fixture(autouse=True)
def _tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Interactive-console simulation: patch the handshake's TTY probe (the
    clean seam — pytest replaces sys.stdout with a capture object whose
    isatty() is False, so patching global stdout fights the capture) and a
    TTY stdin. Output assertions go through capsys (rich writes through
    sys.stdout dynamically)."""
    monkeypatch.setattr(hs, "_tty_interactive", lambda: True)
    monkeypatch.setattr(
        hs.sys, "stdin", types.SimpleNamespace(isatty=lambda: True, readline=lambda: "\n")
    )
    monkeypatch.delenv("NSE_NO_BROWSER", raising=False)


def _patch_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    origin: str,
    verified: bool,
    tmp_path,
) -> list[str]:
    """Pin the two probes (origin + star) and the marker dir; return renders."""
    renders: list[str] = []

    def fake_render(*, full: bool = False) -> Panel:
        renders.append("full" if full else "compact")
        return Panel(f"[fake:{'full' if full else 'compact'}]", border_style="red")

    monkeypatch.setattr(hs, "render_handshake", fake_render)
    monkeypatch.setattr(hs, "_serving_origin_fast", lambda: origin)
    monkeypatch.setattr(hs, "_star_verified", lambda: verified)
    monkeypatch.setattr(hs, "_first_start_marker", lambda: tmp_path / ".handshake-full-v1")
    return renders


# ---------------------------------------------------------------------------
# Copy contract
# ---------------------------------------------------------------------------


def test_compact_handshake_copy_contract() -> None:
    panel = hs.render_handshake(full=False)
    text = str(panel.renderable)
    assert "trained on YOUR machine" in text
    assert "not have verified edge or win-rate" in text
    assert "NOT guaranteed" in text
    assert "Star" in text and hs.REPO in text
    assert "nexus platform connect" in text and "nexus model-official" in text
    assert panel.border_style == "red"


def test_full_handshake_has_steps_and_repo_link() -> None:
    panel = hs.render_handshake(full=True)
    text = str(panel.renderable)
    assert "1." in text and "2." in text and "3." in text
    assert hs.REPO_URL in text
    assert "Star" in text and "Verify" in text


# ---------------------------------------------------------------------------
# maybe_show_handshake policy
# ---------------------------------------------------------------------------


def test_official_and_verified_renders_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    _patch_env(monkeypatch, origin="OFFICIAL", verified=True, tmp_path=tmp_path)
    hs.maybe_show_handshake()
    assert capsys.readouterr().out.strip() == "", "no nag when model + star are both good"


def test_local_unverified_full_on_first_start(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    renders = _patch_env(monkeypatch, origin="USER_TRAINED", verified=False, tmp_path=tmp_path)
    monkeypatch.setattr("builtins.input", lambda *_a: "n")
    hs.maybe_show_handshake()
    assert renders == ["full"], "first start shows the FULL variant"
    assert (tmp_path / ".handshake-full-v1").exists(), "first-start marker recorded"


def test_second_start_uses_compact_variant(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    renders = _patch_env(monkeypatch, origin="USER_TRAINED", verified=False, tmp_path=tmp_path)
    monkeypatch.setattr(hs, "_first_start_marker", lambda: tmp_path / ".handshake-full-v1")
    (tmp_path / ".handshake-full-v1").write_text("seen\n", encoding="utf-8")
    hs.maybe_show_handshake()
    assert renders == ["compact"], "subsequent starts stay compact (no takeover)"
    assert "Continue with GitHub" not in capsys.readouterr().out, "no prompt on later starts"


def test_json_mode_renders_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    _patch_env(monkeypatch, origin="USER_TRAINED", verified=False, tmp_path=tmp_path)
    monkeypatch.setattr(hs, "_tty_interactive", lambda: False)
    hs.maybe_show_handshake(json_mode=True)
    assert capsys.readouterr().out.strip() == "", "JSON mode stays machine-clean"


def test_non_tty_renders_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    _patch_env(monkeypatch, origin="USER_TRAINED", verified=False, tmp_path=tmp_path)
    monkeypatch.setattr(hs, "_tty_interactive", lambda: False)
    hs.maybe_show_handshake()
    assert capsys.readouterr().out.strip() == ""


def test_probe_crash_is_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    """A crashing probe must never break the start (anti-crash contract)."""

    def boom() -> str:
        raise RuntimeError("probe detonation")

    monkeypatch.setattr(hs, "_serving_origin_fast", boom)
    hs.maybe_show_handshake()  # must NOT raise
    assert "probe detonation" not in capsys.readouterr().out + capsys.readouterr().err


# ---------------------------------------------------------------------------
# Provenance probe (fast path)
# ---------------------------------------------------------------------------


def test_serving_origin_fast_starter_note_wins(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A dev starter in the slot is DEV_STARTER even if a sidecar claims OFFICIAL."""
    import nexus_scalp.model_provisioning.service as svc

    monkeypatch.setattr(svc, "serving_model_path", lambda: tmp_path / "model.pt")
    monkeypatch.setattr(svc, "read_install_state", lambda _p: {"origin": "OFFICIAL"})
    monkeypatch.setattr(svc, "_looks_like_starter", lambda _p: True)
    assert hs._serving_origin_fast() == "DEV_STARTER", "starter note wins over sidecar claim"


def test_serving_origin_fast_missing_sidecar_is_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import nexus_scalp.model_provisioning.service as svc

    monkeypatch.setattr(svc, "serving_model_path", lambda: tmp_path / "model.pt")
    monkeypatch.setattr(svc, "read_install_state", lambda _p: {})
    monkeypatch.setattr(svc, "_looks_like_starter", lambda _p: False)
    assert hs._serving_origin_fast() == "UNKNOWN", "unproven bundle never claims silence"


def test_serving_origin_fast_probe_crash_is_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import nexus_scalp.model_provisioning.service as svc

    def boom() -> None:
        raise RuntimeError("sidecar read detonation")

    monkeypatch.setattr(svc, "serving_model_path", boom)
    assert hs._serving_origin_fast() == "UNKNOWN"
