"""XAUUSD calibration report — REAL vs SYNTHETIC PAPER (Phase 2, Priority 3).

This document IS the calibration interpretation.  The numbers live in
scratch/calibration/xauusd_real_calibration.json (written by
scratch/measure_real_xauusd_calibration.py); they are reproduced here so
the reader does not have to open the JSON.

Real data measured: scratch/data/forensic_50d_live_T1_bars.json (900 M1
bars, T1) plus forensic_50d_live_T2/T3 (9 extra bars after dedupe) -> 909
unique M1 bars, 2026-08-18 01:14..16:22 UTC (Asia/London session).  Each bar
records MT5 copy_rates fields: open/high/low/close, tick_volume, spread
(points where 1 point = 0.01 for XAUUSD -> e.g. 12 points = $0.12).

Status: MEASURED_FROM_REAL_DATA.  No separate weeks/days of history were
available for a fuller seasonal estimate — the "UNKNOWN" section below
records what remains unmeasurable until more data arrives.

Interpretation (real vs synthetic 5000-tick seed 42):
  - Spread: REAL mean 14.7 pts = $0.147 (p50 $0.14, p95 $0.24, min 0.07 max
    0.37).  The synthetic tight baseline of $0.08-0.18 (mean $0.148 in 5000
    ticks) is RIGHT — the live spread on that 15h window sits dead-centre
    in the synthetic baseline.  Only the stress-scaled x2.5..x4 profiles
    ($0.30-0.60) fall outside the measured tail.  CALIBRATED.
  - Returns: REAL M1 log-return sd ~3.3e-4/bar (ann. vol ~20%).  Synthetic
    TICK sd ~3.6e-5/tick (250ms ticks) — not directly comparable units —
    but the PAPER tick walk with phi=0.6 AR(1) + vol blocks stays within
    an RTSL-consistent envelope; mean drift near 0 in both.
  - Fat tails / skewness: REAL excess kurtosis +4.4 (heavy tails) and
    skew -0.28; synthetic excess kurt 0.6 and skew +0.32 — synthetic tails
    are THIN relative to the real afternoon spike that produced the 17
    >3-sigma moves (1.9%).  Action: add a SPIKE/VOLATILE regime mix when
    stress-testing tail losses; do not widen the baseline spread to "fake"
    tail — the tail is return kurtosis, not spread.
  - Volatility clustering: REAL |ret| autocorr_lag1 = 0.20, lag10 = 0.17
    — strong clustering, as expected for 2026 intraday XAU.  Synthetic
    paper_stress ticks with the RANGE/AR(1) baseline show ~0 for |ret| in
    the 4-ticks/sec uniform sample — clustering is under-reproduced
    outside Regime.VOLATILE.  The adapter's vol_block (15-45 tick calm/
    storm regimes) partially recovers it; stress runs must exercise the
    VOLATILE regime to reproduce clustering.
  - Autocorr of returns: REAL ~0 at lag1 (-0.02) and lag5 (+0.02) — no
    exploitable momentum/reversion at M1 within this window.  Synthetic
    similarly ~0 (-0.03 at lag1).  OK.
  - Trend/range persistence: REAL 49.1% up-bars, sign autocorr -0.05,
    mean run 1.9 bars, max 9 — choppy.  Synthetic is choppy by design
    under RANGE/TREND mix with 2% mean-revert; consistent.
  - Shock frequency / bar range / gaps: REAL mean bar range $1.93
    (p95 $4.64).  No >1% M1 jumps, 17 bars >3 sd.  Gaps: 0 missing bars
    — contiguous, as promised by MT5 is_complete=True contract.
  - Sessions: the capture covers Asia+London+overlap (no NY evening in
    this slice), so session calibration of NY/evening volatility is
    STILL UNKNOWN — do not extrapolate.

What remains UNKNOWN until a longer history arrives:
  A file data/raw/XAUUSD_M1.parquet does not exist in this tree (mentioned
  by scratch/calibrate_regime_realdata.py as the canonical source).  To
  graduate from this 15h slice to a full seasonal calibration you need a
  multi-week M1 parquet and the following per-metric targets before calling
  the synthetic market "calibrated":
    - Weekly/seasonal spread distribution and hour-of-day spread curve;
    - NY/evening session vol; gap rate over weekends/holidays;
    - Per-regime (trend day, range day, CPI/CAT news window) ret stats;
    - Tail calibrator: hit-rate of >3-sigma and >1%-move events.

Until then: calibration is MEASURED (15h live XAUUSD window) and the
synthetic baseline spread/vol envelope is consistent with that window.
Realism claims outside the measured window stay UNKNOWN and must be gated
behind performative-risk checks (no.paper_perf:* tags).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CALIB = ROOT / "scratch/calibration/xauusd_real_calibration.json"


def markdown() -> str:
    d = json.loads(CALIB.read_text()) if CALIB.exists() else {"status": "not yet generated"}
    return json.dumps(d, indent=2)


if __name__ == "__main__":
    print(markdown())
