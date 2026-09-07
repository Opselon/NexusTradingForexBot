"""Dedicated news-LLM budget (Phase 5) — scoped per-day request/cost accounting.

The provider ledger (strategies/factory/provider.py ProviderUsage) counts
GENERIC lifetime usage on the shared factory provider instance. The news AI
path shares that provider but has NO separate budget: a burst of news
analysis can eat the factory's request window (or the reverse — the factory
budget check silently starves news).

Design (mission Phase 5: 'Do not create a second provider abstraction if the
current provider ledger can represent scoped budgets'):
    * reuse ProviderUsage + the existing provider gate — NO second HTTP
      client, NO second secret, NO second provider build,
    * add a small scoped ledger: NewsBudget tracks the news path's OWN
      requests/tokens/estimated cost per UTC day with a hard daily limit,
    * checked BEFORE any provider call (acquire()), released/committed after,
    * exhaustion -> typed refusal (BudgetExhausted); the caller falls back to
      deterministic/local analysis (already implemented in pro_auto),
    * observable: snapshot() feeds /api/news/health + the weekly report.

Thread-safety: the provider is called from the news worker thread (single
caller per cycle), but acquire() uses a lock anyway — cheap and correct.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.news.budget")

#: Blended cost model matching the factory ledger ($2 per 1M tokens).
COST_PER_TOKEN_USD: float = 0.000_002

#: Defaults (config overridable via NewsConfig.news_budget fields).
DEFAULT_DAILY_REQUEST_LIMIT: int = 120
DEFAULT_DAILY_TOKEN_LIMIT: int = 1_500_000


class BudgetExhaustedError(Exception):
    """Raised by acquire() when the news-LLM daily budget is spent.

    NEVER propagates into trading: the news pipeline catches it and falls
    back to deterministic/local analysis (explicit fallback path, Phase 5B).
    """


@dataclass
class NewsBudgetSnapshot:
    day: str
    requests: int = 0
    successful: int = 0
    failed: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    daily_request_limit: int = DEFAULT_DAILY_REQUEST_LIMIT
    daily_token_limit: int = DEFAULT_DAILY_TOKEN_LIMIT
    exhausted: bool = False
    remaining_requests: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "requests": self.requests,
            "successful": self.successful,
            "failed": self.failed,
            "cache_hits": self.cache_hits,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 4),
            "daily_request_limit": self.daily_request_limit,
            "daily_token_limit": self.daily_token_limit,
            "exhausted": self.exhausted,
            "remaining_requests": self.remaining_requests,
        }


class NewsBudget:
    """Scoped daily budget for the news-LLM path (UTC day boundary)."""

    def __init__(
        self,
        *,
        daily_request_limit: int = DEFAULT_DAILY_REQUEST_LIMIT,
        daily_token_limit: int = DEFAULT_DAILY_TOKEN_LIMIT,
    ) -> None:
        if int(daily_request_limit) < 0 or int(daily_token_limit) < 0:
            raise ValueError("news budget limits must be >= 0")
        self.daily_request_limit = int(daily_request_limit)
        self.daily_token_limit = int(daily_token_limit)
        self._lock = threading.Lock()
        self._day: str = self._today()
        self._requests = 0
        self._successful = 0
        self._failed = 0
        self._cache_hits = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0

    @staticmethod
    def _today() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    def _roll_day_locked(self) -> None:
        today = self._today()
        if today != self._day:
            self._day = today
            self._requests = 0
            self._successful = 0
            self._failed = 0
            self._cache_hits = 0
            self._prompt_tokens = 0
            self._completion_tokens = 0

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def acquire(self) -> None:
        """Reserves ONE request slot. Raises BudgetExhausted when spent.

        Call BEFORE every provider.complete_json; call commit()/release()
        with the usage outcome afterwards.
        """
        with self._lock:
            self._roll_day_locked()
            if self._requests >= self.daily_request_limit:
                raise BudgetExhaustedError(
                    f"news-LLM daily request limit reached ({self.daily_request_limit})"
                )
            if self._prompt_tokens + self._completion_tokens >= self.daily_token_limit:
                raise BudgetExhaustedError(
                    f"news-LLM daily token limit reached ({self.daily_token_limit})"
                )
            self._requests += 1

    def commit(
        self, *, prompt_tokens: int = 0, completion_tokens: int = 0, success: bool = True
    ) -> None:
        """Records usage for the acquired slot (call after the provider call)."""
        with self._lock:
            self._roll_day_locked()
            self._prompt_tokens += max(0, int(prompt_tokens))
            self._completion_tokens += max(0, int(completion_tokens))
            if success:
                self._successful += 1
            else:
                self._failed += 1

    def record_cache_hit(self) -> None:
        """Counts an LLM call avoided (dedup/identity reuse/deterministic skip)."""
        with self._lock:
            self._roll_day_locked()
            self._cache_hits += 1

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    @property
    def exhausted(self) -> bool:
        with self._lock:
            self._roll_day_locked()
            return (
                self._requests >= self.daily_request_limit
                or self._prompt_tokens + self._completion_tokens >= self.daily_token_limit
            )

    @property
    def estimated_cost_usd(self) -> float:
        with self._lock:
            return (self._prompt_tokens + self._completion_tokens) * COST_PER_TOKEN_USD

    def snapshot(self) -> NewsBudgetSnapshot:
        with self._lock:
            self._roll_day_locked()
            total = self._prompt_tokens + self._completion_tokens
            return NewsBudgetSnapshot(
                day=self._day,
                requests=self._requests,
                successful=self._successful,
                failed=self._failed,
                cache_hits=self._cache_hits,
                prompt_tokens=self._prompt_tokens,
                completion_tokens=self._completion_tokens,
                total_tokens=total,
                estimated_cost_usd=total * COST_PER_TOKEN_USD,
                daily_request_limit=self.daily_request_limit,
                daily_token_limit=self.daily_token_limit,
                exhausted=(
                    self._requests >= self.daily_request_limit or total >= self.daily_token_limit
                ),
                remaining_requests=max(0, self.daily_request_limit - self._requests),
            )
