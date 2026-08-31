"""Centralized forensic health checks — news / telegram / shadow checks (CHECK-NWS · CHECK-TEL · CHECK-SHD).

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
    _config_liquidity_enabled,
    _config_news_enabled,
    _iso_age_seconds,
    _load_runtime_config,
    _news_state,
    _ok,
    _shadow_state,
    _unknown,
)
from nexus_scalp.forensics.models import (
    CheckResult,
    HealthStatus,
)
from nexus_scalp.forensics.references import (
    FeatureReferenceRegistry,
)


def check_news_health(news_path: Path | None = None) -> CheckResult:
    """§24: worker progress + source health + usable-article reality.

    A source with HTTP 200 but 0 usable articles is NOT healthy (§25).
    """
    st = _news_state(news_path)
    if not st.get("exists"):
        return _unknown("CHECK-NWS-01", "news.db missing", st, "news.db")
    if st.get("error"):
        return _unknown(
            "CHECK-NWS-01", f"news.db unreadable: {st['error']}", st, "news.db readable"
        )
    (st.get("worker_state") or [{}])[0]
    sources = st.get("sources") or []
    health = st.get("source_health") or []
    problems: list[str] = []
    healthy_sources = 0
    enabled_sources = 0
    for s in sources:
        if not s.get("enabled"):
            continue
        enabled_sources += 1
        sid = s.get("source_id", "")
        hrow: dict[str, Any] = next((h for h in health if h.get("source_id") == sid), {})
        if hrow.get("healthy"):
            healthy_sources += 1
        else:
            problems.append(
                f"{sid}: consecutive_failures={hrow.get('consecutive_failures')} "
                f"last_status={hrow.get('last_status')}"
            )
    article_count = int(st.get("article_count") or 0)
    consensus = int(st.get("consensus_count") or 0)
    if enabled_sources and healthy_sources < enabled_sources:
        return CheckResult(
            "CHECK-NWS-01",
            HealthStatus.DEGRADED,
            evidence=f"{healthy_sources}/{enabled_sources} enabled sources healthy; "
            + "; ".join(problems[:5]),
            observed=st,
            expected="all enabled sources healthy",
            detail="NEWS_SOURCE_DEGRADATION",
        )
    if article_count == 0:
        return CheckResult(
            "CHECK-NWS-01",
            HealthStatus.DEGRADED,
            evidence="news worker running but 0 articles in DB",
            observed=st,
            expected="articles present",
            detail="NEWS_NO_DATA",
        )
    if consensus == 0:
        return CheckResult(
            "CHECK-NWS-01",
            HealthStatus.WARNING,
            evidence="0 consensus rows (parser may produce no signals)",
            observed=st,
            expected="consensus rows present",
            detail="NEWS_PARSER_INERT",
        )
    return _ok(
        "CHECK-NWS-01",
        f"{healthy_sources}/{enabled_sources} sources healthy; {article_count} articles, {consensus} consensus",
        st,
        "all enabled sources healthy",
    )


def check_news_worker_progress(news_path: Path | None = None) -> CheckResult:
    """§23/§24: worker RUNNING-stale / no-new-data detection."""
    st = _news_state(news_path)
    if not st.get("exists"):
        return _unknown("CHECK-NWS-02", "news.db missing", st, "news.db")
    worker = (st.get("worker_state") or [{}])[0]
    if not worker:
        return _unknown(
            "CHECK-NWS-02",
            "news_worker_state empty — worker never checkpointed",
            st,
            "worker checkpoint",
        )
    cycle_count = int(worker.get("cycle_count") or 0)
    last_cycle = worker.get("last_cycle_at") or ""
    age = _iso_age_seconds(last_cycle)
    if cycle_count == 0:
        return CheckResult(
            "CHECK-NWS-02",
            HealthStatus.DEGRADED,
            evidence="news worker checkpoint exists but 0 cycles recorded",
            observed=st,
            expected="cycle_count > 0",
            detail="WORKER_NO_PROGRESS",
        )
    if age is not None and age > 24 * 3600:
        return CheckResult(
            "CHECK-NWS-02",
            HealthStatus.DEGRADED,
            evidence=f"news worker last cycle {age / 3600:.1f}h ago ({last_cycle})",
            observed=st,
            expected="worker cycle within 24h",
            detail="WORKER_STALLED",
        )
    if age is None:
        return _unknown(
            "CHECK-NWS-02", "worker last_cycle_at unparseable", st, "worker cycle timestamp"
        )
    return _ok(
        "CHECK-NWS-02",
        f"news worker active: {cycle_count} cycles, last {age / 3600:.1f}h ago",
        st,
        "worker cycle within 24h",
    )


def check_news_availability_matrix() -> CheckResult:
    """§26: News ON/OFF x Liquidity ON/OFF runtime contract."""
    cfg = _load_runtime_config()
    if cfg is None:
        return _unknown(
            "CHECK-NWS-03", "cannot load config for availability matrix", {}, "config loadable"
        )
    news_on = bool(_config_news_enabled(cfg))
    liq_on = bool(_config_liquidity_enabled(cfg))
    cell = (
        f"{'News ON' if news_on else 'News OFF'} / {'Liquidity ON' if liq_on else 'Liquidity OFF'}"
    )
    feat = (
        "50D (Base only)"
        if not news_on and not liq_on
        else (
            "60D Base+News"
            if news_on and not liq_on
            else ("60D Base+Liquidity" if not news_on and liq_on else "70D Base+News+Liquidity")
        )
    )
    observed = {
        "news_enabled": news_on,
        "liquidity_enabled": liq_on,
        "cell": cell,
        "feature_contract": feat,
    }
    # completeness: news context requires news DB; liquidity requires frozen
    # algorithm + references.
    incomplete: list[str] = []
    if news_on and not Path("artifacts/news.db").exists():
        incomplete.append("news enabled but news.db missing")
    if liq_on and not Path("artifacts/candle_intel.db").exists():
        incomplete.append("liquidity enabled but candle_intel.db missing")
    if liq_on and len(FEATURE_REF_REGISTRY) == 0:
        incomplete.append("liquidity enabled but no frozen reference distribution")
    if incomplete:
        return CheckResult(
            "CHECK-NWS-03",
            HealthStatus.CRITICAL,
            evidence="; ".join(incomplete),
            observed=observed,
            expected="enabled families have their data + frozen references",
            detail="FEATURE_CONTRACT_INCOMPLETE",
        )
    return _ok(
        "CHECK-NWS-03",
        f"runtime contract unambiguous: {cell} -> {feat}",
        observed,
        "no ambiguous 60D/70D status",
    )


#: process-wide registry for the availability matrix check
FEATURE_REF_REGISTRY = FeatureReferenceRegistry()


# ---------------------------------------------------------------------------
# Shadow health (§27)
# ---------------------------------------------------------------------------


def check_shadow_health() -> CheckResult:
    """§27: shadow loaded / inference / errors / progress.

    Shadow tables are LAZY-schema: absence means never-attached (UNKNOWN),
    not PASS. A runtime health row saying shadow off but governance claiming
    running is a contradiction (DEGRADED).
    """
    st = _shadow_state()
    if not st.get("available"):
        return _unknown("CHECK-SHD-01", "shadow state unreadable", st, "audit.db readable")
    shadow_never = (
        st.get("shadow_runs") == "ABSENT"
        and st.get("model_shadow_comparisons") in ("ABSENT", 0)
        and st.get("model_runtime_health") in ("ABSENT", 0)
    )
    if shadow_never:
        return _unknown(
            "CHECK-SHD-01",
            "shadow never attached (no shadow tables/rows) — no progress evidence",
            st,
            "shadow history",
        )
    st.get("model_governance_state") or 0
    runtime_health = (st.get("latest_runtime_health") or [{}])[0]
    shadow_running = bool(runtime_health.get("shadow_running"))
    comparisons = int(runtime_health.get("shadow_comparisons") or 0)
    errors = int(runtime_health.get("shadow_errors") or 0)
    if shadow_running and comparisons == 0:
        return CheckResult(
            "CHECK-SHD-01",
            HealthStatus.DEGRADED,
            evidence="shadow reported RUNNING but 0 comparisons — WORKER_NO_PROGRESS",
            observed=st,
            expected="comparisons > 0 while running",
            detail="SHADOW_NO_PROGRESS",
        )
    if errors > 0 and comparisons == 0:
        return CheckResult(
            "CHECK-SHD-01",
            HealthStatus.WARNING,
            evidence=f"shadow errors {errors} with 0 comparisons — errors silently accumulating",
            observed=st,
            expected="comparisons > 0",
            detail="SHADOW_ERRORS_SILENT",
        )
    return _ok(
        "CHECK-SHD-01",
        f"shadow state: running={shadow_running}, comparisons={comparisons}, errors={errors}",
        st,
        "shadow produces comparisons when running",
    )


# ---------------------------------------------------------------------------
# Governance (§28/§29)
# ---------------------------------------------------------------------------


def check_telegram_delivery() -> CheckResult:
    """§32: notifier configuration + worker + queue + send counts (read-only)."""
    try:
        pass
    except Exception:
        pass  # type: ignore[assignment]
    try:
        from nexus_scalp.settings import load_settings_service  # type: ignore[import-not-found]

        svc = load_settings_service()
        status = svc.telegram_config_status()
    except Exception as exc:
        return _unknown(
            "CHECK-TEL-01",
            f"telegram settings unavailable: {exc!r}",
            {"error": str(exc)},
            "settings service",
        )
    observed: dict[str, Any] = {
        "configured": bool(status.get("configured")),
        "enabled": bool(status.get("enabled")),
        "source": status.get("source", ""),
    }
    if not status.get("configured"):
        return CheckResult(
            "CHECK-TEL-01",
            HealthStatus.WARNING,
            evidence="telegram NOT_CONFIGURED (delivery cannot be verified)",
            observed=observed,
            expected="telegram configured",
            detail="TELEGRAM_NOT_CONFIGURED",
        )
    if not status.get("enabled"):
        return CheckResult(
            "CHECK-TEL-01",
            HealthStatus.WARNING,
            evidence="telegram configured but DISABLED",
            observed=observed,
            expected="telegram enabled",
            detail="TELEGRAM_DISABLED",
        )
    # worker-level evidence when a notifier instance is reachable via settings
    try:
        notifier = getattr(svc, "notifier", None) or getattr(svc, "_notifier", None)
        if notifier is not None and hasattr(notifier, "health_state"):
            hs = notifier.health_state()
            observed["worker"] = hs
            if hs.get("failed_count", 0) > 0 and hs.get("sent_count", 0) == 0:
                return CheckResult(
                    "CHECK-TEL-01",
                    HealthStatus.DEGRADED,
                    evidence=f"telegram worker {hs.get('status')}: {hs.get('failed_count')} failed, 0 sent",
                    observed=observed,
                    expected="sent_count > 0 or no failures",
                    detail="TELEGRAM_SILENT_FAILURE",
                )
    except Exception:
        pass
    return _ok(
        "CHECK-TEL-01",
        "telegram configured and enabled",
        observed,
        "telegram delivery path available",
    )


# ---------------------------------------------------------------------------
# Trace completeness (§33-35) and silent fallback (§36)
# ---------------------------------------------------------------------------
