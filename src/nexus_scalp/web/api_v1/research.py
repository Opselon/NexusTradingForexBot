"""API v1 — RESEARCH domain (status/strategies/runs/datasets).

Sources verified in docs/api/API_PLATFORM_V1.md §7 (research.py, capabilities 51-55).
Reads research/store.py + research/registry.py over the authoritative audit DB.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from nexus_scalp.web.api_v1.common import (
    build_page,
    fail,
    get_audit_repo,
    iso_or_none,
    ok,
    parse_pagination,
    utc_now_iso,
)

router = APIRouter(prefix="/api/v1/research", tags=["research"])


def _try(fn: Any, resource: str) -> Any:
    try:
        return fn()
    except Exception:
        from nexus_scalp.observability.logging import get_logger
        from nexus_scalp.web.errors import log_web_error

        log_web_error(
            get_logger("nexus_scalp.web.api_v1"),
            f"/api/v1/research {resource}",
            None,
            RuntimeError(f"{resource} read failed"),
            resource=resource,
        )
        return None


@router.get("/status", summary="Research subsystem status (health + registry summary)")
def research_status(request: Request) -> Any:
    repo = get_audit_repo(request)

    def _run_health() -> Any:
        from nexus_scalp.research.store import research_health_summary

        return research_health_summary(repo)

    def _run_registry() -> Any:
        from nexus_scalp.research.store import registry_summary

        return registry_summary(repo)

    health = _try(_run_health, "research_store")
    registry = _try(_run_registry, "research_registry")
    return ok(
        request,
        {
            "available": health is not None or registry is not None,
            "health": health,
            "registry": registry,
            "generated_at": utc_now_iso(),
        },
    )


@router.get("/strategies", summary="Strategy registry entries (lifecycle filter)")
def research_strategies(
    request: Request,
    lifecycle: str | None = Query(None, max_length=32),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
) -> Any:
    checked = parse_pagination(page, page_size)
    if not isinstance(checked, tuple):
        return checked
    p, ps = checked
    repo = get_audit_repo(request)

    def _run() -> Any:
        from nexus_scalp.research.registry import StrategyRegistry

        return StrategyRegistry(audit_repo=repo).list(
            lifecycle=lifecycle, limit=ps + 1 + (p - 1) * ps
        )

    entries = _try(_run, "research_registry")
    if entries is None:
        return fail(request, "DEPENDENCY_UNAVAILABLE", message="strategy registry unavailable")
    start = (p - 1) * ps
    page_entries = entries[start : start + ps]
    has_more = len(entries) > start + ps
    out = [
        e.model_dump()
        if hasattr(e, "model_dump")
        else (e.__dict__ if hasattr(e, "__dict__") else str(e))
        for e in page_entries
    ]
    typed_out: list[dict[str, Any]] = [o if isinstance(o, dict) else {"entry": o} for o in out]
    for e in typed_out:
        for f in ("created_at", "updated_at", "timestamp", "registered_at"):
            if f in e:
                e[f] = iso_or_none(e.get(f))
    return ok(request, build_page(typed_out, p, ps, has_more=has_more))


@router.get("/strategies/{strategy_id}", summary="Strategy registry detail + invariant check")
def research_strategy_detail(request: Request, strategy_id: str) -> Any:
    repo = get_audit_repo(request)

    def _run() -> Any:
        from nexus_scalp.research.registry import StrategyRegistry

        return StrategyRegistry(audit_repo=repo).get(strategy_id)

    entry = _try(_run, "research_registry")
    if entry is None:
        return fail(request, "RESOURCE_NOT_FOUND", message=f"strategy {strategy_id} not found")
    dump = entry.model_dump() if hasattr(entry, "model_dump") else getattr(entry, "__dict__", {})
    invariant = _try(lambda: StrategyRegistryInvariantCheck(repo, entry), "invariant_check")
    return ok(request, {"entry": dump, "invariant": invariant})


def StrategyRegistryInvariantCheck(repo: Any, entry: Any) -> Any:
    """Bounded adapter calling registry.invariant_check (keeps route bodies thin)."""
    from nexus_scalp.research.registry import StrategyRegistry

    return StrategyRegistry(audit_repo=repo).invariant_check(entry)


@router.get("/runs", summary="Paginated research run inventory (reproducibility lineage)")
def research_runs(
    request: Request,
    strategy_id: str | None = Query(None, max_length=64),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
) -> Any:
    checked = parse_pagination(page, page_size)
    if not isinstance(checked, tuple):
        return checked
    p, ps = checked
    repo = get_audit_repo(request)

    def _run() -> Any:
        from nexus_scalp.research.store import list_research_runs

        return list_research_runs(repo, strategy_id=strategy_id, limit=ps + 1 + (p - 1) * ps)

    runs = _try(_run, "research_runs")
    if runs is None:
        return fail(request, "DEPENDENCY_UNAVAILABLE", message="research runs unavailable")
    start = (p - 1) * ps
    page_rows = runs[start : start + ps]
    has_more = len(runs) > start + ps
    for r in page_rows:
        for f in ("executed_at", "created_at", "timestamp"):
            if f in r:
                r[f] = iso_or_none(r.get(f))
    return ok(request, build_page(page_rows, p, ps, has_more=has_more))


@router.get("/datasets", summary="Distinct dataset ids + provenance (from real runs)")
def research_datasets(request: Request) -> Any:
    repo = get_audit_repo(request)

    def _run() -> Any:
        from nexus_scalp.research.store import list_research_runs

        return list_research_runs(repo, strategy_id=None, limit=500)

    runs = _try(_run, "research_runs")
    datasets: dict[str, int] = {}
    if runs:
        for r in runs:
            ds = r.get("dataset_id")
            if ds:
                datasets[str(ds)] = datasets.get(str(ds), 0) + 1
    return ok(
        request,
        {
            "datasets": [{"dataset_id": k, "run_count": v} for k, v in sorted(datasets.items())],
            "distinct": len(datasets),
            "generated_at": utc_now_iso(),
        },
    )
