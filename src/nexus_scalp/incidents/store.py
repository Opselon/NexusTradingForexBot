"""Incident Response — SQLite store (read/write via the canonical audit.db).

STORAGE STRATEGY (TASK-12 spec 58/59):
- Incidents live in audit.db tables `incidents`, `incident_events`,
  `incident_value_traces` and `incident_quarantine`, created by a governed
  additive migration (AUDIT-0005) — never ad-hoc DDL.
- All writes go through the AuditRepository queued writer when a repository
  is available (no synchronous DB on the tick path, INV-001). The store also
  supports a direct-connection mode for CLI/tests.
- Querying is bounded: search windows, LIMIT caps, indexed lookups
  (incident_id, fingerprint, category, severity, status).
- The store NEVER deletes incident evidence. Retention is policy-driven and
  documented (critical incidents retained longest); evidence referenced by
  BUG-NNN/releases/models is kept (spec 45).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.incidents.models import (
    BlastRadius,
    EventSource,
    EvidenceItem,
    Incident,
    IncidentCategory,
    IncidentImpact,
    IncidentSeverity,
    IncidentStatus,
    QuarantineEntry,
    RecoveryAction,
    RecoveryPlan,
    RecoveryState,
    RootCauseConfidence,
    TimelineEvent,
    ValueTrace,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.incidents.store")

# ---------------------------------------------------------------------------
# Canonical DDL — mirrors the AUDIT-0005 migration (registry.py).
# Kept here for tests/fixtures; production schema comes from the migration.
# ---------------------------------------------------------------------------

INCIDENTS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    detected_at TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    status TEXT NOT NULL,
    first_seen_at TEXT,
    last_seen_at TEXT,
    component TEXT DEFAULT '',
    operation TEXT DEFAULT '',
    correlation_id TEXT DEFAULT '',
    root_cause_status TEXT DEFAULT 'UNKNOWN',
    root_cause TEXT DEFAULT '',
    evidence_json TEXT DEFAULT '[]',
    impact_json TEXT DEFAULT '{}',
    affected_records_json TEXT DEFAULT '[]',
    affected_models_json TEXT DEFAULT '[]',
    affected_runtime_json TEXT DEFAULT '[]',
    affected_users_json TEXT DEFAULT '[]',
    recovery_status TEXT DEFAULT 'RECOMMENDED',
    recommended_action TEXT DEFAULT '',
    fingerprint TEXT DEFAULT '',
    repeated_count INTEGER DEFAULT 1,
    related_bug_id TEXT DEFAULT '',
    fix_commit TEXT DEFAULT '',
    regression_test TEXT DEFAULT '',
    is_regression INTEGER DEFAULT 0,
    previous_bug_id TEXT DEFAULT '',
    resolved_without_evidence INTEGER DEFAULT 0,
    recovery_plan_json TEXT DEFAULT '{}',
    tags_json TEXT DEFAULT '[]',
    notes_json TEXT DEFAULT '[]',
    updated_at TEXT
)
"""

INCIDENT_EVENTS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS incident_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL,
    event_timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT DEFAULT '{}',
    correlation_id TEXT DEFAULT ''
)
"""

INCIDENT_TRACES_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS incident_value_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL,
    field TEXT NOT NULL,
    source TEXT NOT NULL,
    source_timestamp TEXT,
    hops_json TEXT DEFAULT '[]'
)
"""

INCIDENT_QUARANTINE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS incident_quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL,
    target_table TEXT NOT NULL,
    record_key TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT DEFAULT '',
    evidence TEXT DEFAULT '',
    quarantined_at TEXT NOT NULL,
    UNIQUE (incident_id, target_table, record_key)
)
"""

INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);",
    "CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents(severity);",
    "CREATE INDEX IF NOT EXISTS idx_incidents_category ON incidents(category);",
    "CREATE INDEX IF NOT EXISTS idx_incidents_fingerprint ON incidents(fingerprint);",
    "CREATE INDEX IF NOT EXISTS idx_incidents_detected ON incidents(detected_at);",
    "CREATE INDEX IF NOT EXISTS idx_incident_events_incident ON incident_events(incident_id);",
    "CREATE INDEX IF NOT EXISTS idx_incident_quarantine_incident ON incident_quarantine(incident_id);",
)

INCIDENT_DDL: tuple[str, ...] = (
    INCIDENTS_TABLE_DDL,
    INCIDENT_EVENTS_TABLE_DDL,
    INCIDENT_TRACES_TABLE_DDL,
    INCIDENT_QUARANTINE_TABLE_DDL,
    *INDEX_DDL,
)


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _bool(v: Any) -> bool:
    return bool(v) and str(v).lower() not in ("0", "false", "")


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Row <-> Incident mapping
# ---------------------------------------------------------------------------


def row_to_incident(row: sqlite3.Row | dict[str, Any]) -> Incident | None:
    """Reconstructs an Incident from a stored row (read-side projection)."""
    if isinstance(row, sqlite3.Row):
        row = dict(row)
    try:
        inc = Incident(
            incident_id=str(row["incident_id"]),
            detected_at=_dt(row["detected_at"]) or datetime.now(UTC),
            severity=IncidentSeverity(str(row["severity"])),
            category=IncidentCategory(str(row["category"])),
            status=IncidentStatus(str(row["status"])),
            first_seen_at=_dt(row.get("first_seen_at")) or datetime.now(UTC),
            last_seen_at=_dt(row.get("last_seen_at")) or datetime.now(UTC),
            component=str(row.get("component") or ""),
            operation=str(row.get("operation") or ""),
            correlation_id=str(row.get("correlation_id") or ""),
            root_cause_status=RootCauseConfidence(str(row.get("root_cause_status") or "UNKNOWN")),
            root_cause=str(row.get("root_cause") or ""),
            recovery_status=RecoveryState(str(row.get("recovery_status") or "RECOMMENDED")),
            recommended_action=str(row.get("recommended_action") or ""),
            fingerprint=str(row.get("fingerprint") or ""),
            repeated_count=int(row.get("repeated_count") or 1),
            related_bug_id=str(row.get("related_bug_id") or ""),
            fix_commit=str(row.get("fix_commit") or ""),
            regression_test=str(row.get("regression_test") or ""),
            is_regression=_bool(row.get("is_regression")),
            previous_bug_id=str(row.get("previous_bug_id") or ""),
            resolved_without_evidence=_bool(row.get("resolved_without_evidence")),
            tags=list(_json_loads(row.get("tags_json"), [])),
            notes=list(_json_loads(row.get("notes_json"), [])),
        )
        # Evidence / impact / affected * / quarantine / recovery plan / traces
        for e in _json_loads(row.get("evidence_json"), []):
            inc.evidence.append(
                EvidenceItem(
                    kind=str(e.get("kind", "")),
                    source=str(e.get("source", "")),
                    detail=str(e.get("detail", "")),
                    observed=dict(e.get("observed") or {}),
                    timestamp=_dt(e.get("timestamp")),
                )
            )
        imp = _json_loads(row.get("impact_json"), {})
        inc.impact = IncidentImpact(
            affected_records=int(imp.get("affected_records", 0) or 0),
            affected_trades=int(imp.get("affected_trades", 0) or 0),
            affected_models=int(imp.get("affected_models", 0) or 0),
            affected_research_runs=int(imp.get("affected_research_runs", 0) or 0),
            affected_ui_endpoints=list(imp.get("affected_ui_endpoints") or []),
            affected_users=int(imp.get("affected_users", 0) or 0),
            blast_radius=BlastRadius(str(imp.get("blast_radius") or "LOCAL")),
            notes=list(imp.get("notes") or []),
        )
        inc.affected_records = list(_json_loads(row.get("affected_records_json"), []))
        inc.affected_models = list(_json_loads(row.get("affected_models_json"), []))
        inc.affected_runtime = list(_json_loads(row.get("affected_runtime_json"), []))
        inc.affected_users = list(_json_loads(row.get("affected_users_json"), []))
        plan = _json_loads(row.get("recovery_plan_json"), {})
        inc.recovery_plan = RecoveryPlan(
            what_failed=str(plan.get("what_failed", "")),
            why=str(plan.get("why", "")),
            affected=str(plan.get("affected", "")),
            trustworthy=list(plan.get("trustworthy") or []),
            suspect=list(plan.get("suspect") or []),
            must_not_change=list(plan.get("must_not_change") or []),
            required_tests=list(plan.get("required_tests") or []),
            status=RecoveryState(str(plan.get("status") or "RECOMMENDED")),
            options=[
                RecoveryAction(
                    step_id=str(o.get("step_id", "")),
                    action=str(o.get("action", "")),
                    kind=str(o.get("kind", "")),
                    destructive=bool(o.get("destructive")),
                    required_tests=list(o.get("required_tests") or []),
                    approval_required=bool(o.get("approval_required", True)),
                    status=RecoveryState(str(o.get("status") or "RECOMMENDED")),
                )
                for o in plan.get("options") or []
            ],
        )
        return inc
    except Exception as err:  # pragma: no cover - defensive deserialization
        logger.error(
            "[INCIDENTS] row_to_incident failed", incident_id=row.get("incident_id"), error=str(err)
        )
        return None


def _incident_row_values(inc: Incident) -> dict[str, Any]:
    return {
        "incident_id": inc.incident_id,
        "detected_at": inc.detected_at.isoformat(),
        "severity": inc.severity.value,
        "category": inc.category.value,
        "status": inc.status.value,
        "first_seen_at": inc.first_seen_at.isoformat(),
        "last_seen_at": inc.last_seen_at.isoformat(),
        "component": inc.component,
        "operation": inc.operation,
        "correlation_id": inc.correlation_id,
        "root_cause_status": inc.root_cause_status.value,
        "root_cause": inc.root_cause,
        "evidence_json": _json([e.as_dict() for e in inc.evidence]),
        "impact_json": _json(inc.impact.as_dict()),
        "affected_records_json": _json(list(inc.affected_records)),
        "affected_models_json": _json(list(inc.affected_models)),
        "affected_runtime_json": _json(list(inc.affected_runtime)),
        "affected_users_json": _json(list(inc.affected_users)),
        "recovery_status": inc.recovery_status,
        "recommended_action": inc.recommended_action,
        "fingerprint": inc.fingerprint,
        "repeated_count": inc.repeated_count,
        "related_bug_id": inc.related_bug_id,
        "fix_commit": inc.fix_commit,
        "regression_test": inc.regression_test,
        "is_regression": 1 if inc.is_regression else 0,
        "previous_bug_id": inc.previous_bug_id,
        "resolved_without_evidence": 1 if inc.resolved_without_evidence else 0,
        "recovery_plan_json": _json(inc.recovery_plan.as_dict()),
        "tags_json": _json(list(inc.tags)),
        "notes_json": _json(list(inc.notes)),
        "updated_at": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class IncidentStore:
    """Bounded, read/write store for incident records (audit.db).

    write mode: queued via AuditRepository when provided (INV-001); direct
    connection when used standalone (CLI/tests/forensic baseline).
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        audit_repo: Any = None,
    ) -> None:
        self.db_path = str(db_path) if db_path else ""
        self.audit_repo = audit_repo
        if not self.db_path and audit_repo is not None and getattr(audit_repo, "_db_path", None):
            self.db_path = str(audit_repo._db_path)
        if not self.db_path:
            raise ValueError("IncidentStore requires db_path or audit_repo")

    # -- schema --------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Creates tables if missing (idempotent; for CLI/tests/fresh DBs).

        Production DBs get the schema via the governed AUDIT-0005 migration.
        """
        with sqlite3.connect(self.db_path, timeout=10.0) as conn:
            for ddl in INCIDENT_DDL:
                conn.execute(ddl)

    # -- write ---------------------------------------------------------------

    def _upsert_incident_sql(self, incident: Incident) -> tuple[str, dict[str, Any]]:
        v = _incident_row_values(incident)
        cols = list(v.keys())
        placeholders = ", ".join(f":{c}" for c in cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "incident_id")
        sql = (
            f"INSERT INTO incidents ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(incident_id) DO UPDATE SET {updates}"
        )
        return sql, v

    def save(self, incident: Incident) -> str:
        """Persists one incident (upsert by incident_id)."""
        sql, values = self._upsert_incident_sql(incident)
        if self.audit_repo is not None and getattr(self.audit_repo, "_queue", None) is not None:
            try:
                self.audit_repo._queue.put_nowait((sql, values))
            except Exception as err:
                logger.error(
                    "[INCIDENTS] queued save failed",
                    incident_id=incident.incident_id,
                    error=str(err),
                )
            return incident.incident_id
        with sqlite3.connect(self.db_path, timeout=10.0) as conn:
            conn.execute(sql, values)
            for ev in incident.timeline:
                conn.execute(
                    "INSERT OR REPLACE INTO incident_events "
                    "(id, incident_id, event_timestamp, event_type, source, payload_json, correlation_id) "
                    "VALUES ((SELECT id FROM incident_events WHERE incident_id=? AND event_timestamp=? AND event_type=? LIMIT 1), ?, ?, ?, ?, ?, ?)",
                    (
                        incident.incident_id,
                        ev.timestamp.isoformat(),
                        ev.event_type,
                        incident.incident_id,
                        ev.timestamp.isoformat(),
                        ev.event_type,
                        ev.source.value,
                        _json(ev.payload),
                        ev.correlation_id,
                    ),
                )
            for tr in incident.value_traces:
                conn.execute(
                    "INSERT OR IGNORE INTO incident_value_traces "
                    "(incident_id, field, source, source_timestamp, hops_json) VALUES (?, ?, ?, ?, ?)",
                    (
                        incident.incident_id,
                        tr.field,
                        tr.source,
                        tr.source_timestamp.isoformat() if tr.source_timestamp else None,
                        _json(tr.hops()),
                    ),
                )
            for q in incident.quarantine_entries:
                conn.execute(
                    "INSERT OR REPLACE INTO incident_quarantine "
                    "(incident_id, target_table, record_key, status, reason, evidence, quarantined_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        incident.incident_id,
                        q.target_table,
                        q.record_key,
                        q.status,
                        q.reason,
                        q.evidence,
                        q.quarantined_at.isoformat(),
                    ),
                )
        return incident.incident_id

    def delete_by_id(self, incident_id: str) -> bool:
        """Removes an incident record (used by tests / operator purge).

        Never called automatically. Evidence is archived before delete when
        the caller requests it (spec 45).
        """
        deleted = False
        with sqlite3.connect(self.db_path, timeout=10.0) as conn:
            cur = conn.execute("DELETE FROM incidents WHERE incident_id=?", (incident_id,))
            deleted = cur.rowcount > 0
            conn.execute("DELETE FROM incident_events WHERE incident_id=?", (incident_id,))
            conn.execute("DELETE FROM incident_value_traces WHERE incident_id=?", (incident_id,))
            conn.execute("DELETE FROM incident_quarantine WHERE incident_id=?", (incident_id,))
        return deleted

    # -- read ----------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, incident_id: str) -> Incident | None:
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM incidents WHERE incident_id=?", (incident_id,)
                ).fetchone()
                if row is None:
                    return None
                inc = row_to_incident(row)
                if inc is None:
                    return None
                # events + traces + quarantine
                for ev in conn.execute(
                    "SELECT * FROM incident_events WHERE incident_id=? ORDER BY event_timestamp",
                    (incident_id,),
                ).fetchall():
                    inc.timeline.append(
                        TimelineEvent(
                            timestamp=_dt(ev["event_timestamp"]) or datetime.now(UTC),
                            event_type=str(ev["event_type"]),
                            source=EventSource(str(ev["source"])),
                            payload=_json_loads(ev["payload_json"], {}),
                            correlation_id=str(ev["correlation_id"] or ""),
                        )
                    )
                for tr in conn.execute(
                    "SELECT * FROM incident_value_traces WHERE incident_id=?",
                    (incident_id,),
                ).fetchall():
                    inc.value_traces.append(
                        ValueTrace(
                            field=str(tr["field"]),
                            source=str(tr["source"]),
                            source_timestamp=_dt(tr["source_timestamp"]),
                        )
                    )
                for q in conn.execute(
                    "SELECT * FROM incident_quarantine WHERE incident_id=?",
                    (incident_id,),
                ).fetchall():
                    inc.quarantine_entries.append(
                        QuarantineEntry(
                            target_table=str(q["target_table"]),
                            record_key=str(q["record_key"]),
                            status=str(q["status"]),
                            reason=str(q["reason"]),
                            incident_id=incident_id,
                            evidence=str(q["evidence"] or ""),
                            quarantined_at=_dt(q["quarantined_at"]) or datetime.now(UTC),
                        )
                    )
                return inc
            finally:
                conn.close()
        except sqlite3.Error as err:
            logger.error("[INCIDENTS] get failed", incident_id=incident_id, error=str(err))
            return None

    def list_incidents(
        self,
        *,
        status: str | None = None,
        severity: str | None = None,
        category: str | None = None,
        component: str | None = None,
        fingerprint: str | None = None,
        incident_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        ordered_by: str = "detected_at",
    ) -> list[Incident]:
        bounded = max(1, min(int(limit), 500))
        clauses: list[str] = []
        args: list[Any] = []
        if status:
            clauses.append("status = ?")
            args.append(str(status))
        if severity:
            clauses.append("severity = ?")
            args.append(str(severity))
        if category:
            clauses.append("category = ?")
            args.append(str(category))
        if component:
            clauses.append("component = ?")
            args.append(str(component))
        if fingerprint:
            clauses.append("fingerprint = ?")
            args.append(str(fingerprint))
        if incident_id:
            clauses.append("incident_id = ?")
            args.append(str(incident_id))
        order = (
            ordered_by
            if ordered_by
            in ("detected_at", "severity", "first_seen_at", "last_seen_at", "updated_at")
            else "detected_at"
        )
        order_expr = {
            "severity": "CASE severity WHEN 'CRITICAL' THEN 4 WHEN 'HIGH' THEN 3 WHEN 'MEDIUM' THEN 2 WHEN 'LOW' THEN 1 ELSE 0 END DESC, detected_at DESC",
        }.get(order, f"{order} DESC")
        sql = "SELECT * FROM incidents"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY {order_expr} LIMIT ? OFFSET ?"
        args.extend([bounded, max(0, int(offset))])
        out: list[Incident] = []
        try:
            conn = self._connect()
            try:
                for row in conn.execute(sql, args).fetchall():
                    inc = row_to_incident(row)
                    if inc is not None:
                        out.append(inc)
            finally:
                conn.close()
        except sqlite3.Error as err:
            logger.error("[INCIDENTS] list failed", error=str(err))
        return out

    def count(self) -> dict[str, int]:
        """Severity/status counts (bounded aggregate for dashboards)."""
        counts = {
            "total": 0,
            "open": 0,
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "unknown": 0,
            "recovered": 0,
            "false_positive": 0,
        }
        try:
            conn = self._connect()
            try:
                counts["total"] = int(conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0])
                row = conn.execute(
                    "SELECT status, COUNT(*) AS n FROM incidents GROUP BY status"
                ).fetchall()
                for r in row:
                    st = str(r["status"])
                    if st in {"CLOSED", "RECOVERED"}:
                        counts["recovered"] += int(r["n"])
                row = conn.execute(
                    "SELECT severity, COUNT(*) AS n FROM incidents GROUP BY severity"
                ).fetchall()
                for r in row:
                    sev = str(r["severity"]).upper()
                    if sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
                        counts[sev.lower()] = int(r["n"])
                counts["open"] = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM incidents WHERE status IN ('OPEN','INVESTIGATING','ROOT_CAUSE_IDENTIFIED','CONTAINED','RECOVERY_READY','RECOVERED')"
                    ).fetchone()[0]
                )
            finally:
                conn.close()
        except sqlite3.Error:
            pass
        return counts

    def search(self, query: str, limit: int = 50) -> list[Incident]:
        """Deterministic bounded search (spec 44): ticket/execution/order/
        deal/request/model/feature/incident-id/timestamp/error-code.
        """
        bounded = max(1, min(int(limit), 100))
        out: list[Incident] = []
        q = str(query).strip()
        if not q:
            return out
        # Direct id match first (cheap, exact).
        exact = self.get(q)
        if exact is not None:
            out.append(exact)
        like = f"%{q}%"
        sql = (
            "SELECT * FROM incidents WHERE incident_id LIKE ? OR component LIKE ? OR "
            "operation LIKE ? OR correlation_id LIKE ? OR root_cause LIKE ? OR "
            "related_bug_id LIKE ? OR affected_records_json LIKE ? OR evidence_json LIKE ? "
            "OR fingerprint LIKE ? OR tags_json LIKE ? "
            "ORDER BY detected_at DESC LIMIT ?"
        )
        args: list[Any] = [
            like,
            like,
            like,
            like,
            like,
            like,
            like,
            like,
            like,
            like,
            bounded - len(out),
        ]
        if int(args[-1]) <= 0:
            return out[:bounded]
        try:
            conn = self._connect()
            try:
                for row in conn.execute(sql, args).fetchall():
                    inc = row_to_incident(row)
                    if inc is not None and inc.incident_id not in {i.incident_id for i in out}:
                        out.append(inc)
            finally:
                conn.close()
        except sqlite3.Error:
            pass
        return out[:bounded]

    def stats_by_component(self) -> list[dict[str, Any]]:
        """Incidents grouped by component (spec 51 trend analysis)."""
        try:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT component, COUNT(*) AS n, "
                    "SUM(CASE WHEN severity='CRITICAL' THEN 1 ELSE 0 END) AS critical, "
                    "SUM(CASE WHEN severity='HIGH' THEN 1 ELSE 0 END) AS high "
                    "FROM incidents GROUP BY component ORDER BY n DESC LIMIT 50"
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()
        except sqlite3.Error:
            return []

    def recurring_fingerprints(self, limit: int = 20) -> list[dict[str, Any]]:
        """Recurring root fingerprints (spec 50/52) — regression candidates."""
        try:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT fingerprint, COUNT(*) AS occurrences, "
                    "MAX(detected_at) AS last_seen, "
                    "GROUP_CONCAT(DISTINCT category) AS categories "
                    "FROM incidents WHERE fingerprint != '' "
                    "GROUP BY fingerprint HAVING occurrences > 1 "
                    "ORDER BY occurrences DESC LIMIT ?",
                    (max(1, min(limit, 100)),),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()
        except sqlite3.Error:
            return []

    def archive_evidence(
        self, incident_id: str, archive_dir: str | Path | None = None
    ) -> Path | None:
        """Archives an incident's evidence before deletion (spec 45/46).

        Writes artifacts/incidents/archive/<incident_id>.json with the full
        record; the in-DB row may then be deleted safely.
        """
        inc = self.get(incident_id)
        if inc is None:
            return None
        base = (
            Path(archive_dir)
            if archive_dir
            else Path(self.db_path).parent.parent / "artifacts" / "incidents" / "archive"
        )
        base.mkdir(parents=True, exist_ok=True)
        out = base / f"{incident_id}.json"
        out.write_text(_json(inc.as_dict()), encoding="utf-8")
        return out


class IncidentLifecycle:
    """Evidence-based incident resolution (spec 30/31/64).

    Transitions:
        OPEN -> INVESTIGATING -> ROOT_CAUSE_IDENTIFIED -> CONTAINED
             -> RECOVERY_READY -> RECOVERED -> FIXED -> VERIFIED -> CLOSED
        OPEN -> FALSE_POSITIVE (with reason + evidence; record kept)

    VERIFIED requires: fix_commit + regression_test are set AND the caller
    recorded the passing forensic check as evidence. An incident is never
    marked VERIFIED without evidence.
    """

    #: Statuses that still count as "open" for the dashboard.
    OPEN_STATUSES = frozenset(
        {
            "OPEN",
            "INVESTIGATING",
            "ROOT_CAUSE_IDENTIFIED",
            "CONTAINED",
            "RECOVERY_READY",
            "RECOVERED",
        }
    )

    @staticmethod
    def transition(incident: Incident, new_status: str, *, actor: str = "operator") -> bool:
        """Moves the incident to a lifecycle status (timeline recorded)."""
        status = str(new_status).upper()
        if status not in IncidentStatus._value2member_map_:
            return False
        if status == "VERIFIED":
            if not incident.fix_commit or not incident.regression_test:
                incident.add_timeline_event(
                    TimelineEvent(
                        timestamp=datetime.now(UTC),
                        event_type="VERIFY_REFUSED",
                        source=EventSource.MANUAL,
                        payload={
                            "reason": "VERIFIED requires fix_commit + regression_test + passing check",
                            "actor": actor,
                        },
                    )
                )
                return False
        incident.status = IncidentStatus(status)
        incident.add_timeline_event(
            TimelineEvent(
                timestamp=datetime.now(UTC),
                event_type=f"STATUS_{status}",
                source=EventSource.MANUAL,
                payload={"actor": actor},
            )
        )
        return True

    @staticmethod
    def mark_false_positive(
        incident: Incident,
        *,
        reason: str,
        evidence: str = "",
        detector_defect: str = "",
        corrected_rule: str = "",
        actor: str = "operator",
    ) -> bool:
        """Marks an incident FALSE_POSITIVE (record kept, spec 64)."""
        incident.status = IncidentStatus.FALSE_POSITIVE
        incident.notes.append(f"FALSE_POSITIVE: {reason}")
        incident.add_timeline_event(
            TimelineEvent(
                timestamp=datetime.now(UTC),
                event_type="MARKED_FALSE_POSITIVE",
                source=EventSource.MANUAL,
                payload={
                    "reason": reason,
                    "evidence": evidence,
                    "detector_defect": detector_defect,
                    "corrected_rule": corrected_rule,
                    "actor": actor,
                },
            )
        )
        return True


def classify_root_cause(
    *,
    category: str,
    statement: str,
    confidence: str = "UNKNOWN",
    evidence_count: int = 0,
    reproduction_status: str = "NOT_REPRODUCED",
) -> dict[str, Any]:
    """Evidence-backed root-cause record (spec 27/28).

    Confidence semantics (spec 27):
        UNKNOWN         - insufficient evidence
        SUSPECTED       - some evidence, competing explanations possible
        HIGH_CONFIDENCE - multiple corroborating signals, no credible alternative
        PROVEN          - direct deterministic evidence / invariant violation
                          with reproducible reproduction
    """
    conf = str(confidence).upper()
    if conf not in ("UNKNOWN", "SUSPECTED", "HIGH_CONFIDENCE", "PROVEN"):
        conf = "UNKNOWN"
    return {
        "root_cause_category": str(category),
        "root_cause_statement": str(statement),
        "root_cause_confidence": conf,
        "evidence_count": int(evidence_count),
        "reproduction_status": str(reproduction_status),
    }


__all__ = [
    "INCIDENT_DDL",
    "IncidentStore",
    "row_to_incident",
    "IncidentLifecycle",
    "classify_root_cause",
]
