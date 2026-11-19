"""Broker-change symbol auto-repair (XAUUSD -> XAUUSD_i / GOLD).

A broker switch leaves execution.symbol naming an instrument the new server
does not know; every symbol_info / symbol_info_tick read then returns None
("Terminal: Not found", MT5 code -4) and get_symbol_info raises at boot.
Resolution must:
  * return the broker-truth name for a configured alias,
  * prefer the exact name when the broker has it (never rewrite a match),
  * return None (fail closed, never guess) when nothing tradeable exists,
  * ignore symbols the broker has disabled for trading.

These tests stub the native MT5 driver module so they run without a terminal.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from nexus_scalp.adapters.mt5 import mt5_adapter as mod
from nexus_scalp.application.live.runtime_loop import RuntimeLoop


class _FakeSymbols:
    """Minimal stand-in for the MetaTrader5 driver module."""

    def __init__(self, names: list[tuple[str, int]]) -> None:
        # name -> trade_mode (0 = SYMBOL_TRADE_MODE_DISABLED)
        self._rows = [SimpleNamespace(name=n, trade_mode=m) for n, m in names]

    def symbols_get(self) -> Any:
        return list(self._rows)


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> mod.DirectMT5Adapter:
    a = mod.DirectMT5Adapter()
    a._connected = True
    monkeypatch.setattr(mod, "HAS_NATIVE_MT5", True)
    monkeypatch.setattr(mod, "mt5", _FakeSymbols([]))
    return a


def _with(adapter: mod.DirectMT5Adapter, names: list[tuple[str, int]]) -> mod.DirectMT5Adapter:
    mod.mt5 = _FakeSymbols(names)
    return adapter


class TestResolveSymbol:
    def test_exact_match_returned_verbatim(self, adapter: mod.DirectMT5Adapter) -> None:
        a = _with(adapter, [("XAUUSD", 1), ("EURUSD", 1)])
        # An exact match must NEVER be rewritten to a different spelling.
        assert a.resolve_symbol("XAUUSD") == "XAUUSD"

    def test_case_insensitive_exact_match(self, adapter: mod.DirectMT5Adapter) -> None:
        a = _with(adapter, [("XAUUSD_i", 1)])
        assert a.resolve_symbol("xauusd_i") == "XAUUSD_i"

    def test_suffix_alias_resolves_to_broker_name(self, adapter: mod.DirectMT5Adapter) -> None:
        # The reported crash: configured XAUUSD, broker exposes XAUUSD_i.
        a = _with(adapter, [("XAUUSD_i", 1), ("EURUSD", 1)])
        assert a.resolve_symbol("XAUUSD") == "XAUUSD_i"

    def test_prefix_suffix_broker_spelling(self, adapter: mod.DirectMT5Adapter) -> None:
        a = _with(adapter, [("GOLD", 1), ("XAUUSD_i", 1)])
        # No lexical overlap with GOLD -> the XAUUSD_i whole-word candidate wins.
        assert a.resolve_symbol("XAUUSD") == "XAUUSD_i"

    def test_shortest_candidate_wins_on_tie(self, adapter: mod.DirectMT5Adapter) -> None:
        a = _with(adapter, [("XAUUSD_i", 1), ("XAUUSD_i.pro", 1)])
        assert a.resolve_symbol("XAUUSD") == "XAUUSD_i"

    def test_disabled_symbol_skipped(self, adapter: mod.DirectMT5Adapter) -> None:
        a = _with(adapter, [("XAUUSD_i", 0), ("XAUUSD_m", 1)])
        assert a.resolve_symbol("XAUUSD") == "XAUUSD_m"

    def test_no_candidate_returns_none(self, adapter: mod.DirectMT5Adapter) -> None:
        a = _with(adapter, [("EURUSD", 1), ("GBPUSD", 1)])
        # Fail closed: never fabricate a symbol name.
        assert a.resolve_symbol("XAUUSD") is None

    def test_empty_symbol_returns_none(self, adapter: mod.DirectMT5Adapter) -> None:
        a = _with(adapter, [("XAUUSD", 1)])
        assert a.resolve_symbol("") is None

    def test_empty_broker_list_returns_none(self, adapter: mod.DirectMT5Adapter) -> None:
        a = _with(adapter, [])
        assert a.resolve_symbol("XAUUSD") is None

    def test_symbols_get_raising_is_isolated(self, adapter: mod.DirectMT5Adapter) -> None:
        class _Boom:
            def symbols_get(self) -> Any:
                raise RuntimeError("driver fault")

        mod.mt5 = _Boom()
        assert adapter.resolve_symbol("XAUUSD") is None

    def test_disconnected_returns_none(self, adapter: mod.DirectMT5Adapter) -> None:
        a = _with(adapter, [("XAUUSD_i", 1)])
        a._connected = False
        assert a.resolve_symbol("XAUUSD") is None


def a_resolve(adapter: mod.DirectMT5Adapter) -> Any:
    return adapter.resolve_symbol("XAUUSD")


class _FakeSettings:
    """Captures what the boot repair persists (no DB touched)."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {
            "execution.symbol": SimpleNamespace(value="XAUUSD"),
            "execution.enabled_symbols": SimpleNamespace(value=["XAUUSD"]),
        }
        self.sets: list[tuple[str, Any, str]] = []

    def get(self, key: str) -> Any:
        return self.store.get(key)

    def set(
        self,
        key: str,
        value: Any,
        *,
        source: str = "USER_SETTINGS",
        actor: str = "cli",
        correlation_id: str | None = None,
        audit: bool = True,
        old_safe: str | None = None,
        new_safe: str | None = None,
        value_type: str | None = None,
    ) -> Any:
        self.sets.append((key, value, source))
        self.store[key] = SimpleNamespace(value=value)
        return SimpleNamespace(key=key, value=value)


class _FakeAdapter:
    """Adapter stub for the RuntimeLoop repair path."""

    def __init__(self, resolved: str | None) -> None:
        self._resolved = resolved

    def resolve_symbol(self, symbol: str) -> str | None:
        return self._resolved


class _FakeOM:
    def __init__(self, adapter: Any, cfg: Any, policy: Any) -> None:
        self.adapter = adapter
        self.config = cfg
        self.signal_policy = policy
        self.incidents: list[dict[str, Any]] = []

    def emit_incident_telemetry(self, **kwargs: Any) -> None:
        self.incidents.append(kwargs)


def _fake_settings_service(monkeypatch: pytest.MonkeyPatch) -> _FakeSettings:
    fake = _FakeSettings()
    monkeypatch.setattr("nexus_scalp.settings.service.SettingsDatabase", lambda *a, **k: fake)
    return fake


class TestBootAutoRepair:
    def test_repair_rewrites_symbol_and_persists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nexus_scalp.configuration.config import AppConfig, ExecutionConfig

        cfg = AppConfig(execution=ExecutionConfig(symbol="XAUUSD", enabled_symbols=["XAUUSD"]))
        policy = SimpleNamespace(enabled_symbols=["XAUUSD"])
        om = _FakeOM(_FakeAdapter("XAUUSD_i"), cfg, policy)
        fake_store = _fake_settings_service(monkeypatch)
        loop = RuntimeLoop(om)

        out = loop._repair_symbol("XAUUSD")

        assert out == "XAUUSD_i"
        assert cfg.execution.symbol == "XAUUSD_i"
        # whitelist gained the broker name (policy gate compares uppercased);
        # the configured alias is retained, not evicted.
        assert cfg.execution.enabled_symbols == ["XAUUSD_i", "XAUUSD"]
        assert policy.enabled_symbols == ["XAUUSD_i", "XAUUSD"]
        assert (key := "execution.symbol") and any(
            k == key and v == "XAUUSD_i" for k, v, _ in fake_store.sets
        )
        assert any(
            k == "execution.enabled_symbols" and v == ["XAUUSD_i", "XAUUSD"]
            for k, v, _ in fake_store.sets
        )
        assert om.incidents[0]["event_type"] == "BROKER_SYMBOL_AUTO_REPAIR"

    def test_matching_symbol_is_never_rewritten(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nexus_scalp.configuration.config import AppConfig, ExecutionConfig

        cfg = AppConfig(execution=ExecutionConfig(symbol="XAUUSD", enabled_symbols=["XAUUSD"]))
        om = _FakeOM(_FakeAdapter("XAUUSD"), cfg, SimpleNamespace(enabled_symbols=["XAUUSD"]))
        _fake_settings_service(monkeypatch)
        loop = RuntimeLoop(om)

        assert loop._repair_symbol("XAUUSD") == "XAUUSD"
        assert cfg.execution.symbol == "XAUUSD"
        assert om.incidents == []

    def test_unresolvable_symbol_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nexus_scalp.configuration.config import AppConfig, ExecutionConfig

        cfg = AppConfig(execution=ExecutionConfig(symbol="XAUUSD"))
        om = _FakeOM(_FakeAdapter(None), cfg, SimpleNamespace(enabled_symbols=["XAUUSD"]))
        _fake_settings_service(monkeypatch)
        loop = RuntimeLoop(om)

        # No resolution -> the configured name passes through unchanged, so
        # get_symbol_info keeps raising exactly the original fatal error
        # instead of the engine silently trading a made-up symbol.
        assert loop._repair_symbol("XAUUSD") == "XAUUSD"
        assert om.incidents == []

    def test_adapter_without_resolver_is_untouched(self) -> None:
        om = _FakeOM(SimpleNamespace(), None, None)
        loop = RuntimeLoop(om)
        assert loop._repair_symbol("XAUUSD") == "XAUUSD"

    def test_persist_failure_is_isolated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nexus_scalp.configuration.config import AppConfig, ExecutionConfig

        cfg = AppConfig(execution=ExecutionConfig(symbol="XAUUSD"))
        om = _FakeOM(_FakeAdapter("XAUUSD_i"), cfg, SimpleNamespace(enabled_symbols=["XAUUSD"]))

        class _Broken:
            def __init__(self, *a: Any, **k: Any) -> None:
                raise RuntimeError("settings DB locked")

        monkeypatch.setattr("nexus_scalp.settings.service.SettingsDatabase", _Broken)
        loop = RuntimeLoop(om)

        # In-memory repair still takes effect this session; persistence fault
        # never blocks boot.
        assert loop._repair_symbol("XAUUSD") == "XAUUSD_i"
        assert cfg.execution.symbol == "XAUUSD_i"


class TestPortContract:
    def test_default_resolve_symbol_is_none(self) -> None:
        from nexus_scalp.ports.mt5_port import IMT5Port

        assert IMT5Port.resolve_symbol(None, "XAUUSD") is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Discussion #107 (vivekgohel27-collab): the configured symbol must self-heal
# on EVERY read path, not only the boot repair. Brokers suffix the same
# instrument differently (XAUUSD vs XAUUSD.p / XAUUSD.m / XAUUSD_i) and some
# hide symbols from Market Watch until selected, so the raw symbol_info /
# symbol_info_tick reads returned None (MT5 -4) and the engine died at boot.
# These tests stub the native driver so they run without a terminal.
# ---------------------------------------------------------------------------


class _FakeReads:
    """Native driver stand-in: per-symbol info/tick rows + full catalogue."""

    def __init__(
        self,
        catalogue: list[tuple[str, int]],
        info: dict[str, SimpleNamespace] | None = None,
        ticks: dict[str, SimpleNamespace] | None = None,
    ) -> None:
        self._catalogue = [SimpleNamespace(name=n, trade_mode=m) for n, m in catalogue]
        self._info = info or {}
        self._ticks = ticks or {}
        self.selected: list[str] = []

    def symbols_get(self) -> Any:
        return list(self._catalogue)

    def symbol_info(self, name: str) -> Any:
        return self._info.get(name)

    def symbol_info_tick(self, name: str) -> Any:
        return self._ticks.get(name)

    def symbol_select(self, name: str, enable: bool) -> bool:
        self.selected.append(name)
        return True

    def last_error(self) -> tuple[int, str]:
        return (-4, "Terminal: Not found")


def _reads_adapter(monkeypatch: pytest.MonkeyPatch, fake: _FakeReads) -> mod.DirectMT5Adapter:
    a = mod.DirectMT5Adapter()
    a._connected = True
    monkeypatch.setattr(mod, "HAS_NATIVE_MT5", True)
    monkeypatch.setattr(mod, "mt5", fake)
    return a


_P_ROW = SimpleNamespace(
    name="XAUUSD.p",
    digits=3,
    point=0.001,
    trade_tick_size=0.001,
    trade_tick_value=1.0,
    volume_min=0.01,
)
_P_TICK = SimpleNamespace(time=0, time_msc=0, bid=2600.5, ask=2600.8, last=0.0, volume=0.0)


class TestBrokerAliasSelfHeal:
    def test_direct_probe_hit_is_cached_verbatim(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeReads([], info={"XAUUSD": SimpleNamespace(name="XAUUSD")})
        a = _reads_adapter(monkeypatch, fake)

        assert a._broker_alias_for("XAUUSD") == "XAUUSD"
        # A working name is never rewritten and never re-probed.
        assert a._symbol_alias_cache == {"XAUUSD": "XAUUSD"}
        assert fake.selected == []

    def test_suffix_broker_resolves_selects_and_caches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeReads([("XAUUSD.p", 1), ("EURUSD", 1)])
        a = _reads_adapter(monkeypatch, fake)

        alias = a._broker_alias_for("XAUUSD")

        assert alias == "XAUUSD.p"
        assert a._symbol_alias_cache == {"XAUUSD": "XAUUSD.p"}
        # Market Watch visibility: the resolved winner is selected.
        assert fake.selected == ["XAUUSD.p"]
        # Second read hits the cache: no second symbol_select.
        assert a._broker_alias_for("XAUUSD") == "XAUUSD.p"
        assert fake.selected == ["XAUUSD.p"]

    def test_unresolvable_fails_closed_without_caching(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeReads([("EURUSD", 1), ("GBPUSD", 1)])
        a = _reads_adapter(monkeypatch, fake)

        assert a._broker_alias_for("XAUUSD") is None
        # A failure is never cached: a later Market Watch change can land.
        assert a._symbol_alias_cache == {}

    def test_disconnected_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeReads([("XAUUSD.p", 1)])
        a = _reads_adapter(monkeypatch, fake)
        a._connected = False

        assert a._broker_alias_for("XAUUSD") is None
        assert fake.selected == []

    def test_alias_cache_cleared_on_successful_connect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _ConnectOk:
            def shutdown(self) -> None:
                pass

            def initialize(self, **kwargs: Any) -> bool:
                return True

            def version(self) -> tuple[int, int, int]:
                return (1, 2, 3)

            def terminal_info(self) -> Any:
                return SimpleNamespace(name="FakeTerminal")

            def last_error(self) -> tuple[int, str]:
                return (0, "no error")

        a = _reads_adapter(monkeypatch, _FakeReads([("XAUUSD.p", 1)]))
        a._symbol_alias_cache["XAUUSD"] = "XAUUSD.p"
        monkeypatch.setattr(mod, "mt5", _ConnectOk())

        assert a.connect() is True
        # A reconnect may target a different server: stale aliases must not
        # survive into the new session.
        assert a._symbol_alias_cache == {}


class TestSelfHealingReads:
    def test_snapshot_retries_with_broker_suffix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeReads(
            [("XAUUSD.p", 1), ("EURUSD", 1)],
            info={"XAUUSD.p": _P_ROW},
            ticks={"XAUUSD.p": _P_TICK},
        )
        a = _reads_adapter(monkeypatch, fake)

        snap = a.get_symbol_snapshot("XAUUSD")

        assert snap.available is True
        assert snap.spec.get("name") == "XAUUSD.p"
        assert snap.spec.get("digits") == 3.0
        assert fake.selected == ["XAUUSD.p"]

    def test_snapshot_exact_hit_never_resolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeReads(
            [("XAUUSD", 1), ("XAUUSD.p", 1)],
            info={"XAUUSD": SimpleNamespace(name="XAUUSD", digits=3, point=0.001)},
            ticks={"XAUUSD": _P_TICK},
        )
        a = _reads_adapter(monkeypatch, fake)

        snap = a.get_symbol_snapshot("XAUUSD")

        assert snap.available is True
        assert snap.spec.get("name") == "XAUUSD"
        # The broker's own spelling already worked: nothing selected.
        assert fake.selected == []

    def test_snapshot_unresolvable_keeps_original_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeReads([("EURUSD", 1)])
        a = _reads_adapter(monkeypatch, fake)

        snap = a.get_symbol_snapshot("XAUUSD")

        assert snap.available is False
        # Exactly the error the discussion reporter saw (MT5 -4), preserved
        # fail-closed when no broker spelling matches.
        assert snap.error_state == {
            "operation": "symbol_info_tick",
            "code": -4,
            "message": "Terminal: Not found",
        }

    def test_broker_tick_retries_with_broker_suffix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeReads(
            [("XAUUSD.p", 1)],
            ticks={"XAUUSD.p": _P_TICK},
        )
        a = _reads_adapter(monkeypatch, fake)

        snap = a.get_broker_tick("XAUUSD")

        assert snap.available is True
        assert snap.symbol == "XAUUSD.p"
        assert snap.bid == 2600.5
        assert snap.ask == 2600.8

    def test_get_symbol_info_returns_broker_truth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeReads(
            [("XAUUSD.p", 1)],
            info={"XAUUSD.p": _P_ROW},
            ticks={"XAUUSD.p": _P_TICK},
        )
        a = _reads_adapter(monkeypatch, fake)

        info = a.get_symbol_info("XAUUSD")

        assert info.symbol == "XAUUSD.p"
        assert info.digits == 3


class TestMcpMarketWatchAlias:
    def _mcp(self, monkeypatch: pytest.MonkeyPatch, symbols: list[dict[str, Any]]) -> Any:
        from nexus_scalp.adapters.mt5.mcp_adapter import MCPMT5Adapter

        a = MCPMT5Adapter(url="http://127.0.0.1:22346/mcp", api_key="k")
        a._connected = True
        monkeypatch.setattr(
            a,
            "_call_json",
            lambda tool, arguments=None: {"symbols": symbols},
        )
        return a

    def test_alias_helper_suffix_broker(self) -> None:
        from nexus_scalp.adapters.mt5.mcp_adapter import MCPMT5Adapter

        rows = [{"symbol": "EURUSD"}, {"symbol": "XAUUSD.p"}]
        assert MCPMT5Adapter._marketwatch_alias("XAUUSD", rows) == "XAUUSD.p"
        assert MCPMT5Adapter._marketwatch_alias("XAUUSD.P", rows) == "XAUUSD.p"
        assert MCPMT5Adapter._marketwatch_alias("XAUUSD", [{"symbol": "EURUSD"}]) is None
        assert MCPMT5Adapter._marketwatch_alias("", rows) is None
        assert MCPMT5Adapter._marketwatch_alias("XAUUSD", "not-a-list") is None

    def test_snapshot_retries_with_broker_suffix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        a = self._mcp(
            monkeypatch,
            [
                {
                    "symbol": "XAUUSD.p",
                    "digits": 3,
                    "point": 0.001,
                    "tick_size": 0.001,
                    "tick_value": 1.0,
                    "contract_size": 100.0,
                    "volume_min": 0.01,
                    "volume_max": 100.0,
                    "volume_step": 0.01,
                    "bid": 2600.5,
                    "ask": 2600.8,
                    "update_time": "2026-11-16T10:00:00Z",
                }
            ],
        )

        snap = a.get_symbol_snapshot("XAUUSD")

        assert snap.available is True
        assert snap.spec.get("name") == "XAUUSD.p"
        assert snap.spec.get("digits") == 3.0

    def test_broker_tick_retries_with_broker_suffix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        a = self._mcp(
            monkeypatch,
            [
                {
                    "symbol": "XAUUSD.p",
                    "bid": 2600.5,
                    "ask": 2600.8,
                    "update_time": "2026-11-16T10:00:00Z",
                }
            ],
        )

        snap = a.get_broker_tick("XAUUSD")

        assert snap.available is True
        assert snap.symbol == "XAUUSD.p"
        assert snap.bid == 2600.5

    def test_unmatched_marketwatch_still_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a = self._mcp(monkeypatch, [{"symbol": "EURUSD"}])

        snap = a.get_symbol_snapshot("XAUUSD")

        assert snap.available is False
        assert snap.error_state["code"] == "NOT_FOUND"

    def test_resolve_symbol_uses_market_watch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        a = self._mcp(monkeypatch, [{"symbol": "XAUUSD.p"}])
        assert a.resolve_symbol("XAUUSD") == "XAUUSD.p"

        a._connected = False
        assert a.resolve_symbol("XAUUSD") is None
