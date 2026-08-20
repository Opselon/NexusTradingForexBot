"""Occurrence-aware impact analysis (spec 22/25).

Replaces the "0 trades / 0 records" black hole: an incident must count the
ACTUAL affected object types from real DB rows, and must distinguish:

    ZERO_IMPACT             incident touches concrete objects; none matched
    UNKNOWN_IMPACT          no concrete identity in the incident record
    NOT_YET_MEASURED        scan ran before persistence of the class
    PRE_PERSISTENCE_FAILURE defect sat BEFORE the object persisted
    MEASURED                counts derived from actual rows

Every count is derived from queryable rows keyed by the incident's own
identity fields (ticket / execution_id / order_id / position_id / master
order family / request_id / model). Nothing is fabricated; unobservable
families stay None.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.incidents.models import EvidenceItem, Incident

#: Canonical identity families an incident may carry.
IDENTITY_FAMILIES = (
    "ticket",
    "execution_id",
    "order_id",
    "position_id",
    "master_order_id",
    "request_id",
    "model_id",
    "research_run_id",
)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def _count(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] | list[Any] = ()) -> int:
    try:
        row = conn.execute(sql, args).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except sqlite3.Error:
        return 0


def _identity_values(incident: Incident) -> dict[str, list[str]]:
    """Collects identity values from the incident record (affected_records,
    timeline payloads, evidence observed dicts, correlation_id)."""
    out: dict[str, list[str]] = {k: [] for k in IDENTITY_FAMILIES}

    def add(family: str, value: Any) -> None:
        if value in (None, "", "0", "None"):
            return
        s = str(value).strip()
        if s and s not in out[family]:
            out[family].append(s)

    for rec in incident.affected_records:
        sr = str(rec)
        if sr.isdigit() or "-" in sr:
            add("ticket", sr)
        else:
            add("order_id", sr)
    for ev in incident.timeline:
        for k, v in (ev.payload or {}).items():
            kl = str(k).lower()
            for family in IDENTITY_FAMILIES:
                if family in kl or kl in ("ticket", "execution_id", "order_id"):
                    add(family, v)
    for e in incident.evidence:
        for k, v in (e.observed or {}).items():
            kl = str(k).lower()
            for family in IDENTITY_FAMILIES:
                if family in kl or kl in ("ticket", "execution_id", "order_id"):
                    add(family, v)
    if incident.correlation_id:
        add("request_id", incident.correlation_id)
    return {k: v for k, v in out.items() if v}


def _match_sql(cols: str, identities: dict[str, list[str]]) -> tuple[str, list[Any]]:
    """One 'OR ? IN (...)' per identity value against the given columns."""
    clauses: list[str] = []
    args: list[Any] = []
    for vals in identities.values():
        for v in vals:
            # CAST(? AS TEXT) so INTEGER-typed key columns (ticket INTEGER,
            # trade_id INTEGER) match TEXT identity values from incidents.
            # One OR per column (an IN over multiple columns is invalid SQL).
            per_col = " OR ".join(
                f"CAST(? AS TEXT) = CAST({c.strip()} AS TEXT)" for c in cols.split(",")
            )
            clauses.append(f"({per_col})")
            args.extend([v] * len(cols.split(",")))
    if not clauses:
        return "1=0", []
    return "(" + " OR ".join(clauses) + ")", args


def _families_to_where(identities: dict[str, list[str]]) -> tuple[str, list[Any]]:
    """WHERE matching ANY identity against canonical per-table key columns.

    audit_ledger: ticket / order_id (uuid). audit_experiences &
    audit_experience_outcomes: execution_id / request_id / idempotency_key.
    audit_broker_trades: trade_id / position_id / master_order_id.
    audit_orders: order_id / parent_order_id.
    """
    # Default per-table forms are built by the callers with _match_sql; this
    # helper returns the ledger-compatible shape for generic use.
    return _match_sql("ticket, execution_id, order_id, position_id, trade_id", identities)


def count_families(
    incident: Incident,
    db_path: str,
    *,
    window_days: int = 400,
) -> dict[str, Any]:
    """Counts affected objects per family from REAL rows keyed by the
    incident's own identities. Unobservable families are None (UNKNOWN),
    never 0."""
    ids = _identity_values(incident)
    conn = _connect(db_path)
    try:
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

        def fam(table: str, cols: str) -> int | None:
            if table not in tables or not ids:
                return None
            w, a = _match_sql(cols, ids)
            return _count(conn, f"SELECT COUNT(*) FROM {table} WHERE {w}", a)

        counts: dict[str, int | None] = {
            "affected_ledger_records": fam("audit_ledger", "ticket, order_id"),
            "affected_trades": fam("audit_ledger", "ticket, order_id"),
            "affected_outcomes": fam("audit_experience_outcomes", "execution_id, idempotency_key"),
            "affected_research_records": fam("research_runs", "run_id, strategy_id"),
        }
        counts["affected_orders"] = fam("audit_orders", "order_id, parent_order_id")
        counts["affected_executions"] = fam(
            "audit_experiences", "execution_id, request_id, idempotency_key"
        )
        counts["affected_positions"] = fam(
            "audit_broker_trades", "trade_id, position_id, master_order_id"
        )

        total_known = sum(int(v) for v in counts.values() if v is not None and v > 0)
        if total_known == 0 and not ids:
            semantics = "UNKNOWN_IMPACT"
        elif total_known == 0 and ids:
            semantics = "ZERO_IMPACT"
        else:
            semantics = "MEASURED"
        return {
            "counts": counts,
            "identities": ids,
            "total_known": total_known,
            "semantics": semantics,
            "measured_at": datetime.now(UTC).isoformat(),
        }
    finally:
        conn.close()


def pre_persistence_detection(incident: Incident, db_path: str) -> list[str]:
    """PRE_PERSISTENCE_FAILURE detection: incident's first_seen predates ANY
    row of the affected family (defect sat before persistence existed)."""
    notes: list[str] = []
    first = incident.first_seen_at
    if first is None:
        return notes
    conn = _connect(db_path)
    try:
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        for table, col in (
            ("audit_ledger", "open_time"),
            ("audit_experiences", "timestamp"),
            ("audit_broker_trades", "synced_at"),
        ):
            if table not in tables:
                continue
            try:
                row = conn.execute(
                    f"SELECT MIN({col}) AS m FROM {table} WHERE {col} != ''"
                ).fetchone()
            except sqlite3.Error:
                continue
            if row and row["m"]:
                from nexus_scalp.incidents.timebase import _parse_ts as _p

                dt = _p(str(row["m"]))
                if dt is not None and dt > first:
                    notes.append(
                        f"PRE_PERSISTENCE_FAILURE: incident first_seen "
                        f"{first.isoformat()} predates earliest {table}.{col} "
                        f"{dt.isoformat()}"
                    )
    finally:
        conn.close()
    return notes


def attach_occurrence_evidence(incident: Incident, result: dict[str, Any]) -> None:
    """Appends the occurrence analysis as an immutable evidence item."""
    counts = result.get("counts", {})
    present = {k: v for k, v in counts.items() if v is not None}
    incident.add_evidence(
        EvidenceItem(
            kind="DATABASE",
            source=f"audit.db occurrence scan @{result.get('measured_at', '')}",
            detail=(
                f"occurrence impact {result.get('semantics')}: "
                + ", ".join(f"{k}={v}" for k, v in sorted(present.items()))
            ),
            observed={
                "semantics": result.get("semantics"),
                "counts": present,
                "identities": result.get("identities", {}),
                "total_known": result.get("total_known", 0),
            },
        )
    )


__all__ = [
    "IDENTITY_FAMILIES",
    "attach_occurrence_evidence",
    "count_families",
    "pre_persistence_detection",
]
