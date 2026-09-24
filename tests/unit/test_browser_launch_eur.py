"""Lane D unit tests: browser auto-launch seam + engine readiness wiring.

Covers CONTRACT frozen decisions #5 (open_control_center), #6 (open ONLY after
the readiness gate, real bind port, --no-browser), #10 (enablement precedence)
and the doctor/status Control Center surface.

SAFETY: no test here may open a real browser window or bind a shared port —
webbrowser/subprocess are monkeypatched everywhere and the engine boot is never
started, only its readiness/launch coroutine is driven with a fake probe.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nexus_scalp.cli import browser_launch

REPO_ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_browser_launch_state(monkeypatch: pytest.MonkeyPatch):
    """Module-level singletons are process-global: start AND end every test clean."""
    browser_launch._launched = False
    browser_launch._no_browser_flag = False
    monkeypatch.delenv("NSE_NO_BROWSER", raising=False)
    yield
    browser_launch._launched = False
    browser_launch._no_browser_flag = False


# ---------------------------------------------------------------------------
# CONTRACT #5 — open_control_center
# ---------------------------------------------------------------------------
class TestOpenControlCenterContract5:
    def test_success_returns_true_and_records_launch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []
        monkeypatch.setattr(browser_launch.webbrowser, "open", lambda u: calls.append(u) or True)

        assert browser_launch.open_control_center("http://127.0.0.1:8080/") is True
        assert calls == ["http://127.0.0.1:8080/"]
        assert browser_launch._launched is True

    def test_falsey_driver_result_is_a_failure_not_a_launch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # webbrowser.open returning False means NO window was issued, so the
        # contract ("True only after the URL open was actually issued") demands
        # False — and the guard must stay unset so a retry is still possible.
        monkeypatch.setattr(browser_launch.webbrowser, "open", lambda u: False)
        monkeypatch.setattr(browser_launch, "IS_WINDOWS", False)

        assert browser_launch.open_control_center("http://127.0.0.1:8080/") is False
        assert browser_launch._launched is False

    def test_one_window_per_normal_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []
        monkeypatch.setattr(browser_launch.webbrowser, "open", lambda u: calls.append(u) or True)

        first = browser_launch.open_control_center("http://127.0.0.1:8080/")
        second = browser_launch.open_control_center("http://127.0.0.1:8081/")
        assert first is True
        assert second is False, "second call must not open a second window"
        assert calls == ["http://127.0.0.1:8080/"]

    def test_force_bypasses_the_one_window_guard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []
        monkeypatch.setattr(browser_launch.webbrowser, "open", lambda u: calls.append(u) or True)

        assert browser_launch.open_control_center("http://127.0.0.1:8080/") is True
        assert browser_launch.open_control_center("http://127.0.0.1:8080/", force=True) is True
        assert len(calls) == 2, "force=True is an explicit user action: a second window is allowed"

    @pytest.mark.parametrize("bad", ["", "   ", None, 42])
    def test_never_raises_on_bad_input(self, bad: object) -> None:
        assert browser_launch.open_control_center(bad) is False  # type: ignore[arg-type]

    def test_never_raises_when_driver_raises_and_logs_safe_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(url: str) -> bool:
            raise RuntimeError(f"driver blew up for {url}?token=supersecretvalue")

        monkeypatch.setattr(browser_launch.webbrowser, "open", explode)
        monkeypatch.setattr(browser_launch, "IS_WINDOWS", False)

        # The seam logs through structlog's PRINT logger factory, which never
        # reaches the stdlib root (caplog is blind to it) - use structlog's own
        # capture hook.
        from structlog.testing import capture_logs

        with capture_logs() as logs:
            result = browser_launch.open_control_center(
                "http://admin:hunter2@127.0.0.1:8080/?token=supersecretvalue"
            )

        assert result is False, "failure path returns False, never raises"
        assert logs, "the seam must log its failure path"
        assert any(entry.get("log_level") == "warning" for entry in logs), (
            "the safe reason is logged at WARNING level"
        )
        text = " ".join(str(entry.get("event", "")) for entry in logs)
        assert "RuntimeError" in text, "the safe reason names the failure"
        assert "Traceback" not in text and 'File "' not in text, "no stack traces in user output"
        assert "hunter2" not in text and "supersecretvalue" not in text, "no credentials leaked"

    def test_windows_falls_back_to_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spawned: list[list[str]] = []

        class _FakeProc:
            pass

        def fake_popen(argv, **kwargs):
            spawned.append(list(argv))
            return _FakeProc()

        monkeypatch.setattr(browser_launch, "IS_WINDOWS", True)
        monkeypatch.setattr(browser_launch.webbrowser, "open", lambda u: False)
        monkeypatch.setattr(browser_launch.subprocess, "Popen", fake_popen)

        assert browser_launch.open_control_center("http://127.0.0.1:8080/") is True
        assert len(spawned) == 1 and spawned[0][0] == "cmd.exe"
        assert browser_launch._launched is True

    def test_posix_never_falls_back_to_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def forbid_popen(*args, **kwargs):
            raise AssertionError("posix must use webbrowser only (CONTRACT #5)")

        monkeypatch.setattr(browser_launch, "IS_WINDOWS", False)
        monkeypatch.setattr(browser_launch.webbrowser, "open", lambda u: False)
        monkeypatch.setattr(browser_launch.subprocess, "Popen", forbid_popen)

        assert browser_launch.open_control_center("http://127.0.0.1:8080/") is False

    def test_concurrent_callers_still_open_one_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        def record(url: str) -> bool:
            calls.append(url)
            return True

        monkeypatch.setattr(browser_launch.webbrowser, "open", record)
        results: list[bool] = []
        barrier = threading.Barrier(8)

        def worker() -> None:
            barrier.wait(timeout=5)
            results.append(browser_launch.open_control_center("http://127.0.0.1:8080/"))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert results.count(True) == 1 and len(calls) == 1, (
            "the guard is re-checked under the lock"
        )


# ---------------------------------------------------------------------------
# CONTRACT #10 — enablement precedence: flag > NSE_NO_BROWSER > isatty
# ---------------------------------------------------------------------------
class TestEnablementPrecedence:
    @pytest.mark.parametrize("val", ["1", "true", "True", "YES", "  yes "])
    def test_env_knob_disables(self, monkeypatch: pytest.MonkeyPatch, val: str) -> None:
        monkeypatch.setenv("NSE_NO_BROWSER", val)
        monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: True)
        opened: list[str] = []
        monkeypatch.setattr(browser_launch.webbrowser, "open", lambda u: opened.append(u) or True)

        assert browser_launch.maybe_open_browser("http://127.0.0.1:8080/") is False
        assert opened == [], "disabled means no window at all"

    @pytest.mark.parametrize("val", ["0", "false", "no", ""])
    def test_falsy_env_value_falls_through_to_isatty(
        self, monkeypatch: pytest.MonkeyPatch, val: str
    ) -> None:
        monkeypatch.setenv("NSE_NO_BROWSER", val)
        monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: True)
        opened: list[str] = []
        monkeypatch.setattr(browser_launch.webbrowser, "open", lambda u: opened.append(u) or True)

        assert browser_launch.maybe_open_browser("http://127.0.0.1:8080/") is True
        assert opened == ["http://127.0.0.1:8080/"]

    def test_flag_beats_env_and_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # --no-browser is the highest-precedence switch: it must win even when
        # stdout is a real terminal.
        monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: True)
        monkeypatch.delenv("NSE_NO_BROWSER", raising=False)
        opened: list[str] = []
        monkeypatch.setattr(browser_launch.webbrowser, "open", lambda u: opened.append(u) or True)

        assert browser_launch.maybe_open_browser("http://127.0.0.1:8080/", no_browser=True) is False
        browser_launch.request_no_browser()
        assert browser_launch.maybe_open_browser("http://127.0.0.1:8080/") is False
        assert opened == []

    def test_non_tty_stdout_disables_auto_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: False)
        opened: list[str] = []
        monkeypatch.setattr(browser_launch.webbrowser, "open", lambda u: opened.append(u) or True)

        assert browser_launch.maybe_open_browser("http://127.0.0.1:8080/") is False
        assert opened == []

    def test_tty_without_any_disable_knob_opens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(browser_launch, "_stdout_isatty", lambda: True)
        opened: list[str] = []
        monkeypatch.setattr(browser_launch.webbrowser, "open", lambda u: opened.append(u) or True)

        assert browser_launch.maybe_open_browser("http://127.0.0.1:8080/") is True
        assert opened == ["http://127.0.0.1:8080/"]

    def test_auto_open_state_always_reports_a_reason(self) -> None:
        state = browser_launch.auto_open_state()
        assert set(state) == {"enabled", "reason"} and state["reason"]

    def test_redaction_strips_credentials_and_query(self) -> None:
        redacted = browser_launch._redact(
            "http://admin:hunter2@127.0.0.1:8080/dashboard?token=abc123#frag"
        )
        assert "hunter2" not in redacted and "admin@" not in redacted
        assert "abc123" not in redacted
        assert "127.0.0.1:8080/dashboard" in redacted


# ---------------------------------------------------------------------------
# CONTRACT #6 — the engine opens ONLY after the readiness gate, real port
# ---------------------------------------------------------------------------
class TestReadinessGateWiring:
    def test_opens_after_gate_passes_at_the_actual_port(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from nexus_scalp.cli import engine_boot

        attempts = {"n": 0}

        def probe(host: str, port: int) -> tuple[bool, bool, str]:
            attempts["n"] += 1
            if attempts["n"] < 3:
                return False, False, "starting"
            return True, True, "ready"

        opened: list[str] = []
        monkeypatch.setattr(engine_boot, "_probe_control_center", probe)
        monkeypatch.setattr(
            browser_launch, "maybe_open_browser", lambda url, **kw: opened.append(url) or True
        )

        out = asyncio.run(
            engine_boot._open_control_center_when_ready(
                "127.0.0.1", 9099, timeout=5.0, poll_interval=0.01
            )
        )

        assert out["ready"] is True and out["opened"] is True
        assert opened == ["http://127.0.0.1:9099/"], (
            "the ACTUAL resolved port, never 8080 hardcoded"
        )
        assert attempts["n"] >= 3, "the gate must be probed repeatedly before opening"

    @pytest.mark.parametrize(
        ("health", "index", "phase"),
        [
            (False, False, "starting"),
            (False, False, "health HTTP 503"),
            (True, False, "index HTTP 404"),
            (True, False, "index not html"),
        ],
    )
    def test_never_opens_before_the_gate(
        self,
        monkeypatch: pytest.MonkeyPatch,
        health: bool,
        index: bool,
        phase: str,
    ) -> None:
        from nexus_scalp.cli import engine_boot

        opened: list[str] = []
        monkeypatch.setattr(
            engine_boot, "_probe_control_center", lambda h, p: (health, index, phase)
        )
        monkeypatch.setattr(
            browser_launch, "maybe_open_browser", lambda url, **kw: opened.append(url) or True
        )

        out = asyncio.run(
            engine_boot._open_control_center_when_ready(
                "127.0.0.1", 8080, timeout=0.2, poll_interval=0.02
            )
        )

        assert out["ready"] is False
        assert out["opened"] is False
        assert opened == [], "no browser window before health 200 AND index html"
        assert phase in out["phase"], "timeout reports the phase diagnostic"

    def test_browser_failure_never_fails_the_gate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nexus_scalp.cli import engine_boot

        monkeypatch.setattr(
            engine_boot, "_probe_control_center", lambda h, p: (True, True, "ready")
        )

        def explode(url: str, **kwargs: object) -> bool:
            raise RuntimeError("browser subsystem unavailable")

        monkeypatch.setattr(browser_launch, "maybe_open_browser", explode)

        out = asyncio.run(
            engine_boot._open_control_center_when_ready(
                "127.0.0.1", 8080, timeout=1.0, poll_interval=0.01
            )
        )

        assert out["ready"] is True, "readiness is a server fact, independent of the browser"
        assert out["opened"] is False

    def test_probe_failure_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nexus_scalp.cli import engine_boot

        def boom(host: str, port: int) -> tuple[bool, bool, str]:
            raise OSError("probe exploded")

        opened: list[str] = []
        monkeypatch.setattr(engine_boot, "_probe_control_center", boom)
        monkeypatch.setattr(
            browser_launch, "maybe_open_browser", lambda url, **kw: opened.append(url) or True
        )

        out = asyncio.run(
            engine_boot._open_control_center_when_ready(
                "127.0.0.1", 8080, timeout=0.2, poll_interval=0.02
            )
        )
        assert out["ready"] is False and opened == []

    def test_env_disable_is_honored_at_the_gate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nexus_scalp.cli import engine_boot

        monkeypatch.setenv("NSE_NO_BROWSER", "1")
        monkeypatch.setattr(
            engine_boot, "_probe_control_center", lambda h, p: (True, True, "ready")
        )
        opened: list[str] = []
        monkeypatch.setattr(browser_launch.webbrowser, "open", lambda u: opened.append(u) or True)

        out = asyncio.run(
            engine_boot._open_control_center_when_ready(
                "127.0.0.1", 8080, timeout=1.0, poll_interval=0.01
            )
        )
        assert out["ready"] is True and out["opened"] is False and opened == []

    def test_start_web_and_engine_composes_the_gate(self) -> None:
        from nexus_scalp.cli import engine_boot

        src = inspect.getsource(engine_boot._start_web_and_engine)
        assert "_open_control_center_when_ready" in src, "CONTRACT #6: gate wired into start"
        assert "ShutdownSupervisor" in src and "wait_for_shutdown" in src
        assert "KeyboardInterrupt" in src
        # The gate reads the resolved bind host/port — never a hardcoded 8080/8081.
        assert "_browser_host(bind_host)" in src and ", port)" in src

    def test_probe_requires_health_200_and_html_index(self) -> None:
        """The probe contract itself: a 200-html gate, not a bare connect."""
        from nexus_scalp.cli import engine_boot

        src = inspect.getsource(engine_boot._probe_control_center)
        assert "/health" in src and 'f"{base}/"' in src
        assert "text/html" in src
        assert "ProxyHandler({})" in src, "loopback self-probe must bypass proxies"


# ---------------------------------------------------------------------------
# CLI surface — `nexus start --no-browser` + help text (CONTRACT #6/#8)
# ---------------------------------------------------------------------------
class TestStartCliSurface:
    def test_help_documents_auto_open_and_no_browser(self) -> None:
        res = runner.invoke(app_import(), ["start", "--help"])
        assert res.exit_code == 0, res.output
        out = res.output
        assert "--no-browser" in out
        assert "Control Center" in out
        assert "auto-open" in out.lower()

    def test_flag_is_parsed_and_recorded(self, tmp_path: Path) -> None:
        res = runner.invoke(
            app_import(),
            ["start", "--no-browser", "--config", str(tmp_path / "missing.yaml")],
        )
        # Config validation still fails loudly (unchanged behavior); what we
        # pin is that the flag reached the command body.
        assert res.exit_code != 0
        assert browser_launch._no_browser_flag is True

    def test_daemon_child_receives_the_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import nexus_scalp.cli.main as cmain

        captured: dict[str, list[str]] = {}
        monkeypatch.setattr(cmain, "_spawn_daemon", lambda cmd: captured.setdefault("cmd", cmd))

        res = runner.invoke(
            app_import(),
            [
                "start",
                "--daemon",
                "--no-browser",
                "--no-animate",
                "--config",
                str(REPO_ROOT / "configs" / "base.yaml"),
            ],
        )
        assert res.exit_code == 0, res.output
        assert captured.get("cmd"), "daemon spawn seam not reached"
        assert "--no-browser" in captured["cmd"], "the flag must travel to the child process"

    def test_daemon_command_is_unchanged_without_the_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import nexus_scalp.cli.main as cmain

        captured: dict[str, list[str]] = {}
        monkeypatch.setattr(cmain, "_spawn_daemon", lambda cmd: captured.setdefault("cmd", cmd))

        res = runner.invoke(
            app_import(),
            [
                "start",
                "--daemon",
                "--no-animate",
                "--config",
                str(REPO_ROOT / "configs" / "base.yaml"),
            ],
        )
        assert res.exit_code == 0, res.output
        assert "--no-browser" not in captured.get("cmd", [])
        assert browser_launch._no_browser_flag is False


# ---------------------------------------------------------------------------
# doctor / status surface (CONTRACT #8/#9)
# ---------------------------------------------------------------------------
class TestDoctorStatusSurface:
    def test_frontend_seam_status_degrades_gracefully(self) -> None:
        from nexus_scalp.cli import doctor as doctor_mod

        payload = doctor_mod._frontend_seam_status()
        assert set(payload) == {"frontend", "auto_open"}
        assert payload["frontend"]["state"] in {"AVAILABLE", "MISSING", "UNAVAILABLE"}
        assert set(payload["auto_open"]) == {"enabled", "reason"}
        assert isinstance(payload["frontend"]["dist_present"], (bool, type(None)))

    def test_missing_frontend_assets_module_reports_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lane A's seam may not exist yet: the report must degrade, not crash."""
        import builtins

        from nexus_scalp.cli import doctor as doctor_mod

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "nexus_scalp.web.frontend_assets":
                raise ImportError("frontend_assets not written yet (Lane A in flight)")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        payload = doctor_mod._frontend_seam_status()
        assert payload["frontend"]["state"] == "UNAVAILABLE"
        assert "frontend_assets" in payload["frontend"]["reason"]

    def test_status_json_surfaces_control_center(self) -> None:
        res = runner.invoke(app_import(), ["status", "--json"])
        assert res.exit_code == 0, res.output
        payload = json.loads(res.stdout)
        cc = payload.get("control_center")
        assert isinstance(cc, dict), "nexus status --json must surface the new seam"
        assert cc["frontend"]["state"] in {"AVAILABLE", "MISSING", "UNAVAILABLE"}
        assert "enabled" in cc["auto_open"] and cc["auto_open"]["reason"]

    def test_doctor_json_surfaces_control_center(self) -> None:
        res = runner.invoke(app_import(), ["doctor", "--json"])
        assert res.exit_code in (0, 1), res.output
        payload = json.loads(res.stdout)
        assert "control_center" in payload
        assert payload["control_center"]["frontend"]["state"] in {
            "AVAILABLE",
            "MISSING",
            "UNAVAILABLE",
        }

    def test_doctor_human_renders_the_block(self) -> None:
        from rich.console import Console

        from nexus_scalp.cli import doctor as doctor_mod

        console = Console(width=100, record=True)
        console.print(doctor_mod._control_center_table())
        rendered = console.export_text(clear=True)
        assert "FRONTEND" in rendered and "BROWSER AUTO-OPEN" in rendered
        assert "CONTROL CENTER" in rendered


def app_import():
    from nexus_scalp.cli.main import app

    return app
