# src/nexus_scalp/forensics/models.py

- PURPOSE: Forensic health check result contract (TASK-11 §3/§4/§5): the
  five-level status vocabulary and the full evidence envelope every check
  must return (check_id | timestamp | status | duration_ms | evidence |
  observed | expected | correlation_id). Status is NEVER only PASS/FAIL —
  UNKNOWN is first-class and never converted to PASS or zero.
- ARCHITECTURE LAYER: Domain (contracts).
- RESPONSIBILITY: HealthStatus (severity ranks, is_healthy, is_blocking),
  ForensicCheckError, new_correlation_id, CheckResult (frozen envelope),
  worst_status, severity_label.
- DEPENDENCIES: dataclasses, hashlib, time, datetime, enum.
- CONNECTS TO: every forensics check, engine (snapshot aggregation),
  deploy gate, telegram report, trend, dashboard.
- KEY CONCEPTS:
  - HealthStatus five levels with severity ints: PASS=0, WARNING=1,
    DEGRADED=2, CRITICAL=3, UNKNOWN=2 — "UNKNOWN is never ignorable":
    it outranks WARNING and equals DEGRADED, so an unverifiable check
    cannot be swept under WARNING.
  - is_blocking(): ONLY CRITICAL blocks deployment; UNKNOWN/DEGRADED
    never silently block (they route to REVIEW_REQUIRED in the gate).
  - CheckResult: frozen dataclass; evidence (short proof string),
    observed (structured dict), expected (contract string), detail
    (human dashboard line); correlation_id default
    new_correlation_id() = sha256("fh:{time_ns}")[:16].
  - ForensicCheckError: a check that RAISES is surfaced as
    UNKNOWN/CRITICAL evidence, never a PASS (enforced by _safe in
    checks.py and the engine isolation boundary).
  - worst_status: max by severity — NEVER averages criticals away;
    empty list → UNKNOWN.
- HOT PATH / PERFORMANCE: constructor-only cost; new_correlation_id hashes
  ns-timestamp (collision-safe at process scale).
- EDGE CASES & PITFALLS: severity ordering ties UNKNOWN with DEGRADED
  (2) — worst_status(['UNKNOWN','WARNING']) → UNKNOWN (max returns the
  FIRST max element by key; UNKNOWN precedes DEGRADED in list order only
  when listed first — max() with a key returns the first maximal
  element, so ordering of ties is deterministic-but-input-dependent);
  severity_label maps for UI only; the frozen dataclass is re-created
  (not mutated) by the engine to stamp duration_ms.