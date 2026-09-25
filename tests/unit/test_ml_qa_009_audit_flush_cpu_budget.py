"""ML-QA-009 — push-gate determinism: audit-flush bounded-wait assert.

Roster candidate #7 of the ML-QA-003 determinism census
(docs/ml-system/test_determinism_roster.md section 6):
``tests/unit/test_audit_flush_contract.py`` carried 4
``time.monotonic()`` probes. One pair (the ``test_flush_returns_false_when_worker_stalled``
bounded-wait probe) was a real *measurement* assert on the WALL clock:
``elapsed = time.monotonic() - started`` then ``assert elapsed < 2.0``.

That assert is exactly the flaky shape the roster catalogues: the fixture
wedges ``flush()`` into its 0.2 s poll loop, and the wall clock measures how
long the OS gives this test, not the boundedness of the code. Under xdist
saturation on a 2-core CI runner a scheduler stall inflates the wait with zero
change in the code under test, and the assert ``elapsed < 2.0`` — 10x the
0.2 s timeout — fails while ``flush()`` behaves perfectly.

This battery pins the remediation (textual analysis — no AuditRepository,
no sqlite, no torch import; runs in the slim venv):

1. no ``time.monotonic()`` / ``time.perf_counter()`` probe remains in the
   module (the wall-clock measurement class is gone);
2. ``time`` is no longer imported at all (dead import removed);
3. the bounded-wait test still asserts ``flush(...) is False`` — the real
   contract (it RETURNED instead of deadlocking) — and still does so with an
   un-wedged-queue cleanup before ``close()``;
4. the boundedness assert now uses the shared
   ``tests/e2e/chain_clock.py::budget_cpu_ms`` CPU-time helper (same helper
   ML-QA-004/007/008 standardised on), with a bound of the same magnitude as
   the removed wall-clock one;
5. every other test in the module is unchanged and its durable contract
   asserts (flush True + rows readable, close drains + idempotent-safe,
   non-SQLite short-circuit, batch salvage + dead-letter) are still present
   and still hard;
6. the module still imports ``budget_cpu_ms`` from ``tests.e2e.chain_clock``
   (the contract that helper exists and stays importable);
7. the test name is unchanged (tests/critical_suite.txt manifest line and
   the roster's §5/§6 references stay valid).
"""

from __future__ import annotations

from pathlib import Path

AUDIT_TEST = Path(__file__).resolve().parent / "test_audit_flush_contract.py"
STALL_TEST = "test_flush_returns_false_when_worker_stalled"
DURABLE_TEST_NAMES = (
    "test_flush_returns_true_and_rows_are_durable",
    "test_flush_on_idle_queue_returns_true_immediately",
    "test_close_drains_pending_writes_and_is_idempotent_safe",
    "test_flush_non_sqlite_short_circuits_true",
    "test_batch_recovery_salvages_good_rows_and_dead_letters_bad",
)


def _source() -> str:
    return AUDIT_TEST.read_text(encoding="utf-8")


def _body(name: str) -> str:
    """Extract a top-level test body (up to the next top-level def)."""
    src = _source()
    start = src.index(f"def {name}")
    nxt = src.find("\ndef ", start + 1)
    return src[start:] if nxt == -1 else src[start:nxt]


def test_module_has_no_wall_clock_probe() -> None:
    """Rule 1: the wall-clock measurement class is gone from the module."""
    src = _source()
    assert "time.monotonic" not in src, (
        "time.monotonic() reappeared in test_audit_flush_contract.py — a "
        "wall-clock probe in a push-gate test measures the CI scheduler, not "
        "the boundedness of flush()"
    )
    assert "time.perf_counter" not in src, (
        "time.perf_counter() probe in a push-gate test — same flake class"
    )


def test_time_import_removed() -> None:
    """Rule 2: the now-unused time import is gone, not left dangling."""
    src = _source()
    assert "\nimport time\n" not in src, (
        "the time import is unused after the CPU-time remediation and must be "
        "removed — a bare unused import invites the wall-clock class back"
    )


def test_budget_cpu_ms_is_imported() -> None:
    """Rule 6/0: the shared deterministic-clock helper is the clock source."""
    src = _source()
    assert "from tests.e2e.chain_clock import budget_cpu_ms" in src
    assert "budget_cpu_ms(" in src


def test_stall_test_exists_under_original_name() -> None:
    """Rule 7: manifest/roster references depend on the test name."""
    assert f"def {STALL_TEST}" in _source()


def test_stall_test_keeps_the_real_contract_assert() -> None:
    """Rule 3: the load-bearing assert is unchanged and still hard.

    The contract under test is ``flush()`` RETURNING False instead of
    deadlocking the live path when its worker is wedged. That invariant is
    untouched by the clock change; boundedness *behaviour* is proven by the
    return value, and a genuine hang is caught by the CI test timeout, never
    by a wall-clock magnitude assert.
    """
    body = _body(STALL_TEST)
    assert "ok = repo.flush(timeout_sec=0.2)" in body
    assert "assert ok is False" in body
    # the wedged queue is still unwedged before close() so the teardown drains
    assert "repo._queue = real_queue" in body
    assert "repo.close()" in body


def test_stall_test_boundedness_uses_cpu_time_budget() -> None:
    """Rule 4: the bound is now a CPU-time budget via the shared helper."""
    body = _body(STALL_TEST)
    # the old wall-clock pair is gone from this test body
    assert "time.monotonic" not in body
    # the CPU-time budget context manager wraps the flush call
    with_budget = "with budget_cpu_ms(2000.0) as sw:" in body
    assert with_budget, "the flush call must be wrapped in budget_cpu_ms(...)"
    flush_idx = body.index("ok = repo.flush")
    budget_idx = body.index("with budget_cpu_ms(")
    assert budget_idx < flush_idx, "budget_cpu_ms must wrap the flush call"
    # the assert reads the CPU-time stopwatch, with a bound of the same
    # magnitude as the removed wall-clock one (2.0 s)
    assert "sw.consumed_ms < 2000.0" in body
    assert "CPU-time bound" in body


def test_durable_contract_asserts_unchanged() -> None:
    """Rule 5: the other five tests keep their durable contract asserts."""
    src = _source()
    for name in DURABLE_TEST_NAMES:
        assert f"def {name}" in src, f"durable-contract test {name} was removed"
    # hard asserts of the durable properties, kept verbatim
    body = _body("test_flush_returns_true_and_rows_are_durable")
    assert "assert repo.flush(timeout_sec=10.0) is True" in body
    assert "assert repo._queue.unfinished_tasks == 0" in body
    assert "assert _count_rows(repo) == 5" in body
    idle = _body("test_flush_on_idle_queue_returns_true_immediately")
    # ML-QA-004 already moved this test onto the CPU budget; it must stay
    assert "budget_cpu_ms(2000.0)" in idle
    close_body = _body("test_close_drains_pending_writes_and_is_idempotent_safe")
    assert "assert _count_rows(repo) == 10" in close_body
    salvage = _body("test_batch_recovery_salvages_good_rows_and_dead_letters_bad")
    assert "assert drained is True" in salvage
    assert "assert _count_rows(repo) >= 1" in salvage
    assert "assert repo.audit_batch_failures >= 1" in salvage
    non_sqlite = _body("test_flush_non_sqlite_short_circuits_true")
    assert "assert repo.flush(timeout_sec=0.1) is True" in non_sqlite


def test_audit_repository_contract_untouched() -> None:
    """The remediation is test-only: no production source changed for it."""
    # The test reaches flush()'s poll loop through a queue seam; it does not
    # patch or subclass the repository's timeout/deadline logic.
    body = _body(STALL_TEST)
    assert "unfinished_tasks" in body  # the seam: the polled property itself
    assert "timeout_sec=0.2" in body  # the production timeout path is driven
    assert "AuditRepository.__new__" not in body  # no repository surgery here
