"""Pylint analyzer adapter for NSE code diagnostics."""

from __future__ import annotations

import json
import shutil
import sys

from nexus_scalp.diagnostics.analyzers.base import BaseAnalyzer
from nexus_scalp.diagnostics.models import Diagnostic
from nexus_scalp.diagnostics.runner import run_analyzer


class PylintAnalyzer(BaseAnalyzer):
    name = "pylint"

    def version(self) -> str:
        res = run_analyzer(
            self.health,
            [sys.executable, "-m", "pylint", "--version"],
            timeout=10.0,
            cwd=str(self.workspace),
        )
        if res.status == "COMPLETED" and res.returncode == 0:
            return res.stdout.strip().splitlines()[0] if res.stdout.strip() else "unknown"
        res2 = run_analyzer(
            self.health, ["pylint", "--version"], timeout=10.0, cwd=str(self.workspace)
        )
        if res2.status == "COMPLETED" and res2.returncode == 0:
            return res2.stdout.strip().splitlines()[0] if res2.stdout.strip() else "unknown"
        return "unknown"

    def is_available(self) -> bool:
        if shutil.which("pylint") is not None:
            self.health.executable = "pylint"
            self.health.available = True
            return True
        res = run_analyzer(
            self.health,
            [sys.executable, "-m", "pylint", "--version"],
            timeout=5.0,
            cwd=str(self.workspace),
        )
        if res.status == "COMPLETED" and res.returncode == 0:
            self.health.executable = f"{sys.executable} -m pylint"
            self.health.available = True
            return True
        return False

    def analyze(self, target_paths: list[str] | None = None) -> list[Diagnostic]:
        if not self.is_available():
            self.health.execution_status = "NOT_INSTALLED"
            return []

        cmd = []
        if self.health.executable.startswith(sys.executable):
            cmd = [sys.executable, "-m", "pylint", "--output-format=json"]
        else:
            cmd = ["pylint", "--output-format=json"]

        if target_paths:
            cmd.extend(target_paths)
        else:
            cmd.append(".")

        res = run_analyzer(self.health, cmd, timeout=180.0, cwd=str(self.workspace))
        if res.status != "COMPLETED":
            return []

        # Pylint returns nonzero when it finds messages; treat a non-completed
        # status only as an infrastructure failure.
        diagnostics: list[Diagnostic] = []
        try:
            raw = res.stdout or "[]"
            data = json.loads(raw)
            for item in data:
                msg = item.get("message", "")
                symbol = item.get("symbol", "")
                msg_id = item.get("msg_id", "")
                category = item.get("type", "warning")
                severity = "error" if category in {"error", "fatal"} else "warning"
                diagnostics.append(
                    Diagnostic(
                        tool=self.name,
                        source=symbol,
                        category="lint",
                        severity=severity,
                        code=msg_id,
                        message=msg,
                        file=item.get("path", ""),
                        line=int(item.get("line", 1)),
                        column=int(item.get("column", 1)),
                        confidence=float(item.get("confidence", 0.0)),
                    )
                )
        except Exception as exc:
            self.health.error_message = f"failed to parse pylint json output: {exc}"

        self.health.diagnostics_count = len(diagnostics)
        return diagnostics
