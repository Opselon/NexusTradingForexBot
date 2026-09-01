"""Telegram notifier shared structural protocol (Agent-5 modularization).

The mixins in tg_transport/tg_notifications are stateless method carriers;
this Protocol gives type-checkers the core notifier surface (state + helper
methods) without runtime inheritance changes. Typing only — no behavior.
"""

from __future__ import annotations

from typing import Any, Protocol


class _TelegramCoreProto(Protocol):
    """Structural view of ``TelegramNotifier`` core for mixin type-checking."""

    bot_token: str
    admin_id: str
    enabled: bool
    environment: str
    minimum_severity: str
    timeout_seconds: float
    maximum_retries: int
    retry_backoff: float
    queue_capacity: int
    rate_limit: int
    api_base: str
    _queue: Any
    _lock: Any
    _last_dns_poisoned: bool
    _recent_messages: Any
    _send_url: str
    _me_url: str

    def _escape(self, text: Any) -> str: ...

    def _truncate_message(self, text: str) -> str: ...

    def _redact_secrets(self, text: str) -> str: ...

    def _is_duplicate_or_cooling_down(self, html_text: str) -> bool: ...

    def send(self, *args: Any, **kwargs: Any) -> Any: ...
