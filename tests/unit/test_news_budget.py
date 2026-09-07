"""Focused tests: news-LLM budget (Phase 5) — within/exhaustion/fallback/accounting."""

from __future__ import annotations

import pytest

from nexus_scalp.news.budget import (
    BudgetExhaustedError,
    NewsBudget,
    NewsBudgetSnapshot,
)


def test_budget_allows_within_limit() -> None:
    b = NewsBudget(daily_request_limit=3, daily_token_limit=10_000)
    b.acquire()
    b.commit(prompt_tokens=100, completion_tokens=50, success=True)
    snap: NewsBudgetSnapshot = b.snapshot()
    assert snap.requests == 1
    assert snap.successful == 1
    assert snap.total_tokens == 150
    assert snap.estimated_cost_usd == pytest.approx(150 * 0.000_002)
    assert not snap.exhausted


def test_budget_exhaustion_raises_typed_error() -> None:
    b = NewsBudget(daily_request_limit=2, daily_token_limit=10_000)
    b.acquire()
    b.commit(prompt_tokens=0, completion_tokens=0, success=True)
    b.acquire()
    b.commit(prompt_tokens=0, completion_tokens=0, success=True)
    with pytest.raises(BudgetExhaustedError, match="request limit"):
        b.acquire()
    assert b.exhausted


def test_budget_token_limit_triggers_exhaustion() -> None:
    b = NewsBudget(daily_request_limit=100, daily_token_limit=1000)
    b.acquire()
    b.commit(prompt_tokens=800, completion_tokens=200, success=True)
    with pytest.raises(BudgetExhaustedError, match="token limit"):
        b.acquire()


def test_budget_failed_call_still_counts_request() -> None:
    b = NewsBudget(daily_request_limit=1, daily_token_limit=10_000)
    b.acquire()
    b.commit(prompt_tokens=0, completion_tokens=0, success=False)
    assert b.snapshot().failed == 1
    with pytest.raises(BudgetExhaustedError):
        b.acquire()


def test_budget_cache_hits_are_observable() -> None:
    b = NewsBudget(daily_request_limit=10, daily_token_limit=10_000)
    b.record_cache_hit()
    b.record_cache_hit()
    snap = b.snapshot()
    assert snap.cache_hits == 2
    assert snap.requests == 0  # cache hits cost nothing


def test_budget_snapshot_is_dict_safe() -> None:
    b = NewsBudget()
    d = b.snapshot().to_dict()
    assert d["day"]  # UTC day string present
    assert d["remaining_requests"] == b.daily_request_limit
    assert d["exhausted"] is False


def test_budget_zero_limit_blocks_everything() -> None:
    b = NewsBudget(daily_request_limit=0, daily_token_limit=0)
    with pytest.raises(BudgetExhaustedError):
        b.acquire()
