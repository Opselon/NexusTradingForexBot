"""Stdlib entry point shipped as source for an external managed Python.

stdin: one v1 request then cancellation messages. stdout: JSON-lines only.
No alternate trainer, no dependency installation, no recursive dispatch.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.metadata
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any

# Executed by filename, including from training_payload/src in a frozen build.
sys.path.insert(0, str(Path(__file__).absolute().parents[2]))

# Import diagnostics complement runtime pins; tensor/backend checks stay in status().
PROBE_MODULES = (
    "structlog",
    "pydantic",
    "pydantic_settings",
    "yaml",
    "numpy",
    "polars",
    "torch",
    "nexus_scalp.model_generation.three_model",
    "nexus_scalp.model_provisioning.service",
    "nexus_scalp.release.bootstrap",
)


def dependency_probe(packages: list[str] | None = None) -> dict[str, Any]:
    missing: dict[str, str] = {}
    mismatched: dict[str, dict[str, str]] = {}
    versions: dict[str, str] = {}
    try:
        target_packages: list[str] = (
            packages
            if packages is not None
            else json.loads(
                Path(
                    os.environ.get("NEXUS_TRAINING_CONTRACT")
                    or (
                        Path(__file__).absolute().parents[3]
                        / "configs"
                        / "training_environment.json"
                    )
                ).read_text(encoding="utf-8")
            )
            .get("runtime", {})
            .get("packages", [])
        )
        for raw in target_packages:
            requirement, _, marker = raw.partition(";")
            # The canonical lock uses Python-version arms only. Reject unknown
            # marker grammar rather than evaluating code or guessing applicability.
            if marker:
                match = re.fullmatch(
                    r"\s*(python_full_version|python_version)\s*(<=|>=|==|!=|<|>)\s*['\"](\d+(?:\.\d+)*)['\"]\s*",
                    marker,
                )
                if match is None:
                    raise ValueError(f"unsupported runtime marker: {marker}")
                variable, operator, version = match.groups()
                actual = tuple(sys.version_info[: 3 if variable == "python_full_version" else 2])
                expected = tuple(int(part) for part in version.split("."))
                width = max(len(actual), len(expected))
                actual += (0,) * (width - len(actual))
                expected += (0,) * (width - len(expected))
                if not {
                    "<": actual < expected,
                    ">": actual > expected,
                    "<=": actual <= expected,
                    ">=": actual >= expected,
                    "==": actual == expected,
                    "!=": actual != expected,
                }[operator]:
                    continue
            pin = re.fullmatch(r"\s*([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)\s*", requirement)
            if pin is None:
                raise ValueError(f"runtime requirement must be exact: {requirement}")
            name, required = pin.groups()
            try:
                installed = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                missing[name] = "PackageNotFoundError"
                continue
            versions[name] = installed
            if installed != required:
                mismatched[name] = {"required": required, "installed": installed}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        missing["runtime_contract"] = str(exc)

    for name in PROBE_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:
            missing[name] = type(exc).__name__
    return {
        "python": sys.executable,
        "ready": not missing and not mismatched,
        "missing": missing,
        "mismatched": mismatched,
        "versions": versions,
    }


def main() -> int:
    # Reserve the original pipe even for libraries that write directly to fd 1.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr

    def send(message: dict[str, Any]) -> None:
        protocol.write(json.dumps(message, allow_nan=False) + "\n")
        protocol.flush()

    if sys.argv[1:] in (["--probe"], ["--requirements-stdin", "--probe"]):
        try:
            packages = (
                json.loads(sys.stdin.read())["packages"]
                if "--requirements-stdin" in sys.argv
                else None
            )
            report = dependency_probe(packages)
        except (ValueError, TypeError, KeyError) as exc:
            report = {"ready": False, "missing": {"runtime_contract": str(exc)}}
        send(report)
        return 0 if report["ready"] else 1
    try:
        payload = json.loads(sys.stdin.readline(1024 * 1024))
        if payload.get("protocol") != 1:
            raise ValueError("unsupported worker protocol")
        raw = payload["request"]
        if not isinstance(raw, dict) or set(raw) != {
            "source_file",
            "candles",
            "folds",
            "epochs",
            "install",
            "backend",
        }:
            raise ValueError("invalid request fields")
        if raw["backend"] not in ("cpu", "cuda") or not isinstance(raw["install"], bool):
            raise ValueError("invalid backend/install")
        cancel = threading.Event()

        def watch_cancel() -> None:
            try:
                while line := sys.stdin.readline(4096):
                    if json.loads(line).get("type") == "cancel":
                        cancel.set()
                        return
            except (ValueError, OSError):
                pass
            # Parent died/closed channel: do not publish a candidate unattended.
            cancel.set()

        threading.Thread(target=watch_cancel, daemon=True).start()
        from nexus_scalp.model_provisioning.pipeline import TrainingRequest, train_local_model

        raw["source_file"] = Path(raw["source_file"])
        request = TrainingRequest(**raw, cancel_event=cancel)
        result = train_local_model(
            request,
            lambda ev: send({"type": "progress", "event": ev.as_dict()}),
            _worker_mode=True,
        )
        send({"type": "result", "result": result})
        return 0
    except Exception as exc:
        with contextlib.suppress(Exception):
            send(
                {
                    "type": "result",
                    "result": {
                        "outcome": "TRAINING_ENV_BLOCKED",
                        "reason": f"worker initialization failed ({type(exc).__name__}); repair dependencies and re-check",
                    },
                }
            )
        return 1
    finally:
        protocol.close()


if __name__ == "__main__":
    raise SystemExit(main())
