"""Offline R4 characterization: execute the real RuntimeLoop, never a broker.

Run with PYTHONPATH=src:. python docs/agent_handoffs/swarm/r4_loop_stall_probe.py
Not a regression gate: reports the current stall and naive timeout fan-out hazard.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from nexus_scalp.application.live.runtime_loop import RuntimeLoop


async def loop_probe() -> dict[str, object]:
    engine = MagicMock()
    engine.config.execution.symbol = "XAUUSD"
    engine._restore_runtime_risk_state.return_value.trading_allowed = True
    engine._account_age_max_sec = 30.0
    engine._account_freshness = "FRESH"
    engine._last_account_refresh = time.time()
    engine._last_snapshot_refresh = time.time()
    engine._last_fresh_tick_at = time.time()
    engine._pipeline_last_ts = None
    engine._pipeline_last_bid = 0.0
    engine._pipeline_last_ask = 0.0
    engine._accounting_worker_started = False
    for name in (
        "_cold_start_warmup",
        "_bootstrap_train_if_ready",
        "_startup_experience_self_heal",
        "_shutdown_async",
        "_service_pipeline_workers",
    ):
        setattr(engine, name, AsyncMock())
    engine._maintenance.run_cycle = AsyncMock()
    tick = SimpleNamespace(timestamp=datetime.now(UTC), bid=2400.0, ask=2400.2)
    calls = 0
    threads: list[int] = []

    def poll(symbol: str) -> SimpleNamespace:
        nonlocal calls
        assert symbol == "XAUUSD"
        calls += 1
        if calls > 1:  # startup reconciliation reads once, then the real loop
            threads.append(threading.get_ident())
            time.sleep(0.15)  # injected read latency, not a network measurement
        return tick

    engine.adapter.get_last_tick.side_effect = poll
    engine._process_tick_pipeline.side_effect = lambda **kwargs: setattr(engine, "_running", False)
    beats: list[float] = []
    done = False

    async def heartbeat() -> None:
        while not done:
            beats.append(time.perf_counter())
            await asyncio.sleep(0.005)

    task = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    try:
        await RuntimeLoop(engine).run()
    finally:
        done = True
        await task
    gaps = [b - a for a, b in itertools.pairwise(beats)]
    assert engine._process_tick_pipeline.call_count == 1
    assert engine._shutdown_async.await_count == 1
    assert calls == 2
    return {
        "poll_calls_including_startup": calls,
        "pipeline_calls": engine._process_tick_pipeline.call_count,
        "poll_on_event_loop_thread": threads == [threading.get_ident()],
        "heartbeat_max_gap_ms": round(max(gaps) * 1000, 2),
        "injected_poll_latency_ms": 150,
    }


async def timeout_probe() -> dict[str, object]:
    """Demonstrate that wait_for(to_thread) doesn't stop an in-flight RPC."""
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum = 0
    finished = 0

    def blocked_read() -> None:
        nonlocal active, maximum, finished
        with lock:
            active += 1
            maximum = max(maximum, active)
        try:
            if not release.wait(2.0):
                raise RuntimeError("probe worker release deadline exceeded")
        finally:
            with lock:
                active -= 1
                finished += 1

    timed_out = 0
    try:
        for _ in range(3):
            try:
                await asyncio.wait_for(asyncio.to_thread(blocked_read), 0.03)
            except TimeoutError:
                timed_out += 1
        with lock:
            active_after_timeouts = active
    finally:
        release.set()
        # Join all workers before returning; never leave orphan probe threads.
        await asyncio.get_running_loop().shutdown_default_executor()
    assert finished == 3
    return {
        "timeouts": timed_out,
        "max_concurrent_reads": maximum,
        "reads_still_running_after_timeouts": active_after_timeouts,
        "workers_finished_after_release": finished,
    }


async def main() -> None:
    print(json.dumps({"runtime_loop": await loop_probe(), "naive_timeout": await timeout_probe()}))


if __name__ == "__main__":
    asyncio.run(main())
