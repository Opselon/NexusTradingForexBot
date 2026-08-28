"""Base analyzer abstract interface for NSE diagnostics."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from nexus_scalp.diagnostics.models import AnalyzerHealth, Diagnostic


class BaseAnalyzer(ABC):
    name: str = "base"

    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = workspace or Path.cwd()
        self.health = AnalyzerHealth(name=self.name)

    @abstractmethod
    def version(self) -> str:
        """Return the analyzer version string."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the analyzer tool/executable is installed and usable."""
        ...

    @abstractmethod
    def analyze(self, target_paths: list[str] | None = None) -> list[Diagnostic]:
        """Execute the analyzer and return canonical diagnostics."""
        ...
