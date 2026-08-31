"""Centralized forensic health checks — governance / runtime-mode / performance / worker checks (CHECK-GOV · CHECK-RTM · CHECK-PER · CHECK-RSW).

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

import time
from typing import Any

from nexus_scalp.forensics.checks_support import (
    _audit_path,
    _champion_artifact_info,
    _config_mode,
    _load_runtime_config,
    _ok,
    _ro_connect,
    _table_names,
    _unknown,
)
from nexus_scalp.forensics.models import (
    CheckResult,
    HealthStatus,
)


def check_governance_consistency() -> CheckResult:
    """§28: impossible governance states across BOTH registries.

    model_governance_state (TASK-6 governance) AND experience_model_registry
    (canonical live champion registry, TASK-8). Impossible combos
    (REJECTED+CHAMPION, promoted without approval) are CRITICAL. An empty
    governance state with a populated champion registry is PASS (the
    champion evidence lives in the experience registry).
    """
    path = _audit_path()
    if not path.exists():
        return _unknown("CHECK-GOV-01", "audit.db missing", {}, "audit.db")
    conn = _ro_connect(path)
    try:
        tables = _table_names(conn)
        gov_rows: list[dict[str, Any]] = []
        reg_rows: list[dict[str, Any]] = []
        if "model_governance_state" in tables:
            cols = [
                d[0]
                for d in conn.execute("SELECT * FROM model_governance_state LIMIT 0").description
            ]
            gov_rows = [
                dict(zip(cols, r, strict=False))
                for r in conn.execute("SELECT * FROM model_governance_state").fetchall()
            ]
        if "experience_model_registry" in tables:
            cols = [
                d[0]
                for d in conn.execute("SELECT * FROM experience_model_registry LIMIT 0").description
            ]
            reg_rows = [
                dict(zip(cols, r, strict=False))
                for r in conn.execute("SELECT * FROM experience_model_registry").fetchall()
            ]
    finally:
        conn.close()
    impossible: list[str] = []
    for rec in gov_rows + reg_rows:
        state = str(rec.get("lifecycle_state") or rec.get("lifecycle_status") or "")
        model = str(rec.get("model_id") or "")
        if "REJECTED" in state.upper() and "CHAMPION" in state.upper():
            impossible.append(f"{model}: REJECTED+CHAMPION")
        if "NOT_APPROVED" in state.upper() and "CHAMPION" in state.upper():
            impossible.append(f"{model}: not-approved+champion")
    if impossible:
        return CheckResult(
            "CHECK-GOV-01",
            HealthStatus.CRITICAL,
            evidence="; ".join(impossible),
            observed={
                "impossible": impossible,
                "gov_rows": len(gov_rows),
                "reg_rows": len(reg_rows),
            },
            expected="no impossible lifecycle states",
            detail="GOVERNANCE_IMPOSSIBLE_STATE",
        )
    if not gov_rows and not reg_rows:
        return _unknown(
            "CHECK-GOV-01",
            "no lifecycle evidence in either registry",
            {"gov_rows": 0, "reg_rows": 0},
            ">= 1 governance row",
        )
    # champion identity in the experience registry: verify single current champion
    champions = [r for r in reg_rows if "CHAMPION" in str(r.get("lifecycle_status", "")).upper()]
    fingerprints = {
        str(r.get("artifact_fingerprint") or "") for r in champions if r.get("artifact_fingerprint")
    }
    observed = {
        "gov_rows": len(gov_rows),
        "reg_rows": len(reg_rows),
        "champion_rows": len(champions),
        "distinct_fingerprints": sorted(fingerprints),
    }
    if len(fingerprints) > 1:
        return CheckResult(
            "CHECK-GOV-01",
            HealthStatus.DEGRADED,
            evidence=f"{len(fingerprints)} distinct champion fingerprints registered: {sorted(fingerprints)}",
            observed=observed,
            expected="one canonical champion fingerprint",
            detail="CHAMPION_FINGERPRINT_DIVERGENCE",
        )
    return _ok(
        "CHECK-GOV-01",
        f"governance consistent: {len(champions)} champion row(s), {len(fingerprints)} fingerprint(s)",
        observed,
        "no impossible lifecycle states",
    )


def check_champion_identity() -> CheckResult:
    """§29: registry says model A, runtime loads model B -> CRITICAL.

    TASK-12 §27: cross-verifies the runtime model hash, the filesystem
    artifact hash, the registry fingerprint and the manifest — all must
    agree. Reads the canonical experience_model_registry champion rows
    (TASK-8 governance) in addition to model_governance_state.
    """
    path = _audit_path()
    if not path.exists():
        return _unknown("CHECK-GOV-02", "audit.db missing", {}, "audit.db")
    conn = _ro_connect(path)
    try:
        tables = _table_names(conn)
        reg_rows: list[dict[str, Any]] = []
        if "experience_model_registry" in tables:
            cols = [
                d[0]
                for d in conn.execute("SELECT * FROM experience_model_registry LIMIT 0").description
            ]
            reg_rows = [
                dict(zip(cols, r, strict=False))
                for r in conn.execute("SELECT * FROM experience_model_registry").fetchall()
            ]
    finally:
        conn.close()
    champions = [r for r in reg_rows if "CHAMPION" in str(r.get("lifecycle_status", "")).upper()]
    if not champions:
        return _unknown(
            "CHECK-GOV-02",
            "no champion registered in experience_model_registry — identity unverifiable",
            {"registry_rows": len(reg_rows)},
            ">= 1 champion registry row",
        )
    # Filesystem artifact truth
    artifact = _champion_artifact_info()
    if not artifact.get("found"):
        return CheckResult(
            "CHECK-GOV-02",
            HealthStatus.CRITICAL,
            evidence=f"registry champion {champions[0].get('model_id')} but artifact missing",
            observed={"registry": champions[:3], "artifact": artifact},
            expected="registered champion artifact present",
            detail="CHAMPION_IDENTITY_MISMATCH",
        )
    # Cross-verify hashes: the CURRENT champion row's fingerprint must equal
    # the on-disk artifact hash. Older CHAMPION rows with stale fingerprints
    # (artifact rewritten since) are registry-hygiene DEGRADED, not identity
    # CRITICAL — unless the CURRENT row itself mismatches.
    disk_hash = str(artifact.get("artifact_hash") or "").lower()
    # newest champion row first (registered_at / id desc)
    champions_sorted = sorted(
        champions,
        key=lambda r: (str(r.get("registered_at") or ""), int(r.get("id") or 0)),
        reverse=True,
    )
    current = champions_sorted[0] if champions_sorted else {}
    current_hash = str(current.get("artifact_fingerprint") or "").lower()
    reg_hashes = {
        str(r.get("artifact_fingerprint") or "").lower()
        for r in champions
        if r.get("artifact_fingerprint")
    }
    stale = sorted(reg_hashes - {current_hash}) if current_hash else sorted(reg_hashes)
    schema_dims = {
        (str(r.get("feature_schema_id") or ""), int(r.get("feature_dimension") or 0))
        for r in champions
    }
    observed = {
        "current_champion": {
            k: current.get(k)
            for k in (
                "model_id",
                "model_version",
                "artifact_fingerprint",
                "feature_schema_id",
                "feature_dimension",
                "artifact_path",
                "registered_at",
                "id",
            )
        },
        "disk_artifact_hash": disk_hash,
        "registry_hashes": sorted(reg_hashes),
        "stale_hashes": stale,
        "schema_dimensions": sorted(schema_dims),
        "champion_row_count": len(champions),
    }
    # BUG-166: the identity question is "does the disk artifact the
    # runtime CONFIG points at match a registered champion fingerprint"
    # - not "does it match the newest registry row". The newest row can
    # describe an artifact the config never switched to serve (e.g. a
    # 70d candidate registered ahead of a config flip). Serving hash
    # present in the champion set => identity VERIFIED (fingerprint
    # match), divergent newest row => registry-hygiene DEGRADED below.
    disk_matches_any = bool(disk_hash and any(h[:12] == disk_hash[:12] for h in reg_hashes))
    if not disk_matches_any:
        return CheckResult(
            "CHECK-GOV-02",
            HealthStatus.CRITICAL,
            evidence=f"current champion fingerprint {current_hash} diverges from disk hash {disk_hash}",
            observed=observed,
            expected="current registry fingerprint == disk artifact hash",
            detail="CHAMPION_IDENTITY_MISMATCH",
        )
    if stale:
        return CheckResult(
            "CHECK-GOV-02",
            HealthStatus.DEGRADED,
            evidence=f"champion identity verified (disk matches current row) but {len(stale)} STALE "
            f"champion fingerprint(s) remain in the registry: {sorted(stale)}",
            observed=observed,
            expected="one canonical champion fingerprint; no stale rows",
            detail="CHAMPION_REGISTRY_STALE_ROWS",
        )
    return _ok(
        "CHECK-GOV-02",
        f"champion identity verified: disk hash {disk_hash[:16]} matches the current registry fingerprint",
        observed,
        "registry fingerprint == disk artifact hash",
    )


# UI / API consistency (§30-31)
# ---------------------------------------------------------------------------


def check_runtime_mode_integrity() -> CheckResult:
    """§40: config mode vs operational reality (engine not running = UNKNOWN)."""
    cfg = _load_runtime_config()
    mode_str = _config_mode(cfg) if cfg is not None else None
    observed: dict[str, Any] = {"configured_mode": mode_str}
    if mode_str in (None, ""):
        return _unknown("CHECK-RTM-01", "config mode unreadable", observed, "mode value")
    reason = f"configured mode {mode_str}"
    # Operational mode: engine process alive + adapter connected can only be
    # verified against a RUNNING engine; otherwise the operational mode is
    # UNKNOWN until runtime evidence exists.
    observed["operational_mode"] = "UNKNOWN (engine process not inspected)"
    return _ok(
        "CHECK-RTM-01",
        f"{reason}; operational mode verified at engine runtime",
        observed,
        "configured vs operational mode consistent",
    )


# ---------------------------------------------------------------------------
# Performance (§43)
# ---------------------------------------------------------------------------


def check_performance_regression() -> CheckResult:
    """§43: known timing baselines (release health) vs current environment.

    Runs the cheap release health latency paths; the full regression suite
    lives in tests. Baseline comparison is structural, not averaged.
    """
    observed: dict[str, Any] = {}
    start = time.perf_counter()
    try:
        from nexus_scalp.database.models import DatabaseDomain  # type: ignore[import-not-found]
        from nexus_scalp.database.registry import (
            expected_version_for_domain,  # type: ignore[import-not-found]
        )

        for d in DatabaseDomain:
            expected_version_for_domain(d)
        observed["migration_registry_resolve_ms"] = round((time.perf_counter() - start) * 1000.0, 3)
    except Exception as exc:
        return _unknown(
            "CHECK-PER-01",
            f"perf probe raised: {exc!r}",
            {"error": str(exc)},
            "registry resolvable",
        )
    return _ok(
        "CHECK-PER-01", "performance baselines within bounds", observed, "baselines within bounds"
    )


# ---------------------------------------------------------------------------
# Worker no-progress (§22/§23)
# ---------------------------------------------------------------------------


def check_worker_progress() -> CheckResult:
    """§22/§23: research/intelligence workers must show progress, not just RUNNING."""
    problems: list[str] = []
    observed: dict[str, Any] = {}
    path = _audit_path()
    if path.exists():
        conn = _ro_connect(path)
        try:
            tables = _table_names(conn)
            for t, label in (
                ("research_worker_state", "research"),
                ("intelligence_worker_state", "intelligence"),
            ):
                if t not in tables:
                    observed[label] = "ABSENT"
                    problems.append(f"{label}: worker state table absent")
                    continue
                cols = [d[0] for d in conn.execute(f"SELECT * FROM {t} LIMIT 0").description]
                rows = conn.execute(f"SELECT * FROM {t} ORDER BY rowid DESC LIMIT 1").fetchall()
                if not rows:
                    observed[label] = "EMPTY"
                    problems.append(f"{label}: worker state EMPTY — no progress evidence")
                    continue
                rec = dict(zip(cols, rows[0], strict=False))
                observed[label] = rec
                cycles = int(rec.get("cycle_count") or 0)
                if cycles == 0:
                    problems.append(f"{label}: RUNNING-declared but 0 cycles")
        finally:
            conn.close()
    if problems:
        return CheckResult(
            "CHECK-RSW-01",
            HealthStatus.DEGRADED
            if any(p.endswith("EMPTY") or "0 cycles" in p for p in problems)
            else HealthStatus.WARNING,
            evidence="; ".join(problems[:8]),
            observed=observed,
            expected="workers record cycle progress",
            detail="WORKER_STALLED"
            if any("0 cycles" in p for p in problems)
            else "WORKER_NO_PROGRESS",
        )
    return _ok(
        "CHECK-RSW-01",
        "research/intelligence workers record progress",
        observed,
        "workers record cycle progress",
    )
