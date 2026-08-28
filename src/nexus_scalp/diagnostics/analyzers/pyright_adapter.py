"""Pyright analyzer adapter for NSE code diagnostics."""

from __future__ import annotations

import json
import shutil
import sys

from nexus_scalp.diagnostics.analyzers.base import BaseAnalyzer
from nexus_scalp.diagnostics.models import Diagnostic
from nexus_scalp.diagnostics.runner import run_analyzer


class PyrightAnalyzer(BaseAnalyzer):
    name = "pyright"

    def version(self) -> str:
        res = run_analyzer(
            self.health,
            [sys.executable, "-m", "pyright", "--version"],
            timeout=10.0,
            cwd=str(self.workspace),
        )
        if res.status == "COMPLETED" and res.returncode == 0:
            return res.stdout.strip()
        res2 = run_analyzer(
            self.health, ["pyright", "--version"], timeout=10.0, cwd=str(self.workspace)
        )
        if res2.status == "COMPLETED" and res2.returncode == 0:
            return res2.stdout.strip()
        return "unknown"

    def is_available(self) -> bool:
        if shutil.which("pyright") is not None:
            self.health.executable = "pyright"
            self.health.available = True
            return True
        res = run_analyzer(
            self.health,
            [sys.executable, "-m", "pyright", "--version"],
            timeout=5.0,
            cwd=str(self.workspace),
        )
        if res.status == "COMPLETED" and res.returncode == 0:
            self.health.executable = f"{sys.executable} -m pyright"
            self.health.available = True
            return True
        return False

    def analyze(self, target_paths: list[str] | None = None) -> list[Diagnostic]:
        if not self.is_available():
            self.health.execution_status = "NOT_INSTALLED"
            return []

        cmd = []
        if self.health.executable.startswith(sys.executable):
            cmd = [sys.executable, "-m", "pyright", "--outputjson"]
        else:
            cmd = ["pyright", "--outputjson"]

        if target_paths:
            cmd.extend(target_paths)
        else:
            cmd.append(".")

        res = run_analyzer(self.health, cmd, timeout=180.0, cwd=str(self.workspace))
        if res.status != "COMPLETED":
            return []

        diagnostics: list[Diagnostic] = []
        try:
            data = json.loads(res.stdout or "{}")
            for item in data.get("generalDiagnostics", []):
                sev = item.get("severity", "information")
                if sev == "error":
                    severity = "error"
                    category = "type"
                elif sev == "warning":
                    severity = "warning"
                    category = "type"
                else:
                    severity = "info"
                    category = "type"

                diagnostics.append(
                    Diagnostic(
                        tool=self.name,
                        source=item.get("rule", ""),
                        category=category,
                        severity=severity,
                        code=str(item.get("rule", "")),
                        message=item.get("message", ""),
                        file=item.get("file", ""),
                        line=int(item.get("range", {}).get("start", {}).get("line", 1)),
                        column=int(item.get("range", {}).get("start", {}).get("character", 1)),
                        end_line=int(item.get("range", {}).get("end", {}).get("line", 1)),
                        end_column=int(item.get("range", {}).get("end", {}).get("character", 1)),
                    )
                )
        except Exception as exc:
            self.health.error_message = f"failed to parse pyright json output: {exc}"

        self.health.diagnostics_count = len(diagnostics)
        return diagnostics
