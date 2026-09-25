"""GO-API-GATE: go_api_bootstrap failure-isolation contract.

PINS the contract that lets `nexus start` boot with or without a Go
toolchain, with NO network and NO Go compiler required to run this suite:

  * version parsing: 1.27 accepted, 1.20 rejected, garbage rejected
  * port helpers: a busy port is detected and a free one is found above it
  * resolve_api_addr: NSE_GO_ADDR wins, else python_port + 1
  * build_go_api: missing go-api dir, failing go exe, and the cache-HIT path
    (a stamp matching _source_hash skips the compile entirely)
  * shipped binary: a release-baked nexus-api.exe is used AS-IS and is never
    rebuilt (the end user has no Go toolchain)
  * boot_go_api: no toolchain -> None and NSE_GO_API_ORIGIN is left unset
  * GoApiSupervisor.start: a binary that does not exist fails FAST, it does
    not hang until the readiness timeout

The whole point of this subsystem is that a Go failure never stops the
product: every failure path returns (False, ...) / None and the FastAPI app
keeps serving on its own port. These tests are that invariant's proof.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from types import SimpleNamespace

import pytest

from nexus_scalp.web import go_api_bootstrap as gab

# --------------------------------------------------------------------------- #
# Version parsing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # Real output shape on the shipped platform.
        ("go version go1.27.1 windows/amd64", True),
        ("go version go1.23.0 linux/amd64", True),
        ("go version go1.21 darwin/arm64", True),  # the declared floor
        # Below the floor: the router/auth use generics + slog, 1.21 is minimum.
        ("go version go1.20 windows/amd64", False),
        ("go version go1.15 linux/amd64", False),
        # Garbage / hostile input must never parse as usable.
        ("", False),
        ("not a go binary", False),
        ("go version", False),
        ("garbage with no version token at all", False),
        # A float that is not a version must not accidentally match.
        ("go version go.notaversion linux/amd64", False),
        # No patch component still satisfies >= 1.21.
        ("go version go1.22 linux/amd64", True),
    ],
)
def test_version_ok_parses_real_output(line: str, expected: bool) -> None:
    assert gab._version_ok(line) is expected


# --------------------------------------------------------------------------- #
# Port helpers
# --------------------------------------------------------------------------- #


def _bind_free_port(host: str = "127.0.0.1") -> tuple[socket.socket, int]:
    """Bind a socket the caller keeps open, so a port is genuinely busy."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, 0))
    s.listen(1)
    return s, s.getsockname()[1]


def _a_free_port() -> int:
    """A port that is free right now (bind + close, SO_REUSEADDR)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_port_in_use_detects_a_live_listener() -> None:
    sock, port = _bind_free_port()
    try:
        assert gab._port_in_use("127.0.0.1", port) is True
    finally:
        sock.close()


def test_port_in_use_negative_for_unbound() -> None:
    """A port nobody is listening on is not in use."""
    assert gab._port_in_use("127.0.0.1", _a_free_port()) is False


def test_free_port_above_skips_a_busy_port() -> None:
    """A busy port is skipped and the next free one is returned."""
    sock, busy = _bind_free_port()
    try:
        got = gab._free_port_above("127.0.0.1", busy)
        assert got >= busy
        assert got != busy  # the contract: never return the busy one
        assert gab._port_in_use("127.0.0.1", got) is False
    finally:
        sock.close()


def test_free_port_above_passes_through_a_free_port() -> None:
    """A free port is returned unchanged (no needless skip)."""
    free = _a_free_port()
    if not gab._port_in_use("127.0.0.1", free):  # guard against a race
        assert gab._free_port_above("127.0.0.1", free) == free


# --------------------------------------------------------------------------- #
# resolve_api_addr
# --------------------------------------------------------------------------- #


@pytest.fixture()
def clean_addr_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NSE_GO_ADDR", raising=False)
    monkeypatch.delenv("NSE_WEB_HOST", raising=False)


def test_resolve_api_addr_defaults_to_the_preferred_port(
    clean_addr_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No NSE_GO_ADDR: the preferred port is used verbatim. The +1 is added
    by the CALLER (engine_boot._boot_go_api_plane passes python_port + 1),
    so resolve_api_addr itself must not add it again."""
    host, port = gab.resolve_api_addr(8086)
    assert host == "127.0.0.1"
    assert port == 8086


def test_resolve_api_addr_honours_nse_go_addr(
    clean_addr_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NSE_GO_ADDR", "127.0.0.1:9099")
    host, port = gab.resolve_api_addr(8086)
    assert (host, port) == ("127.0.0.1", 9099)


def test_resolve_api_addr_nse_go_addr_host_part_wins(
    clean_addr_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NSE_GO_ADDR", "0.0.0.0:9099")
    host, port = gab.resolve_api_addr(8086)
    assert (host, port) == ("0.0.0.0", 9099)


def test_resolve_api_addr_bare_port_uses_web_host(
    clean_addr_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NSE_GO_ADDR", "9100")
    monkeypatch.setenv("NSE_WEB_HOST", "127.0.0.1")
    host, port = gab.resolve_api_addr(8086)
    assert (host, port) == ("127.0.0.1", 9100)


def test_resolve_api_addr_unparseable_falls_back(
    clean_addr_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Garbage NSE_GO_ADDR is ignored, not fatal — the boot must survive."""
    monkeypatch.setenv("NSE_GO_ADDR", "not-a-port")
    host, port = gab.resolve_api_addr(8086)
    assert host == "127.0.0.1"
    assert port == 8086  # unparseable env ignored; preferred port kept


def test_resolve_api_addr_busy_port_advances(
    clean_addr_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The configured port being bound is not a hard failure."""
    sock, busy = _bind_free_port()
    try:
        monkeypatch.setenv("NSE_GO_ADDR", f"127.0.0.1:{busy}")
        host, port = gab.resolve_api_addr(busy)
        assert host == "127.0.0.1"
        assert port != busy
        assert port >= busy
    finally:
        sock.close()


# --------------------------------------------------------------------------- #
# build_go_api
# --------------------------------------------------------------------------- #


class _FakeProc(SimpleNamespace):
    """subprocess.run() stand-in: rc + stderr, no toolchain involved."""

    returncode: int
    stdout: str
    stderr: str


@pytest.fixture()
def isolated_build(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Point BUILD_CACHE + GO_API_DIR at temp dirs so nothing real is touched.

    ALSO isolates the SHIPPED-binary resolver: a real release build can leave
    artifacts/go_build/nexus-api.exe (or a packaged copy) on this machine, and
    build_go_api must prefer the locally compiled cache in these tests, not a
    stray shipped copy discovered through the real resolution paths.
    """
    cache = tmp_path / "go_build"
    monkeypatch.setattr(gab, "BUILD_CACHE", cache)
    monkeypatch.delenv("NSE_GO_API_ORIGIN", raising=False)
    monkeypatch.setattr(gab, "resolve_go_api_binary", lambda: None)
    return cache


def _run_capture(cmd, **kw):  # noqa: ANN202 - test stand-in
    """Default replacement: must never run, it records the invocation."""
    raise AssertionError(f"subprocess.run should not be called, got: {cmd}")


def test_build_returns_false_when_go_api_dir_missing(
    isolated_build, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(gab, "GO_API_DIR", tmp_path / "does-not-exist")
    monkeypatch.setattr(subprocess, "run", _run_capture)
    ok, msg = gab.build_go_api("/usr/bin/go")
    assert ok is False
    assert "missing" in msg


def test_build_returns_false_when_go_build_fails(
    isolated_build, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A failing compile returns (False, stderr tail) — never an exception."""
    src = tmp_path / "go-api"
    src.mkdir()
    monkeypatch.setattr(gab, "GO_API_DIR", src)

    def fake_run(cmd, **kw):  # noqa: ANN202
        return _FakeProc(returncode=1, stdout="", stderr="main.go:10: undefined: x")

    monkeypatch.setattr(subprocess, "run", fake_run)

    ok, msg = gab.build_go_api("/usr/bin/go")
    assert ok is False
    assert "go build failed" in msg
    assert "undefined: x" in msg  # the real cause reaches the operator


def test_build_success_writes_stamp_and_binary(
    isolated_build, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A successful compile writes the binary + the hash stamp."""
    src = tmp_path / "go-api"
    src.mkdir()
    monkeypatch.setattr(gab, "GO_API_DIR", src)

    def fake_run(cmd, **kw):  # noqa: ANN202
        # Emulate the compiler producing the artifact.
        gab._binary_path().parent.mkdir(parents=True, exist_ok=True)
        gab._binary_path().write_bytes(b"FAKE BINARY")
        return _FakeProc(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    ok, msg = gab.build_go_api("/usr/bin/go")
    assert ok is True
    assert gab._binary_path().is_file()
    stamp = isolated_build / "source.sha256"
    assert stamp.is_file()
    assert stamp.read_text(encoding="utf-8").strip() == gab._source_hash()


def test_build_cache_hit_skips_the_compile(
    isolated_build, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A stamp matching _source_hash makes build_go_api skip the build."""
    src = tmp_path / "go-api"
    src.mkdir()
    monkeypatch.setattr(gab, "GO_API_DIR", src)

    isolated_build.mkdir(parents=True, exist_ok=True)
    # _binary_path() appends ".exe" on Windows — write it via the helper so
    # the cached artifact is at the exact path build_go_api looks for.
    bin_path = gab._binary_path()
    bin_path.write_bytes(b"CACHED BINARY")
    (isolated_build / "source.sha256").write_text(
        gab._source_hash(), encoding="utf-8"
    )

    monkeypatch.setattr(subprocess, "run", _run_capture)  # would explode if run
    ok, msg = gab.build_go_api("/usr/bin/go")
    assert ok is True
    assert msg == str(bin_path)
    assert bin_path.read_bytes() == b"CACHED BINARY"  # untouched


def test_build_stale_stamp_triggers_rebuild(
    isolated_build, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A stale stamp (source changed since) must NOT be trusted."""
    src = tmp_path / "go-api"
    src.mkdir()
    monkeypatch.setattr(gab, "GO_API_DIR", src)

    isolated_build.mkdir(parents=True, exist_ok=True)
    gab._binary_path().write_bytes(b"STALE")
    (isolated_build / "source.sha256").write_text("0" * 64, encoding="utf-8")

    def fake_run(cmd, **kw):  # noqa: ANN202
        gab._binary_path().write_bytes(b"FRESH")
        return _FakeProc(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, msg = gab.build_go_api("/usr/bin/go")
    assert ok is True
    assert gab._binary_path().read_bytes() == b"FRESH"


# --------------------------------------------------------------------------- #
# shipped (release) binary
# --------------------------------------------------------------------------- #


def test_shipped_binary_is_used_as_is_and_never_built(
    isolated_build, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A release-baked nexus-api.exe wins over the source-build path, and the
    compiler is NEVER invoked (the end user's machine has no Go toolchain)."""
    src = tmp_path / "go-api"
    src.mkdir()
    monkeypatch.setattr(gab, "GO_API_DIR", src)

    shipped = tmp_path / "shipped" / gab.SHIPPED_BINARY_DEST / gab.SHIPPED_BINARY_NAME
    shipped.parent.mkdir(parents=True, exist_ok=True)
    shipped.write_bytes(b"RELEASE BUILD")
    monkeypatch.setattr(gab, "resolve_go_api_binary", lambda: shipped)

    monkeypatch.setattr(subprocess, "run", _run_capture)  # would explode if run
    ok, msg = gab.build_go_api("/usr/bin/go")
    assert ok is True
    assert msg == str(shipped)
    # The shipped bytes are untouched: no rebuild, no cache write.
    assert shipped.read_bytes() == b"RELEASE BUILD"
    assert not (isolated_build / gab.SHIPPED_BINARY_NAME).exists()


def test_shipped_binary_absent_falls_back_to_source_build(
    isolated_build, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A dev checkout (no packaged copy) still compiles from source."""
    src = tmp_path / "go-api"
    src.mkdir()
    monkeypatch.setattr(gab, "GO_API_DIR", src)
    monkeypatch.setattr(gab, "resolve_go_api_binary", lambda: None)

    def fake_run(cmd, **kw):
        gab._binary_path().parent.mkdir(parents=True, exist_ok=True)
        gab._binary_path().write_bytes(b"DEV BUILD")
        return _FakeProc(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, msg = gab.build_go_api("/usr/bin/go")
    assert ok is True
    assert msg == str(gab._binary_path())


def test_shipped_binary_needs_no_toolchain(
    isolated_build, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A packaged install boots the Go plane with NO go toolchain at all."""
    src = tmp_path / "go-api"
    src.mkdir()
    monkeypatch.setattr(gab, "GO_API_DIR", src)
    monkeypatch.setattr(gab, "_find_go", lambda: None)

    shipped = tmp_path / "bundle" / gab.SHIPPED_BINARY_DEST / gab.SHIPPED_BINARY_NAME
    shipped.parent.mkdir(parents=True, exist_ok=True)
    shipped.write_bytes(b"RELEASE BUILD")
    monkeypatch.setattr(gab, "resolve_go_api_binary", lambda: shipped)

    ok, msg = gab.build_go_api()
    assert ok is True
    assert msg == str(shipped)


def test_no_toolchain_and_no_shipped_binary_is_python_only(
    isolated_build, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Neither a toolchain nor a shipped binary -> (False, ...), never raises."""
    src = tmp_path / "go-api"
    src.mkdir()
    monkeypatch.setattr(gab, "GO_API_DIR", src)
    monkeypatch.setattr(gab, "_find_go", lambda: None)
    monkeypatch.setattr(gab, "resolve_go_api_binary", lambda: None)

    monkeypatch.setattr(subprocess, "run", _run_capture)  # would explode if run
    ok, msg = gab.build_go_api()
    assert ok is False
    assert "toolchain" in msg


def test_shipped_resolver_finds_the_onedir_layout(tmp_path) -> None:
    """resolve_go_api_binary discovers the PyInstaller onedir placement."""
    bundle = tmp_path / "NexusScalpEngine"
    internal = bundle / "_internal" / gab.SHIPPED_BINARY_DEST
    internal.mkdir(parents=True, exist_ok=True)
    (internal / gab.SHIPPED_BINARY_NAME).write_bytes(b"RELEASE BUILD")

    class _FakeSys:  # minimal stand-in exposing what the resolver reads
        _MEIPASS = None
        executable = str(bundle / "NexusScalpEngine.exe")

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(gab, "sys", _FakeSys, raising=False)
        monkeypatch.chdir(bundle)
        found = gab.resolve_go_api_binary()
    finally:
        monkeypatch.undo()
    assert found == internal / gab.SHIPPED_BINARY_NAME


# --------------------------------------------------------------------------- #
# boot_go_api
# --------------------------------------------------------------------------- #


def test_boot_returns_none_without_a_toolchain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No usable `go` on the machine -> None, and the origin env stays unset.

    This is the ONLY fallback path in the whole subsystem, so its side
    effects are pinned too: NSE_GO_API_ORIGIN must not be set, or a
    frontend dev proxy would point at a Go plane that does not exist.
    """
    monkeypatch.setattr(gab, "_find_go", lambda: None)
    monkeypatch.setattr(gab, "resolve_go_api_binary", lambda: None)
    monkeypatch.delenv("NSE_GO_API_ORIGIN", raising=False)

    def boom(*a, **k):  # noqa: ANN202
        raise AssertionError("build_go_api must not run without a toolchain")

    monkeypatch.setattr(gab, "build_go_api", boom)

    started = time.monotonic()
    assert gab.boot_go_api("127.0.0.1", 8086, 8087) is None
    assert time.monotonic() - started < 2.0  # fails fast, no retries

    assert gab.api_origin() is None
    assert "NSE_GO_API_ORIGIN" not in os.environ


def test_boot_returns_none_when_the_build_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gab, "_find_go", lambda: "/usr/bin/go")
    monkeypatch.setattr(gab, "build_go_api", lambda *a, **k: (False, "boom"))
    monkeypatch.delenv("NSE_GO_API_ORIGIN", raising=False)
    assert gab.boot_go_api("127.0.0.1", 8086, 8087) is None
    assert "NSE_GO_API_ORIGIN" not in os.environ


# --------------------------------------------------------------------------- #
# GoApiSupervisor.start
# --------------------------------------------------------------------------- #


def test_supervisor_start_fails_fast_on_missing_binary(tmp_path) -> None:
    """A nonexistent binary must fail to launch, not hang until the timeout.

    The readiness loop only runs after Popen succeeds, so a launch failure
    returns immediately — a bug that made this wait the full 20s would
    visibly stall every Python-only boot on a machine with no Go.
    """
    missing = tmp_path / "nexus-api-does-not-exist"
    sup = gab.GoApiSupervisor(
        python_origin="http://127.0.0.1:65500",
        addr=("127.0.0.1", 65501),
        binary=str(missing),
    )
    started = time.monotonic()
    ok, msg = sup.start(ready_timeout_s=20.0)
    elapsed = time.monotonic() - started
    assert ok is False
    assert "could not launch" in msg
    assert elapsed < 5.0, f"start hung for {elapsed:.1f}s on a missing binary"
    # Nothing was spawned and nothing is left to clean up.
    assert sup._proc is None
    sup.stop()


def test_supervisor_stop_is_a_noop_before_start(tmp_path) -> None:
    """stop() before start() must never raise."""
    sup = gab.GoApiSupervisor(
        python_origin="http://127.0.0.1:65500",
        addr=("127.0.0.1", 65501),
        binary=str(tmp_path / "nexus-api"),
    )
    sup.stop()  # no child, no error
    assert sup._proc is None
