"""API v1 aggregate routers (CHG-0043): system snapshot + read-only enforcement.

  GET /api/v1/snapshot - ONE bounded composite snapshot for agents/dashboards
                         (status + version + mode + capabilities + freshness).
  ANY  /api/v1/{rest}  - 405 READ_ONLY truth-teller for unsupported methods on
                         the versioned surface (documents the read-only contract
                         in-band instead of a generic 405).

USED BY: web/api_v1_wiring.py.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from nexus_scalp.api.v1.common import envelope, error_envelope, utc_now_iso
from nexus_scalp.api.v1.deps import get_engine

router = APIRouter(prefix="/api/v1", tags=["automation"])


@router.get("/snapshot", summary="Composite bounded snapshot for agents/dashboards")
def snapshot(request: Request) -> dict[str, Any]:
    from nexus_scalp.api.v1.system import _build_capabilities, _engine_identity, _mode_info

    engine = get_engine(request)
    mode = _mode_info(engine)
    freshness: Any = None
    if engine is not None and hasattr(engine, "compute_live_freshness"):
        try:
            freshness = engine.compute_live_freshness()
        except Exception:
            freshness = {"status": "UNAVAILABLE"}
    data = {
        "overall": "OPERATIONAL"
        if mode["engine_running"]
        else ("UNAVAILABLE" if engine is None else "STOPPED"),
        "mode": mode,
        "engine_identity": _engine_identity(engine),
        "freshness": freshness,
        "capabilities": _build_capabilities(request),
        "generated_at": utc_now_iso(),
    }
    return envelope(request, data)


async def _read_only_reject(request: Request, rest_of_path: str) -> JSONResponse:
    """Shared body: the v1 surface is read-only by contract; mutations are
    rejected in-band with a stable code (not a bare 405) so agents can rely on it."""
    body = error_envelope(
        request,
        "READ_ONLY",
        message="The /api/v1 surface is read-only; this method is not available.",
        details={"path": f"/api/v1/{rest_of_path}"},
    )
    return JSONResponse(status_code=405, content=body)


@router.post("/{rest_of_path:path}", include_in_schema=True, name="read_only_guard_post")
async def read_only_guard_post(request: Request, rest_of_path: str) -> JSONResponse:
    return await _read_only_reject(request, rest_of_path)


@router.put("/{rest_of_path:path}", include_in_schema=True, name="read_only_guard_put")
async def read_only_guard_put(request: Request, rest_of_path: str) -> JSONResponse:
    return await _read_only_reject(request, rest_of_path)


@router.patch("/{rest_of_path:path}", include_in_schema=True, name="read_only_guard_patch")
async def read_only_guard_patch(request: Request, rest_of_path: str) -> JSONResponse:
    return await _read_only_reject(request, rest_of_path)


@router.delete("/{rest_of_path:path}", include_in_schema=True, name="read_only_guard_delete")
async def read_only_guard_delete(request: Request, rest_of_path: str) -> JSONResponse:
    return await _read_only_reject(request, rest_of_path)
