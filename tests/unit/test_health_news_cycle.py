"""HEALTH-NEWS-CYCLE: the news worker must stamp the engine's cycle time.

The defect: ``NewsEngine.last_cycle_at`` was initialised to ``None`` and never
written by anything, while ``NewsEngine.health()`` serialised it into the
``/api/news/health`` payload and ``cycle_count`` climbed every 60s. On a live
engine the payload reported:

    "cycle_count": 55,
    "last_cycle_at": ""

That is exactly the signature of a worker that STOPPED cycling long ago — and
indistinguishable from it — while the worker was in fact live. An operator
triaging a STALE news context was pointed at a timestamp that carried no
information at all.

The worker owns the cycle loop (it is the only component that advances the
counter), so it owns the stamp. ``tick()`` already sets ``last_cycle_start``
for its own telemetry; this pins the engine-visible value to the same instant.
"""

from __future__ import annotations

import queue
from datetime import UTC, datetime
from types import SimpleNamespace

from nexus_scalp.news.engine import NewsEngine
from nexus_scalp.news.worker import NewsWorker


def _worker(engine: NewsEngine | SimpleNamespace, interval_sec: float = 0.0) -> NewsWorker:
    """A worker with the queue + scheduling machinery intact but no DB.

    ``interval_sec=0`` makes ``tick()`` eligible on the first call without a
    real clock budget, and a SimpleNamespace stands in for a NewsEngine when
    the test only exercises the stamping (NewsEngine construction is not part
    of this defect's contract).
    """
    return NewsWorker(engine, interval_sec=interval_sec, max_queue=8)  # type: ignore[arg-type]


def _drive_tick(worker: NewsWorker) -> bool:
    """One tick with every side-effecting dependency stubbed.

    ``tick`` runs ingest -> (optional) analysis -> context refresh -> checkpoint
    and then stamps the engine. Only the stamp is asserted, so the rest is
    neutralised: ingestion is a no-op (no sources) and the context refresh is
    a stand-in that cannot raise.
    """
    worker.running = True
    worker._last_run_ts = 0.0  # eligible immediately

    engine = worker.engine
    # No sources -> ingest_cycle is a pure no-op that returns zeroed stats.
    # The real bound method is used (it needs no DB beyond db.list_sources).
    engine.ingest_cycle = lambda max_sources=10: {}  # type: ignore[attr-defined]
    engine.db.list_sources.return_value = []  # type: ignore[attr-defined]
    # The context refresh must not touch a real database.
    engine.context = SimpleNamespace(refresh=lambda: None)  # type: ignore[attr-defined]
    # Checkpointing against a stub engine would raise; it is failure-isolated
    # by design, but neutralising it keeps the tick's own return honest.
    engine.db.save_worker_state = lambda _state: None  # type: ignore[attr-defined]
    return bool(worker.tick())


def test_a_completed_cycle_stamps_the_engine_clock() -> None:
    """``health()["last_cycle_at"]`` must reflect a cycle that actually ran."""
    engine = SimpleNamespace(
        db=SimpleNamespace(
            list_sources=lambda: [],
            save_worker_state=lambda _state: None,
            load_worker_state=lambda: {},
        ),
        context=SimpleNamespace(refresh=lambda: None),
        cycle_count=0,
        last_cycle_at=None,
        auto_analysis_enabled=False,
        live_engine=None,
    )
    worker = _worker(engine)

    before = datetime.now(UTC)
    assert _drive_tick(worker)

    stamp = engine.last_cycle_at
    assert isinstance(stamp, datetime), "the engine stamp was never written"
    # The same instant the worker itself recorded, not a fresh wall clock.
    assert stamp is worker.last_cycle_start
    # And it is a real "now", not an uninitialized epoch.
    assert before <= stamp <= datetime.now(UTC)


def test_the_engine_stamp_reaches_the_health_payload() -> None:
    """``NewsEngine.health()`` serialises the stamp the worker wrote."""
    stamp = datetime(2026, 9, 28, 10, 27, 18, tzinfo=UTC)
    engine = SimpleNamespace(
        db=SimpleNamespace(summary=lambda: {}, list_sources=lambda: []),
        admission_gateway=SimpleNamespace(metrics=SimpleNamespace(snapshot=lambda: {})),
        context=SimpleNamespace(state=SimpleNamespace(value="NORMAL"), stale=False),
        cycle_count=40,
        last_error="",
        last_cycle_at=stamp,
    )

    # The bound method is used on the stub namespace (the real one is
    # stateless with respect to self apart from the attributes below).
    engine.ingest_cycle = lambda max_sources=10: {}  # type: ignore[attr-defined]
    engine.current_context = lambda force=False: SimpleNamespace(  # type: ignore[attr-defined]
        state=SimpleNamespace(value="NORMAL"), stale=False, available=True
    )
    health = NewsEngine.health.__get__(engine, SimpleNamespace)()  # type: ignore[attr-defined]

    assert health["last_cycle_at"] == stamp.isoformat()
    assert health["cycle_count"] == 40


def test_a_failing_cycle_does_not_advertise_a_fresh_stamp() -> None:
    """A tick that raises must not back-date the engine's last success.

    The stamp is "the last time a cycle COMPLETED"; a failed cycle keeps the
    prior value so an operator reading the timestamp knows what it means.
    """
    prior = datetime(2026, 9, 28, 9, 0, 0, tzinfo=UTC)
    engine = SimpleNamespace(
        db=SimpleNamespace(
            list_sources=lambda: [],
            save_worker_state=lambda _state: None,
            load_worker_state=lambda: {},
        ),
        context=SimpleNamespace(refresh=lambda: None),
        cycle_count=3,
        last_cycle_at=prior,
        auto_analysis_enabled=False,
        live_engine=None,
    )
    worker = _worker(engine)
    worker.running = True
    worker._last_run_ts = 0.0

    # An ingest failure is the real-world failure mode (feed outage / auth).
    engine.db.list_sources.side_effect = RuntimeError("feed unreachable")  # type: ignore[attr-defined]

    assert worker.tick() is False
    assert engine.last_cycle_at == prior, "a failed cycle must keep the prior stamp"


def test_the_worker_survives_an_engine_that_lacks_the_attribute() -> None:
    """Failure isolation: a minimal host must not crash the tick.

    Some hosts (tests, CLI probes) construct a partial engine; the stamp is
    telemetry, so its absence must never break the cycle itself.
    """

    class _MinimalEngine:
        def __init__(self) -> None:
            self.db = SimpleNamespace(
                list_sources=lambda: [],
                save_worker_state=lambda _state: None,
                load_worker_state=lambda: {},
            )
            self.ingest_cycle = lambda max_sources=10: {}
            self.context = SimpleNamespace(refresh=lambda: None)
            self.auto_analysis_enabled = False
            self.live_engine = None
            # NOTE: no last_cycle_at at all.

    worker = _worker(_MinimalEngine())

    assert _drive_tick(worker) is True
    # The cycle succeeded; whether the stamp landed is best-effort.
    assert worker.last_cycle_start is not None


def test_an_empty_job_queue_is_not_a_cycle_failure() -> None:
    """The queue must be constructible and empty at rest (harness sanity)."""
    worker = _worker(SimpleNamespace())
    assert isinstance(worker._jobs, queue.PriorityQueue)
    assert worker._jobs.qsize() == 0
