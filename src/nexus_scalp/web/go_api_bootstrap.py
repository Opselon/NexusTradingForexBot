"""Go API build + boot integration (GO-API-GATE).

The Go API server (go-api/) is the product's API entrypoint: every HTTP
request reaches Go, which proxies the Python runtime for facts it does not
own. This module makes that invisible to the operator — `nexus start` /
`NexusTradingForexBot.py` builds and launches the Go server automatically
so the user only has to open the app.

Design contract:
  * Python is the process the user starts; Go is a child it supervises.
  * Go NEVER mints an auth token and NEVER owns a port the user did not
    ask for. It binds the API port and forwards to Python's own origin.
  * A missing or broken Go toolchain must never stop the product. The
    Python FastAPI app remains a complete, working API surface on its own;
    a Go failure logs a clear warning and the app still serves on its own
    port. This is the ONLY fallback path in the whole system.
  * The build is cached: a content hash of the Go source tree is stamped
    into the binary directory; an unchanged tree skips the rebuild.

Failure modes handled explicitly (proven by tests):
  - go binary absent                     -> WARN, Python-only boot, exit 0
  - go build fails                       -> WARN + stderr tail, Python-only
  - shipped (release) binary present     -> used AS-IS, never rebuilt
  - configured API port already bound    -> pick the next free port, log it
  - Go server never becomes ready        -> WARN + kill child, Python-only
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parents[3]
GO_API_DIR = REPO_ROOT / "go-api"
MAIN_PACKAGE = "./cmd/nexus-api"

#: SHIPPED Go API binary name. MUST stay in lockstep with the release
#: orchestrator (scripts/build/build_release.ps1 --add-data "nexus-api.exe")
#: and the Inno Setup payload. A release build compiles this once; the end
#: user's machine has no Go toolchain, so the packaged copy is authoritative.
SHIPPED_BINARY_NAME = "nexus-api.exe"

#: Where the shipped binary lands inside the PyInstaller onedir
#: (build_release.ps1 --add-data ";go-api"). The runtime resolver looks here,
#: so the release ship path and the runtime lookup cannot drift.
SHIPPED_BINARY_DEST = "go-api"

#: Build cache lives OUTSIDE the source tree so a `git clean` of go-api
#: never wipes a working compiler artifact mid-session.
BUILD_CACHE = REPO_ROOT / "artifacts" / "go_build"

_API_PORT_ENV = "NSE_GO_ADDR"
_PY_ORIGIN_ENV = "NSE_PYTHON_ORIGIN"


def _log(msg: str, *args: Any) -> None:
    """Structured-ish line, always safe (logger may be unconfigured)."""
    try:
        from nexus_scalp.observability.logging import get_logger

        get_logger("go_api").info(msg, *args) if args else get_logger("go_api").info(msg)
    except Exception:
        # Boot-time path: structlog may not be configured yet. Never fail.
        print(f"[go-api] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# Toolchain discovery
# --------------------------------------------------------------------------- #


def _find_go() -> str | None:
    """Locate a usable `go` executable. Returns None if unavailable.

    Order: PATH, then the side-installed toolchain under the user profile
    (the migration environment installs go1.27 there), then common Windows
    and /usr/local locations.
    """
    candidates: list[str] = []

    on_path = shutil.which("go") or shutil.which("go.exe")
    if on_path:
        candidates.append(on_path)

    home = os.path.expanduser("~")
    for rel in (
        "go-toolchain/go/bin/go.exe",  # side install (this host)
        "go/bin/go.exe",  # GOPATH-style install
        "scoop/apps/go/current/bin/go.exe",  # scoop
    ):
        p = Path(home) / rel
        if p.is_file():
            candidates.append(str(p))

    for abs_p in ("/usr/local/go/bin/go", "/usr/bin/go", "C:/Program Files/Go/bin/go.exe"):
        if Path(abs_p).is_file():
            candidates.append(abs_p)

    for cand in candidates:
        if _go_works(cand):
            return cand
    return None


def _go_works(exe: str) -> bool:
    """A toolchain is usable only if `go version` exits 0 and reports 1.21+.

    The route table and the auth middleware use generics + slog, so 1.21 is
    the floor; the build itself targets go 1.27 in go.mod.
    """
    try:
        out = subprocess.run(
            [exe, "version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if out.returncode != 0:
        return False
    return _version_ok(out.stdout.strip())


def _version_ok(version_line: str) -> bool:
    """Parse `go version go1.27.1 windows/amd64` and require >= 1.21."""
    parts = version_line.split()
    for tok in parts:
        if tok.startswith("go") and "." in tok[2:]:
            nums = tok[2:].split(".")[:2]
            try:
                major, minor = int(nums[0]), int(nums[1])
            except ValueError:
                continue
            return (major, minor) >= (1, 21)
    return False


# --------------------------------------------------------------------------- #
# Build (cached)
# --------------------------------------------------------------------------- #


def _source_hash() -> str:
    """Content hash of every tracked .go file + go.mod/go.sum.

    The hash is the cache key: unchanged source means the cached binary is
    still valid, so a warm boot does not pay a compile.
    """
    h = hashlib.sha256()
    h.update(str(GO_API_DIR).encode())
    files: list[Path] = []
    for pat in ("*.go", "go.mod", "go.sum"):
        files.extend(sorted(GO_API_DIR.rglob(pat)))
    for f in sorted(files):
        try:
            rel = f.relative_to(GO_API_DIR).as_posix()
            h.update(rel.encode())
            h.update(f.read_bytes())
        except OSError:
            continue
    return h.hexdigest()


def _binary_path() -> Path:
    """Path of the locally COMPILED binary (dev/source build cache)."""
    suffix = ".exe" if os.name == "nt" else ""
    return BUILD_CACHE / f"nexus-api{suffix}"


def _shipped_binary_candidates() -> list[Path]:
    """Where a RELEASE-BUILT Go API binary can live at runtime.

    Mirrors the frontend_assets.py resolution style: first the PyInstaller
    bundle (frozen _MEIPASS, then the onedir layout next to the running
    EXE), then the repo-relative ship path as a dev/CI convenience.
    """
    candidates: list[Path] = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(str(meipass)) / SHIPPED_BINARY_NAME)

    with contextlib.suppress(OSError):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / SHIPPED_BINARY_DEST / SHIPPED_BINARY_NAME)
        candidates.append(exe_dir / SHIPPED_BINARY_NAME)
        candidates.append(exe_dir / "_internal" / SHIPPED_BINARY_DEST / SHIPPED_BINARY_NAME)

    candidates.append(Path.cwd() / SHIPPED_BINARY_DEST / SHIPPED_BINARY_NAME)
    candidates.append(Path.cwd() / SHIPPED_BINARY_NAME)
    candidates.append(Path.cwd() / "_internal" / SHIPPED_BINARY_DEST / SHIPPED_BINARY_NAME)
    return candidates


def resolve_go_api_binary() -> Path | None:
    """The SHIPPED Go API binary, if this process is running from a package.

    A release build compiles nexus-api.exe once (build_release.ps1) and bakes
    it into the PyInstaller onedir. The end user has NO Go toolchain, so that
    copy is authoritative and is used AS-IS — this function never compiles,
    and never invalidates or rewrites what the release shipped.

    Returns the path when a shipped binary is present, else None (and the
    caller falls back to a source build, which is the dev-checkout path).
    """
    for cand in _shipped_binary_candidates():
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


def build_go_api(go_exe: str | None = None, timeout_s: int = 300) -> tuple[bool, str]:
    """Resolve the nexus-api binary. Returns (ok, message).

    SHIPPED FIRST: when this process is running out of a release package,
    the binary the release compiled is used exactly as shipped — it is never
    rebuilt (the end user has no compiler and no source tree to build from).

    Otherwise this is a dev/source checkout, so the binary is COMPILED here
    (toolchain discovered when go_exe is None). Uses the content cache: a hit
    skips the compile entirely. A build failure returns the tail of the
    compiler output so the operator sees the real cause, not a bare exit code.
    """
    shipped = resolve_go_api_binary()
    if shipped is not None:
        _log("using shipped go api binary (release build): %s", shipped)
        return True, str(shipped)

    if not GO_API_DIR.is_dir():
        return False, f"go-api directory missing: {GO_API_DIR}"

    if go_exe is None:
        go_exe = _find_go()
        if go_exe is None:
            return False, (
                "go toolchain not found and no shipped nexus-api binary — "
                "serving the python API directly"
            )

    BUILD_CACHE.mkdir(parents=True, exist_ok=True)
    bin_path = _binary_path()
    stamp = BUILD_CACHE / "source.sha256"

    want = _source_hash()
    if stamp.is_file():
        try:
            if stamp.read_text(encoding="utf-8").strip() == want and bin_path.is_file():
                _log("go api build cache hit, skipping compile")
                return True, str(bin_path)
        except OSError:
            pass

    env = dict(os.environ)
    # Isolated module cache per build dir keeps a concurrent `go clean` in
    # another worktree from evicting our downloads mid-compile.
    env.setdefault("GOFLAGS", "-mod=mod")
    env.setdefault("GOPROXY", "https://proxy.golang.org,direct")

    _log("building go api server (cache miss)")
    try:
        proc = subprocess.run(
            [go_exe, "build", "-o", str(bin_path), MAIN_PACKAGE],
            cwd=str(GO_API_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"go build exceeded {timeout_s}s"
    except OSError as exc:
        return False, f"go build could not run: {exc}"

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-12:]
        return False, "go build failed:\n" + "\n".join(tail)

    try:
        stamp.write_text(want, encoding="utf-8")
    except OSError:
        pass  # caching is best-effort; the binary itself is valid
    return True, str(bin_path)


# --------------------------------------------------------------------------- #
# Port resolution
# --------------------------------------------------------------------------- #


def _port_in_use(host: str, port: int) -> bool:
    """True if something is already bound on host:port (IPv4)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.4)
    try:
        return probe.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        probe.close()


def _free_port_above(host: str, port: int) -> int:
    """First free port at or above `port`. Mirrors the launcher's own
    port-search so a busy 8087 does not hard-fail a boot."""
    for cand in range(port, port + 64):
        if not _port_in_use(host, cand):
            return cand
    return port


def resolve_api_addr(preferred_port: int) -> tuple[str, int]:
    """Resolve the (host, port) the Go API should bind.

    Honours NSE_GO_ADDR when set ('host:port' or ':port'). Otherwise the
    API sits one port ABOVE the Python web port by convention so the two
    never collide and the mapping is predictable in diagnostics.
    """
    host = os.getenv("NSE_WEB_HOST", "127.0.0.1").strip() or "127.0.0.1"

    raw = os.getenv(_API_PORT_ENV, "").strip()
    if raw:
        if ":" in raw:
            h, _, p = raw.rpartition(":")
            if h:
                host = h
        else:
            p = raw
        try:
            return host, _free_port_above(host, int(p))
        except ValueError:
            _log("NSE_GO_ADDR unparseable (%s), ignoring", raw)

    return host, _free_port_above(host, preferred_port)


# --------------------------------------------------------------------------- #
# Launch + readiness
# --------------------------------------------------------------------------- #


class GoApiSupervisor:
    """Owns the Go API child process for one engine run.

    The supervisor is deliberately small: build, spawn, wait for /health,
    and tear down. It never holds a reference into the engine and never
    mutates trading state — Go is an API plane only.
    """

    def __init__(self, python_origin: str, addr: tuple[str, int], binary: str):
        self.python_origin = python_origin
        self.host, self.port = addr
        self.binary = binary
        self._proc: subprocess.Popen | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    # -- lifecycle -------------------------------------------------------- #

    def start(self, ready_timeout_s: float = 20.0) -> tuple[bool, str]:
        """Spawn Go and block until /health answers (or the timeout).

        Returns (ok, message). On failure the child is killed and the
        caller falls back to Python-only serving.
        """
        env = dict(os.environ)
        # Go must forward to THIS Python process, not to a stale default.
        env[_PY_ORIGIN_ENV] = self.python_origin
        # Python owns the secret store; Go only reads the resolved token.
        env.setdefault("NSE_WEB_HOST", self.host)

        try:
            self._proc = subprocess.Popen(
                [
                    self.binary,
                    "-addr",
                    f"{self.host}:{self.port}",
                    "-python-origin",
                    self.python_origin,
                ],
                cwd=_binary_workdir(self.binary),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=_creation_flags(),
            )
        except OSError as exc:
            return False, f"could not launch go api binary: {exc}"

        self._pump_stderr()
        if not self._wait_ready(ready_timeout_s):
            self.stop()
            return False, f"go api never became ready on :{self.port} within {ready_timeout_s:.0f}s"

        _log("go api ready on %s:%d -> %s", self.host, self.port, self.python_origin)
        return True, f"{self.host}:{self.port}"

    def stop(self) -> None:
        """Terminate the child. Best-effort: never raises."""
        proc = self._proc
        if proc is None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _force_kill(proc.pid)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        except Exception:
            pass
        finally:
            self._proc = None

    # -- internals -------------------------------------------------------- #

    def _wait_ready(self, timeout_s: float) -> bool:
        """Poll the Go /health endpoint until 200 or timeout.

        Uses a raw socket, not urllib: the Go server may answer 503 while
        Python is still starting (its own readiness gate), and we only need
        'the process is up and routing', not 'the engine is live'.
        """
        import urllib.error
        import urllib.request

        url = f"http://{self.host}:{self.port}/health"
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                return False  # child died
            try:
                with urllib.request.urlopen(url, timeout=1.5) as resp:
                    if resp.status in (200, 503):
                        # 503 = Go is up, Python not yet live: still 'ready'
                        # as an API plane. The engine's own readiness gate
                        # is what gates the browser, not this one.
                        return True
            except urllib.error.HTTPError as exc:
                if exc.code in (200, 503):
                    return True
            except (OSError, ConnectionError):
                pass
            time.sleep(0.2)
        return False

    def _pump_stderr(self) -> None:
        """Relay the child's combined output to our stderr in a daemon
        thread so a chatty Go server can never fill a pipe and block it."""

        def _relay(stream: Any) -> None:
            try:
                for line in iter(stream.readline, b""):
                    try:
                        sys.stderr.write("[go-api] " + line.decode("utf-8", "replace"))
                        sys.stderr.flush()
                    except (OSError, ValueError):
                        return
            except (OSError, ValueError):
                return

        if self._proc is not None and self._proc.stdout is not None:
            self._stderr_thread = threading.Thread(
                target=_relay, args=(self._proc.stdout,), daemon=True
            )
            self._stderr_thread.start()


def _binary_workdir(binary: str) -> str:
    """Cwd for the Go child. Prefers the go-api source dir (its templates /
    static assets are relative to it); falls back to the binary's own
    directory so a packaged install (no source tree) still launches."""
    if GO_API_DIR.is_dir():
        return str(GO_API_DIR)
    with contextlib.suppress(OSError):
        return str(Path(binary).resolve().parent)
    return os.getcwd()


def _creation_flags() -> int:
    """On Windows, put the child in its own process group so Ctrl+C in the
    console only signals the Python parent (which supervises the child
    itself), matching the ShutdownSupervisor contract."""
    if os.name != "nt":
        return 0
    try:
        # CREATE_NEW_PROCESS_GROUP = 0x00000200
        return 0x00000200
    except Exception:
        return 0


def _force_kill(pid: int | None) -> None:
    """Cross-platform hard kill; the graceful terminate already failed."""
    if pid is None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        import signal

        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Public entrypoint
# --------------------------------------------------------------------------- #


def boot_go_api(
    python_host: str, python_port: int, preferred_api_port: int
) -> GoApiSupervisor | None:
    """Build and launch the Go API as a supervised child.

    Called from the engine boot path AFTER Python's own web origin is known
    but BEFORE uvicorn starts serving, so the Go plane is ready by the time
    the dashboard opens.

    Returns None when Go cannot be used — the caller then simply serves the
    FastAPI app on its own port and the product still works. That fallback
    is intentional and is the ONLY one in this subsystem.
    """
    go_exe = _find_go()
    if go_exe is None and resolve_go_api_binary() is None:
        _log(
            "go toolchain not found and no shipped nexus-api binary; serving python API directly (no go plane)"
        )
        return None

    ok, msg = build_go_api(go_exe)
    if not ok:
        _log("go api unavailable — serving python API directly: %s", msg)
        return None

    addr = resolve_api_addr(preferred_api_port)
    python_origin = f"http://{python_host}:{python_port}"

    supervisor = GoApiSupervisor(python_origin=python_origin, addr=addr, binary=msg)
    started, where = supervisor.start()
    if not started:
        _log("go api did not start — serving python API directly: %s", where)
        return None

    # Surface the live API origin for the frontend dev proxy / diagnostics.
    os.environ["NSE_GO_API_ORIGIN"] = f"http://{where}"
    return supervisor


def api_origin() -> str | None:
    """The origin the Go API is listening on, if this process started one."""
    return os.environ.get("NSE_GO_API_ORIGIN") or None
