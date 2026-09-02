# src/nexus_scalp/incidents/telegram.py

- PURPOSE: Incident Telegram alerts (TASK-12 spec 48/49) — CRITICAL/HIGH
  incidents notified through the existing TelegramNotifier infrastructure
  (never re-implements bot plumbing). Message includes incident_id,
  severity, component, symptom, root-cause status, impact, correlation_id;
  NO stack traces (spec 48).
- ARCHITECTURE LAYER: Application adapter (alerting onto observability
  Telegram).
- RESPONSIBILITY: throttled/deduped alert dispatch — first occurrence
  alerts immediately; repeats within the cooldown window are summarized
  with repeat_count (spec 49: one root incident must not spam 500 times).
- DEPENDENCIES: incidents.models.Incident, logging; the injected notifier
  (observability TelegramNotifier) with .send(text, severity, event_type,
  correlation_id) and .enabled.
- CONNECTS TO: incidents worker (_telegram, cooldown 900s / repeat 3600s),
  web diagnostics (alerts_sent/suppressed), tests.
- KEY CONCEPTS:
  - ALERT_SEVERITIES = (CRITICAL, HIGH) — MEDIUM+ only when the worker was
    constructed with telegram_min_severity="MEDIUM" (worker line 273-275).
  - should_alert (line 73): disabled → False; severity not eligible →
    False; first occurrence → True; else True only after repeat_cooldown
    (3600s default) has elapsed since last_alerted_at. NOTE: the 900s
    cooldown_sec is declared but the decision uses ONLY repeat_cooldown —
    cooldown_sec is effectively dead configuration.
  - maybe_alert (line 87): tracks IncidentAlertState per incident_id
    (first_alerted_at/last_alerted_at/repeat_count/repeat_alerts_sent);
    suppressed counter when throttled; _trim keeps the ring ≤2000 entries
    dropping entries idle >24h.
  - _dispatch (line 109): notifier None or not enabled → BLOCKED_NOT_
    CONFIGURED warning, returns False (alerts_sent NOT incremented but the
    state was already updated in maybe_alert — repeat bookkeeping still
    advances on failed sends); send() exceptions → SEND_FAILED error log,
    False.
  - _format (line 133): HTML-ish message — "🚨 INCIDENT ALERT" vs
    "🔁 INCIDENT REPEAT", emoji field lines, code-wrapped ids, repeat
    count, BUG linkage when present; no raw exceptions (spec 48).
- HOT PATH / PERFORMANCE: O(1) per incident; ring trim amortized at 2000
  entries; all in-memory.
- EDGE CASES & PITFALLS: cooldown_sec (900.0) is never consulted — only
  repeat_cooldown_sec governs (line 83); repeat bookkeeping advances
  BEFORE dispatch success, so a failing notifier still inflates
  repeat_count and suppresses future alerts via updated last_alerted_at
  (an outage masks repeats); notifier send signature assumed
  (severity/event_type named args) — a mismatched adapter raises and is
  caught; repeat_alerts_sent is tracked but never exposed in the status
  output.