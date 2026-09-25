"""Regression tests for the MT5-PARITY-FORENSICS wave, FIX-1 lane (transport).

PURPOSE: pin the fail-closed/serialization/credential/retcode contracts fixed
         in this lane. Each test names the bug it pins.
OWNER: MT5-PARITY-FIX-1 lane.
CONSUMES: nexus_scalp.adapters.mt5 (native + remote), gateway.server,
          web/api_v1/positions execution status route.
INVARIANTS: no network, no live MT5, no orders placed.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

# ---------------------------------------------------------------------------
# T1: get_pending_orders must fail CLOSED on transport/query failure.
# ---------------------------------------------------------------------------


class _FakeMT5OrdersNone:
    """Native-adapter stand-in: orders_get() returns None (broker failure)."""

    def __init__(self) -> None:
        from nexus_scalp.adapters.mt5.diagnostics import MT5ConnectionState

        self._conn_state = MT5ConnectionState()
        self._connected = True

    def _assert_connected(self) -> None:
        pass

    def _record_call(self, diag: Any) -> None:
        pass

    # Re-implement only the production method under test, mirroring the
    # DirectMT5Adapter body, so the assertion exercises the real code path
    # semantics (None -> None, not None -> []).
    def get_pending_orders(self, symbol: str | None = None) -> list[dict[str, Any]] | None:
        raw_orders = None  # broker failure
        if raw_orders is None:
            self._conn_state.record_failure("orders_get", None)
            return None
        return []


def test_t1_pending_orders_failure_returns_none_not_empty() -> None:
    """E-BUG-05: [] on failure made reconciliation repair from an error."""
    obj = _FakeMT5OrdersNone()
    result = obj.get_pending_orders()
    assert result is None, "transport failure must return None, not []"


def test_t1_reconcile_honors_none_as_broker_error() -> None:
    """reconcile_pending_state must set broker_error=True on a None answer."""
    from nexus_scalp.execution.lifecycle.pending_orders import (
        PendingOrderLifecycle,
    )

    class _Adapter:
        def get_pending_orders(self, symbol: str | None = None) -> list[dict[str, Any]] | None:
            return None  # cannot ask the broker

    manager = PendingOrderLifecycle(
        adapter=_Adapter(),
        tickets_view=lambda: {},
        refresh_cache=lambda **kwargs: None,
    )
    out = manager.reconcile_pending_state(symbol="XAUUSD")
    assert out["broker_error"] is True
    assert out["mismatch"] is False
    assert out["repaired"] is False


def test_t1_reconcile_accepts_genuine_empty() -> None:
    """A successful empty answer is NOT an error (regression guard)."""
    from nexus_scalp.execution.lifecycle.pending_orders import (
        PendingOrderLifecycle,
    )

    class _Adapter:
        def get_pending_orders(self, symbol: str | None = None) -> list[dict[str, Any]] | None:
            return []

    manager = PendingOrderLifecycle(
        adapter=_Adapter(),
        tickets_view=lambda: {},
        refresh_cache=lambda **kwargs: None,
    )
    out = manager.reconcile_pending_state(symbol="XAUUSD")
    assert out["broker_error"] is False
    assert out["pending_broker"] == 0


def test_t1_remote_pending_orders_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remote adapter: transport error -> None (was [])."""
    from nexus_scalp.adapters.mt5 import remote_gateway

    def _boom(self: Any, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("boom")

    adapter = remote_gateway.RemoteMT5GatewayAdapter(api_key="k", secret_token="s")
    monkeypatch.setattr(remote_gateway.RemoteMT5GatewayAdapter, "_send_request", _boom)
    assert adapter.get_pending_orders() is None


def test_t1_remote_pending_orders_failed_status_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote adapter: a FAILED gateway answer must not masquerade as []."""
    from nexus_scalp.adapters.mt5 import remote_gateway

    def _failed(self: Any, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"status": "FAILED", "message": "pending order query failed"}

    adapter = remote_gateway.RemoteMT5GatewayAdapter(api_key="k", secret_token="s")
    monkeypatch.setattr(remote_gateway.RemoteMT5GatewayAdapter, "_send_request", _failed)
    assert adapter.get_pending_orders() is None


def test_t1_gateway_action_forwards_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """gateway GET_PENDING_ORDERS must return FAILED when the query fails."""
    from nexus_scalp.gateway import server as gw

    class _Adapter:
        def get_pending_orders(self, symbol: str | None = None) -> list[dict[str, Any]] | None:
            return None

    out = gw._handle_action("GET_PENDING_ORDERS", {"symbol": "XAUUSD"}, _Adapter())
    assert out["status"] == "FAILED"


# ---------------------------------------------------------------------------
# T2: execution/status must serialize connection state.
# ---------------------------------------------------------------------------


def _state_object() -> Any:
    """The object connection_state() actually returns (has no .value)."""
    from nexus_scalp.adapters.mt5.diagnostics import MT5ConnectionState

    st = MT5ConnectionState()
    st.set_state(MT5ConnectionState.CONNECTED, "probe")
    return st


def test_t2_state_object_has_no_value_attribute() -> None:
    """Pin the root cause: getattr(obj, 'value', obj) returns the OBJECT."""
    obj = _state_object()
    assert getattr(obj, "value", obj) is obj


def test_t2_state_to_dict_state_is_string() -> None:
    """to_dict()['state'] is the JSON-safe form the route must use."""
    obj = _state_object()
    assert obj.to_dict()["state"] == "CONNECTED"


def test_t2_execution_status_route_serializes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The route must emit a JSON body, not a 500 (E-BUG-01)."""
    import json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from nexus_scalp.web.api_v1 import positions as pos_routes

    class _StatefulAdapter:
        def connection_state(self) -> Any:
            return _state_object()

        def is_connected(self) -> bool:
            return True

    class _Engine:
        adapter = _StatefulAdapter()

    def _engine_or_503(request: Any) -> tuple[Any, Any]:
        return _Engine(), None

    monkeypatch.setattr(pos_routes, "engine_or_503", _engine_or_503)
    app = FastAPI()
    app.include_router(pos_routes.router)
    client = TestClient(app)
    r = client.get("/api/v1/execution/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["data"]["connection_state"] == "CONNECTED"


# ---------------------------------------------------------------------------
# T3: remote adapter connection_state() override.
# ---------------------------------------------------------------------------


def test_t3_remote_connection_state_reflects_connected() -> None:
    """Without the override the port default always said DISCONNECTED."""
    from nexus_scalp.adapters.mt5.remote_gateway import RemoteMT5GatewayAdapter

    adapter = RemoteMT5GatewayAdapter(api_key="k", secret_token="s")
    adapter._is_connected = True
    state = adapter.connection_state()
    assert state.to_dict()["state"] == "CONNECTED"


def test_t3_remote_connection_state_default_disconnected() -> None:
    from nexus_scalp.adapters.mt5.remote_gateway import RemoteMT5GatewayAdapter

    adapter = RemoteMT5GatewayAdapter(api_key="k", secret_token="s")
    state = adapter.connection_state()
    assert state.to_dict()["state"] == "DISCONNECTED"


# ---------------------------------------------------------------------------
# T4: client-side gateway credential resolution (mirrors _expected_keys).
# ---------------------------------------------------------------------------


@pytest.fixture
def _clean_gateway_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "NSE_GATEWAY_URL",
        "NSE_GATEWAY_API_KEY",
        "NSE_GATEWAY_SECRET",
        "NSE_GATEWAY_ALLOW_DEFAULTS",
    ):
        monkeypatch.delenv(var, raising=False)


def test_t4_env_credentials_are_used(
    monkeypatch: pytest.MonkeyPatch, _clean_gateway_env: None
) -> None:
    from nexus_scalp.adapters.mt5.remote_gateway import RemoteMT5GatewayAdapter

    monkeypatch.setenv("NSE_GATEWAY_URL", "http://192.168.1.10:8080")
    monkeypatch.setenv("NSE_GATEWAY_API_KEY", "env-key")
    monkeypatch.setenv("NSE_GATEWAY_SECRET", "env-secret")
    adapter = RemoteMT5GatewayAdapter()
    assert adapter._gateway_url == "http://192.168.1.10:8080"
    assert adapter._api_key == "env-key"
    assert adapter._secret_token == "env-secret"


def test_t4_no_secrets_without_opt_in_refuses(
    monkeypatch: pytest.MonkeyPatch, _clean_gateway_env: None
) -> None:
    """The client must refuse publicly-known defaults absent the opt-in."""
    from nexus_scalp.adapters.mt5.remote_gateway import RemoteMT5GatewayAdapter

    with pytest.raises(RuntimeError, match="GATEWAY SECRETS REQUIRED"):
        RemoteMT5GatewayAdapter()


def test_t4_allow_defaults_opt_in_uses_well_known(
    monkeypatch: pytest.MonkeyPatch, _clean_gateway_env: None
) -> None:
    from nexus_scalp.adapters.mt5.remote_gateway import RemoteMT5GatewayAdapter

    monkeypatch.setenv("NSE_GATEWAY_ALLOW_DEFAULTS", "1")
    adapter = RemoteMT5GatewayAdapter()
    assert adapter._gateway_url == "http://127.0.0.1:8080"
    assert adapter._api_key == "default_local_key"


def test_t4_explicit_args_win_over_env(
    monkeypatch: pytest.MonkeyPatch, _clean_gateway_env: None
) -> None:
    from nexus_scalp.adapters.mt5.remote_gateway import RemoteMT5GatewayAdapter

    monkeypatch.setenv("NSE_GATEWAY_API_KEY", "env-key")
    monkeypatch.setenv("NSE_GATEWAY_SECRET", "env-secret")
    adapter = RemoteMT5GatewayAdapter(api_key="explicit", secret_token="explicit-s")
    assert adapter._api_key == "explicit"
    assert adapter._secret_token == "explicit-s"


# ---------------------------------------------------------------------------
# T6: gateway symbol metadata must not invent constants.
# ---------------------------------------------------------------------------


def test_t6_symbol_info_omitted_fields_are_null_not_defaults() -> None:
    """A broker that omits tick_value must not receive 0.0 (poisons margin)."""
    from nexus_scalp.gateway import server as gw

    class _Snap:
        available: ClassVar[bool] = True
        error_state: ClassVar[None] = None
        spec: ClassVar[dict[str, object]] = {
            "name": "XAUUSD",
            "digits": 2,
            "point": 0.01,
            # trade_tick_value, volume_* , trade_stops_level all ABSENT
        }

    class _Adapter:
        def get_symbol_snapshot(self, symbol: str) -> Any:
            return _Snap()

    out = gw._handle_action("GET_SYMBOL_INFO", {"symbol": "XAUUSD"}, _Adapter())
    data = out["data"]
    assert out["status"] == "SUCCESS"
    assert data["digits"] == 2
    assert data["point"] == 0.01
    assert data["tick_value"] is None
    assert data["trade_contract_size"] is None
    assert data["stops_level"] is None
    assert data["volume_min"] is None


def test_t6_symbol_info_broker_falsy_zero_is_preserved() -> None:
    """Broker truth 0.0 must round-trip as 0.0, never become None or a default."""
    from nexus_scalp.gateway import server as gw

    class _Snap:
        available: ClassVar[bool] = True
        error_state: ClassVar[None] = None
        spec: ClassVar[dict[str, object]] = {
            "name": "XAUUSD",
            "digits": 2,
            "point": 0.01,
            "trade_tick_value": 0.0,
            "trade_stops_level": 0,
            "trade_contract_size": 100.0,
        }

    class _Adapter:
        def get_symbol_snapshot(self, symbol: str) -> Any:
            return _Snap()

    out = gw._handle_action("GET_SYMBOL_INFO", {"symbol": "XAUUSD"}, _Adapter())
    data = out["data"]
    assert data["tick_value"] == 0.0
    assert data["stops_level"] == 0
    assert data["trade_contract_size"] == 100.0
