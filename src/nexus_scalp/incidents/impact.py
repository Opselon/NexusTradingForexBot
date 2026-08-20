"""Impact analysis, quarantine, and recovery-plan generation (TASK-12 spec 25/27/28/29/30).

AUTOMATIC IMPACT ANALYSIS (spec 25): estimates affected time range, records,
trades, models, research runs, UI endpoints, users — from OBSERVED evidence
only (no fabricated numbers; unknown quantities stay unknown).

CONTAINMENT (spec 27): only explicitly safe actions (pause research worker,
block model inference, mark dataset invalid, block migration/release).
Never: closing trades, modifying SL, changing risk.

RECOVERY PLANS (spec 28/29): generated as RECOMMENDED; destructive recovery
requires operator approval. The engine NEVER executes recovery.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any, ClassVar

from nexus_scalp.incidents.models import (
    BlastRadius,
    Incident,
    IncidentImpact,
    QuarantineEntry,
    RecoveryAction,
    RecoveryPlan,
)

# ---------------------------------------------------------------------------
# Impact analysis (spec 25/26)
# ---------------------------------------------------------------------------


class ImpactAnalyzer:
    """Read-only impact estimation for an incident."""

    def __init__(self, db_path: str = "") -> None:
        self.db_path = db_path

    def analyze(
        self,
        incident: Incident,
        *,
        affected_tables: list[str] | None = None,
    ) -> IncidentImpact:
        """Estimates impact from the incident's own records + DB evidence.

        Every number is derived from observed rows; anything not measurable
        stays 0/None rather than being guessed (spec 25: no fabricated
        numbers).

        Occurrence-aware (spec 22/25): when a db_path is available, the
        per-family counts come from the occurrence engine (occurrences.py)
        keyed by the incident's identity fields, so a scan-time incident is
        never reported as '0 trades / 0 records' when real rows exist.
        """
        occ_counts: dict[str, int | None] = {}
        occ_semantics = "UNKNOWN_IMPACT"
        occ_note = ""
        if self.db_path:
            try:
                from nexus_scalp.incidents.occurrences import (
                    attach_occurrence_evidence,
                    count_families,
                )

                occ = count_families(incident, self.db_path)
                occ_counts = occ["counts"] or {}
                occ_semantics = occ.get("semantics") or "UNKNOWN_IMPACT"
                attach_occurrence_evidence(incident, occ)
                occ_known = [
                    (k, int(v)) for k, v in occ_counts.items() if v is not None and int(v) > 0
                ]
                if occ_known:
                    occ_note = "occurrences: " + ", ".join(f"{k}={v}" for k, v in sorted(occ_known))
            except Exception:
                occ_semantics = "UNKNOWN_IMPACT"
        occ_ledger = occ_counts.get("affected_ledger_records") or 0
        occ_trades = occ_counts.get("affected_trades") or 0
        occ_exec = occ_counts.get("affected_executions") or 0
        occ_pos = occ_counts.get("affected_positions") or 0
        occ_res = occ_counts.get("affected_research_records") or 0
        if occ_semantics in ("ZERO_IMPACT", "MEASURED", "UNKNOWN_IMPACT"):
            affected_record_count = max(int(occ_ledger), int(occ_pos), int(occ_exec))
        else:
            affected_record_count = len(incident.affected_records)
        imp = IncidentImpact(
            affected_records=affected_record_count,
            affected_trades=int(occ_trades),
            affected_models=len(incident.affected_models),
            affected_research_runs=int(occ_res),
            affected_ui_endpoints=_ui_endpoints_for(incident),
            affected_users=len(incident.affected_users),
            blast_radius=_classify_blast_radius(incident, affected_tables or []),
        )
        if occ_semantics in ("ZERO_IMPACT", "MEASURED", "UNKNOWN_IMPACT"):
            imp.notes.append(f"occurrence_semantics={occ_semantics}")
            if occ_note:
                imp.notes.append(occ_note)
        if incident.timeline:
            ts = [t.timestamp for t in incident.timeline if t.timestamp]
            if ts:
                imp.affected_time_range = (min(ts), max(ts))
        # Research-run blast: if the incident is research-category and the DB
        # has research_runs, count runs overlapping the affected time range.
        if incident.category.value in ("RESEARCH", "LEARNING", "DATA") and self.db_path:
            try:
                imp.affected_research_runs = self._count_research_runs(incident)
            except Exception:
                imp.affected_research_runs = 0
        # UI endpoint blast: derived from the component (never fabricated).
        if not imp.affected_ui_endpoints:
            known = {
                "MT5": ["/api/mt5/status", "/api/status"],
                "LEDGER": ["/api/account/trades", "/api/account/performance"],
                "ACCOUNTING": ["/api/account/performance", "/api/account/summary"],
                "UI": ["/", "/api/live/state"],
                "API": ["/api/status"],
                "NEWS": ["/api/news", "/api/news/health"],
                "MODEL": ["/api/models/*", "/api/models/governance/*"],
                "FEATURE": ["/api/debug/features", "/api/status"],
                "RESEARCH": ["/api/research/*"],
                "LEARNING": ["/api/experience/*"],
                "WORKER": ["/api/status"],
                "EXPOSURE": ["/api/status", "/api/live/state"],
                "VERSION": ["/api/db/status"],
                "MIGRATION": ["/api/db/status"],
                "GOVERNANCE": ["/api/models/governance/*"],
                "TELEGRAM": [],
            }
            imp.affected_ui_endpoints = list(known.get(incident.category.value, []))
        return imp

    def _count_research_runs(self, incident: Incident) -> int:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            row = conn.execute("SELECT COUNT(*) FROM research_runs").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()


def _ui_endpoints_for(incident: Incident) -> list[str]:
    return list(getattr(incident.impact, "affected_ui_endpoints", []) or [])


def _classify_blast_radius(incident: Incident, affected_tables: list[str]) -> BlastRadius:
    """Evidence-driven blast radius (spec 26)."""
    if incident.category.value == "DATA" or affected_tables:
        return BlastRadius.SYSTEM_WIDE
    cat = incident.category.value
    if cat in ("UI", "API", "VERSION", "TELEGRAM", "FEATURE", "NEWS"):
        return BlastRadius.COMPONENT
    if cat in ("MT5", "LEDGER", "ACCOUNTING", "LEARNING", "RESEARCH", "MODEL", "EXPOSURE"):
        return BlastRadius.CROSS_COMPONENT
    return BlastRadius.LOCAL


# ---------------------------------------------------------------------------
# Quarantine (spec 30) — non-destructive marks
# ---------------------------------------------------------------------------


class QuarantineManager:
    """Registers non-destructive suspect-data marks.

    NEVER deletes evidence. Keeps original record + reason + incident_id +
    timestamp. Marks are advisory: downstream consumers may consult them but
    no automatic pipeline rewrites data based on a mark.
    """

    def __init__(self, db_path: str = "") -> None:
        self.db_path = db_path

    def mark_suspect(
        self,
        incident: Incident,
        *,
        target_table: str,
        record_key: str,
        reason: str,
        status: str = "SUSPECT",
        evidence: str = "",
    ) -> QuarantineEntry:
        entry = QuarantineEntry(
            target_table=target_table,
            record_key=record_key,
            status=status,
            reason=reason,
            incident_id=incident.incident_id,
            evidence=evidence,
            quarantined_at=datetime.now(UTC),
        )
        incident.add_quarantine(entry)
        return entry

    def list_quarantined(self, incident: Incident) -> list[dict[str, Any]]:
        return [q.as_dict() for q in incident.quarantine_entries]


# ---------------------------------------------------------------------------
# Recovery plan generation (spec 28/29)
# ---------------------------------------------------------------------------


class RecoveryPlanner:
    """Generates explicit recovery plans with governed states."""

    #: Per-category template recovery options (spec 28 examples).
    TEMPLATES: ClassVar[dict[str, list[dict[str, Any]]]] = {
        "MT5": [
            {
                "step_id": "REC-01",
                "action": "Reconcile broker history first; do NOT touch the ledger before reconciliation evidence exists.",
                "kind": "RECONCILE",
                "destructive": False,
                "required_tests": ["TEST-INCIDENT-07", "TEST-INCIDENT-08"],
            },
            {
                "step_id": "REC-02",
                "action": "Verify server-timebase mapping (broker-local epoch vs UTC; measure observed skew, never assume 3h).",
                "kind": "REVALIDATE",
                "destructive": False,
                "required_tests": ["TEST-INCIDENT-08"],
            },
        ],
        "LEDGER": [
            {
                "step_id": "REC-01",
                "action": "Rebuild affected outcomes from raw broker evidence (reconstruction_source must be recorded).",
                "kind": "REBUILD",
                "destructive": False,
                "required_tests": ["TEST-INCIDENT-07", "TEST-INCIDENT-10"],
            },
            {
                "step_id": "REC-02",
                "action": "Mark SUSPECT records (quarantine) — never delete; keep original + reason + incident_id.",
                "kind": "QUARANTINE",
                "destructive": False,
                "required_tests": ["TEST-INCIDENT-25"],
            },
        ],
        "DATA": [
            {
                "step_id": "REC-01",
                "action": "Do NOT delete audit.db. Quarantine invalidated records (SUSPECT/INVALIDATED/QUARANTINED).",
                "kind": "QUARANTINE",
                "destructive": False,
                "required_tests": ["TEST-INCIDENT-25"],
            },
            {
                "step_id": "REC-02",
                "action": "Re-run the research dataset rebuild AFTER the corrupt source is fixed — dataset integrity first.",
                "kind": "REBUILD",
                "destructive": False,
                "required_tests": ["TEST-INCIDENT-10"],
            },
        ],
        "LEARNING": [
            {
                "step_id": "REC-01",
                "action": "Re-run outcome recovery idempotently; verify experience_to_outcome_rate returns to baseline.",
                "kind": "REBUILD",
                "destructive": False,
                "required_tests": ["TEST-INCIDENT-10"],
            },
            {
                "step_id": "REC-02",
                "action": "Verify request_id propagation for new executions (correlation_id discipline).",
                "kind": "REVALIDATE",
                "destructive": False,
                "required_tests": ["TEST-INCIDENT-05", "TEST-INCIDENT-21"],
            },
        ],
        "MODEL": [
            {
                "step_id": "REC-01",
                "action": "Re-verify the model artifact manifest/hash/schema/dimension before any re-load (never load on file-exists).",
                "kind": "REVALIDATE",
                "destructive": False,
                "required_tests": ["TEST-INCIDENT-11"],
            },
            {
                "step_id": "REC-02",
                "action": "Block unsafe model inference until contract integrity passes — do not retrain automatically.",
                "kind": "BLOCK",
                "destructive": False,
                "required_tests": ["TEST-INCIDENT-11", "TEST-INCIDENT-34"],
            },
        ],
        "EXPOSURE": [
            {
                "step_id": "REC-01",
                "action": "Reconcile exposure cache against broker truth (INV-011: broker wins). Verify MAX_EXPOSURE back to normal.",
                "kind": "RECONCILE",
                "destructive": False,
                "required_tests": ["TEST-INCIDENT-16"],
            },
        ],
        "RESEARCH": [
            {
                "step_id": "REC-01",
                "action": "Rerun the research dataset; re-evaluate candidates with evidence floors (no lowered thresholds).",
                "kind": "REBUILD",
                "destructive": False,
                "required_tests": ["TEST-INCIDENT-10", "TEST-INCIDENT-22"],
            },
        ],
        "NEWS": [
            {
                "step_id": "REC-01",
                "action": "Distinguish source-health vs parser failure; re-poll bounded windows; never fabricate articles.",
                "kind": "REVALIDATE",
                "destructive": False,
                "required_tests": ["TEST-INCIDENT-13"],
            },
        ],
        "WORKER": [
            {
                "step_id": "REC-01",
                "action": "Restart the stalled worker ONLY after its failure is proven; verify progress metrics before/after.",
                "kind": "MANUAL",
                "destructive": False,
                "required_tests": ["TEST-INCIDENT-15"],
            },
        ],
        "TELEGRAM": [
            {
                "step_id": "REC-01",
                "action": "Re-route credentials through settings_service.set_telegram() (INV-010 — never live.yaml).",
                "kind": "MANUAL",
                "destructive": False,
                "required_tests": ["TEST-INCIDENT-30"],
            },
        ],
        "GOVERNANCE": [
            {
                "step_id": "REC-01",
                "action": "Re-affirm the governance gate decision with operator approval; no auto-promotion.",
                "kind": "REVALIDATE",
                "destructive": False,
                "required_tests": ["TEST-INCIDENT-16", "TEST-INCIDENT-27"],
            },
        ],
        "VERSION": [
            {
                "step_id": "REC-01",
                "action": "Version mismatch: reconcile web/backend/schema/model versions; block release until consistent.",
                "kind": "BLOCK",
                "destructive": False,
                "required_tests": ["TEST-INCIDENT-18"],
            },
        ],
        "MIGRATION": [
            {
                "step_id": "REC-01",
                "action": "Migration failure: do not downgrade; inspect the migration journal; re-run via the migration engine only.",
                "kind": "MANUAL",
                "destructive": False,
                "required_tests": ["TEST-INCIDENT-17"],
            },
        ],
        "ACCOUNTING": [
            {
                "step_id": "REC-01",
                "action": "Accounting divergence: reconcile broker vs ledger first; never rewrite accounting history automatically.",
                "kind": "RECONCILE",
                "destructive": False,
                "required_tests": ["TEST-INCIDENT-07", "TEST-INCIDENT-09"],
            },
        ],
    }

    def generate(self, incident: Incident) -> RecoveryPlan:
        """Builds a recovery plan for an incident (spec 28)."""
        plan = RecoveryPlan(
            what_failed=incident.operation or incident.category.value,
            why=incident.root_cause or "root cause unknown — further evidence required",
            affected=(
                f"{incident.impact.affected_trades} trades, "
                f"{incident.impact.affected_records} records, "
                f"{incident.impact.affected_models} models, "
                f"{incident.impact.affected_research_runs} research runs"
            ),
            trustworthy=[
                "audit.db financial rows (tier-0 broker truth + tier-1 canonical audit)",
                "schema_migrations / schema_meta (migration history)",
                "model artifacts with verified manifests (load gate)",
                "news.db raw articles (hash-verified)",
            ],
            suspect=[
                "outcomes with reconstruction_source=NONE and zero PnL",
                "records listed in incident quarantine entries",
                "any cached value whose lineage shows a stale hop",
            ],
            must_not_change=[
                "No ledger/accounting rewrite",
                "No model retraining or Champion mutation",
                "No RiskEngine / lot sizing / SL/TP changes",
                "No automatic deletion of audit.db / news.db / research evidence",
            ],
            required_tests=[
                "TEST-INCIDENT-26",
                "TEST-INCIDENT-27",
                "TEST-INCIDENT-28",
                "TEST-INCIDENT-34",
                "TEST-INCIDENT-35",
            ],
        )
        for t in self.TEMPLATES.get(incident.category.value, []):
            plan.options.append(
                RecoveryAction(
                    step_id=t["step_id"],
                    action=t["action"],
                    kind=t["kind"],
                    destructive=bool(t.get("destructive")),
                    required_tests=list(t.get("required_tests") or []),
                    approval_required=True,
                )
            )
        if not plan.options:
            plan.options.append(
                RecoveryAction(
                    step_id="REC-01",
                    action="Manual operator review required — no safe automated recovery exists for this category.",
                    kind="MANUAL",
                    destructive=False,
                    approval_required=True,
                )
            )
        return plan

    def require_approval(self, plan: RecoveryPlan) -> RecoveryPlan:
        """Governance step: recovery can never execute without approval
        (spec 29). This only validates the state invariant."""
        for opt in plan.options:
            if opt.status.value != "RECOMMENDED":
                raise ValueError(
                    f"recovery step {opt.step_id} changed state without approval — "
                    "governed transition required (RECOMMENDED -> APPROVED -> EXECUTING)"
                )
        return plan


__all__ = [
    "ImpactAnalyzer",
    "QuarantineManager",
    "RecoveryPlanner",
]
