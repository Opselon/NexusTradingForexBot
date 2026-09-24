"""ML-QA-008 — push-gate latency determinism: experiment-registry benchmark.

Roster candidate #6 of the ML-QA-003 determinism census
(docs/ml-system/test_determinism_roster.md section 6): tests/unit/
test_experiment_registry.py carried 4 ``time.perf_counter()`` probes inside
the BENCHMARK_PLAN test (1,000 registrations + top-10 query < 50ms), which is
registered in tests/critical_suite.txt and therefore runs on every push under
``pytest -n auto``. A wall-clock 50ms bound under xdist saturation measures
the scheduler, not the query.

This battery pins the remediation, textually (no registry import, no sqlite,
runs in the slim venv):

1. no ``time.perf_counter()`` probe remains anywhere in the module;
2. both benchmark legs measure ``time.process_time()`` (CPU time);
3. a warmup register/finalize/query precedes the first timed probe, so
   sqlite connection/schema setup is not charged to leg 1;
4. the load-bearing asserts are NOT weakened: 1000-record loop, top-10
   ordering + first-element check, the 50ms CPU query budget, and the
   register-throughput assert all remain;
5. the benchmark test name is unchanged (manifest/roster references stay
   valid).
"""

from __future__ import annotations

from pathlib import Path

REGISTRY_TEST = Path(__file__).resolve().parent / "test_experiment_registry.py"
BENCH_NAME = "test_benchmark_1000_records_top10_under_50ms"


def _source() -> str:
    return REGISTRY_TEST.read_text(encoding="utf-8")


def _bench_body() -> str:
    """Extract the BENCHMARK_PLAN test body (up to the next top-level def)."""
    src = _source()
    start = src.index(f"def {BENCH_NAME}")
    nxt = src.find("\ndef ", start + 1)
    return src[start:] if nxt == -1 else src[start:nxt]


def test_registry_module_has_no_wall_clock_probe() -> None:
    """Rule 1: the wall-clock probe class is gone from the whole module."""
    assert "time.perf_counter" not in _source(), (
        "time.perf_counter() reappeared in test_experiment_registry.py — "
        "wall-clock probes in a push-gate test measure the CI scheduler"
    )


def test_benchmark_exists_under_original_name() -> None:
    """Rule 5: manifest/roster references depend on the test name."""
    assert f"def {BENCH_NAME}" in _source()


def test_both_legs_measure_cpu_time() -> None:
    """Rule 2: register leg and query leg are both time.process_time()."""
    body = _bench_body()
    assert body.count("time.process_time()") >= 4, (
        "both benchmark legs must start and end on time.process_time()"
    )
    assert "t0 = time.process_time()" in body
    assert "t1 = time.process_time()" in body
    assert "register_ms = (time.process_time() - t0) * 1000.0" in body
    assert "query_ms = (time.process_time() - t1) * 1000.0" in body


def test_warmup_precedes_first_timed_probe() -> None:
    """Rule 3: setup cost (sqlite connect/schema) is warmed up, then timed."""
    body = _bench_body()
    warmup_at = body.find("exp_bench_warmup")
    timed_at = body.find("t0 = time.process_time()")
    assert warmup_at != -1, "warmup register/finalize/query block missing"
    assert timed_at != -1
    assert warmup_at < timed_at, "warmup must run before the first timed probe"
    # warmup covers all three operations, not just register
    warmup_section = body[:timed_at]
    assert "reg.register(" in warmup_section
    assert "_finalize(" in warmup_section
    assert "reg.top_n(" in warmup_section


def test_load_bearing_asserts_not_weakened() -> None:
    """Rule 4: remediation must not silently delete the gate's meaning."""
    body = _bench_body()
    # scale: still registers n=1000 and finalizes every 3rd record
    assert "n = 1000" in body
    assert "for i in range(0, n, 3)" in body
    # result correctness: size, best element, monotone ordering
    assert "assert len(top) == 10" in body
    assert 'assert top[0].metrics["val_loss"] == pytest.approx(0.0)' in body
    assert 'top[i].metrics["val_loss"] <= top[i + 1].metrics["val_loss"]' in body
    # the budget itself: 50ms, still hard-gated (CPU time now)
    assert "assert query_ms < 50.0" in body
    assert "budget 50ms" in body
    # throughput probe still asserted non-negative
    assert "assert register_ms >= 0.0" in body


def test_budget_is_cpu_time_not_wall_clock() -> None:
    """Rule 4b: the failure message documents the clock it measures."""
    body = _bench_body()
    assert "(budget 50ms, CPU time)" in body
    assert "process_time" in body
    assert "perf_counter" not in body
