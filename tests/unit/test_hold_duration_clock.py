"""Offline deterministic tests for HoldDurationClock (TASK-HOLD-CLOCK).

Time is fully injected: ``hold_duration.monotonic_now`` and
``hold_duration._utc_now`` are monkeypatched onto the module surface, so no
test sleeps and no real clock is consulted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.execution import hold_duration
from nexus_scalp.execution.hold_duration import HoldDurationClock

T0 = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    """Simulated monotonic + wall clocks, each advanced independently."""

    def __init__(self, *, monotonic_start: float = 1_000_000.0, wall_start: datetime = T0) -> None:
        self.monotonic = monotonic_start
        self.wall = wall_start

    def advance_monotonic(self, sec: float) -> None:
        self.monotonic += sec

    def shift_wall(self, sec: float) -> None:
        self.wall = self.wall + timedelta(seconds=sec)

    def patch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hold_duration, "monotonic_now", lambda: self.monotonic)
        monkeypatch.setattr(hold_duration, "_utc_now", lambda: self.wall)


def make_clock(monkeypatch: pytest.MonkeyPatch) -> tuple[HoldDurationClock, FakeClock]:
    fake = FakeClock()
    fake.patch(monkeypatch)
    return HoldDurationClock(), fake


BROKER_ENTRY = T0  # tz-aware UTC; offset domain is irrelevant to monotonic math


def test_register_returns_zero_elapsed(monkeypatch: pytest.MonkeyPatch) -> None:
    clk, fake = make_clock(monkeypatch)
    fake.advance_monotonic(500.0)  # process already up a while before the trade
    assert clk.register(111, broker_entry_utc=BROKER_ENTRY) == 0.0


def test_monotonic_elapsed_grows_while_wall_clock_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Primary duration follows the monotonic basis, not the wall clock."""
    clk, fake = make_clock(monkeypatch)
    clk.register(222, broker_entry_utc=BROKER_ENTRY)
    fake.advance_monotonic(90.0)  # holding time passes; wall clock frozen
    elapsed = clk.elapsed_sec(222)
    assert elapsed is not None
    assert elapsed == pytest.approx(90.0)
    fake.advance_monotonic(30.0)
    elapsed = clk.elapsed_sec(222)
    assert elapsed is not None
    assert elapsed == pytest.approx(120.0)


def test_wall_clock_jump_backward_does_not_change_elapsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DST fallback / clock rollback must not corrupt the primary duration."""
    clk, fake = make_clock(monkeypatch)
    clk.register(333, broker_entry_utc=BROKER_ENTRY)
    fake.advance_monotonic(600.0)
    before = clk.elapsed_sec(333)
    fake.shift_wall(-3600.0)  # -1h DST-style rollback
    assert clk.elapsed_sec(333) == before


def test_wall_clock_jump_forward_does_not_change_elapsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clk, fake = make_clock(monkeypatch)
    clk.register(334, broker_entry_utc=BROKER_ENTRY)
    fake.advance_monotonic(600.0)
    before = clk.elapsed_sec(334)
    fake.shift_wall(3600.0)  # +1h DST-style jump
    assert clk.elapsed_sec(334) == before


def test_sanity_ok_when_clocks_agree(monkeypatch: pytest.MonkeyPatch) -> None:
    clk, fake = make_clock(monkeypatch)
    clk.register(444, broker_entry_utc=BROKER_ENTRY)
    fake.advance_monotonic(120.0)
    fake.shift_wall(120.0)  # wall moves in lockstep with monotonic
    report = clk.sanity(444, broker_now_utc=BROKER_ENTRY + timedelta(seconds=120))
    assert report["status"] == "OK"
    assert report["monotonic_sec"] == pytest.approx(120.0)
    assert report["wallclock_sec"] == pytest.approx(120.0)
    assert report["broker_clock_sec"] == pytest.approx(120.0)
    assert report["divergence_sec"] == pytest.approx(0.0)


def test_sanity_suspect_on_large_wall_divergence(monkeypatch: pytest.MonkeyPatch) -> None:
    clk, fake = make_clock(monkeypatch)
    clk.register(445, broker_entry_utc=BROKER_ENTRY)
    fake.advance_monotonic(120.0)
    fake.shift_wall(3600.0)  # +1h wall jump: DST/NTP-class divergence
    report = clk.sanity(445)
    assert report["status"] == "SUSPECT"
    assert report["monotonic_sec"] == pytest.approx(120.0)
    # wall anchor was T0; fake wall now reads T0+3600 => 3600s wall duration
    assert report["wallclock_sec"] == pytest.approx(3600.0)
    assert report["divergence_sec"] == pytest.approx(3480.0)


def test_sanity_missing_for_unknown_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    clk, _fake = make_clock(monkeypatch)
    report = clk.sanity(999999)
    assert report == {
        "status": "MISSING",
        "monotonic_sec": None,
        "wallclock_sec": None,
        "broker_clock_sec": None,
        "divergence_sec": None,
    }


def test_sanity_missing_never_fabricates_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    clk, _fake = make_clock(monkeypatch)
    report = clk.sanity(988, broker_now_utc=T0 + timedelta(hours=3))
    for key in ("monotonic_sec", "wallclock_sec", "broker_clock_sec", "divergence_sec"):
        assert report[key] is None


def test_broker_clock_divergence_flags_suspect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Broker-stamped domain far off from monotonic must be surfaced."""
    clk, fake = make_clock(monkeypatch)
    clk.register(446, broker_entry_utc=BROKER_ENTRY)
    fake.advance_monotonic(60.0)
    fake.shift_wall(60.0)  # wall agrees; only the broker domain is skewed
    report = clk.sanity(446, broker_now_utc=BROKER_ENTRY + timedelta(seconds=3900))
    assert report["status"] == "SUSPECT"
    assert report["broker_clock_sec"] == pytest.approx(3900.0)
    assert report["divergence_sec"] == pytest.approx(3840.0)


def test_sanity_within_tolerance_is_ok_despite_small_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clk, fake = make_clock(monkeypatch)
    clk.register(447, broker_entry_utc=BROKER_ENTRY)
    fake.advance_monotonic(120.0)
    fake.shift_wall(150.0)  # 30s drift < max(60, 2% of 150) tolerance
    assert clk.sanity(447)["status"] == "OK"


def test_expire_removes_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    clk, fake = make_clock(monkeypatch)
    clk.register(555, broker_entry_utc=BROKER_ENTRY)
    fake.advance_monotonic(10.0)
    assert clk.elapsed_sec(555) == pytest.approx(10.0)
    clk.expire(555)
    assert clk.elapsed_sec(555) is None
    clk.expire(555)  # idempotent cleanup


def test_elapsed_sec_none_for_unregistered_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    clk, _fake = make_clock(monkeypatch)
    assert clk.elapsed_sec(1_234_567) is None


def test_monotonic_regression_is_clamped_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monotonic clocks are non-decreasing; a regression is never trusted."""
    clk, fake = make_clock(monkeypatch)
    clk.register(666, broker_entry_utc=BROKER_ENTRY)
    fake.advance_monotonic(100.0)
    assert clk.elapsed_sec(666) == pytest.approx(100.0)
    fake.monotonic -= 250.0  # simulated regression (e.g. exotic platform bug)
    assert clk.elapsed_sec(666) == 0.0


def test_sanity_monotonic_regression_clamped_not_suspect_by_sign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clamped zero duration is reported, not a negative number."""
    clk, fake = make_clock(monkeypatch)
    clk.register(667, broker_entry_utc=BROKER_ENTRY)
    fake.monotonic -= 1.0  # regression right after registration
    report = clk.sanity(667)
    assert report["monotonic_sec"] == 0.0


def test_rejects_naive_datetimes(monkeypatch: pytest.MonkeyPatch) -> None:
    clk, _fake = make_clock(monkeypatch)
    naive = datetime(2026, 9, 7, 12, 0, 0)  # noqa: DTZ001 - deliberate
    with pytest.raises(ValueError, match="timezone-aware"):
        clk.register(777, broker_entry_utc=naive)
    with pytest.raises(ValueError, match="timezone-aware"):
        clk.register(778, broker_entry_utc=BROKER_ENTRY, wall_entry_utc=naive)
    # sanity() only validates broker_now_utc when a broker anchor exists
    clk.register(779, broker_entry_utc=BROKER_ENTRY)
    with pytest.raises(ValueError, match="timezone-aware"):
        clk.sanity(779, broker_now_utc=naive)


def test_non_utc_offsets_are_normalized_to_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aware timestamps in other offsets are stored as tz-aware UTC."""
    from datetime import timezone

    clk, fake = make_clock(monkeypatch)
    shifted = T0.replace(tzinfo=timezone(timedelta(hours=3, minutes=30)))
    clk.register(888, broker_entry_utc=shifted)
    fake.advance_monotonic(60.0)
    fake.shift_wall(60.0)
    report = clk.sanity(888, broker_now_utc=shifted + timedelta(seconds=60))
    assert report["status"] == "OK"


def test_none_broker_entry_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Registration may lack a broker epoch (fallback path); duration still works."""
    clk, fake = make_clock(monkeypatch)
    assert clk.register(999, broker_entry_utc=None) == 0.0
    fake.advance_monotonic(45.0)
    assert clk.elapsed_sec(999) == pytest.approx(45.0)
    report = clk.sanity(999)  # no broker domain: only monotonic vs wall
    assert report["broker_clock_sec"] is None


def test_registered_wall_entry_utc_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    clk, fake = make_clock(monkeypatch)
    fake.shift_wall(-3600.0)
    clk.register(1000, broker_entry_utc=None, wall_entry_utc=T0)
    fake.advance_monotonic(300.0)
    report = clk.sanity(1000)
    assert report["status"] == "SUSPECT"  # fake wall is 1h before the T0 anchor
    assert report["monotonic_sec"] == pytest.approx(300.0)
    assert report["wallclock_sec"] == pytest.approx(0.0)
    assert report["divergence_sec"] == pytest.approx(300.0)


def test_reregistration_restarts_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    clk, fake = make_clock(monkeypatch)
    clk.register(1111, broker_entry_utc=BROKER_ENTRY)
    fake.advance_monotonic(500.0)
    assert clk.register(1111, broker_entry_utc=BROKER_ENTRY) == 0.0
    assert clk.elapsed_sec(1111) == pytest.approx(0.0)


def test_broker_now_requires_aware_datetime(monkeypatch: pytest.MonkeyPatch) -> None:
    clk, _fake = make_clock(monkeypatch)
    clk.register(1212, broker_entry_utc=BROKER_ENTRY)
    with pytest.raises(ValueError, match="timezone-aware"):
        clk.sanity(1212, broker_now_utc=datetime(2026, 9, 7, 12, 0, 0))  # noqa: DTZ001
