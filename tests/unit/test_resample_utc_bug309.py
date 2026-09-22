"""BUG-309 regression: NameError crash in is_current_bar_forming boot path.

resample.py used ``datetime.now(UTC)`` without importing ``UTC``; every
cold-start warmup that called ``get_historical_bars`` (live MT5, remote
gateway, paper adapter) died with ``NameError: name 'UTC' is not defined``
before the engine could serve. These tests pin the function's contract AND
the import-correctness of the module so this class of crash cannot return.
"""

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.indicators.resample import is_current_bar_forming


def _utc(y: int, m: int, d: int, h: int, minute: int = 0) -> datetime:
    return datetime(y, m, d, h, minute, tzinfo=UTC)


class TestIsCurrentBarFormingContract:
    """Behavioral contract of the forming/sealed classifier."""

    def test_current_bar_is_forming_when_now_inside_bucket(self) -> None:
        bar_open = _utc(2026, 9, 21, 6)
        now = bar_open + timedelta(minutes=30)  # inside the H1 bucket
        assert is_current_bar_forming(bar_open, "H1", now=now) is True

    def test_completed_bar_is_not_forming_after_close_boundary(self) -> None:
        bar_open = _utc(2026, 9, 21, 6)
        now = bar_open + timedelta(minutes=60)  # exactly at the H1 close
        assert is_current_bar_forming(bar_open, "H1", now=now) is False

    def test_past_bar_is_complete(self) -> None:
        bar_open = _utc(2026, 9, 21, 5)
        now = _utc(2026, 9, 21, 6, 30)
        assert is_current_bar_forming(bar_open, "H1", now=now) is False

    def test_unmapped_timeframe_returns_false(self) -> None:
        bar_open = _utc(2026, 9, 21, 6)
        assert is_current_bar_forming(bar_open, "W1", now=bar_open) is False
        assert is_current_bar_forming(bar_open, "MN1", now=bar_open) is False

    @pytest.mark.parametrize("tf", ["M1", "M5", "M15", "M30", "H1", "H4"])
    def test_default_now_call_does_not_raise(self, tf: str) -> None:
        """The live crash: the default-clock path executed datetime.now(UTC)
        with no UTC binding. Must execute and return a bool for every TF."""
        # a bar opened one bucket ago on the real clock is never forming
        now = datetime.now(UTC)
        minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}[tf]
        bar_open = now - timedelta(minutes=2 * minutes)
        result = is_current_bar_forming(bar_open, tf)
        assert result is False

    def test_forming_bar_halfway_through_bucket(self) -> None:
        bar_open = _utc(2026, 9, 21, 6)
        now = bar_open + timedelta(minutes=59, seconds=59)
        assert is_current_bar_forming(bar_open, "H1", now=now) is True


class TestUtcImportSafety:
    """Guard against the exact NameError class that killed boot (BUG-309)."""

    def test_module_binds_utc(self) -> None:
        import nexus_scalp.indicators.resample as mod

        assert hasattr(mod, "UTC"), (
            "resample.py uses datetime.now(UTC) but no longer imports UTC — "
            "this is the BUG-309 boot crash returning"
        )

    def test_default_clock_path_executes_without_name_error(self) -> None:
        """Call WITHOUT now= to force the datetime.now(UTC) branch."""
        recent = datetime.now(UTC)
        # recent bucket is forming; a year-old bar is sealed
        assert is_current_bar_forming(recent, "H1") is True
        assert is_current_bar_forming(recent - timedelta(days=365), "H1") is False
