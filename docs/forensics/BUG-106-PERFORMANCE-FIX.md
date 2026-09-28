# BUG-106 PERFORMANCE FIX — 70D Frame Builder O(n²) → Bounded Causal Window

> Agent: AGENT-05 (Hermes-Shadow70D-05) · TASK-05 · 2026-08-19
> Status: FIXED · Parity: GREEN (TASK-3 70D parity suites re-run clean)

---

## 1. Root cause

`model_generation/schema_v2.py::compute_70d_frame` (and
`compute_liquidity_frame`) passed the **entire causal history**
(`all_bars[: i + 1]`) to `compute_liquidity_features` for EVERY row `i`:

```text
for i in range(n):
    liquid = compute_liquidity_features(all_bars[: i + 1], ...)
```

The liquidity engine recomputes swing detection, pool lifecycle, session
pools, daily pools and HTF bucket aggregation over the FULL slice per row:

- Cost grows SUPERLINEARLY with history size (measured, see §3):
  55 bars → 7 ms/call · 1,000 → 136 ms · 4,000 → 1.2 s · 8,000 → 4.7 s ·
  20,000 → **27.6 s per call**.
- The full-history variant is O(n²) overall: a 100K-row dataset would take
  **many hours** (projected >3 h).

## 2. Why the fix is parity-correct (train == live == replay == runtime)

The LIVE engine bounds its own causal history:

```text
src/nexus_scalp/application/live_engine.py (~line 2070):
    if len(self.aggregator._completed_bars) > 4000:
        self.aggregator._completed_bars = self.aggregator._completed_bars[-4000:]
```

i.e. the live `LiquidityGovernor.compute_from_engine(bars=completed_bars, ...)`
sees **at most 4000 completed M5 bars** (~14 days — enough for completed D1
buckets, 1440 bars). The TASK-3 parity golden (`tests/golden/deep4000_golden.json`,
`TEST-03-01b`) is itself defined on a **4000-bar** history.

Before this fix, TRAINING passed the full (growing) history while LIVE was
capped at 4000 — a **train ≠ live semantic break** AND an O(n²) runtime
problem. The fix aligns both:

```text
LIQUIDITY_HISTORY_LIMIT = 4000
liquid = compute_liquidity_features(
    all_bars[max(0, i + 1 - LIQUIDITY_HISTORY_LIMIT) : i + 1], ...)
```

- For rows i < 4000: slice is `all_bars[:i+1]` — byte-identical to before.
- For rows i ≥ 4000: slice is the LAST 4000 bars — exactly what live sees.
- Causal semantics preserved: only bars with timestamp ≤ decision_at are in
  the slice; the forming HTF bucket remains excluded; swing confirmation
  (±5 fractal) unchanged.

## 3. Measured evidence

Per-call cost of `compute_liquidity_features` vs history size
(`scratch/probe_bug106_engine_curve.py`, `artifacts/benchmarks/bug106_engine_curve.json`):

| history bars | ms/call | s/call |
| ---: | ---: | ---: |
| 55 | 7.27 | 0.007 |
| 200 | 13.68 | 0.014 |
| 1,000 | 136.47 | 0.136 |
| 2,000 | 357.01 | 0.357 |
| 4,000 | 1,223.32 | 1.223 |
| 8,000 | 4,669.46 | 4.669 |
| 20,000 | 27,565.34 | 27.565 |

Worst-case per-call improvement at 20K rows (was: 27.6 s → now capped at the
4000-bar cost 1.22 s): **~22× per call**. Overall build complexity:
O(n²) → O(n × 4000).

`artifacts/benchmarks/bug106_compute_70d.json` — full-frame benchmark
(1K/5K/10K/20K rows, old vs new implementation) is produced by
`scratch/bench_bug106_compute_70d.py`.

## 4. Fix

```text
schema_v2.py:
  + LIQUIDITY_HISTORY_LIMIT: int = 4000
  compute_liquidity_frame / compute_70d_frame:
      liquid = compute_liquidity_features(
          all_bars[max(0, i + 1 - LIQUIDITY_HISTORY_LIMIT) : i + 1], ...)
```

No feature mathematics changed. No engine change. Schema ordering,
normalization ([-3,+3]) and label contracts untouched.

## 5. Parity verification (post-fix)

```text
tests/unit/test_70d_parity_task3.py      32 passed (incl. deep4000 golden, TEST-03-01b)
tests/unit/test_70d_dataset_parity_task3.py   passed
EXIT 0
```

The 4000-bar golden tolerance (≤1e-10 per-feature delta) still passes —
proving the bounded window reproduces the canonical values.

## 6. Result

| metric | old (full history) | new (bounded 4000) |
| :--- | :--- | :--- |
| complexity | O(n²) | O(n × 4000) |
| 20K-row per-call worst case | 27.6 s | 1.22 s |
| train == live history | ❌ (unbounded vs 4000) | ✅ (both 4000) |
| parity golden (deep4000) | ✅ | ✅ |
| feature values (rows < 4000) | identical | identical |

## 7. Follow-up

- The FULL 100K-row 70D dataset build is now feasible:
  ~100K rows × 1.2 s/row ≈ 33 min (single-threaded) instead of many hours.
  (Parallel/chunked build recommended for the A/B/C benchmark.)
- BUG-106 ledger entry updated: FIXED (this task).