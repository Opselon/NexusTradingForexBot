"""
BUG-046 Historical Outcome Repair
==================================
Identifies past closed-trade outcomes that were corrupted by the 1-hour deal
lookup bug (realized_r=0 / reconstruction_source NONE despite a real broker
close), re-queries broker deal history over a lifecycle-bounded window, and
repairs ONLY the derived outcome layer through `ExperienceLedger.repair_outcome`.

Invariants:
  * The immutable decision row in `audit_experiences` is NEVER modified.
  * Repair is IDEMPOTENT: running it twice converges (same key -> same value),
    never duplicates rows, never double-counts PnL.
  * Repair is BOUNDED: per-call caps on candidates and broker queries.
  * Every repaired outcome carries repair provenance in its payload.
  * When broker truth is still unavailable, the outcome is left as-is and
    reported UNREPAIRED (never silently zeroed again).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    ExperienceOutcome,
    ExperienceRecord,
)
from nexus_scalp.experience.outcome_recovery import reconstruct_broker_outcome
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.experience.outcome_repair")

#: Hard cap on candidates per repair pass (bounded background work).
MAX_REPAIR_CANDIDATES: int = 200
#: Default broker history window (hours) when entry time is unknown.
DEFAULT_HISTORY_HOURS: int = 24 * 3


#: A zero-R outcome is only a repair candidate when it has a broker ticket and
#: no authoritative reconstruction.
def _is_zero_outcome(realized_r: float, realized_pnl: float) -> bool:
    return abs(float(realized_r or 0.0)) < 1e-12 and abs(float(realized_pnl or 0.0)) < 1e-9


@dataclass
class OutcomeRepairResult:
    """Aggregate result of one repair pass."""

    candidates: int = 0
    repaired: int = 0
    unrepaired: int = 0
    skipped_no_broker: int = 0
    repaired_rows: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": self.candidates,
            "repaired": self.repaired,
            "unrepaired": self.unrepaired,
            "skipped_no_broker": self.skipped_no_broker,
            "repaired_rows": self.repaired_rows,
        }


class OutcomeRepairJob:
    """
    Idempotent, bounded, observable repair of zero-R closed outcomes.

    `broker_deals_fn(ticket, hours_back) -> list[dict]` is the broker history
    accessor (injected so tests can fake deals and the caller can pass the
    real MT5 adapter).
    """

    def __init__(
        self,
        ledger: ExperienceLedger,
        broker_deals_fn: Callable[[int, int], list[dict[str, Any]]],
        max_candidates: int = MAX_REPAIR_CANDIDATES,
    ) -> None:
        self.ledger = ledger
        self.broker_deals_fn = broker_deals_fn
        self.max_candidates = int(max_candidates)

    # ------------------------------------------------------------------
    # Candidate discovery
    # ------------------------------------------------------------------

    def _outcome_rows(self) -> list[dict[str, Any]]:
        """All outcome rows with a broker ticket, newest first (bounded)."""
        if not self.ledger.audit_repo._is_sqlite:
            return []
        try:
            conn = sqlite3.connect(self.ledger.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT * FROM audit_experience_outcomes "
                    "WHERE execution_id != '' ORDER BY outcome_timestamp DESC LIMIT ?;",
                    (self.max_candidates,),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()
        except Exception as e:
            logger.error("[BROKER_OUTCOME_REPAIR] event=SCAN_FAILED", error=str(e))
            return []

    def _candidates(self) -> list[dict[str, Any]]:
        """Outcome rows that are zero-R and carry a broker ticket."""
        out: list[dict[str, Any]] = []
        for row in self._outcome_rows():
            try:
                payload = json.loads(row.get("payload") or "{}")
            except Exception:
                payload = {}
            r = float(row.get("realized_r_multiple") or 0.0)
            pnl = float(row.get("realized_pnl_usd") or 0.0)
            if not _is_zero_outcome(r, pnl):
                continue
            bo = payload.get("broker_outcome") or {}
            src = (bo or {}).get("reconstruction_source", "") if isinstance(bo, dict) else ""
            if src in ("BROKER_NATIVE", "BROKER_DEALS", "BROKER_DEALS_AGGREGATED"):
                # Already has authoritative reconstruction; not corrupt.
                continue
            out.append(row)
        return out

    # ------------------------------------------------------------------
    # Decision context recovery
    # ------------------------------------------------------------------

    def _decision_record(self, idempotency_key: str) -> ExperienceRecord | None:
        """The immutable decision row for an outcome (for entry/SL etc.)."""
        try:
            return self.ledger.get_experience_by_key(idempotency_key)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Repair
    # ------------------------------------------------------------------

    def run(self, hours_back: int | None = None) -> OutcomeRepairResult:
        """
        Executes one repair pass.

        Returns the aggregate result. Never raises: each candidate is
        failure-isolated.
        """
        result = OutcomeRepairResult()
        candidates = self._candidates()
        result.candidates = len(candidates)
        logger.info(
            "[BROKER_OUTCOME_REPAIR] event=START",
            candidates=len(candidates),
        )
        for row in candidates:
            try:
                self._repair_one(row, result, hours_back)
            except Exception as e:
                logger.error(
                    "[BROKER_OUTCOME_REPAIR] event=CANDIDATE_FAILED",
                    idempotency_key=str(row.get("idempotency_key", ""))[:24],
                    error=str(e),
                )
                result.unrepaired += 1
        # Make repaired outcomes durable and immediately readable.
        flush_repair_queue(self.ledger)
        logger.info(
            "[BROKER_OUTCOME_REPAIR] event=DONE",
            repaired=result.repaired,
            unrepaired=result.unrepaired,
            candidates=result.candidates,
        )
        return result

    def _repair_one(
        self, row: dict[str, Any], result: OutcomeRepairResult, hours_back: int | None
    ) -> None:
        key = str(row.get("idempotency_key", ""))
        ticket = str(row.get("execution_id", ""))
        rec = self._decision_record(key)
        if rec is None:
            logger.warning(
                "[BROKER_OUTCOME_REPAIR] event=SKIPPED_NO_DECISION",
                idempotency_key=key[:24],
                ticket=ticket,
            )
            result.skipped_no_broker += 1
            result.unrepaired += 1
            return

        # Lifecycle-bounded broker window: entry time known -> cover it; else
        # a sane default.
        window_h = hours_back or DEFAULT_HISTORY_HOURS
        if rec.decision_timestamp is not None:
            age = datetime.now(UTC) - rec.decision_timestamp
            window_h = max(window_h, int(age.total_seconds() / 3600.0) + 2)

        deals = []
        try:
            deals = self.broker_deals_fn(int(ticket), window_h) if ticket.isdigit() else []
        except Exception as e:
            logger.error(
                "[BROKER_OUTCOME_REPAIR] event=BROKER_QUERY_FAILED",
                ticket=ticket,
                error=str(e),
            )

        if not deals:
            logger.info(
                "[BROKER_OUTCOME_REPAIR] event=MATCH_FAILED",
                ticket=ticket,
                idempotency_key=key[:24],
                searched_hours=window_h,
                reason="NO_BROKER_DEALS_IN_WINDOW",
            )
            result.unrepaired += 1
            return

        direction = rec.action.upper().replace("_MARKET", "").replace("_", "") or "BUY"
        if "SELL" in rec.action.upper():
            direction = "SELL"
        elif "BUY" in rec.action.upper():
            direction = "BUY"

        broker_outcome = reconstruct_broker_outcome(
            ticket=int(ticket) if ticket.isdigit() else 0,
            symbol=rec.symbol or "XAUUSD",
            direction=direction,
            deals=deals,
            matched_deal=None,
            entry_price=rec.proposed_entry or 0.0,
            initial_sl=rec.stop_loss or 0.0,
            final_sl=rec.stop_loss or 0.0,
            tp_price=rec.take_profit or 0.0,
            volume=float(row.get("approved_volume") or 0.0),
            fallback_exit_price=0.0,
            close_time=datetime.now(UTC),
            entry_time=rec.decision_timestamp,
        )

        if broker_outcome.reconstruction_source == "NONE":
            logger.info(
                "[BROKER_OUTCOME_REPAIR] event=UNREPAIRED_NO_BROKER_TRUTH",
                ticket=ticket,
                idempotency_key=key[:24],
            )
            result.unrepaired += 1
            return

        net = broker_outcome.net_pnl_usd
        risk_distance = rec.planned_risk_distance
        contract_sz = 100.0
        # Use the BROKER-AGGREGATED volume when available (authoritative);
        # fall back to the outcome/decision volume only as a secondary source.
        actual_volume = (
            broker_outcome.volume or float(row.get("approved_volume") or 0.0) or rec.approved_volume
        )
        risk_usd = max(1.0, risk_distance * max(actual_volume, 0.0) * contract_sz)
        r_multiple = net / risk_usd if net is not None else 0.0

        # Rebuild the outcome payload with repair provenance stamped AND the
        # top-level realized fields corrected (the Outcome object's scalars are
        # what `repair_outcome` persists).
        old_payload = json.loads(row.get("payload") or "{}")
        repaired_payload = dict(old_payload)
        repaired_payload["realized_r_multiple"] = round(float(r_multiple), 6)
        repaired_payload["realized_pnl_usd"] = round(float(net or 0.0), 2)
        repaired_payload["broker_outcome"] = broker_outcome.model_dump()
        repaired_payload["exit_reason"] = old_payload.get("exit_reason") or "HARD_SL_HIT"
        repaired_payload["repair_provenance"] = {
            "repair_id": f"repair_{key[:12]}",
            "repaired_at": datetime.now(UTC).isoformat(),
            "old_realized_r": float(row.get("realized_r_multiple") or 0.0),
            "old_realized_pnl": float(row.get("realized_pnl_usd") or 0.0),
            "new_realized_r": round(float(r_multiple), 6),
            "new_realized_pnl": round(float(net or 0.0), 2),
            "source": broker_outcome.reconstruction_source,
            "deal_ids": broker_outcome.deal_ids,
            "reason": "BUG-046_1H_LOOKUP_WINDOW",
        }
        try:
            outcome = ExperienceOutcome.model_validate(repaired_payload)
        except Exception as e:
            logger.error(
                "[BROKER_OUTCOME_REPAIR] event=PAYLOAD_INVALID",
                idempotency_key=key[:24],
                error=str(e),
            )
            result.unrepaired += 1
            return

        try:
            ok = self.ledger.repair_outcome(outcome, repair_reason="BUG-046")
        except Exception as e:
            logger.error(
                "[BROKER_OUTCOME_REPAIR] event=REPAIR_WRITE_FAILED",
                idempotency_key=key[:24],
                error=str(e),
            )
            result.unrepaired += 1
            return

        if ok:
            result.repaired += 1
            result.repaired_rows.append(
                {
                    "idempotency_key": key,
                    "ticket": ticket,
                    "old_r": 0.0,
                    "new_r": round(float(r_multiple), 4),
                    "new_pnl": round(float(net or 0.0), 2),
                    "source": broker_outcome.reconstruction_source,
                }
            )
            logger.info(
                "[BROKER_OUTCOME_REPAIR] event=REPAIRED",
                ticket=ticket,
                idempotency_key=key[:24],
                old_r=0.0,
                new_r=round(float(r_multiple), 4),
                source=broker_outcome.reconstruction_source,
                status="REPAIRED",
            )
        else:
            result.unrepaired += 1


def flush_repair_queue(ledger: ExperienceLedger) -> None:
    """Joins the audit background queue so repaired outcomes are durable and
    immediately readable. Safe to call after a repair pass."""
    try:
        ledger.audit_repo._queue.join()
    except Exception:
        pass
