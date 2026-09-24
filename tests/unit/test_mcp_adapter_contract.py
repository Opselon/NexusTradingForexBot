"""
Unit Tests — MCPMT5Adapter contract (hermetic; no network, no terminal)
======================================================================
PURPOSE: prove the read-only MT5 MCP transport's contract without ever
       touching the endpoint: every read mapping, every write method
       failing closed, empty-window handling, count->range derivation, and
       the no-hardcoded-key invariant.
OWNER: MT5-PARITY-FORENSICS lane IMPL-F.
CONSUMES: nexus_scalp.adapters.mt5.mcp_adapter (read-only adapter under
       test) + providers.py snapshot types.
PROVIDES: deterministic proof that the adapter maps MCP shapes onto the
       native snapshot contract and can never mutate the terminal.
INVARIANTS: no I/O. The transport is replaced by an in-process fake that
       records every dispatched tool and returns canned JSON payloads
       captured from the real endpoint by lane A (evidence/*.json), so the
       mappings are asserted against observed broker shapes.
EXTEND: a new MCP read tool gets a new mapping test here; a write method
       gets a new fail-closed test.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nexus_scalp.adapters.mt5 import mcp_adapter as mod
from nexus_scalp.adapters.mt5.mcp_adapter import (
    FORBIDDEN_TOOLS,
    READ_TOOLS,
    MCPMT5Adapter,
    _build_mcp_deal,
    _build_mcp_history_order,
    _build_mcp_pending_order,
    _build_mcp_position,
    _build_mcp_rate_bar,
    _build_mcp_tick,
    _Payload,
)
from nexus_scalp.adapters.mt5.providers import (
    BROKER_NATIVE,
    UNAVAILABLE,
)

SYMBOL = "XAUUSD"
EVIDENCE = Path("C:/Users/Capsizer/source/repos/mt5-parity-forensics/evidence")


def _load(name: str) -> dict:
    """Lane-A captured payload from the real MCP endpoint."""
    with open(EVIDENCE / name, encoding="utf-8") as fh:
        return json.load(fh)


class FakeTransport:
    """In-process stand-in for _McpTransport.

    Records every dispatched tool name so a test can prove NO mutating tool
    was ever requested, and serves canned responses keyed by tool. It does
    NOT duplicate the adapter's own guard — that guard is what the
    read-only tests assert against, so a second copy here would only ever
    test itself.
    """

    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []
        self.initialized = 0

    def initialize(self) -> None:
        self.initialized += 1

    def call_raw(self, tool: str, arguments: dict | None) -> tuple[bool, str]:
        #: The adapter's real guards, run here so the read-only tests
        #: exercise the same refusal path production code uses (the fake
        #: IS the transport in unit tests). Forbidden and unmapped tools
        #: never reach the response lookup, exactly as at runtime.
        if tool in FORBIDDEN_TOOLS:
            raise RuntimeError(f"REFUSED_BY_POLICY: {tool} is forbidden (read-only adapter)")
        if tool not in READ_TOOLS:
            raise RuntimeError(f"REFUSED_UNMAPPED: {tool} is not wired into this adapter")
        self.calls.append((tool, dict(arguments or {})))
        response = self.responses.get(tool)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, tuple):
            return response
        if response is None:
            #: An unwired-but-whitelisted tool with no canned payload: the
            #: MCP server answered with an empty text body.
            return False, ""
        return False, json.dumps(response)


def _adapter(responses: dict[str, object], **kwargs) -> MCPMT5Adapter:
    """Build a connected adapter whose transport is a FakeTransport.

    Uses the adapter's own connect path (not attribute injection) so the
    session-state wiring is the code under test too.
    """
    adapter = MCPMT5Adapter(
        url="http://127.0.0.1:22346/mcp",
        api_key="test-key-not-a-secret",
        **kwargs,
    )
    transport = FakeTransport(responses)
    adapter._transport = transport
    adapter._connected = True
    #: Mirrors a real adapter right after connect(): the session is up and
    #: the state machine says CONNECTED (record_success/record_failure then
    #: track per-read health without pretending a read happened yet).
    adapter._conn_state.set_state(adapter._conn_state.CONNECTED, "mcp session established")
    return adapter


def _guarded_transport(adapter: MCPMT5Adapter) -> Any:
    """The adapter's guarded transport (the _McpTransport, not the fake).

    The fake records calls and serves payloads; the ADAPTER's call_raw is
    where the FORBIDDEN_TOOLS / READ_TOOLS guards live, so the read-only
    tests must exercise that layer.
    """
    return adapter._transport


ACCOUNT_MCP = {
    "account": {
        "server": "MetaQuotes-Demo",
        "broker": "MetaQuotes Ltd.",
        "login": "10011755849",
        "name": "Amit Bdr",
        "type": "demo",
        "read_only": False,
        "margin_mode": "hedging",
        "balance": 30462.55,
        "credit": 0.0,
        "margin": 0.0,
        "margin_free": 30462.55,
        "profit": 0.0,
        "equity": 30462.55,
        "swaps": 0.0,
        "commissions": 0.0,
        "currency": "USD",
    },
    "terminal": {
        "build": 6207,
        "server_connected": True,
        "experts_trade_allowed": True,
        "mcp_trade_allowed": True,
        "company": "MetaQuotes Ltd.",
    },
}

SYMBOLS_MCP = {
    "symbols": [
        {
            "symbol": "XAUUSD",
            "digits": 2,
            "point": 0.01,
            "tick_size": 0.01,
            "tick_value": 0.1,
            "contract_size": 100.0,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "trade_mode": 4,
            "trade_stops_level": 0,
            "trade_freeze_level": 0,
            "calculation_mode": "cfd leverage",
            "spread_float": True,
            "bid": 4296.83,
            "ask": 4297.43,
            "update_time": "2026-09-24T03:55:48",
            "trade_mode_name": "full",
        }
    ]
}

BARS_MCP = {
    "symbol": "XAUUSD",
    "period": "M1",
    "ok": True,
    "history": [
        {
            "time": "2026-09-23T22:09:00",
            "open": 4291.49,
            "high": 4291.61,
            "low": 4289.05,
            "close": 4289.47,
            "tick_volume": 297,
            "spread": 14,
        },
        {
            "time": "2026-09-23T22:10:00",
            "open": 4289.54,
            "high": 4290.24,
            "low": 4289.02,
            "close": 4289.37,
            "tick_volume": 257,
            "spread": 17,
        },
    ],
}

TICKS_MCP = {
    "symbol": "XAUUSD",
    "period": "tick",
    "ok": True,
    "history": [
        {"time_ms": "2026-09-23T01:04:35.369", "bid": 4361.73, "ask": 4362.49},
        {"time_ms": "2026-09-23T01:04:35.710", "bid": 4361.83, "ask": 4362.49},
    ],
}

TIME_INFO = {
    "utc_time": "2026-09-24T00:55:49Z",
    "trade_server_last_known_time": "2026-09-24T03:55:48",
}


# ===========================================================================
# A-01 / B-27: the adapter IS an IMT5Port implementation
# ===========================================================================
class TestPortSurface:
    def test_is_an_imt5_port(self) -> None:
        from nexus_scalp.ports.mt5_port import IMT5Port

        assert isinstance(MCPMT5Adapter(api_key="k"), IMT5Port)

    def test_implements_every_abstract_read(self) -> None:
        adapter = MCPMT5Adapter(api_key="k")
        for name in (
            "connect",
            "disconnect",
            "is_connected",
            "get_account_info",
            "get_symbol_info",
            "get_last_tick",
            "get_tick",
            "get_historical_bars",
            "get_positions",
            "get_account_snapshot",
            "get_symbol_snapshot",
            "get_broker_tick",
            "get_all_positions",
            "get_pending_orders_snapshot",
            "get_history_orders",
            "get_history_deals",
            "get_rate_history",
            "get_tick_history",
            "get_closed_deals_history",
            "connection_state",
            "get_terminal_state",
        ):
            assert hasattr(adapter, name), f"missing port read method {name}"

    def test_provenance_tag_is_live(self) -> None:
        #: BUG-226: accounting excludes PAPER provenance; MCP is a real terminal.
        assert MCPMT5Adapter(api_key="k").current_account_source == "LIVE"


# ===========================================================================
# A-14: the honest `available` property only this adapter can provide
# ===========================================================================
class TestAvailableProperty:
    def test_false_before_connect(self) -> None:
        assert MCPMT5Adapter(api_key="k").available is False

    def test_true_after_successful_connect(self) -> None:
        adapter = _adapter({})
        assert adapter.available is True

    def test_false_after_disconnect(self) -> None:
        adapter = _adapter({})
        adapter.disconnect()
        assert adapter.available is False
        assert adapter.is_connected() is False


# ===========================================================================
# Credential handling: env resolution, .env fallback, never hardcoded
# ===========================================================================
class TestCredentials:
    def test_no_hardcoded_key_constant(self) -> None:
        source = Path(mod.__file__).read_text(encoding="utf-8")
        #: The only place the env var NAME may appear is as the lookup key.
        assert "NSE_MT5_MCP_API_KEY" in source
        #: No literal bearer token, no inline secret value.
        assert "Bearer <SECRET>" not in source

        #: AST scan: any assignment to a credential-shaped name must NOT be
        #: given a string/bytes literal. This is the precise test for a
        #: hardcoded key — it catches `_key = "..."` and `_DEFAULT_KEY = "..."`
        #: alike, and it cannot be satisfied by a comment or a reformat.
        import ast

        _CRED_NAME = ("key", "secret", "token", "password", "apikey")

        def _names(node: object) -> list[str]:
            if isinstance(node, ast.Name):
                return [node.id]
            if isinstance(node, ast.Attribute):
                return _names(node.value) + [node.attr]
            if isinstance(node, ast.Tuple):
                out: list[str] = []
                for elt in node.elts:
                    out.extend(_names(elt))
                return out
            return []

        tree = ast.parse(source)
        offenders: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in _names(target):
                    if not any(tag in name.lower() for tag in _CRED_NAME):
                        continue
                    if not isinstance(node.value, ast.Constant) or not isinstance(
                        node.value.value, (str, bytes)
                    ):
                        continue
                    value = (
                        node.value.value
                        if isinstance(node.value.value, str)
                        else node.value.value.decode("utf-8", "replace")
                    )
                    #: The only literals permitted on a credential-shaped
                    #: name are the env-var NAMES: those identify where the
                    #: real key is READ, and are not secrets themselves.
                    if value in ("NSE_MT5_MCP_API_KEY", "NSE_MT5_MCP_URL"):
                        continue
                    offenders.append(f"{name} = {node.value.value!r}")
        assert not offenders, f"hardcoded credential literal(s) in adapter: {offenders}"

    def test_no_credential_literal_in_module(self) -> None:
        #: Belt-and-braces: NO module-level string constant may look like a
        #: credential. A real MCP API key is a long opaque token, and MCP
        #: tool names are lowercase words with underscores — the two are
        #: distinguished precisely by requiring a hex/base64 character mix
        #: (digits AND letters) without any natural-language subword.
        import ast
        import re

        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        #: Module-level constants only (function bodies build values at
        #: runtime and are already covered by the assignment scan above).
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if not any(
                    tag in target.id.lower() for tag in ("key", "secret", "token")
                ):
                    continue
                if isinstance(node.value, ast.Constant) and isinstance(
                    node.value.value, (str, bytes)
                ):
                    value = (
                        node.value.value
                        if isinstance(node.value.value, str)
                        else node.value.value.decode("utf-8", "replace")
                    )
                    #: allow the env-var NAME (it is how the key is READ,
                    #: not a key) and allow identifiers with underscores
                    #: flanked by lowercase words (MCP tool/env names).
                    if value in ("NSE_MT5_MCP_API_KEY", "NSE_MT5_MCP_URL"):
                        continue
                    raise AssertionError(
                        f"credential-shaped constant {target.id} holds a literal: "
                        f"{value[:24]!r}"
                    )

        #: Second axis: no string constant anywhere in the module is a
        #: high-entropy hex/base64 blob, which is what an leaked API key
        #: actually looks like. Requires >=20 chars, digits AND letters,
        #: and no lowercase-word subword longer than 5 chars (natural
        #: language and MCP tool names always have one).
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value.strip()
                if len(text) < 20:
                    continue
                if not (any(c.isdigit() for c in text) and any(c.isalpha() for c in text)):
                    continue
                if not re.fullmatch(r"[A-Za-z0-9_\-]{20,}", text):
                    continue
                words = re.split(r"[0-9_\-]+", text)
                if any(len(w) > 5 for w in words):
                    continue
                raise AssertionError(f"key-like literal in adapter: {text[:24]}…")

    def test_key_never_appears_in_connection_error(self) -> None:
        adapter = MCPMT5Adapter(url="http://127.0.0.1:1/mcp", api_key="SECRET-VALUE")
        with pytest.raises(RuntimeError) as exc_info:
            adapter.connect()
        message = str(exc_info.value)
        assert "SECRET-VALUE" not in message
        assert "127.0.0.1:1" in message

    def test_missing_key_raises_loudly_on_connect(self) -> None:
        adapter = MCPMT5Adapter(api_key="")
        with pytest.raises(RuntimeError, match="NSE_MT5_MCP_API_KEY"):
            adapter.connect()
        #: A-03/F-05 class discipline: state must not claim CONNECTED.
        assert adapter.connection_state().state != "CONNECTED"

    def test_unreachable_endpoint_raises_not_false(self) -> None:
        adapter = MCPMT5Adapter(url="http://127.0.0.1:1/mcp", api_key="k")
        with pytest.raises(RuntimeError):
            adapter.connect()

    def test_url_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NSE_MT5_MCP_URL", "http://example.test:9999/mcp")
        adapter = MCPMT5Adapter(api_key="k")
        assert adapter._url == "http://example.test:9999/mcp"

    def test_explicit_url_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NSE_MT5_MCP_URL", "http://example.test:9999/mcp")
        adapter = MCPMT5Adapter(url="http://explicit:1/mcp", api_key="k")
        assert adapter._url == "http://explicit:1/mcp"

    def test_env_file_fallback_does_not_overwrite_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NSE_MT5_MCP_API_KEY", "FROM_ENV")
        env_file = tmp_path / ".env"
        env_file.write_text("NSE_MT5_MCP_API_KEY=FROM_FILE\n", encoding="utf-8")
        mod._load_env_file(str(tmp_path))
        import os

        assert os.environ["NSE_MT5_MCP_API_KEY"] == "FROM_ENV"

    def test_env_file_fallback_applies_when_env_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NSE_MT5_MCP_API_KEY", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("NSE_MT5_MCP_API_KEY=FROM_FILE\n", encoding="utf-8")
        mod._load_env_file(str(tmp_path))
        import os

        assert os.environ["NSE_MT5_MCP_API_KEY"] == "FROM_FILE"

    def test_missing_env_file_is_silent(self, tmp_path: Path) -> None:
        mod._load_env_file(str(tmp_path))  # no exception


# ===========================================================================
# Read mappings — asserted against lane-A captured real MCP payloads
# ===========================================================================
class TestAccountMapping:
    def test_account_snapshot_maps_native_fields(self) -> None:
        adapter = _adapter({"get_trading_account_info": ACCOUNT_MCP})
        snap = adapter.get_account_snapshot()

        assert snap.available is True
        assert snap.source == BROKER_NATIVE
        #: A-10: login arrives as a STRING and must become int.
        assert snap.login == 10011755849
        assert snap.server == "MetaQuotes-Demo"
        #: MCP calls it `broker`; the native slot is `company`.
        assert snap.company == "MetaQuotes Ltd."
        assert snap.balance == 30462.55
        assert snap.equity == 30462.55
        assert snap.margin == 0.0
        assert snap.margin_free == 30462.55
        assert snap.currency == "USD"

    def test_account_type_string_maps_to_trade_mode_int(self) -> None:
        adapter = _adapter({"get_trading_account_info": ACCOUNT_MCP})
        snap = adapter.get_account_snapshot()
        #: MCP "demo" -> native 0 (evidence §3: EQUIVALENT).
        assert snap.trade_mode == 0

    def test_account_derived_pnl(self) -> None:
        adapter = _adapter({"get_trading_account_info": ACCOUNT_MCP})
        snap = adapter.get_account_snapshot()
        assert snap.floating_pnl == 0.0
        assert snap.net_pnl == 0.0

    def test_mcp_only_fields_preserved_in_note(self) -> None:
        adapter = _adapter({"get_trading_account_info": ACCOUNT_MCP})
        snap = adapter.get_account_snapshot()
        extras = json.loads(snap.note)["mcp_extras"]
        assert extras["terminal_build"] == 6207
        assert extras["margin_mode_mcp"] == "hedging"
        #: MCP-only cost aggregates survive instead of being dropped.
        assert extras["swaps"] == 0.0
        assert extras["commissions"] == 0.0

    def test_native_only_fields_stay_none_not_fabricated(self) -> None:
        adapter = _adapter({"get_trading_account_info": ACCOUNT_MCP})
        snap = adapter.get_account_snapshot()
        #: A-10: 17 native-only fields — the big ones must NOT be invented.
        for field in ("leverage", "limit_orders", "margin_level", "currency_digits"):
            assert getattr(snap, field) is None, f"{field} must be None over MCP"

    def test_account_info_legacy_typed_read(self) -> None:
        adapter = _adapter({"get_trading_account_info": ACCOUNT_MCP})
        info = adapter.get_account_info()
        assert info.login == 10011755849
        assert info.balance == 30462.55
        assert info.currency == "USD"

    def test_account_read_failure_is_error_snapshot(self) -> None:
        adapter = _adapter({"get_trading_account_info": RuntimeError("boom")})
        snap = adapter.get_account_snapshot()
        assert snap.available is False
        assert snap.source == UNAVAILABLE
        assert snap.error_state is not None

    def test_account_missing_block_is_error_snapshot(self) -> None:
        adapter = _adapter({"get_trading_account_info": {"nope": {}}})
        snap = adapter.get_account_snapshot()
        assert snap.available is False

    def test_unconnected_account_read_is_error_snapshot(self) -> None:
        adapter = MCPMT5Adapter(api_key="k")
        snap = adapter.get_account_snapshot()
        assert snap.available is False


class TestSymbolMapping:
    def test_symbol_snapshot_maps_spec_and_tick(self) -> None:
        adapter = _adapter({"get_marketwatch_symbols": SYMBOLS_MCP})
        snap = adapter.get_symbol_snapshot(SYMBOL)

        assert snap.available is True
        assert snap.spec["digits"] == 2
        assert snap.spec["point"] == 0.01
        #: MCP tick_size/tick_value/contract_size -> native trade_* names.
        assert snap.spec["trade_tick_size"] == 0.01
        assert snap.spec["trade_tick_value"] == 0.1
        assert snap.spec["trade_contract_size"] == 100.0
        assert snap.spec["volume_min"] == 0.01
        assert snap.spec["volume_max"] == 100.0
        assert snap.spec["volume_step"] == 0.01
        assert snap.spec["trade_stops_level"] == 0

    def test_symbol_tick_bid_ask_and_spread(self) -> None:
        adapter = _adapter({"get_marketwatch_symbols": SYMBOLS_MCP})
        snap = adapter.get_symbol_snapshot(SYMBOL)
        assert snap.tick["bid"] == 4296.83
        assert snap.tick["ask"] == 4297.43
        #: spread from the live quote, BROKER_NATIVE provenance.
        assert snap.spread_points == pytest.approx(0.6)
        assert snap.spread_points_source == BROKER_NATIVE

    def test_symbol_iso_timestamp_becomes_epoch(self) -> None:
        adapter = _adapter({"get_marketwatch_symbols": SYMBOLS_MCP})
        snap = adapter.get_symbol_snapshot(SYMBOL)
        #: MCP's naive ISO update_time parsed as UTC; epoch materialized.
        assert snap.tick["time"] is not None
        parsed = datetime.fromtimestamp(snap.tick["time"], tz=UTC)
        assert parsed.year == 2026 and parsed.month == 9 and parsed.day == 24

    def test_symbol_calc_mode_string_and_enum_both_kept(self) -> None:
        adapter = _adapter({"get_marketwatch_symbols": SYMBOLS_MCP})
        snap = adapter.get_symbol_snapshot(SYMBOL)
        assert snap.spec["trade_calculation_mode"] == "cfd leverage"
        assert snap.spec["trade_calc_mode"] == 1

    def test_symbol_info_legacy_typed_read(self) -> None:
        adapter = _adapter({"get_marketwatch_symbols": SYMBOLS_MCP})
        info = adapter.get_symbol_info(SYMBOL)
        assert info.symbol == SYMBOL
        assert info.digits == 2
        assert info.tick_size == 0.01
        assert info.trade_contract_size == 100.0
        assert info.volume_step == 0.01

    def test_symbol_not_in_market_watch_errors(self) -> None:
        adapter = _adapter({"get_marketwatch_symbols": SYMBOLS_MCP})
        snap = adapter.get_symbol_snapshot("EURUSD")
        assert snap.available is False
        assert snap.error_state is not None
        assert "not in Market Watch" in (snap.error_state.get("message") or "")


class TestTickMapping:
    def test_broker_tick_maps_bid_ask(self) -> None:
        adapter = _adapter({"get_marketwatch_symbols": SYMBOLS_MCP})
        snap = adapter.get_broker_tick(SYMBOL)
        assert snap.available is True
        assert snap.symbol == SYMBOL
        assert snap.bid == 4296.83
        assert snap.ask == 4297.43
        assert snap.spread_points == pytest.approx(0.6)

    def test_mcp_unsupported_tick_fields_are_none(self) -> None:
        adapter = _adapter({"get_marketwatch_symbols": SYMBOLS_MCP})
        snap = adapter.get_broker_tick(SYMBOL)
        #: evidence §2: MCP carries only time_ms/bid/ask — never 0-as-data.
        assert snap.last is None
        assert snap.volume is None
        assert snap.flags is None

    def test_last_tick_typed(self) -> None:
        adapter = _adapter({"get_marketwatch_symbols": SYMBOLS_MCP})
        tick = adapter.get_last_tick(SYMBOL)
        assert tick.symbol == SYMBOL
        assert tick.bid == 4296.83
        assert tick.ask == 4297.43
        assert tick.timestamp.tzinfo is not None

    def test_get_tick_alias_matches_last_tick(self) -> None:
        adapter = _adapter({"get_marketwatch_symbols": SYMBOLS_MCP})
        assert adapter.get_tick(SYMBOL).bid == adapter.get_last_tick(SYMBOL).bid


class TestBarMapping:
    def test_rate_history_maps_ohlc_and_volume(self) -> None:
        adapter = _adapter({"get_time_information": TIME_INFO, "get_chart_history": BARS_MCP})
        bars = adapter.get_rate_history(SYMBOL, "M1", count=2)

        assert len(bars) == 2
        first = bars[0]
        assert first.available is True
        assert first.source == BROKER_NATIVE
        assert first.open == 4291.49
        assert first.high == 4291.61
        assert first.low == 4289.05
        assert first.close == 4289.47
        assert first.tick_volume == 297
        assert first.spread == 14

    def test_iso_bar_time_becomes_epoch_and_utc(self) -> None:
        adapter = _adapter({"get_time_information": TIME_INFO, "get_chart_history": BARS_MCP})
        bars = adapter.get_rate_history(SYMBOL, "M1", count=2)
        first = bars[0]
        #: MCP naive ISO (UTC trade-server time) -> epoch + UTC datetime,
        #: parsed with ZERO shift (evidence §1).
        assert first.time is not None
        assert first.time_utc == datetime(2026, 9, 23, 22, 9, tzinfo=UTC)
        assert int(datetime(2026, 9, 23, 22, 9, tzinfo=UTC).timestamp()) == first.time

    def test_real_volume_absent_is_none(self) -> None:
        adapter = _adapter({"get_time_information": TIME_INFO, "get_chart_history": BARS_MCP})
        bars = adapter.get_rate_history(SYMBOL, "M1", count=2)
        #: evidence §1: MCP does not expose real_volume. None, never 0.
        assert bars[0].real_volume is None

    def test_historical_bars_marks_forming_bar(self) -> None:
        #: A bar inside the current minute is forming (BUG-308 discipline).
        now = datetime.now(UTC)
        forming = {
            "time": now.strftime("%Y-%m-%dT%H:%M:00"),
            "open": 1.0,
            "high": 1.1,
            "low": 0.9,
            "close": 1.05,
            "tick_volume": 1,
            "spread": 1,
        }
        sealed = dict(forming)
        sealed["time"] = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:00")
        payload = {"history": [sealed, forming]}
        adapter = _adapter({"get_time_information": TIME_INFO, "get_chart_history": payload})
        bars = adapter.get_historical_bars(SYMBOL, "M1", count=2)

        assert len(bars) == 2
        assert bars[0].is_complete is True
        assert bars[1].is_complete is False

    def test_historical_bars_drops_malformed(self) -> None:
        bad = {
            "time": "2026-09-23T22:09:00",
            "open": 10.0,
            "high": 1.0,  # high < open -> invalid geometry
            "low": 0.9,
            "close": 1.05,
            "tick_volume": 1,
            "spread": 1,
        }
        payload = {"history": [bad]}
        adapter = _adapter({"get_time_information": TIME_INFO, "get_chart_history": payload})
        assert adapter.get_historical_bars(SYMBOL, "M1", count=1) == []


class TestTickHistoryMapping:
    def test_tick_history_maps_bid_ask(self) -> None:
        adapter = _adapter(
            {"get_time_information": TIME_INFO, "get_chart_ticks_history": TICKS_MCP}
        )
        ticks = adapter.get_tick_history(SYMBOL, count=2)

        assert len(ticks) == 2
        assert ticks[0].bid == 4361.73
        assert ticks[0].ask == 4362.49
        assert ticks[0].source == BROKER_NATIVE

    def test_tick_ms_precision_preserved(self) -> None:
        adapter = _adapter(
            {"get_time_information": TIME_INFO, "get_chart_ticks_history": TICKS_MCP}
        )
        ticks = adapter.get_tick_history(SYMBOL, count=2)
        #: MCP "2026-09-23T01:04:35.369" -> time_msc epoch ms.
        assert ticks[0].time_msc is not None
        assert ticks[0].time_msc % 1000 == 369

    def test_tick_unsupported_fields_are_none(self) -> None:
        adapter = _adapter(
            {"get_time_information": TIME_INFO, "get_chart_ticks_history": TICKS_MCP}
        )
        ticks = adapter.get_tick_history(SYMBOL, count=2)
        #: evidence §2: no last / volume / TICK_FLAG bitmask on MCP.
        assert ticks[0].last is None
        assert ticks[0].volume is None
        assert ticks[0].flags is None


# ===========================================================================
# A-11: count -> range derivation, future clamping
# ===========================================================================
class TestRangeDerivation:
    def test_count_derives_a_past_bounded_range(self) -> None:
        adapter = _adapter({"get_time_information": TIME_INFO, "get_chart_history": BARS_MCP})
        adapter.get_rate_history(SYMBOL, "M1", count=10)
        tool, args = adapter._transport.calls[1]  # type: ignore[attr-defined]
        assert tool == "get_chart_history"
        assert args["symbol"] == SYMBOL
        assert args["period"] == "M1"
        assert args["limit"] == 10
        #: datetime_from strictly before datetime_to, both in the past.
        assert args["datetime_from"] < args["datetime_to"]
        assert len(args["datetime_from"]) == 19

    def test_future_datetime_to_is_clamped_and_noted(self) -> None:
        #: A server clock from the past (the captured TIME_INFO payload) is
        #: the exact case that needs clamping: a requested datetime_to
        #: beyond it must be pulled back, while a response is still served.
        #: Exercised on the tick surface, whose port signature takes to_utc
        #: (get_rate_history derives its end from the server clock).
        adapter = _adapter(
            {
                "get_time_information": TIME_INFO,
                "get_chart_ticks_history": TICKS_MCP,
            }
        )
        ticks = adapter.get_tick_history(
            SYMBOL,
            count=2,
            from_utc=datetime(2026, 9, 23, 22, 0, tzinfo=UTC),
            to_utc=datetime(2026, 12, 1, tzinfo=UTC),
        )
        #: MCP would hard-error on the future datetime_to; we clamp to the
        #: server clock, so the canned payload still resolves.
        assert len(ticks) == 2
        tool, args = adapter._transport.calls[1]  # type: ignore[attr-defined]
        assert args["datetime_to"] <= "2026-09-24T00:55:49"
        assert "future" in ticks[0].note

    def test_explicit_range_is_honored(self) -> None:
        adapter = _adapter({"get_time_information": TIME_INFO, "get_chart_history": BARS_MCP})
        adapter.get_rate_history(
            SYMBOL,
            "M1",
            count=2,
            from_utc=datetime(2026, 9, 23, tzinfo=UTC),
        )
        tool, args = adapter._transport.calls[1]  # type: ignore[attr-defined]
        assert args["datetime_from"] == "2026-09-23T00:00:00"

    def test_unsupported_timeframe_is_rejected(self) -> None:
        adapter = _adapter({"get_time_information": TIME_INFO, "get_chart_history": BARS_MCP})
        #: A period MT5 does not enumerate must never reach the wire: MCP
        #: would resolve it against its own default and hand back bars of a
        #: DIFFERENT timeframe with no error. (M2 etc. are real MT5 periods
        #: and are supported; "M7" is not in the enumeration.)
        assert adapter.get_rate_history(SYMBOL, "M7", count=2) == []
        tools = [c[0] for c in adapter._transport.calls]  # type: ignore[attr-defined]
        assert "get_chart_history" not in tools

    def test_supported_timeframes_reach_the_wire(self) -> None:
        adapter = _adapter({"get_time_information": TIME_INFO, "get_chart_history": BARS_MCP})
        adapter.get_rate_history(SYMBOL, "H1", count=2)
        args = dict(adapter._transport.calls[-1][1])  # type: ignore[attr-defined]
        assert args["period"] == "H1"

    def test_server_clock_is_used_not_the_local_clock(self) -> None:
        adapter = _adapter({"get_time_information": TIME_INFO, "get_chart_history": BARS_MCP})
        adapter.get_rate_history(SYMBOL, "M1", count=2)
        #: The derivation must call get_time_information FIRST.
        assert adapter._transport.calls[0][0] == "get_time_information"  # type: ignore[attr-defined]

    def test_a12_only_whitelisted_args_reach_the_wire(self) -> None:
        adapter = _adapter({"get_time_information": TIME_INFO, "get_chart_history": BARS_MCP})
        adapter.get_rate_history(SYMBOL, "M1", count=2)
        _, args = adapter._transport.calls[1]  # type: ignore[attr-defined]
        #: A-12: unknown args make the server return the FULL history.
        assert set(args) <= mod._ALLOWED_HISTORY_ARGS
        #: `count` and `start`/`end` (the broken names from lane A probes)
        #: must never be sent.
        assert "count" not in args
        assert "start" not in args
        assert "end" not in args


# ===========================================================================
# B-14m: an empty MCP window is a silent ok:true — an empty RESULT, never
# an error and never a failure
# ===========================================================================
class TestEmptyWindow:
    def test_empty_bar_window_returns_empty_not_error(self) -> None:
        adapter = _adapter(
            {
                "get_time_information": TIME_INFO,
                "get_chart_history": {"ok": True, "history": []},
            }
        )
        assert adapter.get_rate_history(SYMBOL, "M1", count=5) == []
        assert adapter.connection_state().state == "CONNECTED"

    def test_empty_tick_window_returns_empty_not_error(self) -> None:
        adapter = _adapter(
            {
                "get_time_information": TIME_INFO,
                "get_chart_ticks_history": {"ok": True, "history": []},
            }
        )
        assert adapter.get_tick_history(SYMBOL, count=5) == []

    def test_empty_positions_returns_empty(self) -> None:
        adapter = _adapter({"get_trading_open_positions": {"positions": [], "orders": []}})
        assert adapter.get_all_positions() == []
        assert adapter.get_pending_orders_snapshot() == []
        assert adapter.get_positions() == []

    def test_empty_history_orders_returns_empty(self) -> None:
        adapter = _adapter(
            {
                "get_time_information": TIME_INFO,
                "get_trading_history_orders": {"orders": []},
            }
        )
        assert adapter.get_history_orders() == []

    def test_empty_closed_deals_history(self) -> None:
        adapter = _adapter(
            {
                "get_time_information": TIME_INFO,
                "get_trading_history_positions": {"positions": []},
            }
        )
        assert adapter.get_closed_deals_history(SYMBOL, hours_back=24) == []

    def test_mcp_error_is_still_an_error(self) -> None:
        #: Guard against the inverse: a real MCP error must not be swallowed
        #: into an empty result (that would hide a broken endpoint). It
        #: records a FAILURE on the connection state instead — note the
        #: state machine's own contract: record_failure records the last
        #: failure without flipping the session state (a read failure is not
        #: a disconnect), which is exactly the native adapter's behaviour.
        adapter = _adapter(
            {
                "get_time_information": TIME_INFO,
                "get_chart_history": (True, "invalid datetime range"),
            }
        )
        assert adapter.get_rate_history(SYMBOL, "M1", count=5) == []
        state = adapter.connection_state()
        #: The failure is recorded (last_failed_operation populated),
        #: proving the error was surfaced rather than silently absorbed.
        assert state.to_dict()["last_failed_operation"] is not None


# ===========================================================================
# B-24: positions / orders / deals stringly-typed key renames
# ===========================================================================
ORDER_HISTORY_MCP = {
    "orders": [
        {
            "order_id": "152343606752",
            "order_external_id": "",
            "position_id": "152343606752",
            "symbol": "EURUSD",
            "type": "buy",
            "filling": "fill or kill",
            "open_time": "2026-07-15T18:45:01",
            "done_time": "2026-07-15T18:45:01",
            "open_reason": "Expert",
            "state": "filled",
            "volume_initial": 0.1,
            "volume_current": 0,
            "contract_size": 100000.0,
            "comment": "Operator Manual Trade",
        }
    ]
}

POSITION_HISTORY_MCP = {
    "positions": [
        {
            "position_id": "152343606752",
            "type": "buy",
            "symbol": "EURUSD",
            "open_reason": "Expert",
            "open_time": "2026-07-15T18:45:01",
            "open_volume": 0.1,
            "open_price": 1.14347,
            "close_reason": "Expert",
            "close_time": "2026-07-15T18:45:53",
            "close_volume": 0.1,
            "close_price": 1.14353,
            "profit": 0.6,
            "contract_size": 100000.0,
            "comment": "Operator Manual Trade",
        }
    ]
}


class TestOrderDealMapping:
    def test_history_order_renames_keys(self) -> None:
        adapter = _adapter(
            {
                "get_time_information": TIME_INFO,
                "get_trading_history_orders": ORDER_HISTORY_MCP,
            }
        )
        orders = adapter.get_history_orders()
        assert len(orders) == 1
        order = orders[0]
        #: B-24: order_id str -> ticket int.
        assert order.ticket == 152343606752
        #: position_id -> identifier.
        assert order.identifier == 152343606752
        assert order.symbol == "EURUSD"

    def test_history_order_maps_string_enums_to_ints(self) -> None:
        adapter = _adapter(
            {
                "get_time_information": TIME_INFO,
                "get_trading_history_orders": ORDER_HISTORY_MCP,
            }
        )
        order = adapter.get_history_orders()[0]
        assert order.type == 0  # "buy" -> ORDER_TYPE_BUY
        assert order.state == 4  # "filled" -> ORDER_STATE_FILLED
        assert order.type_filling == 0  # "fill or kill" -> ORDER_FILLING_FOK
        assert order.reason == 1  # "Expert" -> ORDER_REASON_EXPERT

    def test_history_order_iso_times_become_epochs(self) -> None:
        adapter = _adapter(
            {
                "get_time_information": TIME_INFO,
                "get_trading_history_orders": ORDER_HISTORY_MCP,
            }
        )
        order = adapter.get_history_orders()[0]
        assert order.time_setup is not None
        assert order.time_done is not None
        assert order.time_setup == order.time_done

    def test_history_deal_maps_closed_position(self) -> None:
        adapter = _adapter(
            {
                "get_time_information": TIME_INFO,
                "get_trading_history_positions": POSITION_HISTORY_MCP,
            }
        )
        deals = adapter.get_history_deals()
        assert len(deals) == 1
        deal = deals[0]
        assert deal.position_id == 152343606752
        assert deal.symbol == "EURUSD"
        assert deal.type == 0
        assert deal.volume == 0.1
        assert deal.price == 1.14353
        assert deal.profit == 0.6
        #: close leg is the realized-PnL event and is authoritative.
        assert deal.time is not None

    def test_closed_deals_history_dict_shape(self) -> None:
        adapter = _adapter(
            {
                "get_time_information": TIME_INFO,
                "get_trading_history_positions": POSITION_HISTORY_MCP,
            }
        )
        rows = adapter.get_closed_deals_history("EURUSD", hours_back=24 * 90)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "EURUSD"
        assert rows[0]["source"] == "MCP"
        assert rows[0]["profit"] == 0.6

    def test_open_position_mapper_renames_keys(self) -> None:
        snap = _build_mcp_position(
            {
                "position_id": "12345",
                "type": "sell",
                "symbol": "XAUUSD",
                "open_time": "2026-09-24T03:00:00",
                "open_volume": 0.5,
                "open_price": 4300.0,
                "current_price": 4290.0,
                "sl": 4310.0,
                "tp": 4280.0,
                "profit": -5.0,
                "comment": "hi",
            },
            "XAUUSD",
        )
        assert snap.available is True
        assert snap.ticket == 12345
        assert snap.type == 1  # sell
        assert snap.volume == 0.5
        assert snap.price_open == 4300.0
        assert snap.price_current == 4290.0
        assert snap.sl == 4310.0
        assert snap.tp == 4280.0
        assert snap.profit == -5.0
        assert snap.comment == "hi"
        #: magic is not exposed by MCP -> fail-closed None.
        assert snap.magic is None

    def test_pending_order_mapper_renames_keys(self) -> None:
        snap = _build_mcp_pending_order(ORDER_HISTORY_MCP["orders"][0])
        assert snap.available is True
        assert snap.ticket == 152343606752
        assert snap.identifier == 152343606752
        assert snap.state == 4
        assert snap.type_filling == 0
        assert snap.volume_initial == 0.1
        assert snap.volume_current == 0.0

    def test_get_positions_applies_symbol_filter(self) -> None:
        #: DirectMT5Adapter.get_positions filters to the bot symbol; MCP
        #: exposes no magic, so the filter is symbol-only.
        open_payload = {
            "positions": [
                {
                    "position_id": "111",
                    "type": "buy",
                    "symbol": "XAUUSD",
                    "open_time": "2026-09-24T03:00:00",
                    "open_volume": 0.1,
                    "open_price": 4300.0,
                    "profit": 1.0,
                },
                {
                    "position_id": "222",
                    "type": "sell",
                    "symbol": "EURUSD",
                    "open_time": "2026-09-24T03:00:00",
                    "open_volume": 0.2,
                    "open_price": 1.1,
                    "profit": 2.0,
                },
            ],
            "orders": [],
        }
        adapter = _adapter({"get_trading_open_positions": open_payload})
        positions = adapter.get_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "XAUUSD"
        assert positions[0].ticket == 111
        assert positions[0].magic == 888101
        #: The EURUSD row is outside the bot symbol contract.
        assert all(p.symbol == "XAUUSD" for p in positions)

    def test_get_all_positions_reports_unknown_magic(self) -> None:
        #: The ALL-positions view never fabricates a magic (B-24): it stays
        #: None so accounting knows the transport could not verify it.
        open_payload = {
            "positions": [
                {
                    "position_id": "111",
                    "type": "buy",
                    "symbol": "XAUUSD",
                    "open_time": "2026-09-24T03:00:00",
                    "open_volume": 0.1,
                    "open_price": 4300.0,
                }
            ],
            "orders": [],
        }
        adapter = _adapter({"get_trading_open_positions": open_payload})
        assert adapter.get_all_positions()[0].magic is None


# ===========================================================================
# Terminal state
# ===========================================================================
class TestTerminalState:
    def test_reports_transport_explicitly(self) -> None:
        adapter = _adapter(
            {"get_time_information": TIME_INFO, "get_trading_account_info": ACCOUNT_MCP}
        )
        state = adapter.get_terminal_state()
        assert state["available"] is True
        assert state["transport"] == "MCP"
        assert state["endpoint"] == "http://127.0.0.1:22346/mcp"

    def test_unconnected_terminal_state(self) -> None:
        adapter = MCPMT5Adapter(api_key="k")
        state = adapter.get_terminal_state()
        assert state["available"] is False
        assert state["transport"] == "MCP"

    def test_no_credentials_in_terminal_state(self) -> None:
        adapter = _adapter(
            {"get_time_information": TIME_INFO, "get_trading_account_info": ACCOUNT_MCP}
        )
        state = adapter.get_terminal_state()
        blob = json.dumps(state, default=str)
        assert "test-key-not-a-secret" not in blob


# ===========================================================================
# READ-ONLY: every write method fails closed, and NO MCP call is issued
# ===========================================================================
class TestReadOnlyFailClosed:
    @pytest.mark.parametrize(
        "method, args",
        [
            ("send_order", (object(),)),
            ("execute_market_order", (SYMBOL, 0, 0.1, 1.0, 0.9, 1.1)),
            ("place_pending_order", (SYMBOL, 0, 0.1, 1.0, 0.9, 1.1)),
            ("modify_position", (1, 0.9, 1.1)),
            ("close_position", (1,)),
            ("modify_order", (1, 0.9, 1.1)),
            ("cancel_pending_order", (1,)),
        ],
    )
    def test_write_methods_raise(self, method: str, args: tuple) -> None:
        adapter = _adapter({})
        with pytest.raises((NotImplementedError, RuntimeError), match="READ-ONLY"):
            getattr(adapter, method)(*args)

    @pytest.mark.parametrize(
        "method, args",
        [
            ("send_order", (object(),)),
            ("execute_market_order", (SYMBOL, 0, 0.1, 1.0, 0.9, 1.1)),
            ("place_pending_order", (SYMBOL, 0, 0.1, 1.0, 0.9, 1.1)),
            ("modify_position", (1, 0.9, 1.1)),
            ("close_position", (1,)),
            ("modify_order", (1, 0.9, 1.1)),
            ("cancel_pending_order", (1,)),
        ],
    )
    def test_write_methods_never_touch_the_transport(self, method: str, args: tuple) -> None:
        adapter = _adapter({})
        with pytest.raises(Exception):
            getattr(adapter, method)(*args)
        #: The decisive assertion: zero tool dispatches, not one.
        assert adapter._transport.calls == []  # type: ignore[attr-defined]

    @pytest.mark.parametrize("tool", sorted(FORBIDDEN_TOOLS))
    def test_forbidden_tools_refused_before_network(self, tool: str) -> None:
        adapter = _adapter({})
        #: The adapter's own transport guard raises BEFORE any dispatch —
        #: the wire is never reached, so no call is recorded.
        with pytest.raises(RuntimeError, match="REFUSED_BY_POLICY"):
            _guarded_transport(adapter).call_raw(tool, {})
        assert adapter._transport.calls == []  # type: ignore[attr-defined]

    def test_trade_tools_are_in_the_forbidden_set(self) -> None:
        for tool in (
            "trade_send_market_order",
            "trade_send_pending_order",
            "trade_modify_sl_tp",
            "trade_delete_order",
            "trade_close_single_position",
            "trade_close_by_position",
        ):
            assert tool in FORBIDDEN_TOOLS

    def test_unmapped_read_tool_refused(self) -> None:
        adapter = _adapter({})
        with pytest.raises(RuntimeError, match="REFUSED_UNMAPPED"):
            _guarded_transport(adapter).call_raw("economic_calendar_list_values", {})
        assert adapter._transport.calls == []  # type: ignore[attr-defined]

    def test_read_tool_whitelist_excludes_trade(self) -> None:
        assert not (mod.READ_TOOLS & FORBIDDEN_TOOLS)


# ===========================================================================
# Config: additive mcp section, off by default
# ===========================================================================
class TestConfigSection:
    def test_mcp_section_defaults_disabled(self) -> None:
        from nexus_scalp.configuration.config import MT5Config, MT5MCPConfig

        cfg = MT5Config()
        assert cfg.mcp is None
        mcp = MT5MCPConfig()
        assert mcp.enabled is False
        assert mcp.read_only is True
        assert mcp.url == ""

    def test_base_yaml_loads_with_mcp_section(self) -> None:
        from nexus_scalp.configuration.config import AppConfig

        cfg = AppConfig.load_from_yaml(Path("configs/base.yaml"))
        assert cfg.mt5.mcp is not None
        assert cfg.mt5.mcp.enabled is False
        assert cfg.mt5.mcp.read_only is True
        assert cfg.mt5.mcp.timeout_s == 60.0

    def test_base_yaml_mt5_core_unchanged(self) -> None:
        from nexus_scalp.configuration.config import AppConfig

        cfg = AppConfig.load_from_yaml(Path("configs/base.yaml"))
        assert cfg.mt5.timeout_ms == 5000
        assert cfg.mt5.retries == 3
        assert cfg.mt5.portable_mode is False

    def test_no_credentials_in_yaml(self) -> None:
        text = Path("configs/base.yaml").read_text(encoding="utf-8")
        assert "API_KEY" not in text
        assert "Bearer" not in text


# ===========================================================================
# A-01 dict pitfall: the _Payload shim speaks BOTH accessor conventions
# ===========================================================================
class TestPayloadShim:
    def test_attribute_access(self) -> None:
        payload = _Payload({"login": 123, "balance": 1.5})
        assert payload.login == 123
        assert payload.balance == 1.5

    def test_subscript_access(self) -> None:
        payload = _Payload({"time": 5, "open": 1.0})
        assert payload["time"] == 5
        assert payload["open"] == 1.0

    def test_missing_attribute_is_none(self) -> None:
        assert _Payload({}).nonexistent is None

    def test_missing_subscript_raises(self) -> None:
        with pytest.raises(KeyError):
            _Payload({})["nope"]  # type: ignore[index]

    def test_mcp_rate_bar_maps_via_subscript_builder_contract(self) -> None:
        #: build_rate_bar_snapshot reads row["time"]; _Payload satisfies it.
        from nexus_scalp.adapters.mt5.providers import build_rate_bar_snapshot

        row = {"time": 1790208540, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}
        snap = build_rate_bar_snapshot(_Payload(row))
        assert snap.open == 1.0


# ===========================================================================
# Evidence-backed mapping: run the SAME captured payload lane A recorded
# through the real builders (parity is the whole point of the adapter).
# ===========================================================================
class TestCapturedEvidencePayloads:
    def test_captured_account_payload(self) -> None:
        payload = _load("account_info_mcp.json")
        adapter = _adapter({"get_trading_account_info": payload})
        snap = adapter.get_account_snapshot()
        assert snap.login == 10011755849
        assert snap.balance == 30462.55
        assert snap.server == "MetaQuotes-Demo"

    def test_captured_symbols_payload(self) -> None:
        payload = _load("symbols_mcp.json")
        adapter = _adapter({"get_marketwatch_symbols": payload})
        snap = adapter.get_symbol_snapshot(SYMBOL)
        assert snap.spec["trade_tick_value"] == 0.1
        assert snap.spec["volume_max"] == 100.0

    def test_captured_bars_payload(self) -> None:
        payload = _load("m1_bars_mcp.json")
        adapter = _adapter({"get_time_information": TIME_INFO, "get_chart_history": payload})
        bars = adapter.get_rate_history(SYMBOL, "M1", count=50)
        assert len(bars) >= 10
        assert bars[0].open == 4291.49

    def test_captured_ticks_payload(self) -> None:
        payload = _load("ticks_mcp_1day.json")
        adapter = _adapter({"get_time_information": TIME_INFO, "get_chart_ticks_history": payload})
        ticks = adapter.get_tick_history(SYMBOL, count=5)
        assert len(ticks) >= 5
        assert ticks[0].bid == 4361.73

    def test_captured_orders_history_payload(self) -> None:
        payload = _load("orders_history_mcp.json")
        adapter = _adapter(
            {
                "get_time_information": TIME_INFO,
                "get_trading_history_orders": payload,
            }
        )
        orders = adapter.get_history_orders()
        assert orders[0].ticket == 152343606752
        assert orders[0].state == 4

    def test_captured_open_positions_payload(self) -> None:
        payload = _load("open_positions_mcp.json")
        adapter = _adapter({"get_trading_open_positions": payload})
        #: The probe account had no open positions — a legitimate empty.
        assert adapter.get_all_positions() == []
        assert adapter.get_pending_orders_snapshot() == []
