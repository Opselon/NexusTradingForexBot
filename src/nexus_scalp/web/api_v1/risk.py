"""API v1 — RISK domain (status/summary).

Sources verified in docs/api/API_PLATFORM_V1.md §7 (risk.py, capabilities 35-36).
Read-only over the last proposal's real risk_checks + the engine risk config
(sanitized). No risk policy values are invented.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from nexus_scalp.web.api_v1.common import (
    engine_or_503,
    ok,
    sanitize_config,
    utc_now_iso,
)

router = APIRouter(prefix="/api/v1/risk", tags=["risk"])


@router.get("/status", summary="Current risk state (last proposal risk_checks + config)")
def risk_status(request: Request) -> Any:
    engine, resp = engine_or_503(request)
    if resp is not None:
        return resp
    proposal = getattr(engine, "_last_proposal", None)
    risk_checks: dict[str, Any] | None = None
    if proposal is not None:
        checks = getattr(proposal, "risk_checks", None)
        if isinstance(checks, dict):
            risk_checks = sanitize_config(checks)
    config_block: dict[str, Any] | None = None
    # RISK-LIVE-003: engine.config is the BOOTSTRAP config (live.yaml). The
    # effective limits live in the runtime config store — the same authority
    # GET /api/config exposes — and the operator's persisted values (settings
    # DB, source=WEB_UI) are already rehydrated into it. Reading engine.config
    # here showed a stale drawdown limit that no longer matched /config, so a
    # gauge compared the live drawdown against an obsolete budget. Prefer the
    # runtime snapshot; keep the bootstrap as a diagnostic fallback.
    risk_cfg = None
    config_source = "bootstrap"
    store = getattr(engine, "runtime_config", None)
    if store is not None:
        try:
            risk_cfg = store.get_snapshot().to_app_config().risk
            config_source = "runtime"
        except Exception:
            risk_cfg = None
    if risk_cfg is None:
        try:
            risk_cfg = engine.config.risk
        except Exception:
            risk_cfg = None
    if risk_cfg is not None:
        try:
            config_block = sanitize_config(
                {
                    "max_account_drawdown_pct": getattr(risk_cfg, "max_account_drawdown_pct", None),
                    "risk_per_trade_pct": getattr(risk_cfg, "risk_per_trade_pct", None),
                    "max_concurrent_positions": getattr(risk_cfg, "max_concurrent_positions", None),
                    "max_spread_points": getattr(risk_cfg, "max_spread_points", None),
                    "max_margin_usage_pct": getattr(risk_cfg, "max_margin_usage_pct", None),
                    "max_allowed_lots": getattr(risk_cfg, "max_allowed_lots", None),
                    "enforce_stop_loss": getattr(risk_cfg, "enforce_stop_loss", None),
                }
            )
        except Exception:
            config_block = None
        if config_block is not None:
            config_block["config_source"] = config_source
            try:
                config_block["configuration_version"] = (
                    store.get_version() if store is not None else None
                )
            except Exception:
                config_block["configuration_version"] = None
    return ok(
        request,
        {
            "last_proposal_present": proposal is not None,
            "risk_checks": risk_checks,
            "risk_config": config_block,
            "probed_at": utc_now_iso(),
        },
    )


@router.get("/summary", summary="Operational risk summary (open exposure + margin)")
def risk_summary(request: Request) -> Any:
    engine, resp = engine_or_503(request)
    if resp is not None:
        return resp
    adapter = getattr(engine, "adapter", None)
    exposure: dict[str, Any] = {"available": False}
    if adapter is not None:
        try:
            snaps = adapter.get_all_positions(symbol=None)
            by_symbol: dict[str, dict[str, float]] = {}
            total_volume = 0.0
            total_profit = 0.0
            for s in snaps:
                sym = getattr(s, "symbol", None) or "UNKNOWN"
                vol = float(getattr(s, "volume", 0.0) or 0.0)
                profit = float(getattr(s, "profit", 0.0) or 0.0)
                slot = by_symbol.setdefault(sym, {"volume": 0.0, "profit": 0.0, "positions": 0})
                slot["volume"] += vol
                slot["profit"] += profit
                slot["positions"] += 1
                total_volume += vol
                total_profit += profit
            account: dict[str, Any] = {}
            try:
                snap = adapter.get_account_snapshot()
                account = {
                    "equity": getattr(snap, "equity", None),
                    "balance": getattr(snap, "balance", None),
                    "margin": getattr(snap, "margin", None),
                    "margin_free": getattr(snap, "margin_free", None),
                    "margin_level": getattr(snap, "margin_level", None),
                }
            except Exception:
                account = {}
            exposure = {
                "available": True,
                "open_positions": len(snaps),
                "total_volume": total_volume,
                "total_floating_profit": total_profit,
                "by_symbol": by_symbol,
                "account": account,
            }
        except Exception:
            exposure = {"available": False, "reason": "adapter read failed"}
    return ok(request, {"exposure": exposure, "probed_at": utc_now_iso()})
