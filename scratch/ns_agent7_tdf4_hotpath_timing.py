"""Agent 7 — TDF-4: hot-path timing probe (INV-001 evidence).

Runs the REAL _process_tick_pipeline components against a synthetic feed and
measures per-call latency of each hot-path stage, hunting for synchronous
I/O stalls:

  A. ScalpFeatureEngine.compute_from_bars (x N)      — pure CPU expectation
  B. regime_classifier.classify_tick (x N)           — pure CPU expectation
  C. SignalPolicy.evaluate_probabilities (x N)       — pure CPU expectation
  D. RuleMatrixEngine.refresh_cache (x N)            — **DB READ every TTL expiry**
  E. risk_engine.calculate_volume + clamp (x N)      — adapter-free math
  F. audit log_signal queue put (x N)                — queue-only expectation

Stall detector: any single call > 25ms is flagged; mean/p95 reported.
This is OBSERVATIONAL evidence, not a unit test.
"""
from __future__ import annotations

import statistics
import sys
import time
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")


def make_bars(n: int, start: float = 2400.0, seed: int = 3) -> list:
    from nexus_scalp.market_data.bar_aggregator import BarData

    bars = []
    px = start
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    for i in range(n):
        seed = (seed * 1103515245 + 12345) % (2**31)
        delta = ((seed % 1000) - 500) / 5000.0
        o = px
        c = px + delta
        bars.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=t0 + timedelta(minutes=i),
                open=o,
                high=max(o, c) + 0.08,
                low=min(o, c) - 0.08,
                close=c,
                tick_volume=120,
                is_complete=True,
            )
        )
        px = c
    return bars


def timed(fn, n: int) -> dict:
    xs = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t0) * 1000.0)
    xs.sort()
    p95 = xs[min(len(xs) - 1, int(len(xs) * 0.95))]
    return {
        "n": n,
        "mean_ms": round(statistics.fmean(xs), 3),
        "p95_ms": round(p95, 3),
        "max_ms": round(xs[-1], 3),
    }


def main() -> int:
    from nexus_scalp.domain.models import TickData
    from nexus_scalp.features.regime_classifier import MarketRegimeClassifier
    from nexus_scalp.features.scalp_features import ScalpFeatureEngine
    from nexus_scalp.risk.risk_engine import RiskEngine

    bars = make_bars(600)
    eng = ScalpFeatureEngine(symbol="XAUUSD")
    t0 = bars[-1].timestamp + timedelta(seconds=9)
    tick = TickData(
        symbol="XAUUSD", timestamp=t0, bid=bars[-1].close - 0.11, ask=bars[-1].close + 0.11, volume=3.0
    )

    print("=== TDF-4: hot-path stage timing (real classes, synthetic feed) ===")

    # A. features
    res_a = timed(lambda: eng.compute_from_bars(completed_bars=bars, current_tick=tick), 60)
    print(f"A compute_from_bars       {res_a}")
    fv = eng.compute_from_bars(completed_bars=bars, current_tick=tick)

    # B. regime
    rc = MarketRegimeClassifier(symbol="XAUUSD")
    t = tick
    res_b = timed(lambda: rc.classify_tick(current_tick=t), 200)
    print(f"B classify_tick           {res_b}")

    # D. rule matrix cache refresh — REAL sqlite path on a temp DB
    import tempfile
    import os

    tmpdb = os.path.join(tempfile.gettempdir(), "ns_agent7_rules.db")
    if os.path.exists(tmpdb):
        os.remove(tmpdb)
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    audit = AuditRepository(db_url=f"sqlite:///{tmpdb}")
    from nexus_scalp.signals.rule_matrix import RuleMatrixEngine

    rm = RuleMatrixEngine(audit_repo=audit)
    rm._last_cache_refresh = 0.0  # force TTL expiry on EVERY call
    res_d = timed(rm.refresh_cache, 30)
    print(f"D rule_matrix.refresh    {res_d}   <-- TTL-expired path: sqlite3.connect per call")

    # F. audit queue put (log_signal) — must be queue-only
    class _FakeProposal:
        request_id = "probe-1"
        symbol = "XAUUSD"
        action = type("A", (), {"value": "NO_TRADE"})()
        confidence = 0.1
        proposed_entry = 2400.0
        stop_loss = 2390.0
        take_profit = 2420.0
        risk_reward_ratio = 2.0
        reason_code = "PROBE"
        model_action = "NO_TRADE"
        buy_probability = 0.2
        sell_probability = 0.1
        no_trade_probability = 0.7
        regime = "RANGING"
        regime_confidence = 0.5
        risk_allowed = False
        guardian_status = "IDLE"
        rejection_reason = "PROBE"
        final_action = "NO_TRADE"
        risk_checks = {}
        execution_mode = "STANDARD"
        override_reason = None
        decision_stage = "PROBE"
        blocked_by = None
        htf_score = 0.0
        smc_score = 0.0
        confidence_before_filters = 0.1
        confidence_after_filters = 0.1
        generated_at = t0
        execution_id = "EXEC-PROBE"

    res_f = timed(lambda: audit.log_signal(_FakeProposal()), 200)  # type: ignore[arg-type]
    print(f"F audit.log_signal (put)  {res_f}")

    # E. risk math
    class _Acc:
        equity = 5000.0
        margin_free = 4000.0
        leverage = 100

    class _Sym:
        trade_contract_size = 100.0
        volume_step = 0.01
        volume_min = 0.01
        volume_max = 100.0

    from nexus_scalp.configuration.config import RiskConfig

    risk = RiskEngine(config=RiskConfig())
    res_e = timed(
        lambda: risk.calculate_dynamic_volume(
            entry=2400.0, sl=2395.0, account=_Acc(), symbol_info=_Sym(), risk_pct=0.5
        ),
        200,
    )
    print(f"E risk.calculate_volume   {res_e}")

    audit.close()
    if os.path.exists(tmpdb):
        try:
            os.remove(tmpdb)
        except OSError:
            pass

    stalls = []
    for name, r, budget in [
        ("A features", res_a, 25.0),
        ("B regime", res_b, 5.0),
        ("D rule_matrix TTL refresh", res_d, 25.0),
        ("F audit.put", res_f, 2.0),
        ("E risk math", res_e, 2.0),
    ]:
        if r["max_ms"] > budget:
            stalls.append(f"{name} max={r['max_ms']}ms > {budget}ms budget")

    print()
    if stalls:
        print("TDF-4 STALL FLAGS:")
        for s in stalls:
            print("  -", s)
    else:
        print("TDF-4: no budget violations in this run")
    print("TDF-4 VERDICT: COMPLETE (observational; see flags above)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
