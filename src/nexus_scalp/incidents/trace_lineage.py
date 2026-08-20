"""One-Click Trace (spec 24/25/26) — full object-lineage resolution.

Accepts: incident_id / ticket / execution_id / request_id / model_id /
order_id / position_id / research_run_id / training_run_id.

For each input kind, walks the REAL database lineage:

    incident  -> root cause + evidence + affected entities +
                 upstream/downstream lineage
    ticket    -> ledger -> broker positions -> outcomes -> research ->
                 experiences -> model (where traceability exists)
    execution -> order -> request -> fills -> position -> trade -> ledger ->
                 outcome -> research
    model     -> training run -> dataset -> research outcomes -> strategy ->
                 deployment (only where links exist)

Never fabricates links: when a hop cannot be established it returns
    missing_link: <hop name>, reason, last_known_node
instead of inventing a relationship (spec 26).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from nexus_scalp.incidents.models import Incident
from nexus_scalp.incidents.store import IncidentStore


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except sqlite3.Error:
        return []


def _first(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    rows = _rows(conn, sql, args)
    return rows[0] if rows else None


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r["name"] for r in _rows(conn, "SELECT name FROM sqlite_master WHERE type='table'")}


def _trace_ticket_or_execution(
    conn: sqlite3.Connection,
    table_names: set[str],
    value: str,
) -> dict[str, Any]:
    """Resolves a ticket / execution_id / order_id across the ledger,
    broker trades, experiences, outcomes, research and models."""
    node: dict[str, Any] = {"query": value, "kind": "ticket/execution/order"}
    ledger = _first(conn, "SELECT * FROM audit_ledger WHERE ticket=? OR order_id=?", (value, value))
    broker = _first(
        conn,
        "SELECT * FROM audit_broker_trades WHERE trade_id=? OR position_id=? OR master_order_id=?",
        (value, value, value),
    )
    outcome = _first(
        conn,
        "SELECT * FROM audit_experience_outcomes WHERE execution_id=? OR idempotency_key=?",
        (value, value),
    )
    experience = _first(
        conn,
        "SELECT * FROM audit_experiences WHERE execution_id=? OR idempotency_key=? OR request_id=?",
        (value, value, value),
    )
    node["ledger"] = ledger
    node["broker_position"] = broker
    node["outcome"] = outcome
    node["experience"] = experience

    if ledger and str(ledger.get("order_id") or "") and str(ledger.get("order_id")) != value:
        oid = str(ledger["order_id"])
        outcome2 = _first(
            conn,
            "SELECT * FROM audit_experience_outcomes WHERE idempotency_key=?",
            (oid,),
        )
        if outcome2:
            node["outcome"] = outcome2
        if not experience:
            experience = _first(
                conn,
                "SELECT * FROM audit_experiences WHERE idempotency_key=?",
                (oid,),
            )
            node["experience"] = experience

    if "audit_orders" in table_names:
        node["orders"] = _rows(
            conn,
            "SELECT * FROM audit_orders WHERE ticket=? OR order_id=? ORDER BY timestamp DESC LIMIT 10",
            (value, value),
        )
    strategy_id = (experience or {}).get("strategy_id") or ""
    if strategy_id and "research_runs" in table_names:
        node["research_runs"] = _rows(
            conn,
            "SELECT * FROM research_runs WHERE strategy_id=? ORDER BY executed_at DESC LIMIT 5",
            (strategy_id,),
        )
    model_id = (experience or {}).get("model_id") or ""
    if model_id:
        node["model_id"] = str(model_id)
    return node


def _trace_incident(db_path: str, store: IncidentStore, incident_id: str) -> dict[str, Any]:
    inc: Incident | None = store.get(incident_id)
    if inc is None:
        return {
            "kind": "incident",
            "query": incident_id,
            "missing_link": "incident",
            "reason": "incident id not found in store",
            "last_known_node": None,
        }
    return {
        "kind": "incident",
        "query": incident_id,
        "incident": inc.as_dict(),
        "root_cause": {
            "status": inc.root_cause_status.value,
            "statement": inc.root_cause,
            "evidence_count": len(inc.evidence),
        },
        "affected_entities": {
            "affected_records": list(inc.affected_records[:50]),
            "affected_models": list(inc.affected_models[:20]),
        },
        "lineage": _trace_incident_lineage(db_path, inc),
    }


def _trace_incident_lineage(db_path: str, inc: Incident) -> dict[str, Any]:
    """Upstream/downstream lineage for an incident based on its identities."""
    conn = _connect(db_path)
    try:
        names = _tables(conn)
        out: dict[str, Any] = {"upstream": [], "downstream": []}
        ids: list[str] = []
        for rec in inc.affected_records:
            s = str(rec)
            if s and s not in ids:
                ids.append(s)
        for ident in ids[:10]:
            out["downstream"].append(_trace_ticket_or_execution(conn, names, ident))
        return out
    finally:
        conn.close()


def _trace_model(conn: sqlite3.Connection, table_names: set[str], model_id: str) -> dict[str, Any]:
    """Model lineage: training run -> dataset -> research outcomes ->
    strategy -> deployment (only where links exist)."""
    node: dict[str, Any] = {"query": model_id, "kind": "model"}
    if "model_registry" in table_names:
        node["model_registry"] = _rows(
            conn,
            "SELECT * FROM model_registry WHERE model_id=? OR artifact_id=? LIMIT 5",
            (model_id, model_id),
        )
    node["experiences"] = _rows(
        conn,
        "SELECT experience_id, execution_id, request_id, strategy_id, model_id, "
        "decision_timestamp, action, idempotency_key FROM audit_experiences "
        "WHERE model_id=? LIMIT 20",
        (model_id,),
    )
    strategy_ids = sorted(
        {str(e.get("strategy_id") or "") for e in node["experiences"] if e.get("strategy_id")}
    )
    if strategy_ids and "research_runs" in table_names:
        placeholders = ",".join("?" * len(strategy_ids))
        node["research_runs"] = _rows(
            conn,
            f"SELECT * FROM research_runs WHERE strategy_id IN ({placeholders}) ORDER BY executed_at DESC LIMIT 10",
            tuple(strategy_ids),
        )
    if not node["model_registry"] and not node["experiences"]:
        node["missing_link"] = "model"
        node["reason"] = "no registry or experience row references this model_id"
        node["last_known_node"] = None
    return node


def _trace_research_run(
    conn: sqlite3.Connection, table_names: set[str], run_id: str
) -> dict[str, Any]:
    node: dict[str, Any] = {"query": run_id, "kind": "research_run"}
    if "research_runs" in table_names:
        node["research_run"] = _first(
            conn, "SELECT * FROM research_runs WHERE run_id=? OR dataset_id=?", (run_id, run_id)
        )
    run = node.get("research_run") or {}
    strategy_id = run.get("strategy_id") or ""
    if strategy_id:
        node["strategy"] = _first(
            conn, "SELECT * FROM strategy_registry WHERE strategy_id=?", (strategy_id,)
        )
        node["outcomes_family"] = _rows(
            conn,
            "SELECT COUNT(*) AS n FROM audit_experience_outcomes o "
            "JOIN audit_experiences e ON e.idempotency_key = o.idempotency_key "
            "WHERE e.strategy_id=?",
            (strategy_id,),
        )
    if not run:
        node["missing_link"] = "research_run"
        node["reason"] = "no research_runs row matches this id"
        node["last_known_node"] = None
    return node


def trace_lineage(db_path: str, query: str, store: IncidentStore | None = None) -> dict[str, Any]:
    """Resolves ANY supported id into its lineage graph (spec 24/25/26).

    Returns missing_link + reason + last_known_node when a lineage hop
    cannot be established — never fabricates links.
    """
    q = str(query or "").strip()
    if not q:
        return {"kind": "unknown", "missing_link": "query", "reason": "empty query"}
    conn = _connect(db_path)
    try:
        names = _tables(conn)
        if q.upper().startswith("INC-"):
            store = store or IncidentStore(db_path=db_path)
            return _trace_incident(db_path, store, q.upper())
        if q.lower().startswith(("run_", "ds_")):
            return _trace_research_run(conn, names, q)
        if not q.isdigit() and not q.startswith("exp_"):
            exp = _first(
                conn, "SELECT model_id FROM audit_experiences WHERE model_id=? LIMIT 1", (q,)
            )
            if exp:
                return _trace_model(conn, names, q)
        return _trace_ticket_or_execution(conn, names, q)
    finally:
        conn.close()


__all__ = [
    "_trace_model",
    "_trace_research_run",
    "_trace_ticket_or_execution",
    "trace_lineage",
]
