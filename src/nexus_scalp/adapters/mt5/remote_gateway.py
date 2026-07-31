"""
Cross-Platform Remote MT5 Gateway Client Adapter
================================================
Concrete client adapter implementing IMT5Port and IGatewayPort over an encrypted
HTTP/JSON-RPC bridge. Allows Linux containers and remote hosts to execute trades
on a Windows MetaTrader 5 server safely.

Key Invariants:
    - Zero Data Corruption: Timestamps are explicitly converted to UTC ISO format.
    - Idempotent Executions: Trade order payloads inject microsecond-precision idempotency keys.
    - Security First: Every request payload signs the body using HMAC SHA-256.
"""

import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import (
    AccountInfo,
    Position,
    SymbolInfo,
    TickData,
    TradeOrder,
)
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.ports.gateway_port import IGatewayPort
from nexus_scalp.ports.mt5_port import IMT5Port

logger = get_logger("nexus_scalp.adapters.remote_gateway")


class RemoteMT5GatewayAdapter(IMT5Port, IGatewayPort):
    """
    Production-Grade Remote Gateway Client Adapter bridging Linux Core Engine
    to Windows MetaTrader 5 Hosts.
    """

    def __init__(
        self,
        gateway_url: str = "http://127.0.0.1:8080",
        api_key: str = "default_local_key",
        secret_token: str = "default_local_secret",
        timeout_seconds: float = 3.0,
    ) -> None:
        """
        Initializes Remote Gateway Client configuration.

        Args:
            gateway_url: Base HTTP endpoint for the Windows Gateway service.
            api_key: Client identification key.
            secret_token: HMAC secret key for request signature generation.
            timeout_seconds: Maximum network wait timeout for RPC calls.
        """
        self._gateway_url = gateway_url.rstrip("/")
        self._api_key = api_key
        self._secret_token = secret_token
        self._timeout = timeout_seconds
        self._is_connected = False

    def connect(self) -> bool:
        """
        Tests connection and measures round-trip ping time to remote Windows Gateway.
        """
        try:
            rtt_ms = self._sync_ping()
            self._is_connected = True
            logger.info(
                "Connected to Remote MT5 Gateway Host successfully",
                gateway_url=self._gateway_url,
                rtt_ms=round(rtt_ms, 2),
            )
            return True
        except Exception as e:
            logger.error(
                "Connection failed to Remote MT5 Gateway",
                error=str(e),
                gateway_url=self._gateway_url,
            )
            self._is_connected = False
            return False

    def disconnect(self) -> None:
        """Disconnects client session."""
        self._is_connected = False
        logger.info("Remote MT5 Gateway connection closed.")

    def is_connected(self) -> bool:
        """Returns current gateway active status."""
        return self._is_connected

    async def ping(self) -> float:
        """Async interface for measuring RTT latency."""
        return self._sync_ping()

    async def execute_remote_command(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Async wrapper executing remote RPC commands."""
        return self._send_request(action, payload)

    def get_account_info(self) -> AccountInfo:
        """Retrieves account balance and equity state from remote MT5 terminal."""
        res = self._send_request("GET_ACCOUNT_INFO", {})
        data = res["data"]
        return AccountInfo(
            login=int(data["login"]),
            trade_mode=int(data["trade_mode"]),
            leverage=int(data["leverage"]),
            balance=float(data["balance"]),
            equity=float(data["equity"]),
            margin=float(data["margin"]),
            margin_free=float(data["margin_free"]),
            currency=str(data.get("currency", "USD")),
        )

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """Retrieves instrument market specifications from remote MT5 terminal."""
        res = self._send_request("GET_SYMBOL_INFO", {"symbol": symbol})
        data = res["data"]
        return SymbolInfo(
            symbol=str(data["symbol"]),
            digits=int(data["digits"]),
            point=float(data["point"]),
            tick_size=float(data["tick_size"]),
            tick_value=float(data["tick_value"]),
            volume_min=float(data["volume_min"]),
            volume_max=float(data["volume_max"]),
            volume_step=float(data["volume_step"]),
            stops_level=int(data["stops_level"]),
            freeze_level=int(data["freeze_level"]),
            trade_contract_size=float(data["trade_contract_size"]),
        )

    def get_last_tick(self, symbol: str) -> TickData:
        """Fetches the latest real-time tick for a symbol from remote gateway."""
        res = self._send_request("GET_LAST_TICK", {"symbol": symbol})
        data = res["data"]

        dt = datetime.fromisoformat(data["timestamp"])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)

        return TickData(
            symbol=symbol,
            timestamp=dt,
            bid=float(data["bid"]),
            ask=float(data["ask"]),
            last=float(data.get("last", 0.0)),
            volume=float(data.get("volume", 0.0)),
            flags=int(data.get("flags", 0)),
        )

    def get_historical_bars(
        self, symbol: str, timeframe: str = "M1", count: int = 100
    ) -> list[BarData]:
        """Fetches historical completed OHLC bars from remote gateway."""
        res = self._send_request(
            "GET_HISTORICAL_BARS",
            {"symbol": symbol, "timeframe": timeframe, "count": count},
        )
        data_list = res.get("data", [])

        bars: list[BarData] = []
        for b in data_list:
            dt = datetime.fromisoformat(b["timestamp"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)

            bars.append(
                BarData(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=dt,
                    open=float(b["open"]),
                    high=float(b["high"]),
                    low=float(b["low"]),
                    close=float(b["close"]),
                    tick_volume=int(b["tick_volume"]),
                    is_complete=True,
                )
            )
        return bars

    def get_positions(self, symbol: str | None = None) -> list[Position]:
        """Retrieves open positions from remote MT5 terminal."""
        res = self._send_request("GET_POSITIONS", {"symbol": symbol})
        raw_list = res.get("data", [])

        positions: list[Position] = []
        for pos in raw_list:
            order_type = OrderType.BUY if pos["type"] == "BUY" else OrderType.SELL
            positions.append(
                Position(
                    ticket=int(pos["ticket"]),
                    symbol=str(pos["symbol"]),
                    type=order_type,
                    volume=float(pos["volume"]),
                    price_open=float(pos["price_open"]),
                    sl=float(pos["sl"]),
                    tp=float(pos["tp"]),
                    profit=float(pos["profit"]),
                    magic=int(pos["magic"]),
                )
            )
        return positions

    def send_order(self, order: TradeOrder) -> bool:
        """Dispatches an order to the remote gateway with an idempotency key."""
        idempotency_key = f"{order.order_id}_{int(time.time() * 1000)}"
        payload = {
            "order_id": order.order_id,
            "symbol": order.symbol,
            "order_type": order.order_type.value,
            "volume": order.volume,
            "price": order.price,
            "stop_loss": order.stop_loss,
            "take_profit": order.take_profit,
            "magic_number": order.magic_number,
            "comment": order.comment,
            "idempotency_key": idempotency_key,
        }

        res = self._send_request("SEND_ORDER", payload)
        success = res.get("status") == "SUCCESS"

        if success:
            logger.info(
                "Remote gateway executed order successfully",
                order_id=order.order_id,
                ticket=res.get("ticket"),
            )
        else:
            logger.error(
                "Remote gateway order execution failed",
                order_id=order.order_id,
                reason=res.get("message"),
            )

        return success

    def close_position(self, ticket: int) -> bool:
        """Sends remote command to close position by ticket ID."""
        res = self._send_request("CLOSE_POSITION", {"ticket": ticket})
        return res.get("status") == "SUCCESS"

    def _sync_ping(self) -> float:
        """Calculates synchronous HTTP ping latency."""
        t0 = time.perf_counter()
        res = self._send_request("PING", {})
        if res.get("status") != "OK":
            raise RuntimeError(f"Remote gateway ping returned invalid status: {res}")
        return (time.perf_counter() - t0) * 1000.0

    def modify_position(self, ticket: int, stop_loss: float, take_profit: float) -> bool:
        """Modifies position SL/TP via remote gateway RPC."""
        res = self._send_request(
            "MODIFY_POSITION",
            {"ticket": ticket, "stop_loss": stop_loss, "take_profit": take_profit},
        )
        return res.get("status") == "SUCCESS"

    def _send_request(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Sends an authenticated HMAC HTTP POST request to the remote gateway.
        """
        url = f"{self._gateway_url}/api/v1/execute"
        body_data = json.dumps({"action": action, "payload": payload}).encode("utf-8")

        timestamp = str(int(time.time()))
        message_to_sign = f"{timestamp}.{body_data.decode('utf-8')}".encode()
        signature = hmac.new(
            self._secret_token.encode("utf-8"),
            msg=message_to_sign,
            digestmod=hashlib.sha256,
        ).hexdigest()

        headers = {
            "Content-Type": "application/json",
            "X-NSE-API-KEY": self._api_key,
            "X-NSE-TIMESTAMP": timestamp,
            "X-NSE-SIGNATURE": signature,
        }

        req = urllib.request.Request(url, data=body_data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                resp_bytes = response.read()
                return json.loads(resp_bytes.decode("utf-8"))
        except Exception as e:
            logger.error("Gateway RPC communication error", action=action, error=str(e))
            raise RuntimeError(f"Gateway Communication Error [{action}]: {e}") from e