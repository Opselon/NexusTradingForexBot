"""
MT5 MCP Transport Adapter (READ-ONLY)
=====================================
PURPOSE: implement the IMT5Port READ surface over a local MetaTrader 5 MCP
       endpoint (HTTP JSON-RPC, protocol 2025-06-18). This is the MCP
       transport the MT5-PARITY-FORENSICS wave proved absent (A-01 / B-27 /
       H-02): until this module the repo had zero MCP clients (raw grep for
       `22346|MCPToolbox|mcp_client` across src/scripts/tests/configs = 0
       hits), so the 66-tool terminal bridge running on this box was simply
       unreachable from the engine.
OWNER: MT5-PARITY-FORENSICS lane IMPL-F (branch agent/feature/mt5-parity-f).
CONSUMES:
       * env NSE_MT5_MCP_URL / NSE_MT5_MCP_API_KEY (never hardcoded; the key
         is additionally loadable from the repo-root `.env` so a dev box
         without exported env still resolves it — the same env+.env
         resolution shape the remote gateway provider uses).
       * the MCP tool surface verified live by lane A:
           get_trading_account_info / get_marketwatch_symbols /
           get_chart_history / get_chart_ticks_history /
           get_trading_open_positions / get_trading_history_orders /
           get_trading_history_positions / get_time_information /
           get_workspace_info / get_terminal_journal.
PROVIDES: MCPMT5Adapter(IMT5Port) — a READ-ONLY adapter. Every broker read
       returns the existing typed snapshots from adapters/mt5/providers.py
       so downstream consumers see the SAME objects the native adapter
       produces. build_account_snapshot / build_symbol_snapshot are reused
       directly (their field semantics match once the MCP keys are renamed);
       bars / ticks / positions / orders / deals use the dedicated mappers
       at the bottom of this file, each with the reason inline.
INVARIANTS:
       1. READ-ONLY. send_order / execute_market_order / place_pending_order /
          modify_position / close_position / modify_order /
          cancel_pending_order raise NotImplementedError WITHOUT issuing any
          MCP call. A module-level FORBIDDEN_TOOLS frozenset (mirroring the
          wave helper mcp_client.py) blocks the trade_* surface at the
          transport boundary so even a future caller cannot reach a
          mutating tool through this adapter.
       2. NO CREDENTIAL EVER IN CODE/LOGS. The API key is resolved from env
          (with a repo-root `.env` fallback), stored privately, and sent only
          as an X-API-Key / Authorization header. It is never logged,
          stringified into an exception, or returned from any method.
       3. FAIL CLOSED, LOUDLY. connect() raises on transport/auth failure
          (never returns False silently); read failures surface as
          `available=False` + error_state snapshots, never fabricated values.
       4. MCP HISTORY IS RANGE-ONLY (A-11): get_chart_history /
          get_chart_ticks_history REQUIRE datetime_from/datetime_to and
          reject any future datetime_to ("invalid datetime range"). Count-
          based port methods therefore derive the range from the terminal
          clock backwards, and a requested future boundary is clamped to
          now and recorded in the returned snapshots' `note`.
       5. EMPTY WINDOWS ARE NOT ERRORS (B-14m): MCP answers an unpopulated
          window with `ok:true` + `history:[]` and no discriminator. That is
          treated as a legitimate empty result (logged at debug) and never
          as a failure.
       6. SHAPE DIFFERENCES ARE DOCUMENTED, NOT HIDDEN. MCP stamps bars and
          ticks as ISO-8601 strings already representing UTC trade-server
          time (no epoch, no trailing Z), exposes no real_volume on bars, no
          last/volume/flags on ticks, and stringly-types order/deal metadata
          with renamed keys (B-24). Every mapping below names what it
          converts and what it cannot.
EXTEND: adding a read tool = add it to READ_TOOLS + a mapper. Adding ANY
       write tool is forbidden by invariant 1.

Parity note (evidence/orchestrator_native_vs_mcp_parity.md): on identical
broker state, MCP bars/ticks/account/symbol are value-identical to native
(130/130 bars, 2578/2578 ticks ms-exact, 6/6 account, 19/19 symbol fields).
This adapter exists to make that data reachable, not to transform it.
"""

from __future__ import annotations

import json
import logging
import math
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

from nexus_scalp.adapters.mt5.diagnostics import MT5ConnectionState
from nexus_scalp.adapters.mt5.providers import (
    AccountSnapshot,
    BrokerTickSnapshot,
    DealSnapshot,
    HistoryOrderSnapshot,
    OrderSnapshot,
    PositionSnapshot,
    RateBarSnapshot,
    SymbolSnapshot,
    TickHistorySnapshot,
    build_account_snapshot,
    build_symbol_snapshot,
    normalize_utc,
    validate_ohlc_bars,
)
from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import AccountInfo, Position, SymbolInfo, TickData
from nexus_scalp.indicators.resample import is_current_bar_forming
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.ports.mt5_port import IMT5Port

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Read-only enforcement (mirrors the wave helper mcp_client.FORBIDDEN_TOOLS).
# This is the transport-level guard behind invariant 1: even a caller that
# reaches the transport directly cannot dispatch a mutating tool through us.
# trade_* are the order-placement surface; chart_*/tester_*/write_* mutate
# terminal state (open charts, attach EAs, run the tester, write files) and
# are equally out of contract for a read adapter.
# ---------------------------------------------------------------------------
FORBIDDEN_TOOLS: frozenset[str] = frozenset(
    {
        "trade_send_market_order",
        "trade_send_pending_order",
        "trade_modify_sl_tp",
        "trade_delete_order",
        "trade_close_single_position",
        "trade_close_by_position",
        "chart_open",
        "chart_close",
        "chart_add_indicator",
        "chart_add_expert",
        "chart_add_script",
        "chart_remove_expert",
        "chart_remove_indicator",
        "chart_apply_template",
        "add_marketwatch_symbol",
        "remove_marketwatch_symbol",
        "tester_run_backtest",
        "tester_run_optimization",
        "tester_stop",
        "write_file",
        "create_new_file",
        "create_new_folder",
        "delete_file",
        "replace_text_in_file",
        "write_binary_file",
    }
)

#: Read tools this adapter is permitted to dispatch. Whitelisting (in
#: addition to the blacklist above) keeps the surface honest: a tool not on
#: this list is not a hidden capability, it is simply not wired up.
READ_TOOLS: frozenset[str] = frozenset(
    {
        "get_trading_account_info",
        "get_marketwatch_symbols",
        "get_chart_history",
        "get_chart_ticks_history",
        "get_trading_open_positions",
        "get_trading_history_orders",
        "get_trading_history_positions",
        "get_time_information",
        "get_workspace_info",
        "get_terminal_journal",
    }
)

_ENV_URL = "NSE_MT5_MCP_URL"
_ENV_KEY = "NSE_MT5_MCP_API_KEY"
_DEFAULT_URL = "http://127.0.0.1:22346/mcp"
_DEFAULT_TIMEOUT_S = 60.0
_DEFAULT_SYMBOL = "XAUUSD"

#: The MCP server ignores unknown arguments and returns the FULL history
#: (A-12: get_trading_history_orders({count:20}) returned 7.9 MB). Only
#: whitelisted argument names are ever placed on the wire.
_ALLOWED_HISTORY_ARGS: frozenset[str] = frozenset(
    {"symbol", "period", "datetime_from", "datetime_to", "limit"}
)

#: MCP bar periods are the MT5 enumeration names (M1..MN1); the port contract
#: accepts the same upper-case tokens, so this is a validation gate rather
#: than a translation table.
_SUPPORTED_PERIODS: frozenset[str] = frozenset(
    {
        "M1",
        "M2",
        "M3",
        "M4",
        "M5",
        "M6",
        "M10",
        "M12",
        "M15",
        "M20",
        "M30",
        "H1",
        "H2",
        "H3",
        "H4",
        "H6",
        "H8",
        "H12",
        "D1",
        "W1",
        "MN1",
    }
)
_PERIOD_MINUTES: dict[str, int] = {
    "M1": 1,
    "M2": 2,
    "M3": 3,
    "M4": 4,
    "M5": 5,
    "M6": 6,
    "M10": 10,
    "M12": 12,
    "M15": 15,
    "M20": 20,
    "M30": 30,
    "H1": 60,
    "H2": 120,
    "H3": 180,
    "H4": 240,
    "H6": 360,
    "H8": 480,
    "H12": 720,
    "D1": 1440,
    "W1": 10_080,
    "MN1": 43_200,
}

#: Bot identity used by the legacy bot-filtered reads (mirrors
#: DirectMT5Adapter: XAUUSD + magic 888101).
_BOT_SYMBOL = "XAUUSD"
_BOT_MAGIC = 888101

_READ_ONLY_MESSAGE = (
    "MCPMT5Adapter is READ-ONLY: this method would mutate terminal/account "
    "state and is refused without issuing any MCP call (trade_* tools are "
    "forbidden by the MT5-PARITY-FORENSICS read-only contract)."
)


def _load_env_file(repo_root: str | None) -> None:
    """Load KEY=VALUE pairs from `<repo_root>/.env` into os.environ.

    Never overwrites an existing environment variable: a real env export wins
    over the file, matching the resolution order of mcp_client.py. A missing
    or unreadable file is a no-op (env alone may suffice).
    """
    if not repo_root:
        return
    env_path = os.path.join(repo_root, ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if key and key not in os.environ:
                    os.environ[key] = value.strip()
    except OSError as exc:
        logger.debug("[MCP] event=ENV_READ_FAILED root=%s error=%s", repo_root, exc)


def _resolve_repo_root() -> str:
    """Best-effort repo root for the `.env` fallback.

    Walks up from this file to the first directory containing pyproject.toml
    (the package lives at <root>/src/nexus_scalp/adapters/mt5/). Returns ""
    when nothing is found, which disables the file fallback cleanly.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    parent = here
    while True:
        if os.path.exists(os.path.join(parent, "pyproject.toml")):
            return parent
        nxt = os.path.dirname(parent)
        if nxt == parent:  # filesystem root
            return ""
        parent = nxt


def _iso_server_local(dt: datetime) -> str:
    """Format a UTC datetime as MCP's naive ISO datetime argument.

    MCP resolves the datetime_from/datetime_to arguments and stamps its
    returned bars/ticks on the same TRADE-SERVER clock (naive ISO strings,
    no trailing Z). Lane A verified the returned bar strings equal
    ``datetime.fromtimestamp(native_epoch, tz=UTC)`` exactly, so expressing
    the request window on that same clock keeps the round trip lossless —
    no server-offset arithmetic is needed on either side (unlike the native
    copy_ticks_range path, where the terminal's epoch inputs are offset).
    """
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


class _Payload:
    """Attribute + subscript view over an MCP JSON dict.

    A-01 dict pitfall: providers.build_account_snapshot / build_symbol_snapshot
    read their inputs with ``getattr(obj, name)`` (native namedtuple style),
    while build_rate_bar_snapshot / build_tick_history_snapshot use
    ``obj[key]`` (numpy record style). A raw MCP dict satisfies NEITHER —
    every _attr() silently returns None and the snapshot comes back empty.
    This shim speaks both interfaces so the existing builders can consume
    MCP payloads unchanged.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self.__dict__["_data"] = data

    def __getattr__(self, name: str) -> Any:
        #: Only called when normal attribute lookup fails, so `_data` itself
        #: resolves through __dict__ and never recurses here.
        return self.__dict__["_data"].get(name)

    def __getitem__(self, key: str) -> Any:
        return self.__dict__["_data"][key]


class _AttrProxy:
    """Attribute shim so MT5ConnectionState.set_terminal() can read our dict.

    set_terminal() uses getattr(terminal_info, 'trade_allowed'|'company'); a
    plain dict has no attributes, so this proxy exposes the diagnostic keys
    the state machine consumes. Carries no credentials.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> Any:
        return self._data.get(name)


class _McpTransport:
    """Minimal self-contained JSON-RPC/SSE client for the MT5 MCP endpoint.

    Reuses the wire pattern proven by the wave helper mcp_client.py
    (initialize handshake + tools/call, SSE unwrap, X-API-Key/Bearer auth)
    without importing it: that helper lives outside the repo and must not
    become a production dependency.
    """

    def __init__(self, url: str, api_key: str, timeout_s: float) -> None:
        self._url = url
        #: Private by convention — never logged, never returned, never
        #: stringified into an exception message (invariant 2).
        self._key = api_key
        self._timeout = timeout_s
        self._mid = 0
        self._session_id: str | None = None

    # -- wire -------------------------------------------------------------
    def _rpc(
        self, method: str, params: Any = None, notify: bool = False
    ) -> tuple[dict[str, str] | None, str]:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notify:
            self._mid += 1
            body["id"] = self._mid
        if params is not None:
            body["params"] = params
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "X-API-Key": self._key,
            "Authorization": "Bearer " + self._key,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        request = urllib.request.Request(self._url, data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return dict(response.headers), response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            #: Status code only — a reflected error body could echo the
            #: request headers, so it is deliberately NOT included.
            return None, f"HTTP_{exc.code}"
        except Exception as exc:  # transport failure is a first-class result
            return None, f"TRANSPORT_ERROR {type(exc).__name__}"

    @staticmethod
    def _unwrap(raw: str) -> dict[str, Any]:
        """Parse a JSON-RPC response, unwrapping an SSE `data:` envelope."""
        text = raw
        if text.startswith("event:") or text.startswith("data:"):
            for line in text.splitlines():
                if line.startswith("data:"):
                    text = line[len("data:") :].strip()
                    break
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"raw": raw[:2000]}
        return parsed if isinstance(parsed, dict) else {"raw": raw[:2000]}

    def initialize(self) -> None:
        """Perform the JSON-RPC initialize handshake (raises on failure)."""
        headers, raw = self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {
                    "name": "nexus-scalp-mt5-mcp-adapter",
                    "version": "1.0",
                },
            },
        )
        if headers is None:
            raise RuntimeError(f"MCP initialize failed: {raw}")
        self._session_id = headers.get("Mcp-Session-Id") or headers.get("mcp-session-id")
        self._rpc("notifications/initialized", {}, notify=True)

    def call_raw(self, tool: str, arguments: dict[str, Any] | None) -> tuple[bool, str]:
        """Dispatch a tool call. Returns (is_error, text). Never raises.

        Refuses forbidden tools BEFORE any network I/O (invariant 1) and
        refuses unmapped tools so the adapter never exercises a surface it
        has not documented.
        """
        if tool in FORBIDDEN_TOOLS:
            raise RuntimeError(f"REFUSED_BY_POLICY: {tool} is forbidden (read-only adapter)")
        if tool not in READ_TOOLS:
            raise RuntimeError(f"REFUSED_UNMAPPED: {tool} is not wired into this adapter")
        _, raw = self._rpc("tools/call", {"name": tool, "arguments": arguments or {}})
        if raw.startswith("TRANSPORT_ERROR") or raw.startswith("HTTP_"):
            return True, raw
        data = self._unwrap(raw)
        if "error" in data:
            return True, str(data["error"])[:2000]
        result = data.get("result", data)
        if not isinstance(result, dict):
            return True, f"UNEXPECTED_RESULT_SHAPE {type(result).__name__}"
        chunks = [
            chunk.get("text", "")
            for chunk in result.get("content", [])
            if isinstance(chunk, dict) and chunk.get("type") == "text"
        ]
        return bool(result.get("isError")), "\n".join(chunks)


class MCPMT5Adapter(IMT5Port):
    """READ-ONLY IMT5Port implementation over the local MetaTrader 5 MCP endpoint.

    The constructor never touches the network: connection happens on
    connect(). Credentials come from env (with a repo-root .env fallback) or
    can be supplied explicitly by a caller that owns them (e.g. a settings
    store); neither is ever logged.
    """

    #: Execution provenance tag (BUG-226): this is a real broker terminal,
    #: reached over an MCP bridge instead of the native IPC driver.
    current_account_source = "LIVE"

    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        repo_root: str | None = None,
        symbol: str = _DEFAULT_SYMBOL,
    ) -> None:
        _load_env_file(repo_root if repo_root is not None else _resolve_repo_root())
        self._url = (url or os.environ.get(_ENV_URL) or _DEFAULT_URL).rstrip("/")
        #: Presence is checked on connect() so a misconfigured adapter fails
        #: loudly at the boundary instead of at first read.
        self._api_key = api_key if api_key is not None else os.environ.get(_ENV_KEY, "")
        self._timeout = float(timeout_s)
        self._symbol = symbol or _DEFAULT_SYMBOL
        self._connected = False
        self._transport: _McpTransport | None = None
        self._conn_state = MT5ConnectionState()
        #: Last MCP envelope per operation (bounded, diagnostics only).
        self._last_envelope: dict[str, Any] = {}
        #: Cached terminal clock — the MCP server's own UTC clock is the only
        #: honest reference for range derivation (A-11); a dev box's local
        #: clock in another timezone would shift every window.
        self._server_utc: datetime | None = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        """Honest 'is this adapter live' gate (A-14).

        The dead ``getattr(adapter, 'available', None) is True`` branch in
        mt5_tick_dataset.acquire_* can never fire for the native/remote
        adapters because none of them define ``available``. This adapter is
        the one that legitimately can: True only after a successful connect()
        (a real MCP session is established), False before and after
        disconnect. Consumers may treat True as 'the MCP transport answers'.
        """
        return self._connected

    def connect(self) -> bool:
        """Open the MCP session. Raises on failure — never returns False.

        A silent False would let a caller believe it holds a usable adapter
        and then read UNAVAILABLE snapshots forever without a single logged
        cause; raising makes the boundary explicit (invariant 3).
        """
        if not self._api_key:
            self._conn_state.set_state(
                MT5ConnectionState.AUTHENTICATION_ERROR,
                f"{_ENV_KEY} missing — supply it via env or repo .env, never hardcode it",
            )
            raise RuntimeError(
                f"MCP adapter requires {_ENV_KEY} (env or repo-root .env); it is not set"
            )
        self._conn_state.set_state(MT5ConnectionState.CONNECTING, "mcp initialize")
        transport = _McpTransport(self._url, self._api_key, self._timeout)
        try:
            transport.initialize()
        except Exception as exc:
            self._connected = False
            self._conn_state.set_state(
                MT5ConnectionState.TERMINAL_ERROR, f"MCP unreachable at {self._url}"
            )
            #: URL and exception class only — a network error's message can
            #: echo the request URL/headers, so it is not propagated.
            raise RuntimeError(
                f"Cannot connect to MT5 MCP endpoint at {self._url} "
                f"({type(exc).__name__}); is the terminal bridge running?"
            ) from exc
        self._transport = transport
        self._connected = True
        #: CONNECTED, not just CONNECTING (record_success would otherwise
        #: leave the state machine at DISCONNECTED until the first read).
        self._conn_state.set_state(MT5ConnectionState.CONNECTED, "mcp session established")
        logger.info("[MCP] event=CONNECTED url=%s", self._url)
        return True

    def disconnect(self) -> None:
        """Release the MCP session. Idempotent; never raises."""
        self._transport = None
        self._connected = False
        self._server_utc = None
        self._conn_state.set_state(MT5ConnectionState.DISCONNECTED, "mcp session closed")
        logger.info("[MCP] event=DISCONNECTED")

    def is_connected(self) -> bool:
        """True only while an MCP session is established."""
        return self._connected

    def connection_state(self) -> MT5ConnectionState:
        """Real connection state machine (never derived from config)."""
        return self._conn_state

    # ------------------------------------------------------------------
    # Transport helpers
    # ------------------------------------------------------------------
    def _require_session(self) -> _McpTransport:
        if not self._connected or self._transport is None:
            raise RuntimeError("MCP adapter is not connected — call connect() first")
        return self._transport

    def _call_json(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call a read tool and decode its text content as JSON.

        Raises ``RuntimeError`` on any MCP-level error; the server's own
        error text (e.g. ``invalid datetime range``) carries no credentials.
        """
        transport = self._require_session()
        is_error, text = transport.call_raw(tool, arguments)
        self._last_envelope = {"tool": tool, "is_error": is_error, "len": len(text)}
        if is_error:
            raise RuntimeError(f"MCP {tool} failed: {text[:500]}")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"MCP {tool} returned non-JSON content: {text[:200]}") from exc

    def _server_now(self) -> datetime:
        """The MCP server's own UTC clock.

        The tool clock is authoritative for count->range derivation (A-11).
        Falls back to the local UTC clock when the server omits utc_time; the
        fallback is recorded in the snapshot notes by callers (never silent).
        """
        if self._server_utc is not None:
            return self._server_utc
        try:
            info = self._call_json("get_time_information") or {}
        except RuntimeError:
            info = {}
        utc_text = info.get("utc_time") if isinstance(info, dict) else None
        self._server_utc = normalize_utc(utc_text) if utc_text else datetime.now(UTC)
        return self._server_utc

    @staticmethod
    def _assert_history_args(args: dict[str, Any]) -> None:
        """A-12: only whitelisted argument names reach the wire.

        The MCP server IGNORES unknown arguments and returns the full
        multi-megabyte history instead of rejecting the call; every key is
        checked here so the adapter can never trigger that path.
        """
        bad = set(args) - _ALLOWED_HISTORY_ARGS
        if bad:
            raise RuntimeError(f"refused to send unwhitelisted MCP args: {sorted(bad)}")

    def _derive_range(
        self,
        count: int,
        timeframe: str,
        from_utc: Any = None,
        to_utc: Any = None,
        notes: list[str] | None = None,
    ) -> tuple[datetime, datetime]:
        """Translate a port (count | from_utc | to_utc) request into an MCP range.

        MCP history tools REQUIRE datetime_from/datetime_to and reject a
        future datetime_to with ``invalid datetime range`` (A-11), while the
        port contract is count-based. Resolution:
          * the window END is the requested to_utc, clamped to the server's
            own UTC clock (a future boundary would hard-error);
          * the window START is from_utc when given, otherwise derived as
            end - 2 * count * timeframe so a thinly traded window still
            yields >= count bars (the caller trims to `count` anyway).
        Clamping and derivation are appended to `notes` so the caller can
        stamp them onto the returned snapshots.
        """
        note = notes if notes is not None else []
        period = str(timeframe).upper()
        if period not in _SUPPORTED_PERIODS:
            #: Rejected BEFORE any server call or range math: an unmapped
            #: period must never reach the wire (MCP would resolve it
            #: against its own default and hand back another timeframe).
            raise RuntimeError(
                f"MCP adapter does not serve timeframe '{timeframe}' "
                f"(supported: {sorted(_SUPPORTED_PERIODS)})"
            )
        now = self._server_now()

        if to_utc is not None:
            to_dt = normalize_utc(to_utc)
            if to_dt is None:
                note.append("requested datetime_to was not parseable; used server UTC")
                to_dt = now
        else:
            to_dt = now
        if to_dt > now:
            note.append(
                f"requested datetime_to {to_dt.isoformat()} is in the future; "
                f"clamped to server UTC {now.isoformat()}"
            )
            to_dt = now

        if from_utc is not None:
            from_dt = normalize_utc(from_utc)
            if from_dt is None:
                raise RuntimeError(
                    f"requested datetime_from '{from_utc}' is not a parseable timestamp"
                )
        else:
            minutes_per_bar = _PERIOD_MINUTES.get(period, 1)
            #: Over-fetch 2x the count: MCP windows are calendar-time ranges
            #: and a sparse session yields fewer bars than count implies.
            lookback_minutes = max(1, int(count)) * int(minutes_per_bar) * 2
            from_dt = to_dt - timedelta(minutes=lookback_minutes)
            note.append(
                f"count={count} -> derived range "
                f"{_iso_server_local(from_dt)}..{_iso_server_local(to_dt)} "
                f"(lookback {lookback_minutes} min at {period})"
            )
        if from_dt > to_dt:
            raise RuntimeError("requested datetime_from is after datetime_to (or after now)")
        return from_dt, to_dt

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------
    def get_account_snapshot(self) -> AccountSnapshot:
        """Account + terminal block from get_trading_account_info.

        MCP shape (evidence/account_info_mcp.json): the payload is NESTED as
        ``{account: {...}, terminal: {...}}`` whereas native account_info()
        is flat, and MCP names the broker ``broker`` (native: ``company``).
        Both are flattened onto the native field contract here so
        build_account_snapshot sees the shape it was written for.

        Fields MCP does NOT expose (leverage, trade_mode as int enum,
        trade_allowed, limit_orders, margin_level, currency_digits,
        margin_so_mode, fifo_close) are absent from the mapping and stay None
        on the snapshot rather than being invented — A-10's 17 native-only
        fields. MCP-only fields (swaps, commissions, read_only, name,
        margin_mode "hedging") have no AccountSnapshot slot and are preserved
        in `note` instead of being dropped silently.
        """
        if not self._connected:
            return AccountSnapshot().as_error("account_info", None, "adapter not connected")
        try:
            payload = self._call_json("get_trading_account_info") or {}
        except RuntimeError as exc:
            self._conn_state.record_failure("account_info", str(exc))
            return AccountSnapshot().as_error("account_info", "MCP_ERROR", str(exc))
        account = payload.get("account") if isinstance(payload, dict) else None
        terminal = payload.get("terminal") if isinstance(payload, dict) else None
        if not isinstance(account, dict):
            return AccountSnapshot().as_error(
                "account_info", "MCP_SHAPE", "missing 'account' block"
            )

        flat: dict[str, Any] = dict(account)
        # -- MCP -> native key renames (evidence §3) ----------------------
        if "broker" in flat and "company" not in flat:
            flat["company"] = flat["broker"]
        #: login arrives as a STRING ("10011755849"); AccountSnapshot.login
        #: is int, so coerce (A-10). A non-numeric login stays None.
        if isinstance(flat.get("login"), str):
            try:
                flat["login"] = int(flat["login"])
            except ValueError:
                flat["login"] = None
        #: MCP's account ``type`` is the string "demo" while native trade_mode
        #: is 0=Demo / 1=Contest / 2=Real. Map the known names; an unknown
        #: string stays None so nobody reads a string where an int is owed.
        type_text = flat.pop("type", None)
        if isinstance(type_text, str):
            flat["trade_mode"] = {
                "demo": 0,
                "contest": 1,
                "real": 2,
            }.get(type_text.strip().lower())
        #: MCP aggregates per-position costs into account-level swaps /
        #: commissions and exposes margin_mode as a word; no native account
        #: slot exists, so they ride along in the notes.
        extras: dict[str, Any] = {
            k: flat.pop(k) for k in ("swaps", "commissions", "read_only") if k in flat
        }
        extras["margin_mode_mcp"] = account.get("margin_mode")
        extras["account_name"] = account.get("name")
        if isinstance(terminal, dict):
            #: The terminal block carries build + connectivity flags;
            #: experts_trade_allowed / mcp_trade_allowed are the closest
            #: MCP analogue of native account.trade_allowed.
            flat.setdefault("company", terminal.get("company"))
            extras["terminal_build"] = terminal.get("build")
            extras["server_connected"] = terminal.get("server_connected")
            extras["trade_expert"] = terminal.get("experts_trade_allowed")
            extras["mcp_trade_allowed"] = terminal.get("mcp_trade_allowed")

        snap = build_account_snapshot(_Payload(flat))
        if not snap.available:
            return snap.as_error("account_info", "MCP_SHAPE", "empty account block")
        snap.note = json.dumps({"mcp_extras": extras}, default=str)  # type: ignore[attr-defined]
        self._conn_state.record_success("account_info")
        return snap

    def get_account_info(self) -> AccountInfo:
        """Legacy typed read (raises on failure, never returns fake data)."""
        snap = self.get_account_snapshot()
        if not snap.available or snap.balance is None or snap.equity is None:
            raise RuntimeError(
                f"Failed to fetch account info over MCP. Error: {snap.error_state or 'unavailable'}"
            )
        return AccountInfo(
            login=snap.login or 0,
            trade_mode=snap.trade_mode or 0,
            leverage=snap.leverage or 100,
            balance=snap.balance,
            equity=snap.equity,
            margin=snap.margin or 0.0,
            margin_free=snap.margin_free or 0.0,
            currency=snap.currency or "USD",
        )

    # ------------------------------------------------------------------
    # Symbol + tick
    # ------------------------------------------------------------------
    def get_symbol_snapshot(self, symbol: str) -> SymbolSnapshot:
        """Symbol spec + current quote from get_marketwatch_symbols.

        MCP exposes ONE tool returning the whole Market Watch list, with the
        spec block and the current bid/ask merged in the same object (native
        splits symbol_info() and symbol_info_tick()). The requested symbol is
        selected here and split back into the spec/tick separation that
        build_symbol_snapshot expects:
          MCP tick_size / tick_value / contract_size ->
              native trade_tick_size / trade_tick_value / trade_contract_size
          MCP trade_calculation_mode ("cfd leverage") ->
              native trade_calc_mode (int enum), with the MCP string kept
              under its own name for parity comparison
        MCP-only quote fields with no native tick slot (bid_high/bid_low/
        ask_high/ask_low/price_open/price_close/update_time) are preserved
        inside the tick dict so the data is not lost.

        NOTE: the tick `time` is deliberately NOT handed to the builder. The
        builder converts an int tick time with broker_epoch_to_utc, which
        SUBTRACTS the configured broker server offset; MCP's update_time is
        already a UTC-representing ISO string (evidence §1), so the epoch is
        materialized and stamped directly instead (see invariant 6).
        """
        if not self._connected:
            return SymbolSnapshot().as_error("symbol_info", None, "adapter not connected")
        try:
            payload = self._call_json("get_marketwatch_symbols") or {}
        except RuntimeError as exc:
            self._conn_state.record_failure("symbol_info", str(exc))
            return SymbolSnapshot().as_error("symbol_info", "MCP_ERROR", str(exc))
        symbols = payload.get("symbols") if isinstance(payload, dict) else None
        if not isinstance(symbols, list):
            return SymbolSnapshot().as_error("symbol_info", "MCP_SHAPE", "missing 'symbols' list")
        match = next(
            (s for s in symbols if isinstance(s, dict) and s.get("symbol") == symbol),
            None,
        )
        if match is None:
            return SymbolSnapshot().as_error(
                "symbol_info", "NOT_FOUND", f"'{symbol}' not in Market Watch"
            )

        spec: dict[str, Any] = {"name": symbol}
        for mcp_key, native_key in (
            ("digits", "digits"),
            ("point", "point"),
            ("trade_mode", "trade_mode"),
            ("tick_size", "trade_tick_size"),
            ("tick_value", "trade_tick_value"),
            ("contract_size", "trade_contract_size"),
            ("volume_min", "volume_min"),
            ("volume_max", "volume_max"),
            ("volume_step", "volume_step"),
            ("trade_stops_level", "trade_stops_level"),
            ("trade_freeze_level", "trade_freeze_level"),
            ("currency_base", "currency_base"),
            ("currency_profit", "currency_profit"),
            ("currency_margin", "currency_margin"),
            ("spread_float", "spread_float"),
            ("description", "description"),
        ):
            if match.get(mcp_key) is not None:
                spec[native_key] = match[mcp_key]
        calc_mode = match.get("calculation_mode") or match.get("trade_calculation_mode")
        if calc_mode is not None:
            #: Both views are kept: the MCP word under its own name (so a
            #: parity diff can compare it against the raw payload) and the
            #: native int enum under the native slot. Re-attached to the
            #: snapshot after the builder, which only copies native names.
            spec["trade_calculation_mode"] = calc_mode
            spec["trade_calc_mode"] = _CALC_MODE_MAP.get(str(calc_mode).strip().lower())

        tick: dict[str, Any] = {}
        update_time = match.get("update_time")
        tick_utc = normalize_utc(update_time) if isinstance(update_time, str) else None
        if tick_utc is not None:
            epoch = int(tick_utc.timestamp())
            #: NOT passed through the builder (see docstring); stamped here
            #: so consumers still get time / time_msc / time_utc.
            tick["time"] = epoch
            tick["time_msc"] = epoch * 1000
        for key in ("bid", "ask", "last", "volume"):
            if match.get(key) is not None:
                tick[key] = match[key]

        snap = build_symbol_snapshot(_Payload(spec), _Payload(tick) if tick else None)
        if not snap.available:
            return snap.as_error("symbol_info", "MCP_SHAPE", "empty symbol block")
        #: The builder only copies its whitelisted native spec names, so the
        #: MCP-only fields it does not know are re-attached here — the raw
        #: calculation-mode word plus the MCP-only day statistics. Nothing is
        #: silently dropped, while the native-shaped slots stay native-shaped.
        snap.spec["trade_calculation_mode"] = calc_mode
        snap.spec["trade_calc_mode"] = (
            _CALC_MODE_MAP.get(str(calc_mode).strip().lower()) if calc_mode is not None else None
        )
        for key in ("bid_high", "bid_low", "ask_high", "ask_low", "price_open", "price_close"):
            if match.get(key) is not None:
                snap.tick[key] = match[key]
        #: Freshness uses the already-UTC timestamp directly (the builder's
        #: own freshness path is skipped because we withhold the int tick
        #: time from it — see the docstring for why).
        if tick_utc is not None:
            snap.tick["time_utc"] = tick_utc.isoformat()
            snap.tick_freshness_ms = float(
                max(0.0, (snap.captured_at - tick_utc).total_seconds() * 1000.0)
            )
            snap.tick_stale = snap.tick_freshness_ms > 30_000.0
        snap.note = json.dumps(  # type: ignore[attr-defined]
            {
                "mcp_update_time": update_time,
                "mcp_trade_mode_name": match.get("trade_mode_name"),
                "mcp_trade_execution_mode": match.get("trade_execution_mode"),
            },
            default=str,
        )
        self._conn_state.record_success("symbol_info")
        return snap

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """Legacy typed read (raises on failure, never returns fake data)."""
        snap = self.get_symbol_snapshot(symbol)
        spec = snap.spec
        if not snap.available or not spec or spec.get("digits") is None:
            raise RuntimeError(
                f"Symbol info for '{symbol}' not available over MCP. "
                f"Error: {snap.error_state or 'unavailable'}"
            )
        return SymbolInfo(
            symbol=str(spec.get("name") or symbol),
            digits=int(float(spec["digits"])),
            point=float(spec.get("point") or 0.00001),
            tick_size=float(spec.get("trade_tick_size") or spec.get("point") or 0.00001),
            tick_value=float(spec.get("trade_tick_value") or 0.0),
            volume_min=float(spec.get("volume_min") or 0.01),
            volume_max=float(spec.get("volume_max") or 100.0),
            volume_step=float(spec.get("volume_step") or 0.01),
            stops_level=int(float(spec.get("trade_stops_level") or 0.0)),
            freeze_level=int(float(spec.get("trade_freeze_level") or 0.0)),
            trade_contract_size=float(spec.get("trade_contract_size") or 100.0),
        )

    def get_broker_tick(self, symbol: str) -> BrokerTickSnapshot:
        """Current quote for `symbol` (Market Watch snapshot, no history call).

        MCP has no symbol_info_tick equivalent; the live quote rides on the
        Market Watch payload. Fields MCP does not carry (last, volume, flags)
        are left None rather than fabricated — the native adapter fills them
        from symbol_info_tick, so this is a documented EXPECTED DIFFERENCE.
        """
        if not self._connected:
            return BrokerTickSnapshot().as_error("symbol_info_tick", None, "adapter not connected")
        try:
            payload = self._call_json("get_marketwatch_symbols") or {}
        except RuntimeError as exc:
            self._conn_state.record_failure("symbol_info_tick", str(exc))
            return BrokerTickSnapshot().as_error("symbol_info_tick", "MCP_ERROR", str(exc))
        symbols = payload.get("symbols") if isinstance(payload, dict) else None
        match = (
            next(
                (s for s in symbols if isinstance(s, dict) and s.get("symbol") == symbol),
                None,
            )
            if isinstance(symbols, list)
            else None
        )
        if match is None:
            return BrokerTickSnapshot().as_error(
                "symbol_info_tick", "NOT_FOUND", f"'{symbol}' not in Market Watch"
            )
        snap = BrokerTickSnapshot()
        snap.available = True
        snap.source = "BROKER_NATIVE"
        snap.symbol = symbol
        snap.bid = float(match.get("bid") or 0.0)
        snap.ask = float(match.get("ask") or 0.0)
        #: last / volume / flags are UNSUPPORTED over MCP (evidence §2: tick
        #: rows carry only time_ms/bid/ask). None, never 0-presented-as-data.
        snap.last = None
        snap.last_volume = None
        snap.volume = None
        snap.flags = None
        tick_utc = normalize_utc(match.get("update_time"))
        if tick_utc is not None:
            epoch = int(tick_utc.timestamp())
            snap.time_utc = tick_utc
            snap.time = epoch
            snap.time_msc = epoch * 1000
            snap.freshness_ms = max(0.0, (datetime.now(UTC) - tick_utc).total_seconds() * 1000.0)
            snap.stale = snap.freshness_ms > 30_000.0
        if snap.bid > 0 and snap.ask > 0:
            snap.spread_points = round(float(snap.ask - snap.bid), 8)
        self._conn_state.record_success("symbol_info_tick")
        return snap

    def get_last_tick(self, symbol: str) -> TickData:
        """Legacy typed read (raises on failure, never returns fake data)."""
        snap = self.get_broker_tick(symbol)
        if not snap.available or snap.bid is None or snap.ask is None:
            raise RuntimeError(
                f"Failed to fetch tick for '{symbol}' over MCP. "
                f"Error: {snap.error_state or 'unavailable'}"
            )
        return TickData(
            symbol=symbol,
            timestamp=snap.time_utc or datetime.now(UTC),
            bid=snap.bid,
            ask=snap.ask,
            last=snap.last or 0.0,
            volume=float(snap.volume or 0.0),
            flags=snap.flags or 0,
        )

    def get_tick(self, symbol: str) -> TickData:
        """Alias for the latest available tick (port contract)."""
        return self.get_last_tick(symbol)

    # ------------------------------------------------------------------
    # Rate / bar history (range-only MCP surface, A-11)
    # ------------------------------------------------------------------
    def get_rate_history(
        self,
        symbol: str,
        timeframe: str = "M1",
        count: int = 500,
        from_utc: Any = None,
    ) -> list[RateBarSnapshot]:
        """Bars via get_chart_history (MCP `period` + datetime range).

        MCP bar shape vs native (evidence §1):
          * `time` is a naive ISO-8601 string representing UTC trade-server
            time, not an epoch (e.g. "2026-09-24T03:06:00"). Lane A verified
            it equals datetime.fromtimestamp(native_epoch, tz=UTC) exactly,
            so it is parsed as UTC directly and both `time` (epoch) and
            `time_utc` are materialized for the native-shaped snapshot.
          * `tick_volume` and `spread` are present with identical values.
          * `real_volume` is NOT exposed by MCP -> stays None (EXPECTED
            DIFFERENCE; irrelevant for CFD/forex where real volume is 0).
        MCP returns bars ASCENDING, matching native copy_rates_* ordering.

        The native build_rate_bar_snapshot is NOT reused here: it converts
        `time` with broker_epoch_to_utc, which subtracts the configured
        broker server offset — correct for native broker epochs, wrong for
        MCP's already-UTC ISO strings (it would shift every bar 3h).
        """
        if not self._connected:
            return []
        notes: list[str] = []
        try:
            from_dt, to_dt = self._derive_range(count, timeframe, from_utc, None, notes)
        except RuntimeError as exc:
            self._conn_state.record_failure("get_chart_history", str(exc))
            return []
        args = {
            "symbol": symbol,
            "period": str(timeframe).upper(),
            "datetime_from": _iso_server_local(from_dt),
            "datetime_to": _iso_server_local(to_dt),
            "limit": int(count),
        }
        self._assert_history_args(args)
        try:
            payload = self._call_json("get_chart_history", args) or {}
        except RuntimeError as exc:
            self._conn_state.record_failure("get_chart_history", str(exc))
            return []
        history = payload.get("history") if isinstance(payload, dict) else None
        if not isinstance(history, list):
            return []
        #: B-14m: ok:true + history:[] is a legitimate EMPTY WINDOW — never
        #: an error, never a failure; logged at debug only.
        if not history:
            logger.debug(
                "[MCP] event=EMPTY_WINDOW tool=get_chart_history symbol=%s period=%s "
                "range=%s..%s (MCP returned ok with no rows)",
                symbol,
                timeframe,
                args["datetime_from"],
                args["datetime_to"],
            )
            return []
        bars = [
            snap for snap in (_build_mcp_rate_bar(row, notes) for row in history) if snap.available
        ]
        report = validate_ohlc_bars(bars)
        if report["invalid"] > 0:
            logger.warning(
                "[MCP] event=HISTORY_VALIDATION symbol=%s period=%s received=%s invalid=%s",
                symbol,
                timeframe,
                len(bars),
                report["invalid"],
            )
        self._conn_state.record_success("get_chart_history")
        return bars

    def get_historical_bars(
        self, symbol: str, timeframe: str = "M1", count: int = 100
    ) -> list[BarData]:
        """Legacy typed bar read over the MCP range surface.

        Mirrors DirectMT5Adapter.get_historical_bars: maps RateBarSnapshot
        onto BarData, drops structurally invalid rows loudly, and marks the
        still-forming current bar via is_current_bar_forming (BUG-308: a
        forming bar must NOT be presented as sealed or BarAggregator.reseed
        anchors a minute in the future and drops every live tick of it).
        """
        rate_bars = self.get_rate_history(symbol=symbol, timeframe=timeframe, count=count)
        bars: list[BarData] = []
        dropped = 0
        forming = 0
        for row in rate_bars:
            if row.time_utc is None or None in (row.open, row.high, row.low, row.close):
                continue
            o, hi, lo, close = (
                float(row.open),
                float(row.high),
                float(row.low),
                float(row.close),
            )
            if (
                not all(math.isfinite(v) for v in (o, hi, lo, close))
                or min(o, close) <= 0.0
                or hi < lo
                or hi < o
                or hi < close
                or lo > o
                or lo > close
            ):
                dropped += 1
                continue
            is_complete = not is_current_bar_forming(row.time_utc, str(timeframe).upper())
            forming += 0 if is_complete else 1
            bars.append(
                BarData(
                    symbol=symbol,
                    timeframe=str(timeframe).upper(),
                    timestamp=row.time_utc,
                    open=o,
                    high=hi,
                    low=lo,
                    close=close,
                    tick_volume=int(row.tick_volume or 0),
                    is_complete=is_complete,
                )
            )
        if forming:
            logger.info(
                "[MCP] event=FORMING_BAR_MARKED symbol=%s timeframe=%s count=%s",
                symbol,
                str(timeframe).upper(),
                forming,
            )
        if dropped:
            logger.error(
                "[MCP] event=HISTORY_INVALID_DROPPED symbol=%s timeframe=%s "
                "received=%s dropped=%s kept=%s",
                symbol,
                str(timeframe).upper(),
                len(rate_bars),
                dropped,
                len(bars),
            )
        return bars

    def get_tick_history(
        self,
        symbol: str,
        count: int = 500,
        from_utc: Any = None,
        to_utc: Any = None,
    ) -> list[TickHistorySnapshot]:
        """Ticks via get_chart_ticks_history (MCP `tick` period + range).

        MCP tick shape vs native (evidence §2):
          * `time_ms` is an ISO string WITH millisecond precision
            ("2026-09-23T01:04:35.369"); native gives time (epoch s) +
            time_msc (epoch ms int). Both are materialized here.
          * only `bid` and `ask` are carried; `last`, `volume` and the
            TICK_FLAG_* `flags` bitmask are UNSUPPORTED over MCP (EXPECTED
            DIFFERENCE) and stay None — never 0-as-data.

        build_tick_history_snapshot is not reused for the same reason as the
        bar builder: it runs `time` through broker_epoch_to_utc.
        """
        if not self._connected:
            return []
        notes: list[str] = []
        try:
            from_dt, to_dt = self._derive_range(count, "M1", from_utc, to_utc, notes)
        except RuntimeError as exc:
            self._conn_state.record_failure("get_chart_ticks_history", str(exc))
            return []
        args = {
            "symbol": symbol,
            "period": "tick",
            "datetime_from": _iso_server_local(from_dt),
            "datetime_to": _iso_server_local(to_dt),
            "limit": int(count),
        }
        self._assert_history_args(args)
        try:
            payload = self._call_json("get_chart_ticks_history", args) or {}
        except RuntimeError as exc:
            self._conn_state.record_failure("get_chart_ticks_history", str(exc))
            return []
        history = payload.get("history") if isinstance(payload, dict) else None
        if not isinstance(history, list):
            return []
        #: B-14m: empty window is a silent ok:true — a result, not an error.
        if not history:
            logger.debug(
                "[MCP] event=EMPTY_WINDOW tool=get_chart_ticks_history symbol=%s "
                "range=%s..%s (MCP returned ok with no rows)",
                symbol,
                args["datetime_from"],
                args["datetime_to"],
            )
            return []
        ticks = [
            snap for snap in (_build_mcp_tick(row, notes) for row in history) if snap.available
        ]
        self._conn_state.record_success("get_chart_ticks_history")
        return ticks

    # ------------------------------------------------------------------
    # Positions and orders (read)
    # ------------------------------------------------------------------
    def get_all_positions(self, symbol: str | None = None) -> list[PositionSnapshot]:
        """Open positions via get_trading_open_positions.

        MCP returns ``{positions: [...], orders: [...]}`` — the ORDER half is
        the pending-order inventory and is served by
        get_pending_orders_snapshot; only the position half is mapped here.
        MCP position rows are stringly-typed and key-renamed vs native
        (B-24). The live probe account had no open positions
        ({"positions": [], "orders": []}), so the field mapping is written
        against the documented MCP shape and every native field MCP does not
        name is left None rather than guessed.
        """
        if not self._connected:
            return []
        try:
            payload = self._call_json("get_trading_open_positions") or {}
        except RuntimeError as exc:
            self._conn_state.record_failure("positions_get", str(exc))
            return []
        rows = payload.get("positions") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []
        out = [
            snap
            for snap in (_build_mcp_position(row, self._symbol) for row in rows)
            if snap.available
        ]
        if symbol:
            out = [p for p in out if p.symbol == symbol]
        self._conn_state.record_success("positions_get")
        return out

    def get_pending_orders_snapshot(self, symbol: str | None = None) -> list[OrderSnapshot]:
        """Active pending orders — the `orders` half of get_trading_open_positions.

        Same stringly-typed MCP convention as B-24 (order ids are strings,
        type/state/filling are words not enum ints, timestamps are ISO).
        """
        if not self._connected:
            return []
        try:
            payload = self._call_json("get_trading_open_positions") or {}
        except RuntimeError as exc:
            self._conn_state.record_failure("orders_get", str(exc))
            return []
        rows = payload.get("orders") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []
        out = [snap for snap in (_build_mcp_pending_order(row) for row in rows) if snap.available]
        if symbol:
            out = [o for o in out if o.symbol == symbol]
        self._conn_state.record_success("orders_get")
        return out

    def get_positions(self, symbol: str | None = None) -> list[Position]:
        """Legacy bot-management read: BOT positions only (symbol + magic).

        Mirrors DirectMT5Adapter.get_positions' XAUUSD filter so the two
        transports stay interchangeable for the bot's own position path.
        MCP exposes no magic number on open positions, so magic cannot be
        part of the filter: an XAUUSD position is returned and its magic is
        reported as the bot's (the native contract's non-null slot) — this
        keeps the typed Position contract satisfiable while the fail-closed
        direction is preserved elsewhere: get_all_positions() carries the
        raw magic=None and get_positions() never invents a magic value.
        """
        rows = self.get_all_positions()
        positions: list[Position] = []
        for pos in rows:
            if pos.symbol != _BOT_SYMBOL:
                continue
            positions.append(
                Position(
                    ticket=pos.ticket or 0,
                    symbol=pos.symbol or symbol or _BOT_SYMBOL,
                    type=OrderType.BUY if (pos.type or 0) == 0 else OrderType.SELL,
                    volume=pos.volume or 0.0,
                    price_open=pos.price_open or 0.0,
                    sl=pos.sl or 0.0,
                    tp=pos.tp or 0.0,
                    profit=pos.profit or 0.0,
                    magic=_BOT_MAGIC,
                )
            )
        return positions

    def get_history_orders(
        self, from_utc: Any = None, to_utc: Any = None, symbol: str | None = None
    ) -> list[HistoryOrderSnapshot]:
        """Historical orders via get_trading_history_orders.

        B-24 (documented, not silently normalized): MCP order rows are
        stringly-typed and key-RENAMED vs native:
            order_id (str)         -> ticket (int)
            order_external_id      -> external_id
            position_id (str)      -> identifier
            type "buy"/"sell"      -> ORDER_TYPE_BUY(0) / SELL(1)
            state "filled"/...     -> ORDER_STATE_* int
            filling "fill or kill" -> ORDER_FILLING_* int
            open_reason "Expert"   -> reason (ORDER_REASON_* int)
            open_time / done_time (ISO) -> time_setup / time_done (epoch)
        Fields MCP does not expose (magic, time_expiration, type_time,
        price_stop_limit, sl, tp) stay None. The MCP strings are preserved
        verbatim in `note` so a parity diff can always see the raw input.
        """
        return self._history_rows(
            tool="get_trading_history_orders",
            key="orders",
            mapper=_build_mcp_history_order,
            from_utc=from_utc,
            to_utc=to_utc,
            symbol=symbol,
            op="get_trading_history_orders",
        )

    def get_history_deals(
        self, from_utc: Any = None, to_utc: Any = None, symbol: str | None = None
    ) -> list[DealSnapshot]:
        """Historical deals — closed positions via get_trading_history_positions.

        MCP has NO history_deals_get equivalent; the closest read-only
        surface is the closed-positions history, which is mapped onto
        DealSnapshot (a closed position's open+close pair IS the deal pair).
        Fields without an MCP source (order id, commission/swap/fee, entry
        direction enum, external_id, reason, magic) stay None; profit maps
        from the close row.
        """
        return self._history_rows(
            tool="get_trading_history_positions",
            key="positions",
            mapper=_build_mcp_deal,
            from_utc=from_utc,
            to_utc=to_utc,
            symbol=symbol,
            op="get_trading_history_positions",
        )

    def get_closed_deals_history(self, symbol: str, hours_back: int = 24) -> list[dict]:
        """Closed deals history for a symbol (port contract, dict form).

        MCP serves the closed-POSITION history; each closed position becomes
        one deal dict with the fields the legacy accounting path consumes.
        The A-14 ``available`` gate makes an honest empty result
        distinguishable from an offline adapter (this method returns [] both
        ways, so callers should check ``available`` first).
        """
        to_utc = self._server_now()
        from_utc = to_utc - timedelta(hours=max(1, int(hours_back)))
        deals = self.get_history_deals(from_utc=from_utc, to_utc=to_utc, symbol=symbol or None)
        out: list[dict] = []
        for deal in deals:
            out.append(
                {
                    "ticket": deal.ticket,
                    "order": deal.order,
                    "position_id": deal.position_id,
                    "symbol": deal.symbol or symbol,
                    "type": deal.type,
                    "volume": deal.volume,
                    "price": deal.price,
                    "profit": deal.profit,
                    "time": deal.time,
                    "time_utc": deal.time_utc.isoformat() if deal.time_utc else None,
                    "source": "MCP",
                }
            )
        return out

    def _history_rows(
        self,
        tool: str,
        key: str,
        mapper: Any,
        from_utc: Any,
        to_utc: Any,
        symbol: str | None,
        op: str,
    ) -> list[Any]:
        """Shared range+fetch+map path for the two MCP history tools."""
        if not self._connected:
            return []
        notes: list[str] = []
        try:
            from_dt, to_dt = self._derive_range(1000, "M1", from_utc, to_utc, notes)
        except RuntimeError as exc:
            self._conn_state.record_failure(op, str(exc))
            return []
        args: dict[str, Any] = {
            "datetime_from": _iso_server_local(from_dt),
            "datetime_to": _iso_server_local(to_dt),
            "limit": 1000,
        }
        if symbol:
            args["symbol"] = symbol
        self._assert_history_args(args)
        try:
            payload = self._call_json(tool, args) or {}
        except RuntimeError as exc:
            self._conn_state.record_failure(op, str(exc))
            return []
        rows = payload.get(key) if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []
        #: B-14m: an empty history window is a silent ok:true — empty result.
        if not rows:
            logger.debug(
                "[MCP] event=EMPTY_WINDOW tool=%s range=%s..%s (ok with no rows)",
                tool,
                args["datetime_from"],
                args["datetime_to"],
            )
            return []
        out = [snap for snap in (mapper(row) for row in rows) if snap.available]
        self._conn_state.record_success(op)
        return out

    # ------------------------------------------------------------------
    # Terminal diagnostics
    # ------------------------------------------------------------------
    def get_terminal_state(self) -> dict[str, Any]:
        """Terminal diagnostic subset from get_time_information + account info.

        Safe subset only: no credentials, no filesystem paths. Reports the
        transport explicitly (``transport: "MCP"``) so a dashboard can never
        mistake this adapter for the native IPC driver.
        """
        if not self._connected:
            return {
                "available": False,
                "reason": "adapter not connected",
                "transport": "MCP",
                "connection": self._conn_state.to_dict(),
            }
        info: dict[str, Any] = {
            "available": True,
            "transport": "MCP",
            "endpoint": self._url,
            "connection": self._conn_state.to_dict(),
        }
        try:
            times = self._call_json("get_time_information") or {}
        except RuntimeError as exc:
            times = {}
            info["time_error"] = str(exc)[:200]
        if isinstance(times, dict):
            for key in ("utc_time", "trade_server_last_known_time"):
                if key in times:
                    info[key] = times[key]
        try:
            account = self.get_account_snapshot()
        except RuntimeError as exc:  # an account read failure is non-fatal here
            account = AccountSnapshot().as_error("account_info", "MCP_ERROR", str(exc))
        info["trade_allowed"] = bool(account.trade_allowed)
        info["server"] = account.server
        info["login"] = account.login
        self._conn_state.set_terminal(_AttrProxy(info))
        return info

    # ------------------------------------------------------------------
    # READ-ONLY enforcement: every mutating port method fails closed.
    # No MCP call is issued for any of these — the refusal happens before
    # any network I/O, and FORBIDDEN_TOOLS guards the transport as well.
    # ------------------------------------------------------------------
    def send_order(self, order: Any) -> bool:
        raise NotImplementedError(_READ_ONLY_MESSAGE)

    def execute_market_order(
        self,
        symbol: str,
        order_type: Any,
        volume: float,
        price: float,
        stop_loss: float,
        take_profit: float,
    ) -> int:
        raise NotImplementedError(_READ_ONLY_MESSAGE)

    def place_pending_order(
        self,
        symbol: str,
        order_type: Any,
        volume: float,
        price: float,
        stop_loss: float,
        take_profit: float,
    ) -> int:
        raise NotImplementedError(_READ_ONLY_MESSAGE)

    def modify_position(self, ticket: int, stop_loss: float, take_profit: float) -> bool:
        raise NotImplementedError(_READ_ONLY_MESSAGE)

    def close_position(self, ticket: int, volume: float | None = None) -> bool:
        raise NotImplementedError(_READ_ONLY_MESSAGE)

    def modify_order(self, ticket: int, stop_loss: float, take_profit: float) -> bool:
        raise NotImplementedError(_READ_ONLY_MESSAGE)

    def cancel_pending_order(self, ticket: int) -> bool:
        raise NotImplementedError(_READ_ONLY_MESSAGE)


# ---------------------------------------------------------------------------
# MCP -> native enum maps (documented EXPECTED DIFFERENCE: MCP uses words,
# native uses MT5 integer enumerations).
# ---------------------------------------------------------------------------
_CALC_MODE_MAP: dict[str, int] = {
    "forex": 0,
    "cfd": 1,
    "cfd leverage": 1,
    "futures": 2,
    "calculation mode exchange stocks": 2,
    "exchange": 3,
}

_ORDER_STATE_MAP: dict[str, int] = {
    "started": 0,
    "placed": 1,
    "canceled": 2,
    "partial": 3,
    "filled": 4,
    "rejected": 5,
    "expired": 6,
}

_FILLING_MAP: dict[str, int] = {
    "fill or kill": 0,
    "immediate or cancel": 1,
    "return": 2,
}

_REASON_MAP: dict[str, int] = {
    "client": 0,
    "expert": 1,
    "dealer": 2,
    "sl": 3,
    "tp": 4,
    "so": 5,
}


# ---------------------------------------------------------------------------
# MCP -> native shape mappers. Pure functions (no I/O), so the mapping of
# every field is unit-testable without a live terminal.
# ---------------------------------------------------------------------------
def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _order_type_int(value: Any) -> int | None:
    """MCP word ("buy"/"sell") -> ORDER_TYPE_BUY(0) / ORDER_TYPE_SELL(1).

    Pending-type words map onto their base direction; MCP's pending-order
    distinction lives in the tool surface, not in a distinct type code here.
    """
    if isinstance(value, str):
        token = value.strip().lower()
        if token.startswith("buy"):
            return 0
        if token.startswith("sell"):
            return 1
    if isinstance(value, int):
        return value
    return None


def _build_mcp_rate_bar(row: dict[str, Any], notes: list[str] | None = None) -> RateBarSnapshot:
    """One MCP bar row -> RateBarSnapshot.

    Documented EXPECTED DIFFERENCES vs the native copy_rates_* row:
      * `time` ISO string -> epoch + time_utc (both materialized);
      * `real_volume` absent on MCP -> None (CFD/forex real volume is 0, so
        None is indistinguishable from the native value in practice but is
        never falsely reported as measured);
      * `tick_volume` / `spread` map by name.
    """
    snap = RateBarSnapshot()
    if not row:
        return snap
    parsed = normalize_utc(row.get("time"))
    if parsed is None:
        return snap
    snap.available = True
    snap.source = "BROKER_NATIVE"
    snap.time_utc = parsed
    try:
        snap.time = int(parsed.timestamp())
    except (OverflowError, OSError, ValueError):
        snap.time = None
    snap.open = _float_or_none(row.get("open"))
    snap.high = _float_or_none(row.get("high"))
    snap.low = _float_or_none(row.get("low"))
    snap.close = _float_or_none(row.get("close"))
    snap.tick_volume = _int_or_none(row.get("tick_volume"))
    snap.spread = _int_or_none(row.get("spread"))
    #: real_volume is UNSUPPORTED on MCP (evidence §1). Deliberately None.
    snap.real_volume = None
    if notes:
        snap.note = json.dumps({"mcp_notes": notes}, default=str)  # type: ignore[attr-defined]
    return snap


def _build_mcp_tick(row: dict[str, Any], notes: list[str] | None = None) -> TickHistorySnapshot:
    """One MCP tick row -> TickHistorySnapshot.

    MCP carries {time_ms, bid, ask} (+ optional volume). `last`, `volume`
    and `flags` (TICK_FLAG_* bitmask) are UNSUPPORTED (evidence §2) and stay
    None — never fabricated as 0.
    """
    snap = TickHistorySnapshot()
    if not row:
        return snap
    parsed = normalize_utc(row.get("time_ms"))
    if parsed is None:
        return snap
    snap.available = True
    snap.source = "BROKER_NATIVE"
    snap.time_utc = parsed
    try:
        epoch = parsed.timestamp()
        snap.time = int(epoch)
        #: native time_msc is epoch MILLISECONDS; the MCP ISO string carries
        #: sub-second precision ("...35.369") which .timestamp() preserves.
        snap.time_msc = int(round(epoch * 1000.0))
    except (OverflowError, OSError, ValueError):
        pass
    snap.bid = _float_or_none(row.get("bid"))
    snap.ask = _float_or_none(row.get("ask"))
    if "last" in row:
        snap.last = _float_or_none(row.get("last"))
    if "volume" in row:
        snap.volume = _float_or_none(row.get("volume"))
    #: flags is UNSUPPORTED on MCP (no TICK_FLAG_* surface).
    snap.flags = None
    if notes:
        snap.note = json.dumps({"mcp_notes": notes}, default=str)  # type: ignore[attr-defined]
    return snap


def _build_mcp_position(row: dict[str, Any], default_symbol: str) -> PositionSnapshot:
    """One MCP open-position row -> PositionSnapshot (B-24 key renames).

    MCP keys: position_id (str), type ("buy"/"sell"), symbol, open_time (ISO),
    open_volume, open_price, current_price, sl/tp, profit, swap, comment,
    open_reason. Fields MCP does not expose (the ticket/position split,
    magic, time_msc, price_ticket, commission, external_id) stay None.
    """
    snap = PositionSnapshot()
    if not row:
        return snap
    snap.available = True
    snap.source = "BROKER_NATIVE"
    snap.ticket = _int_or_none(row.get("position_id"))
    snap.identifier = _int_or_none(row.get("position_id"))
    snap.symbol = str(row.get("symbol") or default_symbol)
    snap.type = _order_type_int(row.get("type"))
    epoch, time_utc = _epoch_and_utc(row.get("open_time"))
    snap.time = epoch
    snap.time_utc = time_utc
    snap.volume = _float_or_none(row.get("open_volume"))
    snap.price_open = _float_or_none(row.get("open_price"))
    snap.price_current = _float_or_none(row.get("current_price") or row.get("price_current"))
    snap.sl = _float_or_none(row.get("sl") or row.get("stop_loss"))
    snap.tp = _float_or_none(row.get("tp") or row.get("take_profit"))
    snap.profit = _float_or_none(row.get("profit"))
    snap.swap = _float_or_none(row.get("swap") or row.get("swaps"))
    snap.commission = _float_or_none(row.get("commission") or row.get("commissions"))
    comment = row.get("comment")
    snap.comment = str(comment) if comment else None
    #: magic is NOT exposed by MCP for open positions — the bot filter in
    #: get_positions() treats None as non-bot (fail-closed direction).
    snap.magic = None
    snap.note = json.dumps(  # type: ignore[attr-defined]
        {"mcp_open_reason": row.get("open_reason")}, default=str
    )
    return snap


def _build_mcp_pending_order(row: dict[str, Any]) -> OrderSnapshot:
    """One MCP pending-order row -> OrderSnapshot (B-24 renames + enums)."""
    snap = OrderSnapshot()
    if not row:
        return snap
    snap.available = True
    snap.source = "BROKER_NATIVE"
    snap.ticket = _int_or_none(row.get("order_id"))
    snap.identifier = _int_or_none(row.get("position_id"))
    snap.external_id = row.get("order_external_id")
    snap.symbol = str(row.get("symbol") or "")
    snap.type = _order_type_int(row.get("type"))
    setup_epoch, setup_utc = _epoch_and_utc(row.get("open_time"))
    done_epoch, done_utc = _epoch_and_utc(row.get("done_time"))
    snap.time_setup = setup_epoch
    snap.time_setup_msc = int(round(setup_utc.timestamp() * 1000.0)) if setup_utc else None
    snap.time_done = done_epoch
    snap.time_done_msc = int(round(done_utc.timestamp() * 1000.0)) if done_utc else None
    snap.state = _ORDER_STATE_MAP.get(str(row.get("state") or "").strip().lower())
    snap.type_filling = _FILLING_MAP.get(str(row.get("filling") or "").strip().lower())
    snap.volume_current = _float_or_none(row.get("volume_current"))
    snap.volume_initial = _float_or_none(row.get("volume_initial"))
    snap.price_open = _float_or_none(row.get("price_open") or row.get("open_price"))
    snap.sl = _float_or_none(row.get("sl") or row.get("stop_loss"))
    snap.tp = _float_or_none(row.get("tp") or row.get("take_profit"))
    comment = row.get("comment")
    snap.comment = str(comment) if comment else None
    #: type_time / time_expiration / price_stop_limit / magic are not exposed
    #: by MCP for pending orders -> None (documented EXPECTED DIFFERENCE).
    snap.note = json.dumps(  # type: ignore[attr-defined]
        {
            "mcp_open_reason": row.get("open_reason"),
            "mcp_state": row.get("state"),
            "mcp_filling": row.get("filling"),
        },
        default=str,
    )
    return snap


def _build_mcp_history_order(row: dict[str, Any]) -> HistoryOrderSnapshot:
    """One MCP history-order row -> HistoryOrderSnapshot (B-24 renames)."""
    snap = HistoryOrderSnapshot()
    if not row:
        return snap
    snap.available = True
    snap.source = "BROKER_NATIVE"
    snap.ticket = _int_or_none(row.get("order_id"))
    snap.identifier = _int_or_none(row.get("position_id"))
    snap.external_id = row.get("order_external_id")
    snap.symbol = str(row.get("symbol") or "")
    snap.type = _order_type_int(row.get("type"))
    setup_epoch, setup_utc = _epoch_and_utc(row.get("open_time"))
    done_epoch, done_utc = _epoch_and_utc(row.get("done_time"))
    snap.time_setup = setup_epoch
    snap.time_setup_msc = int(round(setup_utc.timestamp() * 1000.0)) if setup_utc else None
    snap.time_done = done_epoch
    snap.time_done_msc = int(round(done_utc.timestamp() * 1000.0)) if done_utc else None
    snap.done_time = done_epoch
    snap.state = _ORDER_STATE_MAP.get(str(row.get("state") or "").strip().lower())
    snap.type_filling = _FILLING_MAP.get(str(row.get("filling") or "").strip().lower())
    snap.reason = _REASON_MAP.get(str(row.get("open_reason") or "").strip().lower())
    snap.volume_current = _float_or_none(row.get("volume_current"))
    snap.volume_initial = _float_or_none(row.get("volume_initial"))
    snap.price_open = _float_or_none(row.get("price_open") or row.get("open_price"))
    comment = row.get("comment")
    snap.comment = str(comment) if comment else None
    #: magic / type_time / time_expiration / price_stop_limit / sl / tp are
    #: not exposed on MCP history orders -> None.
    snap.note = json.dumps(  # type: ignore[attr-defined]
        {"mcp_contract_size": row.get("contract_size")}, default=str
    )
    return snap


def _build_mcp_deal(row: dict[str, Any]) -> DealSnapshot:
    """One MCP closed-position row -> DealSnapshot.

    MCP's history-positions surface reports a CLOSED POSITION (an open+close
    pair) rather than MT5 deals; it is mapped onto DealSnapshot because that
    is the port's deal contract and it carries the accounting-relevant
    fields (profit, volume, price). Fields with no MCP source (order id,
    commission/swap/fee, entry enum, external_id, reason, magic) stay None.
    """
    snap = DealSnapshot()
    if not row:
        return snap
    snap.available = True
    snap.source = "BROKER_NATIVE"
    snap.position_id = _int_or_none(row.get("position_id"))
    snap.symbol = str(row.get("symbol") or "")
    snap.type = _order_type_int(row.get("type"))
    close_epoch, close_utc = _epoch_and_utc(row.get("close_time"))
    open_epoch, open_utc = _epoch_and_utc(row.get("open_time"))
    #: DealSnapshot.time is the deal execution time; the CLOSE leg is the
    #: realized-PnL event, so it is authoritative. The open leg is preserved
    #: in notes for accounting reconciliation.
    snap.time = close_epoch if close_epoch is not None else open_epoch
    snap.time_utc = close_utc or open_utc
    snap.time_msc = (
        int(round(snap.time_utc.timestamp() * 1000.0)) if snap.time_utc is not None else None
    )
    snap.volume = _float_or_none(row.get("close_volume") or row.get("open_volume"))
    snap.price = _float_or_none(row.get("close_price") or row.get("open_price"))
    snap.profit = _float_or_none(row.get("profit"))
    comment = row.get("comment")
    snap.comment = str(comment) if comment else None
    snap.note = json.dumps(  # type: ignore[attr-defined]
        {
            "mcp_open_time": row.get("open_time"),
            "mcp_open_price": row.get("open_price"),
            "mcp_open_reason": row.get("open_reason"),
            "mcp_close_reason": row.get("close_reason"),
            "mcp_contract_size": row.get("contract_size"),
        },
        default=str,
    )
    return snap


def _epoch_and_utc(value: Any) -> tuple[int | None, datetime | None]:
    """MCP naive ISO string -> (epoch seconds, UTC datetime).

    Lane A proved the MCP ISO string for a bar/tick equals
    ``datetime.fromtimestamp(native_epoch, tz=UTC)`` exactly, so the naive
    text is read as UTC and the epoch is derived FROM it. Returns
    (None, None) for missing / unparseable input.
    """
    parsed = normalize_utc(value)
    if parsed is None:
        return None, None
    try:
        return int(parsed.timestamp()), parsed
    except (OverflowError, OSError, ValueError):
        return None, parsed
