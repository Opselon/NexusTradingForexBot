"""Easy Setup Wizard anti-crash + star-gate UX contract tests (2026-11 upgrade).

Covers the wizard upgrade contract:
- an unexpected exception inside the wizard NEVER escapes raw — it lands in
  ``_wizard_crash`` (friendly panel, crash log with the failing stage, optional
  error-log share, clean auto-close, exit code EXIT_RUNTIME);
- the auto-close countdown never blocks and never crashes;
- the star promo copy (open repo → Star ⭐ → write Y → verify → official
  models with benchmark/edge/winrate) is rendered by the shared panel;
- a NOT_STARRED verdict produces the "not starred yet" recovery loop and the
  user's Y re-verifies — and the loop is bounded (never an infinite nag, never
  a fake pass).
"""

from __future__ import annotations

import select as _select
import types

import pytest
import typer

from nexus_scalp.cli import platform_commands as pc
from nexus_scalp.cli import wizard as wizard_mod
from nexus_scalp.cli.platform_commands import _verification_loop, render_star_promo
from nexus_scalp.platform_link import LinkStatus, VerificationState
from nexus_scalp.release import exit_codes as xc


class _FakeTTY:
    """stdin/stdout double that reports isatty()=True and swallows reads."""

    def isatty(self) -> bool:
        return True

    def readline(self) -> str:
        return "\n"


@pytest.fixture(autouse=True)
def _fake_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """The crash guard and the star loop only act on interactive consoles."""
    fake = _FakeTTY()
    monkeypatch.setattr(wizard_mod.sys, "stdin", fake)
    monkeypatch.setattr(wizard_mod.sys, "stdout", fake)
    monkeypatch.setattr(pc.sys, "stdin", fake)
    monkeypatch.setattr(pc.sys, "stdout", fake)
    monkeypatch.delenv("NSE_NO_BROWSER", raising=False)


# ---------------------------------------------------------------------------
# Crash guard
# ---------------------------------------------------------------------------


def test_setup_cmd_crash_never_escapes_raw(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    """An unexpected exception in the flow exits EXIT_RUNTIME via the guard."""

    def boom(json_mode: bool) -> dict:
        raise RuntimeError("unexpected wizard detonation")

    monkeypatch.setattr(wizard_mod, "_wizard_flow", boom)
    monkeypatch.setattr(wizard_mod.rpaths, "get_logs_dir", lambda: tmp_path)
    monkeypatch.setattr(wizard_mod, "_auto_close", lambda *a, **k: None)
    monkeypatch.setattr("builtins.input", lambda *_a: "n")

    with pytest.raises(typer.Exit) as excinfo:
        wizard_mod.setup_cmd(json_mode=False)

    assert excinfo.value.exit_code == xc.EXIT_RUNTIME
    out = capsys.readouterr().out
    assert "stopped unexpectedly" in out
    # the crash log records the failing stage + traceback
    logs = list(tmp_path.glob("wizard-crash-*.log"))
    assert logs, "crash log must be written"
    content = logs[0].read_text(encoding="utf-8")
    assert "RuntimeError: unexpected wizard detonation" in content
    assert "stage:" in content


def test_crash_log_share_ask_writes_consent(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Answering y at the crash screen requests diagnostics consent."""
    asked: list[str] = []

    def boom(json_mode: bool) -> dict:
        raise ValueError("boom")

    monkeypatch.setattr(wizard_mod, "_wizard_flow", boom)
    monkeypatch.setattr(wizard_mod.rpaths, "get_logs_dir", lambda: tmp_path)
    monkeypatch.setattr(wizard_mod, "_auto_close", lambda *a, **k: None)
    monkeypatch.setattr("builtins.input", lambda *_a: "y")
    monkeypatch.setattr(
        "nexus_scalp.log_shipping.ensure_consent", lambda **k: asked.append("consent") or True
    )

    with pytest.raises(typer.Exit):
        wizard_mod.setup_cmd(json_mode=False)
    assert asked == ["consent"]


def test_auto_close_non_tty_never_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-interactive contexts just sleep the countdown (process exit closes)."""
    sleeps: list[float] = []
    monkeypatch.setattr(wizard_mod, "_sleep_quiet", sleeps.append)
    monkeypatch.setattr(wizard_mod.sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    wizard_mod._auto_close(10.0)
    assert sleeps, "non-TTY must sleep the countdown, not raise"


def test_auto_close_interactive_returns_early_on_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pressing Enter dismisses the countdown immediately."""
    if wizard_mod.sys.platform == "win32":
        monkeypatch.setattr(wizard_mod, "msvcrt", types.SimpleNamespace())  # POSIX-shape guard
    else:
        monkeypatch.setattr(_select, "select", lambda *a, **k: ([object()], [], []))
    wizard_mod._auto_close(10.0)  # returns fast; a hang would fail the suite timeout


# ---------------------------------------------------------------------------
# Star promo + not-starred recovery loop
# ---------------------------------------------------------------------------


def test_star_promo_copy_contract() -> None:
    panel = render_star_promo()
    text = str(panel.renderable)
    assert "Star" in text
    assert "Y" in text
    assert "Verify" in text and "may take" in text
    assert "benchmark" in text and "edge" in text and "winrate" in text
    assert "Opselon/NexusTradingForexBot" in text


class _FakeSvc:
    """Minimal PlatformLinkService stand-in for the loop."""

    def __init__(self, verdicts: list[VerificationState]) -> None:
        self.verdicts = list(verdicts)
        self.reverifies = 0
        self.await_calls = 0

    def await_verification(self, timeout=None, stop=None, on_tick=None) -> LinkStatus:
        self.await_calls += 1
        state = self.verdicts.pop(0) if self.verdicts else VerificationState.NOT_STARRED
        return LinkStatus(state=state, repository="Opselon/NexusTradingForexBot")

    def reverify(self) -> LinkStatus:
        self.reverifies += 1
        state = self.verdicts.pop(0) if self.verdicts else VerificationState.NOT_STARRED
        return LinkStatus(state=state, repository="Opselon/NexusTradingForexBot")


def _answer(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    monkeypatch.setattr("builtins.input", lambda *_a: answer)


def test_not_starred_loop_y_reverifies_to_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pc, "_interactive", lambda: True)
    svc = _FakeSvc([VerificationState.NOT_STARRED, VerificationState.STARRED_VERIFIED])
    _answer(monkeypatch, "y")
    final = _verification_loop(svc, timeout=5)
    assert final.state is VerificationState.STARRED_VERIFIED
    assert svc.reverifies == 1


def test_not_starred_loop_n_returns_without_fake_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pc, "_interactive", lambda: True)
    svc = _FakeSvc([VerificationState.NOT_STARRED])
    _answer(monkeypatch, "n")
    final = _verification_loop(svc, timeout=5)
    assert final.state is VerificationState.NOT_STARRED
    assert svc.reverifies == 0, "declining must not re-verify into a fake pass"


def test_not_starred_loop_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user who stars nothing and keeps answering Y is asked at most 3 times."""
    monkeypatch.setattr(pc, "_interactive", lambda: True)
    svc = _FakeSvc([])  # always NOT_STARRED
    _answer(monkeypatch, "y")
    final = _verification_loop(svc, timeout=5)
    assert final.state is VerificationState.NOT_STARRED
    assert svc.reverifies == 3
