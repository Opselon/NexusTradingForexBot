"""UPD-RETRY-001 — connection-level failures must not be retried.

The update subsystem's discovery + download layers used to retry ANY failure
of ``urllib.request.urlopen`` up to 3 times with exponential backoff
(``time.sleep(min(2**attempt * 2, 30))`` -> 2s + 4s + 8s = 14s of pure
sleeping). That is correct for a TRANSIENT HTTP CODE — the server answered
503/429 and explicitly asked the client to try again — but it is pure waste
for a CONNECTION-LEVEL failure: connection refused, DNS failure, unreachable
route. In those cases NO HTTP response was ever received, so there is no
server behind the socket hinting recovery. The retry loop re-binds the same
timeout against the same dead peer ``max_retries + 1`` times.

Measured cost of the old behavior (this machine, ``127.0.0.1:1``, nothing
listening):

    fetch_releases(timeout=1)  18.01s   = 4 x 1.0s socket wait + 14s sleeps
    fetch_releases(timeout=2)  22.00s   = 4 x 2.0s socket wait + 14s sleeps
    SafeDownloader.download(timeout=2)  36.01s

Five of the eight slowest tests in the push gate were this exact shape, and
the CLI's real-world failure mode (operator with no egress, GitHub
unreachable) paid the same tax on every ``nexus update check``.

Contract pinned by this file:

1. a refused/unreachable socket is reported ONCE — the wall clock is the
   caller-supplied ``timeout``, not ``timeout * 4 + 14``;
2. the failure still surfaces as the same honest status
   (NETWORK_UNAVAILABLE), never as "no update" (spec 41);
3. TRANSIENT HTTP CODES (408/429/5xx) are still retried — that behavior is
   pinned by ``test_up46_retry_on_transient_github`` / ``test_up47`` in
   test_release_update_phase17.py and must NOT regress here.

The probe uses a socket to a port with no listener, which is the fastest
deterministic "connection refused" available and needs no network egress.
"""

from __future__ import annotations

import socket
import time
import urllib.error
from pathlib import Path

import pytest

from nexus_scalp.release.update_engine.discovery import (
    GitHubDiscoveryError,
    UpdateDiscovery,
)
from nexus_scalp.release.update_engine.downloader import SafeDownloader
from nexus_scalp.release.update_engine.orchestrator import UpdateOrchestrator

#: A port with nothing listening. connect() fails immediately with ECONNREFUSED
#: An ephemeral port with nothing behind it. The OS guarantees nothing accepts
#: there, so the host answers in whichever way it answers a bare SYN — either
#: ECONNREFUSED (Linux/macOS) or a dropped SYN (Windows firewalls). BOTH are
#: "no server answered" and both are what this contract is about.
_PROBE_PORT = 1
_PROBE_SOCKET_SEC = 0.25
#: Wall-clock ceiling for a single probe. Generous relative to a refused socket
#: (~instant) and to a single ``timeout=`` unit; the OLD behavior blew past
#: this by 4x-18x, so the bound stays tight enough to catch a regression while
#: never being so tight that a loaded CI runner false-fails.
_PER_PROBE_CEILING_SEC = 6.0


def _probe_kind() -> str:
    """How this host says 'no server answered': refused | timeout | listener.

    A connect to an unbound port is ECONNREFUSED on Linux/macOS. On hosts
    whose firewall drops the SYN instead (this Windows box does it for every
    low port) the connect raises socket.timeout. Both mean no HTTP response
    is ever received, so both are valid for the retry contract — and the
    'timeout' flavor is exactly the one whose cost the old loop multiplied.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(_PROBE_SOCKET_SEC)
    try:
        probe.connect(("127.0.0.1", _PROBE_PORT))
    except TimeoutError:
        return "timeout"
    except OSError:
        return "refused"
    finally:
        probe.close()
    return "listener"  # pragma: no cover - guarded by the skipif below


pytestmark = pytest.mark.skipif(
    _probe_kind() == "listener",
    reason="probe endpoint unexpectedly has a listener; the timing contract "
    "cannot be measured against a live socket",
)


def _dead_url(path: str) -> str:
    return f"http://127.0.0.1:{_PROBE_PORT}{path}"


def _orchestrator(tmp_path: Path) -> UpdateOrchestrator:
    app = tmp_path / "app"
    user = tmp_path / "user"
    app.mkdir()
    user.mkdir()
    (app / "NexusScalpEngine.exe").write_bytes(b"MZ")
    return UpdateOrchestrator(
        app_root=app,
        user_root=user,
        update_home=user / "update",
        installed_version="9.0.0",
    )


# ---------------------------------------------------------------------------
# 1. discovery: one probe, no backoff
# ---------------------------------------------------------------------------
def test_discovery_refused_socket_is_not_retried(monkeypatch) -> None:
    """A connection-refused endpoint must cost exactly ONE timeout unit,
    not (max_retries + 1) units plus exponential sleeps.

    Failing here means the retry loop came back: either the wall clock grew
    (sleeps or repeated socket waits) or the urlopen call count exceeded 1.
    """
    calls = {"n": 0}
    real_urlopen = urllib.request.urlopen

    def counting_urlopen(req, timeout=None):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return real_urlopen(req, timeout=timeout)

    monkeypatch.setattr(urllib.request, "urlopen", counting_urlopen)
    import nexus_scalp.release.update_engine.discovery as discovery_mod

    monkeypatch.setattr(discovery_mod.urllib.request, "urlopen", counting_urlopen)

    t0 = time.perf_counter()
    with pytest.raises(GitHubDiscoveryError):
        UpdateDiscovery.fetch_releases(api_url=_dead_url("/releases"), timeout=1)
    elapsed = time.perf_counter() - t0

    assert calls["n"] == 1, (
        f"a dead socket must be probed exactly once, got {calls['n']} "
        f"attempts — the exponential-backoff retry loop regressed"
    )
    assert elapsed < _PER_PROBE_CEILING_SEC, (
        f"dead-socket discovery took {elapsed:.2f}s (one timeout unit "
        f"expected) — connection-level retrying with sleeps is back"
    )


def test_discovery_refused_socket_reports_network_unavailable(tmp_path: Path) -> None:
    """The failure surfaces as the honest status, never as 'no update'.

    This is the safety side of the same change: removing the retry must not
    remove the truthful classification (spec 41). A dead endpoint stays a
    NETWORK_UNAVAILABLE plan, and the installed application is untouched.
    """
    orch = _orchestrator(tmp_path)
    t0 = time.perf_counter()
    plan = orch.check(api_url=_dead_url("/releases"), timeout=1)
    elapsed = time.perf_counter() - t0

    assert plan["status"] in ("NETWORK_UNAVAILABLE", "NETWORK_ERROR"), (
        f"a dead endpoint must never read as a healthy plan: {plan['status']}"
    )
    assert plan["error_code"] in ("NETWORK_UNAVAILABLE", "NETWORK_ERROR")
    assert (tmp_path / "app" / "NexusScalpEngine.exe").exists(), (
        "the installed application must be untouched by a failed check"
    )
    assert elapsed < _PER_PROBE_CEILING_SEC, (
        f"check() took {elapsed:.2f}s for a dead endpoint — retry loop returned"
    )


# ---------------------------------------------------------------------------
# 2. download: one probe, no backoff, partial preserved
# ---------------------------------------------------------------------------
def test_downloader_refused_socket_is_not_retried(tmp_path: Path) -> None:
    """SafeDownloader must raise on the first connection failure.

    The old path retried 3x: 4 socket waits of ``timeout`` each plus 14s of
    sleeps, measured at 36.01s for timeout=2. The pinned behavior is a single
    socket wait and an immediate honest error.
    """
    dl = SafeDownloader(tmp_path / "dl")
    part = tmp_path / "dl" / "p.zip.part"
    part.write_bytes(b"PARTIAL")

    t0 = time.perf_counter()
    with pytest.raises((urllib.error.URLError, TimeoutError, OSError)):
        dl.download(
            _dead_url("/nope.zip"),
            "p.zip",
            expected_sha256="ab" * 32,
            timeout=2,
        )
    elapsed = time.perf_counter() - t0

    assert elapsed < _PER_PROBE_CEILING_SEC, (
        f"download of a dead endpoint took {elapsed:.2f}s — the connection-level "
        f"retry loop (4 socket waits + 2s/4s/8s sleeps) regressed"
    )
    # The staged partial must survive a failed attempt exactly as before, so a
    # later successful transfer still resumes (BUG-122's contract).
    assert part.exists() and part.read_bytes() == b"PARTIAL", (
        "a failed download must not delete or alter the staged partial"
    )


# ---------------------------------------------------------------------------
# 3. the retry contract that MUST survive (negative control)
# ---------------------------------------------------------------------------
def test_transient_http_error_is_still_retried(monkeypatch) -> None:
    """UPD-RETRY-001 removed retries for CONNECTION failures only.

    A server that ANSWERS 503 is explicitly saying "try again", and that
    retry must survive this change. Double-checks the discrimination: the fix
    is not "never retry", it is "retry an answer, never a silence".
    """
    import urllib.error as urlerr

    calls = {"n": 0}

    class _FakeResp:
        def read(self) -> bytes:
            import json as _j

            return _j.dumps([{"tag_name": "v9.1.0"}]).encode()

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *a: object) -> bool:
            return False

    def fake_urlopen(req, timeout=None):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise urlerr.HTTPError("https://api", 503, "unavailable", {}, None)
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    import nexus_scalp.release.update_engine.discovery as discovery_mod

    monkeypatch.setattr(discovery_mod.urllib.request, "urlopen", fake_urlopen)

    out = UpdateDiscovery.fetch_releases(max_retries=2)
    assert calls["n"] == 2, (
        f"a transient 503 must be retried exactly once (max_retries=2 ceiling "
        f"already reached after one retry), got {calls['n']} calls"
    )
    assert out[0]["tag_name"] == "v9.1.0"
