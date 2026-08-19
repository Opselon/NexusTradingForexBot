"""Experience -> Outcome gap forensics (TASK-12 §16-20).

Answers "where does outcome information first disappear?" per experience
(signal -> execution -> broker -> close -> ledger -> outcome -> experience
-> research), and classifies every missing outcome into the §18 taxonomy.

CRITICAL (§19): a missing outcome NEVER becomes PnL=0 / R=0 / win=false
silently. Missing stays distinguishable from a genuine zero result.

Governance: thresholds (gap_rate alerting) come from config, not hardcoded
arbitrary numbers in the engine (§20).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.forensics.experience_gap")

#: §18 classification taxonomy.
GAP_CLASSES = (
    "OPEN_TRADE",
    "PENDING",
    "BROKER_HISTORY_MISSING",
    "RECONSTRUCTION_FAILURE",
    "LEDGER_MISSING",
    "OUTCOME_LINK_FAILURE",
    "DUPLICATE_SUPPRESSION",
    "EXPIRED_CONTEXT",
    "LEGITIMATELY_NO_OUTCOME",
    "UNKNOWN",
)

#: Default governance thresholds (overridable via config §20).
DEFAULT_THRESHOLDS = {
    "gap_rate_warning": 0.20,
    "gap_rate_degraded": 0.50,
    "recoverable_min": 5,
}


def load_gap_thresholds(config_path: Path | None = None) -> dict[str, float]:
    """Governance thresholds from config; defaults when absent (§20)."""
    thresholds = dict(DEFAULT_THRESHOLDS)
    path = config_path or Path("configs") / "base.yaml"
    try:
        if path.exists():
            import yaml

            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            section = raw.get("forensic_report", {}).get("experience_gap", {}) or {}
            for k in ("gap_rate_warning", "gap_rate_degraded", "recoverable_min"):
                if k in section:
                    thresholds[k] = float(section[k])
    except Exception:
        pass
    return thresholds


@dataclass
class ExperienceGapReport:
    total_experiences: int = 0
    with_outcome: int = 0
    without_outcome: int = 0
    gap_rate: float = 0.0
    classification: dict[str, int] = None  # type: ignore[assignment]
    age_distribution: dict[str, int] = None  # type: ignore[assignment]
    recoverable_count: int = 0
    unrecoverable_count: int = 0
    defect_rate: float = 0.0
    status: str = "UNKNOWN"
    threshold_warning: float = 0.0
    threshold_degraded: float = 0.0
    first_divergence: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_experiences": self.total_experiences,
            "with_outcome": self.with_outcome,
            "without_outcome": self.without_outcome,
            "gap_rate": round(self.gap_rate, 4),
            "classification": self.classification,
            "age_distribution": self.age_distribution,
            "recoverable_count": self.recoverable_count,
            "unrecoverable_count": self.unrecoverable_count,
            "defect_rate": self.defect_rate,
            "status": self.status,
            "thresholds": {
                "gap_rate_warning": self.threshold_warning,
                "gap_rate_degraded": self.threshold_degraded,
            },
            "first_divergence": self.first_divergence,
            "generated_at": datetime.now(UTC).isoformat(),
        }


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)


def classify_missing_outcome(
    exp_row: dict[str, Any],
    outcome_rows: list[dict[str, Any]],
    broker_rows: list[dict[str, Any]],
    ledger_rows: list[dict[str, Any]],
) -> str:
    """Classifies one experience without an outcome (§18).

    Evidence-driven: looks for the FIRST layer where the outcome
    information disappears rather than blaming the outcome store.

    ORDER MATTERS (proven against the live DB 2026-08-19): the decision
    layer is first — an experience WITHOUT an execution_id never traded,
    so the missing outcome is LEGITIMATELY_NO_OUTCOME (a signal that the
    risk/policy path or engine state rejected), NOT a learning-pipeline
    drop. Only experiences WITH an execution identity are expected to
    produce outcomes; a failure there is a real pipeline defect.
    """
    # 0) Execution linkage: did this decision ever trade?
    execution_id = str(exp_row.get("execution_id") or "").strip()
    str(exp_row.get("request_id") or "").strip()
    if not execution_id:
        # A decision sample with no execution identity never traded.
        # It is NOT a dropped outcome — no outcome was ever expected.
        return "LEGITIMATELY_NO_OUTCOME"
    # 1) Is the trade still open / not closed?
    status = str(exp_row.get("status") or "").upper()
    if status in ("OPEN", "PENDING", "ACTIVE"):
        return "OPEN_TRADE"
    # 2) Broker history exists for the execution identity?
    if not broker_rows:
        return "BROKER_HISTORY_MISSING"
    # 3) Ledger row exists?
    if not ledger_rows:
        return "LEDGER_MISSING"
    # 4) Outcome row exists but was suppressed as duplicate?
    if outcome_rows and any(r.get("suppressed") or r.get("is_duplicate") for r in outcome_rows):
        return "DUPLICATE_SUPPRESSION"
    # 5) The experience is old and never resolved -> expired context
    close_time = exp_row.get("close_time") or exp_row.get("closed_at") or exp_row.get("timestamp")
    if close_time:
        try:
            dt = datetime.fromisoformat(str(close_time).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            age_days = (datetime.now(UTC) - dt).days
            if age_days > 30:
                return "EXPIRED_CONTEXT"
        except (TypeError, ValueError):
            pass
    # 6) Legitimately no outcome (strategy context without execution)
    strategy = str(exp_row.get("strategy_id") or exp_row.get("strategy") or "")
    if "research" in strategy.lower() or "backtest" in strategy.lower():
        return "LEGITIMATELY_NO_OUTCOME"
    # 7) Reconstruction failed historically (outcome_repair legacy)
    payload = str(exp_row.get("payload") or "")
    if "reconstruct" in payload.lower() or "fail" in payload.lower():
        return "RECONSTRUCTION_FAILURE"
    # 8) Fallback
    return "UNKNOWN"


def _exp_row(conn: sqlite3.Connection) -> dict[str, Any]:
    cols = [d[0] for d in conn.execute("SELECT * FROM audit_experiences LIMIT 0").description]
    return dict(zip(cols, [None] * len(cols), strict=False))


def analyze_experience_gap(
    audit_path: Path | None = None,
    thresholds: dict[str, float] | None = None,
) -> ExperienceGapReport:
    """Full gap analysis over audit.db (read-only)."""
    audit_path = audit_path or Path("artifacts") / "audit.db"
    thresholds = thresholds or load_gap_thresholds()
    report = ExperienceGapReport(
        threshold_warning=thresholds.get("gap_rate_warning", 0.20),
        threshold_degraded=thresholds.get("gap_rate_degraded", 0.50),
        classification={c: 0 for c in GAP_CLASSES},
        age_distribution={},
    )
    if not audit_path.exists():
        report.status = "UNKNOWN"
        return report
    conn = _ro(audit_path)
    try:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "audit_experiences" not in tables or "audit_experience_outcomes" not in tables:
            report.status = "UNKNOWN"
            return report
        exp_cols = [
            d[0] for d in conn.execute("SELECT * FROM audit_experiences LIMIT 0").description
        ]
        out_cols = [
            d[0]
            for d in conn.execute("SELECT * FROM audit_experience_outcomes LIMIT 0").description
        ]
        exp_rows = conn.execute("SELECT * FROM audit_experiences").fetchall()
        out_rows = conn.execute("SELECT * FROM audit_experience_outcomes").fetchall()
        report.total_experiences = len(exp_rows)
        report.with_outcome = len(out_rows)
        report.without_outcome = max(0, report.total_experiences - report.with_outcome)
        report.gap_rate = (
            report.without_outcome / report.total_experiences if report.total_experiences else 0.0
        )

        # ---- classify each missing outcome ----
        # outcome keys: idempotency_key / request_id / execution_id
        outcome_keys = {
            str(r[out_cols.index("idempotency_key")])
            for r in out_rows
            if "idempotency_key" in out_cols
        }
        exp_maps = [dict(zip(exp_cols, r, strict=False)) for r in exp_rows]
        for e in exp_maps:
            key = str(
                e.get("idempotency_key") or e.get("request_id") or e.get("execution_id") or ""
            )
            if key and key in outcome_keys:
                continue
            if not key:
                report.classification["UNKNOWN"] += 1
                report.unrecoverable_count += 1
                continue
            ticket = e.get("ticket") or e.get("order_id") or e.get("execution_id") or None
            broker_rows: list[dict[str, Any]] = []
            ledger_rows: list[dict[str, Any]] = []
            if ticket is not None and "audit_broker_trades" in tables:
                bc = [
                    d[0]
                    for d in conn.execute("SELECT * FROM audit_broker_trades LIMIT 0").description
                ]
                broker_rows = [
                    dict(zip(bc, r, strict=False))
                    for r in conn.execute(
                        "SELECT * FROM audit_broker_trades WHERE trade_id=? OR position_id=? OR master_order_id=?",
                        (str(ticket), str(ticket), str(ticket)),
                    ).fetchall()
                ]
            if ticket is not None and "audit_ledger" in tables:
                lc = [d[0] for d in conn.execute("SELECT * FROM audit_ledger LIMIT 0").description]
                ledger_rows = [
                    dict(zip(lc, r, strict=False))
                    for r in conn.execute(
                        "SELECT * FROM audit_ledger WHERE ticket=?", (str(ticket),)
                    ).fetchall()
                ]
            cls = classify_missing_outcome(e, [], broker_rows, ledger_rows)
            report.classification[cls] = report.classification.get(cls, 0) + 1
            if cls in (
                "BROKER_HISTORY_MISSING",
                "RECONSTRUCTION_FAILURE",
                "LEDGER_MISSING",
                "OUTCOME_LINK_FAILURE",
                "EXPIRED_CONTEXT",
            ):
                report.unrecoverable_count += 1
            elif cls in (
                "OPEN_TRADE",
                "PENDING",
                "DUPLICATE_SUPPRESSION",
                "LEGITIMATELY_NO_OUTCOME",
            ):
                report.recoverable_count += 1
            else:
                report.unrecoverable_count += 1

        # ---- age distribution ----
        now = datetime.now(UTC)
        for e in exp_maps:
            ts = e.get("timestamp") or e.get("created_at") or ""
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                days = int((now - dt).total_seconds() / 86400)
            except (TypeError, ValueError):
                days = -1
            bucket = f"{max(0, days // 7) * 7}d" if days >= 0 else "unknown"
            if days >= 0:
                report.age_distribution[bucket] = report.age_distribution.get(bucket, 0) + 1

        # ---- status via thresholds (§20) ----
        # The gap only reflects a LEARNING-PIPELINE defect when an EXECUTED
        # trade (execution identity present) lost its outcome. Legitimate
        # no-outcome samples (never-traded decisions) must not degrade the
        # pipeline status — they are the normal pre-trade population.
        defect_classes = {
            "BROKER_HISTORY_MISSING",
            "RECONSTRUCTION_FAILURE",
            "LEDGER_MISSING",
            "OUTCOME_LINK_FAILURE",
            "DUPLICATE_SUPPRESSION",
            "EXPIRED_CONTEXT",
            "UNKNOWN",
        }
        defect_count = sum(report.classification.get(c, 0) for c in defect_classes)
        total_expect_outcome = defect_count + report.with_outcome
        defect_rate = defect_count / total_expect_outcome if total_expect_outcome else 0.0
        report.defect_rate = round(defect_rate, 4)
        if total_expect_outcome == 0:
            report.status = "PASS"  # no executed trade ever lost an outcome
        elif defect_rate > report.threshold_degraded:
            report.status = "DEGRADED"
        elif defect_rate > report.threshold_warning:
            report.status = "WARNING"
        elif report.total_experiences == 0:
            report.status = "UNKNOWN"
        else:
            report.status = "PASS"

        # ---- first divergence: the earliest experience without outcome ----
        for e in sorted(exp_maps, key=lambda x: str(x.get("timestamp") or "")):
            key = str(
                e.get("idempotency_key") or e.get("request_id") or e.get("execution_id") or ""
            )
            if not key or key in outcome_keys:
                continue
            report.first_divergence = {
                "experience_id": e.get("id") or e.get("idempotency_key") or "",
                "timestamp": str(e.get("timestamp") or ""),
                "strategy_id": str(e.get("strategy_id") or ""),
                "ticket": e.get("ticket") or e.get("order_id") or None,
                "no_broker_row": "audit_broker_trades" not in tables,
                "no_ledger_row": "audit_ledger" not in tables,
                "classification": classify_missing_outcome(e, [], [], []),
            }
            break
        return report
    finally:
        conn.close()


def persist_gap_report(report: ExperienceGapReport, result_dir: Path | None = None) -> Path:
    """Persists the gap report to artifacts/forensics/experience_outcome_gap.json (§16)."""
    result_dir = result_dir or Path("artifacts") / "forensics"
    result_dir.mkdir(parents=True, exist_ok=True)
    path = result_dir / "experience_outcome_gap.json"
    path.write_text(json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
    return path
