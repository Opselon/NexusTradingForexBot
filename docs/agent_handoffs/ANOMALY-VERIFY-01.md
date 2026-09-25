# ANOMALY-VERIFY-01 HANDOFF — Forensic Verification of DUPLICATE_ECONOMIC_OUTCOME + IMPOSSIBLE_EXCURSION

**Agent:** Hermes-Research
**Date:** 2026-08-19
**Branch:** main
**Status:** COMPLETE (all gates green; verified + fixed)

---

## 1. Verdicts

| Anomaly | Classification | Status |
| :--- | :--- | :--- |
| DUPLICATE_ECONOMIC_OUTCOME (ticket 152494870397) | **REAL** | FIXED (creation path guarded) |
| IMPOSSIBLE_EXCURSION (18 rows, 18 distinct tickets) | **REAL DATA DEFECT / detector CORRECT** | FIXED (MFE storage) |
| "Repeated in UI" appearance | **NOT UI duplication** — 18 distinct incidents | Grouped incident view added |

## 2. Forensic Evidence (all PROVEN, read-only)

### DUPLICATE_ECONOMIC_OUTCOME — 1 incident (CRITICAL)
```
execution_id         = 152494870397
broker position      = BUY 4416.61, PnL -18.27 (one of ~10 split-fill siblings at 22:40:26)
outcome A            = exp_87f47ca2 (req 87f47...)  PnL -18.27   == broker truth
outcome B            = exp_d9952f5a (req d9952...)  PnL -31.50   == ledger aggregate; NO broker position
ledger row           = PnL -31.50 (matches outcome B)
pnl_delta            = 13.23 (exact anomaly payload)
BROKER TRUTH: -18.27 | LEDGER: -31.50 | OUTCOME A: -18.27 | OUTCOME B: -31.50
DUPLICATION: YES — one economic trade, two outcome rows (split-fill sibling
context leak, BUG-081 pattern: two proposals' closes both correlated to the
same broker ticket).
```

### IMPOSSIBLE_EXCURSION — 18 incidents (LOW)
```
Example ticket 152495069002: SELL entry 4413.54, price NEVER below entry
(immediately 4414.43+, best point 4414.17 => delta -0.63), correct MFE = 0.0,
stored mfe_points = -0.60, mfe_usd = 0.0.
Root cause: _ensure_ticket_bootstrap seeded _mfe_tracker at the FIRST observed
profit_price_delta (signed by direction; negative for adverse SELL), and
_update_mfe_mae used .get(ticket, profit_price_delta) fallback. max() could
never lift the negative seed => negative MFE persisted. USD branch already
clamped (max 0.0) => asymmetric mfe_points<0 / mfe_usd=0.
Detector CORRECT: SELL MFE < 0 IS impossible (contract MFE>=0, MAE<=0).
```

### Duplication census (DB/API/UI)
```
DB : anomaly_events = 22 rows, 22 UNIQUE anomaly_ids, 18 distinct
     IMPOSSIBLE tickets, 1 DUPLICATE ticket -> NO row duplication.
API: plain SELECT (no JOIN/UNION) -> NO API duplication.
UI : 1 card per row; 18 near-identical cards = 18 distinct incidents
     (no grouping) -> NOT duplication, but poor incident presentation.
```

## 3. Fixes (smallest correct, verified defects only)

1. **order_manager.py** — `_ensure_ticket_bootstrap` seeds `_mfe_tracker`/`_mae_tracker` at 0.0 (never the first delta); `_update_mfe_mae` defaults `.get(ticket, 0.0)`. MFE>=0 / MAE<=0 for both directions. In-memory only; no I/O, no broker calls, not on the async tick I/O path.
2. **experience/ledger.py** — new `owner_of_execution(execution_id, exclude_key)`.
3. **experience/intelligence.py** — `record_trade_outcome` refuses a second outcome sharing the same execution_id under a different idempotency_key (`[EXPERIENCE_OUTCOME] event=ECONOMIC_DUPLICATE_REJECTED`). One broker ticket == one economic outcome.
4. **intelligence/behavior.py** — per-trade anomaly ids deterministic `(ticket, type, version)` (was uuid4).
5. **intelligence/store.py** — `list_anomaly_events(grouped=True)` collapses repeated observations of an incident into one entry with `observation_count`/`first_seen`/`last_seen`; raw rows never deleted.
6. **Web/app.js** — anomaly cards render observation_count + first..last range.

## 4. Tests Added (TEST-ANOM-01..28)

- `tests/unit/test_anomaly_verify01_duplicates.py` — 5 (economic identity, idempotency, split-fill one-outcome, reconciliation, restart)
- `tests/unit/test_anomaly_verify01_mfe.py` — 13 (MFE invariant BUY/SELL, sign symmetry, floating-point, deterministic ids, incident grouping, version preservation, no-deletion)

## 5. Gates

- Full unit suite: EXIT=0 (0 failed)
- Focused: anomaly (18), intelligence phase09 (19), behavior phase16 (26), trade_lifecycle task3, order_manager — all PASS
- ruff / ruff format / mypy: clean
- node --check Web/app.js: OK

## 6. Bugs

- BUG-096 MFE tracker seeding → FIXED
- BUG-097 split-fill double outcome → FIXED
- BUG-098 non-deterministic per-trade anomaly ids → FIXED

## 7. Data Repair Status

- Historical MFE values (18 SELL rows) are NOT rewritten (INV-007 immutable history; corrections flow through raw evidence → derived → provenance). The detector will stop generating new IMPOSSIBLE_EXCURSION incidents as new trades use the corrected tracker; the 18 stored anomaly rows remain auditable under anomaly-v1.
- Historical duplicate outcome (152494870397) is NOT deleted: the creation-path guard prevents NEW duplicates; the old rows remain for audit. A reconciliation job may mark the second outcome as the non-canonical leg (future work; do NOT delete).

## 8. Remaining Risks

- 18 historical IMPOSSIBLE_EXCURSION rows stay as-is (documented historical findings under anomaly-v1); a remediation status field is future work.
- The 3 EXIT_CLASSIFICATION_ANOMALY rows are a separate real finding (was_sl_modified=false with RISK_FREE_SL_HIT) — NOT part of this task's scope; documented for TASK-7 owner.
- The second outcome row for 152494870397 (exp_d9952f5a) still exists; reconcilers must treat ticket as the economic identity.

## 9. NEXT-AGENT INSTRUCTIONS

1. TASK-7 owner (exit intelligence): investigate the 3 EXIT_CLASSIFICATION_ANOMALY rows (RISK_FREE_SL_HIT with was_sl_modified=false) — separate forensic thread.
2. Re-run the anomaly scan (offline `analyze_canonical_trades` / backfiller) after the tracker fix and confirm no NEW IMPOSSIBLE_EXCURSION incidents are produced for new trades; old 18 remain.
3. If desired, add a `remediation_status` (HISTORICAL/CURRENT/REMEDIATED) column to anomaly_events via TASK-10 migration architecture — do not delete old rows.
4. Reconcile the 152494870397 duplicate: mark exp_d9952f5a as the non-canonical leg (correction event), keep broker truth (−18.27) canonical.