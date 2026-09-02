"""Safe, cross-platform subprocess runner for diagnostic analyzers.

Hardening requirements (mission PHASE 4 / PHASE 15):
* explicit argument list (never shell=True)
* controlled timeout with clean process-tree termination
* captured stdout/stderr with lossy-safe UTF-8 decoding
* exit-code + duration reporting
* CTRL+C (KeyboardInterrupt) propagated as INTERRUPTED, not source error
* Windows + Linux compatible process-tree kill
* no analyzer output is trusted as code quality
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass

from nexus_scalp.diagnostics.models import AnalyzerHealth


@dataclass
class RunResult:
    stdout: str
    stderr: str
    returncode: int
    duration_ms: float
    status: str  # COMPLETED | TIMEOUT | INTERRUPTED | FAILED
    error: str = ""


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Terminate the whole process tree (Windows + POSIX)."""
    try:
        if sys.platform.startswith("win"):
            # taskkill reliably kills the tree on Windows.
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
                return
            except Exception:
                pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    except Exception:
        # Last-resort: attempt direct kill; never raise from cleanup.
        try:
            proc.kill()
        except Exception:
            pass


def _decode(raw: bytes) -> str:
    """Lossy-safe UTF-8 decode tolerant of mojibake / invalid bytes."""
    if not raw:
        return ""
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        try:
            return raw.decode(sys.getfilesystemencoding(), errors="replace")
        except Exception:
            return raw.decode("latin-1", errors="replace")


def run_command(
    args: list[str],
    timeout: float = 120.0,
    cwd: str | None = None,
) -> RunResult:
    """Run ``args`` (no shell) with timeout + tree termination.

    ``args[0]`` is the executable; the rest are literal arguments. We never
    use ``shell=True`` so there is no shell-injection surface.
    """
    start = time.perf_counter()
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            # Isolated env: do not inherit analyzer-discoverable secrets.
            env=dict(os.environ),
            text=False,
        )
    except FileNotFoundError as exc:
        return RunResult(
            stdout="",
            stderr="",
            returncode=-1,
            duration_ms=(time.perf_counter() - start) * 1000.0,
            status="FAILED",
            error=f"executable not found: {exc}",
        )
    except Exception as exc:  # pragma: no cover - defensive
        return RunResult(
            stdout="",
            stderr="",
            returncode=-1,
            duration_ms=(time.perf_counter() - start) * 1000.0,
            status="FAILED",
            error=f"spawn failed: {exc}",
        )

    try:
        out, err = proc.communicate(timeout=timeout)
        return RunResult(
            stdout=_decode(out or b""),
            stderr=_decode(err or b""),
            returncode=int(proc.returncode or 0),
            duration_ms=(time.perf_counter() - start) * 1000.0,
            status="COMPLETED",
        )
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        return RunResult(
            stdout="",
            stderr="",
            returncode=-1,
            duration_ms=(time.perf_counter() - start) * 1000.0,
            status="TIMEOUT",
            error=f"analyzer timed out after {timeout}s",
        )
    except KeyboardInterrupt:
        _kill_tree(proc)
        raise
    except Exception as exc:  # pragma: no cover - defensive
        _kill_tree(proc)
        return RunResult(
            stdout="",
            stderr="",
            returncode=-1,
            duration_ms=(time.perf_counter() - start) * 1000.0,
            status="FAILED",
            error=f"execution error: {exc}",
        )


def run_analyzer(
    health: AnalyzerHealth,
    args: list[str],
    timeout: float = 120.0,
    cwd: str | None = None,
) -> RunResult:
    """Run an analyzer given its health record; update health status."""
    health.execution_status = "RUNNING"
    result = run_command(args, timeout=timeout, cwd=cwd)
    health.duration_ms = result.duration_ms
    health.exit_code = result.returncode
    if result.status == "TIMEOUT":
        health.execution_status = "TIMEOUT"
        health.error_message = result.error
    elif result.status == "INTERRUPTED":  # pragma: no cover
        health.execution_status = "INTERRUPTED"
    elif result.status == "FAILED":
        health.execution_status = "FAILED"
        health.error_message = result.error
    else:
        health.execution_status = "COMPLETED"
    return result
