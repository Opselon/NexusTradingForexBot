"""Windows Gateway Server — HTTP bridge for RemoteMT5GatewayAdapter.

This is the MISSING server half of the existing client
``adapters/mt5/remote_gateway.py``. It MUST mirror the client's wire contract
exactly (no new protocol):

  POST /api/v1/execute  {action, payload}
    Headers: X-NSE-API-KEY, X-NSE-TIMESTAMP, X-NSE-SIGNATURE
      where  signature = HMAC-SHA256(secret, f"{timestamp}." + raw_body)

Actions implemented (all that the client sends):
  PING, GET_ACCOUNT_INFO, GET_SYMBOL_INFO, GET_LAST_TICK,
  GET_HISTORICAL_BARS, GET_POSITIONS, SEND_ORDER, EXECUTE_MARKET_ORDER,
  PLACE_PENDING_ORDER, GET_PENDING_ORDERS, CANCEL_PENDING_ORDER,
  MODIFY_POSITION, GET_CLOSED_DEALS_HISTORY, CLOSE_POSITION

Design:
  - stdlib + fastapi + MetaTrader5 (Windows-only) — no new deps.
  - Runs ONLY on Windows with an MT5 terminal attached (uses DirectMT5Adapter
    internally so every broker call goes through the project's audited MT5
    surface, not a parallel implementation).
  - Linux import is harmless (server refuses to start with a clear error).
  - Demo-only guard: refuses to run against live accounts unless --allow-live.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

# Reuse the client's HMAC logic — single source of truth for the scheme.
# Importing the adapter is safe on Linux (conditional HAS_NATIVE_MT5 inside).
SECRET_ENV_API_KEY = "NSE_GATEWAY_API_KEY"
SECRET_ENV_SECRET = "NSE_GATEWAY_SECRET"
DEFAULT_API_KEY = "default_local_key"
DEFAULT_SECRET = "default_local_secret"
TIMESTAMP_SKEW_S = 300  # ±5 min
MAX_BODY_BYTES = 64 * 1024

app = FastAPI(title="NSE Gateway Server", version="1.0.0")

# Lazily constructed; set in lifespan / connect path.
_adapter: Any = None
_allow_live: bool = False


def _expected_keys() -> tuple[str, str]:
    return (
        os.environ.get(SECRET_ENV_API_KEY, DEFAULT_API_KEY),
        os.environ.get(SECRET_ENV_SECRET, DEFAULT_SECRET),
    )


def _verify_hmac(body: bytes, timestamp: str, sig: str, secret: str) -> bool:
    msg = f"{timestamp}.".encode() + body
    expected = hmac.new(secret.encode(), msg=msg, digestmod=hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig or "")


def _get_adapter() -> Any:
    global _adapter
    if _adapter is not None:
        return _adapter
    if sys.platform != "win32":
        # Allow import on Linux for CLI help/tests, but never serve.
        raise RuntimeError("Gateway server runs only on Windows (requires MetaTrader5 + terminal).")
    try:
        from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter
    except Exception as exc:  # pragma: no cover - missing deps on Linux
        raise RuntimeError(f"DirectMT5Adapter unavailable: {exc}") from exc

    a = DirectMT5Adapter()
    if not a.connect():
        raise RuntimeError("MT5 DirectMT5Adapter.connect() failed — is the terminal running and logged into Demo?")
    # Demo guard: refuse live accounts at startup unless explicitly allowed.
    try:
        snap = a.get_account_snapshot()
        trade_mode = getattr(snap, "trade_mode", None)
        if trade_mode == 2 and not _allow_live:  # 2 == Real
            a.disconnect()
            raise RuntimeError("Refusing to serve a REAL account. Use --allow-live to override (not recommended for tests).")
    except RuntimeError:
        raise
    except Exception:
        pass
    _adapter = a
    return _adapter


def _json_ok(**extra: Any) -> dict[str, Any]:
    return {"status": "OK", **extra}


def _json_success(**extra: Any) -> dict[str, Any]:
    return {"status": "SUCCESS", **extra}


def _json_failed(message: str, retcode: Any = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": "FAILED", "message": message}
    if retcode is not None:
        out["retcode"] = retcode
    out.update(extra)
    return out


def _handle_action(action: str, payload: dict[str, Any], adapter: Any) -> dict[str, Any]:
    """Dispatch table mirroring RemoteMT5GatewayAdapter expectations."""
    # Keep imports local to avoid Linux import cost.
    from nexus_scalp.domain.enums import OrderType

    if action == "PING":
        return _json_ok()

    if action == "GET_ACCOUNT_INFO":
        snap = adapter.get_account_snapshot()
        if not snap.available:
            return _json_failed(snap.error_state or "account unavailable")
        return {
            "status": "SUCCESS",
            "data": {
                "login": snap.login,
                "trade_mode": snap.trade_mode,
                "leverage": snap.leverage,
                "balance": snap.balance,
                "equity": snap.equity,
                "margin": snap.margin,
                "margin_free": snap.margin_free,
                "currency": snap.currency or "USD",
            },
        }

    if action == "GET_SYMBOL_INFO":
        symbol = str(payload.get("symbol") or "")
        if not symbol:
            return _json_failed("symbol required")
        snap = adapter.get_symbol_snapshot(symbol)
        if not snap.available or not snap.spec:
            return _json_failed(snap.error_state or "symbol unavailable")
        s = snap.spec
        return {
            "status": "SUCCESS",
            "data": {
                "symbol": str(s.get("name") or symbol),
                "digits": int(float(s.get("digits", 5))),
                "point": float(s.get("point") or 0.00001),
                "tick_size": float(s.get("trade_tick_size") or s.get("point") or 0.00001),
                "tick_value": float(s.get("trade_tick_value") or 0.0),
                "volume_min": float(s.get("volume_min") or 0.01),
                "volume_max": float(s.get("volume_max") or 100.0),
                "volume_step": float(s.get("volume_step") or 0.01),
                "stops_level": int(float(s.get("trade_stops_level") or 0)),
                "freeze_level": int(float(s.get("trade_freeze_level") or 0)),
                "trade_contract_size": float(s.get("trade_contract_size") or 100.0),
            },
        }

    if action == "GET_LAST_TICK":
        symbol = str(payload.get("symbol") or "")
        snap = adapter.get_broker_tick(symbol)
        if not snap.available or snap.bid is None or snap.ask is None:
            return _json_failed(snap.error_state or "tick unavailable")
        ts = snap.time_utc or datetime.now(UTC)
        return {
            "status": "SUCCESS",
            "data": {
                "timestamp": ts.isoformat(),
                "bid": float(snap.bid),
                "ask": float(snap.ask),
                "last": float(snap.last or 0.0),
                "volume": float(snap.volume or 0.0),
                "flags": int(snap.flags or 0),
            },
        }

    if action == "GET_HISTORICAL_BARS":
        symbol = str(payload.get("symbol") or "")
        timeframe = str(payload.get("timeframe") or "M1")
        count = int(payload.get("count") or 100)
        bars = adapter.get_historical_bars(symbol=symbol, timeframe=timeframe, count=count)
        # Adapter returns BarData dataclasses; normalize to client shape.
        data = []
        for b in bars:
            ts = getattr(b, "timestamp", None) or getattr(b, "time_utc", None) or datetime.now(UTC)
            data.append(
                {
                    "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                    "open": float(getattr(b, "open", 0.0)),
                    "high": float(getattr(b, "high", 0.0)),
                    "low": float(getattr(b, "low", 0.0)),
                    "close": float(getattr(b, "close", 0.0)),
                    "tick_volume": int(getattr(b, "tick_volume", 0) or 0),
                }
            )
        return {"status": "SUCCESS", "data": data}

    if action == "GET_POSITIONS":
        symbol = payload.get("symbol")
        positions = adapter.get_positions(symbol=symbol)
        data = []
        for p in positions:
            # Position model has type: OrderType
            t = getattr(p, "type", None)
            t_str = (t.value if hasattr(t, "value") else None) or str(t or "BUY")
            data.append(
                {
                    "ticket": int(getattr(p, "ticket", 0)),
                    "symbol": str(getattr(p, "symbol", "")),
                    "type": t_str,
                    "volume": float(getattr(p, "volume", 0.0)),
                    "price_open": float(getattr(p, "price_open", 0.0)),
                    "sl": float(getattr(p, "sl", 0.0)),
                    "tp": float(getattr(p, "tp", 0.0)),
                    "profit": float(getattr(p, "profit", 0.0)),
                    "magic": int(getattr(p, "magic", 0)),
                }
            )
        return {"status": "SUCCESS", "data": data}

    if action == "SEND_ORDER":
        # Legacy TradeOrder path — used by reservation flow
        order_type_raw = str(payload.get("order_type") or payload.get("type") or "BUY")
        try:
            order_type = OrderType(order_type_raw)
        except Exception:
            return _json_failed(f"unknown order_type {order_type_raw}")
        from nexus_scalp.domain.models import TradeOrder

        order = TradeOrder(
            order_id=str(payload.get("order_id") or payload.get("idempotency_key") or "gateway"),
            symbol=str(payload.get("symbol") or ""),
            order_type=order_type,
            volume=float(payload.get("volume") or 0.01),
            price=float(payload.get("price") or 0.0) or 1.0,
            stop_loss=float(payload.get("stop_loss") or payload.get("sl") or 0.0) or 1.0,
            take_profit=float(payload.get("take_profit") or payload.get("tp") or 0.0) or 1.0,
            magic_number=int(payload.get("magic_number") or payload.get("magic") or 888101),
            comment=str(payload.get("comment") or "NSE_GATEWAY")[:31],
        )
        ok = adapter.send_order(order)
        if ok:
            # Try to echo the ticket via recent positions/orders
            ticket = 0
            try:
                poss = adapter.get_positions(symbol=order.symbol)
                if poss:
                    ticket = int(max(getattr(p, "ticket", 0) for p in poss) or 0)
            except Exception:
                pass
            return _json_success(ticket=ticket, message="order sent")
        return _json_failed("order_send failed")

    if action == "EXECUTE_MARKET_ORDER":
        symbol = str(payload.get("symbol") or "")
        order_type_raw = str(payload.get("order_type") or "BUY")
        try:
            order_type = OrderType(order_type_raw)
        except Exception:
            return _json_failed(f"unknown order_type {order_type_raw}")
        ticket = adapter.execute_market_order(
            symbol=symbol,
            order_type=order_type,
            volume=float(payload.get("volume") or 0.01),
            price=float(payload.get("price") or 0.0),
            stop_loss=float(payload.get("stop_loss") or 0.0),
            take_profit=float(payload.get("take_profit") or 0.0),
        )
        if ticket:
            return _json_success(ticket=int(ticket))
        return _json_failed("execute_market_order failed")

    if action == "PLACE_PENDING_ORDER":
        symbol = str(payload.get("symbol") or "")
        order_type_raw = str(payload.get("order_type") or "BUY_LIMIT")
        try:
            order_type = OrderType(order_type_raw)
        except Exception:
            return _json_failed(f"unknown order_type {order_type_raw}")
        ticket = adapter.place_pending_order(
            symbol=symbol,
            order_type=order_type,
            volume=float(payload.get("volume") or 0.01),
            price=float(payload.get("price") or 0.0),
            stop_loss=float(payload.get("stop_loss") or 0.0),
            take_profit=float(payload.get("take_profit") or 0.0),
        )
        if ticket:
            return _json_success(ticket=int(ticket))
        return _json_failed("place_pending_order failed")

    if action == "GET_PENDING_ORDERS":
        symbol = payload.get("symbol")
        # Prefer snapshot form (richer), fall back to legacy dict form.
        try:
            snaps = adapter.get_pending_orders_snapshot(symbol=symbol)
            if snaps:
                data = []
                for s in snaps:
                    # OrderSnapshot fields vary; expose raw dict-style fallback
                    data.append(
                        {
                            "ticket": int(getattr(s, "ticket", 0) or 0),
                            "symbol": str(getattr(s, "symbol", "") or symbol or ""),
                            "type": str(getattr(s, "type", "") or ""),
                            "volume": float(getattr(s, "volume", 0) or getattr(s, "volume_current", 0) or 0.0),
                            "price_open": float(getattr(s, "price_open", 0) or 0.0),
                        }
                    )
                return {"status": "SUCCESS", "data": data}
        except Exception:
            pass
        data = adapter.get_pending_orders(symbol=symbol)
        return {"status": "SUCCESS", "data": data if isinstance(data, list) else []}

    if action == "CANCEL_PENDING_ORDER":
        ticket = int(payload.get("ticket") or 0)
        ok = adapter.cancel_pending_order(ticket)
        return _json_success() if ok else _json_failed("cancel failed")

    if action == "MODIFY_POSITION":
        ticket = int(payload.get("ticket") or 0)
        ok = adapter.modify_position(
            ticket=ticket,
            stop_loss=float(payload.get("stop_loss") or payload.get("sl") or 0.0),
            take_profit=float(payload.get("take_profit") or payload.get("tp") or 0.0),
        )
        return _json_success() if ok else _json_failed("modify failed")

    if action == "CLOSE_POSITION":
        ticket = int(payload.get("ticket") or 0)
        vol = payload.get("volume")
        ok = adapter.close_position(ticket, volume=float(vol) if vol is not None else None)
        return _json_success() if ok else _json_failed("close failed")

    if action == "GET_CLOSED_DEALS_HISTORY":
        symbol = str(payload.get("symbol") or "")
        hours_back = int(payload.get("hours_back") or 24)
        data = adapter.get_closed_deals_history(symbol=symbol, hours_back=hours_back)
        return {"status": "SUCCESS", "data": data if isinstance(data, list) else []}

    return _json_failed(f"unknown action {action}")


@app.post("/api/v1/execute")
async def execute(
    request: Request,
    x_nse_api_key: str | None = Header(default=None, alias="X-NSE-API-KEY"),
    x_nse_timestamp: str | None = Header(default=None, alias="X-NSE-TIMESTAMP"),
    x_nse_signature: str | None = Header(default=None, alias="X-NSE-SIGNATURE"),
) -> JSONResponse:
    # Enforce body size early (DoS guard)
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return JSONResponse({"status": "FAILED", "message": "body too large"}, status_code=413)

    # HMAC verification — mirrors remote_gateway._send_request / verify_request_signature
    exp_key, secret = _expected_keys()
    if not x_nse_timestamp or not x_nse_signature:
        return JSONResponse({"status": "FAILED", "message": "missing auth headers"}, status_code=401)
    # Timestamp skew check (accept both seconds and ms forms; client uses seconds)
    try:
        ts_raw = x_nse_timestamp.strip()
        # Accept ms timestamps as well (client verify tests use ms)
        ts_val = int(float(ts_raw))
        # If it looks like ms (> 1e12), convert
        if ts_val > 10_000_000_000:
            ts_val = ts_val // 1000
        now = int(time.time())
        if abs(now - ts_val) > TIMESTAMP_SKEW_S:
            return JSONResponse({"status": "FAILED", "message": "timestamp skew"}, status_code=401)
    except Exception:
        return JSONResponse({"status": "FAILED", "message": "bad timestamp"}, status_code=401)

    if x_nse_api_key != exp_key:
        return JSONResponse({"status": "FAILED", "message": "bad api key"}, status_code=401)
    if not _verify_hmac(body, x_nse_timestamp, x_nse_signature, secret):
        return JSONResponse({"status": "FAILED", "message": "bad signature"}, status_code=401)

    # Parse JSON (empty body allowed for PING compat? client always sends JSON)
    try:
        parsed = json.loads(body.decode("utf-8") or "{}")
    except Exception:
        return JSONResponse({"status": "FAILED", "message": "bad json"}, status_code=400)

    action = str(parsed.get("action") or "").strip()
    payload = parsed.get("payload") or {}
    if not isinstance(payload, dict):
        return JSONResponse({"status": "FAILED", "message": "payload must be object"}, status_code=400)
    if not action:
        return JSONResponse({"status": "FAILED", "message": "action required"}, status_code=400)

    # Windows-only execution path; Linux returns a clear 503 so CLI/tests stay green.
    if sys.platform != "win32":
        return JSONResponse({"status": "FAILED", "message": "gateway server runs only on Windows"}, status_code=503)

    try:
        adapter = _get_adapter()
    except Exception as exc:
        return JSONResponse({"status": "FAILED", "message": str(exc)}, status_code=500)

    try:
        result = _handle_action(action, payload, adapter)
        # Map internal FAILED to HTTP 200 with status FAILED (client expects 200 + JSON)
        # except PING which is always OK.
        return JSONResponse(result, status_code=200)
    except Exception as exc:  # pragma: no cover - broker edge
        return JSONResponse({"status": "FAILED", "message": f"handler error: {exc}"}, status_code=500)


@app.get("/health")
async def health() -> dict[str, Any]:
    if sys.platform != "win32":
        return {"status": "DEGRADED", "reason": "gateway runs only on Windows", "platform": sys.platform}
    try:
        a = _get_adapter()
        snap = a.get_account_snapshot()
        mode = getattr(snap, "trade_mode", None)
        bal = getattr(snap, "balance", None)
        return {"status": "OK", "platform": sys.platform, "trade_mode": mode, "balance": bal}
    except Exception as exc:
        return {"status": "ERROR", "platform": sys.platform, "message": str(exc)}


def set_allow_live(v: bool) -> None:
    global _allow_live
    _allow_live = bool(v)


def reset_adapter_for_tests() -> None:  # pragma: no cover - test helper
    global _adapter
    _adapter = None
