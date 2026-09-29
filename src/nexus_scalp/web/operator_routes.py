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
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends

from nexus_scalp.web.auth import require_web_auth

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

#: P1: columns the summary census needs, in dependency order. Only the ones
#: the ledger actually has are projected (an older audit DB may predate
#: ``decision_stage`` / ``blocked_by``), so the bounded read stays valid
#: across schema generations.
_SUMMARY_COLUMNS = ("action", "generated_at", "decision_stage", "blocked_by")

#: P1 (scan-amplification fix): the summary census window. The pre-fix value
#: was 20000, which is LARGER than the whole 7-day-retention ledger (~9,108
#: rows live), so the "bound" was vacuous — the ``WHERE id IN (LIMIT 20000)``
#: semi-join covered the entire table on every 15s poll and PostgreSQL
#: correctly seq-scanned it. The census feeds a summary strip that renders a
#: recent action distribution; a 2000-row latest-N slice is a representative
#: sample at every realistic decision rate, and the bounded ``ORDER BY id
#: DESC LIMIT n`` shape is index-served (it reads the table's tail, never the
#: history). ``scanned_rows`` in the response discloses the actual count.
_SUMMARY_WINDOW = 2000

#: P1: the funnel's terminal-stage distribution window (same defect as the
#: summary; the funnel's pre-fix window was 50000 — 5x the whole table).
_FUNNEL_WINDOW = 2000

#: P1: columns the funnel distribution needs. Adapted to the ledger's actual
#: schema the same way the summary projection is.
_FUNNEL_COLUMNS = ("action", "generated_at", "decision_stage", "blocked_by")

#: P1 + R-3: the NO_TRADE forensics window. The pre-fix endpoint ran five
#: separate unbounded ``WHERE action='NO_TRADE'`` queries; NO_TRADE is ~92% of
#: rows, so every one was a full scan. P1 collapsed them into one bounded tail
#: fetch, but left the majority predicate INSIDE the bounded query (matrix
#: R-3): ``WHERE UPPER(action) = 'NO_TRADE' ORDER BY id DESC LIMIT n`` selects
#: ~92% of the ledger, so no index can serve it and the LIMIT is unreachable -
#: the measured plan is a Seq Scan of the whole table.
#: OUTSIDE the slice (the shape the sibling ``get_decision_stats`` already
#: uses, measured O(1) in ledger size by L5 at 10K→10M rows): one bare
#: ``ORDER BY id DESC LIMIT n`` primary-key tail read, every predicate
#: evaluated in Python.
_NO_TRADE_WINDOW = 2000

#: R-3: oversampling factor for the NO_TRADE tail read. The pre-fix shape
#: returned "the newest ``_NO_TRADE_WINDOW`` NO_TRADE rows"; filtering AFTER a
#: bare primary-key tail read reproduces that exactly whenever NO_TRADE makes
#: up at least ``1/_NO_TRADE_SCAN_FACTOR`` of the recent tail - and the
#: measured NO_TRADE share is 92-94.6% (matrix R-3 / DO-NOT-CHANGE evidence),
#: so a factor of 2 clears the 50% threshold with 42 points of margin while
#: keeping the read a constant ``_NO_TRADE_WINDOW * 2`` rows. A larger factor
#: would buy nothing at the measured mix and would multiply the payload bytes
#: fetched per 15 s poll; a factor of 1 would silently shrink ``total`` from
#: 2,000 to ~1,840. Adaptive re-scanning until ``window`` NO_TRADE rows are
#: collected is deliberately NOT used: it is unbounded in a low-NO_TRADE tail,
#: which is the pathology this route exists to remove.
_NO_TRADE_SCAN_FACTOR = 2


def _audit_db_path() -> str | None:
    """Authoritative audit DB path (workspace-anchored, BUG-149 semantics)."""
    try:
        from nexus_scalp.database.provider import default_sqlite_path

        return default_sqlite_path("audit")
    except Exception:
        return None


#: Ledger reader abstraction.
#:
#: DB-LIFECYCLE (R2 / provider split): the operator surface used to open a raw
#: ``sqlite3.connect(f"file:{path}?mode=ro")`` handle to the audit DB. That
#: bypass is *structurally* provider-blind: on a box whose settings DB carries
#: ``database.provider=postgresql`` the engine writes audit rows to PostgreSQL
#: while this route kept reading the SQLite file, so every summary/funnel/
#: NO_TRADE statistic rendered from a stale snapshot and the two surfaces of
#: one fact disagreed forever (measured: PG newest row 68.9 min old, SQLite
#: 89.3 h old at probe time).
#:
#: The fix routes the read through the fabric's provider-aware read plane
#: (``provider_store.query_rows`` — same path the audit repository's own read
#: surface uses) instead of a private connection. Read-only is preserved by
#: construction: ``query_rows`` issues the caller's SELECT through the READ
#: pool, and this module still issues no statement that can mutate.
class LedgerReader:
    """Provider-aware, read-only window onto the decision ledger.

    Two access shapes, both bounded:

    * ``columns(table)`` — the projection adaptation the pre-fix code did via
      ``PRAGMA table_info`` on a raw SQLite handle. Translated to the
      portable catalog probe so it works on PostgreSQL too.
    * ``select(proj, table, order_by, limit)`` — the bounded tail fetch
      (``ORDER BY id DESC LIMIT n``) every P1 census uses.
    """

    def __init__(self, repo: Any | None = None) -> None:
        self._repo = repo
        self._sqlite_fallback_path: str | None = None
        self._provider = self._resolve_provider()

    def _resolve_provider(self) -> str:
        """'sqlite' | 'postgresql', from the ACTIVE config (never a guess)."""
        try:
            from nexus_scalp.database.config import load_database_config

            cfg = load_database_config("audit")
            return "postgresql" if cfg.is_postgresql else "sqlite"
        except Exception:
            return "sqlite"

    # -- connection handling -------------------------------------------------
    def _sqlite_con(self) -> sqlite3.Connection | None:
        """Read-only SQLite handle — ONLY used when the provider is SQLite."""
        path = self._sqlite_fallback_path
        if path is None:
            path = _audit_db_path()
        if not path:
            return None
        try:
            return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
        except Exception:
            return None

    def _rows(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """One bounded read on the ACTIVE provider; [] on unavailable.

        SQLite keeps the zero-dependency read-only handle (no repository
        construction, no pool — the audit DB is a local file); PostgreSQL
        goes through the fabric's read pool via the repository.
        """
        if self._provider != "postgresql":
            con = self._sqlite_con()
            if con is None:
                return []
            try:
                con.row_factory = sqlite3.Row
                with con:
                    return [dict(r) for r in con.execute(sql, tuple(args)).fetchall()]
            except Exception:
                return []
            finally:
                with _Suppress():
                    con.close()
        from nexus_scalp.adapters.database.provider_store import query_rows

        return query_rows(self._repo, sql, args, operation="operator_routes")

    # -- public API ----------------------------------------------------------
    def columns(self, table: str) -> set[str]:
        """Columns the table actually has (empty set when unavailable).

        Replaces the raw ``PRAGMA table_info`` probe with a portable catalog
        read so the projection adaptation survives the provider switch.
        """
        if self._provider != "postgresql":
            con = self._sqlite_con()
            if con is None:
                return set()
            try:
                return {str(r[1]) for r in con.execute(f"PRAGMA table_info({table})")}
            except Exception:
                return set()
            finally:
                with _Suppress():
                    con.close()
        rows = self._rows(
            "SELECT column_name FROM information_schema.columns "
            "WHERE LOWER(table_name) = LOWER(%s)",
            (table,),
        )
        return {str(r["column_name"]) for r in rows}

    def select(
        self,
        proj: str,
        table: str,
        limit: int,
        *,
        where: str = "",
        args: tuple[Any, ...] = (),
        order_by: str = "id DESC",
    ) -> list[dict[str, Any]]:
        """Bounded read: ``SELECT proj FROM table [where] ORDER BY order_by LIMIT n``."""
        clause = f" WHERE {where}" if where else ""
        sql = f"SELECT {proj} FROM {table}{clause} ORDER BY {order_by} LIMIT ?"
        return self._rows(sql, (*args, limit))

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def available(self) -> bool:
        """True when the ledger is reachable at all (for honest warnings)."""
        return bool(self._rows("SELECT 1 AS v", ()))


def _ledger_reader() -> LedgerReader:
    """A provider-aware ledger reader for one request.

    Constructed per call (never a module-level singleton): under PostgreSQL
    the reader borrows the fabric's read pool through a lazily-built audit
    repository, and a shared instance would outlive re-provisioning.
    """
    repo: Any | None = None
    try:
        from nexus_scalp.web.diagnostics_state_routes import _audit_repository

        repo = _audit_repository()
    except Exception:
        repo = None
    return LedgerReader(repo=repo)


class _Suppress:
    """Suppress-and-forget context manager (close paths never raise out)."""

    def __enter__(self) -> _Suppress:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return True


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
    row: Mapping[str, Any],
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
    @app.get("/api/operator/summary", dependencies=[Depends(require_web_auth)])
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
        ledger = _ledger_reader()
        if not ledger.available:
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
            # P1 (scan-amplification fix): this used to be a two-step
            # ``SELECT id ... LIMIT 20000`` followed by ``WHERE id IN (...)``,
            # a semi-join whose window (20000) is LARGER than the table
            # (~9,108 rows over 7 days), so the window covered the whole
            # table on every call and the planner correctly seq-scanned it.
            # The summary only needs a bounded recent census, so the window
            # is now a real bound, and the census is read in ONE query
            # (no IN-list round-trip, no second pass over the same rows).
            # The projection adapts to the columns the ledger actually has
            # (an older audit DB may predate decision_stage/blocked_by) so
            # the bounded read stays valid across schema generations.
            cols = ledger.columns("audit_signals")
            avail = [c for c in _SUMMARY_COLUMNS if c in cols]
            proj = ", ".join(avail)
            window = _SUMMARY_WINDOW
            rows = ledger.select(proj, "audit_signals", window)
            scanned = len(rows)
            stats: dict[str, Any] = {"scanned_rows": scanned, "window": window}
            if scanned:
                actions = Counter(r["action"] for r in rows)
                stats["actions"] = dict(actions)
                stats["total"] = scanned
                # LATEST, not first-returned: the rows come back newest-first
                # (ORDER BY id DESC), but this stays robust to any row order —
                # take the max over the ledger's own timestamps (never the
                # wall clock). The pre-fix code read rows[0] as "latest" while
                # the IN-list fetch was unordered and returned the OLDEST row
                # of the window.
                stats["latest_decision_at"] = max(
                    (r["generated_at"] for r in rows if r["generated_at"] is not None),
                    default=None,
                )
                # 1h recency split from the same fetched rows (no re-query).
                cutoff_1h = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
                stats["actions_1h"] = dict(
                    Counter(r["action"] for r in rows if (r["generated_at"] or "") >= cutoff_1h)
                )
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
        return serialize_enums(summary)

    # ------------------------------------------------------------------
    # GET /api/operator/decisions - filterable decision history
    # ------------------------------------------------------------------
    @app.get("/api/operator/decisions", dependencies=[Depends(require_web_auth)])
    def operator_decisions(
        hours: float | None = None,
        action: str | None = None,
        gate: str | None = None,
        search: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), _MAX_LIMIT))
        ledger = _ledger_reader()
        if not ledger.available:
            return _err("RESOURCE_UNAVAILABLE", extra={"reason": "LEDGER_UNAVAILABLE"})
        try:
            cols = ledger.columns("audit_signals")
            optional_cols = [c for c in _OPTIONAL_SIGNAL_COLUMNS if c in cols]
            where: list[str] = []
            params: list[Any] = []
            if hours is not None and hours > 0:
                # Filter on the ledger's own ISO-8601 timestamp. The old form
                # used SQLite's ``datetime('now', ?)`` — a provider-only
                # function that PostgreSQL does not have; the cutoff is now
                # computed once in the application and compared as a string,
                # which is portable across both providers (generated_at is a
                # TEXT/ISO column on both).
                cutoff = (datetime.now(UTC) - timedelta(hours=float(hours))).isoformat()
                where.append("generated_at >= ?")
                params.append(cutoff)
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
            clause = " AND ".join(where)
            proj = (
                "id, request_id, symbol, action, confidence, regime, generated_at, "
                "execution_mode, reason_code, decision_stage, blocked_by, payload"
                + (", " + ", ".join(optional_cols) if optional_cols else "")
            )
            rows = ledger.select(proj, "audit_signals", limit, where=clause, args=tuple(params))
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

    # ------------------------------------------------------------------
    # GET /api/operator/decisions/{id} - full evidence for one decision
    # ------------------------------------------------------------------
    @app.get("/api/operator/decisions/{decision_id}", dependencies=[Depends(require_web_auth)])
    def operator_decision_detail(decision_id: int) -> dict[str, Any]:
        ledger = _ledger_reader()
        if not ledger.available:
            return _err("RESOURCE_UNAVAILABLE", extra={"reason": "LEDGER_UNAVAILABLE"})
        try:
            cols = ledger.columns("audit_signals")
            if not cols:
                return _err("RESOURCE_UNAVAILABLE", extra={"reason": "LEDGER_SCHEMA_UNAVAILABLE"})
            wanted = list(
                dict.fromkeys(
                    [*list(_SIGNAL_LIST_COLUMNS), "payload", *list(_OPTIONAL_SIGNAL_COLUMNS)]
                )
            )
            select_cols = [c for c in wanted if c in cols]
            found = ledger._rows(
                f"SELECT {', '.join(select_cols)} FROM audit_signals WHERE id = ?",
                (decision_id,),
            )
            row = found[0] if found else None
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
            order_proj = (
                "id, ticket, order_id, symbol, action, price, stop_loss, "
                "take_profit, volume, reason, latency, execution_mode, timestamp, execution_id"
            )
            if detail.get("request_id"):
                orders = ledger._rows(
                    f"SELECT {order_proj} FROM audit_orders WHERE order_id = ? "
                    "ORDER BY id DESC LIMIT 20",
                    (detail["request_id"],),
                )
            if not orders and "execution_id" in ledger.columns("audit_orders"):
                correlation = "EXECUTION_ID"
                orders = ledger._rows(
                    f"SELECT {order_proj} FROM audit_orders WHERE execution_id = ? "
                    "ORDER BY id DESC LIMIT 20",
                    (decision_id,),
                )
            detail["orders"] = orders
            detail["order_correlation"] = correlation
            return serialize_enums({"available": True, "decision": detail})
        except Exception as exc:
            _log_err(
                exc, "operator decision detail failed", endpoint="/api/operator/decisions/{id}"
            )
            return _err("INTERNAL_ERROR")

    # ------------------------------------------------------------------
    # GET /api/operator/funnel - terminal-stage + gate distributions
    # ------------------------------------------------------------------
    @app.get("/api/operator/funnel", dependencies=[Depends(require_web_auth)])
    def operator_funnel(hours: float | None = None) -> dict[str, Any]:
        ledger = _ledger_reader()
        if not ledger.available:
            return _err("RESOURCE_UNAVAILABLE", extra={"reason": "LEDGER_UNAVAILABLE"})
        try:
            # P1: same vacuous-window defect as the summary — the pre-fix code
            # fetched ``LIMIT 50000`` ids then ``WHERE id IN (...)``: a window
            # 5x larger than the whole table, so it semi-joined the entire
            # ledger on every call. One bounded query reads the same tail.
            avail = [c for c in _FUNNEL_COLUMNS if c in ledger.columns("audit_signals")]
            proj = ", ".join(avail)
            window = _FUNNEL_WINDOW
            rows = ledger.select(proj, "audit_signals", window)
            if not rows:
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
            # Time filter is applied AFTER the bounded fetch (in Python) so the
            # query stays a bounded tail read; a ``generated_at >= ?`` in SQL
            # would select ~100% of a 7-day table and the planner seq-scans.
            if hours is not None and hours > 0:
                cutoff = (datetime.now(UTC) - timedelta(hours=float(hours))).isoformat()
                rows = [r for r in rows if (r["generated_at"] or "") >= cutoff]
            stages = Counter((r["decision_stage"] or "NOT_RECORDED") for r in rows)
            gates = Counter((r["blocked_by"] or "NOT_BLOCKED") for r in rows)
            actions = Counter(r["action"] for r in rows)
            return serialize_enums(
                {
                    "available": True,
                    "window": window,
                    "scanned_rows": len(rows),
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

    # ------------------------------------------------------------------
    # GET /api/operator/no-trade - NO_TRADE forensics
    # ------------------------------------------------------------------
    @app.get("/api/operator/no-trade", dependencies=[Depends(require_web_auth)])
    def operator_no_trade(hours: float | None = None, limit: int = 25) -> dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        ledger = _ledger_reader()
        if not ledger.available:
            return _err("RESOURCE_UNAVAILABLE", extra={"reason": "LEDGER_UNAVAILABLE"})
        try:
            optional_cols = [
                c for c in _OPTIONAL_SIGNAL_COLUMNS if c in ledger.columns("audit_signals")
            ]
            # P1: the pre-fix code ran FIVE separate full-table queries here
            # (COUNT + blocked_by + regime + reason_code + hourly GROUP BY),
            # each ``WHERE action='NO_TRADE'`` — a predicate selecting ~92% of
            # rows, so the planner seq-scanned the whole table five times per
            # call. No index can serve a majority predicate. The panel renders
            # distributions over recent history, so one bounded latest-N fetch
            # of the NO_TRADE tail supplies every distribution, and the hourly
            # trend is derived from the rows already in memory.
            #
            # R-3 (matrix): P1 left the majority predicate INSIDE the bounded
            # query, so the LIMIT never bound — ``ORDER BY id DESC LIMIT n``
            # with ``WHERE UPPER(action)='NO_TRADE'`` is still a plan that must
            # consider ~92% of rows (measured: Seq Scan over the whole table,
            # 8,313 rows examined to return 2,000). The filter is now applied
            # OUTSIDE the slice, which is exactly the shape the sibling
            # ``get_decision_stats`` uses and which L5 measured O(1) in ledger
            #: size (SQLite median 1.41->1.22 ms across 10K->10M rows, ratio
            #: <=1.11x; the bound reads 2,000 of ~9.7M rows at the top scale
            # point). The scan is per-call constant again.
            window = _NO_TRADE_WINDOW
            proj = (
                "id, request_id, symbol, action, confidence, regime, generated_at, "
                "execution_mode, reason_code, decision_stage, blocked_by, payload"
                + (", " + ", ".join(optional_cols) if optional_cols else "")
            )
            # Bare primary-key tail read: ``is no-trade`` is evaluated in
            # Python below, never in SQL, so no majority predicate can reach
            # the planner. ``_NO_TRADE_SCAN_FACTOR`` bounds the oversample so
            # the slice can still be filled with ``window`` NO_TRADE rows when
            # the tail is mixed.
            scan = window * _NO_TRADE_SCAN_FACTOR
            fetched = ledger.select(proj, "audit_signals", scan)
            # The ledger's ``action`` is a plain-text column; the pre-fix
            # predicate was ``UPPER(action) = 'NO_TRADE'`` (case-insensitive),
            # so the Python filter mirrors that comparison exactly.
            rows = [r for r in fetched if str(r.get("action") or "").upper() == "NO_TRADE"][:window]
            if hours is not None and hours > 0:
                cutoff = (datetime.now(UTC) - timedelta(hours=float(hours))).isoformat()
                rows = [r for r in rows if (r["generated_at"] or "") >= cutoff]
            total_rows = len(rows)
            gates = Counter((r["blocked_by"] or "NOT_BLOCKED") for r in rows)
            regimes = Counter((r["regime"] or "NOT_RECORDED") for r in rows)
            reasons = Counter((r["reason_code"] or "NOT_RECORDED") for r in rows)
            # Hourly trend over the fetched rows (bounded buckets, no re-query).
            buckets: Counter[str] = Counter()
            for r in rows:
                ts = r["generated_at"] or ""
                if len(ts) >= 13:
                    buckets[ts[:13]] += 1
            trend = sorted(buckets.items(), key=lambda kv: kv[0], reverse=True)[:12]
            recent_rows = rows[:limit]
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
                    "window": window,
                    "scanned_rows": total_rows,
                    "gates": [{"gate": k, "count": v} for k, v in gates.most_common()],
                    "regimes": [{"regime": k, "count": v} for k, v in regimes.most_common()],
                    "reasons": [{"reason": k, "count": v} for k, v in reasons.most_common()],
                    "reasons_top_n": 10 if len(reasons) > 10 else len(reasons),
                    "hourly_trend": [{"hour": h, "count": n} for h, n in trend],
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

    # ------------------------------------------------------------------
    # GET /api/operator/orders - recent dispatch evidence + latency stats
    # ------------------------------------------------------------------
    @app.get("/api/operator/orders", dependencies=[Depends(require_web_auth)])
    def operator_orders(limit: int = 50) -> dict[str, Any]:
        limit = max(1, min(int(limit), 200))
        ledger = _ledger_reader()
        if not ledger.available:
            return _err("RESOURCE_UNAVAILABLE", extra={"reason": "LEDGER_UNAVAILABLE"})
        try:
            rows = ledger.select(
                "id, ticket, order_id, symbol, action, price, stop_loss, take_profit, "
                "volume, reason, latency, execution_mode, timestamp, execution_id",
                "audit_orders",
                limit,
            )
            latencies = [r["latency"] for r in rows if isinstance(r["latency"], (int, float))]
            stats: dict[str, Any] | None = None
            if latencies:
                ordered = sorted(latencies)
                n = len(ordered)

                def pct(p: float) -> float:
                    return ordered[min(n - 1, round(p * (n - 1)))]

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
