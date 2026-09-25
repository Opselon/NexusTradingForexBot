"""Canonical Diagnostic Models for the NSE Code Analyzer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Diagnostic:
    schema_version: str = "1.0"
    tool: str = "unknown"
    source: str = ""
    category: str = "lint"  # syntax, lint, type, security, code_smell, import, style, configuration, infrastructure, analyzer_failure
    severity: str = "warning"  # error, warning, info
    code: str = ""
    message: str = ""
    file: str = ""
    line: int = 1
    column: int = 1
    end_line: int | None = None
    end_column: int | None = None
    fixable: bool = False
    confidence: float = 1.0
    rule_url: str = ""
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.fingerprint:
            norm_file = str(self.file).replace("\\", "/")
            self.fingerprint = f"{self.tool}:{self.code}:{norm_file}:{self.line}:{self.column}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tool": self.tool,
            "source": self.source,
            "category": self.category,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "fixable": self.fixable,
            "confidence": self.confidence,
            "rule_url": self.rule_url,
            "fingerprint": self.fingerprint,
        }


@dataclass
class AnalyzerHealth:
    name: str
    available: bool = False
    executable: str = ""
    version: str = ""
    configuration_valid: bool = True
    execution_status: str = "NOT_INSTALLED"  # NOT_INSTALLED, AVAILABLE, RUNNING, COMPLETED, FAILED, TIMEOUT, INTERRUPTED, CONFIGURATION_ERROR
    exit_code: int = 0
    duration_ms: float = 0.0
    diagnostics_count: int = 0
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "executable": self.executable,
            "version": self.version,
            "configuration_valid": self.configuration_valid,
            "execution_status": self.execution_status,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "diagnostics_count": self.diagnostics_count,
            "error_message": self.error_message,
        }
