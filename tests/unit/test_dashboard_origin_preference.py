"""Wave 5 Lane C: the operator-facing URL must point at the Go origin.

GO-API-GATE: the Go API server is the product's single origin — it serves
BOTH the API and the React UI. When its plane is up for this process, every
human-facing surface (`nexus start`'s "Web dashboard" panel + browser open,
`nexus dashboard`, `nexus doctor` / `nexus status`) must prefer that origin;
when it is down the answer is byte-identical to the pre-Wave-5 Python origin.

Pinned here:
  * _effective_dashboard_url: Go origin wins, normalized to one trailing slash
  * Go down / empty / whitespace-only -> the Python origin, EXACTLY what
    _dashboard_url returned (the no-regression contract)
  * a resolver fault degrades to the Python origin instead of raising
  * the readiness-gate host/port helpers resolve the Go origin's address and
    degrade to the Python port when the origin carries no port
  * the gate and the operator-facing panel always derive ONE address per boot
"""

from __future__ import annotations

from typing import Any

import pytest

from nexus_scalp.cli import engine_boot as eb
from nexus_scalp.web import go_api_bootstrap as gab

#: The Python origin the tests treat as the pre-Wave-5 answer. The real
#: resolver (resolved_web_port) also reads the repo .env, which a real boot
#: on this machine may have left at any port — pinning the bottom keeps these
#: tests about the Go plane, not about this machine's last boot.
PY_HOST = "127.0.0.1"
PY_PORT = 8081


def _py_url(bind_host: str | None = None, port: int | None = None) -> str:
    """Deterministic stand-in for ``eb._dashboard_url``.

    Same semantics as the real one — a wildcard bind is reported on loopback,
    an explicit host verbatim — minus the .env port resolution that depends on
    what this machine's last boot happened to record.
    """
    host = PY_HOST if bind_host in (None, "", "0.0.0.0", "::", "[::]") else bind_host
    return f"http://{host}:{port if port is not None else PY_PORT}"


@pytest.fixture(autouse=True)
def _deterministic_python_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing here may spawn an OS browser or depend on machine state."""
    monkeypatch.setenv("NSE_NO_BROWSER", "1")
    monkeypatch.delenv("NSE_GO_API_ORIGIN", raising=False)
    for _key in ("NSE_WEB_ACTUAL_PORT", "NSE_WEB_PORT"):
        monkeypatch.delenv(_key, raising=False)
    monkeypatch.setattr(eb, "_dashboard_url", _py_url)


@pytest.fixture
def go_plane_origin(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Set NSE_GO_API_ORIGIN and hand back the value written."""

    def _set(value: str) -> str:
        monkeypatch.setenv("NSE_GO_API_ORIGIN", value)
        return value

    return _set


# --------------------------------------------------------------------------- #
# Go UP: the Go origin is the address humans and the browser get
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("http://127.0.0.1:8087", "http://127.0.0.1:8087/"),
        # No trailing slash -> exactly one is added (paths compose onto this).
        ("http://127.0.0.1:8087/", "http://127.0.0.1:8087/"),
        # A doubled slash is collapsed, never duplicated.
        ("http://127.0.0.1:8087//", "http://127.0.0.1:8087/"),
        # A non-loopback bind is reported where it really listens.
        ("http://192.168.1.10:8090", "http://192.168.1.10:8090/"),
    ],
)
def test_go_origin_wins_when_the_plane_is_up(
    monkeypatch: pytest.MonkeyPatch, env_value: str, expected: str
) -> None:
    monkeypatch.setenv("NSE_GO_API_ORIGIN", env_value)

    # bind_host/port are IGNORED when Go is up: the Go origin is authoritative,
    # so a stale Python port can never be advertised alongside a live plane.
    assert eb._effective_dashboard_url(None, "127.0.0.1", 9999) == expected, (
        "a live Go plane must win over the Python origin"
    )


def test_go_origin_is_read_from_api_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    """The helper trusts only the resolver the bootstrap wrote — no guessing."""
    calls: list[str] = []

    def _fake_origin() -> str | None:
        calls.append("read")
        return "http://127.0.0.1:8099"

    monkeypatch.setattr(gab, "api_origin", _fake_origin)
    assert eb._effective_dashboard_url() == "http://127.0.0.1:8099/"
    assert calls == ["read"]
    # The env value is NOT consulted directly: api_origin() is the one source.
    monkeypatch.setenv("NSE_GO_API_ORIGIN", "http://127.0.0.1:1")
    assert eb._effective_dashboard_url() == "http://127.0.0.1:8099/"


def test_an_explicit_origin_argument_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """`nexus start` passes the origin it just bootstrapped, once."""
    monkeypatch.setenv("NSE_GO_API_ORIGIN", "http://127.0.0.1:8087")
    assert (
        eb._effective_dashboard_url("http://127.0.0.1:9000/", "127.0.0.1", 8080)
        == "http://127.0.0.1:9000/"
    )


# --------------------------------------------------------------------------- #
# Go DOWN: byte-identical to the pre-Wave-5 Python origin (no regression)
# --------------------------------------------------------------------------- #


def test_go_down_matches_legacy_dashboard_url() -> None:
    """No plane -> _effective_dashboard_url IS _dashboard_url's answer."""
    assert eb._effective_dashboard_url() == _py_url()
    assert eb._effective_dashboard_url("", "127.0.0.1", 8081) == _py_url("127.0.0.1", 8081)
    # ``None`` is the same statement: no origin was supplied, so the resolver
    # is consulted and reports no Go plane.
    assert eb._effective_dashboard_url(None, "127.0.0.1", 8081) == _py_url("127.0.0.1", 8081)


@pytest.mark.parametrize("junk", ["", "   ", "\n\t"])
def test_go_down_ignores_a_whitespace_origin(monkeypatch: pytest.MonkeyPatch, junk: str) -> None:
    """An empty/whitespace origin is ignored, never trusted (no dead URL)."""
    monkeypatch.setattr(eb, "_go_api_origin", lambda: junk)
    assert eb._effective_dashboard_url(None, "127.0.0.1", 8080) == _py_url("127.0.0.1", 8080)


def test_origin_lookup_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """An import/resolver fault must degrade to the Python origin, not raise."""

    def _boom() -> str:
        raise RuntimeError("go_api_bootstrap unavailable")

    monkeypatch.setattr(eb, "_go_api_origin", _boom)
    assert eb._effective_dashboard_url(None, "127.0.0.1", 8080) == _py_url("127.0.0.1", 8080)


def test_wildcard_bind_is_still_reported_on_loopback() -> None:
    """A 0.0.0.0 bind is not a browsable URL; loopback is reported."""
    assert eb._effective_dashboard_url(None, "0.0.0.0", 8080) == _py_url("0.0.0.0", 8080)


# --------------------------------------------------------------------------- #
# The readiness gate probes the origin the browser will actually open
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("origin", "host", "port"),
    [
        ("http://127.0.0.1:8087", "127.0.0.1", 8087),
        ("127.0.0.1:8087", "127.0.0.1", 8087),  # scheme-less still parses
        ("http://0.0.0.0:8088/", "0.0.0.0", 8088),
    ],
)
def test_launch_host_port_follow_the_go_origin(origin: str, host: str, port: int) -> None:
    assert eb._effective_launch_host(origin) == host
    assert eb._effective_launch_port(origin, 8080) == port


def test_launch_host_port_fall_back_to_python() -> None:
    """Go down -> the gate probes Python's own host/port, exactly as before."""
    assert eb._effective_launch_host(None) == ""
    assert eb._effective_launch_port(None, 8081) == 8081
    # A scheme-less host with no port keeps Python's port (never port 0).
    assert eb._effective_launch_port("127.0.0.1", 8081) == 8081


# --------------------------------------------------------------------------- #
# Integration shape: the gate and the panel agree on ONE address per boot
# --------------------------------------------------------------------------- #


def test_start_paths_agree_on_one_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate, the panel and the open all derive ONE address per boot.

    Regression guard for the real wiring: `nexus start` resolves the origin
    once and threads it into both the gate (host/port) and the operator-facing
    URL, so the readiness panel can never advertise a different origin than
    the browser opens.
    """
    origin = "http://127.0.0.1:8087"
    monkeypatch.setenv("NSE_GO_API_ORIGIN", origin)

    panel_url = eb._effective_dashboard_url(origin, "127.0.0.1", 8080)
    gate_url = (
        f"http://{eb._effective_launch_host(origin)}:{eb._effective_launch_port(origin, 8080)}/"
    )
    assert panel_url == gate_url == "http://127.0.0.1:8087/"


def test_start_paths_agree_on_python_when_go_is_down() -> None:
    # "" is the documented "Go is not serving" sentinel: nexus start threads
    # the origin it resolved (empty when the plane never came up) into both
    # the gate and the panel.
    panel_url = eb._effective_dashboard_url("", "127.0.0.1", 8081)
    gate_url = f"http://{eb._browser_host('127.0.0.1')}:{eb._effective_launch_port('', 8081)}/"
    # The gate URL keeps its trailing slash (it is the readiness root); the
    # panel keeps the legacy no-slash shape. The origin:port agrees.
    assert (panel_url, gate_url) == ("http://127.0.0.1:8081", "http://127.0.0.1:8081/")
