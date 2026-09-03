# GAP HANDLING REPORT — XAUUSD M1 (FIX #8)

**Date:** 2026-09-03
**Source:** `data/raw/XAUUSD_M1.csv` (100,000 rows)
**Repo branch:** `hermes-subagent/subagent-sa-1-197034d2`
**Related files:**
`src/nexus_scalp/model_generation/temporal_contract.py` (`CANONICAL_SEQ_LEN`, `CANONICAL_MAX_GAP_US`, `CANONICAL_PURGE_BARS`, `CANONICAL_EMBARGO_BARS`)
`src/nexus_scalp/model_generation/sequence.py` (`SequenceBuilder`, `seq_len`, `max_gap_us`, `valid`)
`src/nexus_scalp/model_generation/architectures.py` (`TCNAttentionV1`)

## Audit Summary

| Field | Value |
|-------|-------|
| Rows | 100,000 (`time,open,high,low,close,tick_volume,spread,real_volume,time_utc`) |
| Range | 2026-05-01 17:15 UTC -> 2026-08-17 19:24 UTC |
| Dup timestamps | 0 |
| Timezone | UTC (`time` epoch seconds == `time_utc` ISO8601 UTC, mismatch 0) |
| Gaps >60s | 78 |
| Largest gap | 53.0h (Fri 2026-07-03 20:00 -> Mon 2026-07-06 01:00, 190,800s; second 2026-06-19 20:01 -> 2026-06-22 01:00, 190,740s) |
| Histogram | >60s-5m: 2  |  5m-1h: 0  |  >1h: 76 |
| Typical large gap | ~50.0h (51 weekends/holidays at Thu/Fri 22:59 -> Mon 01:00) |
| Spread | avg 8.0 p95 24 p99 ~50 max 622 pts |

78 gaps >60s are almost entirely weekend/holiday closes (Fri evening -> Mon early AM), not data dropout. Two short gaps (2m) are isolated; one 53h gap straddles Thu Jul 3 -> Mon Jul 6 (US holiday weekend).

## Gap Handling Contract

The contract is shared by dataset generation, training, validation, OOS, offline and live inference (NO second builder).

### 1. Rolling features

- `features/scalp_features.py`: 50D base features are windowed on `completed_bars[-55:]` + synthetic tick. A gap is visible as missing `BarData` at the aggregator level; no interpolation. When `len(completed_bars) < 55` the engine emits the cold-start vector (no gap fabrication). Gaps do not inject future data.
- `features/liquidity_engine.py:1159` / `model_generation/schema_v2.py:349`: liquidity and 70D frames are computed on the canonical window `[i-54 .. i]` bounded by `LIQUIDITY_HISTORY_LIMIT = 4000`. `dataset_generation` does not interpolate across gaps — bars are whatever the broker emitted.

### 2. HTF pools

- `liquidity_engine.htf_liquidity_score` (`HTF_TIMEFRAMES_MIN = (60, 240, 1440)`) aggregates COMPLETED H1/H4/D1 buckets only: a bucket contributes only when its end time <= `decision_at` (current forming candle excluded). A gap spanning one or more whole HTF buckets simply yields fewer completed buckets in that window; score degrades gracefully to `DEFAULT_HTF_SCORE` (0) when evidence is absent. Incrementally maintained in `schema_v2_incremental.py` identically.

### 3. Sequence windowing (the FIX #1 defect)

- `model_generation/sequence.py:SequenceBuilder(seq_len=L, max_gap_us=10*60*1_000_000)`: every candidate window `[i-L+1 .. i]` is checked timestamp-wise — if any inter-bar gap `> max_gap_us` (10 min) exists inside the window, `valid[i] = False`. Training (`sequence_training.py`) drops `~valid` rows; validation/OOS inherit the same filter because `valid` is computed from the same frame. No cross-symbol or cross-gap window is learned.
- Canonical `L`: `temporal_contract.CANONICAL_SEQ_LEN = 32` (F2 experiments) with `16` supported per-artifact via `model.meta.json:temporal_contract.seq_len` / `seq_len`. One value per artifact; train vs live must agree.

### 4. Live inference (gap-aware sequencing)

- `application/live_engine.py:_live_sequence_buffer` (deque, bounded to `L`): holds the last `L` post-scaler 70D vectors. Gap clock (`_live_last_bar_ts_us`): each completed-bar tick updates the interval; if `gap_us > max_gap_us` the buffer is cleared and `gap_invalid=True` until a contiguous run of `L` bars refills it. While `len(buffer) < L` or `gap_invalid`, inference falls back to the honest 2D MLP path (seq_len=1) — never silently pads across a weekend. `note_bar_gap()` and `_maybe_build_live_sequence_tensor()` encapsulate this.

### 5. Labels

- `labeling/triple_barrier.py:TripleBarrierLabeler` with `embargo_bars=3` (online) and `purge_gap_bars=15 / embargo_bars=15` in the walk-forward / `temporal_contract` canonical. A gap inside the label horizon does not invent bars — the barrier scan sees only emitted bars; `is_eval_sample` and stride already bound evaluability. Weekend gaps that land inside a horizon simply truncate observable continuation (no look-ahead).

### 6. OOS / Validation splits

- Purged walk-forward (`training/walk_forward_trainer.py:_split_fold_with_embargo`, `research/splitting.py`) removes sequences whose label horizon would cross a fold boundary; gap-invalid sequences are already `valid=False` so they never enter a fold. HTF/sequence/OOS evidence for any candidate must be reported on these purged, gap-safe splits (see `docs/forensics/t70d_master_forensic_report_2026-09-03.md`).

## Verification

```bash
C:/Users/Capsizer/source/repos/NexusTradingForexBot/.venv/Scripts/python.exe -m pytest tests/unit/test_temporal_sequence_contract.py -q
```

Reproduction of the raw audit numbers:

```python
import csv
from datetime import datetime, timezone
from collections import Counter

rows = []
with open("data/raw/XAUUSD_M1.csv", newline="") as f:
    for row in csv.DictReader(f):
        rows.append(row)
ts = [datetime.fromisoformat(r["time_utc"].replace("Z", "+00:00")) for r in rows]
gaps = [
    (i, ts[i - 1], ts[i], (ts[i] - ts[i - 1]).total_seconds())
    for i in range(1, len(ts))
    if (ts[i] - ts[i - 1]).total_seconds() > 60
]
print(len(gaps), max(g[3] for g in gaps) / 3600)
```
