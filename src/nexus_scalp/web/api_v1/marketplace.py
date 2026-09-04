"""
API v1 — marketplace domain (CHG-0056) — frozen contract (ARCH_SPEC §3).

Router prefix /api/v1/marketplace, tags ["marketplace"], envelope via common.py.
Every route is bounded: _try() isolation, parameterized reads, no untrusted SQL.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from nexus_scalp.web.api_v1.common import (
    build_page,
    fail,
    iso_or_none,
    ok,
    parse_pagination,
)

router = APIRouter(prefix="/api/v1/marketplace", tags=["marketplace"])

# ---------------------------------------------------------------------------
# Singletons (bounded, never write to audit.db; marketplace.db only)
# ---------------------------------------------------------------------------


def _service(request: Request) -> Any:
    """Lazily-cached marketplace service bound to app.state (isolated DB)."""
    svc = getattr(request.app.state, "marketplace_service", None)
    if svc is not None:
        return svc
    from nexus_scalp.marketplace.service import MarketplaceService

    svc = MarketplaceService()
    request.app.state.marketplace_service = svc
    return svc


def _snapshot_store(request: Request) -> Any:
    store = getattr(request.app.state, "marketplace_snapshots", None)
    if store is not None:
        return store
    from nexus_scalp.marketplace.snapshot import StrategyRuntimeSnapshotStore

    store = StrategyRuntimeSnapshotStore()
    request.app.state.marketplace_snapshots = store
    return store


def _try(fn: Any, resource: str) -> Any:
    try:
        return fn()
    except Exception:
        from nexus_scalp.observability.logging import get_logger
        from nexus_scalp.web.errors import log_web_error

        log_web_error(
            get_logger("nexus_scalp.web.api_v1.marketplace"),
            f"/api/v1/marketplace {resource}",
            None,
            RuntimeError(f"{resource} failed"),
            resource=resource,
        )
        return None


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class InstallBody(BaseModel):
    count: int | None = None


class EnableBody(BaseModel):
    mode: str


class RepairBody(BaseModel):
    trigger: str = ""


# ---------------------------------------------------------------------------
# Routes (frozen contract §3)
# ---------------------------------------------------------------------------


@router.get("/packs", summary="Installed + available pack catalog")
def list_packs(request: Request) -> Any:
    from nexus_scalp.marketplace.packs import REGISTRY

    svc = _service(request)
    # installed packs ledger
    installed = _try(
        lambda: svc.store.query_all(
            "SELECT pack_id, version, seed_count, installed_at FROM mk_packages", ()
        ),
        "mk_packages",
    )
    installed_map = {r["pack_id"]: r for r in (installed or [])}
    catalog = []
    for pid, meta in REGISTRY.items():
        seed_cnt = 0
        if pid in installed_map:
            seed_cnt = int(installed_map[pid].get("seed_count", 0))
        catalog.append(
            {
                "id": meta["id"],
                "name": meta["name"],
                "family": meta["family"],
                "description": meta["description"],
                "installed": pid in installed_map,
                "seed_count": seed_cnt,
            }
        )
    return ok(request, {"packs": catalog, "count": len(catalog)})


@router.post("/packs/{pack_id}/install", summary="Install seeds from a pack (idempotent)")
def install_pack(request: Request, pack_id: str, body: InstallBody | None = None) -> Any:
    from nexus_scalp.marketplace.packs import REGISTRY, get_generator

    if pack_id not in REGISTRY:
        return fail(request, "RESOURCE_NOT_FOUND", message=f"pack {pack_id} not found")
    count = int(body.count if body is not None and body.count is not None else 25)
    if count < 1 or count > 500:
        return fail(request, "VALIDATION_ERROR", details={"count": "must be between 1 and 500"})
    gen = get_generator(pack_id)
    seeds = gen(count=count, version="1.0.0")
    svc = _service(request)
    meta = REGISTRY[pack_id]
    result = _try(
        lambda: svc.install_pack(
            pack_id,
            seeds,
            pack_name=meta["name"],
            family=meta["family"],
            description=meta["description"],
            version="1.0.0",
        ),
        "install_pack",
    )
    if result is None:
        return fail(request, "INTERNAL_ERROR", message="pack install failed")
    return ok(request, result, status_code=201)


@router.get("/seeds", summary="Paginated seeds (family/status/q)")
def list_seeds(
    request: Request,
    family: str | None = Query(None, max_length=64),
    status: str | None = Query(None, max_length=32),
    q: str | None = Query(None, max_length=128),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
) -> Any:
    svc = _service(request)
    checked = parse_pagination(page, page_size)
    if not isinstance(checked, tuple):
        return checked
    p, ps = checked
    result = _try(
        lambda: svc.list_seeds(family=family, status=status, q=q, page=p, page_size=ps),
        "list_seeds",
    )
    if result is None:
        return fail(request, "DEPENDENCY_UNAVAILABLE")
    return ok(request, build_page(result["items"], p, ps, has_more=result["has_more"]))


@router.get(
    "/seeds/{seed_id}", summary="Seed detail (lifecycle events + latest scores + gates + repairs)"
)
def seed_detail(request: Request, seed_id: str) -> Any:
    svc = _service(request)
    rec = _try(lambda: svc.get_seed_detail(seed_id), "seed_detail")
    if rec is None:
        return fail(request, "RESOURCE_NOT_FOUND", message=f"seed {seed_id} not found")
    # bound isolation: ensure iso timestamps
    for k in ("created_at", "updated_at"):
        rec[k] = iso_or_none(rec.get(k))
    return ok(request, rec)


@router.post("/seeds/{seed_id}/enable", summary="Enable a seed for a mode (gates enforced)")
def enable_seed(request: Request, seed_id: str, body: EnableBody) -> Any:
    svc = _service(request)
    result = _try(lambda: svc.enable(seed_id, body.mode), "enable")
    if result is None:
        return fail(request, "INTERNAL_ERROR")
    # LIVE_REQUEST PENDING is a distinct denier — still returns 200; the
    # contract never grants silently (ARCH_SPEC §2)
    if result.get("status") == "PENDING":
        return ok(request, result, status_code=202)
    if result.get("status") == "DENIED":
        return fail(
            request,
            "FORBIDDEN",
            message=result.get("reason", ""),
            details={"seed_id": seed_id, "mode": body.mode},
        )
    return ok(request, result)


@router.post("/seeds/{seed_id}/disable", summary="Disable a seed (DISABLED/RETIRED transition)")
def disable_seed(request: Request, seed_id: str) -> Any:
    svc = _service(request)
    result = _try(lambda: svc.disable(seed_id), "disable")
    if result is None or result.get("error") == "not_found":
        return fail(request, "RESOURCE_NOT_FOUND", message=f"seed {seed_id} not found")
    if result.get("error"):
        return fail(
            request, "FORBIDDEN", message=str(result.get("error") or result.get("reason", ""))
        )
    if result.get("status") == "rejected":
        return fail(request, "CONFLICT", message=str(result.get("reason", "")))
    return ok(request, result)


@router.post(
    "/seeds/{seed_id}/run-research", summary="Queue a research run for one seed (existing pipeline)"
)
def run_research(request: Request, seed_id: str) -> Any:
    svc = _service(request)
    result = _try(lambda: svc.run_research(seed_id), "run_research")
    if result is None:
        return fail(request, "INTERNAL_ERROR")
    if result.get("error") == "not_found":
        return fail(request, "RESOURCE_NOT_FOUND", message=f"seed {seed_id} not found")
    if result.get("error"):
        return fail(request, "VALIDATION_ERROR", message=str(result["error"]))
    return ok(request, result, status_code=202)


@router.get("/rankings", summary="Rank seeds by dimension/profile")
def rankings(
    request: Request,
    dimension: str | None = Query(None, max_length=32),
    profile_id: str | None = Query(None, max_length=64),
) -> Any:
    svc = _service(request)
    dim = (dimension or "OVERALL").upper()
    # rankings derive from latest 14-factor total (mk_score_snapshots)
    rows = _try(
        lambda: svc.store.query_all(
            "SELECT seed_id, total, verdict, profile_id, created_at FROM mk_score_snapshots ORDER BY created_at DESC",
            (),
        ),
        "rankings",
    )
    rows = rows or []
    # de-dupe by seed_id (keep latest)
    latest: dict[str, Any] = {}
    for r in rows:
        if r["seed_id"] not in latest:
            latest[r["seed_id"]] = r
    # enrich with seed metadata for family/status
    seeds = _try(
        lambda: svc.store.query_all("SELECT seed_id, family, lifecycle FROM mk_seeds", ()),
        "rankings_meta",
    )
    meta_map = {r["seed_id"]: r for r in (seeds or [])}
    ranked = []
    for sid, snap in latest.items():
        meta = meta_map.get(sid, {})
        ranked.append(
            {
                "seed_id": sid,
                "family": meta.get("family", ""),
                "lifecycle": meta.get("lifecycle", ""),
                "total": snap.get("total"),
                "verdict": snap.get("verdict"),
                "profile_id": snap.get("profile_id"),
                "scored_at": iso_or_none(snap.get("created_at")),
            }
        )
    ranked.sort(
        key=lambda r: r["total"] if isinstance(r.get("total"), (int, float)) else -1, reverse=True
    )
    return ok(
        request,
        {
            "items": ranked,
            "dimension": dim,
            "profile_id": profile_id or "default",
            "has_more": False,
        },
    )


@router.post(
    "/seeds/{seed_id}/repair", summary="Create a repaired seed from a trigger (evolution operators)"
)
def repair_seed(request: Request, seed_id: str, body: RepairBody) -> Any:
    from nexus_scalp.marketplace.repair import mutated_seed_from_trigger, repair_record_payload

    svc = _service(request)
    trigger = (body.trigger or "repair").strip() or "repair"
    row = _try(
        lambda: svc.store.driver.query_one(
            "SELECT seed_id, dsl, version FROM mk_seeds WHERE seed_id = ? LIMIT 1", (seed_id,)
        ),
        "repair_lookup",
    )
    if row is None:
        return fail(request, "RESOURCE_NOT_FOUND", message=f"seed {seed_id} not found")
    # load parent spec
    try:
        import json as _json

        from nexus_scalp.marketplace.models import SeedSpec
        from nexus_scalp.strategies.factory.models import StrategyDsl

        dsl_raw = _json.loads(row["dsl"]) if isinstance(row, dict) else {}  # type: ignore[operator]
        parent = None
        # reconstruct via service detail
        detail = _try(lambda: svc.get_seed_detail(seed_id), "repair_parent_detail")
        if detail is not None:
            dsl = StrategyDsl(**dsl_raw)  # type: ignore[arg-type]
            parent = SeedSpec(
                seed_id=seed_id,
                name=str(detail.get("name", seed_id)),
                family=str(detail.get("family", "PRICE_ACTION")),
                version=str(row["version"] if isinstance(row, dict) else "1.0.0"),
                description=str(detail.get("description", "")),
                dsl=dsl,
            )  # minimal; mutated_seed only needs dsl/family/source + metadata preserved
            child = _try(lambda: mutated_seed_from_trigger(parent, trigger), "mutated_seed")
            if child is None:
                rec = repair_record_payload(seed_id, seed_id, trigger, status="FAILED")
                # append row honestly as FAILED (append-only mk_repairs)
                conn = svc.store.driver.connect()
                try:
                    svc.store.driver.upsert(
                        "mk_repairs",
                        {
                            "repair_id": rec["repair_id"],
                            "seed_id": rec["seed_id"],
                            "parent_seed_id": rec["parent_seed_id"],
                            "trigger": trigger,
                            "status": "FAILED",
                            "outcome": json.dumps({"reason": "operator produced no child"}),
                            "created_at": rec["created_at"],
                        },
                        conn=conn,
                    )
                    svc.store.driver.commit(conn)
                except Exception:
                    with contextlib.suppress(Exception):
                        conn.rollback()
                finally:
                    conn.close()
                return fail(
                    request,
                    "VALIDATION_ERROR",
                    message="repair operator produced no valid child",
                    details={"seed_id": seed_id},
                )
            # install child as INSTALLED pack seed
            svc.install_pack(
                f"repair:{seed_id}",
                [child],
                pack_name=f"Repair of {seed_id}",
                family=child.family,
                version=child.version,
            )
            rec = repair_record_payload(child.seed_id, seed_id, trigger, status="PENDING")
            conn2 = svc.store.driver.connect()
            try:
                svc.store.driver.upsert(
                    "mk_repairs",
                    {
                        "repair_id": rec["repair_id"],
                        "seed_id": child.seed_id,
                        "parent_seed_id": seed_id,
                        "trigger": trigger,
                        "status": "PENDING",
                        "outcome": json.dumps({}),
                        "created_at": rec["created_at"],
                    },
                    conn=conn2,
                )
                svc.store.driver.commit(conn2)
            finally:
                conn2.close()
            return ok(
                request,
                {
                    "repair_id": rec["repair_id"],
                    "parent_seed_id": seed_id,
                    "child_seed_id": child.seed_id,
                    "status": "PENDING",
                    "trigger": trigger,
                },
                status_code=201,
            )
    except Exception as e:
        from nexus_scalp.observability.logging import get_logger
        from nexus_scalp.web.errors import log_web_error

        log_web_error(
            get_logger("nexus_scalp.web.api_v1.marketplace"), "/api/v1/marketplace/repair", None, e
        )
        return fail(request, "INTERNAL_ERROR")
    return fail(request, "INTERNAL_ERROR", message="repair path unreachable")


@router.get("/repairs", summary="Repair attempts (seed filter via query)")
def list_repairs(request: Request, seed_id: str | None = Query(None, max_length=128)) -> Any:
    svc = _service(request)
    if seed_id:
        rows = _try(
            lambda: svc.store.query_all(
                "SELECT * FROM mk_repairs WHERE seed_id = ? OR parent_seed_id = ? ORDER BY created_at DESC",
                (seed_id, seed_id),
            ),
            "repairs",
        )
    else:
        rows = _try(
            lambda: svc.store.query_all(
                "SELECT * FROM mk_repairs ORDER BY created_at DESC LIMIT 100", ()
            ),
            "repairs_all",
        )
    return ok(request, {"items": [dict(r) for r in (rows or [])]})


@router.get(
    "/runtime-snapshot", summary="Current runtime-enabled seed set (immutable versioned snapshot)"
)
def runtime_snapshot(request: Request) -> Any:
    # snapshot store carries the atomic enabled set (RuntimeConfig pattern);
    # service not needed here (read-only current snapshot version).
    snap_store = _snapshot_store(request)
    snap = snap_store.get_snapshot()
    return ok(
        request,
        {
            "version": snap.version,
            "enabled_set": sorted(snap.enabled_set),
            "created_at": snap.created_at,
            "source": snap.source,
        },
    )


@router.get("/scores/{seed_id}/history", summary="14-factor snapshot history for one seed")
def score_history(
    request: Request,
    seed_id: str,
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(50, ge=1, le=200),
) -> Any:
    checked = parse_pagination(page, page_size)
    if not isinstance(checked, tuple):
        return checked
    p, ps = checked
    svc = _service(request)
    # validate seed exists
    exists = _try(
        lambda: svc.store.driver.query_one(
            "SELECT seed_id FROM mk_seeds WHERE seed_id = ? LIMIT 1", (seed_id,)
        ),
        "score_seed_exists",
    )
    if exists is None:
        return fail(request, "RESOURCE_NOT_FOUND", message=f"seed {seed_id} not found")
    rows = _try(
        lambda: svc.store.query_all(
            "SELECT * FROM mk_score_snapshots WHERE seed_id = ? ORDER BY created_at DESC",
            (seed_id,),
        ),
        "score_history",
    )
    rows = rows or []
    # decode factors field for transport
    decoded = []
    for r in rows:
        d = dict(r)
        with contextlib.suppress(Exception):
            d["factors"] = json.loads(d.get("factors") or "{}")
        for k in ("created_at",):
            d[k] = iso_or_none(d.get(k))
        decoded.append(d)
    start = (p - 1) * ps
    page_rows = decoded[start : start + ps]
    return ok(request, build_page(page_rows, p, ps, has_more=len(decoded) > start + ps))


__all__ = ["router"]
