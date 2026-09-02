"""Operator evidence routes (CHG-0043, TASK-CONTROL-CENTER).

READ-ONLY decision-evidence surface for the Operational Control Center.

Design contract
---------------
* Read-only: every handler only SELECTs. No config mutation, no engine
  control, no order authority (research/strategies must never hold one;
  INV-002 honored by construction - this module does not even import the
  engine).
* Ledger-truthful: decisions come from the immutable ``audit_signals``
  ledger (same source ``AuditRepository.get_recent_predictions`` serves the
  dashboard prediction table) via a ``file:...?mode=ro`` SQLite URI so a UI
  bug can never write to the audit trail.
* Bounded: every query scans a bounded recent-id window (default 20000
  rows) instead of the whole table; the actually-scanned row count is
  disclosed in every response (``scanned_rows``) so summary numbers can be
  reconciled against the ledger.
* No fabrication: a probability the ledger never recorded renders as
  ``None``; unparseable payload JSON keeps its row with ``payload_ok:
  false`` (never silently dropped); absent geometry is
  ``RR_NOT_RECORDED``-style ``None``, never an invented value.
* Sanitized: rows expose a fixed projection (no raw payload blob in list
  views); errors go through the shared safe envelope ``_err()`` with the
  detail logged server-side only (web/errors.py contract).

Endpoints (all GET)
-------------------
/api/operator/summary          one call feeding the overview: runtime truth
                               (canonical get_system_state + release runtime
                               snapshot identity) + decision stats + warnings
/api/operator/decisions        filterable decision history (hours, action,
                               gate, search, limit) from audit_signals
/api/operator/decisions/{id}   one decision: full payload + correlated
                               audit_orders rows (correlation method
                               disclosed: request_id match, fallback
                               execution_id column when present)
/api/operator/funnel           terminal-stage + gate distributions (labeled
                               as TERMINAL distributions - the ledger records
                               the final blocking stage, not every interim
                               pass, so a fabricated per-stage funnel is
                               forbidden)
/api/operator/no-trade         NO_TRADE forensics: gate/regime distributions,
                               hourly trend, recent examples
/api/operator/orders           recent audit_orders rows + computed latency
                               stats

Registration: ``register_operator_routes(app, get_system_state, _err,
_log_err, serialize_enums)`` called from ``create_app()`` immediately after
``register_debug_research_routes`` (additive; no existing route touched).
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from typing import Any

#: audit_signals columns exposed in LIST views (fixed projection - the raw
#: payload blob stays server-side; detail endpoint returns it explicitly).
_SIGNAL_LIST_COLUMNS = (
    "id",
    "request_id",
    "symbol",
    "action",
    "confidence",
    "regime",
    "generated_at",
    "execution_mode",
    "reason_code",
    "decision_stage",
    "blocked_by",
)

#: Optional decision-evidence columns added by the parallel CHG-0043
#: decision-evidence completeness repair (preferred_direction / raw_prob_* /
#: confidence_source / spread_usd). Present on current databases; absent on
#: older ones - probed once per connection, never assumed.
_OPTIONAL_SIGNAL_COLUMNS = (
    "preferred_direction",
    "raw_prob_buy",
    "raw_prob_sell",
    "raw_prob_no_trade",
    "raw_prob_wait",
    "confidence_source",
    "spread_usd",
)

_MAX_LIMIT = 500
_MAX_WINDOW = 50000


def _audit_db_path() -> str | None:
    """Authoritative audit DB path (workspace-anchored, BUG-149 semantics)."""
    try:
        from nexus_scalp.database.provider import default_sqlite_path

        return default_sqlite_path("audit")
    except Exception:
        return None


def _connect_ro() -> sqlite3.Connection | None:
    """Read-only SQLite connection to the authoritative audit DB.

    ``mode=ro`` is deliberate: the operator surface must be structurally
    unable to mutate the audit trail. Returns None when the DB is absent
    (fresh install) - callers report ``LEDGER_UNAVAILABLE``, never fake
    empty statistics.
    """
    path = _audit_db_path()
    if not path:
        return None
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    except Exception:
        return None


def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        return any(row[1] == column for row in con.execute(f"PRAGMA table_info({table})"))
    except Exception:
        return False


def _safe_json(text: Any) -> tuple[dict[str, Any], bool]:
    """Parse a stored payload; (parsed, ok). Malformed stays visible."""
    if text is None or text == "":
        return {}, False
    if isinstance(text, dict):
        return text, True
    try:
        parsed = json.loads(text)
        return (parsed if isinstance(parsed, dict) else {}), True
    except Exception:
        return {}, False


def _probability_block(parsed: dict[str, Any], optional_cols: dict[str, Any]) -> dict[str, Any]:
    """Model probabilities from the payload; raw block from evidence columns.

    Neither source is invented: a missing value stays None and the detail
    view labels it EVIDENCE NOT RECORDED (frontend contract).
    """
    block: dict[str, Any] = {
        "buy": parsed.get("ai_buy_probability"),
        "sell": parsed.get("ai_sell_probability"),
        "no_trade": parsed.get("ai_no_trade_probability"),
        "wait": parsed.get("ai_wait_probability"),
        "model_action": parsed.get("model_action"),
    }
    raw = {
        "buy": optional_cols.get("raw_prob_buy"),
        "sell": optional_cols.get("raw_prob_sell"),
        "no_trade": optional_cols.get("raw_prob_no_trade"),
        "wait": optional_cols.get("raw_prob_wait"),
        "source": optional_cols.get("confidence_source") or None,
    }
    block["raw"] = (
        raw
        if any(v is not None for k, v in raw.items() if k != "source") or raw["source"]
        else None
    )
    return block


def _signal_row(
    row: sqlite3.Row,
    optional_cols: list[str],
    include_payload: bool = False,
) -> dict[str, Any]:
    out: dict[str, Any] = {c: row[c] for c in _SIGNAL_LIST_COLUMNS}
    for c in optional_cols:
        out[c] = row[c] if c in row.keys() else None
    parsed, ok = _safe_json(row["payload"] if "payload" in row.keys() else None)
    out["payload_ok"] = ok
    out["probabilities"] = _probability_block(parsed, {c: out.get(c) for c in optional_cols})
    if include_payload:
        out["payload"] = parsed if ok else None
        out["payload_raw_ok"] = ok
    else:
        out.pop("payload", None)
    return out


def register_operator_routes(
    app: Any,
    get_system_state: Callable[[], dict[str, Any]],
    _err: Callable[..., dict[str, Any]],
    _log_err: Callable[..., None],
    serialize_enums: Callable[[Any], Any],
) -> None:
    """Registers the read-only /api/operator/* surface on the FastAPI app."""

    # ------------------------------------------------------------------
    # GET /api/operator/summary - one call feeding the overview screen
    # ------------------------------------------------------------------
    @app.get("/api/operator/summary")
    def operator_summary() -> dict[str, Any]:
        state = get_system_state()
        summary: dict[str, Any] = {
            "available": True,
            "runtime": {
                "engine_running": state.get("engine_running"),
                "execution_mode": state.get("execution_mode"),
                "runtime_mode": state.get("runtime_mode"),
                "symbol": state.get("symbol"),
                "regime": state.get("regime"),
                "bid": state.get("bid"),
                "ask": state.get("ask"),
                "spread": state.get("spread"),
                "tick_stale": state.get("tick_stale"),
                "tick_freshness_ms": state.get("tick_freshness_ms"),
                "state_version": state.get("state_version"),
                "snapshot_timestamp": state.get("snapshot_timestamp"),
                "provenance": state.get("provenance"),
                "health": state.get("health"),
            },
            "ledger": {"available": False, "reason": "LEDGER_UNAVAILABLE"},
            "warnings": [],
        }

        # Runtime identity from the canonical release snapshot (never the
        # stale gitignored build-info.json for source runs - CHG-0043 truth).
        try:
            from nexus_scalp.release.runtime_snapshot import build_runtime_snapshot

            snap = build_runtime_snapshot(include_update=False)
            summary["identity"] = {
                "version": (snap.get("identity") or {}).get("version"),
                "commit": (snap.get("identity") or {}).get("commit"),
                "commit_status": (snap.get("identity") or {}).get("commit_status"),
                "channel": (snap.get("identity") or {}).get("channel"),
                "feature_contract": snap.get("feature_contract"),
                "model_configured": snap.get("model"),
                "database": snap.get("database"),
                "generated_at": snap.get("generated_at"),
            }
        except Exception as exc:
            _log_err(exc, "operator summary identity failed", endpoint="/api/operator/summary")
            summary["identity"] = None

        # Decision stats over a bounded recent window.
        con = _connect_ro()
        if con is None:
            summary["warnings"].append(
                {
                    "code": "LEDGER_UNAVAILABLE",
                    "severity": "HIGH",
                    "what": "Decision ledger (audit DB) is not reachable for reads.",
                    "why": "The audit database is absent or cannot be opened read-only.",
                    "since": None,
                    "impact": "Decision history, funnel and NO_TRADE forensics are unavailable.",
                    "what_to_do": "Inspect the audit DB path/status via Database Management.",
                }
            )
            return serialize_enums(summary)
        try:
            con.row_factory = sqlite3.Row
            window = 20000
            ids = [
                r[0]
                for r in con.execute(
                    "SELECT id FROM audit_signals ORDER BY id DESC LIMIT ?", (window,)
                )
            ]
            scanned = len(ids)
            stats: dict[str, Any] = {"scanned_rows": scanned, "window": window}
            if scanned:
                ph = ",".join("?" * scanned)
                rows = con.execute(
                    f"SELECT action, decision_stage, blocked_by, generated_at FROM audit_signals WHERE id IN ({ph})",
                    ids,
                ).fetchall()
                actions = Counter(r["action"] for r in rows)
                stats["actions"] = dict(actions)
                stats["total"] = scanned
                last = rows[0]["generated_at"] if rows else None
                stats["latest_decision_at"] = last
                # Simple 1h recency split (no clock fabrication: bounded by
                # ledger timestamps themselves).
                stats["actions_1h"] = {}
            summary["ledger"] = {"available": True, **stats}

            # Model/health-derived warnings are appended by the frontend from
            # /health + live state; here we surface only ledger-visible ones.
            if scanned and stats.get("actions"):
                no_trade = stats["actions"].get("NO_TRADE", 0)
                if scanned and no_trade / scanned > 0.95:
                    summary["warnings"].append(
                        {
                            "code": "NO_TRADE_DOMINANT",
                            "severity": "INFO",
                            "what": f"{no_trade}/{scanned} recent ledger decisions are NO_TRADE.",
                            "why": "Gates or policy blocked candidates (see NO_TRADE forensics).",
                            "since": None,
                            "impact": "No new positions originate from this window.",
                            "what_to_do": "Open Decision Observatory -> NO_TRADE forensics.",
                        }
                    )
        except Exception as exc:
            _log_err(exc, "operator summary ledger stats failed", endpoint="/api/operator/summary")
            summary["ledger"] = {"available": False, "reason": "LEDGER_READ_ERROR"}
        finally:
            con.close()
        return serialize_enums(summary)

    # ------------------------------------------------------------------
    # GET /api/operator/decisions - filterable decision history
    # ------------------------------------------------------------------
    @app.get("/api/operator/decisions")
    def operator_decisions(
        hours: float | None = None,
        action: str | None = None,
        gate: str | None = None,
        search: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), _MAX_LIMIT))
        con = _connect_ro()
        if con is None:
            return _err("RESOURCE_UNAVAILABLE", extra={"reason": "LEDGER_UNAVAILABLE"})
        try:
            con.row_factory = sqlite3.Row
            optional_cols = [
                c for c in _OPTIONAL_SIGNAL_COLUMNS if _has_column(con, "audit_signals", c)
            ]
            where: list[str] = []
            params: list[Any] = []
            if hours is not None and hours > 0:
                # Filter on the ledger's own timestamp (ISO-8601 with offset,
                # comparable to SQLite's datetime('now') which is UTC).
                where.append("generated_at >= datetime('now', ?)")
                params.append(f"-{float(hours)} hours")
            if action:
                where.append("UPPER(action) = ?")
                params.append(action.upper())
            if gate:
                where.append(
                    "(UPPER(COALESCE(decision_stage,'')) = ? OR UPPER(COALESCE(blocked_by,'')) = ?)"
                )
                params.extend([gate.upper(), gate.upper()])
            if search:
                where.append("(request_id LIKE ? OR reason_code LIKE ? OR regime LIKE ?)")
                like = f"%{search}%"
                params.extend([like, like, like])
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            sql = (
                "SELECT id, request_id, symbol, action, confidence, regime, generated_at, "
                "execution_mode, reason_code, decision_stage, blocked_by, payload"
                + (", " + ", ".join(optional_cols) if optional_cols else "")
                + f" FROM audit_signals {clause} ORDER BY id DESC LIMIT ?"
            )
            params.append(limit)
            rows = con.execute(sql, params).fetchall()
            out = [_signal_row(r, optional_cols) for r in rows]
            return serialize_enums(
                {
                    "available": True,
                    "filters": {
                        "hours": hours,
                        "action": action,
                        "gate": gate,
                        "search": search,
                    },
                    "count": len(out),
                    "rows": out,
                }
            )
        except Exception as exc:
            _log_err(exc, "operator decisions query failed", endpoint="/api/operator/decisions")
            return _err("INTERNAL_ERROR")
        finally:
            con.close()

    # ------------------------------------------------------------------
    # GET /api/operator/decisions/{id} - full evidence for one decision
    # ------------------------------------------------------------------
    @app.get("/api/operator/decisions/{decision_id}")
    def operator_decision_detail(decision_id: int) -> dict[str, Any]:
        con = _connect_ro()
        if con is None:
            return _err("RESOURCE_UNAVAILABLE", extra={"reason": "LEDGER_UNAVAILABLE"})
        try:
            con.row_factory = sqlite3.Row
            cols = [r[1] for r in con.execute("PRAGMA table_info(audit_signals)")]
            if not cols:
                return _err("RESOURCE_UNAVAILABLE", extra={"reason": "LEDGER_SCHEMA_UNAVAILABLE"})
            wanted = list(
                dict.fromkeys(
                    list(_SIGNAL_LIST_COLUMNS) + ["payload"] + list(_OPTIONAL_SIGNAL_COLUMNS)
                )
            )
            select_cols = [c for c in wanted if c in cols]
            row = con.execute(
                f"SELECT {', '.join(select_cols)} FROM audit_signals WHERE id = ?",
                (decision_id,),
            ).fetchone()
            if row is None:
                return _err("NOT_FOUND", extra={"reason": f"DECISION_NOT_FOUND: {decision_id}"})
            detail = _signal_row(
                row, [c for c in _OPTIONAL_SIGNAL_COLUMNS if c in cols], include_payload=True
            )
            detail["columns_not_recorded"] = [c for c in wanted if c not in cols]

            # Correlated dispatch rows: audit_orders.order_id stores the engine
            # request id (see idx_orders_ticket comment in audit_repository) -
            # that is the primary join key; execution_id (when the column
            # exists) is the fallback. The method is disclosed in the response
            # so the operator knows how the orders were matched.
            orders: list[dict[str, Any]] = []
            correlation = "REQUEST_ID_VIA_ORDER_ID"
            if detail.get("request_id"):
                try:
                    orders = [
                        dict(r)
                        for r in con.execute(
                            "SELECT id, ticket, order_id, symbol, action, price, stop_loss, "
                            "take_profit, volume, reason, latency, execution_mode, timestamp, execution_id "
                            "FROM audit_orders WHERE order_id = ? "
                            "ORDER BY id DESC LIMIT 20",
                            (detail["request_id"],),
                        )
                    ]
                except Exception:
                    orders = []
            if not orders and _has_column(con, "audit_orders", "execution_id"):
                correlation = "EXECUTION_ID"
                try:
                    orders = [
                        dict(r)
                        for r in con.execute(
                            "SELECT id, ticket, order_id, symbol, action, price, stop_loss, "
                            "take_profit, volume, reason, latency, execution_mode, timestamp, execution_id "
                            "FROM audit_orders WHERE execution_id = ? ORDER BY id DESC LIMIT 20",
                            (decision_id,),
                        )
                    ]
                except Exception:
                    orders = []
            detail["orders"] = orders
            detail["order_correlation"] = correlation
            return serialize_enums({"available": True, "decision": detail})
        except Exception as exc:
            _log_err(
                exc, "operator decision detail failed", endpoint="/api/operator/decisions/{id}"
            )
            return _err("INTERNAL_ERROR")
        finally:
            con.close()

    # ------------------------------------------------------------------
    # GET /api/operator/funnel - terminal-stage + gate distributions
    # ------------------------------------------------------------------
    @app.get("/api/operator/funnel")
    def operator_funnel(hours: float | None = None) -> dict[str, Any]:
        con = _connect_ro()
        if con is None:
            return _err("RESOURCE_UNAVAILABLE", extra={"reason": "LEDGER_UNAVAILABLE"})
        try:
            con.row_factory = sqlite3.Row
            window = _MAX_WINDOW
            ids = [
                r[0]
                for r in con.execute(
                    "SELECT id FROM audit_signals ORDER BY id DESC LIMIT ?", (window,)
                )
            ]
            if not ids:
                return serialize_enums(
                    {
                        "available": True,
                        "window": window,
                        "scanned_rows": 0,
                        "total": 0,
                        "stages": [],
                        "gates": [],
                        "actions": [],
                        "note": "TERMINAL distributions: the ledger records the final blocking stage per decision, not every interim pass.",
                    }
                )
            ph = ",".join("?" * len(ids))
            params: list[Any] = list(ids)
            time_clause = ""
            if hours is not None and hours > 0:
                time_clause = " AND generated_at >= datetime('now', ?)"
                params.append(f"-{float(hours)} hours")
            rows = con.execute(
                f"SELECT action, decision_stage, blocked_by, generated_at FROM audit_signals "
                f"WHERE id IN ({ph}){time_clause}",
                params,
            ).fetchall()
            stages = Counter((r["decision_stage"] or "NOT_RECORDED") for r in rows)
            gates = Counter((r["blocked_by"] or "NOT_BLOCKED") for r in rows)
            actions = Counter(r["action"] for r in rows)
            return serialize_enums(
                {
                    "available": True,
                    "window": window,
                    "scanned_rows": len(ids),
                    "total": len(rows),
                    "stages": [{"stage": k, "count": v} for k, v in stages.most_common()],
                    "gates": [{"gate": k, "count": v} for k, v in gates.most_common()],
                    "actions": [{"action": k, "count": v} for k, v in actions.most_common()],
                    "note": "TERMINAL distributions: the ledger records the final blocking stage per decision, not every interim pass.",
                }
            )
        except Exception as exc:
            _log_err(exc, "operator funnel failed", endpoint="/api/operator/funnel")
            return _err("INTERNAL_ERROR")
        finally:
            con.close()

    # ------------------------------------------------------------------
    # GET /api/operator/no-trade - NO_TRADE forensics
    # ------------------------------------------------------------------
    @app.get("/api/operator/no-trade")
    def operator_no_trade(hours: float | None = None, limit: int = 25) -> dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        con = _connect_ro()
        if con is None:
            return _err("RESOURCE_UNAVAILABLE", extra={"reason": "LEDGER_UNAVAILABLE"})
        try:
            con.row_factory = sqlite3.Row
            optional_cols = [
                c for c in _OPTIONAL_SIGNAL_COLUMNS if _has_column(con, "audit_signals", c)
            ]
            where = ["UPPER(action) = 'NO_TRADE'"]
            params: list[Any] = []
            if hours is not None and hours > 0:
                where.append("generated_at >= datetime('now', ?)")
                params.append(f"-{float(hours)} hours")
            clause = " AND ".join(where)
            total_rows = con.execute(
                f"SELECT COUNT(*) FROM audit_signals WHERE {clause}", params
            ).fetchone()[0]
            gates = Counter(
                (r[0] or "NOT_BLOCKED")
                for r in con.execute(f"SELECT blocked_by FROM audit_signals WHERE {clause}", params)
            )
            regimes = Counter(
                (r[0] or "NOT_RECORDED")
                for r in con.execute(f"SELECT regime FROM audit_signals WHERE {clause}", params)
            )
            reasons = Counter(
                (r[0] or "NOT_RECORDED")
                for r in con.execute(
                    f"SELECT reason_code FROM audit_signals WHERE {clause}", params
                )
            )
            # Hourly trend over the last 12 ledger hours (bounded buckets).
            trend = con.execute(
                f"SELECT substr(generated_at, 1, 13) AS hour_bucket, COUNT(*) AS n "
                f"FROM audit_signals WHERE {clause} "
                f"GROUP BY hour_bucket ORDER BY hour_bucket DESC LIMIT 12",
                params,
            ).fetchall()
            recent_rows = con.execute(
                "SELECT id, request_id, symbol, action, confidence, regime, generated_at, "
                "execution_mode, reason_code, decision_stage, blocked_by, payload"
                + (", " + ", ".join(optional_cols) if optional_cols else "")
                + f" FROM audit_signals WHERE {clause} ORDER BY id DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
            unresolved_direction = 0
            recent = []
            for r in recent_rows:
                item = _signal_row(r, optional_cols)
                parsed = item.pop("probabilities", {})
                model_action = (parsed.get("model_action") or "").upper()
                if "BUY" not in model_action and "SELL" not in model_action:
                    unresolved_direction += 1
                item["probabilities"] = parsed
                recent.append(item)
            return serialize_enums(
                {
                    "available": True,
                    "total": total_rows,
                    "gates": [{"gate": k, "count": v} for k, v in gates.most_common()],
                    "regimes": [{"regime": k, "count": v} for k, v in regimes.most_common()],
                    "reasons": [{"reason": k, "count": v} for k, v in reasons.most_common()],
                    "reasons_top_n": 10 if len(reasons) > 10 else len(reasons),
                    "hourly_trend": [{"hour": r["hour_bucket"], "count": r["n"]} for r in trend],
                    "model_direction_unresolved": unresolved_direction,
                    "model_direction_unresolved_note": (
                        "Rows where the recorded model_action carries no directional candidate (e.g. GUARDIAN rows where the model abstained). The counterfactual direction is NOT reconstructable - kept honest per TICK_COUNTERFACTUAL v1."
                    ),
                    "recent": recent,
                }
            )
        except Exception as exc:
            _log_err(exc, "operator no-trade failed", endpoint="/api/operator/no-trade")
            return _err("INTERNAL_ERROR")
        finally:
            con.close()

    # ------------------------------------------------------------------
    # GET /api/operator/orders - recent dispatch evidence + latency stats
    # ------------------------------------------------------------------
    @app.get("/api/operator/orders")
    def operator_orders(limit: int = 50) -> dict[str, Any]:
        limit = max(1, min(int(limit), 200))
        con = _connect_ro()
        if con is None:
            return _err("RESOURCE_UNAVAILABLE", extra={"reason": "LEDGER_UNAVAILABLE"})
        try:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT id, ticket, order_id, symbol, action, price, stop_loss, take_profit, "
                "volume, reason, latency, execution_mode, timestamp, execution_id "
                "FROM audit_orders ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            latencies = [r["latency"] for r in rows if isinstance(r["latency"], (int, float))]
            stats: dict[str, Any] | None = None
            if latencies:
                ordered = sorted(latencies)
                n = len(ordered)

                def pct(p: float) -> float:
                    return ordered[min(n - 1, int(round(p * (n - 1))))]

                stats = {
                    "n": n,
                    "p50_ms": pct(0.50),
                    "p95_ms": pct(0.95),
                    "p99_ms": pct(0.99),
                }
            return serialize_enums(
                {
                    "available": True,
                    "count": len(rows),
                    "latency": stats,
                    "rows": [dict(r) for r in rows],
                }
            )
        except Exception as exc:
            _log_err(exc, "operator orders failed", endpoint="/api/operator/orders")
            return _err("INTERNAL_ERROR")
        finally:
            con.close()
