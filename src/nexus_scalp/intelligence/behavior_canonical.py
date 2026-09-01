"""Canonical batch behavior/anomaly analysis pipeline.

Extracted VERBATIM from intelligence/behavior.py (Agent-5 modularization,
behavior-preserving). Owns: analyze_canonical_trades (the batch driver
used by the intelligence worker), evidence-coverage accounting, the
deterministic anomaly builders (_trade_data_anomalies,
_duplicate_outcome_anomalies) and the behavior_analysis/anomaly_events
persist helpers + their idempotency keys.

BOUNDARY: consumes the detection engine ONLY through engine.analyze() and
engine.persist() — no detector internals.

USED BY: intelligence/worker.py, tests (via the behavior facade).
"""

from __future__ import annotations

import hashlib
import json
import statistics
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.intelligence.behavior_detect import (
    EXCESSIVE_HOLD_MIN_SAMPLE,
    INSERT_ANALYSIS_SQL,
    INSERT_ANOMALY_SQL,
    BehaviorDetectionEngine,
    _json_default,
    _jsonable,
)
from nexus_scalp.intelligence.models import (
    AnomalyEvent,
    BehaviorAnalysis,
    BehaviorDetection,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.intelligence.behavior_canonical")


def _build_analysis_key(ticket: str, behavior_version: str, anomaly_version: str) -> str:
    raw = f"{ticket}|{behavior_version}|{anomaly_version}"
    return f"ana_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _coverage_fields(record: Any, trade: Any) -> tuple[float, int, int]:
    """
    Evidence-coverage estimate for one trade.

    `complete_context` counts the fields a behavioral analysis actually needs;
    `partial_context` counts fields that exist but are sparse/zero. Coverage is
    complete/(complete+partial) — a zero-flag result with 100% coverage means
    something very different from zero flags at 20% coverage (task §16).
    """
    complete = 0
    partial = 0
    checks: list[bool] = [
        bool(getattr(trade, "mae_points", 0.0) or getattr(trade, "mae_usd", 0.0)),
        bool(getattr(trade, "mfe_points", 0.0) or getattr(trade, "mfe_usd", 0.0)),
        getattr(trade, "realized_r", None) is not None,
        getattr(trade, "risk_usd", None) is not None,
        getattr(trade, "duration_sec", 0.0) > 0.0,
        bool(getattr(trade, "exit_mechanism_raw", "")),
        getattr(trade, "closed_at", None) is not None,
    ]
    complete = sum(1 for c in checks if c)
    partial = len(checks) - complete
    coverage = complete / len(checks) if checks else 0.0
    return round(coverage, 4), complete, partial


def analyze_canonical_trades(
    audit_repo: AuditRepository,
    engine: BehaviorDetectionEngine,
    behavior_version: str = "behavior-v1",
    anomaly_version: str = "anomaly-v1",
    max_trades: int = 200,
) -> dict[str, Any]:
    """
    Runs the detector set over canonical closed trades and persists derived
    records. This is the offline/background path (NEVER on the tick hot path).

    Idempotency: records key on (ticket, behavior_version, anomaly_version);
    a ticket already analyzed under these versions is skipped.

    Returns a summary dict: analyzed / skipped / flags / anomalies / coverage.
    """
    from nexus_scalp.accounting.normalize import normalize_trade_row

    if not audit_repo._is_sqlite:
        return {"analyzed": 0, "skipped": 0, "flags": 0, "anomalies": 0, "coverage": 0.0}

    import sqlite3

    conn = None
    try:
        conn = sqlite3.connect(audit_repo._db_path, timeout=5.0)
        conn.row_factory = None

        # Existing analysis keys under these versions (idempotency set).
        done_rows = conn.execute(
            "SELECT analysis_key, ticket FROM behavior_analysis "
            "WHERE behavior_version = ? AND anomaly_version = ?",
            (behavior_version, anomaly_version),
        ).fetchall()
        done_tickets = {str(r[1]) for r in done_rows}

        rows = conn.execute(
            "SELECT * FROM audit_ledger WHERE status != 'OPENED' "
            "AND close_time != '' ORDER BY close_time DESC LIMIT ?",
            (max_trades,),
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM audit_ledger LIMIT 0").description]
    finally:
        if conn is not None:
            conn.close()

    analyzed = 0
    skipped = 0
    flags_total = 0
    anomalies_total = 0
    coverage_sum = 0.0

    for raw in rows:
        row = dict(zip(cols, raw, strict=False))
        ticket = str(row.get("ticket", ""))
        if not ticket or ticket in done_tickets:
            skipped += 1
            continue
        trade = normalize_trade_row(row)

        # Robust strategy baseline for EXCESSIVE_HOLD_TIME.
        baseline = _strategy_hold_baseline(audit_repo, trade.strategy_id, ticket)

        coverage, complete_n, partial_n = _coverage_fields(None, trade)
        detections: list[BehaviorDetection] = []
        anomalies: list[AnomalyEvent] = []

        mfe_r = float(trade.mfe_r or 0.0)
        mae_r = float(trade.mae_r or 0.0)
        realized_r = float(trade.realized_r or 0.0)
        giveback = _giveback_fraction(trade)
        detections = engine.analyze(
            ticket=ticket,
            realized_r=realized_r,
            mfe_r=mfe_r,
            mae_r=mae_r,
            giveback_pct=giveback,
            holding_duration_sec=float(trade.duration_sec or 0.0),
            expected_duration_sec=baseline["median"] if baseline["median"] else 600.0,
            exit_mechanism=trade.exit_mechanism_raw or "UNKNOWN",
            sl_moved=bool(trade.was_sl_modified),
            actual_risk_usd=float(trade.risk_usd or 0.0) if trade.risk_usd else None,
            intended_risk_usd=None,
            strategy_baseline_median_sec=baseline["median"],
            strategy_baseline_mad_sec=baseline["mad"],
            strategy_baseline_hold_sec=baseline["mean"],
        )

        # -- data/context anomalies for this trade ------------------------
        anomalies.extend(_trade_data_anomalies(trade, ticket, anomaly_version))

        for det in detections:
            engine.persist(det)
        flags_total += len(detections)

        for anomaly in anomalies:
            _persist_anomaly(audit_repo, anomaly, anomaly_version)
        anomalies_total += len(anomalies)

        analysis = BehaviorAnalysis(
            ticket=ticket,
            symbol=trade.symbol,
            strategy_id=trade.strategy_id,
            behavior_version=behavior_version,
            anomaly_version=anomaly_version,
            evidence_coverage=coverage,
            complete_context=complete_n,
            partial_context=partial_n,
            flags=[_jsonable(d) for d in detections],
            anomalies=[_jsonable(a) for a in anomalies],
        )
        _persist_analysis(audit_repo, analysis)
        analyzed += 1
        coverage_sum += coverage

    # -- batch-level anomalies: duplicate economic outcomes ----------------
    dup_anomalies = _duplicate_outcome_anomalies(audit_repo, anomaly_version)
    for anomaly in dup_anomalies:
        _persist_anomaly(audit_repo, anomaly, anomaly_version)
    anomalies_total += len(dup_anomalies)

    # Deterministic batch semantics: drain the async audit queue so the
    # caller can observe persisted records immediately after this returns.
    # This is the OFFLINE path (never the tick hot path) — a bounded join is
    # safe and keeps idempotency checks truthful.
    try:
        audit_repo._queue.join()
    except Exception:
        pass

    return {
        "analyzed": analyzed,
        "skipped": skipped,
        "flags": flags_total,
        "anomalies": anomalies_total,
        "coverage": round(coverage_sum / analyzed, 4) if analyzed else 0.0,
    }


def _giveback_fraction(trade: Any) -> float:
    """MFE -> realized surrender fraction, 0..1 (0 when unknown)."""
    mfe_usd = abs(float(getattr(trade, "mfe_usd", 0.0) or 0.0))
    net = float(getattr(trade, "net_pnl", 0.0) or 0.0)
    if mfe_usd <= 1e-9:
        return 0.0
    if net >= mfe_usd:
        return 0.0
    return max(0.0, min(1.0, (mfe_usd - net) / mfe_usd))


def _strategy_hold_baseline(
    audit_repo: AuditRepository, strategy_id: str, exclude_ticket: str
) -> dict[str, float | None]:
    """Robust per-strategy hold-duration baseline (median + MAD)."""
    if not strategy_id:
        return {"median": None, "mad": None, "mean": None}
    try:
        from nexus_scalp.experience import ExperienceLedger

        ledger = ExperienceLedger(audit_repo=audit_repo)
        records = ledger.get_experiences_for_strategy(strategy_id=strategy_id, limit=500)
        durations = [
            float(getattr(r, "holding_duration_seconds", 0.0) or 0.0)
            for r in records
            if float(getattr(r, "holding_duration_seconds", 0.0) or 0.0) > 0.0
        ]
    except Exception:
        durations = []
    if len(durations) < EXCESSIVE_HOLD_MIN_SAMPLE:
        return {"median": None, "mad": None, "mean": None}
    median = float(statistics.median(durations))
    mad = float(statistics.median([abs(d - median) for d in durations]))
    return {
        "median": median,
        "mad": mad,
        "mean": float(statistics.fmean(durations)),
    }


def _trade_data_anomalies(trade: Any, ticket: str, anomaly_version: str) -> list[AnomalyEvent]:
    """Objective data-inconsistency anomalies for one canonical trade.

    ANOMALY-VERIFY-01: every anomaly id is DETERMINISTIC
    (ticket | type | algorithm_version), matching the batch duplicate detector.
    The same underlying incident on the same trade under the same detector
    version can never produce an unlimited number of rows (TEST-ANOM-14/15).
    """
    out: list[AnomalyEvent] = []

    def _aid(anomaly_type: str) -> str:
        return _duplicate_anomaly_id(ticket, anomaly_type, anomaly_version)

    # STRATEGY_CONTEXT_LOSS — closed trade without strategy attribution.
    if not (trade.strategy_id or trade.entry_reason):
        out.append(
            AnomalyEvent(
                anomaly_id=_aid("STRATEGY_CONTEXT_LOSS"),
                ticket=ticket,
                anomaly_type="STRATEGY_CONTEXT_LOSS",
                category="DATA",
                severity="MEDIUM",
                confidence=0.7,
                evidence={
                    "explanation": "closed trade carries no strategy attribution",
                    "threshold": {"strategy_id": "present"},
                    "actual": {"strategy_id": ""},
                    "expected": {"strategy_id": "present"},
                    "algorithm_version": anomaly_version,
                },
            )
        )

    # EXIT_CLASSIFICATION_ANOMALY — risk-free claim without SL modification.
    mech = (trade.exit_mechanism_raw or "").upper()
    if mech in ("RISK_FREE_SL_HIT", "BREAK_EVEN_SL_HIT") and not trade.was_sl_modified:
        out.append(
            AnomalyEvent(
                anomaly_id=_aid("EXIT_CLASSIFICATION_ANOMALY"),
                ticket=ticket,
                anomaly_type="EXIT_CLASSIFICATION_ANOMALY",
                category="EXECUTION",
                severity="MEDIUM",
                confidence=0.9,
                evidence={
                    "explanation": "exit recorded as risk-free/breakeven while "
                    "was_sl_modified=false",
                    "threshold": {"sl_modified": True},
                    "actual": {"sl_modified": False, "exit_mechanism": mech},
                    "expected": {"sl_modified": True},
                    "algorithm_version": anomaly_version,
                },
            )
        )

    # IMPOSSIBLE_EXCURSION — MAE/MFE signs contradict the direction.
    direction = (trade.direction or "").upper()
    mae = float(getattr(trade, "mae_points", 0.0) or 0.0)
    mfe = float(getattr(trade, "mfe_points", 0.0) or 0.0)
    if direction == "BUY" and mae > 0.0:
        out.append(
            AnomalyEvent(
                anomaly_id=_aid("IMPOSSIBLE_EXCURSION"),
                ticket=ticket,
                anomaly_type="IMPOSSIBLE_EXCURSION",
                category="DATA",
                severity="LOW",
                confidence=0.8,
                evidence={
                    "explanation": "BUY trade records positive MAE (adverse excursion "
                    "must be <= 0)",
                    "actual": {"mae_points": mae},
                    "expected": {"mae_points": "<= 0"},
                    "algorithm_version": anomaly_version,
                },
            )
        )
    if direction == "SELL" and mfe < 0.0:
        out.append(
            AnomalyEvent(
                anomaly_id=_aid("IMPOSSIBLE_EXCURSION"),
                ticket=ticket,
                anomaly_type="IMPOSSIBLE_EXCURSION",
                category="DATA",
                severity="LOW",
                confidence=0.8,
                evidence={
                    "explanation": "SELL trade records negative MFE (favourable "
                    "excursion must be >= 0)",
                    "actual": {"mfe_points": mfe},
                    "expected": {"mfe_points": ">= 0"},
                    "algorithm_version": anomaly_version,
                },
            )
        )

    # IMPOSSIBLE_TIMESTAMP — closed before opened.
    if (
        trade.opened_at is not None
        and trade.closed_at is not None
        and trade.closed_at < trade.opened_at
    ):
        out.append(
            AnomalyEvent(
                anomaly_id=_aid("IMPOSSIBLE_TIMESTAMP"),
                ticket=ticket,
                anomaly_type="IMPOSSIBLE_TIMESTAMP",
                category="DATA",
                severity="LOW",
                confidence=0.9,
                evidence={
                    "explanation": "close timestamp precedes open timestamp",
                    "actual": {"closed_at": trade.closed_at.isoformat()},
                    "expected": {"closed_at": ">= opened_at"},
                    "algorithm_version": anomaly_version,
                },
            )
        )
    return out


def _duplicate_outcome_anomalies(
    audit_repo: AuditRepository, anomaly_version: str
) -> list[AnomalyEvent]:
    """Batch-level DATA anomaly: two closed outcomes for one execution_id.

    Idempotent: the anomaly_id is deterministic for (execution_id, type,
    version), and executions already flagged under this version are skipped.
    """
    import sqlite3

    out: list[AnomalyEvent] = []
    try:
        conn = sqlite3.connect(audit_repo._db_path, timeout=5.0)
        try:
            rows = conn.execute(
                "SELECT execution_id, COUNT(*) c, "
                "MIN(realized_pnl_usd) min_pnl, MAX(realized_pnl_usd) max_pnl "
                "FROM audit_experience_outcomes WHERE is_closed = 1 "
                "GROUP BY execution_id HAVING c > 1"
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:
        logger.error("[BEHAVIOR] duplicate-outcome scan failed (isolated)", error=str(e))
        return out

    # Skip executions already flagged under THIS anomaly version (idempotency).
    try:
        conn = sqlite3.connect(audit_repo._db_path, timeout=5.0)
        try:
            existing = {
                str(r[0])
                for r in conn.execute(
                    "SELECT anomaly_id FROM anomaly_events WHERE algorithm_version = ?",
                    (anomaly_version,),
                ).fetchall()
            }
        finally:
            conn.close()
    except Exception:
        existing = set()

    for execution_id, count, min_pnl, max_pnl in rows:
        delta = abs(float(max_pnl or 0.0) - float(min_pnl or 0.0))
        if delta > 1e-9:
            anomaly_id = _duplicate_anomaly_id(
                str(execution_id), "DUPLICATE_ECONOMIC_OUTCOME", anomaly_version
            )
            if anomaly_id in existing:
                continue
            out.append(
                AnomalyEvent(
                    anomaly_id=anomaly_id,
                    ticket=str(execution_id),
                    anomaly_type="DUPLICATE_ECONOMIC_OUTCOME",
                    category="DATA",
                    severity="CRITICAL",
                    confidence=0.95,
                    evidence={
                        "explanation": "multiple closed ledger outcomes exist for one "
                        "economic trade with different realized PnL",
                        "threshold": {"outcomes_per_execution": 1},
                        "actual": {"outcome_count": int(count), "pnl_delta": round(delta, 2)},
                        "expected": {"outcome_count": 1},
                        "algorithm_version": anomaly_version,
                    },
                )
            )
    return out


def _duplicate_anomaly_id(ticket: str, anomaly_type: str, version: str) -> str:
    """Deterministic anomaly id: (ticket, type, version) -> stable key."""
    raw = f"{ticket}|{anomaly_type}|{version}"
    return f"ano_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _persist_analysis(audit_repo: AuditRepository, analysis: BehaviorAnalysis) -> bool:
    """Idempotent analysis-record persistence (ON CONFLICT DO NOTHING)."""
    if not audit_repo._is_sqlite:
        return False
    key = _build_analysis_key(analysis.ticket, analysis.behavior_version, analysis.anomaly_version)
    args = (
        key,
        analysis.ticket,
        analysis.symbol,
        analysis.strategy_id,
        analysis.behavior_version,
        analysis.anomaly_version,
        analysis.analyzed_at.isoformat(),
        analysis.evidence_coverage,
        analysis.complete_context,
        analysis.partial_context,
        json.dumps(analysis.flags, default=_json_default),
        json.dumps(analysis.anomalies, default=_json_default),
    )
    try:
        audit_repo._queue.put_nowait((INSERT_ANALYSIS_SQL, args))
        return True
    except Exception as e:
        logger.error("[BEHAVIOR] analysis persist failed (isolated)", error=str(e))
        return False


def _persist_anomaly(audit_repo: AuditRepository, anomaly: AnomalyEvent, version: str) -> bool:
    """Idempotent anomaly-event persistence."""
    if not audit_repo._is_sqlite:
        return False
    args = (
        anomaly.anomaly_id,
        anomaly.ticket,
        anomaly.anomaly_type,
        anomaly.category,
        anomaly.severity,
        anomaly.confidence,
        json.dumps(anomaly.evidence, default=_json_default),
        anomaly.detected_at.isoformat(),
        version,
    )
    try:
        audit_repo._queue.put_nowait((INSERT_ANOMALY_SQL, args))
        return True
    except Exception as e:
        logger.error("[BEHAVIOR] anomaly persist failed (isolated)", error=str(e))
        return False
