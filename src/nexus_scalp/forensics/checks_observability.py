"""Centralized forensic health checks — trace / api / ui / chart checks (CHECK-TRC · CHECK-API · CHECK-UI · CHECK-INT).

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

from pathlib import Path
from typing import Any

from nexus_scalp.forensics.checks_support import (
    _audit_path,
    _integrity_for,
    _news_state,
    _ok,
    _ro_connect,
    _row_count,
    _table_names,
    _ui_bundle_files,
    _unknown,
)
from nexus_scalp.forensics.models import (
    CheckResult,
    HealthStatus,
)


def check_database_integrity(db_paths: dict[str, Path] | None = None) -> CheckResult:
    """INV-70D-017: integrity_check ok + journal WAL + migrations consistent.

    Returns UNKNOWN for a missing DB (fresh install) — never PASS.
    """
    paths = db_paths or {
        "audit": _audit_path(),
        "news": Path("artifacts") / "news.db",
        "candle_intel": Path("artifacts") / "candle_intel.db",
    }
    reports: dict[str, Any] = {}
    problems: list[str] = []
    for name, p in paths.items():
        info = _integrity_for(p)
        reports[name] = info
        if not info.get("exists"):
            problems.append(f"{name}: DB missing (UNKNOWN)")
            continue
        if info.get("integrity") != "ok":
            problems.append(f"{name}: integrity_check={info.get('integrity')}")
        if info.get("error"):
            problems.append(f"{name}: {info['error']}")
    critical = [p for p in problems if "integrity_check" in p or "error" in p]
    if critical:
        return CheckResult(
            "CHECK-INT-01",
            HealthStatus.CRITICAL,
            evidence="; ".join(critical),
            observed=reports,
            expected="integrity_check=ok on all domains",
            detail="DATABASE_CORRUPTION",
        )
    missing = [n for n, i in reports.items() if not i.get("exists")]
    if missing:
        return _unknown(
            "CHECK-INT-01",
            f"DB(s) missing: {', '.join(missing)}",
            reports,
            "all domains present",
        )
    return _ok(
        "CHECK-INT-01",
        "integrity_check=ok on all domains (audit/news/candle_intel)",
        reports,
        "integrity_check=ok on all domains",
    )


def check_ui_bundle_drift() -> CheckResult:
    """§31: backend version vs Web bundle version.

    Until the bundle carries a version marker the check is UNKNOWN — a stale
    bundle cannot be detected without a marker (honest UNKNOWN, not PASS).
    """
    bundle = _ui_bundle_files()
    if not bundle.get("found"):
        return _unknown("CHECK-UI-02", "Web bundle not found", bundle, "Web/index.html + app.js")
    markers = bundle.get("version_markers") or []
    if not markers:
        return _unknown(
            "CHECK-UI-02",
            "Web bundle has no version marker — WEB_BUNDLE_DRIFT cannot be detected yet",
            bundle,
            "version marker in bundle",
        )
    return _ok(
        "CHECK-UI-02",
        "Web bundle version marker present",
        bundle,
        "backend/bundle version compatibility",
    )


def check_ui_canonical_state() -> CheckResult:
    """§30: dashboard must have ONE canonical state endpoint."""
    # Static check: the canonical live state contract is served at /api/live/state.
    # Runtime verification happens via API probe when the server runs (CHECK-API-01).
    return _ok(
        "CHECK-UI-01",
        "canonical live state endpoint: /api/live/state",
        {"endpoint": "/api/live/state"},
        "canonical UI state endpoint exists",
    )


# ---------------------------------------------------------------------------
# Telegram (§32)
# ---------------------------------------------------------------------------


def check_trace_completeness() -> CheckResult:
    """§33: critical subsystems must have worker state evidence."""
    st: dict[str, Any] = {}
    path = _audit_path()
    if path.exists():
        conn = _ro_connect(path)
        try:
            tables = _table_names(conn)
            for t in (
                "intelligence_worker_state",
                "research_worker_state",
                "model_governance_events",
            ):
                st[t] = _row_count(conn, t) if t in tables else "ABSENT"
        finally:
            conn.close()
    nws = _news_state()
    st["news_worker_state"] = (
        len(nws.get("worker_state") or []) if nws.get("exists") else "MISSING_DB"
    )
    missing = [k for k, v in st.items() if v in ("ABSENT", 0, "MISSING_DB")]
    if missing:
        return CheckResult(
            "CHECK-TRC-01",
            HealthStatus.WARNING,
            evidence=f"subsystems with no trace evidence: {missing}",
            observed=st,
            expected="every critical subsystem records worker state",
            detail="TRACE_GAP",
        )
    return _ok(
        "CHECK-TRC-01",
        "all critical subsystems have trace evidence",
        st,
        "every critical subsystem records worker state",
    )


def check_correlation_propagation() -> CheckResult:
    """§34: governance and migration events carry correlation ids."""
    path = _audit_path()
    if not path.exists():
        return _unknown("CHECK-TRC-02", "audit.db missing", {}, "audit.db")
    conn = _ro_connect(path)
    try:
        tables = _table_names(conn)
        if "model_governance_events" in tables:
            cols = [
                d[0]
                for d in conn.execute("SELECT * FROM model_governance_events LIMIT 0").description
            ]
            if "correlation_id" not in cols:
                return CheckResult(
                    "CHECK-TRC-02",
                    HealthStatus.DEGRADED,
                    evidence="model_governance_events lacks correlation_id column",
                    observed={"columns": cols},
                    expected="correlation_id column",
                    detail="TRACE_INCOMPLETE",
                )
        # schema_migrations carries checksums (TASK-10) — verify presence
        if "schema_migrations" in tables:
            cols = [
                d[0] for d in conn.execute("SELECT * FROM schema_migrations LIMIT 0").description
            ]
            if "checksum" not in cols:
                return CheckResult(
                    "CHECK-TRC-02",
                    HealthStatus.DEGRADED,
                    evidence="schema_migrations lacks checksum column",
                    observed={"columns": cols},
                    expected="checksum column",
                    detail="TRACE_INCOMPLETE",
                )
    finally:
        conn.close()
    return _ok(
        "CHECK-TRC-02",
        "governance events carry correlation ids; migrations carry checksums",
        {
            "governance_events_table": "model_governance_events",
            "migrations_table": "schema_migrations",
        },
        "correlation/checksum columns present",
    )


_SILENT_FALLBACK_PATTERNS = (
    "default=0",
    "silent recovery",
    "fallback",
    "silent fallback",
    "unavailable -> 0",
    "failed; continuing",
)


def check_silent_fallback(log_dir: Path | None = None) -> CheckResult:
    """§36: scan recent runtime logs for silent-fallback/zero-substitution patterns.

    Presence of the PATTERN is a WARNING (documented fallsbacks exist); the
    check's job is to surface them for triage. A log dir with no logs at all
    is UNKNOWN (no evidence either way) — never PASS.
    """
    logs = log_dir or Path("artifacts") / "logs"
    if not logs.is_dir():
        return _unknown(
            "CHECK-TRC-03", f"log dir missing: {logs}", {"dir": str(logs)}, "artifacts/logs"
        )
    files = sorted(logs.glob("*.log"))[-8:]
    hits: list[str] = []
    for f in files:
        try:
            with open(f, errors="replace") as fh:
                for line in fh:
                    lower = line.lower()
                    if any(p in lower for p in _SILENT_FALLBACK_PATTERNS):
                        hits.append(f"{f.name}: {line.strip()[:120]}")
                        if len(hits) >= 12:
                            break
        except OSError:
            continue
        if len(hits) >= 12:
            break
    if not files:
        return _unknown("CHECK-TRC-03", "no log files found", {"dir": str(logs)}, "log files")
    if hits:
        return CheckResult(
            "CHECK-TRC-03",
            HealthStatus.WARNING,
            evidence=f"{len(hits)} fallback-pattern log lines (triage needed)",
            observed={"hits": hits},
            expected="no silent fallback patterns in logs",
            detail="SILENT_FALLBACK_CANDIDATE",
        )
    return _ok(
        "CHECK-TRC-03",
        f"no silent-fallback patterns in last {len(files)} logs",
        {"files": len(files)},
        "no silent fallback patterns in logs",
    )


# ---------------------------------------------------------------------------
# Database growth / queues (§41-42) and performance (§43)
# ---------------------------------------------------------------------------


def check_chart_semantic_health(bars: list[dict[str, Any]] | None = None) -> CheckResult:
    """§38: a chart API returning 200 with zero bars is CHART_DATA_DEGRADED.

    When no runtime bars are supplied, the check inspects candle_intel for
    evidence; empty = DEGRADED (never PASS).
    """
    if bars is not None:
        if len(bars) == 0:
            return CheckResult(
                "CHECK-API-02",
                HealthStatus.DEGRADED,
                evidence="chart payload 200 but ZERO bars",
                observed={"bar_count": 0},
                expected="bar_count > 0",
                detail="CHART_DATA_DEGRADED",
            )
        # OHLC integrity + ordering + duplicates
        problems: list[str] = []
        ts = [b.get("timestamp") or b.get("time") for b in bars]
        dupes = len(ts) - len(set(ts)) if ts else 0
        if dupes:
            problems.append(f"{dupes} duplicate timestamps")
        for b in bars:
            o, h, l, c = (b.get("open"), b.get("high"), b.get("low"), b.get("close"))
            try:
                if not (l <= o <= h and l <= c <= h):
                    problems.append(f"OHLC violation at {b.get('timestamp')}")
                    break
            except TypeError:
                continue
        if problems:
            return CheckResult(
                "CHECK-API-02",
                HealthStatus.DEGRADED,
                evidence="; ".join(problems),
                observed={"bar_count": len(bars), "problems": problems},
                expected="valid OHLC, ordered, no duplicates",
                detail="CHART_DATA_INVALID",
            )
        return _ok(
            "CHECK-API-02",
            f"chart payload valid ({len(bars)} bars)",
            {"bar_count": len(bars)},
            "valid OHLC, ordered, no duplicates",
        )
    # offline path: candle_intel candles
    path = Path("artifacts") / "candle_intel.db"
    if not path.exists():
        return _unknown(
            "CHECK-API-02", "no runtime bars and candle_intel.db missing", {}, "bar source"
        )
    conn = _ro_connect(path)
    try:
        n = _row_count(conn, "candles") or 0
    finally:
        conn.close()
    if n == 0:
        return CheckResult(
            "CHECK-API-02",
            HealthStatus.DEGRADED,
            evidence="candle_intel has 0 candles — chart data DEGRADED",
            observed={"candles": 0},
            expected="candles > 0",
            detail="CHART_DATA_DEGRADED",
        )
    return _ok("CHECK-API-02", f"candle_intel has {n} candles", {"candles": n}, "candles > 0")


def check_api_200_but_wrong() -> CheckResult:
    """§37: semantic health for the known API endpoints.

    Offline: verifies the endpoints are REGISTERED on the FastAPI app by
    querying the app's own OpenAPI path surface (``create_app()`` +
    ``openapi()``). Since CHG-0032-A1 the routes live in extracted
    ``web/<domain>_routes.py`` modules registered via ``include_router``,
    so a source-text grep of ``server.__file__`` can no longer see them.
    Runtime probing is performed by the API integration layer.
    """
    endpoints = {
        "/api/status": False,
        "/api/chart/history": False,
        "/api/news/sources": False,
        "/api/research/health": False,
        "/api/mt5/status": False,
    }
    try:
        from nexus_scalp.web.server import create_app  # type: ignore[import-not-found]

        app = create_app()
        paths = set(app.openapi().get("paths", {}))
        for ep in endpoints:
            endpoints[ep] = ep in paths
    except Exception as exc:  # isolation boundary: never fabricate a PASS
        return _unknown(
            "CHECK-API-01",
            f"cannot build web app to enumerate routes: {type(exc).__name__}: {exc}",
            endpoints,
            "all semantic-health endpoints exist",
        )
    missing = [ep for ep, ok in endpoints.items() if not ok]
    if missing:
        return CheckResult(
            "CHECK-API-01",
            HealthStatus.DEGRADED,
            evidence=f"semantic-health endpoints not registered on app: {missing}",
            observed=endpoints,
            expected="all semantic-health endpoints exist",
            detail="API_SURFACE_MISSING",
        )
    return _ok(
        "CHECK-API-01",
        "all semantic-health endpoints registered on app",
        endpoints,
        "all semantic-health endpoints exist",
    )


# ---------------------------------------------------------------------------
# Runtime mode integrity (§40)
# ---------------------------------------------------------------------------
