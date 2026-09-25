"""Ruff analyzer adapter for NSE code diagnostics."""

from __future__ import annotations

import json
import shutil
import sys

from nexus_scalp.diagnostics.analyzers.base import BaseAnalyzer
from nexus_scalp.diagnostics.models import Diagnostic
from nexus_scalp.diagnostics.runner import run_analyzer


class RuffAnalyzer(BaseAnalyzer):
    name = "ruff"

    def version(self) -> str:
        res = run_analyzer(
            self.health,
            [sys.executable, "-m", "ruff", "--version"],
            timeout=10.0,
            cwd=str(self.workspace),
        )
        if res.status == "COMPLETED" and res.returncode == 0:
            return res.stdout.strip()
        # Fallback to direct ruff command
        res2 = run_analyzer(
            self.health, ["ruff", "--version"], timeout=10.0, cwd=str(self.workspace)
        )
        if res2.status == "COMPLETED" and res2.returncode == 0:
            return res2.stdout.strip()
        return "unknown"

    def is_available(self) -> bool:
        if shutil.which("ruff") is not None:
            self.health.executable = "ruff"
            self.health.available = True
            return True
        # Check via python -m ruff
        res = run_analyzer(
            self.health,
            [sys.executable, "-m", "ruff", "--version"],
            timeout=5.0,
            cwd=str(self.workspace),
        )
        if res.status == "COMPLETED" and res.returncode == 0:
            self.health.executable = f"{sys.executable} -m ruff"
            self.health.available = True
            return True
        return False

    def analyze(self, target_paths: list[str] | None = None) -> list[Diagnostic]:
        if not self.is_available():
            self.health.execution_status = "NOT_INSTALLED"
            return []

        cmd = []
        if self.health.executable.startswith(sys.executable):
            cmd = [sys.executable, "-m", "ruff", "check", "--output-format=json"]
        else:
            cmd = ["ruff", "check", "--output-format=json"]

        if target_paths:
            cmd.extend(target_paths)
        else:
            cmd.append(".")

        res = run_analyzer(self.health, cmd, timeout=120.0, cwd=str(self.workspace))
        if res.status != "COMPLETED":
            return []

        diagnostics: list[Diagnostic] = []
        try:
            data = json.loads(res.stdout or "[]")
            if isinstance(data, list):
                for item in data:
                    code = str(item.get("code", "RUF001"))
                    message = str(item.get("message", ""))
                    filename = str(item.get("filename", ""))
                    location = item.get("location", {})
                    end_location = item.get("end_location", {})
                    line = int(location.get("row", 1))
                    col = int(location.get("column", 1))
                    end_line = int(end_location.get("row", line)) if end_location else None
                    end_col = int(end_location.get("column", col)) if end_location else None
                    fix = item.get("fix")
                    fixable = fix is not None

                    # Severity mapping
                    severity = (
                        "error" if code.startswith("E") or code.startswith("F") else "warning"
                    )
                    category = "syntax" if code.startswith("E9") else "lint"

                    diagnostics.append(
                        Diagnostic(
                            tool=self.name,
                            source=code,
                            category=category,
                            severity=severity,
                            code=code,
                            message=message,
                            file=filename,
                            line=line,
                            column=col,
                            end_line=end_line,
                            end_column=end_col,
                            fixable=fixable,
                            rule_url=f"https://beta.ruff.rs/docs/rules/{code}/" if code else "",
                        )
                    )
        except Exception as exc:
            self.health.error_message = f"failed to parse ruff json output: {exc}"

        self.health.diagnostics_count = len(diagnostics)
        return diagnostics
