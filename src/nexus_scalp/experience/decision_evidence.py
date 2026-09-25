"""Decision-evidence resolver (BUG-185, P0-M canonical resolver).

ONE authoritative, deterministic classification of a ledger decision's
terminal-state evidence. Both consumers MUST use this module so their
semantics can never diverge again:

    * experience/outcome_recovery_sweep.py  (recovery: what to backfill)
    * research/dataset.py                   (research: why a record is excluded)

Before BUG-185 the two modules each had private, subtly different notions of
"dispatch evidence" (the sweep trusted audit_signals gate rejections; the
dataset builder looked only at outcome presence), which is exactly why the
dataset builder kept calling unknown-provenance orphans "recoverable" while
the recovery sweep skipped them as no-dispatch — and the log flood persisted
after every restart.

Evidence taxonomy (deterministic, auditable):
    GATE_REJECTION   audit_signals row with decision_stage in
                     {EXPERIENCE_INTELLIGENCE_GATE, TRADE_INTELLIGENCE_GATE}
                     => positive proof the decision was refused BEFORE any
                     dispatch could exist (the gates run strictly before
                     risk sizing / dispatch). Terminal state: NOT_DISPATCHED.
    DISPATCH_TICKET  audit_orders row for the request carrying a broker
                     ticket => the engine dispatched; broker-history states
                     then decide FILLED/CANCELED/EXPIRED/REJECTED (handled by
                     the sweep's broker-truth path).
    NO_EVIDENCE      neither of the above: honest provenance is UNKNOWN.
                     It is NOT proof of "not dispatched" (the dispatch log
                     and signals table were both introduced mid-Aug-2026;
                     older decisions legitimately have neither).

P0-I contract: NOT_DISPATCHED means ONLY "a terminal pre-dispatch decision"
backed by GATE_REJECTION (or the live writer at the moment of rejection).
Unknown provenance stays UNKNOWN — never fabricated into NOT_DISPATCHED.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from nexus_scalp.experience.lifecycle import DecisionLifecycle

#: Gate stages that PROVE a pre-dispatch refusal (Phase 08 / Phase 09).
GATE_REJECTION_STAGES: frozenset[str] = frozenset(
    {"EXPERIENCE_INTELLIGENCE_GATE", "TRADE_INTELLIGENCE_GATE"}
)

#: Canonical evidence classes returned by :func:`resolve_decision_evidence`.
EVIDENCE_GATE_REJECTION = "GATE_REJECTION"
EVIDENCE_DISPATCH_TICKET = "DISPATCH_TICKET"
EVIDENCE_NO_EVIDENCE = "NO_EVIDENCE"

#: Provenance confidence per evidence class (deterministic mapping).
_CONFIDENCE: dict[str, str] = {
    EVIDENCE_GATE_REJECTION: "PROVEN",
    EVIDENCE_DISPATCH_TICKET: "PROVEN",
    EVIDENCE_NO_EVIDENCE: "UNKNOWN",
}


@dataclass(frozen=True)
class TerminalStateEvidence:
    """Structured verdict for one decision's dispatch provenance."""

    decision_id: str
    evidence: str  # GATE_REJECTION / DISPATCH_TICKET / NO_EVIDENCE
    provenance_source: str  # audit_signals / audit_orders / none
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    dispatch_proven: bool = False
    pre_dispatch_gate: str = ""  # gate stage name when GATE_REJECTION
    reason: str = ""

    @property
    def confidence(self) -> str:
        return _CONFIDENCE.get(self.evidence, "UNKNOWN")

    @property
    def implied_terminal_state(self) -> DecisionLifecycle | None:
        """The ONLY terminal state this evidence can justify, or None.

        NO_EVIDENCE implies nothing (UNKNOWN is not a DecisionLifecycle and
        must never be folded into NOT_DISPATCHED).
        """
        if self.evidence == EVIDENCE_GATE_REJECTION:
            return DecisionLifecycle.NOT_DISPATCHED
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "provenance_source": self.provenance_source,
            "evidence_ids": list(self.evidence_ids),
            "dispatch_proven": self.dispatch_proven,
            "pre_dispatch_gate": self.pre_dispatch_gate,
            "reason": self.reason,
        }


def resolve_decision_evidence(
    conn: sqlite3.Connection,
    request_id: str,
) -> TerminalStateEvidence:
    """Single-source dispatch-provenance resolution for one decision.

    ``conn`` is a read-only SQLite connection to the audit DB (row_factory
    not required). Deterministic: the same rows always produce the same
    verdict. Never raises for missing evidence — absence is NO_EVIDENCE.
    """
    rid = str(request_id or "")
    if not rid:
        return TerminalStateEvidence(
            decision_id="",
            evidence=EVIDENCE_NO_EVIDENCE,
            provenance_source="none",
            reason="empty request_id",
        )

    # 1. Positive pre-dispatch gate rejection (audit_signals).
    try:
        row = conn.execute(
            """SELECT id, decision_stage FROM audit_signals
               WHERE request_id = ?
                 AND decision_stage IN
                     ('EXPERIENCE_INTELLIGENCE_GATE', 'TRADE_INTELLIGENCE_GATE')
               ORDER BY id LIMIT 1""",
            (rid,),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is not None:
        return TerminalStateEvidence(
            decision_id=rid,
            evidence=EVIDENCE_GATE_REJECTION,
            provenance_source="audit_signals",
            evidence_ids=(str(row[0]),),
            dispatch_proven=False,
            pre_dispatch_gate=str(row[1]),
            reason=f"{row[1]}: pre-dispatch gate rejection",
        )

    # 2. Engine dispatch log with a broker ticket.
    try:
        row = conn.execute(
            """SELECT id, ticket FROM audit_orders
               WHERE order_id = ? AND ticket != 0 ORDER BY id LIMIT 1""",
            (rid,),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is not None:
        return TerminalStateEvidence(
            decision_id=rid,
            evidence=EVIDENCE_DISPATCH_TICKET,
            provenance_source="audit_orders",
            evidence_ids=(str(row[0]), str(row[1])),
            dispatch_proven=True,
            reason="engine dispatch row with broker ticket",
        )

    # 3. Honest unknown provenance.
    return TerminalStateEvidence(
        decision_id=rid,
        evidence=EVIDENCE_NO_EVIDENCE,
        provenance_source="none",
        reason="no dispatch row and no gate-rejection signal (unknown provenance)",
    )


__all__ = [
    "EVIDENCE_DISPATCH_TICKET",
    "EVIDENCE_GATE_REJECTION",
    "EVIDENCE_NO_EVIDENCE",
    "GATE_REJECTION_STAGES",
    "TerminalStateEvidence",
    "resolve_decision_evidence",
]
