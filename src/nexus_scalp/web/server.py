"""
FastAPI Production Control Dashboard Backend
============================================
Handles high-performance async REST APIs and Server-Sent Events (SSE) live telemetry streams
connecting the modern front-end console to real-time broker states, AI parameters,
and risk engines.
"""

import asyncio
import json
import math
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from nexus_scalp.accounting import PeriodKind
from nexus_scalp.accounting.worker import format_worker_status
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.domain.enums import ActionType, ExecutionMode
from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import FEATURE_NAMES
from nexus_scalp.observability.logging import get_logger


def serialize_enums(obj: Any) -> Any:
    """Recursively converts Enum instances to their underlying values."""
    if isinstance(obj, dict):
        return {k: serialize_enums(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize_enums(x) for x in obj]
    elif isinstance(obj, Enum):
        return obj.value
    return obj


logger = get_logger("nexus_scalp.web.server")

# Global/Static UI folder relative path
WEB_DIR = Path("Web")


class ServerState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.bars: list[dict[str, Any]] = []
        self.real_overlays: dict[str, Any] = {
            "rectangles": [],
            "bos_lines": [],
            "midlines": [],
            "liq_markers": [],
        }

    def update_live_visuals(
        self, bars: list[dict[str, Any]], real_overlays: dict[str, Any]
    ) -> None:
        with self._lock:
            self.bars = list(bars)
            self.real_overlays = dict(real_overlays)

    def get_live_visuals(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        with self._lock:
            return self.bars, self.real_overlays


# Define API request bodies
class ModifyPositionRequest(BaseModel):
    ticket: int
    stop_loss: float
    take_profit: float


class ClosePositionRequest(BaseModel):
    ticket: int


class ToggleRequest(BaseModel):
    active: bool


class AlgoConfigRequest(BaseModel):
    atr_sl_buffer_multiplier: float
    min_risk_reward_ratio: float
    ai_zone_confidence_threshold: float
    fvg_mitigation_sensitivity: float
    order_block_lookback_bars: int


class ToggleReplayRequest(BaseModel):
    active: bool
    speed: int = 1


class SimulationTickRequest(BaseModel):
    type: str  # 'BUY_PRESSURE', 'SELL_PRESSURE', 'VOLATILE_SWEEP'


class ToggleRuleRequest(BaseModel):
    rule_name: str
    is_enabled: bool
    parameters: dict[str, Any] | None = None


class ModelTestRequest(BaseModel):
    """
    Debug Hub model-test payload.

    `features` accepts the 50-dimensional vector directly. When omitted, the live
    feature vector is used, which makes the endpoint a one-click "what does the net
    think right now" probe.
    """

    features: list[float] | None = None
    use_live_features: bool = False


def _classify_feature(value: Any) -> tuple[float, str]:
    """
    Normalizes a raw feature value and classifies its health for the Debug Hub.

    Returns:
        (sanitized_value, status) where status is VALID, NAN, INF or NON_NUMERIC.
    """
    try:
        fval = float(value)
    except (TypeError, ValueError):
        return 0.0, "NON_NUMERIC"

    if math.isnan(fval):
        return 0.0, "NAN"
    if math.isinf(fval):
        return 0.0, "INF"
    return fval, "VALID"


async def _run_training_async(orchestrator: Any, dataset: Any, num_epochs: int) -> dict[str, Any]:
    """Runs controlled training off the event loop via asyncio.to_thread."""
    import asyncio

    return await asyncio.to_thread(
        orchestrator.run_controlled_training, dataset, num_epochs=num_epochs
    )


def create_app(engine_ref: Any = None) -> FastAPI:
    """Creates and configures the FastAPI web server instance."""
    app = FastAPI(title="Nexus Scalp Engine Control Center", version="0.1.0")

    # Store engine reference in app state
    app.state.engine = engine_ref
    app.state.server_state = ServerState()

    # Active simulation and replay parameters
    app.state.is_replaying = False
    app.state.replay_speed = 1
    app.state.simulated_history_ticks = []

    # Keep track of simulated signal outcomes
    app.state.simulated_outcomes = []

    # Helper function to get live data from engine or return mock details if offline/simulating
    def get_system_state() -> dict[str, Any]:
        engine = app.state.engine

        # Retrieve thread-safe live visuals state if available
        real_bars = []
        real_smc_overlays = {}
        if hasattr(app.state, "server_state") and app.state.server_state is not None:
            real_bars, real_smc_overlays = app.state.server_state.get_live_visuals()

        # Default fallback values
        symbol = "XAUUSD"
        bid = 2334.21
        ask = 2334.41
        spread = 20
        atr = 1.15
        regime = "NORMAL_VOLATILITY"
        engine_running = False
        execution_mode = "LIVE"

        account_data: dict[str, Any] = {
            # PHASE 08 HARDENING: no synthetic placeholders. When there is no
            # engine the account fields stay None so the UI renders an
            # explicit unavailable state instead of a fake $10,000 balance and
            # a fake 78.5% win rate (see agents/bugs.md BUG-020).
            "available": False,
            "balance": None,
            "equity": None,
            "floating": None,
            "drawdown": None,
            "win_rate": None,
        }

        positions_list: list[dict[str, Any]] = []
        bars_list: list[dict[str, Any]] = []
        probs_data = {"no_trade": 0.995, "buy": 0.002, "sell": 0.003}
        ai_decision = "NO_ACTION"
        ai_confidence = 0.0
        ai_reason = "Neural weights are stable. Waiting for structural volatility thresholds."

        features_values = [0.0] * 40

        # Read actual live engine state if connected
        if engine:
            symbol = engine.config.execution.symbol
            engine_running = engine._running
            execution_mode = engine.config.execution.mode.value

            # Fetch MT5 live ticks and prices
            try:
                # Use engine's last synchronized tick first to ensure complete consistency
                tick = engine._last_tick or engine.adapter.get_last_tick(symbol)
                if tick:
                    bid = tick.bid
                    ask = tick.ask
                    spread = round((tick.ask - tick.bid) * 100)
            except Exception:
                pass

            # Fetch regime state & ATR from the exact synchronized last state
            try:
                reg_state = engine._last_regime_state
                if reg_state:
                    regime = reg_state.regime_type.name
                    atr = reg_state.realized_volatility_5m  # Single source of truth for volatility
                elif (
                    hasattr(engine, "regime_classifier") and engine.regime_classifier._stable_regime
                ):
                    regime = engine.regime_classifier._stable_regime.name
            except Exception:
                pass

            # Fetch account info
            try:
                acc = engine.adapter.get_account_info()
                if acc:
                    account_data["available"] = True
                    account_data["balance"] = acc.balance
                    account_data["equity"] = acc.equity
                    account_data["floating"] = acc.equity - acc.balance
                    account_data["drawdown"] = (
                        ((engine._peak_equity - acc.equity) / max(engine._peak_equity, 1.0)) * 100.0
                        if engine._peak_equity > 0
                        else 0.0
                    )
            except Exception:
                pass

            # Real win rate from the canonical AccountingCore (authoritative
            # ledger), not from the legacy duplicate calculator. Unavailable
            # stays None.
            try:
                core = getattr(engine, "accounting_core", None)
                if core is not None:
                    trades = core.load_trades(limit=1000)
                    closed = [t for t in trades if t.closed_at is not None]
                    decided = sum(1 for t in closed if t.outcome.value in ("WIN", "LOSS"))
                    wins = sum(1 for t in closed if t.is_win)
                    if decided:
                        account_data["win_rate"] = round(wins / decided * 100.0, 2)
            except Exception:
                pass

            # Fetch positions
            try:
                live_positions = engine.adapter.get_positions(symbol=symbol)
                for p in live_positions:
                    positions_list.append(
                        {
                            "ticket": p.ticket,
                            "symbol": p.symbol,
                            "type": p.type.value,
                            "volume": p.volume,
                            "price_open": p.price_open,
                            "sl": p.sl,
                            "tp": p.tp,
                            "profit": p.profit,
                        }
                    )
            except Exception:
                pass

            # Fetch bars (synchronized completed history) - Expand to 250 completed bars for 150+ visible bars support
            if real_bars:
                bars_list = real_bars
            else:
                try:
                    completed_bars = engine.aggregator.get_completed_bars()
                    for b in completed_bars[-250:]:
                        bars_list.append(
                            {
                                "time": b.timestamp.isoformat(),
                                "open": b.open,
                                "high": b.high,
                                "low": b.low,
                                "close": b.close,
                                "volume": b.tick_volume,
                                "is_complete": True,
                            }
                        )
                    # Single Source of Truth forming candle injection
                    forming_bar = engine.aggregator.get_current_forming_bar()
                    if forming_bar:
                        bars_list.append(
                            {
                                "time": forming_bar.timestamp.isoformat(),
                                "open": forming_bar.open,
                                "high": forming_bar.high,
                                "low": forming_bar.low,
                                "close": forming_bar.close,
                                "volume": forming_bar.tick_volume,
                                "is_complete": False,
                            }
                        )
                except Exception as e:
                    logger.error("Failed to fetch synchronized bar stream", error=str(e))

            # Fetch synchronized features and model predictions
            try:
                fv = engine._last_fv
                if fv:
                    features_values = fv.to_tensor_input()

                # Sync actual live inference probabilities
                probs = engine._last_probs
                if probs is not None:
                    probs_list = probs.cpu().numpy().flatten().tolist()
                    probs_data = {
                        "no_trade": float(probs_list[0]),
                        "buy": float(probs_list[1]),
                        "sell": float(probs_list[2]),
                    }

                # Sync actual policy proposals
                proposal = engine._last_proposal
                if proposal:
                    ai_decision = proposal.action.value
                    ai_confidence = proposal.confidence
                    ai_reason = proposal.reason_code
            except Exception as e:
                logger.error("Failed to fetch engine sync predictions/features", error=str(e))

        # Create structured features objects
        features_payload = []
        for i, name in enumerate(FEATURE_NAMES):
            val = features_values[i] if i < len(features_values) else 0.0
            features_payload.append({"index": i, "name": name, "value": val})

        # Build Visual Overlays and Algo Config response
        rectangles = []
        order_lines = None
        algo_config_data = {
            "atr_sl_buffer_multiplier": 1.5,
            "min_risk_reward_ratio": 1.8,
            "ai_zone_confidence_threshold": 0.82,
            "fvg_mitigation_sensitivity": 0.5,
            "order_block_lookback_bars": 30,
        }

        if engine:
            try:
                algo_config_data = {
                    "atr_sl_buffer_multiplier": float(
                        getattr(engine.config.algo, "atr_sl_buffer_multiplier", 1.5)
                    ),
                    "min_risk_reward_ratio": float(
                        getattr(engine.config.algo, "min_risk_reward_ratio", 1.8)
                    ),
                    "ai_zone_confidence_threshold": float(
                        getattr(engine.config.algo, "ai_zone_confidence_threshold", 0.82)
                    ),
                    "fvg_mitigation_sensitivity": float(
                        getattr(engine.config.algo, "fvg_mitigation_sensitivity", 0.5)
                    ),
                    "order_block_lookback_bars": int(
                        getattr(engine.config.algo, "order_block_lookback_bars", 30)
                    ),
                }
            except Exception:
                pass

            # Scan completed bars for active/unmitigated zones (FVGs, OBs, sweeps)
            if real_smc_overlays and real_smc_overlays.get("rectangles"):
                rectangles = real_smc_overlays.get("rectangles", [])
            else:
                try:
                    completed_bars = engine.aggregator.get_completed_bars()
                    if completed_bars and len(completed_bars) >= 10:
                        lookback = int(algo_config_data.get("order_block_lookback_bars", 30))
                        bars_to_scan = completed_bars[-lookback:]
                        atr_val = atr if atr > 0 else 1.50

                    for i in range(2, len(bars_to_scan)):
                        bar_idx = len(completed_bars) - len(bars_to_scan) + i
                        b_current = completed_bars[bar_idx]
                        b_prev1 = completed_bars[bar_idx - 1]
                        b_prev2 = completed_bars[bar_idx - 2]

                        # Bullish FVG
                        if b_prev2 and b_current.low > b_prev2.high + (atr_val * 0.20):
                            price_low = b_prev2.high
                            price_high = b_current.low
                            mitigated = False
                            for k in range(bar_idx + 1, len(completed_bars)):
                                if completed_bars[k].low <= price_low:
                                    mitigated = True
                                    break
                            if not mitigated:
                                rectangles.append(
                                    {
                                        "id": f"fvg_bull_{bar_idx}",
                                        "type": "BULLISH_FVG",
                                        "price_low": float(price_low),
                                        "price_high": float(price_high),
                                        "ai_confidence": float(ai_confidence or 0.82),
                                        "time": b_prev2.timestamp.isoformat(),
                                    }
                                )

                        # Bearish FVG
                        if b_prev2 and b_current.high < b_prev2.low - (atr_val * 0.20):
                            price_low = b_current.high
                            price_high = b_prev2.low
                            mitigated = False
                            for k in range(bar_idx + 1, len(completed_bars)):
                                if completed_bars[k].high >= price_high:
                                    mitigated = True
                                    break
                            if not mitigated:
                                rectangles.append(
                                    {
                                        "id": f"fvg_bear_{bar_idx}",
                                        "type": "BEARISH_FVG",
                                        "price_low": float(price_low),
                                        "price_high": float(price_high),
                                        "ai_confidence": float(ai_confidence or 0.82),
                                        "time": b_prev2.timestamp.isoformat(),
                                    }
                                )

                        # Bullish Order Block
                        if b_current.close > b_prev1.high and b_prev1.close < b_prev1.open:
                            price_low = b_prev1.low
                            price_high = b_prev1.high
                            mitigated = False
                            for k in range(bar_idx + 1, len(completed_bars)):
                                if completed_bars[k].low < price_low:
                                    mitigated = True
                                    break
                            if not mitigated:
                                rectangles.append(
                                    {
                                        "id": f"ob_bull_{bar_idx}",
                                        "type": "BULLISH_ORDER_BLOCK",
                                        "price_low": float(price_low),
                                        "price_high": float(price_high),
                                        "ai_confidence": float(ai_confidence or 0.85),
                                        "time": b_prev1.timestamp.isoformat(),
                                    }
                                )

                        # Bearish Order Block
                        if b_current.close < b_prev1.low and b_prev1.close > b_prev1.open:
                            price_low = b_prev1.low
                            price_high = b_prev1.high
                            mitigated = False
                            for k in range(bar_idx + 1, len(completed_bars)):
                                if completed_bars[k].high > price_high:
                                    mitigated = True
                                    break
                            if not mitigated:
                                rectangles.append(
                                    {
                                        "id": f"ob_bear_{bar_idx}",
                                        "type": "BEARISH_ORDER_BLOCK",
                                        "price_low": float(price_low),
                                        "price_high": float(price_high),
                                        "ai_confidence": float(ai_confidence or 0.85),
                                        "time": b_prev1.timestamp.isoformat(),
                                    }
                                )

                        # Sweep / Stop Hunt Zone
                        if bar_idx >= 11:
                            recent_lows = [b.low for b in completed_bars[bar_idx - 11 : bar_idx]]
                            recent_highs = [b.high for b in completed_bars[bar_idx - 11 : bar_idx]]
                            min_low = min(recent_lows)
                            max_high = max(recent_highs)

                            if b_current.low < min_low and b_current.close > min_low:
                                rectangles.append(
                                    {
                                        "id": f"sweep_bull_{bar_idx}",
                                        "type": "STOP_HUNT_ZONE",
                                        "price_low": float(b_current.low),
                                        "price_high": float(min_low),
                                        "ai_confidence": float(ai_confidence or 0.90),
                                        "time": b_current.timestamp.isoformat(),
                                    }
                                )
                            elif b_current.high > max_high and b_current.close < max_high:
                                rectangles.append(
                                    {
                                        "id": f"sweep_bear_{bar_idx}",
                                        "type": "STOP_HUNT_ZONE",
                                        "price_low": float(max_high),
                                        "price_high": float(b_current.high),
                                        "ai_confidence": float(ai_confidence or 0.90),
                                        "time": b_current.timestamp.isoformat(),
                                    }
                                )
                except Exception as e:
                    logger.error(
                        "Failed to detect real structural zones from completed bars", error=str(e)
                    )

            fv = engine._last_fv
            proposal = engine._last_proposal

            if not rectangles and fv:
                # Fallback to fv currently forming bar attributes if we have no unmitigated historical ones
                forming_bar = engine.aggregator.get_current_forming_bar()
                f_time = forming_bar.timestamp.isoformat() if forming_bar else None
                if getattr(fv, "order_block_type", 0) == 1:
                    rectangles.append(
                        {
                            "id": "ob_bull",
                            "type": "BULLISH_ORDER_BLOCK",
                            "price_low": float(bid - atr * 0.8),
                            "price_high": float(bid),
                            "ai_confidence": float(ai_confidence or 0.85),
                            "time": f_time,
                        }
                    )
                elif getattr(fv, "order_block_type", 0) == -1:
                    rectangles.append(
                        {
                            "id": "ob_bear",
                            "type": "BEARISH_ORDER_BLOCK",
                            "price_low": float(bid),
                            "price_high": float(bid + atr * 0.8),
                            "ai_confidence": float(ai_confidence or 0.85),
                            "time": f_time,
                        }
                    )
                if getattr(fv, "fvg_bullish_active", False):
                    rectangles.append(
                        {
                            "id": "fvg_bull",
                            "type": "BULLISH_FVG",
                            "price_low": float(bid - atr * 0.5),
                            "price_high": float(bid),
                            "ai_confidence": float(ai_confidence or 0.82),
                            "time": f_time,
                        }
                    )
                if getattr(fv, "fvg_bearish_active", False):
                    rectangles.append(
                        {
                            "id": "fvg_bear",
                            "type": "BEARISH_FVG",
                            "price_low": float(bid),
                            "price_high": float(bid + atr * 0.5),
                            "ai_confidence": float(ai_confidence or 0.82),
                            "time": f_time,
                        }
                    )
                if getattr(fv, "liquidity_sweep_signal", 0) != 0:
                    rectangles.append(
                        {
                            "id": "sweep_zone",
                            "type": "STOP_HUNT_ZONE",
                            "price_low": float(bid - atr * 1.2),
                            "price_high": float(bid + atr * 1.2),
                            "ai_confidence": float(ai_confidence or 0.90),
                            "time": f_time,
                        }
                    )

            # Check for active trade proposals or live active positions to overlay horizontal execution lines
            if proposal and proposal.action != ActionType.NO_TRADE:
                risk_usd = account_data["equity"] * (engine.config.risk.risk_per_trade_pct / 100.0)
                profit_usd = risk_usd * proposal.risk_reward_ratio
                order_lines = {
                    "active": True,
                    "direction": "BUY" if "BUY" in proposal.action.value else "SELL",
                    "entry_price": float(proposal.proposed_entry),
                    "sl_price": float(proposal.stop_loss),
                    "tp_price": float(proposal.take_profit),
                    "risk_reward_ratio": float(proposal.risk_reward_ratio),
                    "risk_usd": float(round(risk_usd, 2)),
                    "profit_usd": float(round(profit_usd, 2)),
                    "zone_score": float(round(proposal.confidence * 100.0, 1)),
                }
            else:
                try:
                    live_positions = engine.adapter.get_positions(symbol=symbol)
                    if live_positions:
                        p = live_positions[0]
                        risk_usd = account_data["equity"] * (
                            engine.config.risk.risk_per_trade_pct / 100.0
                        )
                        sl_dist = abs(p.price_open - p.sl) if p.sl > 0 else (atr * 1.5)
                        tp_dist = abs(p.tp - p.price_open) if p.tp > 0 else (atr * 1.8)
                        risk_reward_ratio = tp_dist / max(sl_dist, 1e-5)
                        profit_usd = risk_usd * risk_reward_ratio
                        order_lines = {
                            "active": True,
                            "direction": p.type.value,
                            "entry_price": float(p.price_open),
                            "sl_price": float(p.sl)
                            if p.sl > 0
                            else float(
                                p.price_open - sl_dist
                                if p.type.value == "BUY"
                                else p.price_open + sl_dist
                            ),
                            "tp_price": float(p.tp)
                            if p.tp > 0
                            else float(
                                p.price_open + tp_dist
                                if p.type.value == "BUY"
                                else p.price_open - tp_dist
                            ),
                            "risk_reward_ratio": float(round(risk_reward_ratio, 2)),
                            "risk_usd": float(round(risk_usd, 2)),
                            "profit_usd": float(round(profit_usd, 2)),
                            "zone_score": 85.0,
                        }
                except Exception:
                    pass

        if not rectangles and not real_smc_overlays:
            t1 = bars_list[30]["time"] if len(bars_list) > 30 else None
            t2 = bars_list[70]["time"] if len(bars_list) > 70 else None
            rectangles = [
                {
                    "id": "mock_ob_bull_1",
                    "type": "BULLISH_ORDER_BLOCK",
                    "price_low": float(bid - atr * 1.5),
                    "price_high": float(bid - atr * 0.5),
                    "ai_confidence": 0.89,
                    "time": t1,
                },
                {
                    "id": "mock_fvg_bear_1",
                    "type": "BEARISH_FVG",
                    "price_low": float(bid + atr * 0.5),
                    "price_high": float(bid + atr * 1.5),
                    "ai_confidence": 0.82,
                    "time": t2,
                },
            ]

        state = {
            "engine_running": engine_running,
            "symbol": symbol,
            "execution_mode": execution_mode,
            "bid": bid,
            "ask": ask,
            "spread": spread,
            "atr": atr,
            "regime": regime,
            "account": account_data,
            "positions": positions_list,
            "bars": bars_list,
            "features": features_payload,
            "probs": probs_data,
            "ai_decision": ai_decision,
            "ai_confidence": ai_confidence,
            "ai_reason": ai_reason,
            "predictions": app.state.simulated_outcomes,
            "algo_config": algo_config_data,
            "visual_overlays": {
                "rectangles": rectangles,
                "bos_lines": real_smc_overlays.get("bos_lines", []),
                "midlines": real_smc_overlays.get("midlines", []),
                "liq_markers": real_smc_overlays.get("liq_markers", []),
                "order_lines": order_lines,
            },
        }
        return serialize_enums(state)

    # WebSocket and /web /ws active connection list
    active_connections: set[WebSocket] = set()

    @app.websocket("/web")
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        active_connections.add(websocket)
        try:
            # Send initial state
            await websocket.send_json(get_system_state())
            while True:
                # Keep connection alive
                await websocket.receive_text()
        except WebSocketDisconnect:
            active_connections.remove(websocket)
        except Exception:
            if websocket in active_connections:
                active_connections.remove(websocket)

    # Static Web Pages routes
    @app.get("/")
    def serve_index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/styles.css")
    def serve_styles() -> FileResponse:
        return FileResponse(WEB_DIR / "styles.css")

    @app.get("/app.js")
    def serve_app() -> FileResponse:
        return FileResponse(WEB_DIR / "app.js")

    # REST APIs: System status
    @app.get("/api/status")
    def get_status() -> dict[str, Any]:
        return get_system_state()

    # REST APIs: Trading Rules
    @app.get("/api/rules")
    def get_trading_rules() -> list[dict[str, Any]]:
        engine = app.state.engine
        if engine:
            return engine.audit.get_trading_rules()
        else:
            from nexus_scalp.adapters.database.audit_repository import AuditRepository

            repo = AuditRepository()
            return repo.get_trading_rules()

    @app.post("/api/rules/toggle")
    def toggle_trading_rule(req: ToggleRuleRequest) -> dict[str, Any]:
        engine = app.state.engine
        params_json = json.dumps(req.parameters) if req.parameters is not None else None

        if engine:
            success = engine.audit.toggle_trading_rule(
                rule_name=req.rule_name, is_enabled=req.is_enabled, parameters_json=params_json
            )
            if success and hasattr(engine, "rule_matrix"):
                engine.rule_matrix.refresh_cache(force=True)
            return {"success": success}
        else:
            from nexus_scalp.adapters.database.audit_repository import AuditRepository

            repo = AuditRepository()
            success = repo.toggle_trading_rule(
                rule_name=req.rule_name, is_enabled=req.is_enabled, parameters_json=params_json
            )
            return {"success": success}

    # REST APIs: Account summary
    @app.get("/api/account/summary")
    def get_account_summary() -> dict[str, Any]:
        """
        Canonical account + performance summary.

        PHASE 08 HARDENING: every number here comes from `AccountingCore`
        (broker adapter -> live state; authoritative ledger -> performance
        totals). When the adapter cannot be read or there is no closed-trade
        history, the fields are `None` - NEVER hardcoded placeholders like
        balance=10000 or win_rate=0.0 (the previous revision served synthetic
        zeros, contradicting the no-synthetic-numbers invariant and the
        duplicate-engine rule; see agents/bugs.md BUG-020).
        """
        engine = app.state.engine
        core = getattr(engine, "accounting_core", None) if engine else None
        if core is None:
            return {
                "available": False,
                "balance": None,
                "equity": None,
                "margin": None,
                "open_positions": None,
                "win_rate": None,
                "profit_factor": None,
                "max_drawdown": None,
                "total_trades": None,
            }

        try:
            live = core.live_state()
            trades = core.load_trades(limit=1000)
            closed = [t for t in trades if t.closed_at is not None]
            decided = sum(1 for t in closed if t.outcome.value in ("WIN", "LOSS"))
            wins = sum(1 for t in closed if t.is_win)
            gross_profit = sum(t.net_pnl for t in closed if t.net_pnl > 0.0)
            gross_loss = abs(sum(t.net_pnl for t in closed if t.net_pnl < 0.0))
            dd = core.drawdown_report()
            return serialize_enums(
                {
                    "available": live.available,
                    "balance": live.balance,
                    "equity": live.equity,
                    "margin": live.margin,
                    "open_positions": live.open_positions,
                    "win_rate": round(wins / decided * 100.0, 2) if decided else None,
                    "profit_factor": (
                        round(gross_profit / gross_loss, 2) if gross_loss > 0.0 else None
                    ),
                    "max_drawdown": dd.max_drawdown_pct,
                    "total_trades": len(closed),
                }
            )
        except Exception as e:
            logger.error("Account summary read failed", error=str(e))
            return {"available": False, "reason": str(e)}

    # REST APIs: Historical trade logs with pagination/filters
    @app.get("/api/account/trades")
    def get_account_trades(
        limit: int = 100, offset: int = 0, status: str | None = None
    ) -> list[dict[str, Any]]:
        engine = app.state.engine
        if not engine:
            return []
        return engine.audit.get_ledger_trades(limit=limit, offset=offset, status_filter=status)

    # REST APIs: Account growth data for visualizer chart
    @app.get("/api/account/growth")
    def get_account_growth() -> list[dict[str, Any]]:
        engine = app.state.engine
        if not engine:
            return []
        return engine.audit.get_equity_growth_chart_data()

    # Toggle Engine Run Loop
    # Toggle Engine Run Loop
    @app.post("/api/engine/toggle")
    def toggle_engine(req: ToggleRequest) -> dict[str, Any]:
        engine = app.state.engine
        if not engine:
            raise HTTPException(status_code=400, detail="Trading Engine reference not loaded.")

        if req.active:
            if not engine._running:
                logger.info("Web Dashboard triggered system start command.")
                task = asyncio.create_task(engine.run_loop())
                if not hasattr(app.state, "background_tasks"):
                    app.state.background_tasks = set()
                app.state.background_tasks.add(task)
                task.add_done_callback(app.state.background_tasks.discard)
        else:
            logger.info("Web Dashboard triggered system stop command.")
            engine._running = False

        return {"success": True, "engine_running": engine._running}

    # GET /api/config
    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        live_config_path = Path("configs/live.yaml")
        if not live_config_path.exists():
            live_config_path = Path("configs/base.yaml")

        with open(live_config_path, encoding="utf-8") as f:
            raw_data = yaml.safe_load(f) or {}
        return raw_data

    # POST /api/config
    @app.post("/api/config")
    def save_config(raw_config: dict[str, Any]) -> dict[str, Any]:
        live_config_path = Path("configs/live.yaml")

        try:
            # Write to disk atomically
            tmp = live_config_path.with_suffix(".yaml.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                yaml.safe_dump(raw_config, f, default_flow_style=False)
            tmp.replace(live_config_path)

            # Hot-reload in active Engine if running
            engine = app.state.engine
            if engine:
                logger.info("Hot-reloading system configurations dynamically.")
                new_cfg = AppConfig.load_from_yaml(live_config_path)
                engine.config = new_cfg
                # Re-apply risk constraints
                engine.risk_engine.config = new_cfg.risk
                engine.signal_policy.confidence_threshold = new_cfg.model.confidence_threshold

            return {"success": True}
        except Exception as e:
            logger.error("Failed to save and hot-reload configurations", error=str(e))
            return {"success": False, "message": str(e)}

    # GET /api/chart/history
    @app.get("/api/chart/history")
    def get_chart_history() -> dict[str, Any]:
        state = get_system_state()
        bars = state.get("bars", [])
        if not bars:
            import random

            start_price = 2334.21
            now_dt = datetime.now()
            for i in range(160):
                close_p = start_price + random.uniform(-0.8, 0.8)
                high_p = max(start_price, close_p) + random.uniform(0.1, 0.4)
                low_p = min(start_price, close_p) - random.uniform(0.1, 0.4)

                bars.append(
                    {
                        "time": (now_dt - timedelta(minutes=160 - i)).isoformat(),
                        "open": start_price,
                        "high": high_p,
                        "low": low_p,
                        "close": close_p,
                        "volume": float(random.randint(10, 50)),
                        "is_complete": True,
                    }
                )
                start_price = close_p
            state["bars"] = bars

        overlays = state.get("visual_overlays", {})
        if not overlays.get("rectangles"):
            overlays["rectangles"] = [
                {
                    "id": "mock_ob_bull_1",
                    "type": "BULLISH_ORDER_BLOCK",
                    "price_low": 2332.10,
                    "price_high": 2333.30,
                    "ai_confidence": 0.89,
                    "time": bars[30]["time"] if len(bars) > 30 else None,
                },
                {
                    "id": "mock_fvg_bear_1",
                    "type": "BEARISH_FVG",
                    "price_low": 2335.50,
                    "price_high": 2336.80,
                    "ai_confidence": 0.82,
                    "time": bars[70]["time"] if len(bars) > 70 else None,
                },
            ]
        if not overlays.get("order_lines"):
            overlays["order_lines"] = {
                "active": True,
                "direction": "BUY",
                "entry_price": 2334.21,
                "sl_price": 2331.50,
                "tp_price": 2339.50,
                "risk_reward_ratio": 1.95,
                "risk_usd": 100.00,
                "profit_usd": 195.00,
                "zone_score": 88.0,
            }
        state["visual_overlays"] = overlays
        return state

    # GET /api/algo/config
    @app.get("/api/algo/config")
    def get_algo_config() -> dict[str, Any]:
        live_config_path = Path("configs/live.yaml")
        if not live_config_path.exists():
            live_config_path = Path("configs/base.yaml")

        with open(live_config_path, encoding="utf-8") as f:
            raw_data = yaml.safe_load(f) or {}

        algo_data = raw_data.get("algo", {})
        return {
            "atr_sl_buffer_multiplier": algo_data.get("atr_sl_buffer_multiplier", 1.5),
            "min_risk_reward_ratio": algo_data.get("min_risk_reward_ratio", 1.8),
            "ai_zone_confidence_threshold": algo_data.get("ai_zone_confidence_threshold", 0.82),
            "fvg_mitigation_sensitivity": algo_data.get("fvg_mitigation_sensitivity", 0.5),
            "order_block_lookback_bars": algo_data.get("order_block_lookback_bars", 30),
        }

    # PUT /api/algo/config
    @app.put("/api/algo/config")
    def save_algo_config(req: AlgoConfigRequest) -> dict[str, Any]:
        live_config_path = Path("configs/live.yaml")
        if not live_config_path.exists():
            live_config_path = Path("configs/base.yaml")

        try:
            with open(live_config_path, encoding="utf-8") as f:
                raw_data = yaml.safe_load(f) or {}

            raw_data["algo"] = req.model_dump()

            tmp = Path("configs/live.yaml").with_suffix(".yaml.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                yaml.safe_dump(raw_data, f, default_flow_style=False)
            tmp.replace(Path("configs/live.yaml"))

            engine = app.state.engine
            if engine:
                logger.info("Hot-reloading algorithm live tuner parameters dynamically.")
                new_cfg = AppConfig.load_from_yaml(Path("configs/live.yaml"))
                engine.config = new_cfg
                engine.signal_policy.algo_config = new_cfg.algo

            return {"success": True}
        except Exception as e:
            logger.error("Failed to save and hot-reload algo tuner configurations", error=str(e))
            return {"success": False, "message": str(e)}

    # Modify position SL/TP
    @app.post("/api/positions/modify")
    def modify_position(req: ModifyPositionRequest) -> dict[str, Any]:
        engine = app.state.engine
        if not engine:
            raise HTTPException(status_code=400, detail="Trading Engine offline.")

        success = engine.adapter.modify_position(
            ticket=req.ticket, stop_loss=req.stop_loss, take_profit=req.take_profit
        )
        return {"success": success}

    # Close live positions
    @app.post("/api/positions/close")
    def close_position(req: ClosePositionRequest) -> dict[str, Any]:
        engine = app.state.engine
        if not engine:
            raise HTTPException(status_code=400, detail="Trading Engine offline.")

        success = engine.adapter.close_position(ticket=req.ticket)
        return {"success": success}

    # Simulation: Inject simulated tick
    @app.post("/api/simulation/tick")
    def inject_tick(req: SimulationTickRequest) -> dict[str, Any]:
        engine = app.state.engine
        if not engine:
            return {"success": False, "message": "Engine not initialized"}

        symbol = engine.config.execution.symbol
        try:
            current_tick = engine.adapter.get_last_tick(symbol)
            if not current_tick:
                current_tick = TickData(
                    symbol=symbol, timestamp=datetime.now(UTC), bid=2334.21, ask=2334.41, volume=1.0
                )
        except Exception:
            current_tick = TickData(
                symbol=symbol, timestamp=datetime.now(UTC), bid=2334.21, ask=2334.41, volume=1.0
            )

        # Apply simulation tick displacement pressure
        bid_change = 0.0
        ask_change = 0.0

        if req.type == "BUY_PRESSURE":
            bid_change = 0.85
            ask_change = 0.85
        elif req.type == "SELL_PRESSURE":
            bid_change = -0.85
            ask_change = -0.85
        elif req.type == "VOLATILE_SWEEP":
            bid_change = -2.50
            ask_change = -2.50

        simulated_tick = TickData(
            symbol=symbol,
            timestamp=datetime.now(UTC),
            bid=current_tick.bid + bid_change,
            ask=current_tick.ask + ask_change,
            volume=current_tick.volume + 5.0,
        )

        # Inject simulated tick directly to engine tick processor pipeline
        logger.info(
            "Injecting interactive simulation tick pressure.",
            type=req.type,
            bid=simulated_tick.bid,
            ask=simulated_tick.ask,
        )

        # Track simulated outcome
        prob_sim = (
            0.76 if req.type == "BUY_PRESSURE" else (0.81 if req.type == "SELL_PRESSURE" else 0.12)
        )
        outcome_status = "TRUE_POSITIVE" if req.type != "VOLATILE_SWEEP" else "FALSE_POSITIVE"

        app.state.simulated_outcomes.append(
            {
                "time": datetime.now(UTC).strftime("%H:%M:%S"),
                "action": "BUY_MARKET"
                if req.type == "BUY_PRESSURE"
                else ("SELL_MARKET" if req.type == "SELL_PRESSURE" else "NO_ACTION"),
                "confidence": prob_sim,
                "actual_delta": bid_change,
                "outcome": outcome_status,
            }
        )

        # Process the simulated tick
        if engine._running:
            engine._process_tick_pipeline(
                tick=simulated_tick, account=engine.adapter.get_account_info()
            )

        return {"success": True}

    # Historical Replay Mode Controller
    @app.post("/api/replay/toggle")
    def toggle_replay(req: ToggleReplayRequest) -> dict[str, Any]:
        app.state.is_replaying = req.active
        app.state.replay_speed = req.speed

        engine = app.state.engine
        if engine:
            if req.active:
                logger.info("Historical Replay mode enabled.", speed=req.speed)
                # Toggle engine configuration to replay mode
                engine.config.execution.mode = ExecutionMode.PAPER
            else:
                logger.info("Historical Replay mode disabled.")
                engine.config.execution.mode = ExecutionMode.LIVE

        return {"success": True, "replaying": req.active}

    # =========================================================================
    # MODULE C: DEBUG & DIAGNOSTICS HUB — BACKEND REST ENDPOINTS
    # =========================================================================

    @app.get("/api/debug/features")
    def get_debug_features() -> dict[str, Any]:
        """
        Real-time values of all 50 features (feat_0 .. feat_49).

        Each entry reports the raw model-input value alongside a health status so the UI
        can flag NaN/Inf anomalies, plus a staleness assessment of the feature snapshot
        as a whole (age of the last computed FeatureVector).
        """
        engine = app.state.engine

        raw_values: list[Any] = [0.0] * len(FEATURE_NAMES)
        feature_timestamp: str | None = None
        age_seconds: float | None = None
        engine_online = engine is not None

        if engine is not None:
            try:
                fv = engine._last_fv
                if fv is not None:
                    raw_values = list(fv.to_tensor_input())
                    feature_timestamp = getattr(fv, "timestamp_utc", None)
                    if feature_timestamp:
                        try:
                            ts = datetime.fromisoformat(str(feature_timestamp))
                            if ts.tzinfo is None:
                                ts = ts.replace(tzinfo=UTC)
                            age_seconds = max(0.0, (datetime.now(UTC) - ts).total_seconds())
                        except (TypeError, ValueError):
                            age_seconds = None
            except Exception as e:
                logger.error("Debug features: failed to read live feature vector", error=str(e))

        features_payload: list[dict[str, Any]] = []
        nan_count = 0
        inf_count = 0

        for idx, name in enumerate(FEATURE_NAMES):
            raw = raw_values[idx] if idx < len(raw_values) else 0.0
            value, status = _classify_feature(raw)
            if status == "NAN":
                nan_count += 1
            elif status == "INF":
                inf_count += 1
            features_payload.append(
                {
                    "index": idx,
                    "key": f"feat_{idx}",
                    "name": name,
                    "value": value,
                    "status": status,
                    "is_valid": status == "VALID",
                }
            )

        # A snapshot older than 15s means the tick pipeline is not feeding the model.
        STALE_THRESHOLD_SEC = 15.0
        is_stale = (age_seconds is None) or (age_seconds > STALE_THRESHOLD_SEC)

        return {
            "engine_online": engine_online,
            "feature_count": len(features_payload),
            "features": features_payload,
            "nan_count": nan_count,
            "inf_count": inf_count,
            "anomaly_count": nan_count + inf_count,
            "all_valid": (nan_count + inf_count) == 0,
            "timestamp_utc": feature_timestamp,
            "age_seconds": age_seconds,
            "is_stale": is_stale,
            "stale_threshold_seconds": STALE_THRESHOLD_SEC,
        }

    @app.post("/api/debug/model-test")
    def post_debug_model_test(req: ModelTestRequest) -> dict[str, Any]:
        """
        Runs an instant PyTorch ScalpNet inference against a supplied (or live) 50D vector.

        Returns class probabilities (ai_no_trade / ai_buy / ai_sell), the argmax label and
        the inference latency, so the Debug Hub can verify the model end-to-end without
        waiting for a live signal.
        """
        engine = app.state.engine
        expected_dim = len(FEATURE_NAMES)

        features = req.features
        source = "REQUEST"

        if features is None or req.use_live_features:
            if engine is None or getattr(engine, "_last_fv", None) is None:
                raise HTTPException(
                    status_code=400,
                    detail="No feature vector supplied and no live features available (engine offline).",
                )
            features = list(engine._last_fv.to_tensor_input())
            source = "LIVE"

        if len(features) != expected_dim:
            raise HTTPException(
                status_code=422,
                detail=f"Feature vector must contain exactly {expected_dim} values, got {len(features)}.",
            )

        sanitized: list[float] = []
        sanitized_count = 0
        for raw in features:
            value, status = _classify_feature(raw)
            if status != "VALID":
                sanitized_count += 1
            sanitized.append(value)

        try:
            import numpy as np
            import torch
        except Exception as import_err:
            raise HTTPException(
                status_code=503, detail=f"PyTorch runtime unavailable: {import_err}"
            ) from import_err

        started = time.perf_counter()

        try:
            if engine is not None and getattr(engine, "_bundle", None) is not None:
                # Use the live bundle so the test exercises the exact deployed weights and scaler.
                with engine._bundle_lock:
                    bundle = engine._bundle
                x_np = np.array(sanitized, dtype=np.float32).reshape(1, -1)
                x_np = bundle.scaler.transform_50d(x_np)
                x = torch.tensor(x_np, dtype=torch.float32)
                x = torch.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0)
                bundle.model.eval()
                with torch.inference_mode():
                    probs_tensor = bundle.model(x)
                model_source = "LIVE_BUNDLE"
            else:
                # Engine offline: instantiate a fresh net so the endpoint still validates
                # the model graph and tensor contract.
                from nexus_scalp.models.scalp_net import ScalpNet

                model = ScalpNet(num_features=expected_dim, num_classes=4)
                model.eval()
                x = torch.tensor([sanitized], dtype=torch.float32)
                x = torch.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0)
                with torch.inference_mode():
                    probs_tensor = model(x)
                model_source = "FRESH_INSTANCE"
        except HTTPException:
            raise
        except Exception as infer_err:
            logger.error("Debug model test inference failed", error=str(infer_err))
            raise HTTPException(
                status_code=500, detail=f"Inference failed: {infer_err}"
            ) from infer_err

        latency_ms = (time.perf_counter() - started) * 1000.0

        probs_list = [float(p) for p in probs_tensor.detach().cpu().numpy().flatten().tolist()]
        while len(probs_list) < 4:
            probs_list.append(0.0)

        ai_no_trade, ai_buy, ai_sell = probs_list[0], probs_list[1], probs_list[2]
        ai_wait = probs_list[3]

        labels = {0: "NO_TRADE", 1: "BUY_MARKET", 2: "SELL_MARKET", 3: "WAIT"}
        argmax_idx = max(range(len(probs_list)), key=lambda i: probs_list[i])

        return {
            "success": True,
            "feature_source": source,
            "model_source": model_source,
            "sanitized_inputs": sanitized_count,
            "ai_no_trade": ai_no_trade,
            "ai_buy": ai_buy,
            "ai_sell": ai_sell,
            "ai_wait": ai_wait,
            "probabilities": probs_list,
            "predicted_class_index": argmax_idx,
            "predicted_label": labels.get(argmax_idx, "UNKNOWN"),
            "confidence": probs_list[argmax_idx],
            "latency_ms": round(latency_ms, 3),
            "evaluated_at": datetime.now(UTC).isoformat(),
        }

    @app.get("/api/debug/health")
    def get_debug_health() -> dict[str, Any]:
        """
        Subsystem health widgets: Feature Engine, PyTorch Model, Risk Engine,
        MT5 Win32 IPC Adapter and Audit Database.

        Each subsystem reports HEALTHY / DEGRADED / UNHEALTHY / DISCONNECTED plus a short
        human-readable detail string and subsystem-specific metrics.
        """
        engine = app.state.engine
        subsystems: list[dict[str, Any]] = []

        def add(name: str, status: str, detail: str, metrics: dict[str, Any] | None = None) -> None:
            subsystems.append(
                {
                    "name": name,
                    "status": status,
                    "detail": detail,
                    "metrics": metrics or {},
                }
            )

        # --- 1. Feature Engine ---
        if engine is None:
            add(
                "Feature Engine",
                "DISCONNECTED",
                "Engine reference is not attached to the web server.",
            )
        else:
            try:
                fv = engine._last_fv
                if fv is None:
                    add(
                        "Feature Engine",
                        "DEGRADED",
                        "No feature vector computed yet (waiting for first tick).",
                    )
                else:
                    values = list(fv.to_tensor_input())
                    bad = sum(1 for v in values if _classify_feature(v)[1] != "VALID")
                    dim_ok = len(values) == len(FEATURE_NAMES)
                    if not dim_ok:
                        add(
                            "Feature Engine",
                            "UNHEALTHY",
                            f"Dimensionality contract violated: {len(values)} != {len(FEATURE_NAMES)}.",
                            {"dimensions": len(values), "expected": len(FEATURE_NAMES)},
                        )
                    elif bad:
                        add(
                            "Feature Engine",
                            "DEGRADED",
                            f"{bad} of {len(values)} features are NaN/Inf.",
                            {"anomalies": bad, "dimensions": len(values)},
                        )
                    else:
                        add(
                            "Feature Engine",
                            "HEALTHY",
                            f"All {len(values)} features numeric and within contract.",
                            {"anomalies": 0, "dimensions": len(values)},
                        )
            except Exception as e:
                add("Feature Engine", "UNHEALTHY", f"Feature extraction raised: {e}")

        # --- 2. PyTorch Model ---
        if engine is None:
            add("PyTorch Model", "DISCONNECTED", "Engine offline; model bundle not loaded.")
        else:
            try:
                with engine._bundle_lock:
                    bundle = engine._bundle
                if bundle is None:
                    add("PyTorch Model", "UNHEALTHY", "Model bundle is not initialized.")
                else:
                    scaler_ready = bool(getattr(bundle.scaler, "is_ready", lambda: False)())
                    probs = engine._last_probs
                    last_infer_ok = probs is not None
                    metrics = {
                        "artifact_path": str(getattr(bundle, "artifact_path", "")),
                        "scaler_ready": scaler_ready,
                        "last_inference_available": last_infer_ok,
                    }
                    if not scaler_ready:
                        add(
                            "PyTorch Model",
                            "DEGRADED",
                            "Weights loaded but scaler artifact is not fitted.",
                            metrics,
                        )
                    elif not last_infer_ok:
                        add(
                            "PyTorch Model",
                            "DEGRADED",
                            "Model ready; awaiting first live inference.",
                            metrics,
                        )
                    else:
                        add(
                            "PyTorch Model",
                            "HEALTHY",
                            "ScalpNet loaded with fitted scaler and live inference flowing.",
                            metrics,
                        )
            except Exception as e:
                add("PyTorch Model", "UNHEALTHY", f"Model introspection raised: {e}")

        # --- 3. Risk Engine ---
        if engine is None:
            add("Risk Engine", "DISCONNECTED", "Engine offline.")
        else:
            try:
                risk = engine.risk_engine
                kill_switch = bool(getattr(risk, "_kill_switch_active", False))
                metrics = {
                    "kill_switch_active": kill_switch,
                    "max_allowed_lots": float(getattr(risk, "max_allowed_lots", 0.0)),
                    "hard_max_lots": 10.0,
                    "min_risk_reward_ratio": float(getattr(risk, "min_risk_reward_ratio", 0.0)),
                    "survival_mode": bool(getattr(engine, "_survival_mode_active", False)),
                }
                if kill_switch:
                    add(
                        "Risk Engine",
                        "UNHEALTHY",
                        "EMERGENCY KILL SWITCH ACTIVE — all execution rejected.",
                        metrics,
                    )
                elif metrics["survival_mode"]:
                    add(
                        "Risk Engine",
                        "DEGRADED",
                        "Survival mode active: thresholds tightened after drawdown.",
                        metrics,
                    )
                else:
                    add(
                        "Risk Engine",
                        "HEALTHY",
                        "Clamps armed (HARD_MAX_LOTS = 10.0), kill switch disengaged.",
                        metrics,
                    )
            except Exception as e:
                add("Risk Engine", "UNHEALTHY", f"Risk engine introspection raised: {e}")

        # --- 4. MT5 Win32 IPC Adapter ---
        if engine is None:
            add("MT5 Win32 IPC Adapter", "DISCONNECTED", "Engine offline; no broker adapter bound.")
        else:
            try:
                adapter = engine.adapter
                is_conn_fn = getattr(adapter, "is_connected", None)
                connected = bool(is_conn_fn()) if callable(is_conn_fn) else True

                tick = engine._last_tick
                tick_age = None
                if tick is not None:
                    try:
                        tick_age = max(0.0, (datetime.now(UTC) - tick.timestamp).total_seconds())
                    except Exception:
                        tick_age = None

                metrics = {
                    "adapter": type(adapter).__name__,
                    "connected": connected,
                    "last_tick_age_seconds": tick_age,
                    "execution_mode": engine.config.execution.mode.value,
                    "symbol": engine.config.execution.symbol,
                }
                if not connected:
                    add(
                        "MT5 Win32 IPC Adapter",
                        "DISCONNECTED",
                        "Broker IPC channel reports disconnected.",
                        metrics,
                    )
                elif tick_age is None:
                    add(
                        "MT5 Win32 IPC Adapter",
                        "DEGRADED",
                        "Connected but no tick has been received yet.",
                        metrics,
                    )
                elif tick_age > 15.0:
                    add(
                        "MT5 Win32 IPC Adapter",
                        "DEGRADED",
                        f"Tick stream stale ({tick_age:.1f}s since last tick).",
                        metrics,
                    )
                else:
                    add(
                        "MT5 Win32 IPC Adapter",
                        "HEALTHY",
                        f"Live tick stream active ({tick_age:.1f}s ago).",
                        metrics,
                    )
            except Exception as e:
                add("MT5 Win32 IPC Adapter", "UNHEALTHY", f"Adapter introspection raised: {e}")

        # --- 5. Audit Database ---
        try:
            if engine is not None:
                repo = engine.audit
            else:
                from nexus_scalp.adapters.database.audit_repository import AuditRepository

                repo = AuditRepository()

            metrics_db = repo.get_account_performance_metrics()
            queue_size = 0
            queue_obj = getattr(repo, "_queue", None)
            if queue_obj is not None:
                try:
                    queue_size = int(queue_obj.qsize())
                except Exception:
                    queue_size = 0

            worker = getattr(repo, "_worker_thread", None)
            worker_alive = bool(worker.is_alive()) if worker is not None else False

            metrics = {
                "db_path": getattr(repo, "_db_path", ""),
                "write_queue_depth": queue_size,
                "worker_alive": worker_alive,
                "total_trades": metrics_db.get("total_trades", 0),
            }
            if not worker_alive:
                add(
                    "Audit Database", "DEGRADED", "Background write worker is not running.", metrics
                )
            elif queue_size > 5000:
                add(
                    "Audit Database",
                    "DEGRADED",
                    f"Write queue backing up ({queue_size} pending).",
                    metrics,
                )
            else:
                add(
                    "Audit Database",
                    "HEALTHY",
                    "WAL storage reachable; async writer draining normally.",
                    metrics,
                )
        except Exception as e:
            add("Audit Database", "UNHEALTHY", f"Audit DB unreachable: {e}")

        rank = {"HEALTHY": 0, "DEGRADED": 1, "UNHEALTHY": 2, "DISCONNECTED": 2}
        overall = "HEALTHY"
        for sub in subsystems:
            if rank.get(sub["status"], 0) > rank.get(overall, 0):
                overall = sub["status"]

        return {
            "overall_status": overall,
            "subsystems": subsystems,
            "checked_at": datetime.now(UTC).isoformat(),
        }

    @app.get("/api/debug/ipc-telemetry")
    def get_debug_ipc_telemetry(limit: int = 50) -> dict[str, Any]:
        """
        Recent broker execution events for the MT5 IPC Telemetry Console:
        order state transitions, reason/retcode strings and measured IPC latency.
        """
        engine = app.state.engine
        try:
            if engine is not None:
                repo = engine.audit
            else:
                from nexus_scalp.adapters.database.audit_repository import AuditRepository

                repo = AuditRepository()
            events = repo.get_recent_order_events(limit=max(1, min(limit, 500)))
        except Exception as e:
            logger.error("Debug IPC telemetry retrieval failed", error=str(e))
            events = []

        latencies = [float(e.get("latency") or 0.0) for e in events if e.get("latency") is not None]
        avg_latency_ms = round((sum(latencies) / len(latencies)) * 1000.0, 2) if latencies else 0.0

        exposure = {"positions": 0, "pendings": 0}
        if engine is not None and hasattr(engine.order_manager, "count_total_exposure"):
            try:
                pos, pend = engine.order_manager.count_total_exposure()
                exposure = {"positions": pos, "pendings": pend}
            except Exception:
                pass

        return {
            "events": events,
            "event_count": len(events),
            "avg_latency_ms": avg_latency_ms,
            "exposure": exposure,
            "max_total_exposure": 1,
            "fetched_at": datetime.now(UTC).isoformat(),
        }

    # =========================================================================
    # PHASE 08 EXPERIENCE INTELLIGENCE REST APIs
    # -------------------------------------------------------------------------
    # All endpoints are READ-ONLY over derived state, except the explicit
    # self-heal endpoint which only rebuilds derived intelligence from the
    # immutable ledger (it can never modify or delete raw experience rows).
    # =========================================================================
    @app.get("/api/experience/summary")
    def get_experience_summary() -> dict[str, Any]:
        """Aggregate experience/gate telemetry including schema provenance."""
        engine = app.state.engine
        if not engine or not hasattr(engine, "experience_engine"):
            return {
                "enabled": False,
                "recorded_experiences": 0,
                "active_strategies": 0,
            }

        try:
            summary = dict(engine.experience_engine.summary())
        except Exception as e:
            logger.error("Failed to build experience summary", error=str(e))
            summary = {"enabled": False, "recorded_experiences": 0}

        lifecycle_counts: dict[str, int] = {}
        try:
            with sqlite3.connect(engine.audit._db_path, timeout=5.0) as conn:
                rows = conn.execute(
                    """
                    SELECT lifecycle_state, COUNT(*) FROM strategy_intelligence_registry
                    GROUP BY lifecycle_state;
                    """
                ).fetchall()
                lifecycle_counts = {str(r[0]): int(r[1]) for r in rows}
        except Exception:
            lifecycle_counts = {}

        summary["lifecycle_counts"] = lifecycle_counts
        summary["active_strategies"] = lifecycle_counts.get("ACTIVE", 0)
        summary["retired_strategies"] = lifecycle_counts.get("RETIRED", 0)
        summary["fetched_at"] = datetime.now(UTC).isoformat()
        return serialize_enums(summary)

    @app.get("/api/experience/strategies")
    def get_experience_strategies(limit: int = 50) -> list[dict[str, Any]]:
        """Bounded listing of derived strategy scores, newest first."""
        engine = app.state.engine
        if not engine:
            return []

        bounded = max(1, min(int(limit), 500))
        try:
            with sqlite3.connect(engine.audit._db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM strategy_intelligence_registry ORDER BY updated_at DESC LIMIT ?;",
                    (bounded,),
                )
                return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error("Failed to retrieve experience strategies", error=str(e))
            return []

    @app.get("/api/experience/decision")
    def get_last_experience_decision() -> dict[str, Any]:
        """Most recent pre-trade experience verdict, for live explainability."""
        engine = app.state.engine
        decision = getattr(engine, "_last_experience_decision", None) if engine else None
        if decision is None:
            return {"available": False}
        try:
            payload = json.loads(decision.model_dump_json())
        except Exception as e:
            logger.error("Failed to serialize experience decision", error=str(e))
            return {"available": False}
        return {"available": True, "decision": payload}

    @app.get("/api/experience/models")
    def get_experience_models(limit: int = 25) -> list[dict[str, Any]]:
        """
        Registered model provenance history.

        Proves model/memory separation: entries here may reference artifacts that
        no longer exist while the experience ledger remains intact.
        """
        engine = app.state.engine
        registry = getattr(engine, "model_registry", None) if engine else None
        if registry is None:
            return []
        try:
            return [dict(r) for r in registry.list_registered_models(limit=limit)]
        except Exception as e:
            logger.error("Failed to retrieve model registry", error=str(e))
            return []

    @app.post("/api/experience/self-heal")
    def trigger_experience_self_heal() -> dict[str, Any]:
        """
        Rebuilds derived strategy intelligence from the immutable ledger.

        Raw experience rows are read-only during this operation.
        """
        engine = app.state.engine
        if not engine or not hasattr(engine, "rebuild_experience_intelligence"):
            return {"success": False, "rebuilt_strategies": 0, "reason": "ENGINE_UNAVAILABLE"}
        try:
            count = engine.rebuild_experience_intelligence()
            return {"success": True, "rebuilt_strategies": int(count)}
        except Exception as e:
            logger.error("Experience self-heal failed", error=str(e))
            return {"success": False, "rebuilt_strategies": 0, "reason": str(e)}

    # =========================================================================
    # PHASE 08: UNIFIED ACCOUNTING & PERFORMANCE INTELLIGENCE REST APIs
    # -------------------------------------------------------------------------
    # Every endpoint reads REAL data through the single canonical AccountingCore
    # facade (authoritative SQLite tables + derived cache warmed by the worker).
    # There is no synthetic fallback anywhere: when a metric cannot be derived
    # it is null and the dashboard renders an explicit unavailable state.
    # =========================================================================

    def _accounting() -> tuple[Any, Any] | None:
        """Returns (accounting_core, accounting_worker) when available."""
        engine = app.state.engine
        if not engine or not hasattr(engine, "accounting_core"):
            return None
        return engine.accounting_core, getattr(engine, "accounting_worker", None)

    @app.get("/api/account/performance")
    def get_account_performance() -> dict[str, Any]:
        """Canonical live + period performance overview (single truth)."""
        pair = _accounting()
        if pair is None:
            return {"available": False, "reason": "ENGINE_UNAVAILABLE"}
        core, worker = pair
        try:
            live = core.live_state()
            periods = core.all_period_reports()
            dd = core.drawdown_report()
            trades = core.load_trades(limit=1000)
            closed = [t for t in trades if t.closed_at is not None]
            wins = sum(1 for t in closed if t.is_win)
            losses = sum(1 for t in closed if t.outcome.value == "LOSS")
            decided = wins + losses
            realized_pnl = sum(t.net_pnl for t in closed)
            return serialize_enums(
                {
                    "available": True,
                    "live": live.to_dict(),
                    "periods": {k: v.to_dict() for k, v in periods.items()},
                    "drawdown": dd.to_dict(),
                    "worker": format_worker_status(worker) if worker else None,
                    "totals": {
                        "closed_trades": len(closed),
                        "win_count": wins,
                        "loss_count": losses,
                        "win_rate": round(wins / decided * 100.0, 2) if decided else None,
                        "realized_pnl": round(realized_pnl, 2),
                    },
                    "fetched_at": datetime.now(UTC).isoformat(),
                }
            )
        except Exception as e:
            logger.error("Account performance read failed", error=str(e))
            return {"available": False, "reason": str(e)}

    @app.get("/api/account/performance/{kind}")
    def get_account_performance_period(kind: str) -> dict[str, Any]:
        """Canonical report for one granularity (DAY/WEEK/MONTH/YEAR)."""
        pair = _accounting()
        if pair is None:
            return {"available": False, "reason": "ENGINE_UNAVAILABLE"}
        core, _ = pair
        try:
            enum_kind = PeriodKind(kind.upper())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown period kind: {kind}") from None
        try:
            report = core.period_report(enum_kind)
            return {"available": True, "period": report.to_dict()}
        except Exception as e:
            logger.error("Period report failed", kind=kind, error=str(e))
            return {"available": False, "reason": str(e)}

    @app.get("/api/account/performance/{kind}/series")
    def get_account_performance_series(kind: str, count: int = 30) -> dict[str, Any]:
        """Bounded consecutive-period series for charts (oldest -> newest)."""
        pair = _accounting()
        if pair is None:
            return {"available": False, "reason": "ENGINE_UNAVAILABLE"}
        core, _ = pair
        try:
            enum_kind = PeriodKind(kind.upper())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown period kind: {kind}") from None
        bounded = max(1, min(int(count), 60))
        try:
            reports = core.period_series(enum_kind, count=bounded)
            return {
                "available": True,
                "kind": enum_kind.value,
                "periods": [r.to_dict() for r in reports],
            }
        except Exception as e:
            logger.error("Period series failed", kind=kind, error=str(e))
            return {"available": False, "reason": str(e)}

    @app.get("/api/account/equity-curve")
    def get_account_equity_curve(lookback_days: int | None = None) -> dict[str, Any]:
        """Canonical balance/equity/drawdown time series for the dashboard."""
        pair = _accounting()
        if pair is None:
            return {"available": False, "reason": "ENGINE_UNAVAILABLE"}
        core, _ = pair
        try:
            bounded = max(1, min(int(lookback_days), 730)) if lookback_days else None
            curve = core.equity_curve(lookback_days=bounded)
            cumulative = core.cumulative_pnl_curve(limit=500)
            return {
                "available": True,
                "equity_curve": curve,
                "cumulative_pnl": cumulative,
                "fetched_at": datetime.now(UTC).isoformat(),
            }
        except Exception as e:
            logger.error("Equity curve read failed", error=str(e))
            return {"available": False, "reason": str(e)}

    @app.get("/api/account/drawdown")
    def get_account_drawdown(lookback_days: int | None = None) -> dict[str, Any]:
        """Canonical drawdown state (ONE methodology for the whole system)."""
        pair = _accounting()
        if pair is None:
            return {"available": False, "reason": "ENGINE_UNAVAILABLE"}
        core, _ = pair
        try:
            bounded = max(1, min(int(lookback_days), 730)) if lookback_days else None
            report = core.drawdown_report(lookback_days=bounded)
            out = report.to_dict()
            out["available"] = report.has_data or True
            return out
        except Exception as e:
            logger.error("Drawdown read failed", error=str(e))
            return {"available": False, "reason": str(e)}

    @app.get("/api/account/trades/{trade_id}")
    def get_account_trade_forensics(trade_id: int) -> dict[str, Any]:
        """Forensic reconstruction of one closed trade (ledger + orders + experience)."""
        pair = _accounting()
        if pair is None:
            return {"available": False, "reason": "ENGINE_UNAVAILABLE"}
        core, _ = pair
        try:
            trace = core.trade_trace(ticket=int(trade_id))
            payload = trace.to_dict()
            payload["available"] = trace.found
            return payload
        except Exception as e:
            logger.error("Trade forensics failed", ticket=trade_id, error=str(e))
            return {"available": False, "reason": str(e)}

    @app.get("/api/account/strategies")
    def get_account_strategies(limit: int = 50) -> dict[str, Any]:
        """Per-strategy contribution joined to Strategy Intelligence."""
        pair = _accounting()
        if pair is None:
            return {"available": False, "reason": "ENGINE_UNAVAILABLE"}
        core, _ = pair
        try:
            bounded = max(1, min(int(limit), 200))
            contributions = core.strategy_contributions(limit=bounded)
            return {
                "available": True,
                "strategies": [c.to_dict() for c in contributions],
                "fetched_at": datetime.now(UTC).isoformat(),
            }
        except Exception as e:
            logger.error("Strategy contributions failed", error=str(e))
            return {"available": False, "reason": str(e)}

    # Observability stats
    @app.get("/api/observability/stats")
    def get_observability_stats() -> dict[str, Any]:
        engine = app.state.engine
        tg_queue_size = 0
        tg_enabled = False

        if engine and engine.notifier:
            tg_enabled = engine.notifier.enabled
            if hasattr(engine.notifier, "_queue"):
                tg_queue_size = engine.notifier._queue.qsize()

        return {"tg_enabled": tg_enabled, "tg_queue": tg_queue_size}

    # =========================================================================
    # PHASE 09: TRADE INTELLIGENCE REST APIs
    # -------------------------------------------------------------------------
    # READ-ONLY views over the derived intelligence tables (lifecycle timeline,
    # trade autopsies, behavior detections, evolution candidates) plus the
    # suitability verdict. Nothing here mutates financial truth or executes.
    # =========================================================================

    def _intelligence() -> tuple[Any, Any] | None:
        """Returns (engine, intelligence_worker) when available."""
        engine = app.state.engine
        if not engine or not hasattr(engine, "intelligence_worker"):
            return None
        return (
            engine,
            getattr(engine, "intelligence_worker", None),
        )

    @app.get("/api/intelligence/summary")
    def get_intelligence_summary() -> dict[str, Any]:
        """Aggregate Trade Intelligence Brain telemetry + worker status."""
        pair = _intelligence()
        if pair is None:
            return {"available": False, "reasons": "ENGINE_UNAVAILABLE"}
        engine, worker = pair
        try:
            from nexus_scalp.intelligence.store import (
                count_autopsies,
                count_lifecycle_events,
            )

            summary = {
                "available": True,
                "lifecycle_events": count_lifecycle_events(engine.audit),
                "autopsies": count_autopsies(engine.audit),
                "worker": _intelligence_worker_status(worker),
                "fetch_time": datetime.now(UTC).isoformat(),
            }
            # Suitability verdict for the last proposal (live explainability).
            verdict = getattr(engine, "_last_suitability_verdict", None)
            if verdict is not None:
                summary["last_suitability"] = verdict.to_dict()
            return serialize_enums(summary)
        except Exception as e:
            logger.error("Intelligence summary failed", error=str(e))
            return {"available": False, "reasons": str(e)}

    @app.get("/api/intelligence/positions/{ticket}/timeline")
    def get_position_timeline(ticket: int) -> dict[str, Any]:
        """Immutable position lifecycle timeline for one ticket."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            from nexus_scalp.intelligence.store import load_lifecycle_events

            events = load_lifecycle_events(engine.audit, ticket=str(ticket), limit=500)
            return serialize_enums(
                {
                    "available": True,
                    "ticket": str(ticket),
                    "events": [e.model_dump() for e in events],
                }
            )
        except Exception as e:
            logger.error("Timeline read failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.get("/api/intelligence/autopsies")
    def get_intelligence_autopsies(
        strategy_id: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """Bounded listing of trade autopsies (why did each trade win/lose)."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            from nexus_scalp.intelligence.store import list_autopsies

            rows = list_autopsies(engine.audit, strategy_id=strategy_id, limit=limit)
            return serialize_enums({"available": True, "autopsies": rows})
        except Exception as e:
            logger.error("Autopsy list failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.get("/api/intelligence/autopsies/{ticket}")
    def get_intelligence_autopsy(ticket: str) -> dict[str, Any]:
        """Single forensic autopsy for one ticket."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            from nexus_scalp.intelligence.store import load_autopsy

            row = load_autopsy(engine.audit, ticket)
            if row is None:
                return {"available": False, "reason": "NO_AUTOPSY"}
            return serialize_enums({"available": True, "autopsy": row})
        except Exception as e:
            logger.error("Autopsy read failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.get("/api/intelligence/behavior")
    def get_intelligence_behavior(ticket: int | None = None, limit: int = 100) -> dict[str, Any]:
        """Measurable behavioral-pattern detections."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            from nexus_scalp.intelligence.store import list_behavior_detections

            rows = list_behavior_detections(engine.audit, ticket=ticket, limit=limit)
            return serialize_enums({"available": True, "detections": rows})
        except Exception as e:
            logger.error("Behavior list failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.get("/api/intelligence/evolution")
    def get_intelligence_evolution(status: str | None = None, limit: int = 100) -> dict[str, Any]:
        """Discovered-but-unvalidated strategy evolution candidates."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            from nexus_scalp.intelligence.store import load_evolution_candidates

            rows = load_evolution_candidates(engine.audit, status=status, limit=limit)
            return serialize_enums({"available": True, "candidates": rows})
        except Exception as e:
            logger.error("Evolution list failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.post("/api/intelligence/evolution/scan")
    def trigger_evolution_scan() -> dict[str, Any]:
        """Runs a bounded evolution discovery pass; candidates are never live."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            candidates = engine.intelligence_evolution.scan()
            return serialize_enums(
                {"available": True, "candidates": [c.model_dump() for c in candidates]}
            )
        except Exception as e:
            logger.error("Evolution scan failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.post("/api/intelligence/evolution/validate")
    def validate_evolution_candidate(
        candidate_id: str, backtest_expectancy_r: float, backtest_sample_count: int
    ) -> dict[str, Any]:
        """Records a backtest result; a candidate becomes VALIDATED only on positive
        evidence over a sample floor, and even then is never live until promoted."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            candidate = engine.intelligence_evolution.validate_candidate(
                candidate_id=candidate_id,
                backtest_expectancy_r=backtest_expectancy_r,
                backtest_sample_count=backtest_sample_count,
            )
            if candidate is None:
                return {"available": False, "reason": "CANDIDATE_NOT_FOUND"}
            return serialize_enums({"available": True, "candidate": candidate.model_dump()})
        except Exception as e:
            logger.error("Evolution validate failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.post("/api/intelligence/self-heal")
    def trigger_intelligence_self_heal() -> dict[str, Any]:
        """Rebuilds all derived strategy intelligence from the immutable ledger."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            count = engine.rebuild_experience_intelligence()
            return {"available": True, "rebuilt_strategies": int(count)}
        except Exception as e:
            logger.error("Intelligence self-heal failed", error=str(e))
            return {"available": False, "error": str(e)}

    # =========================================================================
    # PHASE 09B: STRATEGY RESEARCH, BACKTEST & VALIDATION ENGINE (read + gates)
    # -------------------------------------------------------------------------
    # Research consumes the immutable experience ledger ONLY. Every endpoint is
    # bounded; validation runs live in the background worker or are triggered
    # explicitly by an operator. Research NEVER places, modifies or closes an
    # order, and a candidate can NEVER become ACTIVE automatically.
    # =========================================================================

    def _research() -> Any:
        """Returns the research pipeline when available."""
        engine = app.state.engine
        if not engine or not hasattr(engine, "research_pipeline"):
            return None
        return engine

    @app.get("/api/research/summary")
    def get_research_summary() -> dict[str, Any]:
        """Candidate count, validation status, lifecycle distribution."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.store import registry_summary

            summary = registry_summary(engine.audit)
            worker = getattr(engine, "research_worker", None)
            if worker is not None:
                from nexus_scalp.research.worker import format_research_worker_status

                summary["worker"] = format_research_worker_status(worker)
            return serialize_enums({"available": True, "summary": summary})
        except Exception as e:
            logger.error("Research summary failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.get("/api/research/registry")
    def get_research_registry(lifecycle: str | None = None, limit: int = 200) -> dict[str, Any]:
        """Bounded registry listing (validation lineage, results, score)."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.store import list_registry

            rows = list_registry(engine.audit, lifecycle=lifecycle, limit=limit)
            return serialize_enums({"available": True, "registry": rows})
        except Exception as e:
            logger.error("Research registry failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.get("/api/research/registry/{strategy_id}")
    def get_research_registry_entry(strategy_id: str) -> dict[str, Any]:
        """Single registry entry for a strategy (latest version)."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.store import get_registry_entry

            row = get_registry_entry(engine.audit, strategy_id)
            if row is None:
                return {"available": False, "reason": "NOT_IN_REGISTRY"}
            return serialize_enums({"available": True, "entry": row})
        except Exception as e:
            logger.error("Research registry entry failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.get("/api/research/runs")
    def get_research_runs(strategy_id: str | None = None, limit: int = 100) -> dict[str, Any]:
        """Append-only validation run records (reproducibility lineage)."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.store import list_research_runs

            rows = list_research_runs(engine.audit, strategy_id=strategy_id, limit=limit)
            return serialize_enums({"available": True, "runs": rows})
        except Exception as e:
            logger.error("Research runs failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.post("/api/research/discover")
    def trigger_research_discovery() -> dict[str, Any]:
        """Builds the dataset + runs bounded candidate discovery.

        Candidates are NEVER live; they enter the validation pipeline only.
        """
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            dataset = engine.research_dataset_builder.build()
            candidates = engine.research_pipeline.discover(dataset)
            return serialize_enums(
                {
                    "available": True,
                    "dataset_id": dataset.dataset_id,
                    "samples": len(dataset.samples),
                    "candidates": [c.model_dump() for c in candidates],
                }
            )
        except Exception as e:
            logger.error("Research discovery failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.post("/api/research/validate")
    def trigger_research_validate(strategy_id: str) -> dict[str, Any]:
        """Runs the full validation gate chain for one candidate by strategy_id.

        Pipeline: backtest -> walk-forward -> OOS -> robustness -> score ->
        registry. The result can be VALIDATED or REJECTED - NEVER ACTIVE.
        """
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            dataset = engine.research_dataset_builder.build()
            candidates = engine.research_pipeline.discover(dataset)
            target = next((c for c in candidates if c.strategy_id == strategy_id), None)
            if target is None:
                # Try the registry: validate the recorded definition.
                entry = engine.strategy_registry.get(strategy_id)
                if entry is None:
                    return {"available": False, "reason": "CANDIDATE_NOT_FOUND"}
                from nexus_scalp.research.candidates import StrategyCandidate

                target = StrategyCandidate(
                    strategy_id=entry.strategy_id,
                    strategy_version=entry.strategy_version,
                    feature_schema_id=entry.feature_schema_id,
                    feature_dimension=entry.feature_dimension,
                    context_definition=entry.context_definition,
                )
            result = engine.research_pipeline.validate_candidate(target, dataset)
            return serialize_enums({"available": True, "result": result})
        except Exception as e:
            logger.error("Research validate failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.post("/api/research/self-heal")
    def trigger_research_self_heal() -> dict[str, Any]:
        """Rebuilds derived research state from the immutable ledger."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.store import self_heal_research

            repaired = self_heal_research(engine.audit, engine.strategy_registry)
            return {"available": True, "repaired": int(repaired)}
        except Exception as e:
            logger.error("Research self-heal failed", error=str(e))
            return {"available": False, "error": str(e)}

    # =========================================================================
    # PHASE 10: CONTROLLED MODEL TRAINING & CHALLENGER ENGINE (read + trigger)
    # -------------------------------------------------------------------------
    # Exposes real model-training state. Training is OFFLINE/BACKGROUND; the
    # production Champion is never touched by candidate training, and a
    # validated Challenger is never auto-promoted.
    # =========================================================================

    def _model_lifecycle() -> Any:
        """Returns the engine when the model-lifecycle subsystem is available."""
        engine = app.state.engine
        if not engine or not hasattr(engine, "model_lifecycle_orchestrator"):
            return None
        return engine

    @app.get("/api/models/summary")
    def get_models_summary() -> dict[str, Any]:
        """Model registry status + training run counts + worker state."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            summary = engine.training_run_store.summary()
            summary["registry"] = engine.model_lifecycle_orchestrator.lifecycle_registry.summary()
            worker = getattr(engine, "training_worker", None)
            if worker is not None:
                from nexus_scalp.model_lifecycle.worker import format_training_worker_status

                summary["worker"] = format_training_worker_status(worker)
            champ = engine.champion_manager.champion_or_none()
            if champ is not None:
                summary["champion"] = champ.summary()
            else:
                summary["champion"] = {"available": False}
            return serialize_enums({"available": True, "summary": summary})
        except Exception as e:
            logger.error("Model summary failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.get("/api/models")
    def get_models_list(status: str | None = None, limit: int = 100) -> dict[str, Any]:
        """Bounded model registry listing (champion/challenger/candidate...)."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            rows = engine.model_lifecycle_orchestrator.lifecycle_registry.list_models(
                status=status, limit=limit
            )
            return serialize_enums({"available": True, "models": rows})
        except Exception as e:
            logger.error("Model list failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.get("/api/models/champion")
    def get_models_champion() -> dict[str, Any]:
        """Current production Champion (metadata + integrity)."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            champ = engine.champion_manager.champion_or_none()
            if champ is None:
                return {"available": True, "champion": {"available": False}}
            return serialize_enums({"available": True, "champion": champ.summary()})
        except Exception as e:
            logger.error("Model champion failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.get("/api/models/challengers")
    def get_models_challengers(limit: int = 50) -> dict[str, Any]:
        """Validated Challengers (shadow-eligible, never production)."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            rows = engine.model_lifecycle_orchestrator.lifecycle_registry.list_models(
                status="CHALLENGER", limit=limit
            )
            return serialize_enums({"available": True, "challengers": rows})
        except Exception as e:
            logger.error("Model challengers failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.get("/api/models/runs")
    def get_models_runs(status: str | None = None, limit: int = 50) -> dict[str, Any]:
        """Append-only training-run records."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            rows = engine.training_run_store.list_runs(status=status, limit=limit)
            return serialize_enums({"available": True, "runs": rows})
        except Exception as e:
            logger.error("Model runs failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.get("/api/models/runs/{run_id}")
    def get_models_run(run_id: str) -> dict[str, Any]:
        """Single training run with gates and artifacts."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            row = engine.training_run_store.get_run(run_id)
            if row is None:
                return {"available": False, "reason": "RUN_NOT_FOUND"}
            return serialize_enums({"available": True, "run": row})
        except Exception as e:
            logger.error("Model run failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.get("/api/models/comparison/{run_id}")
    def get_models_comparison(run_id: str) -> dict[str, Any]:
        """Champion vs Challenger comparison for a training run."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            row = engine.training_run_store.get_comparison(run_id)
            if row is None:
                return {"available": False, "reason": "NO_COMPARISON"}
            return serialize_enums({"available": True, "comparison": row})
        except Exception as e:
            logger.error("Model comparison failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.post("/api/models/train")
    def trigger_model_training(num_epochs: int = 10) -> dict[str, Any]:
        """Runs ONE controlled training pass (candidate only, never Champion).

        Triggers the pipeline synchronously for operator use; the background
        worker handles scheduled training. Heavy CPU work is off the event loop.
        """
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            orchestrator = engine.model_lifecycle_orchestrator
            dataset = orchestrator.build_training_dataset(
                include_no_trade=True, weight_no_trade=0.25, only_executed=True
            )
            if dataset.sample_count < 50:
                return {
                    "available": False,
                    "reason": "INSUFFICIENT_SAMPLES",
                    "samples": dataset.sample_count,
                }
            import asyncio

            result = asyncio.run(_run_training_async(orchestrator, dataset, num_epochs))
            return serialize_enums({"available": True, "result": result})
        except Exception as e:
            logger.error("Model training trigger failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.post("/api/models/worker/start")
    def start_training_worker() -> dict[str, Any]:
        """Starts the background training worker (idempotent, isolated)."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            engine._start_training_worker()
            return {"available": True, "started": engine._training_worker_started}
        except Exception as e:
            logger.error("Training worker start failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.post("/api/models/worker/stop")
    def stop_training_worker() -> dict[str, Any]:
        """Stops the background training worker (idempotent)."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            import asyncio

            asyncio.run(engine._stop_training_worker())
            return {"available": True, "stopped": not engine._training_worker_started}
        except Exception as e:
            logger.error("Training worker stop failed", error=str(e))
            return {"available": False, "error": str(e)}

    @app.post("/api/models/worker/cancel")
    def cancel_training_worker() -> dict[str, Any]:
        """Requests cancellation of any in-flight training (bounded, safe)."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            engine.training_worker.request_cancel()
            return {"available": True}
        except Exception as e:
            logger.error("Training worker cancel failed", error=str(e))
            return {"available": False, "error": str(e)}

    def _intelligence_worker_status(worker: Any) -> dict[str, Any]:
        from nexus_scalp.intelligence.worker import format_intelligence_worker_status

        if worker is None:
            return {}
        try:
            return format_intelligence_worker_status(worker)
        except Exception as e:
            logger.error("Intelligence worker status failed", error=str(e))
            return {}

    # Server-Sent Events (SSE) telemetry stream
    @app.get("/api/ticks/stream")
    async def sse_telemetry_stream(request: Request) -> StreamingResponse:
        """Asynchronous SSE streamer providing zero-latency live telemetry."""

        async def event_generator():
            while True:
                # Client disconnected check
                if await request.is_disconnected():
                    break

                try:
                    payload = get_system_state()
                    yield f"data: {json.dumps(payload)}\n\n"

                    # Also broadcast to active WebSocket clients
                    for ws in list(active_connections):
                        try:
                            await ws.send_json(payload)
                        except Exception:
                            active_connections.discard(ws)
                except Exception as e:
                    logger.error("SSE stream serialization warning", error=str(e))

                # Stream at ~5Hz (0.2s) for snappy real-time visualizer updates
                await asyncio.sleep(0.2)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    return app
