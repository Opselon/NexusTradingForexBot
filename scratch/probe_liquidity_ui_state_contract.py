"""probe_liquidity_ui_state_contract.py — repro every Liquidity UI contradiction
with the REAL runtime payloads (no mocking of the bug away).

Reproduces exactly the state described in the Liquidity Intelligence
forensic task: disabled governor + retained stale snapshot -> the API/UI
contract contradictions. Output is captured to
probe_liquidity_ui_state_contract.out.txt
"""
from __future__ import annotations

from datetime import UTC, datetime

from nexus_scalp.features.liquidity_runtime import LiquidityGovernor, SourceKind
from nexus_scalp.features.schema_contract import (
    LIQUIDITY_10D_NAMES,
    canonical_feature_names,
    family_of,
)


def _steady_bars(n: int = 120):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    t0 = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=n)

    def _bar(i: int) -> SimpleNamespace:
        ts = t0 + timedelta(minutes=i)
        close = 3300.0 + (i * 0.1)
        return SimpleNamespace(
            timestamp=ts,
            open=close - 0.05,
            high=close + 0.2,
            low=close - 0.2,
            close=close,
            tick_volume=100,
            is_complete=True,
        )

    return [_bar(i) for i in range(n)]


class _FakeEngine:
    """Models a 50D live champion (scalp_v1/50D) — the CURRENT repo active."""

    FEATURE_SCHEMA_ID = "scalp_v1"
    FEATURE_DIM = 50


def main() -> None:
    out: list[str] = []
    p = out.append

    p("=" * 78)
    p("PROBE: Liquidity Intelligence UI State Contract — reproduction")
    p("=" * 78)

    # --- 1. Build the EXACT production state: enabled+compute -> toggle OFF.
    gov = LiquidityGovernor(enabled=True)
    gov.bind_engine(_FakeEngine())
    bars = _steady_bars(120)
    gov.compute_from_engine(
        bars=bars,
        mid_price=3305.0,
        atr=1.5,
        decision_at=datetime(2026, 8, 19, 9, 30, 0, tzinfo=UTC),
        source=SourceKind.LIVE_MARKET_STATE,
    )
    # simulate the real toggle: UI OFF after a live session
    gov.set_enabled(False, actor="probe")

    p("\n[1] RUNTIME STATE (governor internals)")
    p(f"    enabled            = {gov.enabled}")
    p(f"    snapshot present   = {gov.last_snapshot is not None}")
    p(f"    _last_success_at   = {gov._last_success_at!r}  (time.monotonic!)")
    p(f"    _source            = {gov._source}")
    p(f"    status()           = {gov.status()}")
    p(f"    causal_state()     = {gov.causal_state()}")

    # --- 2. /api/liquidity/state payload (what the UI renders)
    rep = gov.report()
    p("\n[2] GET /api/liquidity/state  (report())")
    for k in (
        "enabled", "available", "status", "causal_state", "source",
        "algorithm_version", "last_update", "latency_ms",
    ):
        p(f"    {k:22s} = {rep.get(k)!r}")
    p(f"    schema             = {rep.get('schema')}")
    p(f"    model_compatibility= {rep.get('model_compatibility')}")
    p(f"    feature_count      = {rep.get('feature_count')}")
    feats = rep.get("features", {})
    for name in LIQUIDITY_10D_NAMES:
        p(f"    feat {name:28s} = {feats.get(name)!r}")

    # --- 3. /api/liquidity/features payload (snapshot_payload) — index math
    fp = gov.snapshot_payload()
    p("\n[3] GET /api/liquidity/features (snapshot_payload())")
    p(f"    schema_id = {fp.get('schema_id')}  dimension = {fp.get('dimension')}")
    p(f"    timestamp = {fp.get('timestamp')}")
    for name in LIQUIDITY_10D_NAMES:
        e = fp.get("features", {}).get(name, {})
        p(
            f"    {name:28s} idx={e.get('index'):>3} value={e.get('value')!r:>8} "
            f"status={e.get('status')} source={e.get('source')}"
        )

    # --- 4. Canonical contract truth
    p("\n[4] AUTHORITATIVE REGISTRY (schema_contract.py scalp_v3 70D)")
    names70 = canonical_feature_names()
    p("    schema_id    = scalp_v3  dimension = 70")
    for name in LIQUIDITY_10D_NAMES:
        idx = names70.index(name)
        p(f"    {name:28s} idx={idx:>3} family={family_of(idx)}")
    liq_start = min(names70.index(n) for n in LIQUIDITY_10D_NAMES)
    liq_end = max(names70.index(n) for n in LIQUIDITY_10D_NAMES)
    p(f"    liquidity block = {liq_start}..{liq_end}")

    # --- 5. timestamp forensics
    p("\n[5] TIMESTAMP FORENSICS (last_update path)")
    p(f"    _last_success_at (monotonic sec since boot) = {gov._last_success_at}")
    p(
        "    report last_update = "
        f"{datetime.fromtimestamp(gov._last_success_at, tz=UTC).isoformat()}"
        "    <- WRONG: fromtimestamp(monotonic) => 1970 epoch"
    )
    p(
        f"    snapshot decision_at (real bar ts)         = "
        f"{gov.last_snapshot.decision_at.isoformat() if gov.last_snapshot else None}"
    )

    # --- 6. contradiction matrix assertions
    p("\n[6] CONTRADICTION MATRIX (PROVEN)")
    c = []
    if rep["enabled"] is False and len(rep.get("features", {})) == 10:
        c.append("A. Status=DISABLED yet 10 liquidity values exposed as 'features'")
    if rep["available"] is True and rep["source"] == "UNAVAILABLE":
        c.append("B. available=True while source=UNAVAILABLE")
    if rep["causal_state"] == "VALID" and rep["source"] == "UNAVAILABLE":
        c.append("C. causal_state=VALID while source=UNAVAILABLE")
    if rep["enabled"] is False and (
        rep.get("model_compatibility", {}).get("result") == "BLOCK"
    ):
        c.append(
            "D. enabled=False yet model_compatibility=BLOCK("
            + str(rep.get("model_compatibility", {}).get("reason"))
            + ")"
        )
    liq_indices = [
        fp["features"][n]["index"]
        for n in LIQUIDITY_10D_NAMES
        if n in fp.get("features", {})
    ]
    if liq_indices and (min(liq_indices) != 60 or max(liq_indices) != 69):
        c.append(
            f"E. snapshot_payload indices {min(liq_indices)}..{max(liq_indices)} "
            "NOT canonical 60..69"
        )
    if rep["algorithm_version"] != "liquidity-v1.1":
        c.append(
            "F. algorithm_version="
            f"{rep['algorithm_version']!r} != producer version liquidity-v1.1"
        )
    for line in c or ["(none)"]:
        p(f"    {line}")

    p("\nPROBE DONE")
    print("\n".join(out))


if __name__ == "__main__":
    main()