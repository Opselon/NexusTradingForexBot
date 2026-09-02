"""Centralized forensic health checks — accounting / dataset / migration / growth checks (CHECK-ACC · CHECK-DTA · CHECK-MIG · CHECK-GRW).

Mechanically extracted VERBATIM from the former monolith ``checks.py``
(CHG-0032 Step 2, behavior-preserving decomposition). Function bodies are
byte-identical to the pre-split file; only import wiring changed.

BOUNDARY: read-only health checks. No check mutates databases, artifacts or
runtime state (TASK-11 §0/§55). Imports: forensics.models/references +
``checks_support`` — never a sibling domain module.

USED BY: ``checks.py`` (the facade every consumer imports) and
``forensics.engine`` via ``checks.check_*`` attribute access.

DO-NOT-PUT-HERE: engine wiring (engine.py), gate policy (deploy_gate.py),
new check families that belong to another domain module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.forensics.checks_support import (
    _audit_path,
    _broker_ledger_divergence,
    _integrity_for,
    _ok,
    _parse_close_time,
    _ro_connect,
    _table_names,
    _unknown,
)
from nexus_scalp.forensics.models import (
    CheckResult,
    HealthStatus,
)


def check_dataset_manifest_health(dataset_root: Path | None = None) -> CheckResult:
    """Dataset sniff: manifest/schema presence, dimension, row counts (read-only).

    Datasets live under artifacts/model_generation/datasets/<id>/. Absence of
    datasets is UNKNOWN (research worker not producing yet) — never PASS.
    """
    root = dataset_root or Path("artifacts") / "model_generation" / "datasets"
    try:
        if not root.is_dir():
            return _unknown(
                "CHECK-DTA-01",
                f"dataset root missing: {root}",
                {"root": str(root)},
                "dataset root exists",
            )
        entries = sorted(root.iterdir())
        if not entries:
            return _unknown(
                "CHECK-DTA-01",
                "dataset root empty — no datasets produced",
                {"root": str(root)},
                "at least one dataset",
            )
    except Exception as exc:
        return _unknown(
            "CHECK-DTA-01",
            f"dataset scan failed: {exc!r}",
            {"root": str(root)},
            "dataset root exists",
        )
    reports: list[dict[str, Any]] = []
    problems: list[str] = []
    for d in entries:
        if not d.is_dir():
            continue
        info: dict[str, Any] = {"id": d.name}
        for fname in ("manifest.json", "dataset.parquet", "dataset.csv", "schema.json"):
            p = d / fname
            if p.is_file():
                info[fname] = p.stat().st_size
        reports.append(info)
        dim = info.get("feature_count")  # placeholder for enriched manifests
        if dim is not None and dim < 50:
            problems.append(f"{d.name}: feature_count {dim} < 50")
    if problems:
        return CheckResult(
            "CHECK-DTA-01",
            HealthStatus.CRITICAL,
            evidence="; ".join(problems),
            observed={"datasets": reports},
            expected="dataset schemas preserve the Base contract",
            detail="DATASET_SCHEMA_DRIFT",
        )
    return _ok(
        "CHECK-DTA-01",
        f"{len(reports)} dataset(s) present",
        {"datasets": reports},
        "datasets present and schema-consistent",
    )


# ---------------------------------------------------------------------------
# Accounting checks (INV-70D-015/016 + duplicate/excursion monitors)
# ---------------------------------------------------------------------------


def check_accounting_divergence() -> CheckResult:
    """Broker truth vs ledger truth: flag unexplained divergence (INV-70D-016 context).

    NOTE: ledger MAY legitimately contain rows without broker covers (paper
    trades, pre-migration gap, BUG-045 era). Divergence beyond a documented
    tolerance is WARNING (for investigation) — never auto-rewrite.
    """
    diag = _broker_ledger_divergence()
    if not diag.get("available"):
        return _unknown(
            "CHECK-ACC-01",
            "broker/ledger reconciliation unavailable (tables absent or DB missing)",
            diag,
            "audit_broker_trades + audit_ledger",
        )
    broker = diag.get("broker_pnl_sum")
    ledger = diag.get("ledger_pnl_sum")
    tolerance = 0.0 if broker is None or ledger is None else abs(broker) * 0.02 + 5.0
    if broker is not None and ledger is not None and abs(broker - ledger) > tolerance:
        return CheckResult(
            "CHECK-ACC-01",
            HealthStatus.WARNING,
            evidence=f"broker PnL {broker} vs ledger PnL {ledger} diverges beyond tolerance {tolerance:.2f}",
            observed=diag,
            expected=f"|broker - ledger| <= {tolerance:.2f}",
            detail="ACCOUNTING_DIVERGENCE",
        )
    return _ok(
        "CHECK-ACC-01",
        f"broker PnL {broker} vs ledger PnL {ledger} within tolerance (unmatched ratio {diag.get('unmatched_ratio')})",
        diag,
        "broker/ledger PnL within tolerance",
    )


def check_duplicate_economic_outcome() -> CheckResult:
    """INV-70D-016: no execution identity owns more than one canonical outcome.

    Uses the same identity rule as BUG-097 guard: an execution_id (broker
    ticket) must appear at most once as `execution_id` across outcome rows.
    Historical duplicate rows remain (immutable history) — the check reports
    WARNING with incident count, CRITICAL only for NEW duplicates after the
    guard baseline timestamp.
    """
    path = _audit_path()
    if not path.exists():
        return _unknown("CHECK-ACC-02", "audit.db missing", {}, "audit.db")
    try:
        conn = _ro_connect(path)
        try:
            tables = _table_names(conn)
            if "audit_experience_outcomes" not in tables:
                return _unknown(
                    "CHECK-ACC-02", "outcomes table absent", {}, "audit_experience_outcomes"
                )
            rows = conn.execute(
                "SELECT idempotency_key, execution_id, "
                "COALESCE(realized_pnl_usd, 0) AS realized_pnl "
                "FROM audit_experience_outcomes"
            ).fetchall()
            # column names may vary; normalize
            cols = ["idempotency_key", "execution_id", "realized_pnl"]
            by_exec: dict[str, list[tuple[str, object]]] = {}
            for row in rows:
                rec = dict(zip(cols, row, strict=False))
                exec_id = rec.get("execution_id") or rec.get("order_id") or rec.get("ticket")
                if exec_id is None:
                    continue
                by_exec.setdefault(str(exec_id), []).append(
                    (str(rec.get("idempotency_key", "")), rec.get("realized_pnl"))
                )
            dupes = {k: v for k, v in by_exec.items() if len(v) > 1}
            if dupes:
                # Distinguish historical (known, BUG-097) from new: we cannot
                # timestamp-filter reliably without a created_at; report WARNING
                # for the known historical incident, CRITICAL for any OTHER.
                known_historical = {"152494870397"}
                fresh = [k for k in dupes if k not in known_historical]
                if fresh:
                    return CheckResult(
                        "CHECK-ACC-02",
                        HealthStatus.CRITICAL,
                        evidence=f"execution identities with >1 outcome: {sorted(fresh)}",
                        observed={"duplicates": dupes},
                        expected="one canonical outcome per execution identity",
                        detail="DUPLICATE_ECONOMIC_OUTCOME",
                    )
                return CheckResult(
                    "CHECK-ACC-02",
                    HealthStatus.WARNING,
                    evidence="known historical duplicate incident(s) remain (BUG-097, immutable)",
                    observed={"duplicates": dupes},
                    expected="one canonical outcome per execution identity",
                    detail="DUPLICATE_ECONOMIC_OUTCOME_HISTORICAL",
                )
            return _ok(
                "CHECK-ACC-02",
                "no execution identity owns more than one outcome",
                {"outcome_rows": len(rows)},
                "one canonical outcome per execution identity",
            )
        finally:
            conn.close()
    except Exception as exc:
        return _unknown(
            "CHECK-ACC-02",
            f"duplicate outcome check raised: {exc!r}",
            {"error": str(exc)},
            "outcomes readable",
        )


def check_impossible_excursion() -> CheckResult:
    """MFE >= 0, MAE <= 0 persistent invariant (BUG-096) plus ledger sanity.

    Raw ledger rows violating the excursion contract are classified as
    WARNING when they pre-date the BUG-096 fix (2026-08-19 — immutable
    historical findings, ANOMALY-VERIFY-01); any NEW violation after the fix
    is CRITICAL. No auto-repair ever.
    """
    path = _audit_path()
    if not path.exists():
        return _unknown("CHECK-ACC-03", "audit.db missing", {}, "audit.db")
    try:
        conn = _ro_connect(path)
        try:
            tables = _table_names(conn)
            if "audit_ledger" not in tables:
                return _unknown("CHECK-ACC-03", "ledger table absent", {}, "audit_ledger")
            cols = [d[0] for d in conn.execute("SELECT * FROM audit_ledger LIMIT 0").description]
            if "mfe" not in cols or "mae" not in cols:
                return _unknown(
                    "CHECK-ACC-03", "ledger lacks mfe/mae columns", {}, "audit_ledger.mfe/mae"
                )
            rows = conn.execute(
                "SELECT ticket, mfe, mae, close_time FROM audit_ledger WHERE mfe < 0 OR mae > 0"
            ).fetchall()
            violations = [
                dict(zip(("ticket", "mfe", "mae", "close_time"), r, strict=False)) for r in rows
            ]
            if violations:
                # BUG-096 fix landed 2026-08-19 (ANOMALY-VERIFY-01): rows closed
                # at/after the fix must be clean. Historical rows are immutable.
                FIX_DATE = datetime(2026, 8, 19, tzinfo=UTC)
                new_violations = [
                    v
                    for v in violations
                    if (age := _parse_close_time(v.get("close_time"))) is not None
                    and age >= FIX_DATE
                ]
                if new_violations:
                    return CheckResult(
                        "CHECK-ACC-03",
                        HealthStatus.CRITICAL,
                        evidence=f"{len(new_violations)} NEW excursion violations after BUG-096 fix",
                        observed={
                            "violations": violations[:20],
                            "new_violations": new_violations[:10],
                        },
                        expected="MFE >= 0 and MAE <= 0",
                        detail="IMPOSSIBLE_EXCURSION",
                    )
                return CheckResult(
                    "CHECK-ACC-03",
                    HealthStatus.WARNING,
                    evidence=f"{len(violations)} historical excursion rows pre-date the BUG-096 fix "
                    "(immutable audit trail, ANOMALY-VERIFY-01)",
                    observed={"violations": violations[:20]},
                    expected="MFE >= 0 and MAE <= 0",
                    detail="IMPOSSIBLE_EXCURSION_HISTORICAL",
                )
            return _ok(
                "CHECK-ACC-03",
                "no MFE<0 / MAE>0 violations",
                {"checked": True},
                "MFE >= 0 and MAE <= 0",
            )
        finally:
            conn.close()
    except Exception as exc:
        return _unknown(
            "CHECK-ACC-03",
            f"excursion check raised: {exc!r}",
            {"error": str(exc)},
            "ledger readable",
        )


def check_experience_outcome_gap() -> CheckResult:
    """§21: experiences-without-outcome — EXECUTED trades must not lose outcomes.

    TASK-12 §16-20 correction (proven 2026-08-19): raw experience-vs-outcome
    counts misattribute pre-execution decision samples (which never trade and
    legitimately have no outcome) as pipeline losses. The truthful signal is
    the DEFECT rate over executed trades: only executed trades with missing
    outcomes indicate a learning-pipeline defect.
    """
    try:
        from nexus_scalp.forensics.experience_gap import analyze_experience_gap

        rep = analyze_experience_gap(_audit_path())
    except Exception as exc:
        return _unknown(
            "CHECK-ACC-04",
            f"gap analysis raised: {exc!r}",
            {"error": str(exc)},
            "experience tables readable",
        )
    d = rep.to_dict()
    observed = {
        "experiences": d["total_experiences"],
        "outcomes": d["with_outcome"],
        "gap": d["without_outcome"],
        "gap_rate": d["gap_rate"],
        "defect_rate": d["defect_rate"],
        "classification": d["classification"],
        "recoverable": d["recoverable_count"],
        "unrecoverable": d["unrecoverable_count"],
    }
    status = (
        HealthStatus(rep.status)
        if rep.status
        in (
            "PASS",
            "WARNING",
            "DEGRADED",
            "UNKNOWN",
            "CRITICAL",
        )
        else HealthStatus.UNKNOWN
    )
    if status is HealthStatus.PASS:
        reason = (
            f"no executed trade lost its outcome (defect_rate {d['defect_rate']}); "
            f"{d['without_outcome']} never-traded decision samples are legitimate"
        )
    else:
        reason = f"learning pipeline defect rate {d['defect_rate']} (status {rep.status})"
    return CheckResult(
        "CHECK-ACC-04",
        status,
        evidence=reason,
        observed=observed,
        expected="defect_rate over executed trades within thresholds",
    )


# ---------------------------------------------------------------------------
# Database integrity (INV-70D-017 context)
# ---------------------------------------------------------------------------


def check_migration_state() -> CheckResult:
    """INV-70D-017: applied schema versions vs runtime expectations."""
    from nexus_scalp.database.models import DatabaseDomain  # type: ignore[import-not-found]
    from nexus_scalp.database.registry import (
        expected_version_for_domain,  # type: ignore[import-not-found]
    )

    paths = {
        "audit": _audit_path(),
        "news": Path("artifacts") / "news.db",
        "candle_intel": Path("artifacts") / "candle_intel.db",
    }
    expected = {d.value: expected_version_for_domain(d) for d in DatabaseDomain}
    from nexus_scalp.database.registry import BASELINE_VERSIONS, REGISTRY

    # pending migrations = registered-but-not-applied (legitimate, applies at
    # next startup gate); anything else below expected is UNEXPECTED drift.
    pending_ids: dict[str, list[str]] = {}
    for dom in DatabaseDomain:
        base = BASELINE_VERSIONS.get(dom, 1)
        applied = 0
        p = paths.get(dom.value)
        if p is not None and p.exists():
            info = _integrity_for(p)
            meta = info.get("schema_meta", {})
            version = int(meta.get("schema_version", 0) or 0)
            applied = version
        reg: Any = REGISTRY.get(dom, [])
        pend = [m.migration_id for m in reg if base + reg.index(m) + 1 > applied]
        pending_ids[dom.value] = pend
    reports: dict[str, Any] = {}
    problems: list[str] = []
    for name, p in paths.items():
        if not p.exists():
            reports[name] = {"state": "MISSING"}
            continue
        info = _integrity_for(p)
        meta = info.get("schema_meta", {})
        version = int(meta.get("schema_version", 0) or 0)
        exp = expected.get(name)
        pend = pending_ids.get(name, [])
        reports[name] = {
            "schema_version": version,
            "expected_version": exp,
            "pending_migrations": pend,
        }
        if exp is not None and version < exp:
            if pend:
                problems.append(f"{name}: schema v{version} with PENDING migration(s) {pend}")
            else:
                problems.append(
                    f"{name}: schema v{version} != expected v{exp} with NO pending migration"
                )
    pending_only = [p for p in problems if "PENDING" in p]
    real_drift = [p for p in problems if "NO pending" in p]
    if real_drift:
        return CheckResult(
            "CHECK-MIG-01",
            HealthStatus.CRITICAL,
            evidence="; ".join(real_drift),
            observed=reports,
            expected=f"schema versions == {expected}",
            detail="MIGRATION_DRIFT",
        )
    if pending_only:
        return CheckResult(
            "CHECK-MIG-01",
            HealthStatus.WARNING,
            evidence="; ".join(pending_only),
            observed=reports,
            expected=f"schema versions == {expected} (pending migrations apply at startup gate)",
            detail="MIGRATION_PENDING",
        )
    missing = [n for n, r in reports.items() if r.get("state") == "MISSING"]
    if missing:
        return _unknown(
            "CHECK-MIG-01", f"DB(s) missing: {', '.join(missing)}", reports, "all domains present"
        )
    return _ok(
        "CHECK-MIG-01",
        f"all domains at expected schema versions {expected}",
        reports,
        f"schema versions == {expected}",
    )


# ---------------------------------------------------------------------------
# Liquidity feature health (INV-70D-003 + §7/§8/§9/§10)
# ---------------------------------------------------------------------------


def check_database_growth(db_paths: dict[str, Path] | None = None) -> CheckResult:
    """§41: DB size + WAL size; alert on unexpected explosion or stalls."""
    paths = db_paths or {
        "audit": _audit_path(),
        "news": Path("artifacts") / "news.db",
        "candle_intel": Path("artifacts") / "candle_intel.db",
    }
    reports: dict[str, Any] = {}
    for name, p in paths.items():
        info: dict[str, Any] = {"exists": p.exists()}
        if p.exists():
            info["size_bytes"] = p.stat().st_size
            wal = Path(str(p) + "-wal")
            info["wal_size_bytes"] = wal.stat().st_size if wal.exists() else 0
        reports[name] = info
    # compare against the baseline probe (2026-08-19): audit 50.9MB, news 6.4MB, candle 1.1MB
    baseline = {"audit": 50_921_472, "news": 6_373_376, "candle_intel": 1_134_592}
    # Fresh DBs legitimately start small; the shrink guard applies only above
    # a size floor so tiny/test DBs are never flagged.
    SHRINK_FLOOR = 5_000_000
    anomalies: list[str] = []
    for name, info in reports.items():
        if not info.get("exists"):
            anomalies.append(f"{name}: DB missing")
            continue
        size = info["size_bytes"]
        base = baseline.get(name, size)
        if base and size > base * 3:
            anomalies.append(f"{name}: size {size} > 3x baseline {base} (growth anomaly)")
        elif base and size > SHRINK_FLOOR and size < base * 0.3:
            anomalies.append(f"{name}: size {size} < 0.3x baseline {base} (unexpected shrink)")
    if anomalies:
        return CheckResult(
            "CHECK-GRW-01",
            HealthStatus.WARNING,
            evidence="; ".join(anomalies),
            observed=reports,
            expected="DB sizes within baseline bounds",
            detail="DB_GROWTH_ANOMALY",
        )
    return _ok(
        "CHECK-GRW-01",
        "DB sizes within baseline bounds",
        reports,
        "DB sizes within baseline bounds",
    )


def check_queue_growth() -> CheckResult:
    """§42: background queue sizes (telegram/audit writer) — sustained growth alert."""
    observed: dict[str, Any] = {}
    problems: list[str] = []
    try:
        from nexus_scalp.settings import load_settings_service  # type: ignore[import-not-found]

        svc = load_settings_service()
        n = getattr(svc, "notifier", None) or getattr(svc, "_notifier", None)
        if n is not None and hasattr(n, "health_state"):
            hs = n.health_state()
            observed["telegram"] = {"queue_size": hs.get("queue_size"), "status": hs.get("status")}
            qs = int(hs.get("queue_size") or 0)
            if qs >= 80:
                problems.append(f"telegram queue {qs} (capacity ~100) — sustained growth")
    except Exception:
        pass
    if problems:
        return CheckResult(
            "CHECK-GRW-02",
            HealthStatus.WARNING,
            evidence="; ".join(problems),
            observed=observed,
            expected="queues bounded",
            detail="QUEUE_GROWTH",
        )
    return _ok("CHECK-GRW-02", "background queues bounded", observed, "queues bounded")


# ---------------------------------------------------------------------------
# 200-but-wrong semantic API checks (§37/§38)
# ---------------------------------------------------------------------------
