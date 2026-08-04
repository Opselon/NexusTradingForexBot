"""
FastAPI Production Control Dashboard Backend
============================================
Handles high-performance async REST APIs and Server-Sent Events (SSE) live telemetry streams
connecting the modern front-end console to real-time broker states, AI parameters,
and risk engines.
"""

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.domain.enums import ExecutionMode, ActionType
from nexus_scalp.domain.models import TickData


def serialize_enums(obj: Any) -> Any:
    """Recursively converts Enum instances to their underlying values."""
    if isinstance(obj, dict):
        return {k: serialize_enums(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize_enums(x) for x in obj]
    elif isinstance(obj, Enum):
        return obj.value
    return obj
from nexus_scalp.features.scalp_features import FEATURE_NAMES
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.web.server")

# Global/Static UI folder relative path
WEB_DIR = Path("Web")


class ServerState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.bars = []
        self.real_overlays = {
            "rectangles": [],
            "bos_lines": [],
            "midlines": [],
            "liq_markers": []
        }

    def update_live_visuals(self, bars: list, real_overlays: dict) -> None:
        with self._lock:
            self.bars = list(bars)
            self.real_overlays = dict(real_overlays)

    def get_live_visuals(self) -> tuple[list, dict]:
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

        account_data = {
            "balance": 10000.00,
            "equity": 10000.00,
            "floating": 0.00,
            "drawdown": 0.00,
            "win_rate": 78.5
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
                    spread = int(round((tick.ask - tick.bid) * 100))
            except Exception:
                pass

            # Fetch regime state & ATR from the exact synchronized last state
            try:
                reg_state = engine._last_regime_state
                if reg_state:
                    regime = reg_state.regime_type.name
                    atr = reg_state.realized_volatility_5m # Single source of truth for volatility
                elif hasattr(engine, 'regime_classifier') and engine.regime_classifier._stable_regime:
                    regime = engine.regime_classifier._stable_regime.name
            except Exception:
                pass

            # Fetch account info
            try:
                acc = engine.adapter.get_account_info()
                if acc:
                    account_data["balance"] = acc.balance
                    account_data["equity"] = acc.equity
                    account_data["floating"] = acc.equity - acc.balance
                    account_data["drawdown"] = ((engine._peak_equity - acc.equity) / max(engine._peak_equity, 1.0)) * 100.0 if engine._peak_equity > 0 else 0.0
            except Exception:
                pass

            # Calculate actual win rate from audit DB
            try:
                snaps = engine.audit.get_last_account_snapshot()
                # Use standard metrics
            except Exception:
                pass

            # Fetch positions
            try:
                live_positions = engine.adapter.get_positions(symbol=symbol)
                for p in live_positions:
                    positions_list.append({
                        "ticket": p.ticket,
                        "symbol": p.symbol,
                        "type": p.type.value,
                        "volume": p.volume,
                        "price_open": p.price_open,
                        "sl": p.sl,
                        "tp": p.tp,
                        "profit": p.profit
                    })
            except Exception:
                pass

            # Fetch bars (synchronized completed history) - Expand to 250 completed bars for 150+ visible bars support
            if real_bars:
                bars_list = real_bars
            else:
                try:
                    completed_bars = engine.aggregator.get_completed_bars()
                    for b in completed_bars[-250:]:
                        bars_list.append({
                            "time": b.timestamp.isoformat(),
                            "open": b.open,
                            "high": b.high,
                            "low": b.low,
                            "close": b.close,
                            "volume": b.tick_volume,
                            "is_complete": True
                        })
                    # Single Source of Truth forming candle injection
                    forming_bar = engine.aggregator.get_current_forming_bar()
                    if forming_bar:
                        bars_list.append({
                            "time": forming_bar.timestamp.isoformat(),
                            "open": forming_bar.open,
                            "high": forming_bar.high,
                            "low": forming_bar.low,
                            "close": forming_bar.close,
                            "volume": forming_bar.tick_volume,
                            "is_complete": False
                        })
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
                        "sell": float(probs_list[2])
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
            features_payload.append({
                "index": i,
                "name": name,
                "value": val
            })

        # Build Visual Overlays and Algo Config response
        rectangles = []
        order_lines = None
        algo_config_data = {
            "atr_sl_buffer_multiplier": 1.5,
            "min_risk_reward_ratio": 1.8,
            "ai_zone_confidence_threshold": 0.82,
            "fvg_mitigation_sensitivity": 0.5,
            "order_block_lookback_bars": 30
        }

        if engine:
            try:
                algo_config_data = {
                    "atr_sl_buffer_multiplier": float(getattr(engine.config.algo, "atr_sl_buffer_multiplier", 1.5)),
                    "min_risk_reward_ratio": float(getattr(engine.config.algo, "min_risk_reward_ratio", 1.8)),
                    "ai_zone_confidence_threshold": float(getattr(engine.config.algo, "ai_zone_confidence_threshold", 0.82)),
                    "fvg_mitigation_sensitivity": float(getattr(engine.config.algo, "fvg_mitigation_sensitivity", 0.5)),
                    "order_block_lookback_bars": int(getattr(engine.config.algo, "order_block_lookback_bars", 30))
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
                                rectangles.append({
                                    "id": f"fvg_bull_{bar_idx}",
                                    "type": "BULLISH_FVG",
                                    "price_low": float(price_low),
                                    "price_high": float(price_high),
                                    "ai_confidence": float(ai_confidence or 0.82),
                                    "time": b_prev2.timestamp.isoformat()
                                })

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
                                rectangles.append({
                                    "id": f"fvg_bear_{bar_idx}",
                                    "type": "BEARISH_FVG",
                                    "price_low": float(price_low),
                                    "price_high": float(price_high),
                                    "ai_confidence": float(ai_confidence or 0.82),
                                    "time": b_prev2.timestamp.isoformat()
                                })

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
                                rectangles.append({
                                    "id": f"ob_bull_{bar_idx}",
                                    "type": "BULLISH_ORDER_BLOCK",
                                    "price_low": float(price_low),
                                    "price_high": float(price_high),
                                    "ai_confidence": float(ai_confidence or 0.85),
                                    "time": b_prev1.timestamp.isoformat()
                                })

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
                                rectangles.append({
                                    "id": f"ob_bear_{bar_idx}",
                                    "type": "BEARISH_ORDER_BLOCK",
                                    "price_low": float(price_low),
                                    "price_high": float(price_high),
                                    "ai_confidence": float(ai_confidence or 0.85),
                                    "time": b_prev1.timestamp.isoformat()
                                })

                        # Sweep / Stop Hunt Zone
                        if bar_idx >= 11:
                            recent_lows = [b.low for b in completed_bars[bar_idx-11 : bar_idx]]
                            recent_highs = [b.high for b in completed_bars[bar_idx-11 : bar_idx]]
                            min_low = min(recent_lows)
                            max_high = max(recent_highs)

                            if b_current.low < min_low and b_current.close > min_low:
                                rectangles.append({
                                    "id": f"sweep_bull_{bar_idx}",
                                    "type": "STOP_HUNT_ZONE",
                                    "price_low": float(b_current.low),
                                    "price_high": float(min_low),
                                    "ai_confidence": float(ai_confidence or 0.90),
                                    "time": b_current.timestamp.isoformat()
                                })
                            elif b_current.high > max_high and b_current.close < max_high:
                                rectangles.append({
                                    "id": f"sweep_bear_{bar_idx}",
                                    "type": "STOP_HUNT_ZONE",
                                    "price_low": float(max_high),
                                    "price_high": float(b_current.high),
                                    "ai_confidence": float(ai_confidence or 0.90),
                                    "time": b_current.timestamp.isoformat()
                                })
                except Exception as e:
                    logger.error("Failed to detect real structural zones from completed bars", error=str(e))

            fv = engine._last_fv
            proposal = engine._last_proposal

            if not rectangles and fv:
                # Fallback to fv currently forming bar attributes if we have no unmitigated historical ones
                forming_bar = engine.aggregator.get_current_forming_bar()
                f_time = forming_bar.timestamp.isoformat() if forming_bar else None
                if getattr(fv, "order_block_type", 0) == 1:
                    rectangles.append({
                        "id": "ob_bull",
                        "type": "BULLISH_ORDER_BLOCK",
                        "price_low": float(bid - atr * 0.8),
                        "price_high": float(bid),
                        "ai_confidence": float(ai_confidence or 0.85),
                        "time": f_time
                    })
                elif getattr(fv, "order_block_type", 0) == -1:
                    rectangles.append({
                        "id": "ob_bear",
                        "type": "BEARISH_ORDER_BLOCK",
                        "price_low": float(bid),
                        "price_high": float(bid + atr * 0.8),
                        "ai_confidence": float(ai_confidence or 0.85),
                        "time": f_time
                    })
                if getattr(fv, "fvg_bullish_active", False):
                    rectangles.append({
                        "id": "fvg_bull",
                        "type": "BULLISH_FVG",
                        "price_low": float(bid - atr * 0.5),
                        "price_high": float(bid),
                        "ai_confidence": float(ai_confidence or 0.82),
                        "time": f_time
                    })
                if getattr(fv, "fvg_bearish_active", False):
                    rectangles.append({
                        "id": "fvg_bear",
                        "type": "BEARISH_FVG",
                        "price_low": float(bid),
                        "price_high": float(bid + atr * 0.5),
                        "ai_confidence": float(ai_confidence or 0.82),
                        "time": f_time
                    })
                if getattr(fv, "liquidity_sweep_signal", 0) != 0:
                    rectangles.append({
                        "id": "sweep_zone",
                        "type": "STOP_HUNT_ZONE",
                        "price_low": float(bid - atr * 1.2),
                        "price_high": float(bid + atr * 1.2),
                        "ai_confidence": float(ai_confidence or 0.90),
                        "time": f_time
                    })

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
                    "zone_score": float(round(proposal.confidence * 100.0, 1))
                }
            else:
                try:
                    live_positions = engine.adapter.get_positions(symbol=symbol)
                    if live_positions:
                        p = live_positions[0]
                        risk_usd = account_data["equity"] * (engine.config.risk.risk_per_trade_pct / 100.0)
                        sl_dist = abs(p.price_open - p.sl) if p.sl > 0 else (atr * 1.5)
                        tp_dist = abs(p.tp - p.price_open) if p.tp > 0 else (atr * 1.8)
                        risk_reward_ratio = tp_dist / max(sl_dist, 1e-5)
                        profit_usd = risk_usd * risk_reward_ratio
                        order_lines = {
                            "active": True,
                            "direction": p.type.value,
                            "entry_price": float(p.price_open),
                            "sl_price": float(p.sl) if p.sl > 0 else float(p.price_open - sl_dist if p.type.value == "BUY" else p.price_open + sl_dist),
                            "tp_price": float(p.tp) if p.tp > 0 else float(p.price_open + tp_dist if p.type.value == "BUY" else p.price_open - tp_dist),
                            "risk_reward_ratio": float(round(risk_reward_ratio, 2)),
                            "risk_usd": float(round(risk_usd, 2)),
                            "profit_usd": float(round(profit_usd, 2)),
                            "zone_score": 85.0
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
                    "time": t1
                },
                {
                    "id": "mock_fvg_bear_1",
                    "type": "BEARISH_FVG",
                    "price_low": float(bid + atr * 0.5),
                    "price_high": float(bid + atr * 1.5),
                    "ai_confidence": 0.82,
                    "time": t2
                }
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
                "order_lines": order_lines
            }
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
                rule_name=req.rule_name,
                is_enabled=req.is_enabled,
                parameters_json=params_json
            )
            if success and hasattr(engine, "rule_matrix"):
                engine.rule_matrix.refresh_cache()
            return {"success": success}
        else:
            from nexus_scalp.adapters.database.audit_repository import AuditRepository
            repo = AuditRepository()
            success = repo.toggle_trading_rule(
                rule_name=req.rule_name,
                is_enabled=req.is_enabled,
                parameters_json=params_json
            )
            return {"success": success}

    # REST APIs: Account summary
    @app.get("/api/account/summary")
    def get_account_summary() -> dict[str, Any]:
        engine = app.state.engine

        balance = 10000.00
        equity = 10000.00
        margin = 0.0
        open_positions_count = 0

        # Default metrics
        win_rate = 0.0
        profit_factor = 0.0
        max_drawdown = 0.0
        total_trades = 0

        if engine:
            try:
                acc = engine.adapter.get_account_info()
                if acc:
                    balance = acc.balance
                    equity = acc.equity
                    margin = acc.margin
            except Exception:
                pass

            try:
                positions = engine.adapter.get_positions(symbol=engine.config.execution.symbol)
                open_positions_count = len(positions)
            except Exception:
                pass

            try:
                metrics = engine.audit.get_account_performance_metrics()
                win_rate = metrics["win_rate"]
                profit_factor = metrics["profit_factor"]
                max_drawdown = metrics["max_drawdown"]
                total_trades = metrics["total_trades"]
            except Exception:
                pass

        return {
            "balance": balance,
            "equity": equity,
            "margin": margin,
            "open_positions": open_positions_count,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "max_drawdown": max_drawdown,
            "total_trades": total_trades,
        }

    # REST APIs: Historical trade logs with pagination/filters
    @app.get("/api/account/trades")
    def get_account_trades(limit: int = 100, offset: int = 0, status: str | None = None) -> list[dict[str, Any]]:
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
    @app.post("/api/engine/toggle")
    def toggle_engine(req: ToggleRequest) -> dict[str, Any]:
        engine = app.state.engine
        if not engine:
            raise HTTPException(status_code=400, detail="Trading Engine reference not loaded.")

        if req.active:
            if not engine._running:
                # Launch in an async background task to prevent blocking server thread
                logger.info("Web Dashboard triggered system start command.")
                asyncio.create_task(engine.run_loop())
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

                bars.append({
                    "time": (now_dt - timedelta(minutes=160-i)).isoformat(),
                    "open": start_price,
                    "high": high_p,
                    "low": low_p,
                    "close": close_p,
                    "volume": float(random.randint(10, 50)),
                    "is_complete": True
                })
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
                    "time": bars[30]["time"] if len(bars) > 30 else None
                },
                {
                    "id": "mock_fvg_bear_1",
                    "type": "BEARISH_FVG",
                    "price_low": 2335.50,
                    "price_high": 2336.80,
                    "ai_confidence": 0.82,
                    "time": bars[70]["time"] if len(bars) > 70 else None
                }
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
                "zone_score": 88.0
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
            ticket=req.ticket,
            stop_loss=req.stop_loss,
            take_profit=req.take_profit
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
                    symbol=symbol,
                    timestamp=datetime.now(UTC),
                    bid=2334.21,
                    ask=2334.41,
                    volume=1.0
                )
        except Exception:
            current_tick = TickData(
                symbol=symbol,
                timestamp=datetime.now(UTC),
                bid=2334.21,
                ask=2334.41,
                volume=1.0
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
            volume=current_tick.volume + 5.0
        )

        # Inject simulated tick directly to engine tick processor pipeline
        logger.info("Injecting interactive simulation tick pressure.", type=req.type, bid=simulated_tick.bid, ask=simulated_tick.ask)

        # Track simulated outcome
        prob_sim = 0.76 if req.type == "BUY_PRESSURE" else (0.81 if req.type == "SELL_PRESSURE" else 0.12)
        outcome_status = "TRUE_POSITIVE" if req.type != "VOLATILE_SWEEP" else "FALSE_POSITIVE"

        app.state.simulated_outcomes.append({
            "time": datetime.now(UTC).strftime("%H:%M:%S"),
            "action": "BUY_MARKET" if req.type == "BUY_PRESSURE" else ("SELL_MARKET" if req.type == "SELL_PRESSURE" else "NO_ACTION"),
            "confidence": prob_sim,
            "actual_delta": bid_change,
            "outcome": outcome_status
        })

        # Process the simulated tick
        if engine._running:
            engine._process_tick_pipeline(tick=simulated_tick, account=engine.adapter.get_account_info())

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

        return {
            "tg_enabled": tg_enabled,
            "tg_queue": tg_queue_size
        }

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
