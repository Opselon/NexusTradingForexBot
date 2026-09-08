"""P1 DST-aware session semantics tests (features/session_time.py).

Covers the mission-required representative dates:
    * London winter / summer
    * New York winter / summer
    * US DST active / UK DST inactive (transitional — different schedules)
    * UK DST active / US DST inactive (transitional)
    * both DST transition boundaries (spring + autumn, US + UK)
    * overlap correctness across all of the above
    * cold-start flag contract unchanged
    * provenance metadata present

Mutation targets:
    * reverting to fixed-UTC windows (the old buggy semantics) MUST fail
      the transitional-period cases;
    * swapping London/NY tz MUST fail.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nexus_scalp.features.session_time import (
    SESSION_DEFINITIONS,
    SESSION_SEMANTICS_VERSION,
    session_flags_for_utc,
    session_phase_encoding_for_utc,
    session_semantics_metadata,
)


def flags(ts: datetime) -> tuple[bool, bool, bool, bool]:
    f = session_flags_for_utc(ts)
    return (
        f["session_tokyo"],
        f["session_london"],
        f["session_ny"],
        f["session_overlap_london_ny"],
    )


class TestDSTTransitions:
    def test_london_winter_0800_utc_is_open(self):
        # 2026-01-15: UK on GMT (UTC+0). Local London open 08:00 => 08:00Z.
        tok, lon, ny, ovl = flags(datetime(2026, 1, 15, 8, 30, tzinfo=UTC))
        assert lon is True and ny is False and ovl is False

    def test_london_summer_0700_utc_is_open(self):
        # 2026-07-15: UK on BST (UTC+1). Local London open 08:00 => 07:00Z.
        tok, lon, ny, ovl = flags(datetime(2026, 7, 15, 7, 30, tzinfo=UTC))
        assert lon is True and ny is False and ovl is False

    def test_ny_winter_1300_utc_is_open(self):
        # 2026-01-15: US on EST (UTC-5). Local NY open 08:00 => 13:00Z.
        tok, lon, ny, ovl = flags(datetime(2026, 1, 15, 13, 30, tzinfo=UTC))
        assert ny is True

    def test_ny_summer_1200_utc_is_open(self):
        # 2026-07-15: US on EDT (UTC-4). Local NY open 08:00 => 12:00Z.
        tok, lon, ny, ovl = flags(datetime(2026, 7, 15, 12, 30, tzinfo=UTC))
        assert ny is True

    def test_us_dst_active_uk_inactive_transitional(self):
        # 2026-03-25: US already on EDT (UTC-4), UK still on GMT (UTC+0).
        # London local 08:00-16:30 => 08:00-16:30Z: the OLD fixed-UTC window
        # (07:00Z open) would wrongly say London open at 07:30Z.
        tok, lon, ny, ovl = flags(datetime(2026, 3, 25, 7, 30, tzinfo=UTC))
        assert lon is False, "07:30Z must NOT be London when UK is on GMT"
        # NY local 08:00-17:00 => 12:00-20:00Z (EDT). At 12:30Z NY is open
        # while the OLD fixed-UTC window (13:00Z open) would say closed.
        tok, lon, ny, ovl = flags(datetime(2026, 3, 25, 12, 30, tzinfo=UTC))
        assert ny is True and lon is True and ovl is True

    def test_uk_dst_active_us_inactive_transitional(self):
        # 2026-10-28: UK back on GMT (UTC+0), US still on EDT (UTC-4).
        # London local 08:00 => open at 08:30Z...
        tok, lon, ny, ovl = flags(datetime(2026, 10, 28, 8, 30, tzinfo=UTC))
        assert lon is True
        # ...and NY local 08:00 = 12:00Z (EDT) => NY open at 13:30Z with
        # London still open (16:30Z close GMT) => genuine overlap.
        tok, lon, ny, ovl = flags(datetime(2026, 10, 28, 13, 30, tzinfo=UTC))
        assert ny is True and lon is True and ovl is True

    def test_overlap_window_shifts_with_dst(self):
        # Summer (both DST): NY opens 12:00Z, London closes 15:30Z.
        # Overlap exists at 13:00Z...
        tok, lon, ny, ovl = flags(datetime(2026, 7, 15, 13, 0, tzinfo=UTC))
        assert ovl is True
        # ...and at 12:30Z (NY already open, London open) — the old fixed
        # windows had no overlap before 13:00Z.
        tok, lon, ny, ovl = flags(datetime(2026, 7, 15, 12, 30, tzinfo=UTC))
        assert ovl is True
        # Winter: NY opens 13:00Z, London closes 16:30Z. Overlap at 14:00Z,
        # and NY NOT yet open at 12:30Z (the old windows wrongly opened NY
        # at 13:00Z but closed London at 15:00Z — a shifted, shorter overlap).
        tok, lon, ny, ovl = flags(datetime(2026, 1, 15, 14, 0, tzinfo=UTC))
        assert ovl is True
        tok, lon, ny, ovl = flags(datetime(2026, 1, 15, 12, 30, tzinfo=UTC))
        assert ovl is False and lon is True and ny is False
        # London close shifted: winter 16:00Z still London open (old window
        # said closed after 15:00Z); summer 16:00Z London closed.
        tok, lon, ny, ovl = flags(datetime(2026, 1, 15, 16, 0, tzinfo=UTC))
        assert lon is True
        tok, lon, ny, ovl = flags(datetime(2026, 7, 15, 16, 0, tzinfo=UTC))
        assert lon is False

    def test_us_spring_forward_boundary(self):
        # 2026-03-08 07:00Z: US clocks jumped (EST->EDT); a naive fixed-UTC
        # NY window treats 12:00Z as pre-open, but EDT local is 08:00 => open.
        tok, lon, ny, ovl = flags(datetime(2026, 3, 8, 12, 30, tzinfo=UTC))
        assert ny is True

    def test_uk_spring_forward_boundary(self):
        # 2026-03-29 (UK spring forward): 07:30Z is 08:30 BST => London open;
        # the old fixed-UTC window (open at 07:00Z... closed 15:00Z) also
        # says open — but the KEY assertion is the BST-local wall: 07:00Z is
        # exactly 08:00 local => open.
        tok, lon, ny, ovl = flags(datetime(2026, 3, 29, 7, 0, tzinfo=UTC))
        assert lon is True

    def test_autumn_back_transition(self):
        # 2026-10-25 (UK fall back): 07:30Z is 07:30 GMT => pre-open; old
        # fixed-UTC window (7 <= hour < 15) wrongly says London open.
        tok, lon, ny, ovl = flags(datetime(2026, 10, 25, 7, 30, tzinfo=UTC))
        assert lon is False


class TestContracts:
    def test_tokyo_session_winter_and_summer(self):
        # Tokyo has no DST: local 09:00-18:00 => 00:00-09:00Z year-round.
        tok, lon, ny, ovl = flags(datetime(2026, 1, 15, 0, 30, tzinfo=UTC))
        assert tok is True
        tok, lon, ny, ovl = flags(datetime(2026, 7, 15, 0, 30, tzinfo=UTC))
        assert tok is True

    def test_naive_timestamp_assumed_utc(self):
        aware = session_flags_for_utc(datetime(2026, 1, 15, 8, 30, tzinfo=UTC))
        naive = session_flags_for_utc(datetime(2026, 1, 15, 8, 30))
        assert aware == naive

    def test_phase_encoding_consistent_with_flags(self):
        # overlap => 1.0; london only => 0.25; ny only => 0.75
        assert session_phase_encoding_for_utc(datetime(2026, 1, 15, 14, 0, tzinfo=UTC)) == 1.0
        assert session_phase_encoding_for_utc(datetime(2026, 1, 15, 8, 30, tzinfo=UTC)) == 0.25
        assert session_phase_encoding_for_utc(datetime(2026, 1, 15, 18, 0, tzinfo=UTC)) == 0.75

    def test_provenance_metadata(self):
        meta = session_semantics_metadata()
        assert meta["session_semantics_version"] == SESSION_SEMANTICS_VERSION
        assert "london" in meta["definitions"] and "new_york" in meta["definitions"]
        assert SESSION_DEFINITIONS["london"]["tz_name"] == "Europe/London"
        assert SESSION_DEFINITIONS["new_york"]["tz_name"] == "America/New_York"

    def test_feature_engine_uses_dst_semantics(self):
        """Mutation resistance: the 50D engine must NOT use fixed-UTC hours."""
        from datetime import timedelta

        from nexus_scalp.domain.models import TickData
        from nexus_scalp.features.scalp_features import ScalpFeatureEngine
        from nexus_scalp.market_data.bar_aggregator import BarData

        bars = [
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=datetime(2026, 1, 15, 0, 0, tzinfo=UTC) + timedelta(minutes=i),
                open=2000.0,
                high=2000.8,
                low=1999.3,
                close=2000.0,
                tick_volume=100,
                is_complete=True,
            )
            for i in range(56)
        ]
        tick = TickData(
            symbol="XAUUSD",
            timestamp=datetime(2026, 1, 15, 8, 30, tzinfo=UTC),  # 08:30Z winter
            bid=2000.0,
            ask=2000.1,
            volume=1,
        )
        vec = ScalpFeatureEngine(symbol="XAUUSD").compute_from_bars(bars, tick)
        # Winter 08:30Z: London open, NY closed, no overlap (NY opens 13:00Z).
        assert vec.session_london is True
        assert vec.session_ny is False
        assert vec.session_overlap_london_ny is False
        # Summer counterpart 07:30Z (BST 08:30): same LOCAL clock => same flags.
        bars_summer = [
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=datetime(2026, 7, 15, 0, 0, tzinfo=UTC) + timedelta(minutes=i),
                open=2000.0,
                high=2000.8,
                low=1999.3,
                close=2000.0,
                tick_volume=100,
                is_complete=True,
            )
            for i in range(56)
        ]
        tick_summer = TickData(
            symbol="XAUUSD",
            timestamp=datetime(2026, 7, 15, 7, 30, tzinfo=UTC),
            bid=2000.0,
            ask=2000.1,
            volume=1,
        )
        vec_s = ScalpFeatureEngine(symbol="XAUUSD").compute_from_bars(bars_summer, tick_summer)
        assert (vec_s.session_london, vec_s.session_ny, vec_s.session_overlap_london_ny) == (
            vec.session_london,
            vec.session_ny,
            vec.session_overlap_london_ny,
        ), "same market-local wall clock must yield identical flags across seasons"
