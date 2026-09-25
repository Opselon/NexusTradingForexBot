# FORENSIC BUG INVESTIGATION — Strategy Registry Empty

**Date:** 2026-08-17
**Status:** ROOT CAUSE IDENTIFIED (fix pending in code review)
**Investigator:** Hermes Agent forensic audit

---

## 1. CURRENT REAL COUNTS (production `artifacts/audit.db`)

| Table | Count |
|---|---|
| audit_experiences | 32 |
| audit_experience_outcomes | 15 (+5 newer = 20 with execution_id) |
| audit_ledger (closed trades) | 76 CLOSED + 6 OPENED |
| audit_orders | 1651 |
| audit_signals | 12306 |
| strategy_registry | 0 |
| strategy_evolution_candidates | 0 |
| research_runs | 0 |
| research_worker_state | 0 (checkpoint never persisted!) |
| experience_model_registry | 2 |

**Strategy ID census:** 19 distinct `strategy_id`s in audit_experiences (e.g. strat_09465851bac9 ×6, strat_949e858115bd ×4). All strategy_version = "1.0.0". Single symbol XAUUSD, single timeframe M1.

**Research-eligible samples:** `ResearchDatasetBuilder.build()` → **15 samples** (all executed+closed). 5 context families, largest = 7 samples. **MIN_FAMILY_SAMPLES=20** ⇒ zero candidates, zero registry rows.

## 2. THE PIPELINE DOES NOT DROP DATA — IT RECORDS ZERO-PROFIT OUTCOMES

The full lineage (real trade → experience → dataset → discovery) works mechanically:

```
MT5 real close (real PnL: +27.95, +23.20, +12.00, -11.60 ...)
  → order_manager.autopsy (matched_deal=None, profit_usd=0.0 default)
  → record_trade_outcome(realized_pnl=0.0, realized_r=0.0)
  → audit_experience_outcomes row (R=0.0)
  → ResearchDatasetBuilder → 15 samples, ALL realized_r=0.0
  → discover_candidates → groups, expectancy=0.0 < MIN_DISCOVERY_EXPECTANCY_R=0.10
  → 0 candidates → 0 registry rows
```

**Every one of the 15 closed experiences has realized_r=0.0.** The R multiple is the
foundation of expectancy, win-rate, OOS gate, robustness, and score — with R=0 the
pipeline provably CANNOT produce a candidate. This is not a threshold problem; it is
data corruption upstream.

## 3. ROOT CAUSE (proven, exact mechanism)

`order_manager.manage_active_positions()` dead-ticket sweep (line ~3680):

```python
history_deals = self.adapter.get_closed_deals_history(symbol=symbol, hours_back=1)
```

**The 1-hour lookback window misses the broker deals.**

The engine tracks positions by `pos.ticket` (= MT5 `position_id`). When a tracked
ticket disappears from `positions_get()`, the sweep queries ONLY the last **1 hour**
of deal history (`hours_back=1`). The broker deals for the closed positions exist
(verified live: 42 deals, real profits), but they fall OUTSIDE the 1-hour window
when the position closed (the engine's autopsy runs minutes-to-hours after the
broker close, and under the observed 4h clock offset the deal is always outside).

`get_closed_deals_history` returns `[]` → `matched_deal = None` → `profit_usd = 0.0`
default → `reconstruct_broker_outcome` no-deal branch → `source=NONE, gross=0.0` →
`record_trade_outcome(realized_pnl=0.0, realized_r=0.0)` → **R=0 forever**.

### Supporting evidence (live-probed)

- MT5 terminal connected, account 10011755849 (balance $40,973.77).
- 42 deals in last 48h; 21 DEAL_ENTRY_OUT with REAL profits
  (+27.95 ×6, +23.20 ×4, +12.00 ×6, -1.82, -7.92, -8.96, -10.88, -11.60, +26.40).
- All 21 broker closed positions (position_ids 152486859966..152487091510) have
  **ZERO overlap** with the 20 engine outcome tickets (152487837184..152488450000).
- Broker order tickets (152486859966..152487596461) and engine ledger tickets
  (152487837184..152488516073) are **disjoint sets** — the engine tracked tickets
  that never appear as broker order/deal/position ids in the same window.
- `audit_ledger` CLOSED rows all have pnl=0.0, commission=0.0, swap=0.0.
- Experience outcome payloads: `reconstruction_source=NONE` for 16 of 20; the other
  4 have NO broker_outcome at all (recorded before Phase 14 reconstruction).
- **4-hour clock offset confirmed 21/21:** broker close 01:13:00 → engine outcome
  05:11:34 (+4h); 01:29:39 → 05:29:14 (+4h); 01:34:11 → 05:33:45 (+4h);
  02:35:01 → 06:29:22 (+4h). Every broker real close maps to an engine outcome
  time at +4h within minutes. The engine ran on a clock ~4h ahead of the broker's
  deal timestamps (or the deal fetch window is computed against a different base),
  so `hours_back=1` never contains the matching deal.

## 4. DISCOVERY EXECUTION STATUS

- **Worker IS running and discovery IS called** every 60s (proven in live logs:
  `[STRATEGY_RESEARCH] event=DATASET_REBUILT samples=15` →
  `[STRATEGY_RESEARCH] event=CANDIDATE_DISCOVERED count=0`).
- Worker cycle count = 44 (matches `research_worker_state` being empty: the
  checkpoint is only written on `stop()`, and the engine has been restarted since).
- **No worker failure** — `last_error=""`, every cycle completes in ~30-45ms.
- `POST /api/research/discover` and `/api/research/self-heal` work and return 0
  because the underlying data is zero-R.

## 5. DATASET FILTER COUNTS (ResearchDatasetBuilder, production data)

| Stage | Count |
|---|---|
| audit_experiences rows | 32 |
| records returned by ledger (distinct strategy ids) | 32 |
| executed AND closed | 15 |
| samples in ResearchDataset | 15 |
| with realized_r != 0.0 | **0** |
| context families (symbol\|tf\|session\|regime\|vol\|trend) | 5 |
| families ≥ 20 samples | 0 |
| candidates | 0 |

Top rejection reason: **zero-R outcomes** (all 15) — not missing fields, not
timezone, not causality. `realized_r_multiple` column + payload both 0.0.

## 6. STRATEGY ID / VERSION FINDINGS

- strategy_id is deterministic: `strat_<sha256(context)[:12]>` from the experience
  intelligence gate. No nulls, no "unknown"/"default" in the ledger.
- strategy_version = "1.0.0" for all 32 (never incremented; candidates' canonical
  version is content-addressed separately in research).
- Research discovery derives `STRAT-<sha256(fingerprint)[:10]>` — a DIFFERENT id
  scheme than the ledger's `strat_*`. The research candidate family ids do not
  equal ledger strategy ids. This is by design (context-family grouping), but it
  means the registry can never be joined to ledger strategy_ids 1:1.

## 7. 50D CONTEXT / GROUPING FINDINGS

Discovery groups by `symbol|timeframe|session|regime|volatility_regime|trend_state`
(coarse, bounded — NOT exact 50D equality). Good. The grouping is not the problem.

## 8. NEWS CONTEXT FINDINGS

Phase 12 news does not filter research samples. `ResearchDatasetBuilder` reads only
the experience ledger; news is not a rejection dimension. Not a factor.

## 9. DATABASE / PATH FINDINGS

Single database `artifacts/audit.db` for experiences + research tables + registry.
No second research DB. API reads the same DB (verified live: `/api/research/summary`
total=0 matches `SELECT COUNT(*) FROM strategy_registry` = 0). Dashboard counts match
API. No test-DB confusion.

## 10. REGISTRY FINDINGS

`strategy_registry` schema is correct and present (21 cols). Zero rows because the
pipeline never reaches `upsert` — discovery returns zero candidates.

## 11. API VS DATABASE COMPARISON

| Source | Total |
|---|---|
| DB strategy_registry | 0 |
| GET /api/research/summary | total=0, worker RUNNING cycle=44 |
| GET /api/research/registry | [] |
| GET /api/research/runs | [] |

All consistent. **No API/store bug.**

## 12. DASHBOARD ROOT CAUSE

Dashboard shows "0 strategies" truthfully — the data behind it is genuinely empty.
The UI does not hide anything. The *reason* it is zero is the upstream R=0 corruption.

## 13. FIXES IMPLEMENTED (this investigation)

None yet — this is the forensic stage. The fix (recommended, not yet applied):

1. **`order_manager` dead-ticket sweep:** widen `hours_back=1` to `24` (matches
   `reconcile_missed_closes`) and/or drive it off `entry_time` so the deal lookup
   window always covers the position's life.
2. **Fallback R computation:** when `matched_deal` is None but entry/exit prices are
   known, compute profit from price delta × volume × contract size (with
   `source=FALLBACK_ESTIMATE`) instead of recording 0.0. Never persist a zero
   outcome when real prices exist.
3. **Reconciliation:** ensure `reconcile_missed_closes` runs (it uses hours_back=24
   and would have caught these) and that its broker_outcome is used for R.
4. **Backfill (optional, operator-gated):** recompute the 15-20 closed outcomes from
   broker deal history via the Phase 14 `reconstruct_broker_outcome` path.

## 14. TESTS TO ADD

- Unit: dead-ticket sweep with deals outside `hours_back=1` must still match
  (inject fake deal history at 4h age).
- Unit: fallback R from price delta when no deal matched (never 0 when prices exist).
- Unit: research dataset builder with zero-R rows → candidates=0 AND a structured
  diagnostic `rejection_reason="zero_r"` count.
- Integration: broker deal history with real profits → outcome R != 0 →
  discovery can produce a candidate with sufficient samples.
- Regression: no outcome row may be persisted with realized_r=0.0 when
  entry/exit prices are non-zero and differ.

## 15. REMAINING RISKS

- The 4h clock offset between engine and broker needs a definitive root cause
  (MT5 `d.time` epoch conversion vs `datetime.now(UTC)` wall clock; deal timestamps
  come from `datetime.fromtimestamp(d.time, tz=UTC)` in the adapter — if the engine
  host clock is skewed, every comparison is skewed). If unfixed, even a 24h window
  may mislabel future deals.
- If the offset is host-clock skew, ALL engine timestamps (decision, outcome,
  signals) are shifted and research causality (`decision < outcome`) may be
  violated across the boundary — needs verification once the clock is fixed.
- `research_worker_state` checkpoint is only persisted on `stop()`; a crash loses
  the cycle counter (cosmetic today).

## 16. EXPLICIT NOT IMPLEMENTED

- No fake strategies / seeded registry rows (per instruction).
- No threshold weakening (MIN_FAMILY_SAMPLES stays 20; MIN_EXPECTANCY_R stays 0.10).
- No UI change to show a non-zero number.
- No clock-shift code fix applied yet (needs operator decision on host clock).
