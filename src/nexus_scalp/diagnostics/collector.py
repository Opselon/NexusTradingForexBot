"""Bounded collector for Warning/Error log records.

A stdlib ``logging.Handler`` that:
- accepts WARNING and above (configurable),
- redacts obvious credential shapes,
- caps message length and queue size (oldest dropped),
- tags every record with the pipeline source (boot/cli/web/install/first_run).

The handler never raises and never blocks the logging call site: the app's
log path must stay untouched whether diagnostics are on or off.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

MAX_MESSAGE_CHARS = 2000
MAX_BUFFER = 500
MAX_CONTEXT_KEYS = 8

_LEVEL_PRIORITY = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "WARN": 30, "ERROR": 40, "CRITICAL": 50}

_REDACT_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"gho_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"github_pat_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"nsc_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(authorization\s*[:=]\s*)\S+"),
    re.compile(r"(?i)(token|secret|password)(\s*[=:]\s*)\S+"),
]


def redact_message(text: str) -> str:
    out = text
    for pattern in _REDACT_PATTERNS:
        out = pattern.sub(lambda m: m.group(0)[:8] + "[REDACTED]", out)
    return out[:MAX_MESSAGE_CHARS]


@dataclass
class DiagnosticRecord:
    ts: str
    level: str
    logger: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "level": self.level,
            "logger": self.logger,
            "message": self.message,
            "context": self.context,
        }


class DiagnosticCollector:
    """Thread-safe ring buffer of redacted WARNING+ records."""

    def __init__(self, source: str, max_buffer: int = MAX_BUFFER) -> None:
        self.source = source
        self._buffer: list[DiagnosticRecord] = []
        self._lock = threading.Lock()
        self._max = max_buffer
        self.dropped = 0

    def add(
        self,
        level: str,
        logger_name: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        # Contract: this collector carries WARNING+ records only, regardless
        # of who calls it (the log handler already filters; this is the
        # second gate so direct callers cannot bypass it).
        if _LEVEL_PRIORITY.get(level.upper(), 0) < 30:
            return
        rec = DiagnosticRecord(
            ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            level=level.upper(),
            logger=logger_name[:120],
            message=redact_message(message),
            context=context or {},
        )
        with self._lock:
            if len(self._buffer) >= self._max:
                self._buffer.pop(0)
                self.dropped += 1
            self._buffer.append(rec)

    def drain(self, limit: int = 200) -> list[dict[str, Any]]:
        """Pop up to ``limit`` records (oldest first)."""
        with self._lock:
            taken = self._buffer[:limit]
            del self._buffer[:limit]
        return [r.as_dict() for r in taken]

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)


class DiagnosticLogHandler(logging.Handler):
    """logging.Handler bridge into a DiagnosticCollector."""

    def __init__(self, collector: DiagnosticCollector, min_level: int = logging.WARNING) -> None:
        super().__init__(level=min_level)
        self.collector = collector

    def emit(self, record: logging.LogRecord) -> None:
        try:
            context: dict[str, Any] = {}
            if record.exc_info:
                context["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            if record.funcName:
                context["func"] = record.funcName[:80]
            self.collector.add(
                level=record.levelname,
                logger_name=record.name,
                message=record.getMessage(),
                context=context,
            )
        except Exception:  # pragma: no cover - a diagnostics bug must never log-flood
            self.handleError(record)  # stdlib: swallows + flags internally
