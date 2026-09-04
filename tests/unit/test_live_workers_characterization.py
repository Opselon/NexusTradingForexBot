"""Characterization for WorkerSupervisor."""

from __future__ import annotations

import asyncio

import pytest

from nexus_scalp.application.live_workers import WorkerSupervisor


class FakeWorker:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.fail_start = False

    def start(self):
        if self.fail_start:
            raise RuntimeError("cannot start")
        self.started = True

    def stop(self):
        self.stopped = True


class TestWorkerSupervisor:
    def test_start_worker_success(self):
        w = FakeWorker()
        state = {"started": False}
        res = WorkerSupervisor.start_worker(
            "test", w, state["started"], lambda v: state.update(started=v)
        )
        assert res is True
        assert state["started"] is True
        assert w.started is True

    def test_start_worker_idempotent(self):
        w = FakeWorker()
        state = {"started": True}
        res = WorkerSupervisor.start_worker(
            "test", w, state["started"], lambda v: state.update(started=v)
        )
        assert res is True
        assert w.started is False  # not called again

    def test_start_worker_failure_isolated(self):
        w = FakeWorker()
        w.fail_start = True
        state = {"started": False}
        res = WorkerSupervisor.start_worker(
            "test", w, state["started"], lambda v: state.update(started=v)
        )
        assert res is False
        assert state["started"] is False

    @pytest.mark.asyncio
    async def test_stop_worker_success(self):
        w = FakeWorker()
        state = {"started": True}
        await WorkerSupervisor.stop_worker("test", w, lambda v: state.update(started=v))
        assert state["started"] is False
        assert w.stopped is True

    @pytest.mark.asyncio
    async def test_kick_worker_executes_and_cleans(self):
        called = False

        def work():
            nonlocal called
            called = True

        inflight = set()
        bg = set()
        WorkerSupervisor.kick_worker("job", work, inflight, bg)
        assert "job" in inflight
        assert len(bg) == 1
        await asyncio.gather(*bg)
        assert called is True
        assert "job" not in inflight
        assert len(bg) == 0
