"""API v1 POSITION/EXECUTION + MODEL/FEATURES routers (CHG-0043).

Truthful position/execution/model/feature surface:
  GET /api/v1/positions            - current open positions (broker adapter snapshot)
  GET /api/v1/positions/{ticket}   - one position detail
  GET /api/v1/execution/status     - execution subsystem status (adapter state)
  GET /api/v1/execution/history    - paginated execution/ledger history (audit_ledger)
  GET /api/v1/model/status         - serving model state (engine bundle attrs)
  GET /api/v1/model/identity       - artifact identity / manifest / schema hash
  GET /api/v1/model/contracts      - model contract inventory (inference validator compat)
  GET /api/v1/features/contract    - active feature contract (schema_contract.py = SSoT)
  GET /api/v1/features/groups      - feature groups/dimensions (Base|News|Liquidity)
  GET /api/v1/features/current     - last computed feature vector (engine state)

All values read REAL state; absent state returns available=false truthfully.
USED BY: web/api_v1_wiring.py.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from nexus_scalp.api.v1.common import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    envelope,
    iso_or_none,
    utc_now_iso,
)
from nexus_scalp.api.v1.deps import get_audit_repository, get_engine

router = APIRouter(prefix="/api/v1", tags=["positions", "execution", "model", "features"])


def _require_engine(request: Request) -> Any:
    engine = get_engine(request)
    if engine is None:
        raise HTTPException(
            status_code=503, detail={"code": "DEPENDENCY_UNAVAILABLE", "dependency": "engine"}
        )
    return engine


# ---------------------------------------------------------------------------
# POSITIONS
# ---------------------------------------------------------------------------


@router.get("/positions", summary="Current open positions (real broker adapter snapshot)")
def positions_list(
    request: Request, symbol: str | None = Query(None, max_length=32)
) -> dict[str, Any]:
    engine = _require_engine(request)
    adapter = getattr(engine, "adapter", None)
    if adapter is None:
        raise HTTPException(
            status_code=503, detail={"code": "DEPENDENCY_UNAVAILABLE", "dependency": "adapter"}
        )
    try:
        snaps = adapter.get_all_positions(symbol=symbol)
    except Exception as exc:
        from nexus_scalp.observability.logging import get_logger
        from nexus_scalp.web.errors import log_web_error

        log_web_error(
            get_logger("nexus_scalp.api.v1"), "/api/v1/positions", None, exc, resource="adapter"
        )
        raise HTTPException(status_code=503, detail={"code": "DEPENDENCY_UNAVAILABLE"}) from exc
    out: list[dict[str, Any]] = []
    for s in snaps:
        out.append(
            {
                "ticket": getattr(s, "ticket", None),
                "symbol": getattr(s, "symbol", None),
                "type": getattr(s, "type", None),
                "volume": getattr(s, "volume", None),
                "price_open": getattr(s, "price_open", None),
                "price_current": getattr(s, "price_current", None),
                "sl": getattr(s, "sl", None),
                "tp": getattr(s, "tp", None),
                "profit": getattr(s, "profit", None),
                "swap": getattr(s, "swap", None),
                "commission": getattr(s, "commission", None),
                "time": iso_or_none(getattr(s, "time", None)),
                "magic": getattr(s, "magic", None),
                "comment": getattr(s, "comment", None),
            }
        )
    return envelope(request, {"positions": out, "count": len(out), "probed_at": utc_now_iso()})


@router.get("/positions/{ticket}", summary="One open position by broker ticket")
def position_detail(request: Request, ticket: int) -> dict[str, Any]:
    engine = _require_engine(request)
    adapter = getattr(engine, "adapter", None)
    if adapter is None:
        raise HTTPException(
            status_code=503, detail={"code": "DEPENDENCY_UNAVAILABLE", "dependency": "adapter"}
        )
    try:
        snaps = adapter.get_all_positions(symbol=None)
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"code": "DEPENDENCY_UNAVAILABLE"}) from exc
    match = next((s for s in snaps if getattr(s, "ticket", None) == ticket), None)
    if match is None:
        raise HTTPException(
            status_code=404, detail={"code": "NOT_FOUND", "resource": "position", "ticket": ticket}
        )
    data = {
        "ticket": getattr(match, "ticket", None),
        "symbol": getattr(match, "symbol", None),
        "type": getattr(match, "type", None),
        "volume": getattr(match, "volume", None),
        "price_open": getattr(match, "price_open", None),
        "price_current": getattr(match, "price_current", None),
        "sl": getattr(match, "sl", None),
        "tp": getattr(match, "tp", None),
        "profit": getattr(match, "profit", None),
        "swap": getattr(match, "swap", None),
        "commission": getattr(match, "commission", None),
        "time": iso_or_none(getattr(match, "time", None)),
    }
    return envelope(request, data)


# ---------------------------------------------------------------------------
# EXECUTION
# ---------------------------------------------------------------------------


@router.get("/execution/status", summary="Execution subsystem status (adapter connection state)")
def execution_status(request: Request) -> dict[str, Any]:
    engine = _require_engine(request)
    adapter = getattr(engine, "adapter", None)
    connected = None
    state: Any = None
    if adapter is not None:
        try:
            conn_state = adapter.connection_state()
            state = getattr(conn_state, "value", conn_state)
        except Exception:
            state = None
        try:
            connected = bool(adapter.is_connected())
        except Exception:
            connected = None
    data = {
        "adapter_present": adapter is not None,
        "connection_state": state,
        "connected": connected,
        "engine_running": bool(getattr(engine, "_running", False)),
        "probed_at": utc_now_iso(),
    }
    return envelope(request, data)


@router.get("/execution/history", summary="Paginated execution/ledger history (audit_ledger)")
def execution_history(
    request: Request,
    status: str | None = Query(None, max_length=24, description="audit_ledger status filter."),
    symbol: str | None = Query(None, max_length=32),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> dict[str, Any]:
    repo = get_audit_repository(request)

    where: list[str] = []
    args: list[Any] = []
    if status:
        where.append("status = ?")
        args.append(status)
    if symbol:
        where.append("symbol = ?")
        args.append(symbol)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{repo._db_path}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(
                f"SELECT * FROM audit_ledger {where_sql} ORDER BY ticket DESC LIMIT ? OFFSET ?",
                (*args, page_size, (page - 1) * page_size),
            )
            raw = [dict(r) for r in cur.fetchall()]
            total_row = conn.execute(
                f"SELECT COUNT(*) AS c FROM audit_ledger {where_sql}", tuple(args)
            ).fetchone()
            total = int(total_row["c"]) if total_row else 0
        finally:
            conn.close()
    except Exception as exc:
        from nexus_scalp.api.v1.deps import _dep_unavailable

        raise _dep_unavailable("audit_ledger", exc) from exc
    for r in raw:
        for ts_field in ("opened_at", "closed_at", "time"):
            if ts_field in r:
                r[ts_field] = iso_or_none(r.get(ts_field))
        payload = r.pop("payload", None)
        if payload:
            try:
                parsed = json.loads(payload) if isinstance(payload, str) else payload
                r["payload"] = parsed if isinstance(parsed, dict) else {}
            except (TypeError, ValueError):
                r["payload"] = {}
    pagination = {
        "page": page,
        "page_size": page_size,
        "total_items": total,
        "has_next": page * page_size < total,
        "has_prev": page > 1,
    }
    return envelope(request, {"executions": raw}, pagination=pagination)


# ---------------------------------------------------------------------------
# MODEL / FEATURES
# ---------------------------------------------------------------------------


@router.get("/model/status", summary="Serving model state (engine bundle presence + warmup)")
def model_status(request: Request) -> dict[str, Any]:
    engine = _require_engine(request)
    bundle = getattr(engine, "_bundle", None)
    data = {
        "bundle_loaded": bundle is not None,
        "inference_enabled": bool(getattr(engine, "_inference_enabled", False)),
        "warmup_state": getattr(engine, "warmup_state", None),
        "runtime_mode": getattr(engine, "_runtime_mode", None),
        "probed_at": utc_now_iso(),
    }
    return envelope(request, data)


@router.get("/model/identity", summary="Artifact identity / manifest / schema fingerprint")
def model_identity(request: Request) -> dict[str, Any]:
    engine = _require_engine(request)
    bundle = getattr(engine, "_bundle", None)
    if bundle is None:
        return envelope(request, {"available": False, "reason": "no model bundle loaded"})
    manifest = getattr(bundle, "manifest", None)
    identity: dict[str, Any] = {"available": True}
    for attr in ("artifact_id", "model_id", "schema_id", "scaler", "version", "created_at"):
        value = getattr(manifest, attr, None) if manifest is not None else None
        if value is None:
            value = getattr(bundle, attr, None)
        if value is not None:
            identity[attr] = value if not hasattr(value, "model_dump") else value.model_dump()
    try:
        from nexus_scalp.features.schema_contract import feature_schema_hash

        identity["feature_schema_hash"] = feature_schema_hash()
    except Exception:
        identity["feature_schema_hash"] = None
    return envelope(request, identity)


@router.get("/model/contracts", summary="Model/feature contract compatibility inventory")
def model_contracts(request: Request) -> dict[str, Any]:
    engine = get_engine(request)
    contracts: dict[str, Any] = {
        "feature_schema_ids": None,
        "compatible_model_schemas": None,
        "generated_at": utc_now_iso(),
    }
    try:
        from nexus_scalp.features.schema_contract import expected_schema_ids

        contracts["feature_schema_ids"] = list(expected_schema_ids())
    except Exception as exc:
        from nexus_scalp.observability.logging import get_logger
        from nexus_scalp.web.errors import log_web_error

        log_web_error(
            get_logger("nexus_scalp.api.v1"),
            "/api/v1/model/contracts",
            None,
            exc,
            resource="schema_contract",
        )
        contracts["feature_schema_ids_error"] = "DEPENDENCY_UNAVAILABLE"
    # Compatibility matrix from the inference validator (real function).
    try:
        from nexus_scalp.features.inference_validator import compatible_model_schema

        contracts["compatible_model_schemas"] = compatible_model_schema()  # type: ignore[call-arg]
    except TypeError:
        contracts["compatible_model_schemas"] = None
    except Exception:
        contracts["compatible_model_schemas"] = None
    if engine is not None and getattr(engine, "_bundle", None) is not None:
        contracts["serving_bundle_present"] = True
    return envelope(request, contracts)


@router.get("/features/contract", summary="Active feature contract (canonical SSoT registry)")
def features_contract(request: Request) -> dict[str, Any]:
    try:
        from nexus_scalp.features.schema_contract import (
            canonical_feature_names,
            feature_schema_hash,
            registry_has_canonical_70d,
        )

        names = canonical_feature_names()
        groups: dict[str, list[int]] = {"base_0_49": [], "news_50_59": [], "liquidity_60_69": []}
        from nexus_scalp.features.schema_contract import family_of

        for i, _n in enumerate(names):
            fam = family_of(i)
            if fam in groups:
                groups[fam].append(i)
        data = {
            "schema_id": None,
            "feature_count": len(names),
            "feature_schema_hash": feature_schema_hash(),
            "registry_canonical": registry_has_canonical_70d(),
            "groups": {k: {"count": len(v), "indices": v[:70]} for k, v in groups.items()},
            "first_10_names": list(names[:10]),
        }
        try:
            from nexus_scalp.features.schema_contract import SCHEMA_ID

            data["schema_id"] = SCHEMA_ID
        except Exception:
            pass
        return envelope(request, data)
    except HTTPException:
        raise
    except Exception as exc:
        from nexus_scalp.observability.logging import get_logger
        from nexus_scalp.web.errors import log_web_error

        log_web_error(
            get_logger("nexus_scalp.api.v1"),
            "/api/v1/features/contract",
            None,
            exc,
            resource="schema_contract",
        )
        raise HTTPException(status_code=503, detail={"code": "DEPENDENCY_UNAVAILABLE"}) from exc


@router.get("/features/groups", summary="Feature groups / dimensions")
def features_groups(request: Request) -> dict[str, Any]:
    from nexus_scalp.features.schema_contract import family_of

    try:
        from nexus_scalp.features.schema_contract import canonical_feature_names

        names = canonical_feature_names()
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"code": "DEPENDENCY_UNAVAILABLE"}) from exc
    groups: dict[str, int] = {}
    for i in range(len(names)):
        fam = family_of(i)
        groups[fam] = groups.get(fam, 0) + 1
    return envelope(request, {"groups": groups, "total": len(names)})


@router.get("/features/current", summary="Last computed feature vector (engine state)")
def features_current(request: Request) -> dict[str, Any]:
    engine = _require_engine(request)
    fv = getattr(engine, "_last_fv", None)
    if fv is None:
        return envelope(request, {"available": False, "reason": "no feature vector computed yet"})
    try:
        values = list(fv)
    except TypeError:
        return envelope(request, {"available": False, "reason": "feature vector not iterable"})
    names: list[str] = []
    try:
        from nexus_scalp.features.schema_contract import canonical_feature_names

        names = list(canonical_feature_names())
    except Exception:
        names = []
    data = {
        "available": True,
        "dimension": len(values),
        "named": bool(names and len(names) == len(values)),
        "features": {n: v for n, v in zip(names, values, strict=False)}
        if names and len(names) == len(values)
        else None,
        "values_preview": [v for v in values[:10]],
        "probed_at": utc_now_iso(),
    }
    return envelope(request, data)
