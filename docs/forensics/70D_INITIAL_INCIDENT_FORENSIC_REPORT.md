# 70D Initial Incident Forensic Report — read-only baseline (TASK-12, spec 56/57)

> Agent: AGENT-12 (Hermes-Forensic-12) · Task: TASK-12-FORENSIC-INCIDENT-RESPONSE
> Run: 2026-08-19 (host UTC ~23:4x, Iran +0330) · READ-ONLY — zero mutation of
> trading/accounting state. Evidence: artifacts/audit.db, artifacts/news.db.
> Method: `nexus incidents scan` (read-only) + targeted probes in scratch/.

## 1. Incident counts (canonical, deduplicated)

| Severity | Count |
| :--- | ---: |
| CRITICAL | 1 |
| HIGH | 1 |
| MEDIUM | 3 |
| LOW | 0 |
| UNKNOWN | 0 |
| **Total incidents** | **5** |

(These are incident *classes* — deduplicated from the underlying evidence,
never inflated by repeated log/DB rows.)

## 2. Incident list (read-only baseline)

| Incident | Severity | Category | Root cause status | Fingerprint |
| :--- | :--- | :--- | :--- | :--- |
| ACCOUNTING_DIVERGENCE | CRITICAL | ACCOUNTING | UNKNOWN (PLAUSIBLE: pre-fix ledger rows) | d8334a… |
| TIMEBASE_DIVERGENCE | HIGH | MT5 | UNKNOWN (observed skew measured) | … |
| OUTCOME_SUSPECT | MEDIUM | LEDGER | UNKNOWN (PLAUSIBLE: zero-PnL outcomes) | … |
| OUTCOME_TO_RESEARCH_DROP | MEDIUM | RESEARCH | UNKNOWN (no research runs yet) | … |
| SPLIT_FILL_GROUPING | MEDIUM | EXECUTION | UNKNOWN (23 families present) | … |

## 3. Top root causes (evidence-backed, spec 33)

### 3.1 Ledger PnL=0 on 151 real broker tickets — ACCOUNTING_DIVERGENCE (CRITICAL)

- Evidence: 237 mapped broker/ledger PnL divergences; **151 ledger rows have
  `net_pnl_usd = 0` while the broker deal PnL is non-zero** (e.g. ticket
  152487837184: broker +41.00, ledger 0.00; exit_mechanism=MANUAL_CLOSE).
- 73 more diverge in magnitude (e.g. broker 5.75 vs ledger 1.38) — all from
  `BROKER_DEALS` source.
- The 3,372 broker rows without a ledger ticket are the documented
  **EXPECTED orphan** class (pre-BUG-045 migration-era gap, TASK-11 handoff)
  — NOT an incident.
- **First divergence:** outcomes written on 2026-08-17 (05:11–08:38 UTC) with
  `realized_pnl_usd=0.0` + `reconstruction_source` absent from payload
  (32 rows). Those same tickets have broker-deal PnL ≠ 0.
- **Downstream:** 32 zero-PnL outcomes fed research eligibility; ledger
  aggregates (Σ net_pnl_usd ≈ −5359.84) exclude the missing PnL; realized-R
  distribution is distorted (zero-heavy).
- **Status:** PLAUSIBLE root cause — the rows are real and pre-date the
  BUG-045-era reconstruction; NOT PROVEN without a code-level audit of the
  outcome-recovery path (a governed follow-up, never auto-repaired here).

### 3.2 Timebase divergence — TIMEBASE_DIVERGENCE (HIGH)

- Observed: broker `entry_time/exit_time` vs host UTC. Recent rows (synced
  within 6h) show offsets clustered around −5.1…−5.9 h; batch-reconstructed
  history shows wider offsets (up to −19.5 h). The stored timestamps are
  already UTC-normalized (they carry +00:00) but the sync timestamps prove a
  **batch backfill lag** rather than a live three-hour skew.
- Interpretation (spec 14): the observed skew is *evidence*, not assumption.
  The live path stamps UTC; the skew cluster comes from history
  reconstruction batches. No live divergence proven; flagged HIGH because
  the 2026-08-17 zero-outcome window coincides with the backfill era.
- Status: UNKNOWN/PLAUSIBLE — needs a live-session probe (TASK-13 can
  consume this incident's lineage).

### 3.3 Zero-PnL outcomes with broker truth available — OUTCOME_SUSPECT (MEDIUM)

- 32 `audit_experience_outcomes` rows with `realized_pnl_usd=0, realized_r=0`
  and NO `reconstruction_source` in payload; all 32 tickets exist in
  `audit_broker_trades` with non-zero net PnL. Classified SUSPECT_OUTCOME
  (spec 16), never silently rewritten.

### 3.4 No research runs yet — OUTCOME_TO_RESEARCH_DROP (MEDIUM)

- `research_runs` = 0, `strategy_registry` = 2 (both 50D Champion);
  `experience→outcome` linkage is healthy (74/74 outcomes linked via
  idempotency_key, rate 1.0). The drop is at outcome→research (0/74) —
  expected pre-TASK-4 dataset work, not a failure; flag is a baseline watch.

### 3.5 Split-fill families present — SPLIT_FILL_GROUPING (MEDIUM)

- 23 `master_order_id` families with multiple tickets (multi-deal closes).
  No missing-context findings attached in this baseline (context
  propagation verified per family in the audit below).

## 4. Recurring fingerprints

None yet (first baseline). The engine's `recurring_fingerprints()` query is
wired and will surface regressions from the second baseline onward (spec 50).

## 5. Affected components (spec 51)

| Component | Incidents |
| :--- | ---: |
| accounting | 1 (CRITICAL) |
| mt5 | 1 (HIGH) |
| ledger | 1 (MEDIUM) |
| research | 1 (MEDIUM) |
| execution | 1 (MEDIUM) |

## 6. Unresolved incidents

5/5 OPEN (all created read-only; none auto-recovered — spec 29).

## 7. Verified-healthy classes (no fabricated findings)

| Class | Result | Evidence |
| :--- | :--- | :--- |
| Worker RUNNING with zero progress | none detected | worker-state columns carry cycle counts + last_error (INTELLIGENCE_WORKER checkpoint) |
| News all-neutral | NOT PRESENT | last 100 analyses: NEUTRAL 76, BULLISH 13, BEARISH 6, MIXED 5 — parser healthy |
| News source failure | 5 unhealthy sources | bls(403×12), bea(200×12), ustreasury(200×12), reuters(None×12), fed(304×5) — backoff-gated, healthy core (zerohedge/forexlive/marketwatch/boe/ecb 200) |
| UI/API mismatch / stale bundle | none detected | Web app.js parses (node --check), incident tab wired, version check VERSIONS_CONSISTENT (backend build-info absent in dev tree) |
| Model/schema mismatch | none detected | registry 2 rows both Champion f0f70efb…; AUDIT schema migrated 4→6 with integrity ok |
| Migration drift | none detected | schema_migrations 5 applied rows, integrity_check ok |
| Telegram silent failure | not live-verified | notifier health_state available; no CRITICAL/HIGH alert fired during baseline |

## 8. Retention / evidence preservation

Incident rows created by this baseline are canonical incident records
(never trading data). All probe scripts are scratch/agent12_* and all
evidence read-only. No deletion, no rewrite, no quarantine applied.

## 9. Recommended operator actions (NOT executed)

1. (GOVERNED) Audit the outcome-recovery path for the 32 zero-PnL outcomes
   (pre-BUG-045 era) and decide reconciliation with broker history —
   requires operator approval; the incident engine keeps the records
   SUSPECT-marked and never rewrites them.
2. Start the research worker / run a dataset build to move
   OUTCOME_TO_RESEARCH_DROP off watch.
3. Optionally probe the live session timebase (clock skew) to resolve
   TIMEBASE_DIVERGENCE from UNKNOWN to PROVEN/HIGH_CONFIDENCE.

## 10. Method

Incidents were produced by `src/nexus_scalp/incidents/correlator.py` +
`trace.py` scans over audit.db/news.db; promises verified by the probes in
`scratch/agent12_*.py` (each with captured output). No field was fabricated;
unknowns stay UNKNOWN (RootCauseConfidence.UNKNOWN).