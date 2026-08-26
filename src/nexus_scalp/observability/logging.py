"""Enterprise Structured Logging Engine (severity-split, date-organized)
=====================================================================
Centralized, thread-safe, structured logging for the Nexus Scalp Engine
(observability contract — public API: configure_logging / get_logger).

Log layout (one convention, severity-first)::

    logs/
      info/     YYYY/MM/YYYY-MM-DD[.part-NNN].log
      warning/  YYYY/MM/YYYY-MM-DD[.part-NNN].log
      error/    YYYY/MM/YYYY-MM-DD[.part-NNN].log
      critical/ YYYY/MM/YYYY-MM-DD[.part-NNN].log

Every record carries::

    timestamp   ISO-8601 with explicit project timezone (+03:30)
    level       INFO / WARNING / ERROR / CRITICAL
    event       stable event name (GENERATION_STARTED ...) or message
    component   originating subsystem (StrategyFactory, BacktestEngine, ...)
    category    DATA/MODEL/TRAINING/STRATEGY/BACKTEST/VALIDATION/EXECUTION/
                RISK/ACCOUNTING/DATABASE/NETWORK/API/TELEGRAM/UI/
                CONFIGURATION/SYSTEM
    correlation_id / run_id / generation_id / strategy_id / ... when bound
    machine / process / thread identifiers
    exception type/message + full stack trace (exc_info preserved)

Rotation: daily + size cap (MAX_BYTES_PER_FILE). Files that exceed the cap
split into ``YYYY-MM-DD.part-NNN.log`` — never deleted mid-use, so no logs
are lost. Retention: per-severity ``retention_days`` (defaults: info/
warning 30, error 90, critical 365) enforced by a prune pass on configure
and then at most hourly; unknown buckets (logs/archive) are never
auto-deleted.

Multi-process safety: every write is appended under a process-wide
``threading.RLock`` and files are opened in append mode, so parallel
EXE / CLI / worker processes never corrupt each other's sink.

Hot path: file writes are synchronous appends — same as the previous
production logging and the only per-event hot-path sink previously observed
is the throttled champion verification (BUG-118). No queue indirection:
ProcessorFormatter chains run in the calling thread, so contextvars
correlation ids are captured correctly and records are formatted once.

Sensitive data: centralized key-based redaction + high-entropy catch-all
(BUG-121 discipline) — secrets accidentally passed to a log call never
reach disk.
"""

from __future__ import annotations

import logging
import logging.handlers
import math
import re
import sys
import threading
import time
from collections.abc import MutableMapping
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

import structlog

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default base directory for the severity-split log tree (source/dev runs).
LOG_DIR = Path("logs")

#: Project-standard timezone label appended to every timestamp (+03:30 Iran).
#: Timestamps carry an explicit offset so they are never ambiguous.
TIMESTAMP_TZ = "+03:30"

#: Max size of one log file before it is split into .part-NNN.log.
MAX_BYTES_PER_FILE = 10 * 1024 * 1024

#: Retention (days) per severity bucket. Configurable via configure_logging.
DEFAULT_RETENTION_DAYS: dict[str, int] = {
    "info": 30,
    "warning": 30,
    "error": 90,
    "critical": 365,
}

#: Severity name -> subdirectory.
_SEVERITY_DIRS: dict[str, str] = {
    "DEBUG": "info",
    "INFO": "info",
    "WARNING": "warning",
    "ERROR": "error",
    "CRITICAL": "critical",
}

#: stdlib loggers silenced to WARNING regardless of app level.
_QUIET_LOGGERS = ("urllib3", "asyncio", "polars", "torch", "uvicorn")

#: Structural/trusted keys exempt from high-entropy value redaction. Event
#: names, components and stable codes can be long alnum runs (e.g.
#: GLOBAL_KILL_SWITCH_ACTIVATED) and must never be scrubbed.
_TRUSTED_STRING_KEYS = frozenset(
    {
        "event",
        "component",
        "logger",
        "level",
        "category",
        "error_code",
        "exc_info",
        "timestamp",
        "machine",
    }
)

#: Secret-bearing key fragments (key-based redaction).
_SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "bearer",
    "credential",
    "private_key",
    "bot_token",
    "access_id",
)

#: Fragments that look secret-bearing but are NOT secrets. Substring matching
#: on _SENSITIVE_KEY_FRAGMENTS would wrongly redact these (BUG-126-class
#: over-redaction): e.g. "author", "authored_by", "token_bucket" (a rate-limit
#: counter, not a credential). These are excluded from key-based redaction.
_NON_SECRET_KEY_FRAGMENTS = (
    "author",
    "authored_by",
    "token_bucket",
    "authority",
)

#: Secret-shaped assignment catch-all for trusted string values (event/message/
#: exc_info). These keys are intentionally NOT scrubbed by the high-entropy
#: catch-all (which requires >=24-char high-entropy runs), so short/medium
#: secrets like password=SECRET would otherwise leak. Redacts assignment-style
#: `key=value` / `key: value` and bare telemetry-shaped secrets.
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)(?:password|passwd|secret|token|api[_-]?key|apikey|private[_-]?key|"
    r"bot[_-]?token|access[_-]?id|bearer|authorization|credential)"
    r"\s*[:=]\s*['\\\"]?[^\s'\\\"]{2,}"
)

#: High-entropy catch-all (BUG-121 discipline): >=24 char runs that are
#: >=75% alnum with Shannon entropy >=3.2 bits/char are treated as secrets.
#: ANSI escape sequence stripper for FILE output (rich ExceptionRenderer
#: colors tracebacks even with colors=False; files must be plain text).
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_HIGH_ENTROPY_RE = re.compile(r"[A-Za-z0-9_\-+/=]{24,}")
_ENTROPY_ALNUM_THRESHOLD = 0.75
_ENTROPY_BITS_THRESHOLD = 3.2

#: Stable event -> category map (master logging brief §16/§17).
EVENT_CATEGORIES: dict[str, str] = {
    "APPLICATION_STARTED": "SYSTEM",
    "APPLICATION_STOPPED": "SYSTEM",
    "EVOLUTION_STARTED": "STRATEGY",
    "GENERATION_STARTED": "STRATEGY",
    "GENERATION_COMPLETED": "STRATEGY",
    "GENERATION_FAILED": "STRATEGY",
    "STRATEGY_GENERATED": "STRATEGY",
    "STRATEGY_REJECTED": "STRATEGY",
    "STRATEGY_PROMOTED": "STRATEGY",
    "STRATEGY_SCORED": "STRATEGY",
    "STRATEGY_RANKED": "STRATEGY",
    "BACKTEST_STARTED": "BACKTEST",
    "BACKTEST_COMPLETED": "BACKTEST",
    "BACKTEST_FAILED": "BACKTEST",
    "WALK_FORWARD_STARTED": "BACKTEST",
    "WALK_FORWARD_FAILED": "BACKTEST",
    "OOS_STARTED": "VALIDATION",
    "OOS_FAILED": "VALIDATION",
    "ROBUSTNESS_STARTED": "VALIDATION",
    "ROBUSTNESS_FAILED": "VALIDATION",
    "MODEL_LOADED": "MODEL",
    "MODEL_INFERENCE_FAILED": "MODEL",
    "MODEL_PROMOTED": "MODEL",
    "TRAINING_STARTED": "TRAINING",
    "TRAINING_COMPLETED": "TRAINING",
    "TRAINING_FAILED": "TRAINING",
    "ORDER_CREATED": "EXECUTION",
    "ORDER_REJECTED": "EXECUTION",
    "ORDER_EXECUTED": "EXECUTION",
    "POSITION_OPENED": "EXECUTION",
    "POSITION_CLOSED": "EXECUTION",
    "ACCOUNTING_UPDATED": "ACCOUNTING",
    "ACCOUNTING_ERROR": "ACCOUNTING",
    "ACCOUNTING_MISMATCH": "ACCOUNTING",
    "RISK_LIMIT_NEAR": "RISK",
    "RISK_VALIDATION_FAILED": "RISK",
    "GLOBAL_KILL_SWITCH_ACTIVATED": "RISK",
    "SAVE_FAILED": "DATABASE",
    "VALIDATION_FAILED": "VALIDATION",
    "LOW_TRADE_COUNT": "BACKTEST",
    "TELEGRAM_NOTIFICATION_FAILED": "TELEGRAM",
    "FAILED": "GENERAL",
}

#: Stable error codes by category (master logging brief §18).
ERROR_CODES: dict[str, dict[str, str]] = {
    "DATA": {"callback": "NEXUS-DATA-001"},
    "MODEL": {"callback": "NEXUS-MODEL-001"},
    "TRAINING": {"callback": "NEXUS-TRAIN-001"},
    "BACKTEST": {"callback": "NEXUS-BT-001"},
    "VALIDATION": {"callback": "NEXUS-OOS-001"},
    "RISK": {"callback": "NEXUS-RISK-001"},
    "EXECUTION": {"callback": "NEXUS-EXEC-001"},
    "ACCOUNTING": {"callback": "NEXUS-ACCOUNT-001"},
    "DATABASE": {"callback": "NEXUS-DB-001"},
}

# ---------------------------------------------------------------------------
# Process-wide state
# ---------------------------------------------------------------------------

#: Shared write lock across ALL severity handlers of this process — appends
#: are atomic within the process; files are opened append-mode so concurrent
#: processes append safely too.
_WRITE_LOCK = threading.RLock()

_current_base: Path = LOG_DIR
_current_retention_days: dict[str, int] = dict(DEFAULT_RETENTION_DAYS)
_last_prune_ts: float = 0.0
_PRUNE_INTERVAL_SEC = 3600.0


def _set_state(base: Path, retention: dict[str, int] | None) -> None:
    """Update module state without global statements (PLW0603-safe)."""
    global _current_base, _current_retention_days  # noqa: PLW0603
    _current_base = base
    if retention:
        merged = dict(DEFAULT_RETENTION_DAYS)
        merged.update(retention)
        _current_retention_days = merged


def reset_prune_throttle() -> None:
    """Reset the hourly retention-prune throttle (test/ops seam)."""
    _set_prune_ts(0.0)


def _set_prune_ts(ts: float) -> None:
    """Record the last prune timestamp (no global statement in caller)."""
    global _last_prune_ts  # noqa: PLW0603
    _last_prune_ts = ts


# ---------------------------------------------------------------------------
# Redaction (centralized; key-based + high-entropy catch-all)
# ---------------------------------------------------------------------------


def _shannon_entropy(value: str) -> float:
    """Empirical Shannon entropy in bits/char (0.0 for empty strings)."""
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    size = float(len(value))
    return -sum((c / size) * math.log2(c / size) for c in counts.values())


def _redact_value(value: Any) -> Any:
    """Secret-assignment scrub + high-entropy catch-all for string values.

    First pass: redact short/medium secret-bearing assignments (password=SECRET,
    TELEGRAM_BOT_TOKEN=..., bearer ..., etc.) that the >=24-char high-entropy
    catch-all would otherwise miss. Second pass: the high-entropy blob catcher.
    """
    if not isinstance(value, str) or not value:
        return value

    val = _SECRET_ASSIGN_RE.sub("[REDACTED_SECRET]", value)

    def _scrub(match: re.Match[str]) -> str:
        token = match.group(0)
        alnum_ratio = sum(1 for ch in token if ch.isalnum()) / len(token)
        if (
            alnum_ratio >= _ENTROPY_ALNUM_THRESHOLD
            and _shannon_entropy(token) >= _ENTROPY_BITS_THRESHOLD
        ):
            return "[REDACTED_SECRET]"
        return token

    return _HIGH_ENTROPY_RE.sub(_scrub, val)


def _redact_sensitive_fields(
    logger: Any,
    method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Centralized redaction: secret-bearing KEYS and high-entropy VALUES.

    Two layers (BUG-121/BUG-126 discipline, hardened 2026-08-26):
      * Key-based: a key containing a secret fragment is redacted ENTIRELY,
        unless the key contains a known non-secret fragment (author /
        authored_by / token_bucket / authority) — prevents over-redaction of
        benign fields.
      * Value-based: every string value (including trusted keys event/message/
        exc_info) is scanned for secret-shaped assignments AND high-entropy
        blob runs. This closes the under-redaction gap where short secrets in
        trusted free text passed through cleartext.
    """
    for key in list(event_dict.keys()):
        lower = str(key).lower()
        # Benign keys that merely contain secret-like substrings are never masked.
        if any(frag in lower for frag in _NON_SECRET_KEY_FRAGMENTS):
            continue
        if any(frag in lower for frag in _SENSITIVE_KEY_FRAGMENTS):
            event_dict[key] = "[REDACTED_SECRET]"
            continue
        if isinstance(event_dict[key], str):
            scrubbed = _redact_value(event_dict[key])
            if scrubbed is not event_dict[key]:
                event_dict[key] = scrubbed
    return event_dict


# ---------------------------------------------------------------------------
# Timestamp
# ---------------------------------------------------------------------------


def timestamp_now() -> str:
    """ISO-8601 timestamp string with explicit project timezone (+03:30).

    Convenience wrapper: same format the pipeline stamps every event with.
    """
    now = datetime.now()
    return f"{now.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}{TIMESTAMP_TZ}"


def _add_timestamp(
    logger: Any,
    method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """ISO-8601 timestamp with explicit project timezone.

    Produces ``2026-08-20T02:25:14.392+03:30`` — date + time + milliseconds +
    explicit offset, never bare HH:MM. Runs LAST so it reflects the resolved
    event (and uses a fixed width for stable alignment).
    """
    now = datetime.now()
    event_dict["timestamp"] = f"{now.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}{TIMESTAMP_TZ}"
    return event_dict


# ---------------------------------------------------------------------------
# Rotation: severity-dated file handler (daily + size split)
# ---------------------------------------------------------------------------


def _today_stamp() -> str:
    return time.strftime("%Y-%m-%d")


def _severity_dir(levelno: int) -> str:
    return _SEVERITY_DIRS.get(logging.getLevelName(levelno), "info")


def _dated_log_path(base: Path, severity: str, date_stamp: str, part: int = 0) -> Path:
    """``logs/<severity>/YYYY/MM/YYYY-MM-DD[.part-NNN].log``"""
    year, month = date_stamp[:4], date_stamp[5:7]
    directory = base / severity / year / month
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{date_stamp}.log" if part <= 0 else f"{date_stamp}.part-{part:03d}.log"
    return directory / name


def _next_part_number(directory: Path, date_stamp: str) -> int:
    existing = list(directory.glob(f"{date_stamp}.part-*.log"))
    nums = [int(p.stem.rsplit("-", 1)[1]) for p in existing if p.stem.rsplit("-", 1)[1].isdigit()]
    return (max(nums) + 1) if nums else 1


class _LevelMatchFilter(logging.Filter):
    """stdlib delivers every record to EVERY handler whose level <= record
    level — that would dump INFO into the warning/error/critical files.
    This filter keeps ONLY records of the handler's exact severity."""

    def __init__(self, level: int) -> None:
        super().__init__()
        self.target_level = level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno == self.target_level


class DatedRotatingFileHandler(logging.Handler):
    """Daily + size-bounded severity file handler (append mode, lock-safe).

    - The filename is recomputed at emit time from the current date.
    - When the active file would exceed ``max_bytes``, the next event goes
      to ``YYYY-MM-DD.part-NNN.log`` (NNN monotonic across writers).
    - All writes go through the process-wide ``_WRITE_LOCK``; the file is
      opened in append mode so concurrent processes append safely.
    """

    def __init__(self, base_dir: Path, level: int, max_bytes: int = MAX_BYTES_PER_FILE) -> None:
        super().__init__(level=level)
        self.addFilter(_LevelMatchFilter(level))
        self._base_dir = Path(base_dir)
        self._max_bytes = max_bytes
        self._date_stamp = ""
        self._active_part = 0
        self._stream: TextIO | None = None
        self._path: Path | None = None

    # -- path resolution ------------------------------------------------
    def _target_path(self) -> Path:
        today = _today_stamp()
        if today != self._date_stamp:
            self._date_stamp = today
            self._active_part = 0
        severity = _severity_dir(self.level)
        if self._active_part:
            directory = self._base_dir / severity / today[:4] / today[5:7]
            self._active_part = max(self._active_part, _next_part_number(directory, today))
            return _dated_log_path(self._base_dir, severity, today, self._active_part)
        return _dated_log_path(self._base_dir, severity, today)

    # -- streaming ------------------------------------------------------
    def _open_stream(self) -> TextIO:
        path = self._target_path()
        stream = open(path, "a", encoding="utf-8")
        self._path = path
        return stream

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.flush()
                self._stream.close()
            except OSError:
                pass
            self._stream = None

    @property
    def baseFilename(self) -> str:
        path = self._target_path()
        return str(path)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            with _WRITE_LOCK:
                today = _today_stamp()
                if today != self._date_stamp:
                    self._close_stream()
                    self._date_stamp = today
                    self._active_part = 0
                if self._stream is None:
                    self._stream = self._open_stream()
                if self._stream is None:
                    return
                path = self._path or self._target_path()
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                message = self.format(record) + "\n"
                # Files must be plain text — rich's ExceptionRenderer
                # still emits ANSI colors even with colors=False.
                message = _ANSI_RE.sub("", message)
                if size + len(message.encode("utf-8")) > self._max_bytes:
                    self._close_stream()
                    self._active_part += 1
                    self._stream = self._open_stream()
                self._stream.write(message)
                self._stream.flush()
        except Exception:
            # Never let a log write take down the engine (BUG-122 discipline).
            self.handleError(record)

    def close(self) -> None:
        with _WRITE_LOCK:
            self._close_stream()
            super().close()


# ---------------------------------------------------------------------------
# Retention pruning
# ---------------------------------------------------------------------------


def _prune_old_logs(base: Path | None = None, retention_days: dict[str, int] | None = None) -> None:
    """Delete severity files older than their retention window.

    Runs at configure time, then at most once per hour. Unknown buckets
    (e.g. ``logs/archive``) are never auto-deleted.
    """
    now = time.time()
    if _last_prune_ts > 0 and now - _last_prune_ts < _PRUNE_INTERVAL_SEC:
        return
    _set_prune_ts(now)

    root = Path(base or _current_base)
    retention = retention_days or _current_retention_days
    if not root.exists():
        return
    for severity_dir in root.iterdir():
        if not severity_dir.is_dir():
            continue
        days = retention.get(severity_dir.name.lower())
        if days is None:
            continue  # archive/ and unknown buckets: never auto-delete
        cutoff = now - 3600 * 24 * days
        for path in severity_dir.rglob("*.log"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _console_stream() -> TextIO:
    stream: Any = sys.stdout
    try:
        reconfigured = stream.reconfigure(errors="replace")  # BUG-122 hardened
        if reconfigured is not None:
            stream = reconfigured
    except (AttributeError, ValueError, OSError):
        pass
    return stream


def _configure_stdout() -> None:
    """Reconfigure the console stream once (idempotent, BUG-122 discipline)."""
    try:
        _console_stream()
    except Exception:
        pass


def configure_logging(
    log_level: str = "INFO",
    json_format: bool = False,
    log_to_file: bool = True,
    log_file_path: Path | None = None,
    retention_days: dict[str, int] | None = None,
) -> None:
    """Configure the centralized severity-split logging pipeline.

    Args:
        log_level: Severity threshold ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL").
        json_format: If True the FILE output uses JSON lines (machine-readable);
            console output stays human-readable (master brief §28).
        log_to_file: If False only the console handler is installed.
        log_file_path: Custom BASE directory (default ``logs/``). The packaged
            EXE passes ``%LOCALAPPDATA%/NexusScalpEngine/logs``.
        retention_days: Per-severity retention overrides (defaults above).
    """
    numeric_level = getattr(logging, str(log_level).upper(), logging.INFO)
    base_dir = Path(log_file_path) if log_file_path else LOG_DIR
    _set_state(base_dir, retention_days)

    _configure_stdout()

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        _redact_sensitive_fields,
        _add_timestamp,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_format
        else structlog.dev.ConsoleRenderer(colors=False, pad_event=0)
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    console_renderer: Any = structlog.dev.ConsoleRenderer(colors=True, pad_event=28)
    console_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            console_renderer,
        ],
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    with _WRITE_LOCK:
        root_logger.handlers.clear()

    console_handler = logging.StreamHandler(_console_stream())
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(numeric_level)
    root_logger.addHandler(console_handler)

    if log_to_file:
        base_dir.mkdir(parents=True, exist_ok=True)
        for levelno in (logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL):
            handler = DatedRotatingFileHandler(base_dir, levelno)
            handler.setFormatter(formatter)
            root_logger.addHandler(handler)

    for quiet in _QUIET_LOGGERS:
        logging.getLogger(quiet).setLevel(logging.WARNING)

    _prune_old_logs(base_dir, _current_retention_days)


def get_logger(name: str = "nexus_scalp") -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger (unchanged public contract)."""
    return structlog.get_logger(name)


def log_event(
    logger: structlog.stdlib.BoundLogger,
    level: str,
    event: str,
    component: str | None = None,
    category: str | None = None,
    **context: Any,
) -> None:
    """Emit a structured event with stable event name + category + context.

    Usage::

        log_event(logger, "INFO", "GENERATION_STARTED",
                  component="StrategyFactory", generation_id=14, run_id="R-1029")

    The event name becomes the message; category and error codes resolve
    from EVENT_CATEGORIES / ERROR_CODES when not provided.
    """
    method = getattr(logger, level.lower(), None)
    if method is None:
        return
    kwargs: dict[str, Any] = dict(context)
    if component:
        kwargs["component"] = component
    if category is None:
        category = EVENT_CATEGORIES.get(event, "GENERAL")
    kwargs["category"] = category
    if level.upper() in ("ERROR", "CRITICAL") and category in ERROR_CODES:
        kwargs.setdefault("error_code", ERROR_CODES[category].get("callback", ""))
    method(event, **kwargs)


def bind_correlation_id(
    logger: structlog.stdlib.BoundLogger,
    correlation_id: str,
) -> structlog.stdlib.BoundLogger:
    """Return a logger copy bound with the workflow correlation id."""
    return logger.bind(correlation_id=correlation_id)


__all__ = [
    "DEFAULT_RETENTION_DAYS",
    "ERROR_CODES",
    "EVENT_CATEGORIES",
    "LOG_DIR",
    "MAX_BYTES_PER_FILE",
    "DatedRotatingFileHandler",
    "bind_correlation_id",
    "configure_logging",
    "get_logger",
    "log_event",
    "timestamp_now",
]
