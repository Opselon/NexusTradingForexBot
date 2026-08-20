# src/nexus_scalp/forensics/engine.py

- PURPOSE: ForensicHealthEngine — the centralized POST-70D continuous
  health engine (TASK-11 §3/§49/§50/§54): orchestrates the read-only
  check matrix, classifies the aggregate FORENSIC_HEALTH_SNAPSHOT
  (never averaging criticals away), keeps in-memory + on-disk health
  history, and throttles alert severity classes (§54). EXPLICITLY NOT a
  self-modifying system (§0/§55): it detects, diagnoses, quarantines (by
  flagging) and blocks unsafe startup/deployment only when a mandatory
  check is CRITICAL.
- ARCHITECTURE LAYER: Application (monitoring engine).
- RESPONSIBILITY: check matrix (17 named groups), run_checks, snapshot,
  alert throttling (_fire_alerts/_fire), can_deploy, persistence
  (_persist/load_persisted), dashboard, auto-freeze of golden references.
- DEPENDENCIES: forensics.checks (as C), forensics.models, forensics.
  references, json, time, logging.
- CONNECTS TO: deploy gate (snapshot), TelegramReportScheduler
  (snapshot), dashboard/API, checks module.
- KEY CONCEPTS:
  - CHECK GROUPS (the dashboard rows): FeatureContract (4), Model (2),
    Parity (2: causal canary + training/live parity), Dataset (1),
    Accounting (4: divergence, dup outcome, impossible excursion,
    experience gap), Database (3: integrity, migration, growth),
    Liquidity (1), News (3), Shadow (1), Governance (2), UI (2), API (2),
    Telegram (1), Trace (3), Workers (1), Runtime (1), Performance (1).
  - run_checks: per-check isolation boundary — any raising check becomes
    a CHECK-RAISED UNKNOWN result; duration_ms stamped.
  - snapshot(): group status = worst per group; overall = worst over
    ALL check results (never average); counts per severity; correlation
    id from the first check; appends to bounded history (max_history=50),
    persists snapshot JSON + history.jsonl (bounded rolling), fires
    alerts, returns the record.
  - ALERT_POLICY (§54): immediate checks fire EVERY failing run
    (schema/vector/model/db/dup-outcome/migration/parity/leakage/
    champion-identity); aggregated checks at most once per
    AGGREGATED_WINDOW_SEC=15min (liquidity drift, news degradation);
    periodic (performance) once per PERIODIC_WINDOW_SEC=1h. Only
    CRITICAL statuses alert; _active_blockers = immediate+aggregated
    fired this run.
  - can_deploy(): snapshot(persist=False); blockers = any CRITICAL
    check; returns (not blockers, blockers) — the release pre-flight §44.
  - _auto_freeze_references (TASK-12 §23): when docs/
    LIQUIDITY_70D_GOLDEN_BASELINE.json exists and the registry is empty,
    load the frozen liquidity references (provenance-guarded via the
    golden doc).
  - dashboard(): snapshot + per-check rows with last_check/evidence/
    detail_view incl. RECOMMENDED_ACTION from _recommended_action
    (prefix-matched guidance per family).
- HOT PATH / PERFORMANCE: snapshot runs on report/periodic cadence —
    the full matrix touches several DBs read-only; NOT on the tick path.
- EDGE CASES & PITFALLS: correlation_id of a snapshot with zero checks
    falls back to an ISO timestamp; ALERT_POLICY keys not present →
    "aggregated" default; _fire uses monotonic time vs the state file
    timestamps (persist uses ISO) — no cross-restart dedup for alerts
    (alerts are per-process); _recommended_action matches on
    check_id[:9] — families with shorter prefixes fall back to the
    generic line; auto-freeze runs once per engine construction.