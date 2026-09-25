# src/nexus_scalp/forensics/trend.py

- PURPOSE: Health snapshot trend analysis (TASK-12 §34) — read-only
  comparison of the current FORENSIC_HEALTH_SNAPSHOT against the
  previous one: new failures, resolved failures, worsened warnings,
  improved warnings, new unknowns, resolved unknowns. Never mutates
  anything.
- ARCHITECTURE LAYER: Application (analysis).
- RESPONSIBILITY: compare_snapshots (status-transition classifier),
  load_history (history.jsonl → SnapshotRecord list, oldest first),
  latest_trend (last two persisted snapshots).
- DEPENDENCIES: forensics.engine (SnapshotRecord), forensics.models
  (HealthStatus), json, pathlib.
- CONNECTS TO: dashboard trend panel, periodic report add-ons, operator
  review of history.
- KEY CONCEPTS:
  - Rank ordering for worsening/improvement: PASS=0 < WARNING=1 <
    UNKNOWN=2 < DEGRADED=3 < CRITICAL=4 (UNKNOWN ranks above WARNING —
    an unknown check counts as a worsening from WARNING).
  - compare_snapshots transitions:
    - new_failures: DEGRADED/CRITICAL now with previous PASS/WARNING/
      UNKNOWN, or from ABSENT (check did not exist before).
    - resolved_failures: previous DEGRADED/CRITICAL → now PASS/WARNING.
    - worsened/improved: any rank increase/decrease (includes the
      failure/resolution sets — overlapping by design; UNKNOWN →
      DEGRADED is both new_failure and worsened).
    - new_unknowns / resolved_unknowns: UNKNOWN transitions in/out.
  - previous=None (no history) → previous_available=False with empty
    lists.
  - load_history: appends each valid JSON line as a SnapshotRecord
    (skips corrupt lines); OSError → empty list (no raise).
  - latest_trend: needs >= 2 records; compares the LAST two.
- HOT PATH / PERFORMANCE: runs on dashboard/report read cadence;
  compares O(checks) dict lookups.
- EDGE CASES & PITFALLS:
  - trend.py line 55: `prev_status = prev.get(cid, PASS)` computed then
    immediately overwritten by `prev_status = prev.get(cid)` (the
    default-PASS line is dead code — truly absent checks get prev=None
    and are handled in the ABSENT branch; the intended "new check =
    was pass?" semantics is NOT applied).
  - The rank map puts UNKNOWN (2) above WARNING (1) — an UNKNOWN→WARNING
    transition counts as IMPROVED even though it may be a resolution of
    "cannot determine" into a real warning.
  - worsened and new_failures overlap (a WARNING→CRITICAL is in both).
  - SnapshotRecord reconstruction from JSON loses the typed evidence
    (checks are raw dicts — fine for trend diffs).