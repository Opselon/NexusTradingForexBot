# 70D Incident Response Model — canonical incident structure (TASK-12)

> Author: AGENT-12 (Hermes-Forensic-12)
> Task: TASK-12-FORENSIC-INCIDENT-RESPONSE
> Source of truth: `src/nexus_scalp/incidents/` (executable code governs)

## 1. Purpose

The system must evolve from **"something failed"** to a full forensic
statement:

```text
It started at:            first divergence
The first incorrect value: Y
The failure was:          Z
It affected:              A, B, C
The evidence is:          1, 2, 3
The trustworthy data:     ...
The suspect data:         ...
The safe recovery:        ... (NOT executed automatically)
```

## 2. Canonical incident record

Persisted in `audit.db.incidents` (AUDIT-0006 migration, additive &
governed). Fields:

| Field | Type | Meaning |
| :--- | :--- | :--- |
| incident_id | TEXT PK | `INC-<year>-<hex8>` |
| detected_at | TEXT | when the incident engine noticed it |
| severity | TEXT | INFO / LOW / MEDIUM / HIGH / CRITICAL (evidence-driven) |
| category | TEXT | MT5 / LEDGER / ACCOUNTING / DATA / LEARNING / RESEARCH / MODEL / FEATURE / NEWS / UI / API / WORKER / EXECUTION / GOVERNANCE / MIGRATION / VERSION / TELEGRAM / EXPOSURE / SECURITY / OTHER |
| status | TEXT | OPEN / INVESTIGATING / ROOT_CAUSE_IDENTIFIED / CONTAINED / RECOVERY_READY / RECOVERED / CLOSED / FALSE_POSITIVE |
| first_seen_at / last_seen_at | TEXT | observed event window |
| component / operation | TEXT | where + what |
| correlation_id | TEXT | cross-component trace id |
| root_cause_status | TEXT | UNKNOWN / PLAUSIBLE / HIGH_CONFIDENCE / PROVEN |
| root_cause | TEXT | narrative, evidence-backed |
| evidence | JSON | EvidenceItem[] (LOG/DATABASE/BROKER/RUNTIME_TRACE/TEST) |
| impact | JSON | IncidentImpact (bounded, observed-only counts) |
| affected_records / _models / _runtime / _users | JSON | explicit blast list |
| recovery_status | TEXT | RECOMMENDED / APPROVED / EXECUTING / COMPLETED / FAILED |
| recommended_action | TEXT | short operator action |
| fingerprint | TEXT | dedup identity (sha256 of category|component|error_code) |
| repeated_count | INT | 55 identical exceptions -> 1 incident, count=55 |
| related_bug_id / fix_commit / regression_test | TEXT | BUG ledger linkage (spec 54) |
| is_regression / previous_bug_id | INT/TEXT | same fingerprint recurring after a fix |
| resolved_without_evidence | INT | CLOSED without root cause + fix + regression test |
| recovery_plan_json | JSON | RecoveryPlan (what/why/trustworthy/suspect/must-not-change/options) |
| tags_json / notes_json | JSON | free-form |

Companion tables: `incident_events` (timeline, real timestamps only),
`incident_value_traces` (lineage), `incident_quarantine` (non-destructive
suspect marks, never deletes evidence).

## 3. Status lifecycle

```text
OPEN -> INVESTIGATING -> ROOT_CAUSE_IDENTIFIED -> CONTAINED -> RECOVERY_READY
     -> RECOVERED -> CLOSED
OPEN -> FALSE_POSITIVE               (evidence disproves)
```

An incident may NOT reach CLOSED merely because the exception disappeared
(spec 53): root cause fixed + regression test added + runtime verification +
no recurrence window are required; otherwise the status remains MITIGATED.

## 4. Severity table (evidence-driven)

| Severity | Classes |
| :--- | :--- |
| CRITICAL | schema mismatch, accounting divergence, duplicate economic outcome, data corruption, future leakage, Champion artifact mismatch, silent financial corruption |
| HIGH | learning pipeline loss, shadow isolation failure, migration failure, model incompatibility, major UI/API divergence, deal lookup failure, MT5 call failure, silently swallowed exception |
| MEDIUM | worker stalled, news source failure, feature drift, performance regression, exposure cache stale |
| LOW | isolated recoverable transient failure, non-critical telemetry delay |

Severity is *inferred* from the error class and may only escalate with
evidence; it never downgrades silently.

## 5. Deduplication (spec 31)

Group by `(fingerprint, component, error_code, correlation pattern, time
window)`; repeated identical events merge into ONE incident whose
`repeated_count` grows. Windows per category are configurable (defaults in
`correlator.DEFAULT_WINDOWS_SEC`, e.g. MT5 30s, LEDGER 300s, DATA 900s).

## 6. Correlation identity (spec 4/5)

Priority: correlation_id > ticket/execution identity > fingerprint+window.
The causal chain is reconstructed from actual timeline timestamps:

```text
ROOT_EVENT -> PRIMARY_FAILURE -> STATE_CORRUPTION -> DOWNSTREAM_EFFECT
           -> USER_VISIBLE_SYMPTOM
```

No invented sequence — the timeline is the sorted, deduplicated event log.

## 7. Value lineage (spec 8)

Every critical value can be walked hop-by-hop:

```text
SOURCE OF TRUTH -> TRANSFORMATIONS -> CACHES -> PERSISTENCE -> API -> UI
```

Example (PnL): MT5 deal -> broker adapter -> deal snapshot ->
reconciliation -> accounting core -> API -> UI. The lineage engine finds the
first hop that diverged from truth (`find_first_divergence`).

## 8. Recovery governance (spec 28/29/30)

- Recovery plans are generated as **RECOMMENDED**; execution requires
  operator **APPROVED** state; destructuring steps are never auto-executed.
- Containment is limited to explicitly safe advisory actions (pause
  research worker, block model inference, mark dataset invalid, block
  migration/release). NEVER closing trades, SL/TP changes, risk changes
  (spec 27).
- Quarantine marks data SUSPECT/INVALIDATED/QUARANTINED without deleting;
  the original record + reason + incident_id + timestamp are preserved.

## 9. Reports & exports (spec 34/46/47)

`artifacts/incidents/<incident_id>.{json,md}` (machine + human readable);
optional ZIP bundle with log excerpts, DB query results, model manifest,
runtime snapshot. Secret masking (API keys, bot tokens, passwords) is applied
recursively on every export — never included.

## 10. API & UI (spec 35/36/37)

Endpoints (all read-only GETs, reused diagnostics surface):

| Endpoint | Purpose |
| :--- | :--- |
| /api/diagnostics/incidents | list + counts (status/severity/category filters) |
| /api/diagnostics/incidents/{id} | full record |
| /api/diagnostics/health | aggregated counts + recurring fingerprints |
| /api/diagnostics/lineage | value lineage + why-traces |
| /api/diagnostics/search | deterministic bounded search |

Web: **Forensic Incident Center** tab (open incidents by severity,
one-click trace input, incident detail with timeline / recovery plan /
quarantine).

## 11. Telegram (spec 48/49)

CRITICAL/HIGH incidents alert with incident_id, severity, component,
symptom, root-cause status, impact, correlation_id. Throttled per incident:
first occurrence alerts; repeats within the cooldown window are suppressed
and summarized (repeat_count). No stack traces in Telegram.

## 12. Performance (spec 58/59)

- Worker runs in background via `asyncio.to_thread` (never the tick path).
- Bounded cycles: ≤2000 events, ≤50 saves per cycle, windowed queries,
  indexed lookups.
- No full-table scans per tick; incident analysis is read-only and bounded.

## 13. Safety (spec 0)

TASK-12 MUST NOT (and the code does not): change trading strategy,
RiskEngine, lot sizing, SL/TP, execution rules, thresholds, the Champion,
retrain models, mutate Liquidity algorithms, rewrite accounting history,
delete databases, delete research evidence, auto-repair financial records,
or auto-trade. Verifiable via TEST-INCIDENT-34/35 (import-scan + route-scan).