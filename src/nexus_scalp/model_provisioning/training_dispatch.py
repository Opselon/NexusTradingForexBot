"""Local managed-interpreter dispatch; no pip, shell or network operations."""

from __future__ import annotations

import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nexus_scalp.model_provisioning.pipeline import ProgressCb, TrainingRequest
    from nexus_scalp.model_provisioning.training_env import EnvironmentReport

CANCEL_GRACE_SECONDS = 5.0
WORKER_TIMEOUT_SECONDS = 24 * 3600.0
MAX_MESSAGE_BYTES = 1024 * 1024


def worker_script() -> Path:
    """The EXE ships real .py sources, NOT a path into its frozen PYZ archive."""
    from nexus_scalp.release.paths import exe_dir, is_frozen

    if is_frozen():
        roots = [Path(getattr(sys, "_MEIPASS", exe_dir() / "_internal")), exe_dir()]
        for root in roots:
            script = (
                root
                / "training_payload"
                / "src"
                / "nexus_scalp"
                / "model_provisioning"
                / "training_worker.py"
            )
            if script.is_file():
                return script
        raise FileNotFoundError("training source payload missing; reinstall the application")
    script = Path(__file__).with_name("training_worker.py")
    if not script.is_file():
        raise FileNotFoundError("training worker source missing")
    return script


def run_training_worker(
    request: TrainingRequest, report: EnvironmentReport, progress: ProgressCb | None = None
) -> dict[str, Any]:
    """Stream measured progress; require one valid result AND a clean process exit."""
    from nexus_scalp.model_provisioning.pipeline import ProgressEvent, _emit
    from nexus_scalp.release.paths import get_runtime_workspace, is_frozen

    def blocked(reason: str) -> dict[str, Any]:
        _emit(progress, ProgressEvent(stage="env", status="blocked", message=reason))
        return {
            "outcome": "TRAINING_ENV_BLOCKED",
            "reason": reason,
            "environment_report": report.as_dict(),
        }

    if not report.training_ready:
        return blocked("selected environment is not READY; re-check dependencies")
    executable = report.environment.get("python") or report.pytorch.get("path")
    if not executable or not Path(executable).is_absolute() or not Path(executable).is_file():
        return blocked("selected training interpreter is missing; re-check environment")
    if is_frozen() and os.path.abspath(executable) == os.path.abspath(sys.executable):
        return blocked("application EXE is not a Python interpreter")
    cancel = request.cancel_event or threading.Event()
    if cancel.is_set():
        return {"outcome": "CANCELLED"}
    proc = None
    readers = []
    stop_readers = threading.Event()
    try:
        script = worker_script()
        env = os.environ.copy()
        # Never let frozen-loader paths contaminate the external interpreter.
        for key in (
            "PYTHONHOME",
            "PYTHONPATH",
            "_MEIPASS2",
            "_PYI_APPLICATION_HOME_DIR",
            "_PYI_ARCHIVE_FILE",
            "_PYI_PARENT_PROCESS_LEVEL",
        ):
            env.pop(key, None)
        if is_frozen():
            for key in ("LD_LIBRARY_PATH", "LIBPATH"):
                if key + "_ORIG" in env:
                    env[key] = env.pop(key + "_ORIG")
                else:
                    env.pop(key, None)
        env["PYTHONIOENCODING"] = "utf-8"
        env["NEXUS_TRAINING_PYTHON"] = str(executable)
        env["NEXUS_TRAINING_ENV"] = str(Path(executable).parent.parent)
        env["NEXUS_TRAINING_BACKEND"] = report.backend
        contract = report.contract.get("path")
        if contract:
            env["NEXUS_TRAINING_CONTRACT"] = str(Path(contract).absolute())
        proc = subprocess.Popen(
            [str(executable), "-u", str(script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=str(get_runtime_workspace()),
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        messages: queue.Queue[str | None] = queue.Queue(maxsize=128)
        errors: deque[str] = deque(maxlen=20)

        def enqueue(line: str | None) -> None:
            while not stop_readers.is_set():
                try:
                    messages.put(line, timeout=0.1)
                    return
                except queue.Full:
                    continue

        def read_stdout() -> None:
            assert proc is not None and proc.stdout is not None
            try:
                while not stop_readers.is_set():
                    line = proc.stdout.readline(MAX_MESSAGE_BYTES + 1)
                    if not line:
                        break
                    enqueue(line)
                    if len(line) > MAX_MESSAGE_BYTES:
                        break
            finally:
                enqueue(None)

        def read_stderr() -> None:
            assert proc is not None and proc.stderr is not None
            while chunk := proc.stderr.read(4096):
                errors.append(chunk)

        for target in (read_stdout, read_stderr):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            readers.append(thread)
        payload = {
            "protocol": 1,
            "request": {
                "source_file": str(Path(request.source_file).absolute()),
                "candles": request.candles,
                "folds": request.folds,
                "epochs": request.epochs,
                "install": False,
                "backend": report.backend,
            },
        }
        proc.stdin.write(json.dumps(payload, allow_nan=False) + "\n")
        proc.stdin.flush()
        result = None
        eof = False
        started = time.monotonic()
        cancel_started = None
        while not eof or proc.poll() is None:
            now = time.monotonic()
            if cancel.is_set() and cancel_started is None:
                cancel_started = now
                with contextlib.suppress(OSError):
                    proc.stdin.write('{"type":"cancel"}\n')
                    proc.stdin.flush()
            if cancel_started is not None and now - cancel_started > CANCEL_GRACE_SECONDS:
                proc.kill()
                proc.wait(timeout=5)
                return {
                    "outcome": "CANCELLED",
                    "reason": "worker stopped after cancellation grace period",
                }
            if now - started > WORKER_TIMEOUT_SECONDS:
                return blocked("training worker timed out; no successful result accepted")
            try:
                line = messages.get(timeout=0.05)
            except queue.Empty:
                continue
            if line is None:
                eof = True
                continue
            if len(line) > MAX_MESSAGE_BYTES:
                return blocked("training worker protocol message too large")
            message = json.loads(line)
            if not isinstance(message, dict):
                return blocked("invalid training worker protocol")
            if message.get("type") == "progress":
                _emit(progress, ProgressEvent(**message["event"]))
            elif message.get("type") == "result":
                if result is not None:
                    return blocked("duplicate training worker result")
                result = message.get("result")
                if not isinstance(result, dict) or result.get("outcome") not in {
                    "CANDIDATE",
                    "VALIDATION_FAILED",
                    "CANCELLED",
                    "FAILED",
                    "TRAINING_ENV_BLOCKED",
                }:
                    return blocked("invalid training worker result")
            else:
                return blocked("unknown training worker protocol message")
        if proc.returncode != 0 or result is None:
            # Raw stderr can include paths/secrets; never expose it in API output.
            return blocked(
                f"training worker failed (exit={proc.returncode}); repair training dependencies and re-check"
            )
        if cancel.is_set():
            return {"outcome": "CANCELLED"}
        return result
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        return blocked("training worker launch or protocol failed; repair environment and re-check")
    finally:
        stop_readers.set()
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)
            for thread in readers:
                thread.join(timeout=1)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    with contextlib.suppress(OSError):
                        stream.close()
