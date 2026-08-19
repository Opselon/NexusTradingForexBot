"""Probe: why do the causality tests fail? Deep dive on pool/candidate semantics.

TASK-06-70D-LIQUIDITY-OPTIMIZATION forensics.
Read-only. No source modified.
"""
from datetime import UTC, datetime, timedelta

from nexus_scalp.features import liquidity_engine as le
from tests.helpers.liquidity_fixtures import swing_high_bars

t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)


def show(label, bars, decision_at=None, mid=None):
    f = le.compute_liquidity_features(bars, decision_at=decision_at, mid_price=mid)
    print("==", label, "decision=", decision_at or "last",
          "vec=", [f"{v:.3f}" for v in f.as_vector()])
    return f


# ---- test_liq23 semantics ----
bars = swing_high_bars(50, 3310.0, 3300.0, t0=t0)
print("len(bars)=", len(bars), "bar50 time=", bars[50].timestamp, "bar55 time=", bars[55].timestamp)

# what swings does the engine see with FULL bars (no decision cutoff)?
sh, sl = le.detect_confirmed_swings(bars)
print("SWING HIGHS (full):", [(round(p.price,3), p.candidate_at, p.confirmed_at, p.side.name) for p in sh])
print("SWING LOWS  (full):", [(round(p.price,3), p.candidate_at, p.confirmed_at, p.side.name) for p in sl])

# decision = spike bar time (candidate not yet confirmed)
f1 = le.compute_liquidity_features(bars, decision_at=bars[50].timestamp, mid_price=3300.0)
print("f1 (decision=bar50 time) bsl=", f1.bsl_distance_atr, "ssl=", f1.ssl_distance_atr)
# which pools are usable at that decision?
for p in f1.pools:
    print("  pool", round(p.price,3), p.side.name, p.source.name, "cand=", p.candidate_at,
          "conf=", p.confirmed_at, "usable_at=", p.usable_at, "state=", p.state.name)

# decision = bar50 + 30s (test_liq23 f_before) - friction 30 seconds
f2 = le.compute_liquidity_features(bars, decision_at=bars[50].timestamp + timedelta(seconds=30), mid_price=3300.0)
print("f2 (decision=bar50+30s) bsl=", f2.bsl_distance_atr, "ssl=", f2.ssl_distance_atr)
for p in f2.pools:
    print("  pool", round(p.price,3), p.side.name, p.source.name, "cand=", p.candidate_at,
          "conf=", p.confirmed_at, "usable_at=", p.usable_at, "state=", p.state.name)

# decision = bar55 (confirmed)
f3 = le.compute_liquidity_features(bars, decision_at=bars[55].timestamp, mid_price=3300.0)
print("f3 (decision=bar55) bsl=", f3.bsl_distance_atr, "ssl=", f3.ssl_distance_atr)
for p in f3.pools:
    print("  pool", round(p.price,3), p.side.name, p.source.name, "cand=", p.candidate_at,
          "conf=", p.confirmed_at, "state=", p.state.name)
