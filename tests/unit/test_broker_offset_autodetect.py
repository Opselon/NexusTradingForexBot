"""Broker server-UTC offset auto-detection (live-stream driven).

MT5 tick/bar epochs are server-wall-clock seconds; converting them with a
stale offset (the hardcoded +3h default) shifts every timestamp and freezes
the freshness gate after a broker DST transition — observed live on the
v9.0.20 install (MetaQuotes-Demo, EEST +3 -> EET +2). The detector adopts
the offset from the advancing stream; the env override always wins.
"""

from __future__ import annotations

import time as _time
from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.mt5 import providers


@pytest.fixture(autouse=True)
def _clean_detector(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(providers.BROKER_SERVER_UTC_OFFSET_MINUTES_ENV, raising=False)
    providers._BROKER_OFFSET_AUTODETECT.reset()
    yield
    providers._BROKER_OFFSET_AUTODETECT.reset()


def _offset_now() -> int:
    return providers.get_broker_server_utc_offset_minutes()


def test_default_offset_without_env_and_without_stream() -> None:
    assert _offset_now() == providers.BROKER_SERVER_UTC_OFFSET_MINUTES


def test_advancing_stream_adopts_true_offset() -> None:
    # Server on UTC+2 (EET winter): raw epoch = real UTC + 7200s.
    real_now = _time.time()
    base = real_now + 7200.0
    det = providers._BROKER_OFFSET_AUTODETECT
    for i in range(4):
        det.note_broker_epoch(base + i * 1.0)  # stream advances ~1s per tick
        _time.sleep(0.001)
    assert _offset_now() == 120
    # Conversion subtracts the adopted offset: raw epoch -> real UTC.
    converted = providers.broker_epoch_to_utc(base + 4)
    assert converted is not None
    assert abs((converted - datetime.now(UTC)).total_seconds()) < 90.0


def test_stale_stream_never_adopts() -> None:
    # Market closed: newest epoch is 3h in the server's past — no adoption.
    real_now = _time.time()
    stale = real_now + 7200.0 - 3 * 3600.0
    det = providers._BROKER_OFFSET_AUTODETECT
    for i in range(6):
        det.note_broker_epoch(stale + i)  # advances 1s per call but 3h old
    assert _offset_now() == providers.BROKER_SERVER_UTC_OFFSET_MINUTES


def test_env_override_beats_autodetect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(providers.BROKER_SERVER_UTC_OFFSET_MINUTES_ENV, "120")
    det = providers._BROKER_OFFSET_AUTODETECT
    real_now = _time.time()
    for i in range(4):  # stream says +2h... env pinned at 120 wins anyway
        det.note_broker_epoch(real_now + 7200.0 + i)
    assert _offset_now() == 120
    monkeypatch.setenv(providers.BROKER_SERVER_UTC_OFFSET_MINUTES_ENV, "180")
    assert _offset_now() == 180


def test_dst_transition_readopts_new_offset() -> None:
    det = providers._BROKER_OFFSET_AUTODETECT
    real_now = _time.time()
    # Summer: server UTC+3.
    for i in range(4):
        det.note_broker_epoch(real_now + 10800.0 + i * 1.0)
    assert _offset_now() == 180
    # DST switch: server clock falls back to UTC+2 while wall time advanced.
    later = _time.time()
    for i in range(4):
        det.note_broker_epoch(later + 7200.0 + i * 1.0)
    assert _offset_now() == 120


def test_garbage_and_backwards_epochs_are_ignored() -> None:
    det = providers._BROKER_OFFSET_AUTODETECT
    det.note_broker_epoch(None)
    det.note_broker_epoch(float("nan"))
    det.note_broker_epoch(-5)
    det.note_broker_epoch(1000.0)
    det.note_broker_epoch(500.0)  # backwards
    assert _offset_now() == providers.BROKER_SERVER_UTC_OFFSET_MINUTES


def test_detected_offset_helper_still_pure_and_clamped() -> None:
    ref = datetime.now(UTC)
    minutes = providers.detect_server_utc_offset_minutes(ref, ref.timestamp() + 7200)
    assert minutes == 120
    absurd = providers.detect_server_utc_offset_minutes(ref, ref.timestamp() + 90 * 86400)
    assert absurd == 1440  # clamped to the env-override validation range
