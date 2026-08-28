"""Bandit security analyzer adapter for NSE code diagnostics."""

from __future__ import annotations

import json
import shutil
import sys

from nexus_scalp.diagnostics.analyzers.base import BaseAnalyzer
from nexus_scalp.diagnostics.models import Diagnostic
from nexus_scalp.diagnostics.runner import run_analyzer


class BanditAnalyzer(BaseAnalyzer):
    name = "bandit"

    def version(self) -> str:
        res = run_analyzer(
            self.health,
            [sys.executable, "-m", "bandit", "--version"],
            timeout=10.0,
            cwd=str(self.workspace),
        )
        if res.status == "COMPLETED" and res.returncode == 0:
            return res.stdout.strip().splitlines()[0] if res.stdout.strip() else "unknown"
        res2 = run_analyzer(
            self.health, ["bandit", "--version"], timeout=10.0, cwd=str(self.workspace)
        )
        if res2.status == "COMPLETED" and res2.returncode == 0:
            return res2.stdout.strip().splitlines()[0] if res2.stdout.strip() else "unknown"
        return "unknown"

    def is_available(self) -> bool:
        if shutil.which("bandit") is not None:
            self.health.executable = "bandit"
            self.health.available = True
            return True
        res = run_analyzer(
            self.health,
            [sys.executable, "-m", "bandit", "--version"],
            timeout=5.0,
            cwd=str(self.workspace),
        )
        if res.status == "COMPLETED" and res.returncode == 0:
            self.health.executable = f"{sys.executable} -m bandit"
            self.health.available = True
            return True
        return False

    def analyze(self, target_paths: list[str] | None = None) -> list[Diagnostic]:
        if not self.is_available():
            self.health.execution_status = "NOT_INSTALLED"
            return []

        cmd = []
        if self.health.executable.startswith(sys.executable):
            cmd = [sys.executable, "-m", "bandit", "-f", "json"]
        else:
            cmd = ["bandit", "-f", "json"]

        if target_paths:
            # Bandit needs recursive flag -r for directories
            cmd.append("-r")
            cmd.extend(target_paths)
        else:
            cmd.extend(["-r", "."])

        res = run_analyzer(self.health, cmd, timeout=120.0, cwd=str(self.workspace))
        if res.status != "COMPLETED" and res.returncode not in (0, 1):
            return []

        diagnostics: list[Diagnostic] = []
        try:
            raw = res.stdout or "{}"
            data = json.loads(raw)
            for item in data.get("results", []):
                sev = str(item.get("issue_severity", "MEDIUM")).upper()
                severity = "error" if sev == "HIGH" else ("warning" if sev == "MEDIUM" else "info")
                conf = str(item.get("issue_confidence", "MEDIUM")).upper()
                confidence_score = 1.0 if conf == "HIGH" else (0.7 if conf == "MEDIUM" else 0.4)

                diagnostics.append(
                    Diagnostic(
                        tool=self.name,
                        source=item.get("test_id", ""),
                        category="security",
                        severity=severity,
                        code=item.get("test_id", ""),
                        message=item.get("issue_text", ""),
                        file=item.get("filename", ""),
                        line=int(item.get("line_number", 1)),
                        column=1,
                        confidence=confidence_score,
                        rule_url=item.get("more_info", ""),
                    )
                )
        except Exception as exc:
            self.health.error_message = f"failed to parse bandit json output: {exc}"

        self.health.diagnostics_count = len(diagnostics)
        return diagnostics
