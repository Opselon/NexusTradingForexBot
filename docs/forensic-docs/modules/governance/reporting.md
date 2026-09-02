# src/nexus_scalp/governance/reporting.py

- PURPOSE: Canonical MODEL SHADOW UPDATE Telegram report (TASK-6 spec 29).
  Consumes ONLY canonical model-governance data (shadow runtime summary,
  health envelope, promotion state). NEVER claims "Challenger ready"
  unless the promotion gate actually says READY_FOR_REVIEW / APPROVED.
- ARCHITECTURE LAYER: Application / reporting adapter (read-only consumer).
- RESPONSIBILITY: model_shadow_update_text (formats the telegram text),
  build_governance_report (collects the snapshot from a LiveEngine).
- DEPENDENCIES: observability.logging; LiveEngine internals accessed
  defensively via getattr in build_governance_report.
- CONNECTS TO: LiveEngine (governance_engine / _governance_shadow /
  champion_manager / governance_store / shadow_engine), Telegram delivery
  pipeline, forensics telegram_report (separate periodic reporter).
- KEY CONCEPTS:
  - model_shadow_update_text: champion/challenger identity, shadow
    samples/errors/dropped/timeouts, avg & p95 latency; the status line
    is derived from the promotion_state argument: only
    READY_FOR_REVIEW/APPROVED/CHAMPION render "⚠️ PROMOTION REVIEW
    OPEN — OPERATOR ACTION REQUIRED"; everything else renders
    "⛔ NO PROMOTION". Evidence floor line when samples < sample_floor
    (default 30).
  - build_governance_report: returns None when the engine lacks
    governance wiring (offline/safe); champion from
    champion_manager.champion_or_none (healthy = available); challenger
    from _governance_shadow.summary(); shadow counters incl. running =
    shadow_engine.active_run_id truthy; promotion_state read from the
    governance store row for the challenger (default SHADOW). All access
    wrapped; any exception → None + error log.
- HOT PATH / PERFORMANCE: periodic only (report cadence).
- EDGE CASES & PITFALLS: build_governance_report depends on private
  attributes of LiveEngine (engine._governance_shadow,
  engine.shadow_engine.active_run_id) — any rename silently yields
  empty/None values; champion id fallback "?"; a challenger whose state
  row was never written defaults to SHADOW (report may understate
  READY_FOR_REVIEW after a manual store update race).