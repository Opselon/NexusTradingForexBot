"""OBS-013 flood-control unit tests.

Real LogRecords from BOTH logging origins (structlog and stdlib) are pushed
through the real FloodControlFilter. ProcessorFormatter ignores DropEvent for
stdlib records, so a structlog processor cannot be the suppression point —
these tests pin the contract at the logging.Filter level, where both origins
are LogRecords.
"""

from __future__ import annotations

import logging
import time

import pytest

from nexus_scalp.observability.flood_control import (
    DEFAULT_FLOOD_THRESHOLD,
    FloodControlFilter,
)
from nexus_scalp.observability.logging import configure_logging


@pytest.fixture(scope="module")
def configured_logging():
    configure_logging(json_format=False, log_to_file=False)
    return True


def _record(event: str, *, level: int = logging.WARNING) -> logging.LogRecord:
    return logging.LogRecord(
        name="probe.flood",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=event,
        args=(),
        exc_info=None,
    )


def _structlog_record(event: str, *, level: int = logging.WARNING) -> logging.LogRecord:
    """A record the way structlog delivers it: msg IS the event dict."""
    rec = _record(event, level=level)
    rec.msg = {
        "event": event,
        "logger": "probe.flood",
        "level": "warning",
        "timestamp": "2026-10-21T22:00:00.000+03:30",
    }
    return rec


# ---------------------------------------------------------------------------
# original message survives
# ---------------------------------------------------------------------------


def test_first_occurrence_always_passes() -> None:
    f = FloodControlFilter()
    rec = _record("fresh event line")
    assert f.filter(rec) is True
    # The un-annotated record must not carry a suppression note.
    assert "flood_note" not in rec.msg


def test_leading_sample_passes_untouched() -> None:
    """The first flood_threshold occurrences emit verbatim (no annotation)."""
    f = FloodControlFilter()
    recs = [_record("same line") for _ in range(DEFAULT_FLOOD_THRESHOLD)]
    # Every occurrence in the leading sample is emitted...
    for rec in recs:
        assert f.filter(rec) is True
    # ...and the LAST one announces that further repeats are being collapsed,
    # so a suddenly quiet log is never mistaken for "no more occurrences".
    last = recs[-1]
    assert "repeated" in str(last.msg) or getattr(last, "flood_annotated", False)
    # The earlier ones are unannotated originals.
    for rec in recs[:-1]:
        assert "repeated" not in str(rec.msg)


# ---------------------------------------------------------------------------
# repeats collapse, signal and summary are preserved
# ---------------------------------------------------------------------------


def test_repeats_beyond_threshold_are_dropped() -> None:
    f = FloodControlFilter()
    n = DEFAULT_FLOOD_THRESHOLD + 7
    passed = sum(1 for _ in range(n) if f.filter(_record("identical noisy line")))
    assert passed == DEFAULT_FLOOD_THRESHOLD
    # ...and the filter KNOWS what it dropped.
    assert f.stats()["suppressed_total"] == n - DEFAULT_FLOOD_THRESHOLD


def test_different_signatures_are_independent() -> None:
    f = FloodControlFilter()
    n = DEFAULT_FLOOD_THRESHOLD + 5
    a = sum(1 for _ in range(n) if f.filter(_record("component A failed")))
    b = sum(1 for _ in range(n) if f.filter(_record("component B failed")))
    assert a == DEFAULT_FLOOD_THRESHOLD, "component B's flood swallowed component A"
    assert b == DEFAULT_FLOOD_THRESHOLD, "component A's flood swallowed component B"


def test_window_rollover_emits_summary() -> None:
    f = FloodControlFilter(window_sec=0.05)
    for _ in range(DEFAULT_FLOOD_THRESHOLD * 3):
        f.filter(_record("burst line"))
    assert f.stats()["suppressed_total"] == DEFAULT_FLOOD_THRESHOLD * 3 - DEFAULT_FLOOD_THRESHOLD
    time.sleep(0.08)
    rec = _record("burst line")
    assert f.filter(rec) is True
    # The first post-window occurrence carries what was hidden.
    assert "flood-suppressed" in str(rec.msg)


# ---------------------------------------------------------------------------
# severity floor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", [logging.CRITICAL, logging.FATAL])
def test_critical_and_fatal_never_suppressed(level: int) -> None:
    f = FloodControlFilter()
    for _ in range(DEFAULT_FLOOD_THRESHOLD * 4):
        assert f.filter(_record("critical burst", level=level)) is True


# ---------------------------------------------------------------------------
# structlog dict payloads
# ---------------------------------------------------------------------------


def test_structlog_dict_records_collapse() -> None:
    """structlog keeps the event DICT in record.msg; the signature must be the
    event FIELD, not str(dict) - otherwise the per-record timestamp makes every
    line a unique signature and nothing collapses (observed live)."""
    f = FloodControlFilter()
    n = DEFAULT_FLOOD_THRESHOLD + 10
    passed = sum(1 for _ in range(n) if f.filter(_structlog_record("noisy repeat")))
    assert passed == DEFAULT_FLOOD_THRESHOLD


def test_annotation_keeps_signature_stable() -> None:
    """Annotating must NOT change the signature: in-place event mutation made
    annotated records a fresh signature and floods escaped suppression."""
    f = FloodControlFilter()
    recs = [_record("same line") for _ in range(DEFAULT_FLOOD_THRESHOLD + 5)]
    outcomes = [f.filter(r) for r in recs]
    # All occurrences share ONE signature; only the leading sample passes.
    assert outcomes == [True] * DEFAULT_FLOOD_THRESHOLD + [False] * 5
    # The threshold-annotated record still carries its stable note.
    note_seen = any(
        getattr(r, "flood_annotated", False) or "flood_note" in str(r.msg)
        for r in recs[:DEFAULT_FLOOD_THRESHOLD]
    )
    assert note_seen


def test_structlog_annotation_uses_side_field_not_event() -> None:
    f = FloodControlFilter()
    rec = _structlog_record("same line")
    for _ in range(DEFAULT_FLOOD_THRESHOLD):
        f.filter(_structlog_record("same line"))
    assert f.filter(rec) is False
    # The annotated earlier record kept event == original message.
    f2 = FloodControlFilter()
    last = None
    for _ in range(DEFAULT_FLOOD_THRESHOLD):
        r = _structlog_record("same line")
        if f2.filter(r):
            last = r
    assert last is not None
    assert isinstance(last.msg, dict)
    assert last.msg["event"] == "same line", "event field was mutated"
    assert "flood_note" in last.msg
