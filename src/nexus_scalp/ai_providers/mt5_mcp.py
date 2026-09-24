"""MetaTrader 5 MCP client for replay/backtest (ECOSYSTEM-001, Section 45).

A minimal, session-aware MCP (JSON-RPC over HTTP) client. Read-only for NSE's
purposes: replay uses historical chart bars, never an order tool.

MCP is a session protocol, so a client MUST:

    initialize -> notifications/initialized -> tools/call

and echo the ``Mcp-Session-Id`` response header on every later request; a bare
``tools/call`` without the handshake is rejected (that was the first thing the
forensic probe found on this endpoint).

Endpoint and key are CONFIGURABLE and never hardcoded (Sections 4, 37): they are
read from the secure secret store, not from source.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from nexus_scalp.ai_providers.errors import (
    ProviderError,
    ProviderErrorCategory,
)

PROTOCOL_VERSION = "2024-11-05"
DEFAULT_MCP_URL = "http://127.0.0.1:22346/mcp"
SECRET_NAME_MT5_KEY = "mt5_mcp_api_key"


class MT5MCPClient:
    """Session-aware MCP client bound to one terminal endpoint."""

    def __init__(
        self,
        endpoint: str = DEFAULT_MCP_URL,
        api_key: str | None = None,
        timeout: float = 30.0,
        retries: int = 2,
    ) -> None:
        self.endpoint = endpoint
        self._api_key = api_key
        self.timeout = timeout
        self.retries = retries
        self._session: str | None = None
        self._rpc_id = 0
        self._handshaken = False

    # -- wire ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._api_key:
            # Sent verbatim to the terminal only; never logged or persisted.
            headers["Authorization"] = f"Bearer {self._api_key}"
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        return headers

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        """Send one request; empty 202 bodies (notifications) parse as ``{}``."""
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            self.endpoint, data=data, headers=self._headers(), method="POST"
        )
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    sid = resp.headers.get("Mcp-Session-Id")
                    if sid:
                        self._session = sid
                    raw = resp.read().decode()
                if not raw.strip():
                    # HTTP 202 to a notification: no response object exists.
                    return {}
                return _parse_body(raw)
            except urllib.error.HTTPError as exc:
                detail = _safe_read(exc)
                # 401/403 are credential problems: retrying cannot help.
                if exc.code in (401, 403):
                    raise ProviderError(
                        ProviderErrorCategory.AUTH_FAILED,
                        f"MT5 MCP rejected credentials (HTTP {exc.code}): {detail}",
                    ) from exc
                if exc.code == 429:
                    raise ProviderError(
                        ProviderErrorCategory.RATE_LIMITED, "MT5 MCP rate limited"
                    ) from exc
                last = exc
                if attempt < self.retries:
                    time.sleep(0.25 * (2**attempt))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
                if attempt < self.retries:
                    time.sleep(0.25 * (2**attempt))
        raise ProviderError(
            ProviderErrorCategory.NETWORK,
            f"MT5 MCP unreachable: {type(last).__name__}: {last}",
        )

    def rpc(
        self, method: str, params: dict[str, Any] | None = None, notify: bool = False
    ) -> dict[str, Any]:
        self._rpc_id += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            body["id"] = self._rpc_id
        if notify:
            # A JSON-RPC notification has no id and no response body: the server
            # answers HTTP 202 with an empty payload. Parsing that as JSON was a
            # real bug found by the live probe of this endpoint.
            self._post(body)
            return {}
        return self._post(body)

    def handshake(self) -> dict[str, Any]:
        """initialize + initialized. Required before any tools/call."""
        info = self.rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "nse-ai-provider-replay", "version": "1.0"},
            },
        )
        self.rpc("notifications/initialized", {}, notify=True)
        self._handshaken = True
        return info

    def list_tools(self) -> list[dict[str, Any]]:
        if not self._handshaken:
            self.handshake()
        return self.rpc("tools/list", {}).get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke one MCP tool and return its structured content."""
        if not self._handshaken:
            self.handshake()
        result = self.rpc("tools/call", {"name": name, "arguments": arguments}).get("result", {})
        if result.get("isError"):
            raise ProviderError(
                ProviderErrorCategory.UPSTREAM_UNAVAILABLE,
                f"MT5 MCP tool {name} failed: {_flatten(result)[:400]}",
            )
        return _content_to_obj(result)

    def health(self) -> dict[str, Any]:
        """Cheap liveness probe: handshake + tool list, no trading side effect."""
        start = time.perf_counter()
        try:
            tools = self.list_tools()
            return {
                "available": True,
                "tool_count": len(tools),
                "latency_ms": round((time.perf_counter() - start) * 1000, 1),
                "error": None,
            }
        except ProviderError as exc:
            return {
                "available": False,
                "tool_count": 0,
                "latency_ms": round((time.perf_counter() - start) * 1000, 1),
                "error": f"{exc.category.value}: {exc}",
            }

    # -- replay data -----------------------------------------------------------

    def chart_history(
        self,
        symbol: str,
        period: str,
        datetime_from: str,
        datetime_to: str,
        limit: int = 100000,
    ) -> list[dict[str, Any]]:
        """Historical OHLCV bars for replay. READ-ONLY (never an order tool)."""
        payload = self.call_tool(
            "get_chart_history",
            {
                "symbol": symbol,
                "period": period,
                "datetime_from": datetime_from,
                "datetime_to": datetime_to,
                "limit": limit,
            },
        )
        rows = payload.get("history") or payload.get("bars") or payload.get("data") or []
        return [_normalise_bar(r) for r in rows if isinstance(r, dict)]

    def account_info(self) -> dict[str, Any]:
        """Broker account snapshot. The broker remains the source of truth
        (Section 51): replay never lets an AI provider author these values."""
        payload = self.call_tool("get_trading_account_info", {})
        return payload if isinstance(payload, dict) else {"raw": payload}

    def open_positions(self) -> list[dict[str, Any]]:
        payload = self.call_tool("get_trading_open_positions", {})
        rows = payload if isinstance(payload, list) else payload.get("positions") or []
        return [r for r in rows if isinstance(r, dict)]


# -- helpers ------------------------------------------------------------------


def _parse_body(raw: str) -> dict[str, Any]:
    """MCP servers may answer plain JSON or an SSE stream."""
    text = raw.strip()
    if text.startswith("{"):
        return json.loads(text)
    for line in raw.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise ProviderError(ProviderErrorCategory.SCHEMA_VIOLATION, "unparseable MCP response")


def _safe_read(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode(errors="replace")[:300]
    except Exception:
        return ""


def _content_to_obj(result: dict[str, Any]) -> Any:
    contents = result.get("content") or []
    texts = [c.get("text", "") for c in contents if isinstance(c, dict) and c.get("type") == "text"]
    if not texts:
        return result.get("structuredContent", result)
    joined = "\n".join(texts)
    try:
        return json.loads(joined)
    except json.JSONDecodeError:
        return joined


def _flatten(result: dict[str, Any]) -> str:
    try:
        return json.dumps(result, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return str(result)


def _normalise_bar(row: dict[str, Any]) -> dict[str, Any]:
    """Map MCP bar keys onto one stable shape for the replay engine."""

    def pick(*names: str) -> Any:
        for n in names:
            if n in row:
                return row[n]
        return None

    time_val = pick("time", "datetime", "timestamp", "date")
    return {
        "time": str(time_val) if time_val is not None else None,
        "open": _num(pick("open")),
        "high": _num(pick("high")),
        "low": _num(pick("low")),
        "close": _num(pick("close")),
        "tick_volume": _num(pick("tick_volume", "volume")),
    }


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
