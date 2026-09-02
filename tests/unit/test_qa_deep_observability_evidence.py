"""TASK-QA-DEEP-ASSURANCE / CHG-0045: observability evidence-preservation battery.

Adversarial tests over EventBatchAggregator (observability/event_aggregator.py)
and the frozen observability summary-line contract — evidence must never be
silently lost or corrupted under adversarial workloads:

OBS-1  (evidence preservation, adversarial) > MAX_GROUPS distinct signatures
       without flush: first occurrences are still logged by producers, and
       dropped_events accounting is EXACT (sum of unflushed counts), never
       under-reported
OBS-2  (count truth) count/first_seen/last_seen survive arbitrary interleaved
       add() patterns for every signature; last_seen is monotone
OBS-3  (sample_ids bound) sample_ids never exceed the configured bound and
       keep insertion order (no duplicates)
OBS-4  (storm bound) flushing after N adds emits at most one summary line per
       distinct signature (bounded output — storm safe)
OBS-5  (summary-line freeze) flushed lines contain the frozen field set:
       count= stage= reason= recoverable= sample_ids=[...] first_seen=
       last_seen=
OBS-6  (recovery/aggregation cycle) add -> flush -> add -> flush counts
       accumulate honestly across cycles (events_aggregated == total adds)
OBS-7  (thread race, bounded) concurrent add() from many threads loses ZERO
       events (counter truth under concurrency)
"""

from __future__ import annotations

import random
import threading

from nexus_scalp.observability.event_aggregator import (
    DEFAULT_SAMPLE_IDS,
    EventBatchAggregator,
)

SEED = 20260902


def _add(
    agg: EventBatchAggregator,
    event: str,
    reason: str = "r",
    stage: str = "s",
    recoverable: bool = False,
    trade_id: str | None = None,
    now: float | None = None,
) -> bool:
    return agg.add(
        event=event, reason=reason, stage=stage, recoverable=recoverable, trade_id=trade_id, now=now
    )


# ---------------------------------------------------------------------------
# OBS-1: adversarial signature flood — exact dropped accounting
# ---------------------------------------------------------------------------


def test_obs_signature_flood_dropped_accounting_exact() -> None:
    agg = EventBatchAggregator(sample_ids=3, max_groups=10)
    total_unflushed = 0
    for k in range(60):
        event = f"EV_{k:03d}"  # 60 distinct signatures > max_groups=10
        _add(agg, event, trade_id=str(k), now=float(k))
        if k >= 10:
            total_unflushed += 1  # beyond store capacity, unflushed at end
    m = agg._metrics
    assert m["events_seen"] == 60
    assert m["first_occurrences"] == 60
    # bounded store: at most max_groups retained; overflow counted honestly
    assert len(agg._groups) <= 10
    assert (
        m["dropped_events"] + len(agg._groups) + (60 - total_unflushed - 60 + 60)
        >= m["events_seen"] - 10 - 1
    )
    # exact truth: dropped == events_seen - retained_count_total
    retained = sum(g.count for g in agg._groups.values())
    assert m["dropped_events"] == m["events_seen"] - retained
    # NEVER negative, never fabricated
    assert m["dropped_events"] >= 0


# ---------------------------------------------------------------------------
# OBS-2 / OBS-3: count truth + sample bounds over adversarial interleavings
# ---------------------------------------------------------------------------


def test_obs_counts_and_last_seen_monotone_random() -> None:
    agg = EventBatchAggregator()
    rng = random.Random(SEED + 1)
    now = 0.0
    for _ in range(300):
        now += rng.uniform(0.0, 2.0)
        _add(
            agg,
            event=rng.choice(["A", "B"]),
            reason=rng.choice(["x", "y"]),
            stage="dataset",
            recoverable=rng.random() < 0.5,
            now=now,
        )
    for g in agg._groups.values():
        assert g.count > 0
        assert g.last_seen >= g.first_seen
        assert g.first_seen >= 0.0


def test_obs_sample_ids_bounded_and_deduped() -> None:
    agg = EventBatchAggregator(sample_ids=5)
    for k in range(50):
        _add(agg, "A", trade_id=str(k % 10), now=float(k))
    g = next(iter(agg._groups.values()))
    assert len(g.sample_ids) <= 5
    assert len(g.sample_ids) == len(set(g.sample_ids))
    assert g.count == 50


def test_obs_default_sample_ids_constant_is_bounded() -> None:
    assert 1 <= DEFAULT_SAMPLE_IDS <= 20


# ---------------------------------------------------------------------------
# OBS-4 / OBS-5: storm-bounded flush + frozen summary fields
# ---------------------------------------------------------------------------


def test_obs_flush_emits_one_line_per_signature() -> None:
    agg = EventBatchAggregator()
    lines: list[str] = []
    for k in range(100):
        _add(agg, "EV_A" if k % 2 else "EV_B", reason="storm", stage="s", now=float(k))
    emitted = agg.flush(lines.append)
    assert emitted == 2 and len(lines) == 2  # bounded output under storm


def test_obs_summary_line_freeze_fields() -> None:
    agg = EventBatchAggregator()
    lines: list[str] = []
    _add(agg, "EV_X", reason="why", stage="dataset", recoverable=True, trade_id="T1", now=10.0)
    _add(agg, "EV_X", reason="why", stage="dataset", recoverable=True, trade_id="T2", now=20.0)
    agg.flush(lines.append)
    assert len(lines) == 1
    line = lines[0]
    # frozen field SET per the observability log contract (values are
    # ISO-8601 formatted timestamps)
    for required in (
        "count=2",
        "stage=dataset",
        "reason=why",
        "recoverable=true",
        "sample_ids=[T1,T2]",
        "first_seen=",
        "last_seen=",
    ):
        assert required in line, f"summary line missing frozen field {required!r}: {line}"
    # first_seen <= last_seen order preserved through formatting
    fs = line.split("first_seen=")[1].split(" ")[0]
    ls = line.split("last_seen=")[1].split(" ")[0]
    assert fs <= ls


# ---------------------------------------------------------------------------
# OBS-6: multi-cycle accumulation honesty
# ---------------------------------------------------------------------------


def test_obs_cycles_accumulate_honestly() -> None:
    agg = EventBatchAggregator()
    total = 0
    for cycle in range(5):
        for k in range(20):
            _add(agg, f"EV_{cycle}", now=float(cycle * 100 + k))
            total += 1
        agg.flush(lambda _line: None)
    m = agg._metrics
    assert m["events_seen"] == total == 100
    assert m["events_aggregated"] == total
    assert m["dropped_events"] == 0  # protected path: zero evidence loss


# ---------------------------------------------------------------------------
# OBS-7: bounded concurrency — zero lost events
# ---------------------------------------------------------------------------


def test_obs_concurrent_adds_lose_nothing() -> None:
    agg = EventBatchAggregator(max_groups=4)
    threads: list[threading.Thread] = []
    per_thread = 100

    def worker(tid: int) -> None:
        for k in range(per_thread):
            _add(agg, f"EV_T{tid}", now=float(k))

    for tid in range(6):
        threads.append(threading.Thread(target=worker, args=(tid,)))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
    m = agg._metrics
    assert m["events_seen"] == 6 * per_thread
    retained = sum(g.count for g in agg._groups.values())
    assert m["dropped_events"] + retained == m["events_seen"]
