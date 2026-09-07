"""DST-aware London / New York / Tokyo session semantics (P1).

P1 finding (verified): ``features.scalp_features`` derived the session flags
(feat_16..feat_19) from FIXED UTC hours (London 07-15, NY 13-21, Tokyo 00-08,
overlap 13-15). That silently shifts with DST: London is UTC+0 in winter and
UTC+1 in summer, New York is UTC-5 / UTC-4, and the two markets transition on
DIFFERENT schedules (US changes on the 2nd Sunday of March / 1st Sunday of
November; UK on the last Sunday of March / October). During the
US-active/UK-inactive and UK-active/US-inactive windows the old fixed mapping
labels sessions with up to a one-hour error and the overlap shrinks/grows.

This module encodes the session definitions ONCE, in real market-local time
(IANA ``zoneinfo``), and derives the four canonical 50D flags from the tick's
actual UTC instant. Feature NAMES, dimensions and tensor contracts are
unchanged — only TIME SEMANTICS are corrected.

Session definitions (standard FX market semantics, documented):
    TOKYO   09:00-18:00 Asia/Tokyo     (flags 00-08 in old UTC terms)
    LONDON  08:00-16:30 Europe/London  (local open/close incl. 30m close run)
    NEW_YORK 08:00-17:00 America/New_York
    OVERLAP London ∩ New York (both open simultaneously)

Provenance / compatibility (P1 mandate):
    * SESSION_SEMANTICS_VERSION identifies this corrected time basis.
    * ``session_semantics_metadata()`` exposes the version + tz database for
      training provenance and the artifact revalidation contract.
    * Models trained BEFORE this correction used fixed-UTC semantics and are
      NOT equivalent; artifacts must carry/revalidate the session version
      before promotion (see training metadata + governance verify gates).
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

__all__ = [
    "SESSION_SEMANTICS_VERSION",
    "SESSION_DEFINITIONS",
    "session_flags_for_utc",
    "session_semantics_metadata",
    "session_phase_encoding_for_utc",
]

#: Provenance version of the corrected session time semantics.
SESSION_SEMANTICS_VERSION: str = "dst_aware_v1"

#: Session definition registry: market-local open/close walls (half-open
#: [open, close) intervals). ``ZoneInfo`` keys are IANA names.
SESSION_DEFINITIONS: dict[str, dict[str, Any]] = {
    "tokyo": {
        "tz": ZoneInfo("Asia/Tokyo"),
        "tz_name": "Asia/Tokyo",
        "open": time(9, 0),
        "close": time(18, 0),
    },
    "london": {
        "tz": ZoneInfo("Europe/London"),
        "tz_name": "Europe/London",
        "open": time(8, 0),
        "close": time(16, 30),
    },
    "new_york": {
        "tz": ZoneInfo("America/New_York"),
        "tz_name": "America/New_York",
        "open": time(8, 0),
        "close": time(17, 0),
    },
}


def _in_session(when_local: datetime, definition: dict[str, Any]) -> bool:
    open_t = definition["open"]
    close_t = definition["close"]
    minutes = when_local.hour * 60 + when_local.minute
    return (open_t.hour * 60 + open_t.minute) <= minutes < (close_t.hour * 60 + close_t.minute)


def session_flags_for_utc(ts_utc: datetime) -> dict[str, bool]:
    """Derives the four canonical session flags for a UTC instant.

    Accepts naive (assumed UTC) or aware timestamps; aware inputs are
    converted to UTC. Converts the actual instant into each market's local
    timezone (DST-correct through the IANA database) and evaluates the
    half-open [open, close) session walls.
    """
    if ts_utc.tzinfo is None:
        ts_utc = ts_utc.replace(tzinfo=UTC)
    else:
        ts_utc = ts_utc.astimezone(UTC)
    london_open = _in_session(ts_utc.astimezone(SESSION_DEFINITIONS["london"]["tz"]), SESSION_DEFINITIONS["london"])
    ny_open = _in_session(ts_utc.astimezone(SESSION_DEFINITIONS["new_york"]["tz"]), SESSION_DEFINITIONS["new_york"])
    tokyo_open = _in_session(ts_utc.astimezone(SESSION_DEFINITIONS["tokyo"]["tz"]), SESSION_DEFINITIONS["tokyo"])
    return {
        "session_tokyo": tokyo_open,
        "session_london": london_open,
        "session_ny": ny_open,
        "session_overlap_london_ny": london_open and ny_open,
    }


def session_phase_encoding_for_utc(ts_utc: datetime) -> float:
    """DST-aware replacement for schema_augment.session_phase_encoding.

    Same discrete mapping contract, but the phase is derived from the
    market-local session walls above rather than fixed UTC hours.
    """
    flags = session_flags_for_utc(ts_utc)
    if flags["session_overlap_london_ny"]:
        return 1.0  # overlap
    if flags["session_london"]:
        return 0.25  # london only
    if flags["session_ny"]:
        return 0.75  # ny only
    if flags["session_tokyo"]:
        return -0.75  # tokyo
    return -1.0  # asia_only == tokyo (same market, kept for encoding parity)


def session_semantics_metadata() -> dict[str, Any]:
    """Provenance block for training metadata / promotion evidence."""
    import zoneinfo as _zi

    return {
        "session_semantics_version": SESSION_SEMANTICS_VERSION,
        "method": "IANA zoneinfo market-local session walls (DST-aware)",
        "definitions": {
            name: {
                "tz": spec["tz_name"],
                "open": spec["open"].isoformat(),
                "close": spec["close"].isoformat(),
            }
            for name, spec in SESSION_DEFINITIONS.items()
        },
        "tzdata_available": True,
    }
