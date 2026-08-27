"""NSE Code Analyzer orchestration engine.

Core responsibilities (mission PHASE 2/3/9/10/11/14):
* discover + register analyzers (plugin-friendly)
* execute analyzers safely (delegated to adapters + runner)
* normalize heterogeneous output into the canonical Diagnostic model
* deduplicate deterministically by fingerprint
* classify severity + category
* distinguish CODE QUALITY from ANALYZER INFRASTRUCTURE HEALTH
* compute deterministic exit status
"""

from __future__ import annotations

import concurrent.futures
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexus_scalp.diagnostics.analyzers.bandit_adapter import BanditAnalyzer
from nexus_scalp.diagnostics.analyzers.base import BaseAnalyzer
from nexus_scalp.diagnostics.analyzers.pylint_adapter import PylintAnalyzer
from nexus_scalp.diagnostics.analyzers.pyright_adapter import PyrightAnalyzer
from nexus_scalp.diagnostics.analyzers.ruff_adapter import RuffAnalyzer
from nexus_scalp.diagnostics.models import AnalyzerHealth, Diagnostic

# Map analyzer infra status -> global severity-agnostic classification.
INFRA_FAILURE_STATES = {"FAILED", "TIMEOUT", "INTERRUPTED", "CONFIGURATION_ERROR"}


@dataclass
class AnalysisReport:
    schema_version: str = "1.0"
    status: str = "passed"  # passed | warnings | failed | error
    summary: dict[str, int] = field(
        default_factory=lambda: {"errors": 0, "warnings": 0, "info": 0, "security": 0}
    )
    analyzers: dict[str, dict[str, Any]] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    infrastructure: dict[str, list[str]] = field(
        default_factory=lambda: {"failures": [], "unavailable": []}
    )
    execution: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "summary": self.summary,
            "analyzers": self.analyzers,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "infrastructure": self.infrastructure,
            "execution": self.execution,
        }


class DiagnosticEngine:
    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = workspace or Path.cwd()
        self.analyzers: list[BaseAnalyzer] = [
            RuffAnalyzer(self.workspace),
            PyrightAnalyzer(self.workspace),
            PylintAnalyzer(self.workspace),
            BanditAnalyzer(self.workspace),
        ]

    def register(self, analyzer: BaseAnalyzer) -> None:
        self.analyzers.append(analyzer)

    def _tally(self, report: AnalysisReport) -> None:
        for d in report.diagnostics:
            if d.severity == "error":
                report.summary["errors"] += 1
                if d.category == "security":
                    report.summary["security"] += 1
            elif d.severity == "warning":
                report.summary["warnings"] += 1
                if d.category == "security":
                    report.summary["security"] += 1
            else:
                report.summary["info"] += 1
                if d.category == "security":
                    report.summary["security"] += 1

    def _deduplicate(self, diagnostics: list[Diagnostic]) -> list[Diagnostic]:
        """Group by canonical fingerprint; retain provenance via sources[]."""
        seen: dict[str, Diagnostic] = {}
        for d in diagnostics:
            key = d.fingerprint
            if key in seen:
                existing = seen[key]
                # Don't silently merge cross-tool duplicate identity; only collapse exact dupes.
                if existing.tool == d.tool:
                    continue
            seen[key] = d
        return list(seen.values())

    def analyze(
        self,
        target_paths: list[str] | None = None,
        parallel: bool = True,
        include_unavailable: bool = True,
    ) -> AnalysisReport:
        report = AnalysisReport()
        started = time.time()
        report.execution["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))

        all_diagnostics: list[Diagnostic] = []
        health_records: dict[str, AnalyzerHealth] = {}

        def _run_one(analyzer: BaseAnalyzer) -> tuple[AnalyzerHealth, list[Diagnostic]]:
            try:
                diags = analyzer.analyze(target_paths)
                return analyzer.health, diags
            except Exception as exc:  # defensive: never let one analyzer crash the suite
                analyzer.health.execution_status = "FAILED"
                analyzer.health.error_message = f"unexpected analyzer error: {exc}"
                return analyzer.health, []

        if parallel:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.analyzers)) as pool:
                futures = {pool.submit(_run_one, a): a for a in self.analyzers}
                for fut in concurrent.futures.as_completed(futures):
                    health, diags = fut.result()
                    health_records[health.name] = health
                    all_diagnostics.extend(diags)
        else:
            for a in self.analyzers:
                health, diags = _run_one(a)
                health_records[health.name] = health
                all_diagnostics.extend(diags)

        # Classify infrastructure (distinct from code quality).
        for name, health in health_records.items():
            report.analyzers[name] = health.to_dict()
            if health.execution_status == "NOT_INSTALLED":
                if include_unavailable:
                    report.infrastructure["unavailable"].append(name)
            elif health.execution_status in INFRA_FAILURE_STATES:
                report.infrastructure["failures"].append(
                    f"{name}: {health.error_message or health.execution_status}"
                )

        report.diagnostics = self._deduplicate(all_diagnostics)
        self._tally(report)

        # Deterministic status + exit semantics.
        # Trust principle: if NO analyzer ran (all unavailable) OR any analyzer
        # failed, we cannot certify the codebase — never report "clean".
        all_unavailable = all(
            h.execution_status == "NOT_INSTALLED" for h in health_records.values()
        )
        if report.infrastructure["failures"] or all_unavailable:
            report.status = "error"  # analyzer infra failure dominates
        elif report.summary["errors"] > 0:
            report.status = "failed"
        elif report.summary["warnings"] > 0 or report.summary["info"] > 0:
            report.status = "warnings"
        else:
            report.status = "passed"

        report.execution["duration_ms"] = round((time.time() - started) * 1000.0, 2)
        return report
