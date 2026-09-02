# tests/unit/test_telegram_notifier.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- Production-hardening tests for TelegramNotifier + OrderManager integration: dedup, rate limiting, queue capacity, async callbacks, HTML escaping, truncation, retry semantics.
- Guards: send success (msg_id == 42); DEDUPLICATION — same content within window → `second_id is None` (no duplicate send); rate limiting enforced; QUEUE CAPACITY LIMIT — overflow returns None (`res is None`, `res_crit is None` — dropped, not crashed); HTML escaping (`&lt;script&gt;...` escape + truncation).
- Async: callback fired asynchronously; `slow_urlopen` simulates network latency.
- OrderManager integration: telegram thread replies to manual commands; extended notifications fire.
- Retry semantics: FAILED send retried and NOT marked deduplicated (`test_telegram_notifier_failed_send_retry_not_deduplicated`); distinct messages never conflated.
- 15 defs / 336 lines; network faked via urlopen monkeypatch.