"""
Enterprise Structured Logging Engine
====================================
Provides low-overhead, thread-safe, structured logging with correlation IDs,
execution mode tags, and non-blocking JSON formatting.

Key Features:
    - Context Binding: Automatically binds `symbol`, `account_id`, `execution_mode`,
      and `request_id` to log entries.
    - Dual Output: High-performance colored console formatting for CLI / DEV,
      JSON formatting for Docker / Production logs.
    - Audit Security: Redacts passwords, API tokens, and sensitive account data.
"""

import logging
import logging.handlers
import sys
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import structlog

LOG_DIR = Path("artifacts/logs")


def _redact_sensitive_fields(
    logger: Any,
    method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """
    Processor filtering sensitive authentication keys before writing logs.
    """
    sensitive_keys = {"password", "secret", "token", "api_key", "pass"}
    for key in list(event_dict.keys()):
        if any(s in key.lower() for s in sensitive_keys):
            event_dict[key] = "[REDACTED_SECRET]"
    return event_dict


def configure_logging(
    log_level: str = "INFO",
    json_format: bool = False,
    log_to_file: bool = True,
    log_file_path: Path | None = None,
) -> None:
    """
    Configures root loggers and structlog pipeline with enhanced terminal UI/UX.

    Args:
        log_level: Severity threshold ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL").
        json_format: If True, outputs raw JSON lines (ideal for Docker/Loki).
        log_to_file: If True, writes rotated log files to disk.
        log_file_path: Custom log file destination. Defaults to `artifacts/logs/nse_live.log`.
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    target_path: Path | None = None
    if log_to_file:
        target_path = log_file_path or (LOG_DIR / "nse_live.log")
        target_path.parent.mkdir(parents=True, exist_ok=True)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
        structlog.processors.StackInfoRenderer(),
        _redact_sensitive_fields,
    ]

    renderer: Any
    if json_format:
        renderer = structlog.processors.JSONRenderer()
    else:
        # Upgraded Terminal UI/UX: Rich color scheme with aligned pads and clear key-value styling
        renderer = structlog.dev.ConsoleRenderer(
            colors=True,
            pad_event=28,
        )

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handlers: list[logging.Handler] = []

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(numeric_level)
    handlers.append(console_handler)

    if log_to_file:
        file_handler = logging.handlers.RotatingFileHandler(
            filename=str(target_path or (LOG_DIR / "nse_live.log")),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(numeric_level)
        handlers.append(file_handler)

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    root_logger.handlers.clear()
    for handler in handlers:
        root_logger.addHandler(handler)

    for quiet_logger in ["urllib3", "asyncio", "polars", "torch", "uvicorn"]:
        logging.getLogger(quiet_logger).setLevel(logging.WARNING)


def get_logger(name: str = "nexus_scalp") -> structlog.stdlib.BoundLogger:
    """
    Returns a bound structlog logger instance.

    Args:
        name: Logger identifier namespace.
    """
    return structlog.get_logger(name)