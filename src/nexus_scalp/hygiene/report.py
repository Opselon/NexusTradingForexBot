"""
Database Hygiene Reports (TASK-22)
==================================
Structured report builders:

  * DATABASE_HYGIENE_INITIAL_REPORT — full first-run audit (orphans, broken
    relations, impossible timestamps, invalid states, duplicates, stale
    cache, index health, abandoned states). Written once to
    artifacts/archive/_hygiene_state/initial_audit.json.
  * Cycle telemetry (spec §15): cleanup_id, start/end, duration,
    records scanned/deleted/archived/quarantined, errors.
  * QUERY_HEALTH_REPORT — index health summary for Telegram/debug output.
  * Telegram report TEXT builder — the exact spec §16 shape
    (DATABASE HYGIENE REPORT with Cycle/Scanned/Removed/Archived/
    Quarantined/Duration/Status). No sending here — delivery goes through
    the engine's notifier (INV-010: telegram is a read-only consumer).

All builders are pure functions over the scan/cycle results — no DB access.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Initial audit report filename (persisted under _hygiene_state).
INITIAL_AUDIT_FILENAME = "initial_audit.json"


def build_initial_audit_report(
    *,
    database_results: dict[str, dict[str, Any]],
    consistency: dict[str, dict[str, Any]],
    index_health: dict[str, dict[str, Any]],
    quarantine_stats: dict[str, Any],
    run_id: str = "",
) -> dict[str, Any]:
    """DATABASE_HYGIENE_INITIAL_REPORT (spec §4)."""
    totals = {
        "tables": 0,
        "rows_scanned": 0,
        "duplicates": 0,
        "orphans": 0,
        "retention_candidates": 0,
        "delete_candidates": 0,
        "violations": 0,
    }
    for res in database_results.values():
        plan = res.get("plan", res.get("plan_summary", {})) or {}
        totals["tables"] += int(plan.get("tables_scanned", 0) or 0)
        totals["rows_scanned"] += int(plan.get("rows_scanned", 0) or 0)
        totals["duplicates"] += int(plan.get("duplicates_found", 0) or 0)
        totals["orphans"] += int(plan.get("orphans_found", 0) or 0)
        totals["retention_candidates"] += int(plan.get("retention_candidates", 0) or 0)
        totals["delete_candidates"] += int(plan.get("delete_candidates", 0) or 0)
    for c in consistency.values():
        totals["violations"] += int(c.get("violations", 0) or 0)

    index_findings = {
        db: {
            "missing": ih.get("summary", {}).get("MISSING", 0),
            "duplicate": ih.get("summary", {}).get("DUPLICATE", 0),
            "unused": ih.get("summary", {}).get("UNUSED", 0),
        }
        for db, ih in index_health.items()
    }

    report = {
        "report_type": "DATABASE_HYGIENE_INITIAL_REPORT",
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "totals": totals,
        "per_database": {
            db: {
                "plan": res.get("plan", res.get("plan_summary", {})),
                "consistency": c,
                "index_health": index_health.get(db, {}).get("summary", {}),
            }
            for db, res in database_results.items()
            if (c := consistency.get(db, {}))
        },
        "index_health_summary": index_findings,
        "quarantine": quarantine_stats,
        "verdict": "ACTION_REQUIRED" if totals["violations"] else "CLEAN",
    }
    return report


def persist_initial_audit(report: dict[str, Any], state_root: Path) -> Path:
    """Writes the initial audit report (idempotent; keeps a .prev backup)."""
    d = Path(state_root) / "archive" / "_hygiene_state"
    d.mkdir(parents=True, exist_ok=True)
    target = d / INITIAL_AUDIT_FILENAME
    if target.exists():
        prev = d / (INITIAL_AUDIT_FILENAME + ".prev")
        prev.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    target.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return target


def build_cycle_telemetry(
    *,
    run_id: str,
    mode: str,
    started_at: str,
    duration_ms: float,
    rows_scanned: int,
    deleted: dict[str, int],
    archived: dict[str, int],
    quarantined: int,
    errors: list[str],
    verification: str,
    deep: bool = False,
) -> dict[str, Any]:
    """Cycle telemetry (spec §15)."""
    return {
        "cleanup_id": run_id,
        "mode": mode,
        "deep_maintenance": deep,
        "start_time": started_at,
        "end_time": datetime.now(UTC).isoformat(),
        "duration_ms": round(float(duration_ms), 1),
        "records_scanned": int(rows_scanned),
        "records_deleted": int(sum(deleted.values())),
        "records_archived": int(sum(archived.values())),
        "records_quarantined": int(quarantined),
        "deleted_by_table": {k: int(v) for k, v in deleted.items()},
        "archived_by_table": {k: int(v) for k, v in archived.items()},
        "errors": list(errors),
        "verification": verification,
    }


def build_query_health_report(index_reports: list[dict[str, Any]]) -> dict[str, Any]:
    """QUERY_HEALTH_REPORT (spec §10) — aggregated index findings."""
    findings: list[dict[str, Any]] = []
    for rep in index_reports:
        findings.extend(rep.get("findings", []))
    missing = [f for f in findings if f["category"] == "MISSING"]
    duplicate = [f for f in findings if f["category"] == "DUPLICATE"]
    unused = [f for f in findings if f["category"] == "UNUSED"]
    return {
        "report_type": "QUERY_HEALTH_REPORT",
        "generated_at": datetime.now(UTC).isoformat(),
        "findings_total": len(findings),
        "missing_indexes": [f["ref_sql"] for f in missing],
        "duplicate_indexes": [
            {"table": f["table"], "detail": f["detail"]} for f in duplicate
        ],
        "unused_indexes": [
            {"table": f["table"], "detail": f["detail"]} for f in unused
        ],
        "advice": (
            "CREATE INDEX statements are ADVISORY ONLY — schema changes go "
            "through the TASK-10 migration engine, never the runtime worker."
        ),
    }


def build_telegram_report_text(telemetry: dict[str, Any], cycle_number: int) -> str:
    """Spec §16 DATABASE HYGIENE REPORT text (plain, tag-safe)."""
    status = telemetry.get("verification", "NOT_RUN")
    lines = [
        "DATABASE HYGIENE REPORT",
        f"Cycle: #{cycle_number}",
        f"Scanned: {telemetry.get('records_scanned', 0):,} records",
        f"Removed: {telemetry.get('records_deleted', 0):,} "
        f"(cache/expired/stale)",
        f"Archived: {telemetry.get('records_archived', 0):,} telemetry records",
        f"Quarantined: {telemetry.get('records_quarantined', 0):,} "
        f"suspicious records",
        f"Duration: {telemetry.get('duration_ms', 0.0) / 1000.0:.1f}s",
        f"Mode: {telemetry.get('mode', 'AUDIT_ONLY')}",
        f"Status: {status}",
    ]
    if telemetry.get("errors"):
        lines.append(f"Errors: {len(telemetry['errors'])}")
    return "\n".join(lines)


def build_telegram_initial_report_text(report: dict[str, Any]) -> str:
    """Telegram digest of the first-run audit."""
    totals = report.get("totals", {})
    lines = [
        "DATABASE HYGIENE INITIAL AUDIT",
        f"Scanned: {totals.get('rows_scanned', 0):,} records / "
        f"{totals.get('tables', 0)} tables",
        f"Duplicates: {totals.get('duplicates', 0)}",
        f"Orphans: {totals.get('orphans', 0)}",
        f"Retention candidates: {totals.get('retention_candidates', 0)}",
        f"Consistency violations: {totals.get('violations', 0)}",
        f"Verdict: {report.get('verdict', 'UNKNOWN')}",
    ]
    return "\n".join(lines)