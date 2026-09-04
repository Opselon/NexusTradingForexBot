"""API v1 — POSITIONS + EXECUTION + MODEL + FEATURES domains.

Sources verified in docs/api/API_PLATFORM_V1.md §7 (positions.py, execution.py,
model.py, features.py — capabilities 30-45). Positions/execution are truthful
adapter snapshots (503 ENGINE_UNAVAILABLE when absent); model/features read the
engine bundle state and the canonical schema contract.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from nexus_scalp.web.api_v1.common import (
    adapter_or_503,
    build_page,
    engine_or_503,
    fail,
    fetch_rows_bounded,
    get_audit_repo,
    get_engine,
    iso_or_none,
    ok,
    parse_pagination,
    utc_now_iso,
)

router = APIRouter(
    prefix="/api/v1",
    tags=["positions", "execution", "model", "features"],
)


def _dataclass_dict(obj: Any) -> Any:
    """Converts dataclass/objects to dicts via public attributes (never __dict__ of slots)."""
    from dataclasses import asdict, is_dataclass

    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj


# ---------------------------------------------------------------------------
# POSITIONS (30-33)
# ---------------------------------------------------------------------------


@router.get("/positions", summary="Current open positions (real broker adapter snapshot)")
def positions_list(request: Request, symbol: str | None = Query(None, max_length=32)) -> Any:
    adapter, resp = adapter_or_503(request)
    if resp is not None:
        return resp
    try:
        snaps = adapter.get_all_positions(symbol=symbol)
    except Exception:
        return fail(request, "DEPENDENCY_UNAVAILABLE", message="broker adapter read failed")
    out: list[dict[str, Any]] = []
    for s in snaps:
        d = _dataclass_dict(s)
        if isinstance(d, dict):
            d["time"] = iso_or_none(d.get("time"))
        out.append(d)
    return ok(request, {"positions": out, "count": len(out)})


@router.get("/positions/{ticket}", summary="One open position by broker ticket")
def position_detail(request: Request, ticket: int) -> Any:
    adapter, resp = adapter_or_503(request)
    if resp is not None:
        return resp
    try:
        snaps = adapter.get_all_positions(symbol=None)
    except Exception:
        return fail(request, "DEPENDENCY_UNAVAILABLE", message="broker adapter read failed")
    for s in snaps:
        if getattr(s, "ticket", None) == ticket:
            return ok(request, _dataclass_dict(s))
    return fail(request, "RESOURCE_NOT_FOUND", message=f"position {ticket} not open")


@router.get("/positions/history/{ticket}", summary="Ledger context for a historical position")
def position_history_detail(request: Request, ticket: int) -> Any:
    repo = get_audit_repo(request)
    row = _ledger_row(repo, ticket)
    if row is None:
        return fail(request, "RESOURCE_NOT_FOUND", message=f"ledger row {ticket} not found")
    deals = _deals_for(repo, ticket)
    return ok(request, {"ledger": _iso_row(row), "deals": [_iso_row(d) for d in deals]})


# ---------------------------------------------------------------------------
# EXECUTION (37-38)
# ---------------------------------------------------------------------------


@router.get("/execution/status", summary="Execution subsystem status (adapter connection)")
def execution_status(request: Request) -> Any:
    engine, resp = engine_or_503(request)
    if resp is not None:
        return resp
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
    return ok(
        request,
        {
            "adapter_present": adapter is not None,
            "connection_state": state,
            "connected": connected,
            "engine_running": bool(getattr(engine, "_running", False)),
            "mode": _safe_mode(engine),
            "probed_at": utc_now_iso(),
        },
    )


@router.get("/execution/history", summary="Paginated order-event history (audit_orders)")
def execution_history(
    request: Request,
    status: str | None = Query(None, max_length=24),
    symbol: str | None = Query(None, max_length=32),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
) -> Any:
    checked = parse_pagination(page, page_size)
    if not isinstance(checked, tuple):
        return checked
    p, ps = checked
    where: list[str] = []
    args: list[Any] = []
    if status:
        where.append("status = ?")
        args.append(status)
    if symbol:
        where.append("symbol = ?")
        args.append(symbol)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    repo = get_audit_repo(request)
    sql = (
        "SELECT id, order_id, symbol, order_type, volume, price, status, executed_at "
        "FROM audit_executions" + where_sql + " ORDER BY id DESC"
    )
    rows = fetch_rows_bounded(repo, sql, tuple(args), ps + 1 + (p - 1) * ps)
    page_rows = rows[(p - 1) * ps : (p - 1) * ps + ps]
    has_more = len(rows) > (p - 1) * ps + ps
    items = [_iso_row(r) for r in page_rows]
    return ok(request, build_page(items, p, ps, has_more=has_more))


# ---------------------------------------------------------------------------
# MODEL (39-42)
# ---------------------------------------------------------------------------


@router.get("/model/status", summary="Serving model state (bundle presence + warmup)")
def model_status(request: Request) -> Any:
    engine, resp = engine_or_503(request)
    if resp is not None:
        return resp
    bundle = getattr(engine, "_bundle", None)
    return ok(
        request,
        {
            "bundle_loaded": bundle is not None,
            "inference_enabled": bool(getattr(engine, "_inference_enabled", False)),
            "warmup_state": getattr(engine, "warmup_state", None),
            "runtime_mode": getattr(engine, "_runtime_mode", None),
            "probed_at": utc_now_iso(),
        },
    )


@router.get("/model/identity", summary="Artifact identity / manifest / schema fingerprint")
def model_identity(request: Request) -> Any:
    engine, resp = engine_or_503(request)
    if resp is not None:
        return resp
    bundle = getattr(engine, "_bundle", None)
    if bundle is None:
        return ok(request, {"available": False, "reason": "no model bundle loaded"})
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
    return ok(request, identity)


@router.get("/model/contracts", summary="Model/feature contract compatibility inventory")
def model_contracts(request: Request) -> Any:
    engine = get_engine(request)
    contracts: dict[str, Any] = {
        "feature_schema_ids": None,
        "compatible_model_schemas": None,
        "generated_at": utc_now_iso(),
    }
    try:
        from nexus_scalp.features.schema_contract import expected_schema_ids

        contracts["feature_schema_ids"] = list(expected_schema_ids())
    except Exception:
        contracts["feature_schema_ids_error"] = "DEPENDENCY_UNAVAILABLE"
    try:
        from nexus_scalp.features.inference_validator import compatible_model_schema
        from nexus_scalp.features.schema_contract import DIMENSION, SCHEMA_ID

        _bundle = getattr(engine, "_bundle", None) if engine is not None else None
        _manifest = getattr(_bundle, "manifest", None) if _bundle is not None else None
        _model_schema_id = getattr(_manifest, "schema_id", None) if _manifest is not None else None
        _model_dimension = getattr(_manifest, "dimension", None) if _manifest is not None else None
        if _model_dimension is None and _manifest is not None:
            _model_dimension = getattr(_manifest, "feature_count", None)
        try:
            _model_dimension = int(_model_dimension) if _model_dimension is not None else None
        except Exception:
            _model_dimension = None
        contracts["compatible_model_schemas"] = compatible_model_schema(
            _model_schema_id, _model_dimension, SCHEMA_ID, DIMENSION
        )
    except TypeError:
        contracts["compatible_model_schemas"] = None
    except Exception:
        contracts["compatible_model_schemas"] = None
    if engine is not None and getattr(engine, "_bundle", None) is not None:
        contracts["serving_bundle_present"] = True
    return ok(request, contracts)


@router.get("/model/champion", summary="Current champion (read-only governance summary)")
def model_champion(request: Request) -> Any:
    """Champion identity from the engine bundle (authoritative serving truth)."""
    engine = get_engine(request)
    if engine is None:
        return ok(request, {"available": False, "reason": "engine not attached"})
    bundle = getattr(engine, "_bundle", None)
    if bundle is None:
        return ok(request, {"available": True, "champion": {"available": False}})
    manifest = getattr(bundle, "manifest", None)
    champ: dict[str, Any] = {"available": True}
    for attr in ("artifact_id", "model_id", "schema_id", "version"):
        value = getattr(manifest, attr, None)
        if value is not None:
            champ[attr] = str(value)
    return ok(request, {"champion": champ})


# ---------------------------------------------------------------------------
# FEATURES (43-45)
# ---------------------------------------------------------------------------


@router.get("/features/status", summary="Feature pipeline health (warmup + last vector)")
def features_status(request: Request) -> Any:
    engine, resp = engine_or_503(request)
    if resp is not None:
        return resp
    fv = getattr(engine, "_last_fv", None)
    missing: list[str] = []
    proposal = getattr(engine, "_last_proposal", None)
    if proposal is not None:
        try:
            checks = getattr(proposal, "risk_checks", None)
            if isinstance(checks, dict):
                missing = list(checks.get("missing_features") or [])
        except Exception:
            missing = []
    return ok(
        request,
        {
            "warmup_state": getattr(engine, "warmup_state", None),
            "inference_enabled": bool(getattr(engine, "_inference_enabled", False)),
            "last_vector_available": fv is not None,
            "missing_features": missing,
            "probed_at": utc_now_iso(),
        },
    )


@router.get("/features/contract", summary="Active feature contract (canonical SSoT registry)")
def features_contract(request: Request) -> Any:
    try:
        from nexus_scalp.features.schema_contract import (
            SCHEMA_ID,
            canonical_feature_names,
            family_of,
            feature_schema_hash,
            registry_has_canonical_70d,
        )

        names = canonical_feature_names()
        groups: dict[str, list[int]] = {"base_0_49": [], "news_50_59": [], "liquidity_60_69": []}
        for i, _n in enumerate(names):
            fam = family_of(i)
            if fam in groups:
                groups[fam].append(i)
        return ok(
            request,
            {
                "schema_id": SCHEMA_ID,
                "feature_count": len(names),
                "feature_schema_hash": feature_schema_hash(),
                "registry_canonical": registry_has_canonical_70d(),
                "groups": {k: {"count": len(v), "indices": v[:70]} for k, v in groups.items()},
                "first_10_names": list(names[:10]),
            },
        )
    except Exception:
        return fail(request, "DEPENDENCY_UNAVAILABLE", message="schema contract unavailable")


@router.get("/features/groups", summary="Feature groups / dimensions")
def features_groups(request: Request) -> Any:
    try:
        from nexus_scalp.features.schema_contract import (
            canonical_feature_names,
            family_of,
        )

        names = canonical_feature_names()
        by_family: dict[str, list[str]] = {}
        for i, n in enumerate(names):
            by_family.setdefault(family_of(i), []).append(n)
        return ok(
            request,
            {
                "dimension": len(names),
                "families": {
                    k: {"count": len(v), "names": v[:70]} for k, v in sorted(by_family.items())
                },
            },
        )
    except Exception:
        return fail(request, "DEPENDENCY_UNAVAILABLE", message="schema contract unavailable")


@router.get("/features/current", summary="Last computed feature vector (engine state)")
def features_current(request: Request) -> Any:
    engine, resp = engine_or_503(request)
    if resp is not None:
        return resp
    fv = getattr(engine, "_last_fv", None)
    if fv is None:
        return ok(request, {"available": False, "reason": "no feature vector computed yet"})
    try:
        values = list(fv)
    except TypeError:
        return ok(request, {"available": False, "reason": "feature vector not iterable"})
    names: list[str] = []
    try:
        from nexus_scalp.features.schema_contract import canonical_feature_names

        names = list(canonical_feature_names())
    except Exception:
        names = []
    named = bool(names and len(names) == len(values))
    return ok(
        request,
        {
            "available": True,
            "dimension": len(values),
            "named": named,
            "features": dict(zip(names, values, strict=True)) if named else None,
            "values_preview": [float(v) for v in values[:10]],
            "probed_at": utc_now_iso(),
        },
    )


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _safe_mode(engine: Any) -> str | None:
    try:
        return engine.config.execution.mode.value  # type: ignore[union-attr]
    except Exception:
        return None


def _iso_row(row: dict[str, Any]) -> dict[str, Any]:
    for key in (
        "executed_at",
        "timestamp",
        "event_timestamp",
        "generated_at",
        "opened_at",
        "closed_at",
    ):
        if key in row:
            row[key] = iso_or_none(row.get(key))
    return sanitize_row(row)


def sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
    from nexus_scalp.web.api_v1.common import sanitize_record_secrets

    return sanitize_record_secrets(row)


def _ledger_row(repo: Any, ticket: int) -> dict[str, Any] | None:
    rows = fetch_rows_bounded(repo, "SELECT * FROM audit_ledger WHERE ticket = ?", (ticket,), 1)
    return rows[0] if rows else None


def _deals_for(repo: Any, ticket: int) -> list[dict[str, Any]]:
    try:
        return list(repo.get_broker_deals_for_position(ticket))[:200]
    except Exception:
        return []
