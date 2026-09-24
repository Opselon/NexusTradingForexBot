"""R4 tick-read slice: real remote adapter, injected transport, no network/orders."""

from __future__ import annotations

import asyncio
import inspect
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nexus_scalp.adapters.mt5.remote_gateway import RemoteMT5GatewayAdapter
from nexus_scalp.application.live.runtime_loop import RuntimeLoop


def _response():
    return {"data": {"timestamp": "2026-09-16T19:00:00+00:00", "bid": 2400, "ask": 2400.2}}


def _loop(monkeypatch, transport):
    adapter = RemoteMT5GatewayAdapter(api_key="probe-key", secret_token="probe-secret")
    monkeypatch.setattr(adapter, "_send_request", transport)
    return RuntimeLoop(SimpleNamespace(adapter=adapter))


@pytest.mark.asyncio
async def test_remote_read_yields_and_preserves_real_adapter_result(monkeypatch):
    release = threading.Event()
    started = threading.Event()
    threads = []

    def transport(action, payload):
        assert (action, payload) == ("GET_LAST_TICK", {"symbol": "XAUUSD"})
        threads.append(threading.get_ident())
        started.set()
        assert release.wait(2), "loop failed to release transport"
        return _response()

    loop = _loop(monkeypatch, transport)
    task = asyncio.create_task(loop._poll_tick("XAUUSD"))
    try:
        async with asyncio.timeout(1):
            while not started.is_set():
                await asyncio.sleep(0.001)
        # This coroutine ran while the transport was blocked: deterministic
        # responsiveness evidence, not a host-speed percentile threshold.
        assert not task.done()
        assert threads == [threads[0]] and threads[0] != threading.get_ident()
    finally:
        release.set()
    tick = await task
    assert (tick.symbol, tick.bid, tick.ask) == ("XAUUSD", 2400, 2400.2)
    assert tick.timestamp.isoformat() == "2026-09-16T19:00:00+00:00"


@pytest.mark.asyncio
async def test_transport_error_is_not_cached_or_fabricated(monkeypatch):
    def transport(*args):
        raise OSError("injected transport failure")

    loop = _loop(monkeypatch, transport)
    with pytest.raises(OSError, match="injected transport failure"):
        await loop._poll_tick("XAUUSD")


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_fails", [False, True])
async def test_repeated_cancellation_drains_worker(monkeypatch, worker_fails):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    calls = []

    def transport(*args):
        calls.append(1)
        started.set()
        try:
            assert release.wait(2)
            if worker_fails:
                raise OSError("late transport failure")
            return _response()
        finally:
            finished.set()

    loop = _loop(monkeypatch, transport)
    task = asyncio.create_task(loop._poll_tick("XAUUSD"))
    try:
        async with asyncio.timeout(1):
            while not started.is_set():
                await asyncio.sleep(0.001)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done(), "cancel must not detach the running read"
        assert calls == [1], "cancellation must never retry a broker read"
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


@pytest.mark.asyncio
async def test_non_remote_adapter_stays_inline():
    threads = []
    expected = object()

    def get_last_tick(symbol):
        threads.append(threading.get_ident())
        return expected

    loop = RuntimeLoop(SimpleNamespace(adapter=SimpleNamespace(get_last_tick=get_last_tick)))
    assert await loop._poll_tick("XAUUSD") is expected
    assert threads == [threading.get_ident()]


@pytest.mark.asyncio
async def test_real_loop_awaits_poll_before_pipeline_and_next_poll(monkeypatch):
    """Exercise production run(), not just an isolated helper/source-string pin."""
    from unittest.mock import AsyncMock

    import nexus_scalp.application.live.runtime_loop as module

    monkeypatch.setattr(module, "logger", MagicMock())
    engine = MagicMock()
    for name in (
        "_cold_start_warmup",
        "_bootstrap_train_if_ready",
        "_startup_experience_self_heal",
        "_shutdown_async",
    ):
        setattr(engine, name, AsyncMock())
    engine._maintenance.run_cycle = AsyncMock()
    engine._restore_runtime_risk_state.return_value.trading_allowed = True
    engine._last_fresh_tick_at = None
    engine._pipeline_last_ts = None
    engine._last_account_refresh = 0.0
    engine._last_snapshot_refresh = 0.0
    engine._account_freshness = "FRESH"
    engine._accounting_worker_started = False
    engine.config.execution.symbol = "XAUUSD"
    engine.adapter.get_last_tick.return_value = SimpleNamespace(bid=1, ask=2)
    loop = RuntimeLoop(engine)
    tick = SimpleNamespace(timestamp=object(), bid=2400.0, ask=2400.2)
    events = []

    async def poll(symbol):
        events.append("poll-start")
        await asyncio.sleep(0)
        events.append("poll-finish")
        return tick

    def pipeline(**kwargs):
        assert kwargs["tick"] is tick
        events.append("pipeline")
        engine._running = False

    monkeypatch.setattr(loop, "_poll_tick", poll)
    engine._process_tick_pipeline.side_effect = pipeline
    await asyncio.wait_for(loop.run(), 2)
    assert events == ["poll-start", "poll-finish", "pipeline"]
    engine._shutdown_async.assert_awaited_once()


def test_execution_and_freshness_gates_not_moved_to_worker():
    source = inspect.getsource(RuntimeLoop._poll_tick)
    assert "send_order" not in source
    assert "_process_tick_pipeline" not in source
    assert "get_account_info" not in source
    assert "wait_for" not in source
