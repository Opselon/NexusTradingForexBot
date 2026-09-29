"""Regression tests for the payload-archival executor.

``RetentionEngine.archive_eligible_payloads`` is the SCHEDULER half of the
DB-LIFECYCLE payload contract: ``classify()`` returns a verdict, this method
runs the bounded archival that verdict describes. It is the method a periodic
purge scheduler will call, so its boundary behaviour is load-bearing.

Two defects are pinned here:

1. ``max_rows=0`` is a legal bounded request ("do nothing this tick") and must
   still reach the operation. An early ``return`` on falsy ``max_rows`` would
   make a scheduler that sizes its batch as ``max(0, budget - spent)``
   silently skip archival forever at the end of a budget-exhausted pass, and
   the caller could not tell that from "nothing eligible".
2. ``archive_op`` counters are read defensively: an operation that reports a
   partial counter dict must not raise ``KeyError`` inside the scheduler.

These are contract tests, not implementation tests: they assert the interface
any archival op must satisfy, using a recording fake rather than a real
database.
"""

from __future__ import annotations

from typing import Any

import pytest

from nexus_scalp.hygiene.retention import NEWS_RETENTION, RetentionEngine


class _RecordingOp:
    """Callable archival op that records its kwargs and returns fixed counters."""

    def __init__(self, counters: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._counters = (
            counters
            if counters is not None
            else {
                "eligible": 7,
                "archived": 5,
                "skipped": 2,
                "bytes_before": 4096,
                "bytes_after": 0,
                "rounds": 1,
            }
        )

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return dict(self._counters)


@pytest.fixture
def engine() -> RetentionEngine:
    return RetentionEngine.for_database("news")


def test_news_articles_is_registered_as_an_archival_target(engine: RetentionEngine) -> None:
    """The executor must have a target for the news domain, or it is dead code."""
    assert ("news", "news_articles") in RetentionEngine._ARCHIVE_OPS
    assert engine.rule_for("news_articles") is not None


def test_payload_policy_coexists_with_never_delete_row_policy() -> None:
    """never_delete protects the ROW; archival reclaims a DUPLICATE PAYLOAD."""
    rule = NEWS_RETENTION["news_articles"]
    assert rule.never_delete is True
    assert rule.archive_after_days is not None
    # Both statements hold at once — that independence is the whole point.
    assert rule.is_archive_candidate(10_000.0) is True


def test_executes_and_propagates_counters(engine: RetentionEngine) -> None:
    op = _RecordingOp()
    out = engine.archive_eligible_payloads("news", archive_op=op)

    assert out["table"] == "news_articles"
    assert out["verdict"] == "ARCHIVE"
    assert out["dry_run"] is False
    assert out["archived"] == 5
    assert out["bytes_before"] == 4096
    assert out["counters"]["skipped"] == 2
    assert len(op.calls) == 1
    assert op.calls[0]["dry_run"] is False


def test_dry_run_is_shadow_mode_and_still_measures(engine: RetentionEngine) -> None:
    op = _RecordingOp()
    out = engine.archive_eligible_payloads("news", dry_run=True, archive_op=op)

    assert out["verdict"] == "DRY_RUN"
    assert out["dry_run"] is True
    # A dry run must still reach the op (it is the op that measures) and must
    # tell it not to mutate.
    assert len(op.calls) == 1
    assert op.calls[0]["dry_run"] is True


def test_max_rows_exhausted_boundary_still_reaches_the_op(engine: RetentionEngine) -> None:
    """max_rows=0 is a legal bounded request, not an early return.

    A scheduler that sizes each tick as ``max(0, budget - spent)`` passes 0 on
    an exhausted pass; that must be an explicit bounded no-op on the operation,
    never a scheduler-level skip the caller cannot distinguish from idle.
    """
    op = _RecordingOp()
    out = engine.archive_eligible_payloads("news", max_rows=0, archive_op=op)

    assert len(op.calls) == 1
    assert op.calls[0]["max_rows"] == 0
    assert len(op.calls) == 1, "the op must be called exactly once"
    assert out["table"] == "news_articles"


def test_batch_size_and_max_rows_are_forwarded(engine: RetentionEngine) -> None:
    op = _RecordingOp()
    engine.archive_eligible_payloads("news", batch_size=25, max_rows=100, archive_op=op)

    assert op.calls[0] == {"batch_size": 25, "max_rows": 100, "dry_run": False}


def test_partial_counter_dict_does_not_raise(engine: RetentionEngine) -> None:
    """An op reporting fewer counters must not crash the scheduler."""
    op = _RecordingOp(counters={"archived": 3})
    out = engine.archive_eligible_payloads("news", archive_op=op)

    assert out["archived"] == 3
    assert out["bytes_before"] == 0


def test_no_operation_resolved_is_a_noop(engine: RetentionEngine) -> None:
    """An unresolvable op returns the default envelope and calls nothing."""
    out = engine.archive_eligible_payloads("news")

    assert out["verdict"] == "KEEP"
    assert out["table"] is None
    assert out["counters"] == {}


def test_unknown_database_is_a_noop(engine: RetentionEngine) -> None:
    op = _RecordingOp()
    out = engine.archive_eligible_payloads("audit", archive_op=op)

    assert out["verdict"] == "KEEP"
    assert out["table"] is None
    assert op.calls == []


def test_default_envelope_is_stable() -> None:
    """The returned shape is a contract for the scheduler that reads it."""
    out = RetentionEngine().archive_eligible_payloads("news")
    assert set(out) == {"table", "dry_run", "verdict", "counters"}
