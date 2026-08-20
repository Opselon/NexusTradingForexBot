"""
Unit Tests - Structured Logging (severity-split, date-organized)
================================================================
Verifies logging configuration, severity routing to per-level files,
ISO-8601 timestamps with explicit timezone, structured fields, exception
stack traces, secret redaction, retention pruning and correlation IDs.
"""

import logging
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from nexus_scalp.observability.logging import (
    _ANSI_RE,
    DEFAULT_RETENTION_DAYS,
    _LevelMatchFilter,
    _redact_sensitive_fields,
    configure_logging,
    get_logger,
    log_event,
    timestamp_now,
)


@pytest.fixture()
def log_root(tmp_path: Path):
    """Configure logging into a temp dir; yield the dir + a logger."""
    configure_logging(log_level="INFO", json_format=False, log_to_file=True, log_file_path=tmp_path)
    logger = get_logger("test_logging")
    yield tmp_path, logger
    root = logging.getLogger()
    lock = threading.RLock()
    lock.acquire()
    try:
        root.handlers.clear()
    finally:
        lock.release()
    root.setLevel(logging.WARNING)


def _read_events(dir_path: Path, severity: str) -> list[str]:
    """Event names found in logs/<severity>/YYYY/MM/YYYY-MM-DD.log files.

    Rendered line: ``2026-08-20T03:41:05.210+03:30 [info     ] EVENT [logger] k=v``
    """
    events: list[str] = []
    for f in sorted((dir_path / severity).rglob("*.log")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith("20") and "] " in line:
                head = line.split("]")[1].strip()
                events.append(head.split()[0])
    return events


def test_redact_sensitive_fields() -> None:
    """Password/token keys and high-entropy values are redacted."""
    event = {
        "message": "login attempt",
        "password": "secret123",
        "api_key": "sk-12345678901234567890abcdefghijklmnop",
        "event": "GLOBAL_KILL_SWITCH_ACTIVATED",
    }
    result = _redact_sensitive_fields(None, None, event)
    assert result["password"] == "[REDACTED_SECRET]"
    assert result["api_key"] == "[REDACTED_SECRET]"
    assert result["event"] == "GLOBAL_KILL_SWITCH_ACTIVATED"


def test_timestamp_now_iso_with_tz() -> None:
    """timestamp_now yields ISO-8601 with explicit +03:30 offset."""
    ts = timestamp_now()
    assert ts.endswith("+03:30"), ts
    parsed = datetime.strptime(ts[:23], "%Y-%m-%dT%H:%M:%S.%f")
    assert parsed.year == datetime.now().year


def test_info_routes_to_info_file(log_root) -> None:
    root, logger = log_root
    logger.info("APPLICATION_STARTED", component="Test", correlation_id="RUN-T")
    time.sleep(0.1)
    assert _read_events(root, "info") == ["APPLICATION_STARTED"]
    assert _read_events(root, "warning") == []
    assert _read_events(root, "error") == []
    assert _read_events(root, "critical") == []


def test_warning_routes_to_warning_file(log_root) -> None:
    root, logger = log_root
    logger.warning("LOW_TRADE_COUNT", component="Backtest", strategy_id="S-1", trades=2)
    time.sleep(0.1)
    assert _read_events(root, "warning") == ["LOW_TRADE_COUNT"]
    assert _read_events(root, "info") == []
    assert _read_events(root, "error") == []


def test_error_routes_to_error_file_with_stack(log_root) -> None:
    root, logger = log_root
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception(
            "BACKTEST_FAILED", component="BacktestEngine", strategy_id="S-1", category="BACKTEST"
        )
    time.sleep(0.2)
    assert _read_events(root, "error") == ["BACKTEST_FAILED"]
    assert _read_events(root, "info") == []
    content = (root / "error" / "2026" / "08" / "2026-08-20.log").read_text(encoding="utf-8")
    assert "ValueError: boom" in content
    assert "Traceback (most recent call last)" in content
    assert _ANSI_RE.search(content) is None


def test_critical_routes_to_critical_file(log_root) -> None:
    root, logger = log_root
    logger.critical("GLOBAL_KILL_SWITCH_ACTIVATED", component="RiskEngine", category="RISK")
    time.sleep(0.1)
    assert _read_events(root, "critical") == ["GLOBAL_KILL_SWITCH_ACTIVATED"]
    assert _read_events(root, "info") == []
    assert _read_events(root, "error") == []


def test_structured_fields_present(log_root) -> None:
    root, logger = log_root
    log_event(
        logger,
        "INFO",
        "GENERATION_STARTED",
        component="StrategyFactory",
        generation_id=14,
        run_id="R-1029",
        correlation_id="RUN-20260820-0001",
    )
    time.sleep(0.1)
    content = (root / "info" / "2026" / "08" / "2026-08-20.log").read_text(encoding="utf-8")
    assert "GENERATION_STARTED" in content
    assert "component=StrategyFactory" in content
    assert "generation_id=14" in content
    assert "run_id=R-1029" in content
    assert "correlation_id=RUN-20260820-0001" in content
    assert "category=STRATEGY" in content


def test_secrets_redacted_on_disk(log_root) -> None:
    root, logger = log_root
    logger.info(
        "SECRET_PROBE",
        api_key="sk-12345678901234567890abcdefghijklmnopqrstuvwxyz012345",
        password="hunter2",
        correlation_id="RUN-T",
    )
    time.sleep(0.1)
    content = (root / "info" / "2026" / "08" / "2026-08-20.log").read_text(encoding="utf-8")
    assert "sk-12345" not in content
    assert "hunter2" not in content
    assert "[REDACTED_SECRET]" in content


def test_level_match_filter_exact_severity() -> None:
    filt = _LevelMatchFilter(logging.INFO)
    assert filt.filter(_record(logging.INFO))
    assert not filt.filter(_record(logging.WARNING))
    assert not filt.filter(_record(logging.ERROR))
    assert not filt.filter(_record(logging.CRITICAL))
    filt_err = _LevelMatchFilter(logging.ERROR)
    assert filt_err.filter(_record(logging.ERROR))
    assert not filt_err.filter(_record(logging.CRITICAL))
    assert not filt_err.filter(_record(logging.INFO))


def _record(levelno: int) -> logging.LogRecord:
    return logging.LogRecord("t", levelno, __file__, 1, "m", None, None)


def test_configure_logging_and_get_logger() -> None:
    """Public API contract stays intact."""
    configure_logging(log_level="INFO", json_format=False, log_to_file=False)
    log = get_logger("test")
    assert log is not None


def test_retention_defaults() -> None:
    assert DEFAULT_RETENTION_DAYS["info"] >= 7
    assert DEFAULT_RETENTION_DAYS["error"] >= DEFAULT_RETENTION_DAYS["info"]
    assert DEFAULT_RETENTION_DAYS["critical"] >= DEFAULT_RETENTION_DAYS["error"]


def test_dated_path_convention(tmp_path: Path) -> None:
    """Handler writes to logs/<severity>/YYYY/MM/YYYY-MM-DD.log"""
    configure_logging(log_level="INFO", json_format=False, log_to_file=True, log_file_path=tmp_path)
    get_logger("t").info("APPLICATION_STARTED")
    time.sleep(0.1)
    today = datetime.now().strftime("%Y-%m-%d")
    year, month = today[:4], today[5:7]
    target = tmp_path / "info" / year / month / f"{today}.log"
    assert target.exists(), f"expected {target}"
