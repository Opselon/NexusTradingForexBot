"""MT5 diagnostic failure-storm throttle — regression tests (2026-09-09).

Pins the D1 fix from the storage-hygiene pass: identical MT5 call failures
during a degraded connection repeat at retry cadence (~100ms) and used to
emit an UNBOUNDED stream of WARNING lines (a measured driver of multi-GB
client log trees). Now: first failure always logs, identical repeats inside
the 30s window are demoted to DEBUG with a counter, one honest
FAILURE_STORM_SUMMARY line re-logs after the window, and recoveries are
never suppressed.
"""

from __future__ import annotations

import logging

import pytest

from nexus_scalp.adapters.mt5.diagnostics import (
    MT5CallDiagnostic,
    _emit,
    reset_storm_suppression,
)


@pytest.fixture(autouse=True)
def _clean_storm_state():
    reset_storm_suppression()
    yield
    reset_storm_suppression()


@pytest.fixture()
def capture():
    logger = logging.getLogger("test.mt5.storm")
    logger.setLevel(logging.DEBUG)
    records: list[logging.LogRecord] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Handler(level=logging.DEBUG)
    logger.addHandler(handler)
    yield logger, records
    logger.removeHandler(handler)


def _failed(op: str = "symbol_info_tick", code: int | None = 10031) -> MT5CallDiagnostic:
    return MT5CallDiagnostic(
        operation=op,
        status="FAILED",
        duration_ms=1.0,
        mt5_error_code=code,
        mt5_error_message="NO_CONNECTION",
    )


def test_first_failure_always_logs_warning(capture) -> None:
    logger, records = capture
    _emit(_failed(), logger.name)
    assert any(r.levelno == logging.WARNING for r in records)


def test_identical_repeats_suppressed_inside_window(capture) -> None:
    logger, records = capture
    for _ in range(50):
        _emit(_failed(), logger.name)
    warnings = [r for r in records if r.levelno == logging.WARNING]
    # exactly ONE warning line for 50 identical failures inside the window
    assert len(warnings) == 1


def test_suppressed_repeats_still_visible_at_debug(capture) -> None:
    logger, records = capture
    for _ in range(5):
        _emit(_failed(), logger.name)
    debugs = [r for r in records if r.levelno == logging.DEBUG]
    assert len(debugs) == 4  # every repeat remains observable at DEBUG


def test_window_close_emits_honest_summary(capture) -> None:
    logger, records = capture
    _emit(_failed(), logger.name)
    # simulate window elapse by rewriting the recorded timestamp
    from nexus_scalp.adapters.mt5 import diagnostics as diag_mod

    key = ("symbol_info_tick", 10031, "NO_CONNECTION")
    with diag_mod._MT5_STORM_LOCK:
        state = diag_mod._MT5_STORM_STATE[key]
        state["last"] -= diag_mod._MT5_STORM_WINDOW_SEC + 1.0
        state["suppressed"] = 42
    _emit(_failed(), logger.name)
    summaries = [r for r in records if "FAILURE_STORM_SUMMARY" in r.getMessage()]
    assert len(summaries) == 1
    assert "suppressed_identical_failures=42" in summaries[0].getMessage()


def test_distinct_error_codes_not_cross_suppressed(capture) -> None:
    logger, records = capture
    _emit(_failed(code=10031), logger.name)
    _emit(_failed(code=10018), logger.name)
    warnings = [r for r in records if r.levelno == logging.WARNING]
    assert len(warnings) == 2


def test_recovery_success_never_suppressed(capture) -> None:
    logger, records = capture
    _emit(_failed(), logger.name)
    ok = MT5CallDiagnostic(operation="symbol_info_tick", status="SUCCESS", duration_ms=2.0)
    _emit(ok, logger.name)
    # the success closes the storm: a following failure logs fresh again
    _emit(_failed(), logger.name)
    warnings = [r for r in records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
