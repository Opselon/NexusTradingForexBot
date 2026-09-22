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
