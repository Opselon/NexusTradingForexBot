"""Forensic traces — the reusable "WHY" workflows (TASK-12 spec 38..43).

Each workflow is a read-only diagnostic procedure over audit.db / logs:

    why_blocked(ticket)   — why was this trade blocked?
    why_closed(ticket)    — why was this trade closed?
    why_no_learning(ticket) — why did this trade not enter learning?
    why_no_strategy()     — why is the strategy registry empty?
    why_ui_empty(field)   — why is this dashboard field empty?
    broker_ledger_divergence() — broker PnL vs ledger PnL (spec 13)
    clock_skew()          — timebase divergence (spec 14)
    split_fill_groups()   — one order, many tickets (spec 15)
    outcome_forensics()   — R=0 / reconstruction_source=NONE (spec 16)
    learning_pipeline_rates() — experience->outcome->research->candidate (spec 17)

NOTHING here mutates trading state, databases or configuration.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.incidents.trace")

#: Column sets used by the trace helpers (read-only projections).
_LEDGER_COLS = (
    "ticket, symbol, direction, volume, net_pnl_usd, exit_mechanism, "
    "exit_reason_source, exit_evidence, exit_reason_confidence, "
    "order_id, open_time, close_time, entry_reason, ai_confidence_at_open"
)
_BROKER_COLS = (
    "trade_id, position_id, symbol, direction, entry_time, exit_time, "
    "entry_price, exit_price, volume, gross_pnl, commission, swap, fee, "
    "net_pnl, deal_ids, order_ids, master_order_id, exit_reason, source"
)
_EXP_COLS = (
    "experience_id, request_id, execution_id, decision_id, idempotency_key, "
    "strategy_id, action, entry_reason, model_id"
)
_OUTCOME_COLS = (
    "id, idempotency_key, execution_id, is_executed, is_closed, exit_reason, "
    "realized_pnl_usd, realized_r_multiple, approved_volume, outcome_timestamp"
)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_rows(
    conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except sqlite3.Error as err:
        logger.debug("[INCIDENT_TRACE] query failed", error=str(err))
        return []


# ---------------------------------------------------------------------------
# Spec 13 — broker / ledger reconciliation
# ---------------------------------------------------------------------------


def broker_ledger_divergence(db_path: str, window_days: int = 90) -> dict[str, Any]:
    """Compares broker trade PnL vs ledger PnL (spec 13).

    Investigates ticket mapping / timezone / commission / partial fills.
    NEVER rewrites the ledger.
    """
    conn = _connect(db_path)
    try:
        brokers = _safe_rows(
            conn,
            f"SELECT {_BROKER_COLS} FROM audit_broker_trades "
            "WHERE exit_time != '' AND exit_time >= date('now', ?) ORDER BY exit_time DESC LIMIT 5000",
            (f"-{window_days} days",),
        )
        ledger = _safe_rows(
            conn,
            f"SELECT {_LEDGER_COLS} FROM audit_ledger ORDER BY close_time DESC LIMIT 5000",
        )
    finally:
        conn.close()

    ledger_by_ticket = {str(r.get("ticket")): r for r in ledger if r.get("ticket") is not None}
    mapped = 0
    mismatches: list[dict[str, Any]] = []
    for b in brokers:
        bid = str(b.get("trade_id") or "")
        pos_id = str(b.get("position_id") or "")
        lr = ledger_by_ticket.get(bid) or ledger_by_ticket.get(pos_id)
        if lr is None:
            continue
        mapped += 1
        bp = float(b.get("net_pnl") or 0.0)
        lp = float(lr.get("net_pnl_usd") or 0.0)
        if abs(bp - lp) > 0.01:
            mismatches.append(
                {
                    "ticket": bid,
                    "position_id": pos_id,
                    "broker_net_pnl": round(bp, 4),
                    "ledger_net_pnl": round(lp, 4),
                    "delta": round(bp - lp, 4),
                    "broker_exit_time": b.get("exit_time"),
                    "broker_source": b.get("source"),
                    "ledger_exit_mechanism": lr.get("exit_mechanism"),
                }
            )
    return {
        "checked_broker_trades": len(brokers),
        "mapped_to_ledger": mapped,
        "unmapped_broker_trades": len(brokers) - mapped,
        "pnl_divergences": mismatches,
        "divergence_count": len(mismatches),
        "note": "READ-ONLY — the ledger is never rewritten (spec 13).",
    }


# ---------------------------------------------------------------------------
# Spec 14 — clock / timezone forensics
# ---------------------------------------------------------------------------


def clock_skew(db_path: str) -> dict[str, Any]:
    """Measures observed timebase divergence (spec 14).

    Two DISTINCT measurements are reported so a stale-data age is never
    mistaken for a live clock bug (TIME-1):
      - sync_lag_seconds: host-UTC now minus the LATEST broker sync time
        (audit_broker_trades.synced_at). A large value = the sync worker
        has not run recently, NOT a clock defect.
      - observed_data_age_seconds: age of stored entry/exit timestamps
        (reported separately, never treated as clock skew).
    TIMEBASE_DIVERGENCE is raised ONLY on sync_lag > 300s (evidence: an
    unhealthy broker sync cadence).
    """
    conn = _connect(db_path)
    try:
        rows = _safe_rows(
            conn,
            "SELECT entry_time, exit_time, synced_at FROM audit_broker_trades "
            "WHERE entry_time != '' ORDER BY synced_at DESC LIMIT 500",
        )
        _safe_rows(
            conn,
            "SELECT * FROM audit_broker_history_meta ORDER BY rowid DESC LIMIT 10",
        )
    finally:
        conn.close()

    now_utc = datetime.now(UTC)
    sync_lags: list[float] = []
    data_ages: list[float] = []
    for r in rows:
        sync = _parse_ts(str(r.get("synced_at") or ""))
        if sync is not None:
            sync_lags.append((now_utc - sync).total_seconds())
        for col in ("entry_time", "exit_time"):
            ts = _parse_ts(str(r.get(col) or ""))
            if ts is not None:
                data_ages.append((now_utc - ts).total_seconds())
    sync_lag = sum(sync_lags) / len(sync_lags) if sync_lags else None
    data_age = sum(data_ages) / len(data_ages) if data_ages else None
    divergence = bool(sync_lag is not None and abs(sync_lag) > 300)
    return {
        "observed_skew_seconds": round(sync_lag, 1) if sync_lag is not None else None,
        "sync_lag_seconds": round(sync_lag, 1) if sync_lag is not None else None,
        "observed_data_age_seconds": round(data_age, 1) if data_age is not None else None,
        "samples": len(sync_lags),
        "host_now_utc": now_utc.isoformat(),
        "sample_window": (rows[0].get("synced_at") if rows else None),
        "divergence": "TIMEBASE_DIVERGENCE" if divergence else "IN_BOUNDS",
        "measurement": "sync_lag vs host UTC (data age reported separately; never treated as clock skew)",
        "note": "MT5 epochs are SERVER-LOCAL (broker GMT+3, BUG-070) — stored broker times must be normalized to UTC at sync; skew is measured, never assumed.",
    }


def _parse_ts(raw: str) -> datetime | None:
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y.%m.%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
    ):
        try:
            return datetime.strptime(str(raw)[:23].strip(), fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Spec 15 — split-fill incident correlation
# ---------------------------------------------------------------------------


def split_fill_groups(db_path: str, limit: int = 50) -> dict[str, Any]:
    """Finds one economic order -> multiple broker tickets (spec 15).

    Group key: broker_trades.master_order_id (project execution identity).
    Sibling fills missing propagation context are flagged
    CONTEXT_PROPAGATION_FAILURE.
    """
    conn = _connect(db_path)
    try:
        rows = _safe_rows(
            conn,
            f"SELECT {_BROKER_COLS} FROM audit_broker_trades "
            "WHERE master_order_id != '' AND master_order_id IS NOT NULL "
            "AND master_order_id != '0' "
            "ORDER BY master_order_id, trade_id LIMIT 10000",
        )
    finally:
        conn.close()

    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        key = str(r.get("master_order_id"))
        if not key or key in ("0", "None"):
            continue
        groups.setdefault(key, []).append(r)
    families = [g for g in groups.values() if len(g) > 1]
    return {
        "split_fill_families": len(families),
        "tickets_in_families": sum(len(g) for g in families),
        "families": [
            {
                "master_order_id": g[0]["master_order_id"],
                "tickets": [t["trade_id"] for t in g],
                "deal_ids": [t.get("deal_ids") for t in g],
                "direction": g[0]["direction"],
                "symbol": g[0]["symbol"],
            }
            for g in families[:limit]
        ],
    }


# ---------------------------------------------------------------------------
# Spec 16 — outcome forensics
# ---------------------------------------------------------------------------


def outcome_forensics(db_path: str, limit: int = 200) -> dict[str, Any]:
    """SUSPECT_OUTCOME detection (spec 16): realized_r=0 / profit=0 /
    reconstruction_source=NONE — was real broker PnL available?
    """
    conn = _connect(db_path)
    try:
        # outcomes table is audit_experience_outcomes; reconstruction source
        # lives in the payload/columns when a reconstruction ran.
        outcomes = _safe_rows(
            conn,
            f"SELECT {_OUTCOME_COLS}, payload FROM audit_experience_outcomes "
            "ORDER BY outcome_timestamp DESC LIMIT ?",
            (limit,),
        )
    finally:
        conn.close()

    suspect: list[dict[str, Any]] = []
    zero_total = 0
    for o in outcomes:
        r = float(o.get("realized_r_multiple") or 0.0)
        p = float(o.get("realized_pnl_usd") or 0.0)
        rec_src = str(o.get("reconstruction_source") or "")
        payload = o.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                payload = {}
        rec_src = rec_src or str(payload.get("reconstruction_source") or "NONE")
        if abs(r) < 1e-9 and abs(p) < 1e-9:
            zero_total += 1
            suspect.append(
                {
                    "idempotency_key": o.get("idempotency_key"),
                    "execution_id": o.get("execution_id"),
                    "outcome_timestamp": o.get("outcome_timestamp"),
                    "realized_r": r,
                    "profit_usd": p,
                    "reconstruction_source": rec_src,
                    "classification": "SUSPECT_OUTCOME"
                    if rec_src == "NONE"
                    else "ZERO_WITH_SOURCE",
                    "exit_reason": o.get("exit_reason"),
                }
            )
    return {
        "checked_outcomes": len(outcomes),
        "zero_realized_outcomes": zero_total,
        "suspect_outcomes": [s for s in suspect if s["classification"] == "SUSPECT_OUTCOME"],
        "zero_with_source": [s for s in suspect if s["classification"] == "ZERO_WITH_SOURCE"],
        "note": "SUSPECT records are reported, never silently rewritten (spec 16).",
    }


# ---------------------------------------------------------------------------
# Spec 17 — learning pipeline loss
# ---------------------------------------------------------------------------


def learning_pipeline_rates(db_path: str) -> dict[str, Any]:
    """experience_to_outcome_rate / outcome_to_research_rate /
    research_to_candidate_rate + sudden-drop detection (spec 17)."""
    conn = _connect(db_path)
    try:
        experiences = _safe_rows(
            conn,
            "SELECT COUNT(*) AS n FROM audit_experiences",
        )
        outcomes = _safe_rows(conn, "SELECT COUNT(*) AS n FROM audit_experience_outcomes")
        research_samples = _safe_rows(conn, "SELECT COUNT(*) AS n FROM research_runs")
        candidates = _safe_rows(conn, "SELECT COUNT(*) AS n FROM strategy_registry")
    finally:
        conn.close()

    n_exp = int(experiences[0]["n"]) if experiences else 0
    n_out = int(outcomes[0]["n"]) if outcomes else 0
    n_res = int(research_samples[0]["n"]) if research_samples else 0
    n_cand = int(candidates[0]["n"]) if candidates else 0

    exp_to_out = n_out / n_exp if n_exp else None
    out_to_res = n_res / n_out if n_out else None
    res_to_cand = n_cand / n_res if n_res else None

    flags: list[str] = []
    # Bounded expectations: historical production norms (2026-08 evidence).
    if exp_to_out is not None and exp_to_out < 0.25 and n_exp >= 40:
        flags.append("LEARNING_DATA_LOSS")
    if out_to_res is not None and out_to_res < 0.05 and n_out >= 40:
        flags.append("OUTCOME_TO_RESEARCH_DROP")
    if res_to_cand is not None and res_to_cand < 0.05 and n_res >= 5:
        flags.append("RESEARCH_TO_CANDIDATE_DROP")
    return {
        "experiences": n_exp,
        "outcomes": n_out,
        "research_samples": n_res,
        "candidates": n_cand,
        "experience_to_outcome_rate": round(exp_to_out, 4) if exp_to_out is not None else None,
        "outcome_to_research_rate": round(out_to_res, 4) if out_to_res is not None else None,
        "research_to_candidate_rate": round(res_to_cand, 4) if res_to_cand is not None else None,
        "flags": flags,
        "note": "Rates are evidence-based; thresholds are documented baselines, not fixed assumptions.",
    }


# ---------------------------------------------------------------------------
# Spec 39..43 — "WHY" workflows
# ---------------------------------------------------------------------------


def why_blocked(db_path: str, ticket: str | int) -> dict[str, Any]:
    """Why was this trade blocked? (spec 39). Diagnostic only."""
    conn = _connect(db_path)
    try:
        signals = _safe_rows(
            conn,
            "SELECT * FROM audit_signals WHERE ticket=? OR payload LIKE ? "
            "ORDER BY timestamp DESC LIMIT 20",
            (str(ticket), f"%{ticket}%"),
        )
        guard = _safe_rows(
            conn,
            "SELECT * FROM audit_guard_telemetry WHERE symbol IN "
            "(SELECT symbol FROM audit_ledger WHERE ticket=?) "
            "ORDER BY window_start DESC LIMIT 20",
            (str(ticket),),
        )
    finally:
        conn.close()
    reasons = [s.get("rejection_reason") for s in signals if s.get("rejection_reason")]
    return {
        "ticket": str(ticket),
        "signal_rows": len(signals),
        "rejection_reasons": reasons[:10],
        "guard_telemetry_rows": len(guard),
        "blocked_by": (reasons[0] if reasons else "UNKNOWN"),
    }


def why_closed(db_path: str, ticket: str | int) -> dict[str, Any]:
    """Why was this trade closed? (spec 40). Diagnostic only."""
    conn = _connect(db_path)
    try:
        ledger = _safe_rows(
            conn,
            f"SELECT {_LEDGER_COLS} FROM audit_ledger WHERE ticket=? OR order_id=?",
            (str(ticket), str(ticket)),
        )
        lifecycle = _safe_rows(
            conn,
            "SELECT * FROM position_lifecycle_events WHERE ticket=? OR trade_id=? "
            "ORDER BY event_timestamp LIMIT 200",
            (str(ticket), str(ticket)),
        )
        autopsy = _safe_rows(
            conn,
            "SELECT * FROM trade_autopsies WHERE ticket=? OR trade_id=? LIMIT 5",
            (str(ticket), str(ticket)),
        )
    finally:
        conn.close()
    entry = ledger[0] if ledger else {}
    exit_events = [
        e
        for e in lifecycle
        if "EXIT" in str(e.get("event_type", "")).upper()
        or "CLOSE" in str(e.get("event_type", "")).upper()
    ]
    return {
        "ticket": str(ticket),
        "ledger_row": entry,
        "exit_mechanism": entry.get("exit_mechanism"),
        "exit_evidence": entry.get("exit_evidence"),
        "exit_reason_source": entry.get("exit_reason_source"),
        "final_net_pnl": entry.get("net_pnl_usd"),
        "lifecycle_exit_events": exit_events[-10:],
        "autopsy": autopsy[0] if autopsy else None,
        "conclusion": (
            f"CLOSED via {entry.get('exit_mechanism')} "
            f"(evidence: {entry.get('exit_reason_source')})"
            if entry.get("exit_mechanism")
            else "UNKNOWN"
        ),
    }


def why_no_learning(db_path: str, ticket: str | int) -> dict[str, Any]:
    """Why did this trade not enter learning? (spec 41)."""
    conn = _connect(db_path)
    try:
        experiences = _safe_rows(
            conn,
            f"SELECT {_EXP_COLS} FROM audit_experiences "
            "WHERE execution_id=? OR request_id=? OR idempotency_key=?",
            (str(ticket), str(ticket), str(ticket)),
        )
        outcomes = _safe_rows(
            conn,
            "SELECT * FROM audit_experience_outcomes WHERE execution_id=? OR idempotency_key=?",
            (str(ticket), str(ticket)),
        )
        corrections = _safe_rows(
            conn,
            "SELECT * FROM audit_experience_corrections WHERE execution_id=? OR idempotency_key=?",
            (str(ticket), str(ticket)),
        )
    finally:
        conn.close()
    exp = experiences[0] if experiences else None
    return {
        "ticket": str(ticket),
        "experience": exp,
        "has_experience": exp is not None,
        "has_outcome": len(outcomes) > 0,
        "outcomes": len(outcomes),
        "corrections": len(corrections),
        "request_id": (exp or {}).get("request_id"),
        "conclusion": (
            "NO_EXPERIENCE_RECORD"
            if exp is None
            else ("LEARNING_ENTERED" if outcomes else "NO_OUTCOME_YET")
        ),
    }


def why_no_strategy(db_path: str) -> dict[str, Any]:
    """Why is the strategy registry empty? (spec 42).

    Inspects broker outcomes -> realized R -> dataset -> families ->
    discovery gates -> candidate rejection -> registry.
    """
    conn = _connect(db_path)
    try:
        registry = _safe_rows(
            conn,
            "SELECT strategy_id, lifecycle, score, sample_count, discovery_source "
            "FROM strategy_registry ORDER BY updated_at DESC LIMIT 100",
        )
        candidates = _safe_rows(
            conn,
            "SELECT strategy_id, lifecycle_state, sample_count, confidence_score "
            "FROM strategy_intelligence_registry ORDER BY updated_at DESC LIMIT 100",
        )
        outcomes = _safe_rows(conn, "SELECT COUNT(*) AS n FROM audit_experience_outcomes")
        research = _safe_rows(
            conn,
            "SELECT id, run_id, dataset_id, result_summary FROM research_runs ORDER BY id DESC LIMIT 10",
        )
    finally:
        conn.close()
    return {
        "registry_rows": registry,
        "registry_count": len(registry),
        "intelligence_registry_count": len(candidates),
        "outcome_count": int(outcomes[0]["n"]) if outcomes else 0,
        "recent_research_runs": research,
        "diagnosis": (
            "REGISTRY_POPULATED"
            if registry
            else (
                "NO_VALIDATED_CANDIDATE"
                if not candidates
                else "CANDIDATES_PRESENT_BUT_NOT_PROMOTED"
            )
        ),
    }


def why_ui_empty(db_path: str, field: str = "strategies") -> dict[str, Any]:
    """Why is this dashboard field empty? (spec 43).

    Distinguishes: backend empty / backend failed / API failed / JS failed /
    DOM missing / stale bundle.
    """
    conn = _connect(db_path)
    backend_count: int | None = None
    try:
        if field == "strategies":
            backend_count = int(
                conn.execute("SELECT COUNT(*) FROM strategy_registry").fetchone()[0]
            )
        elif field == "news":
            backend_count = int(conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
        elif field in ("trades", "ledger"):
            backend_count = int(conn.execute("SELECT COUNT(*) FROM audit_ledger").fetchone()[0])
        elif field == "experiences":
            backend_count = int(
                conn.execute("SELECT COUNT(*) FROM audit_experiences").fetchone()[0]
            )
    except sqlite3.Error:
        backend_count = None
    finally:
        conn.close()
    return {
        "field": field,
        "backend_record_count": backend_count,
        "diagnosis": (
            "BACKEND_EMPTY"
            if backend_count == 0
            else ("BACKEND_HAS_DATA" if backend_count else "BACKEND_UNAVAILABLE")
        ),
        "note": "If backend has data but UI shows EMPTY: check API -> JS loader -> renderer / stale bundle.",
    }


# ---------------------------------------------------------------------------
# Spec 20 — News incidents
# ---------------------------------------------------------------------------


def news_incidents(news_db_path: str) -> dict[str, Any]:
    """News source-health + all-neutral detection (spec 20).

    Distinguishes: source empty (no articles) vs parser broken (articles but
    all NEUTRAL) vs healthy. Read-only.
    """
    conn = _connect(news_db_path)
    try:
        sources = _safe_rows(conn, "SELECT * FROM news_health")
        articles_n = 0
        try:
            articles_n = int(conn.execute("SELECT COUNT(*) FROM news_articles").fetchone()[0])
        except sqlite3.Error:
            articles_n = 0
        dir_rows = []
        try:
            dir_rows = _safe_rows(
                conn,
                "SELECT direction, COUNT(*) AS n FROM news_analysis "
                "JOIN news_articles a ON a.article_id = news_analysis.article_id "
                "WHERE a.published_at >= date('now', '-2 days') "
                "GROUP BY direction",
            )
        except sqlite3.Error:
            pass
    finally:
        conn.close()
    counts = {str(r.get("direction") or "?"): int(r.get("n") or 0) for r in dir_rows}
    total_recent = sum(counts.values())
    neutral_recent = int(counts.get("NEUTRAL", 0))
    unhealthy = [s for s in sources if not bool(s.get("healthy"))]
    findings: list[str] = []
    if articles_n == 0:
        findings.append("NEWS_SOURCE_EMPTY")
    if unhealthy:
        findings.append("NEWS_SOURCE_UNHEALTHY")
    if total_recent >= 20 and neutral_recent / total_recent >= 0.9:
        findings.append("NEWS_ALL_NEUTRAL")
    return {
        "source_rows": len(sources),
        "unhealthy_sources": [s.get("source_id") for s in unhealthy],
        "recent_articles": total_recent,
        "recent_neutral": neutral_recent,
        "recent_direction_counts": counts,
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Spec 22 — package / version incidents
# ---------------------------------------------------------------------------


def version_consistency(workspace: str) -> dict[str, Any]:
    """Detects VERSION_INCONSISTENCY (spec 22): backend version vs web bundle
    vs model schema vs runtime migration schema. Exact observed values only.
    """
    import json as _json
    from pathlib import Path as _Path

    base = _Path(workspace)
    observed: dict[str, Any] = {
        "backend_version": "",
        "web_bundle_stamp": "",
        "audit_schema_version": "",
    }
    for cand in (
        base / "artifacts" / "build-info.json",
        base / "artifacts" / "logs" / "build-info.json",
    ):
        if cand.exists():
            try:
                observed["backend_version"] = _json.loads(cand.read_text(encoding="utf-8")).get(
                    "version", ""
                )
            except (ValueError, OSError):
                pass
            break
    js = base / "Web" / "app.js"
    if js.exists():
        head = js.read_text(encoding="utf-8", errors="ignore")[:800]
        import re as _re

        m = _re.search(r"(?:version|bundle)[^0-9]{0,20}([0-9]+\.[0-9]+\.[0-9]+)", head, _re.I)
        if m:
            observed["web_bundle_stamp"] = m.group(1)
    try:
        conn = _connect(str(base / "artifacts" / "audit.db"))
        try:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
            observed["audit_schema_version"] = str(row[0]) if row else ""
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    mismatch: list[str] = []
    if observed["backend_version"] and observed["web_bundle_stamp"]:
        if observed["backend_version"] != observed["web_bundle_stamp"]:
            mismatch.append("backend_web_version_mismatch")
    return {
        "observed": observed,
        "version_mismatches": mismatch,
        "finding": "VERSION_INCONSISTENCY" if mismatch else "VERSIONS_CONSISTENT",
    }


__all__ = [
    "broker_ledger_divergence",
    "clock_skew",
    "learning_pipeline_rates",
    "news_incidents",
    "outcome_forensics",
    "split_fill_groups",
    "version_consistency",
    "why_blocked",
    "why_closed",
    "why_no_learning",
    "why_no_strategy",
    "why_ui_empty",
]
