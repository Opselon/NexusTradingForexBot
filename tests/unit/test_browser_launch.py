"""CONTRACT #10 — browser auto-launch seam, readiness gate and start wiring.

SCOPE (Lane D, END-USER-RUNTIME-UI-INTEGRATION): pin the four promises of the
frozen decision #10 —
  1. enablement precedence ``--no-browser`` > ``NSE_NO_BROWSER`` in
     (1,true,yes) > ``stdout.isatty()``,
  2. the launch SEQUENCE: ``/health`` 200 AND ``/`` 200 html, retried <=30s,
     then exactly ONE open of ``http://127.0.0.1:<ACTUAL port>/``,
  3. failure isolation in BOTH directions (a browser fault never fails the
     engine, a backend that never reached the gate never opens a browser),
  4. the ``SERVER READY`` + actual URL + phase diagnostic fallback.

SAFETY RULE: no test here may bind a shared port or open a real window.
The transport is a duck-typed fake opener, ``webbrowser.open`` and the
Windows ``start`` fallback are always monkeypatched away, and the engine is
never started (wiring is asserted from source + ``--help`` instead).

Where the readiness transport could plausibly regress, the gate is asserted
against a LOCAL ephemeral listener is deliberately avoided too — a fake
opener gives the same evidence without touching the network stack.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.cli import browser_launch

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------
class _FakeResponse:
    """Duck-typed ``urllib`` response (status / headers / body / close)."""

    def __init__(
        self,
        status: int = 200,
        ctype: str = "text/html",
        body: bytes = b"<!doctype html><html><body></body></html>",
    ) -> None:
        self.status = status
        self.headers = {"Content-Type": ctype}
        self._body = body

    def read(self, size: int = -1) -> bytes:
        return self._body if size is None or size < 0 else self._body[:size]

    def getcode(self) -> int:
        return self.status

    def close(self) -> None:  # pragma: no cover - parity with urllib
        return None


def _fake_opener(routes: dict[str, Any]) -> Callable[[str], Any]:
    """Map URL -> response (or Exception to raise, or None = unreachable)."""

    def _open(url: str) -> Any:
        value = routes.get(url)
        if isinstance(value, BaseException):
            raise value
        return value

    return _open


READY_ROUTES = {
    "http://127.0.0.1:9/health": _FakeResponse(200, "application/json", b'{"status":"ok"}'),
    "http://127.0.0.1:9/": _FakeResponse(200, "text/html; charset=utf-8"),
}
HEALTH_DOWN_ROUTES: dict[str, Any] = {
    "http://127.0.0.1:9/health": None,
    "http://127.0.0.1:9/": None,
}
NOT_HTML_ROUTES = {
    "http://127.0.0.1:9/health": _FakeResponse(200, "application/json", b"{}"),
    "http://127.0.0.1:9/": _FakeResponse(200, "application/json", b'{"index":true}'),
}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    """Reset seam state and make a real browser window IMPOSSIBLE."""
    browser_launch._launched = False
    browser_launch._no_browser_flag = False
    monkeypatch.delenv("NSE_NO_BROWSER", raising=False)
    # Belt and braces: even a mis-routed test can only reach a stub.
    monkeypatch.setattr(browser_launch, "IS_WINDOWS", False)
    monkeypatch.setattr(browser_launch.webbrowser, "open", lambda url: True)
    yield
    browser_launch._launched = False
    browser_launch._no_browser_flag = False


def _calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(browser_launch.webbrowser, "open", lambda u: seen.append(u) or True)
    return seen


# ---------------------------------------------------------------------------
# 1. the canonical URL is the ACTUAL port, never a remembered default
# ---------------------------------------------------------------------------
def test_canonical_root_url_is_loopback_root_with_actual_port() -> None:
    assert browser_launch.canonical_root_url(8081) == "http://127.0.0.1:8081/"
    assert browser_launch.canonical_root_url(8080) == "http://127.0.0.1:8080/"
    # BUG-147/BUG-267: the port the operator's server really bound wins, so an
    # auto-incremented 8081 must be advertised as 8081 and not silently reset.
    assert "localhost" not in browser_launch.canonical_root_url(8081)


# ---------------------------------------------------------------------------
# 2. enablement precedence (frozen): flag > env > stdout.isatty()
# ---------------------------------------------------------------------------
def test_flag_beats_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "http://127.0.0.1:8080/"
    seen = _calls(monkeypatch)
    monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: True)
    # Baseline: on a TTY with no disable rule, the window opens.
    assert browser_launch.maybe_open_browser(url) is True
    assert seen == [url]
    # CONTRACT #10 rule 1: the --no-browser flag beats everything below it.
    browser_launch.request_no_browser()
    browser_launch._launched = False  # neutralise the one-window guard
    assert browser_launch.auto_open_state()["reason"] == "--no-browser"
    assert browser_launch.maybe_open_browser(url) is False
    assert seen == [url]  # the flag alone stopped the second open


@pytest.mark.parametrize("value", ["1", "true", "True", "yes", "YES", " yes ", "TRUE"])
def test_env_truthy_disables_on_a_tty(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    seen = _calls(monkeypatch)
    monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: True)
    monkeypatch.setenv("NSE_NO_BROWSER", value)
    assert browser_launch.maybe_open_browser("http://127.0.0.1:8080/") is False
    assert seen == []


@pytest.mark.parametrize("value", ["0", "false", "no", "", "off", "banana"])
def test_env_non_truthy_still_opens_on_a_tty(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    seen = _calls(monkeypatch)
    monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: True)
    monkeypatch.setenv("NSE_NO_BROWSER", value)
    assert browser_launch.maybe_open_browser("http://127.0.0.1:8080/") is True
    assert seen == ["http://127.0.0.1:8080/"]


def test_flag_reason_wins_over_env_reason() -> None:
    # Precedence is observable in the diagnostic the SERVER READY line prints.
    reason = browser_launch.auto_open_state(no_browser=True)["reason"]
    assert reason == "--no-browser"


def test_env_reason_reported_before_the_tty_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: True)
    monkeypatch.setenv("NSE_NO_BROWSER", "1")
    state = browser_launch.auto_open_state()
    assert state["enabled"] is False
    assert "NSE_NO_BROWSER" in state["reason"]


def test_non_tty_stdout_disables_auto_open(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _calls(monkeypatch)
    monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: False)
    assert browser_launch.maybe_open_browser("http://127.0.0.1:8080/") is False
    assert seen == []


def test_tty_with_no_disable_opens_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _calls(monkeypatch)
    monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: True)
    assert browser_launch.maybe_open_browser("http://127.0.0.1:8080/") is True
    assert seen == ["http://127.0.0.1:8080/"]


# ---------------------------------------------------------------------------
# 3. never raises — the engine can NEVER inherit a browser fault
# ---------------------------------------------------------------------------
def test_browser_exception_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(url: str) -> bool:
        raise RuntimeError("no default browser")

    monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: True)
    monkeypatch.setattr(browser_launch.webbrowser, "open", _boom)
    assert browser_launch.maybe_open_browser("http://127.0.0.1:8080/") is False


def test_false_return_is_not_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: True)
    monkeypatch.setattr(browser_launch.webbrowser, "open", lambda url: False)
    assert browser_launch.maybe_open_browser("http://127.0.0.1:8080/") is False


def test_empty_url_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: True)
    assert browser_launch.maybe_open_browser("") is False


def test_state_and_report_helpers_never_raise() -> None:
    # doctor consumes these with no guard; they must be total functions.
    assert isinstance(browser_launch.auto_open_state(), dict)
    assert isinstance(browser_launch.report_ready_or_opened({"url": "http://127.0.0.1:1/"}), str)


# ---------------------------------------------------------------------------
# 4. readiness gate: /health 200 AND / 200 html
# ---------------------------------------------------------------------------
def test_probe_ready_when_health_and_html_root_answer() -> None:
    flags = browser_launch.probe_server("http://127.0.0.1:9/", opener=_fake_opener(READY_ROUTES))
    assert flags == {"health": True, "root": True}


def test_probe_adds_trailing_slash_and_probes_health() -> None:
    hits: list[str] = []

    def _open(url: str) -> Any:
        hits.append(url)
        return READY_ROUTES.get(url)

    browser_launch.probe_server("http://127.0.0.1:9", opener=_open)
    assert hits == ["http://127.0.0.1:9/health", "http://127.0.0.1:9/"]


def test_probe_rejects_a_200_that_is_not_html() -> None:
    flags = browser_launch.probe_server("http://127.0.0.1:9/", opener=_fake_opener(NOT_HTML_ROUTES))
    assert flags == {"health": True, "root": False}


def test_probe_body_sniff_accepts_html_without_content_type() -> None:
    routes = {
        "http://127.0.0.1:9/health": _FakeResponse(200, "", b"{}"),
        "http://127.0.0.1:9/": _FakeResponse(200, "", b"  <HTML lang=en></HTML>"),
    }
    assert browser_launch.probe_server("http://127.0.0.1:9/", opener=_fake_opener(routes)) == {
        "health": True,
        "root": True,
    }


def test_probe_reports_health_failure_honestly() -> None:
    routes = {
        "http://127.0.0.1:9/health": _FakeResponse(503, "application/json", b"{}"),
        "http://127.0.0.1:9/": _FakeResponse(200, "text/html"),
    }
    assert browser_launch.probe_server("http://127.0.0.1:9/", opener=_fake_opener(routes)) == {
        "health": False,
        "root": True,
    }


def test_probe_never_raises_when_the_transport_explodes() -> None:
    flags = browser_launch.probe_server(
        "http://127.0.0.1:9/", opener=_fake_opener({"http://127.0.0.1:9/health": OSError("down")})
    )
    assert flags == {"health": False, "root": False}


def test_wait_returns_ready_phase() -> None:
    diag = browser_launch.wait_for_server_ready(
        "http://127.0.0.1:9/", timeout_s=1, opener=_fake_opener(READY_ROUTES)
    )
    assert diag["ready"] is True
    assert diag["phase"] == "READY"
    assert diag["attempts"] >= 1
    assert diag["url"] == "http://127.0.0.1:9/"


def test_wait_times_out_on_health_first(monkeypatch: pytest.MonkeyPatch) -> None:
    diag = browser_launch.wait_for_server_ready(
        "http://127.0.0.1:9/", timeout_s=0, interval_s=0.01, opener=_fake_opener(HEALTH_DOWN_ROUTES)
    )
    assert diag["ready"] is False
    assert diag["phase"] == "TIMEOUT_HEALTH"
    assert diag["health"] is False


def test_wait_times_out_on_the_root_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    diag = browser_launch.wait_for_server_ready(
        "http://127.0.0.1:9/", timeout_s=0, interval_s=0.01, opener=_fake_opener(NOT_HTML_ROUTES)
    )
    assert diag["ready"] is False
    assert diag["phase"] == "TIMEOUT_ROOT"
    assert diag["health"] is True
    assert diag["root"] is False


def test_wait_budget_is_bounded_and_default_is_at_most_30s() -> None:
    assert browser_launch.READY_TIMEOUT_S <= 30.0
    import time

    started = time.monotonic()
    browser_launch.wait_for_server_ready(
        "http://127.0.0.1:9/",
        timeout_s=0.2,
        interval_s=0.05,
        opener=_fake_opener(HEALTH_DOWN_ROUTES),
    )
    assert time.monotonic() - started < 5.0


def test_wait_retries_until_the_backend_comes_up() -> None:
    """The gate is a retry loop, not a single probe: a late bind still wins."""
    calls = {"n": 0}

    def _slow(url: str) -> Any:
        if url.endswith("/health"):
            calls["n"] += 1
            if calls["n"] < 3:
                return None
            return READY_ROUTES["http://127.0.0.1:9/health"]
        return READY_ROUTES["http://127.0.0.1:9/"]

    diag = browser_launch.wait_for_server_ready(
        "http://127.0.0.1:9/", timeout_s=5, interval_s=0.01, opener=_slow
    )
    assert diag["ready"] is True
    assert diag["attempts"] >= 3


# ---------------------------------------------------------------------------
# 5. the launch sequence: wait -> open ONCE -> or never open
# ---------------------------------------------------------------------------
def test_launch_opens_exactly_once_with_the_canonical_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _calls(monkeypatch)
    monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: True)
    diag = browser_launch.launch_when_ready(
        "http://127.0.0.1:9/", opener=_fake_opener(READY_ROUTES)
    )
    assert diag["opened"] is True
    assert diag["reason"] == ""
    assert seen == ["http://127.0.0.1:9/"]


def test_backend_never_ready_means_browser_never_opens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _calls(monkeypatch)
    monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: True)
    diag = browser_launch.launch_when_ready(
        "http://127.0.0.1:9/", timeout_s=0, interval_s=0.01, opener=_fake_opener(HEALTH_DOWN_ROUTES)
    )
    assert diag["ready"] is False
    assert diag["opened"] is False
    assert seen == []  # section 57: a backend failure never opens a browser
    assert "TIMEOUT" in diag["reason"]


def test_gate_passes_but_flag_suppresses_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _calls(monkeypatch)
    monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: True)
    diag = browser_launch.launch_when_ready(
        "http://127.0.0.1:9/", no_browser=True, opener=_fake_opener(READY_ROUTES)
    )
    assert diag["ready"] is True
    assert diag["opened"] is False
    assert seen == []
    assert diag["reason"] == "--no-browser"


def test_gate_passes_but_non_tty_suppresses_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _calls(monkeypatch)
    monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: False)
    diag = browser_launch.launch_when_ready(
        "http://127.0.0.1:9/", opener=_fake_opener(READY_ROUTES)
    )
    assert diag["ready"] is True
    assert diag["opened"] is False
    assert seen == []


def test_launch_never_raises_on_a_broken_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: True)

    def _boom(url: str) -> Any:
        raise RuntimeError("socket meltdown")

    diag = browser_launch.launch_when_ready("http://127.0.0.1:9/", timeout_s=0.1, opener=_boom)
    assert diag["opened"] is False
    assert diag["ready"] is False


# ---------------------------------------------------------------------------
# 6. SERVER READY fallback line
# ---------------------------------------------------------------------------
def test_report_carries_server_ready_url_and_phase() -> None:
    diag = browser_launch.launch_when_ready(
        "http://127.0.0.1:9/",
        no_browser=True,
        opener=_fake_opener(READY_ROUTES),
    )
    line = browser_launch.report_ready_or_opened(diag)
    assert line.startswith("SERVER READY")
    assert "http://127.0.0.1:9/" in line
    assert "phase=READY" in line
    assert "--no-browser" in line


def test_report_on_a_timeout_names_the_failed_phase() -> None:
    diag = browser_launch.launch_when_ready(
        "http://127.0.0.1:9/", timeout_s=0, interval_s=0.01, opener=_fake_opener(HEALTH_DOWN_ROUTES)
    )
    line = browser_launch.report_ready_or_opened(diag)
    assert line.startswith("SERVER READY")
    assert "TIMEOUT_HEALTH" in line


def test_report_of_an_opened_window_says_so() -> None:
    diag = {"url": "http://127.0.0.1:8081/", "opened": True}
    assert browser_launch.report_ready_or_opened(diag) == (
        "CONTROL CENTER OPENED http://127.0.0.1:8081/"
    )


# ---------------------------------------------------------------------------
# 7. worker isolation (the engine never blocks on a browser)
# ---------------------------------------------------------------------------
def test_worker_runs_the_sequence_and_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _calls(monkeypatch)
    monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: True)
    captured: dict[str, Any] = {}

    # The worker builds its own transport (real urllib) — force readiness by
    # stubbing the wait so the test never touches a socket.
    monkeypatch.setattr(
        browser_launch,
        "wait_for_server_ready",
        lambda url, **kw: {
            "url": url,
            "ready": True,
            "phase": "READY",
            "health": True,
            "root": True,
            "attempts": 1,
            "elapsed_s": 0.0,
        },
    )
    thread = browser_launch.start_browser_worker("http://127.0.0.1:9/", reporter=captured.update)
    assert isinstance(thread, threading.Thread)
    assert thread.daemon is True
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert captured.get("opened") is True
    assert seen == ["http://127.0.0.1:9/"]


def test_worker_survives_a_reporter_that_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: True)
    monkeypatch.setattr(
        browser_launch,
        "wait_for_server_ready",
        lambda url, **kw: {
            "url": url,
            "ready": False,
            "phase": "TIMEOUT_HEALTH",
            "health": False,
            "root": False,
            "attempts": 1,
            "elapsed_s": 0.0,
        },
    )

    def _bad_reporter(diag: dict) -> None:
        raise RuntimeError("console gone")

    thread = browser_launch.start_browser_worker("http://127.0.0.1:9/", reporter=_bad_reporter)
    assert thread is not None
    thread.join(timeout=5)
    assert not thread.is_alive()


# ---------------------------------------------------------------------------
# 8. start-path wiring (source + --help; the engine is never actually started)
# ---------------------------------------------------------------------------
def test_start_help_advertises_no_browser() -> None:
    from typer.testing import CliRunner

    from nexus_scalp.cli.main import app

    res = CliRunner().invoke(app, ["start", "--help"])
    assert res.exit_code == 0
    assert "--no-browser" in res.stdout


def test_facade_reexports_the_frozen_seam() -> None:
    import nexus_scalp.cli.main as cmain

    assert cmain.maybe_open_browser is browser_launch.maybe_open_browser
    assert "maybe_open_browser" in cmain.__all__


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_engine_boot_gate_is_wired_into_the_serve_loop() -> None:
    src = _read("src/nexus_scalp/cli/engine_boot.py")
    assert "_open_control_center_when_ready" in src
    assert "SERVER READY" in src
    # The gate must be scheduled on the real loop, and the ACTUAL bind port
    # (uvicorn_config.port) must be what is opened — never a literal 8080/8081.
    assert "asyncio.create_task(" in src
    assert (
        "_open_control_center_when_ready(_browser_host(bind_host), int(uvicorn_config.port))" in src
    )
    assert 'f"http://{host}:{port}/"' in src
    # No hardcoded canonical URL anywhere in the start path.
    assert "http://127.0.0.1:8080/" not in src
    assert "http://127.0.0.1:8081/" not in src


def test_engine_boot_records_and_forwards_the_flag() -> None:
    src = _read("src/nexus_scalp/cli/engine_boot.py")
    assert "browser_launch.request_no_browser()" in src
    # The daemon child is a NEW process — it must receive the flag itself.
    assert '"--no-browser"' in src


def test_legacy_launcher_wires_the_same_gate() -> None:
    src = _read("NexusTradingForexBot.py")
    assert '"--no-browser"' in src
    assert "request_no_browser" in src
    assert "start_browser_worker" in src
    assert "canonical_root_url(int(uvicorn_config.port))" in src
    assert "http://127.0.0.1:8080/" not in src


def test_doctor_reports_the_auto_open_decision() -> None:
    src = _read("src/nexus_scalp/cli/doctor.py")
    assert "auto_open_state" in src
    assert "BROWSER AUTO-OPEN" in src
