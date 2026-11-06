"""Native Windows MT5 certification contract tests (DevOps audit + review R4).

The certification script (scripts/windows/mt5_native_cert.py) can only run on
win32 hosts, but the CONTRACT that failed in real CI (runs 128040771175 and
128045834311: mt5.initialize() -> -10005 IPC timeout on fresh headless
installs) is testable here:

  * the tiered attach must EXHAUST transparently and FAIL CLOSED - a failed
    IPC attach may never be reported as success, and tier order must be
    credentialless-first;
  * every attempt is logged (a skipped attempt is not a run);
  * tier C (startup-config) requires demo credentials, deletes its config
    file, and never invents a pass;
  * live broker credentials in the environment are refused outright;
  * deterministic terminal ownership (leftover instances are terminated).

These tests run on every PR via tests/critical_suite.txt (quality lane).
"""

from __future__ import annotations

import importlib.util
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "windows" / "mt5_native_cert.py"

_spec = importlib.util.spec_from_file_location("mt5_native_cert", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


class _FakeMT5NeverReady:
    """initialize() always fails with the observed -10005 IPC timeout."""

    def __init__(self) -> None:
        self.calls = 0

    def initialize(self, **_kw: Any) -> bool:
        self.calls += 1
        return False

    def last_error(self) -> tuple[int, str]:
        return (-10005, "IPC timeout")


class _FakeMT5ReadyAfterRetry:
    def __init__(self, ready_on_call: int) -> None:
        self.calls = 0
        self._ready_on = ready_on_call

    def initialize(self, **_kw: Any) -> bool:
        self.calls += 1
        return self.calls >= self._ready_on

    def last_error(self) -> tuple[int, str]:
        return (-10005, "IPC timeout")


def _result() -> Any:
    return _mod.CertResult(1, "test")


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _s: None)


# ---------------------------------------------------------------------------
# Tiered attach: fail-closed exhaustion
# ---------------------------------------------------------------------------


def test_attach_exhausts_all_tiers_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The observed CI failure mode (-10005 forever) must end in (False,
    exhausted...) with every attempt logged - never a pass."""
    fake = _FakeMT5NeverReady()
    result = _result()
    monkeypatch.setattr(_mod, "_kill_terminal_instances", lambda: None)
    monkeypatch.setattr(_mod, "_demo_credentials", lambda: None)
    monkeypatch.delenv("MT5_DEMO_LOGIN", raising=False)
    monkeypatch.delenv("MT5_DEMO_PASSWORD", raising=False)
    monkeypatch.delenv("MT5_DEMO_SERVER", raising=False)

    class _FakePopen:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            self.pid = 1717

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    ok, strategy = _mod._attach_with_strategy(fake, str(tmp_path / "terminal64.exe"), result)

    assert ok is False
    assert strategy.startswith("exhausted:A,B")
    assert fake.calls == 3 + 2  # tier A: 3 attempts, tier B: 2 attempts
    notes = [s for s in result.steps if s["status"] == "INFO"]
    attempted = [n for n in notes if "attempt" in n["detail"]]
    # every attempt was logged (a skipped attempt is not a run)
    assert len(attempted) == fake.calls


def test_attach_tier_order_credentialless_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Tier A (credentialless) must be attempted before tier C (startup
    config); a tier-A success must never touch broker credentials."""
    result = _result()

    tier_order: list[str] = []

    real_init = _mod._try_initialize

    def spy_init(mt5: Any, path: str, result_: Any, attempts: int, label: str) -> bool:
        tier_order.append(label)
        return real_init(mt5, path, result_, attempts, label)

    monkeypatch.setattr(_mod, "_try_initialize", spy_init)
    monkeypatch.setattr(_mod, "_kill_terminal_instances", lambda: None)
    monkeypatch.setattr(
        _mod, "_demo_credentials", lambda: {"login": "1", "password": "x", "server": "s"}
    )
    fake2 = _FakeMT5ReadyAfterRetry(2)  # succeeds on the 2nd tier-A attempt

    ok, strategy = _mod._attach_with_strategy(fake2, str(tmp_path / "t.exe"), result)

    assert ok is True
    assert strategy == "A:wheel-spawn-attach"
    assert tier_order == ["A (wheel spawn/attach)"]


def test_attach_tier_c_requires_credentials_and_cleans_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without credentials tier C is unavailable (no invented pass); with
    credentials its config file is deleted even when the attach fails."""
    result = _result()
    monkeypatch.setattr(_mod, "_kill_terminal_instances", lambda: None)

    class _FakePopen:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            self.pid = 4242

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(_mod, "_demo_credentials", lambda: None)
    monkeypatch.delenv("MT5_DEMO_LOGIN", raising=False)
    monkeypatch.delenv("MT5_DEMO_PASSWORD", raising=False)
    monkeypatch.delenv("MT5_DEMO_SERVER", raising=False)

    fake = _FakeMT5NeverReady()
    ok, strategy = _mod._attach_with_strategy(fake, str(tmp_path / "t.exe"), result)
    assert ok is False
    assert "no C" in strategy

    # With credentials: tier C runs, writes and DELETES its config file.
    result2 = _result()
    monkeypatch.setattr(
        _mod, "_demo_credentials", lambda: {"login": "123", "password": "pw", "server": "srv"}
    )
    ok2, strategy2 = _mod._attach_with_strategy(fake, str(tmp_path / "t2.exe"), result2)
    assert ok2 is False
    assert strategy2 == "exhausted:A,B,C"
    cleanup_notes = [n for n in result2.steps if "config file deleted" in n["detail"]]
    assert cleanup_notes, "tier C must record that its startup config was deleted"


# ---------------------------------------------------------------------------
# Credential hygiene + verdict honesty
# ---------------------------------------------------------------------------


def test_live_credentials_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MT5_LOGIN", "12345")
    with pytest.raises(SystemExit) as exc:
        _mod._refuse_live_credentials()
    assert exc.value.code == 2


def test_demo_credentials_complete_or_none(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in ("MT5_DEMO_LOGIN", "MT5_DEMO_PASSWORD", "MT5_DEMO_SERVER"):
        monkeypatch.delenv(env, raising=False)
    assert _mod._demo_credentials() is None
    monkeypatch.setenv("MT5_DEMO_LOGIN", "123")
    monkeypatch.setenv("MT5_DEMO_PASSWORD", "pw")
    monkeypatch.setenv("MT5_DEMO_SERVER", "srv")
    assert _mod._demo_credentials() == {"login": "123", "password": "pw", "server": "srv"}


def test_failed_step_is_never_reported_pass() -> None:
    result = _mod.CertResult(1, "honesty")
    ok = result.finish(False, "IPC attach failed")
    assert ok is False
    assert result.status == "FAIL"


def test_level3_gates_are_double_locked() -> None:
    """A demo order may only be sent when BOTH --demo-trade and
    NSE_DEMO_TRADE_TEST=true are set (source-contract assertion)."""
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "NSE_DEMO_TRADE_TEST" in src
    assert "demo_trade_requested" in src
    assert _mod.NSE_CI_DEMO_MAGIC != 888101, (
        "test magic must not collide with the engine fallback magic"
    )
