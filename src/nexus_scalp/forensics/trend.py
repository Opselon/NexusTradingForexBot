"""Health snapshot trend analysis (TASK-12 §34).

Read-only comparison of the current FORENSIC_HEALTH_SNAPSHOT against the
previous one: new failures, resolved failures, worsened warnings, improved
warnings, new unknowns. Never mutates anything.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nexus_scalp.forensics.engine import SnapshotRecord
from nexus_scalp.forensics.models import HealthStatus


def _by_check(rec: SnapshotRecord) -> dict[str, str]:
    return {c["check_id"]: c["status"] for c in rec.checks}


def compare_snapshots(
    current: SnapshotRecord,
    previous: SnapshotRecord | None,
) -> dict[str, Any]:
    """Classifies status transitions between two snapshots (read-only)."""
    if previous is None:
        return {
            "previous_available": False,
            "new_failures": [],
            "resolved_failures": [],
            "worsened": [],
            "improved": [],
            "new_unknowns": [],
            "resolved_unknowns": [],
        }
    cur = _by_check(current)
    prev = _by_check(previous)
    rank = {
        HealthStatus.PASS.value: 0,
        HealthStatus.WARNING.value: 1,
        HealthStatus.UNKNOWN.value: 2,
        HealthStatus.DEGRADED.value: 3,
        HealthStatus.CRITICAL.value: 4,
    }

    new_failures: list[dict[str, str]] = []
    resolved_failures: list[dict[str, str]] = []
    worsened: list[dict[str, str]] = []
    improved: list[dict[str, str]] = []
    new_unknowns: list[dict[str, str]] = []
    resolved_unknowns: list[dict[str, str]] = []

    for cid, cur_status in cur.items():
        prev_status = prev.get(cid, HealthStatus.PASS.value)  # new check = was pass? No: absent
        prev_status = prev.get(cid)
        if prev_status is None:
            # a check that did not exist before
            if cur_status in (HealthStatus.DEGRADED.value, HealthStatus.CRITICAL.value):
                new_failures.append({"check_id": cid, "from": "ABSENT", "to": cur_status})
            if cur_status == HealthStatus.UNKNOWN.value:
                new_unknowns.append({"check_id": cid, "from": "ABSENT", "to": cur_status})
            continue
        if cur_status in (
            HealthStatus.DEGRADED.value,
            HealthStatus.CRITICAL.value,
        ) and prev_status in (
            HealthStatus.PASS.value,
            HealthStatus.WARNING.value,
            HealthStatus.UNKNOWN.value,
        ):
            new_failures.append({"check_id": cid, "from": prev_status, "to": cur_status})
        if prev_status in (
            HealthStatus.DEGRADED.value,
            HealthStatus.CRITICAL.value,
        ) and cur_status in (HealthStatus.PASS.value, HealthStatus.WARNING.value):
            resolved_failures.append({"check_id": cid, "from": prev_status, "to": cur_status})
        if rank.get(cur_status, 0) > rank.get(prev_status, 0):
            worsened.append({"check_id": cid, "from": prev_status, "to": cur_status})
        elif rank.get(cur_status, 0) < rank.get(prev_status, 0):
            improved.append({"check_id": cid, "from": prev_status, "to": cur_status})
        if cur_status == HealthStatus.UNKNOWN.value and prev_status != HealthStatus.UNKNOWN.value:
            new_unknowns.append({"check_id": cid, "from": prev_status, "to": cur_status})
        if prev_status == HealthStatus.UNKNOWN.value and cur_status != HealthStatus.UNKNOWN.value:
            resolved_unknowns.append({"check_id": cid, "from": prev_status, "to": cur_status})

    return {
        "previous_available": True,
        "previous_timestamp": previous.timestamp,
        "new_failures": new_failures,
        "resolved_failures": resolved_failures,
        "worsened": worsened,
        "improved": improved,
        "new_unknowns": new_unknowns,
        "resolved_unknowns": resolved_unknowns,
    }


def load_history(history_dir: Path) -> list[SnapshotRecord]:
    """Reads the bounded history.jsonl into SnapshotRecord list (oldest first)."""
    path = history_dir / "history.jsonl"
    records: list[SnapshotRecord] = []
    if not path.exists():
        return records
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            records.append(
                SnapshotRecord(
                    timestamp=data.get("timestamp", ""),
                    overall=data.get("overall", HealthStatus.UNKNOWN.value),
                    groups=data.get("groups", {}),
                    checks=data.get("checks", []),
                    critical_count=data.get("critical_count", 0),
                    warning_count=data.get("warning_count", 0),
                    degraded_count=data.get("degraded_count", 0),
                    unknown_count=data.get("unknown_count", 0),
                    correlation_id=data.get("correlation_id", ""),
                )
            )
    except OSError:
        return records
    return records


def latest_trend(history_dir: Path) -> dict[str, Any]:
    """Trend between the two most recent persisted snapshots."""
    records = load_history(history_dir)
    if not records:
        return {"previous_available": False, "records": 0}
    previous = records[-2] if len(records) >= 2 else None
    return compare_snapshots(records[-1], previous)
