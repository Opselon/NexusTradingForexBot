# src/nexus_scalp/strategies/factory/telegram.py

- PURPOSE: Strategy Factory — Telegram lifecycle reports (2026-08-20, spec
  46/47): meaningful lifecycle events through the existing TelegramNotifier
  queue — NEVER spamming hundreds of individual messages: one Generation
  Started, one Generation Progress, one Generation Completed, one per
  Important Strategy Found / Elite Promotion / Rejection cause-class /
  Research Failure. The same build_* functions power the UI event stream.
- ARCHITECTURE LAYER: Application notification formatting (pure builders +
  a notifier passthrough; no order authority, never raises).
- RESPONSIBILITY: bounded event-type allowlist, structured HTML-escaped
  message builders, and the single routing function used by the
  orchestrator.
- DEPENDENCIES: `observability.logging`, stdlib html.
- CONNECTS TO: orchestrator._send_telegram (GENERATION_STARTED /
  GENERATION_COMPLETED), TelegramNotifier (injected `notifier` with
  .enabled/.send), UI event stream.

- KEY CONCEPTS:
  - `FACTORY_EVENT_TYPES` (26-40): the allowlist — GENERATION_STARTED /
    GENERATION_COMPLETED / GENERATION_PROGRESS / IMPORTANT_STRATEGY_FOUND /
    ELITE_PROMOTED / STRATEGY_REJECTED / RESEARCH_FAILURE / SYSTEM_FAILURE /
    LOOP_PAUSED / LOOP_RESUMED / DEPLOYMENT_GATE. Anything outside is
    silently dropped.
  - Builders (all html-escaped, emoji-prefixed, compact):
    - build_generation_started (47-53): generation id + population + mode.
    - build_generation_completed (56-78): population/structurally valid/
      evaluated/validated/rejected/elite counts, best/median score,
      diversity, runtime, top-3 failure modes.
    - build_strategy_rejected (81-91): failure report — id, generation,
      stage, reason, detail (spec 47).
    - build_elite_promoted (94-101): id, generation, score, rank.
    - build_failure_alert (104-109): stage + error (truncated to 200 chars).
    - build_generation_progress (112-118): done/total + pct.
  - `send_factory_event(notifier, event_type, payload, severity='INFO')`
    (121-162): disabled/missing notifier ⇒ False; unknown event type ⇒
    False; maps type → builder (RESEARCH/SYSTEM_FAILURE escalate severity
    to ERROR; LOOP_PAUSED/RESUMED use fixed strings; DEPLOYMENT_GATE and
    others get a generic structured line); calls notifier.send(text,
    severity, event_type="STRATEGY_FACTORY") inside try/except — a notifier
    failure is logged and returns False (never raises).
- HOT PATH / PERFORMANCE: formatting is trivial; events are per-lifecycle
  (not per candidate) — no spam; off the tick path.
- EDGE CASES & PITFALLS:
  - The generic fallback branch (lines 153-156) only renders payload items
    that are str/int/float — dict/list payloads (e.g. the "summary" object
    in GENERATION_COMPLETED... which is handled by its own builder) are
    skipped in the DEPLOYMENT_GATE-style branches.
  - `build_generation_completed` uses `best:.2f`/`median_score:.2f`/etc.
    formatting — a payload whose values are strings (e.g. from a
    JSON-decoded row where summary numbers were serialized as text) raises
    ValueError inside the BUILDER, which send_factory_event does NOT catch
    (its try/except wraps only notifier.send) — a formatter crash
    propagates to the orchestrator's `_send_telegram` which catches it and
    logs; the event is lost but the factory survives.
  - `html.escape` with quote=False leaves double-quote characters
    unescaped inside HTML attributes — harmless for Telegram's HTML parse
    mode on text content.
  - `build_strategy_rejected`'s Detail field is raw-string-truncated at
    200 chars only in build_failure_alert; the rejected report emits full
    detail — bounded by the caller's payload, not here.