# POST-70D Historical Anomaly Audit (TASK-12 §21/§22, STEP-06)

> AGENT-12, 2026-08-19. Read-only forensic verification of the historical
> anomalies TASK-11 reported. No rows rewritten (INV-007 immutable history).

## 1. The 54 impossible-excursion rows — VERIFIED HISTORICAL LEGACY DATA

| Attribute | Value |
| :--- | :--- |
| Count | 54 ledger rows (31 BUY, 23 SELL), 54 distinct tickets |
| Symbol | XAUUSD (all) |
| Date range | 2026-08-17 → 2026-08-18 (pre-BUG-096 fix) |
| Exit mechanism | MANUAL_CLOSE / SYSTEM_CLOSE |
| Pattern | Split-fill families share the same entry (e.g. 4390.31), tiny
  per-row MFE deviations (-0.05..-0.60) — the BUG-096 seeding defect
  (MFE tracker seeded at first signed price delta, never lifted to 0) |

**Classification: HISTORICAL LEGACY DATA (BUG-096-era seeding defect).**
The monitor reports WARNING (immutable), and CRITICAL only for NEW rows
closed at/after the 2026-08-19 fix date. Zero new violations since the fix.
Not corruption of current data; not a classifier issue; not a valid
edge-case representation (the invariant MFE>=0/MAE<=0 is contract-correct).

## 2. The duplicate economic outcome (ticket 152494870397) — TRUE DUPLICATE

| Layer | Value |
| :--- | :--- |
| Broker trade | 152494870397, BUY 4416.61→4416.32, net_pnl **-18.27** (exit reason 3) |
| Outcome A | exp_87f47ca2, execution 152494870397, SYSTEM_CLOSE, **-18.27** ✓ broker-matching |
| Outcome B | exp_d9952f5a, execution 152494870397, SYSTEM_CLOSE, **-31.50** ✗ ledger aggregate |
| Ledger row | ticket 152494870397, pnl -31.50 (matches the WRONG outcome B) |

**Classification: TRUE DUPLICATE (BUG-097 split-fill sibling context leak).**
One broker ticket == one economic outcome (INV-70D-016 / TRADE_OUTCOME v3);
the -31.50 row is a sibling-fill artifact where two proposals' closes both
correlated to the same broker ticket. The creation-path guard (BUG-097 fix)
prevents NEW duplicates; this historical row pair stays immutable for audit.
The monitor reports WARNING (historical), CRITICAL for any fresh duplicate.

## 3. Monitoring disposition (no auto-repair, §0)

- Both findings remain visible in the forensic snapshot:
  CHECK-ACC-02 (duplicate) and CHECK-ACC-03 (excursion) = WARNING,
  date-gated so any NEW violation escalates to CRITICAL immediately.
- Recommended governance action (NOT performed by the monitor):
  - Mark exp_d9952f5a as the non-canonical leg via a correction event
    (reconciliation owner), keeping broker truth -18.27 canonical.
  - Optionally add a remediation_status column (HISTORICAL/CURRENT/
    REMEDIATED) to anomaly_events via the TASK-10 migration registry.
- Evidence persists in artifacts/forensics/ (deploy gate + snapshot).