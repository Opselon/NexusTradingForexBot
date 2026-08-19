"""Timebase forensics (TASK-13 STEP-07).

Read-only probe comparing host UTC, Python UTC, DB now, and broker-deal
timestamps to determine whether TIMEBASE_DIVERGENCE is real and where it
matters (spec 22/23/24).

Classifies the drift (spec 24):
    DISPLAY_ONLY / UTC_OFFSET_BUG / NAIVE_DATETIME /
    BROKER_TIME_MISINTERPRETATION / SECONDS_VS_MILLISECONDS / DST_ERROR /
    PERSISTENCE_ERROR / HISTORY_QUERY_ERROR / MATCHING_ERROR / OTHER

Never writes trading data. Produces artifacts/forensics/timebase_probe.json.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.incidents.timebase")


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


class TimebaseProbe:
    """Read-only timebase comparison across host/DB/broker evidence."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def run(self) -> dict[str, Any]:
        host_now = datetime.now(UTC)
        db_now = self._db_now()
        broker_offsets, _deal_offsets = self._broker_evidence(host_now)
        log_ts = self._latest_log_timestamp()

        # Measured offsets (host as the anchor)
        host_mt5 = median(broker_offsets) if broker_offsets else None
        host_db = (db_now - host_now).total_seconds() if db_now else None
        host_log = (log_ts - host_now).total_seconds() if log_ts else None
        mt5_db = (
            (db_now - host_now).total_seconds() - host_mt5
            if host_mt5 is not None and db_now
            else None
        )

        classification = self._classify(host_mt5, host_db, host_log)
        affected = self._affected_subsystems(host_mt5, host_db, host_log)

        return {
            "probed_at": host_now.isoformat(),
            "host_now_utc": host_now.isoformat(),
            "python_now_utc": datetime.now(UTC).isoformat(),
            "db_now": db_now.isoformat() if db_now else None,
            "latest_log_time": log_ts.isoformat() if log_ts else None,
            "broker_deal_samples": len(broker_offsets),
            "measured_offsets_seconds": {
                "host_to_broker_median": round(host_mt5, 1) if host_mt5 is not None else None,
                "host_to_db": round(host_db, 1) if host_db is not None else None,
                "host_to_log": round(host_log, 1) if host_log is not None else None,
                "broker_to_db": round(mt5_db, 1) if mt5_db is not None else None,
            },
            "classification": classification,
            "affected_subsystems": affected,
            "note": "MT5 epochs are SERVER-LOCAL (BUG-070). Skew is measured, never assumed.",
        }

    # ------------------------------------------------------------------

    def _db_now(self) -> datetime | None:
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            try:
                row = conn.execute("SELECT datetime('now')").fetchone()
                if not row:
                    return None
                return datetime.fromisoformat(str(row[0]) + "+00:00")
            finally:
                conn.close()
        except sqlite3.Error:
            return None

    def _broker_evidence(self, host_now: datetime) -> tuple[list[float], list[float]]:
        """Offsets (seconds) of broker-deal timestamps vs host UTC.

        LIVE window = rows synced within 12h (active session evidence);
        BACKFILL = everything else (historical reconstruction batches).
        A large backfill offset is sync-lag, NOT a live clock bug.
        """
        offsets: list[float] = []
        deal_offsets: list[float] = []
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT entry_time, exit_time, synced_at FROM audit_broker_trades "
                    "WHERE exit_time != '' AND synced_at >= datetime('now', '-12 hours') "
                    "ORDER BY exit_time DESC LIMIT 2000"
                ).fetchall()
                for r in rows:
                    for col in ("entry_time", "exit_time"):
                        dt = _parse_ts(str(r[col] or ""))
                        if dt is not None:
                            offsets.append((dt - host_now).total_seconds())
                    sync = _parse_ts(str(r["synced_at"] or ""))
                    if sync is not None:
                        deal_offsets.append((sync - host_now).total_seconds())
            finally:
                conn.close()
        except sqlite3.Error:
            pass
        return offsets, deal_offsets

    def _latest_log_timestamp(self) -> datetime | None:
        """Latest structured log file mtime (best-effort, read-only)."""
        import glob

        for pattern in (
            "artifacts/logs/*.log",
            "artifacts/logs/*.jsonl",
        ):
            files = sorted(glob.glob(pattern), key=lambda p: Path(p).stat().st_mtime, reverse=True)
            if files:
                return datetime.fromtimestamp(Path(files[0]).stat().st_mtime, UTC)
        return None

    # ------------------------------------------------------------------

    def _classify(
        self,
        host_mt5: float | None,
        host_db: float | None,
        host_log: float | None,
    ) -> str:
        """Classification (spec 24) — evidence-driven.

        The DB clock is the canonical UTC anchor (host_to_db ~0). A large
        broker-offset with a healthy DB clock is HISTORY_QUERY_ERROR
        (bulk sync lag / backfill), not a live timebase bug.
        """
        if host_mt5 is None:
            return "OTHER"  # no live broker evidence available
        if host_db is not None and abs(host_db) > 300:
            return "PERSISTENCE_ERROR"
        if abs(host_mt5) > 3600:
            return "HISTORY_QUERY_ERROR"  # sync-lag / backfill, not live skew
        if abs(host_mt5) > 300:
            return "BROKER_TIME_MISINTERPRETATION"
        return "DISPLAY_ONLY"

    def _affected_subsystems(
        self,
        host_mt5: float | None,
        host_db: float | None,
        host_log: float | None,
    ) -> list[str]:
        """Which subsystems the drift affects (spec 24)."""
        affected: list[str] = []
        if host_mt5 is not None and abs(host_mt5) > 300:
            affected += ["history sync", "reconciliation", "incident timestamps"]
        if host_db is not None and abs(host_db) > 300:
            affected += ["persistence", "UI timestamps"]
        if host_log is not None and abs(host_log or 0) > 300:
            affected += ["log forensics"]
        if not affected:
            affected = ["none — within bounds"]
        return affected


def build_timebase_probe(db_path: str, out_path: str | Path) -> dict[str, Any]:
    """Runs the probe and writes artifacts/forensics/timebase_probe.json."""
    probe = TimebaseProbe(db_path)
    result = probe.run()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


__all__ = [
    "TimebaseProbe",
    "build_timebase_probe",
]
