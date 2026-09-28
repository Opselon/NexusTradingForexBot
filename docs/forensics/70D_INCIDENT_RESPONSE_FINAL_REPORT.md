# TASK-12 — 70D Incident Response / Root-Cause / Recovery Diagnostics — FINAL REPORT

> Agent: AGENT-12 (Hermes-Forensic-12)
> Task: TASK-12-FORENSIC-INCIDENT-RESPONSE
> Role: Incident Correlation / Root-Cause / Recovery Diagnostics Engineer

## SYSTEM STATE

- Branch: `main`
- HEAD at start: `c56d334` (Hermes-LiquidityResearch blocked-state registration)
- Audit schema: **v4 → v6** (AUDIT-0005 governance tables [parallel WIP] +
  AUDIT-0006 incident tables — both additive, integrity ok)
- New package: `src/nexus_scalp/incidents/` (models, store, correlator,
  lineage, trace, impact, reports, worker, telegram, __init__)
- Databases: audit.db (incidents/incident_events/incident_value_traces/
  incident_quarantine added; ledger 266 rows untouched), news.db,
  candle_intel.db untouched
- Champion: unchanged (registry 2 rows, both 50D f0f70efb…)

## INCIDENT MODEL

- Canonical fields per docs/70D_INCIDENT_RESPONSE_MODEL.md (20 mandatory
  fields + dedup/regression/BUG-linkage/recovery/quarantine structure).
- Statuses: OPEN / INVESTIGATING / ROOT_CAUSE_IDENTIFIED / CONTAINED /
  RECOVERY_READY / RECOVERED / CLOSED / FALSE_POSITIVE.
- Severities: INFO / LOW / MEDIUM / HIGH / CRITICAL (evidence-driven,
  escalation-only).
- Deduplication: fingerprint (sha256 of category|component|error_code) +
  category windows; 55 identical exceptions → ONE incident repeated_count=55
  (TEST-INCIDENT-02).

## ROOT-CAUSE ENGINE

- `IncidentCorrelator` groups by correlation_id > ticket/execution identity >
  fingerprint+window; builds causal chains (ROOT_EVENT → PRIMARY_FAILURE →
  STATE_CORRUPTION → DOWNSTREAM_EFFECT → USER_VISIBLE_SYMPTOM) from ACTUAL
  timeline timestamps (spec 4/5/10).
- Root-cause confidence: UNKNOWN / PLAUSIBLE / HIGH_CONFIDENCE / PROVEN —
  never auto-proven; evidence items (LOG/DATABASE/BROKER/RUNTIME_TRACE/TEST)
  must be attached (TEST-INCIDENT-03).
- First-failure identification: `LineageEngine.find_first_divergence` walks
  hops backward from the symptom (spec 6/7).

## VALUE LINEAGE (examples)

| Value | Chain |
| :--- | :--- |
| PnL | MT5 deal → broker adapter → deal snapshot → reconciliation → accounting core → API → UI |
| Feature vector | bar aggregator (reseed REPLACE+ALIGN) → feature calc → clip → vector assembly → inference |
| Model output | validated vector → Champion inference → action classification → policy → API → UI |
| UI value | backend DB → API → JS loader → renderer → DOM (spec 21/43) |
| Exposure | MT5 positions (INV-011 broker wins) → snapshot → cache → MAX_EXPOSURE check → API → UI |

## HISTORICAL FAILURE CLASSES — exact current findings (read-only baseline)

1. **ACCOUNTING_DIVERGENCE (CRITICAL)**: 237 mapped broker/ledger PnL
   divergences; **151 ledger rows net_pnl_usd=0 while broker PnL ≠ 0**
   (e.g. ticket 152487837184: broker +41.00 / ledger 0.00). 32 zero-PnL
   outcomes (2026-08-17) have NO reconstruction_source and the tickets exist
   in broker_trades with real PnL → SUSPECT_OUTCOME (spec 16).
2. **TIMEBASE_DIVERGENCE (HIGH)**: observed skew clusters −5.1…−5.9 h for
   recent rows (batch backfill); stored timestamps UTC-normalized. Measured,
   not assumed (spec 14).
3. **OUTCOME_TO_RESEARCH_DROP (MEDIUM)**: research_runs=0 (no dataset run
   yet); experience→outcome linkage healthy (74/74 linked, rate 1.0).
4. **SPLIT_FILL_GROUPING (MEDIUM)**: 23 families (one master order, multiple
   tickets) — grouped; no context-propagation failure found in baseline.
5. Verified-healthy: no News all-neutral (last 100: 76 neutral/13 bull/6
   bear/5 mixed), no stale bundle (node --check ok), no migration drift
   (integrity ok, 5 applied migrations), no model/schema mismatch (Champion
   intact).

## CURRENT INCIDENTS

- Pre-baseline: 0 (new system).
- Initial read-only scan: **5 incidents** (1 CRITICAL / 1 HIGH / 3 MEDIUM)
  — recorded as canonical incident rows (dry-run default; `--write` persists).

## TOP ROOT CAUSES (evidence-backed)

- PLAUSIBLE (not PROVEN): pre-BUG-045-era outcome rows (2026-08-17) whose
  PnL was never reconciled from broker deals — 151 ledger zeros + 32
  zero-outcomes. Code-level audit of the recovery path is a governed TASK-13
  follow-up; the engine keeps the records SUSPECT and never rewrites them.

## RECOVERY

- `RecoveryPlanner` generates category-templated plans (RECONCILE / REBUILD /
  REVALIDATE / QUARANTINE / BLOCK / MANUAL) with **no destructive options**,
  all `approval_required=True`, state RECOMMENDED (TEST-INCIDENT-26/27).
- Never auto-executed: no ledger rewrite, no model retrain, no risk/SL/TP
  change, no deletion (TEST-INCIDENT-34/35).

## QUARANTINE

- `QuarantineManager.mark_suspect` (SUSPECT / INVALIDATED / QUARANTINED),
  keeps original + reason + incident_id + timestamp; persisted in
  `incident_quarantine` (TEST-INCIDENT-25).

## UI

- New **Forensic Incident Center** tab: severity summary cards, open
  incident list (CRITICAL/HIGH/MEDIUM), one-click trace input (spec 37),
  incident detail (timeline / recovery plan / quarantine). Nav badge shows
  open count. HTML div-balance verified, node --check clean.

## API

| Endpoint | Purpose |
| :--- | :--- |
| GET /api/diagnostics/incidents | list + counts (filters) |
| GET /api/diagnostics/incidents/{id} | full record |
| GET /api/diagnostics/health | aggregated health + recurring fingerprints |
| GET /api/diagnostics/lineage | value lineage + why-traces |
| GET /api/diagnostics/search | bounded deterministic search |

All GET-only (read-only; verified by TEST-INCIDENT-34 route scan). Reuses
the existing diagnostics surface — no competing diagnostic APIs.

## TELEGRAM

- `IncidentTelegramNotifier`: CRITICAL/HIGH alerts with incident_id,
  severity, component, symptom, root-cause status, impact, correlation_id;
  per-incident cooldown + repeat summarization (spec 48/49,
  TEST-INCIDENT-30). Wired to the canonical TelegramNotifier; no secrets.

## PERFORMANCE

- IncidentWorker runs via asyncio.to_thread (never tick path); bounded
  cycles (≤2000 events, ≤50 saves), indexed lookups, windowed queries.
  No full-table scans per tick (TEST-INCIDENT-15 + design, spec 58/59).

## TESTS

- `tests/unit/test_incident_response_task12.py`: **62 tests**
  (TEST-INCIDENT-01..35 with sub-cases).
- 137 passed in the task12+migrations+hygiene cluster (85s).
- Full unit suite running in background (baseline of this repo is long:
  ~8-10 min).

## BUGS

- No new BUG-NNN entries created: the forensic baseline findings are
  incidents (INC- records) with PLAUSIBLE root causes that require a
  governed code-level audit before any bug entry — per contract only
  PROVEN issues enter bugs.md (spec 62: "bugs updated only for proven
  issues").
- BUG-070 (MT5 epochs server-local) referenced as related in TIMEBASE
  lineage; BUG-081/BUG-088/BUG-095 patterns consumed by the engine's known
  failure-class tables.

## COMMIT

- `AGENT-12: Add forensic incident response and root-cause tracing` (SHA
  appended at commit time). Details in commit body + handoff.

## REMAINING RISKS

- **PROVEN**: incident engine is safe by construction (no execution/risk
  imports; GET-only routes; no auto-recovery; additive migration).
- **NOT PROVEN**: the 151 zero-PnL ledger rows / 32 zero-outcomes root cause
  (needs governed code audit of outcome recovery; TASK-13 or a dedicated
  forensic task).
- **UNKNOWN**: live session timebase (needs a live MT5 probe);
  Telegram CRITICAL/HIGH delivery (needs a live alert); worker-stall
  classification on live workers (needs runtime observation).