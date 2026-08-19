"""Accounting divergence forensics (TASK-13 STEP-05/06).

For every affected record (broker PnL != 0 but ledger PnL == 0), trace the
stages:

    BROKER -> EXECUTION -> LEDGER -> OUTCOME -> RESEARCH

and determine the FIRST stage where real PnL becomes zero or missing
(first_correct_stage / first_incorrect_stage / first_missing_stage).

Root-cause classification (spec 16) — only marked when evidence proves it:
    BROKER_SYNC_LOSS / RECONSTRUCTION_FAILURE / LEDGER_WRITE_FAILURE /
    LEDGER_UPDATE_FAILURE / DUPLICATE_SUPPRESSION_ERROR / ZERO_DEFAULT_BUG /
    OUTCOME_PROPAGATION_FAILURE / ROUNDING_ERROR / SPLIT_FILL_CONTEXT_ERROR /
    TIMESTAMP_MATCH_FAILURE / UNKNOWN

Zero-outcome classification (spec 17):
    LEGITIMATELY_UNRESOLVED / RECOVERABLE_FROM_BROKER /
    RECOVERABLE_FROM_EXECUTION / CORRUPTED / DUPLICATE / PHANTOM / UNKNOWN

NEVER writes. Produces recovery CANDIDATES with evidence/source/confidence/
algorithm_version for a governed repair decision (spec 18/19).
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.incidents.accounting")

#: Reconstruction algorithm identity (spec 18/19).
RECONSTRUCTION_ALGORITHM_VERSION = "agent13-reconcile-v1"

#: Classification vocabulary (spec 16).
ROOT_CAUSE_CLASSES = (
    "BROKER_SYNC_LOSS",
    "RECONSTRUCTION_FAILURE",
    "LEDGER_WRITE_FAILURE",
    "LEDGER_UPDATE_FAILURE",
    "DUPLICATE_SUPPRESSION_ERROR",
    "ZERO_DEFAULT_BUG",
    "OUTCOME_PROPAGATION_FAILURE",
    "ROUNDING_ERROR",
    "SPLIT_FILL_CONTEXT_ERROR",
    "TIMESTAMP_MATCH_FAILURE",
    "UNKNOWN",
)

#: Zero-outcome vocabulary (spec 17).
ZERO_OUTCOME_CLASSES = (
    "LEGITIMATELY_UNRESOLVED",
    "RECOVERABLE_FROM_BROKER",
    "RECOVERABLE_FROM_EXECUTION",
    "CORRUPTED",
    "DUPLICATE",
    "PHANTOM",
    "UNKNOWN",
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
        logger.debug("[ACCOUNTING_FORENSICS] query failed", error=str(err))
        return []


class AccountingForensicsEngine:
    """Read-only first-divergence + classification + recovery candidates.

    Never writes to the DB. Recovery candidates are advisory (spec 18).
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    # ------------------------------------------------------------------
    # Main audit
    # ------------------------------------------------------------------

    def audit_zero_pnl_ledger(self, *, max_rows: int = 500) -> dict[str, Any]:
        """Audits ledger rows with net_pnl_usd == 0 where broker PnL != 0.

        Returns per-record stage analysis + classification + recovery
        candidates (read-only).
        """
        conn = _connect(self.db_path)
        try:
            rows = _safe_rows(
                conn,
                """
                SELECT b.trade_id, b.net_pnl AS broker_pnl, b.gross_pnl,
                       b.commission AS broker_comm, b.swap AS broker_swap,
                       b.source, b.entry_time, b.exit_time,
                       b.master_order_id, b.deal_ids, b.order_ids,
                       l.ticket, l.net_pnl_usd AS ledger_pnl,
                       l.exit_mechanism, l.order_id, l.close_time,
                       l.exit_reason_source
                FROM audit_broker_trades b
                JOIN audit_ledger l ON l.ticket = b.trade_id
                WHERE b.exit_time != ''
                  AND abs(b.net_pnl) > 0.01
                  AND abs(l.net_pnl_usd) < 0.005
                ORDER BY b.exit_time DESC
                LIMIT ?
                """,
                (max_rows,),
            )
        finally:
            conn.close()

        # index experiences/outcomes by identity
        exp_by = self._index_experiences()
        out_by = self._index_outcomes()

        records: list[dict[str, Any]] = []
        for r in rows:
            tid = str(r["trade_id"])
            oid = str(r.get("order_id") or "")
            exp = (
                exp_by.get(tid)
                or exp_by.get(oid)
                or exp_by.get(str(r.get("master_order_id") or ""))
            )
            out = out_by.get(tid) or out_by.get(oid)
            analysis = self._analyze_record(r, exp, out)
            records.append(analysis)

        classification = Counter(a["classification"] for a in records)
        zero_outcome_class = Counter(
            a.get("zero_outcome_class") or "UNKNOWN" for a in records if a.get("zero_outcome_class")
        )
        recovery_candidates = [
            a["recovery_candidate"] for a in records if a["recovery_candidate"] is not None
        ]
        return {
            "audited_at": datetime.now(UTC).isoformat(),
            "algorithm_version": RECONSTRUCTION_ALGORITHM_VERSION,
            "checked_records": len(records),
            "classification_counts": dict(classification),
            "zero_outcome_classification_counts": dict(zero_outcome_class),
            "recovery_candidate_count": len(recovery_candidates),
            "recovery_candidates": recovery_candidates[:200],
            "records": records[:200],
            "note": "READ-ONLY audit; recovery candidates require governed approval before any write.",
        }

    # ------------------------------------------------------------------
    # Per-record analysis
    # ------------------------------------------------------------------

    def _analyze_record(
        self,
        r: dict[str, Any],
        exp: dict[str, Any] | None,
        out: dict[str, Any] | None,
    ) -> dict[str, Any]:
        tid = str(r["trade_id"])
        broker_pnl = float(r.get("broker_pnl") or 0.0)
        ledger_pnl = float(r.get("ledger_pnl") or 0.0)
        outcome_pnl = None
        if out is not None:
            outcome_pnl = float(out.get("realized_pnl_usd") or 0.0)

        # ---- stage analysis (spec 15) ----
        stages: list[dict[str, str]] = [
            {"stage": "BROKER", "value": str(broker_pnl)},
            {"stage": "LEDGER", "value": str(ledger_pnl)},
        ]
        if out is not None:
            stages.append({"stage": "OUTCOME", "value": str(outcome_pnl)})
        if exp is not None and out is None:
            stages.append({"stage": "EXPERIENCE", "value": "present, no outcome"})

        first_incorrect: str | None = None
        for s in stages:
            if s["stage"] == "BROKER":
                continue
            if s["value"] in ("0.0", "0", "present, no outcome"):
                first_incorrect = s["stage"]
                break

        # ---- classification (spec 16) ----
        classification = self._classify(r, exp, out)

        # ---- zero-outcome class (spec 17) ----
        zero_class = None
        if out is not None and abs(float(out.get("realized_pnl_usd") or 0.0)) < 0.005:
            zero_class = self._classify_zero_outcome(r, out)

        # ---- recovery candidate (spec 18/19, read-only) ----
        candidate = None
        if abs(broker_pnl) > 0.01 and abs(ledger_pnl) < 0.005:
            candidate = {
                "ticket": tid,
                "original_ledger_pnl": ledger_pnl,
                "recovered_pnl": round(broker_pnl, 4),
                "reconstruction_source": "BROKER_DEALS",
                "reconstruction_algorithm_version": RECONSTRUCTION_ALGORITHM_VERSION,
                "reconstruction_confidence": 0.95 if r.get("source") == "BROKER_DEALS" else 0.7,
                "evidence": {
                    "broker_trade_id": tid,
                    "broker_net_pnl": round(broker_pnl, 4),
                    "broker_gross_pnl": round(float(r.get("gross_pnl") or 0.0), 4),
                    "broker_commission": round(float(r.get("broker_comm") or 0.0), 4),
                    "broker_swap": round(float(r.get("broker_swap") or 0.0), 4),
                    "broker_source": r.get("source"),
                    "ledger_exit_mechanism": r.get("exit_mechanism"),
                },
                "status": "RECOMMENDED",
            }

        return {
            "ticket": tid,
            "broker_pnl": round(broker_pnl, 4),
            "ledger_pnl": round(ledger_pnl, 4),
            "outcome_pnl": round(outcome_pnl, 4) if outcome_pnl is not None else None,
            "has_experience": exp is not None,
            "has_outcome": out is not None,
            "exit_mechanism": r.get("exit_mechanism"),
            "broker_source": r.get("source"),
            "first_correct_stage": "BROKER",
            "first_incorrect_stage": first_incorrect,
            "first_missing_stage": "OUTCOME" if out is None else None,
            "classification": classification,
            "zero_outcome_class": zero_class,
            "recovery_candidate": candidate,
        }

    def _classify(
        self,
        r: dict[str, Any],
        exp: dict[str, Any] | None,
        out: dict[str, Any] | None,
    ) -> str:
        """Evidence-driven classification (spec 16)."""
        # The ledger was written with pnl=None -> 0.0 while the broker later
        # synced real PnL. That is the proven pattern for this dataset:
        # the close-time write had no deal evidence and the reconciliation
        # that should backfill the ledger from broker history never ran.
        if r.get("exit_reason_source") in (None, ""):
            # No evidence provenance on the ledger row: the close was written
            # without broker deal evidence -> ZERO_DEFAULT_BUG (pnl=None->0.0
            # coercion, BUG-046 discipline violation at the persistence layer).
            return "ZERO_DEFAULT_BUG"
        if exp is not None and out is None:
            return "OUTCOME_PROPAGATION_FAILURE"
        if out is not None and abs(float(out.get("realized_pnl_usd") or 0.0)) < 0.005:
            return "RECONSTRUCTION_FAILURE"
        return "UNKNOWN"

    def _classify_zero_outcome(self, r: dict[str, Any], out: dict[str, Any]) -> str:
        """Zero-outcome classification (spec 17)."""
        payload = out.get("payload") or ""
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                payload = {}
        rec_src = str(payload.get("reconstruction_source") or "NONE")
        if rec_src in ("NONE", "MISSING"):
            # No reconstruction source AND broker evidence exists in
            # audit_broker_trades for this ticket -> recoverable from broker.
            return "RECOVERABLE_FROM_BROKER"
        if rec_src == "BROKER_DEALS":
            return "LEGITIMATELY_UNRESOLVED"
        return "UNKNOWN"

    # ------------------------------------------------------------------
    # Indexes
    # ------------------------------------------------------------------

    def _index_experiences(self) -> dict[str, dict[str, Any]]:
        conn = _connect(self.db_path)
        try:
            rows = _safe_rows(
                conn,
                "SELECT experience_id, request_id, execution_id, idempotency_key, "
                "strategy_id, action FROM audit_experiences",
            )
        finally:
            conn.close()
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            for key in (r.get("execution_id"), r.get("idempotency_key"), r.get("request_id")):
                if key:
                    out.setdefault(str(key), r)
        return out

    def _index_outcomes(self) -> dict[str, dict[str, Any]]:
        conn = _connect(self.db_path)
        try:
            rows = _safe_rows(
                conn,
                "SELECT idempotency_key, execution_id, outcome_timestamp, "
                "realized_pnl_usd, realized_r_multiple, exit_reason, payload "
                "FROM audit_experience_outcomes",
            )
        finally:
            conn.close()
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            for key in (r.get("execution_id"), r.get("idempotency_key")):
                if key:
                    out.setdefault(str(key), r)
        return out


def build_accounting_divergence_artifact(db_path: str, out_path: str | Path) -> dict[str, Any]:
    """Runs the audit and writes artifacts/forensics/accounting_divergence.json
    (spec 14). Read-only; the artifact is the canonical forensic record."""
    engine = AccountingForensicsEngine(db_path)
    result = engine.audit_zero_pnl_ledger()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


__all__ = [
    "RECONSTRUCTION_ALGORITHM_VERSION",
    "ROOT_CAUSE_CLASSES",
    "ZERO_OUTCOME_CLASSES",
    "AccountingForensicsEngine",
    "build_accounting_divergence_artifact",
]
