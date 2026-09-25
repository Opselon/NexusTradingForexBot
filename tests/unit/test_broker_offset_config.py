"""Unit tests for the broker server-UTC-offset configuration (audit G1).

Covers:
  - default 180 with no env var set,
  - valid env override (NSE_BROKER_SERVER_UTC_OFFSET_MINUTES),
  - invalid env override (falls back to 180, no crash),
  - boundary values 0 and 1440,
  - pure detection helper (incl. EET GMT+2 winter / EEST GMT+3 summer),
  - classify_offset_mismatch OK / MISMATCH,
  - downstream consumers (broker_history, timebase) follow the env override.

xdist-safe: all env manipulation goes through pytest monkeypatch (per-test),
no global state is mutated.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.database.broker_history import _epoch_utc
from nexus_scalp.adapters.mt5 import providers
from nexus_scalp.adapters.mt5.providers import (
    BROKER_SERVER_UTC_OFFSET_MINUTES,
    broker_epoch_to_utc,
    classify_offset_mismatch,
    detect_server_utc_offset_minutes,
    get_broker_server_utc_offset_minutes,
)
from nexus_scalp.incidents.timebase import timebase_event_chain

ENV_VAR = "NSE_BROKER_SERVER_UTC_OFFSET_MINUTES"


def _server_epoch_for_utc(dt_utc: datetime, offset_minutes: int) -> int:
    """Server-local epoch an MT5 terminal would report for a real UTC instant.

    MT5 epochs are seconds since the epoch in SERVER-LOCAL time: a wall
    clock AHEAD of UTC by `offset` produces an epoch AHEAD of the true
    UTC epoch by the same amount (epoch_reported = true_utc_epoch + offset).
    """
    return int((dt_utc + timedelta(seconds=offset_minutes * 60)).timestamp())


class TestDefaultWithoutEnv:
    def test_default_is_180_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENV_VAR, raising=False)
        assert get_broker_server_utc_offset_minutes() == 180

    def test_default_equals_module_constant(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENV_VAR, raising=False)
        assert get_broker_server_utc_offset_minutes() == BROKER_SERVER_UTC_OFFSET_MINUTES
        assert BROKER_SERVER_UTC_OFFSET_MINUTES == 180


class TestEnvOverride:
    def test_valid_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_VAR, "120")
        assert get_broker_server_utc_offset_minutes() == 120

    def test_env_read_live_each_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENV_VAR, raising=False)
        assert get_broker_server_utc_offset_minutes() == 180
        monkeypatch.setenv(ENV_VAR, "60")
        assert get_broker_server_utc_offset_minutes() == 60

    @pytest.mark.parametrize("bad", ["abc", "3.5", "", "-1", "1441", "999999"])
    def test_invalid_env_falls_back_without_crash(
        self, monkeypatch: pytest.MonkeyPatch, bad: str
    ) -> None:
        monkeypatch.setenv(ENV_VAR, bad)
        assert get_broker_server_utc_offset_minutes() == 180

    def test_invalid_env_logs_single_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # xdist-safe logging assertion (project convention: monkeypatch the
        # module logger instead of caplog, per BUG-112/118).
        monkeypatch.setenv(ENV_VAR, "not-a-number")
        emitted: list[object] = []

        class _Probe:
            def warning(self, *args: object, **kwargs: object) -> None:
                emitted.append(args)

            def error(self, *args: object, **kwargs: object) -> None:
                emitted.append(args)

        monkeypatch.setattr(providers, "_logger", _Probe())
        assert get_broker_server_utc_offset_minutes() == 180
        assert len(emitted) == 1


class TestBoundaryValues:
    def test_zero_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_VAR, "0")
        assert get_broker_server_utc_offset_minutes() == 0

    def test_1440_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_VAR, "1440")
        assert get_broker_server_utc_offset_minutes() == 1440

    def test_minus_one_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_VAR, "-1")
        assert get_broker_server_utc_offset_minutes() == 180

    def test_1441_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_VAR, "1441")
        assert get_broker_server_utc_offset_minutes() == 180


class TestBrokerEpochConversion:
    def test_broker_epoch_to_utc_uses_configured_offset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_VAR, "120")
        real_utc = datetime(2026, 8, 18, 22, 55, 2, tzinfo=UTC)
        server_epoch = _server_epoch_for_utc(real_utc, 120)
        got = broker_epoch_to_utc(server_epoch)
        assert got is not None
        assert got == real_utc

    def test_broker_epoch_to_utc_none_on_garbage(self) -> None:
        assert broker_epoch_to_utc(None) is None
        assert broker_epoch_to_utc(float("nan")) is None  # type: ignore[arg-type]


class TestDetectServerUtcOffsetMinutes:
    def test_detected_matches_configured_offset(self) -> None:
        real_utc = datetime(2026, 8, 18, 22, 55, 2, tzinfo=UTC)
        server_epoch = _server_epoch_for_utc(real_utc, 180)
        assert detect_server_utc_offset_minutes(real_utc, server_epoch) == 180

    def test_gmt2_winter_eet(self) -> None:
        # EET winter (GMT+2): server wall clock reads 00:55:02 on Jan 16 when
        # real UTC is 22:55:02 on Jan 15. Server-local epoch = epoch of the
        # server wall clock interpreted as UTC, minus the 2h zone shift.
        real_utc = datetime(2026, 1, 15, 22, 55, 2, tzinfo=UTC)
        # Server wall clock reads 00:55:02 (GMT+2); MT5 reports that wall
        # clock as if UTC: epoch(00:55:02 as UTC) = true_utc_epoch + 2h.
        server_epoch = int(datetime(2026, 1, 16, 0, 55, 2, tzinfo=UTC).timestamp())
        assert detect_server_utc_offset_minutes(real_utc, server_epoch) == 120

    def test_gmt3_summer_eest(self) -> None:
        # EEST summer (GMT+3, the verified broker setting): server wall clock
        # reads 01:55:02 on Jun 16 when real UTC is 22:55:02 on Jun 15.
        real_utc = datetime(2026, 6, 15, 22, 55, 2, tzinfo=UTC)
        # Server wall clock reads 01:55:02 (GMT+3, verified broker setting):
        # epoch(01:55:02 as UTC) = true_utc_epoch + 3h.
        server_epoch = int(datetime(2026, 6, 16, 1, 55, 2, tzinfo=UTC).timestamp())
        assert detect_server_utc_offset_minutes(real_utc, server_epoch) == 180

    def test_result_clamped_to_valid_range(self) -> None:
        real_utc = datetime(2026, 6, 15, 22, 55, 2, tzinfo=UTC)
        assert detect_server_utc_offset_minutes(real_utc, -10_000_000_000) == 0
        assert detect_server_utc_offset_minutes(real_utc, 10_000_000_000) == 1440


class TestClassifyOffsetMismatch:
    def test_ok_exact(self) -> None:
        assert classify_offset_mismatch(180, 180) == "OK"

    def test_ok_within_default_tolerance(self) -> None:
        assert classify_offset_mismatch(180, 150) == "OK"
        assert classify_offset_mismatch(180, 210) == "OK"

    def test_boundary_equals_tolerance_is_ok(self) -> None:
        assert classify_offset_mismatch(180, 240) == "OK"
        assert classify_offset_mismatch(180, 120) == "OK"

    def test_mismatch_beyond_default_tolerance(self) -> None:
        assert classify_offset_mismatch(180, 241) == "MISMATCH"
        assert classify_offset_mismatch(180, 119) == "MISMATCH"

    def test_explicit_tolerance(self) -> None:
        assert classify_offset_mismatch(120, 180, tolerance_minutes=30) == "MISMATCH"
        assert classify_offset_mismatch(120, 180, tolerance_minutes=90) == "OK"


class TestConsumersFollowEnv:
    def test_broker_history_epoch_utc_respects_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_VAR, "60")
        real_utc = datetime(2026, 8, 18, 22, 55, 2, tzinfo=UTC)
        server_epoch = _server_epoch_for_utc(real_utc, 60)
        got = _epoch_utc(server_epoch)
        assert got is not None
        assert got == real_utc

    def test_timebase_event_chain_uses_configured_offset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_VAR, "120")
        chain = timebase_event_chain(
            broker={"exit_time": "2026-08-19T01:55:02+00:00"},
            ledger={"close_time": "2026-08-18T23:55:02+00:00"},
        )
        assert "subtract 120 min" in str(chain.get("normalization_rule", ""))
        assert chain.get("pre_fix_raw_value") == "2026-08-18T23:55:02+00:00"


class TestNoGlobalStateLeak:
    def test_no_leak_after_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_VAR, "90")
        assert get_broker_server_utc_offset_minutes() == 90
        monkeypatch.undo()
        monkeypatch.delenv(ENV_VAR, raising=False)
        assert get_broker_server_utc_offset_minutes() == 180
        assert BROKER_SERVER_UTC_OFFSET_MINUTES == 180
