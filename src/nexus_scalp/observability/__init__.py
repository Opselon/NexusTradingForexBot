"""
Observability Subsystem
=======================
Structured JSON logging, telemetry, Prometheus metrics exporter, and tracing.
"""

from nexus_scalp.observability.logging import configure_logging, get_logger

__all__ = ["configure_logging", "get_logger"]
