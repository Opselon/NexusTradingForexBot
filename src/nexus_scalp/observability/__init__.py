"""
Observability Subsystem
=======================
Structured JSON logging, telemetry, Prometheus metrics exporter, and tracing.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nexus_scalp.observability.logging import configure_logging, get_logger

__all__ = ["configure_logging", "get_logger"]


def __getattr__(name: str):
    if name in ("configure_logging", "get_logger"):
        import nexus_scalp.observability.logging as _logging

        return getattr(_logging, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
