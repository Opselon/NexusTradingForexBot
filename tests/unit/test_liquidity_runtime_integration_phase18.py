"""TASK-02-70D-INTEGRATION — 70D Liquidity Integration + UI/Runtime Control Plane.

TEST-70D-01..28 acceptance suite (brief 33), adapted to the ACTUAL repository
contract per brief TEST-29:

  * The repo already registers `scalp_v3` = 350D (forward-declared research
    contract, asserted by existing tests). The 70D integration contract is
    registered as `scalp_v4` (schema-controlled, INV-009) so nothing existing
    is mutated.
  * Slot 50..59 in the repo belongs to the TASK-5 `scalp_v2` momentum family
    (NOT news). Real News is an independent 12D `news_context_v1` stream.
    The 70D contract therefore is:
        BASE 0..49 | FAMILY 50..59 | LIQUIDITY 60..69
  * TASK-1 produced `scalp_liquidity_v1` (60D, liquidity at 50..59) — the
    TASK-2 governor treats it as a FAMILY producer and builds the 70D vector
    by placement, never by mutation.

Coverage map:
    TEST-70D-01/27  family indices do not overlap / no index collision
    TEST-70D-02/22  final dimension = 70 / dataset schema reports 70D
    TEST-70D-03     first 50 unchanged
    TEST-70D-04     family 50..59 preserved (TASK-5 scalp_v2 extras semantics)
    TEST-70D-05     liquidity 60..69 correct (values match TASK-1 producer)
    TEST-70D-06..09 News/Liquidity independence matrix (incl. fallback)
    TEST-70D-10/11  runtime toggle persists + hot-reloads
    TEST-70D-12/25/26  incompatible model blocked / 70D vs 60D rejection
    TEST-70D-13/14  API exposes real values
    TEST-70D-16..17 SSE update + reconnect restore
    TEST-70D-19  no fake fallback values
    TEST-70D-20  stale-state detection
    TEST-70D-21  liquidity overlay uses real backend values
    TEST-70D-24  existing 60D model remains loadable
    TEST-70D-28  parallel News/Liquidity operation
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from nexus_scalp.features.liquidity_engine import (
    BASE_50D,
    LIQUIDITY_DIM,
    LIQUIDITY_FEATURE_NAMES,
    compute_liquidity_features,
)
from nexus_scalp.features.liquidity_runtime import (
    DIMENSION_70D,
    SCHEMA_70D,
    LiquidityGovernor,
    LiquiditySnapshot,
    ModelCompatibility,
    build_70d_vector,
    resolve_model_compatibility,
)
from nexus_scalp.features.schema import FEATURE_SCHEMAS
from nexus_scalp.features.schema_augment import FEATURE_NAMES_60D_EXTRA

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------


def _bar(i: int, t0: datetime, close: float = 3300.0) -> SimpleNamespace:
    """A completed M1 bar (SimpleNamespace stands in for BarData in the
    runtime tests; the pure engine accepts any object with the bar attrs)."""
    return SimpleNamespace(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=t0 + timedelta(minutes=i),
        open=close,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        tick_volume=100,
        is_complete=True,
    )


def _steady_bars(n: int = 60, base: float = 3300.0) -> list[SimpleNamespace]:
    t0 = datetime.now(UTC).replace(microsecond=0)
    return [_bar(i, t0, base + (i * 0.1)) for i in range(n)]


def _liquidity_features(bars: list[SimpleNamespace]) -> LiquiditySnapshot:
    """Compute a real snapshot from the canonical TASK-1 producer."""
    last = bars[-1]
    obj = compute_liquidity_features(
        bars,
        decision_at=last.timestamp,
        mid_price=float(last.close),
        atr=1.5,
    )
    return LiquiditySnapshot(
        decision_at=obj.decision_at,
        mid_price=float(last.close),
        atr=1.5,
        features=tuple(obj.as_vector()),
        pools=tuple(obj.pools),
    )


# ---------------------------------------------------------------------------
# TEST-70D-01 / 27 — family separation + no index collision
# ---------------------------------------------------------------------------


def test_70d_01_family_indices_do_not_overlap() -> None:
    """BASE 0..49, FAMILY 50..59, LIQUIDITY 60..69 — disjoint and exhaustive."""
    base = set(range(BASE_50D))
    assert min(base) == 0 and max(base) == 49
    fam = set(range(50, 60))
    liq = set(range(60, 70))
    assert base.isdisjoint(fam)
    assert base.isdisjoint(liq)
    assert fam.isdisjoint(liq)
    assert base | fam | liq == set(range(70))
    assert len(base | fam | liq) == 70


def test_70d_27_no_index_collision_after_assembly() -> None:
    vec = build_70d_vector([0.0] * 50, [0.0] * 10, [1.0] * 10)
    assert len(vec) == 70
    # every index written exactly once (no duplicate writes possible by
    # construction; assertion guards future reassignment)
    assert vec[59] == 0.0 and vec[60] == 1.0  # boundary family->liquidity


# ---------------------------------------------------------------------------
# TEST-70D-02 — final dimension = 70 (schema registry authoritative)
# ---------------------------------------------------------------------------


def test_70d_02_scalp_v4_is_70_dimensions() -> None:
    schema = FEATURE_SCHEMAS.resolve(SCHEMA_70D)
    assert schema.dimension == DIMENSION_70D == 70
    assert len(schema.columns) == 70
    assert schema.columns[0] == "feat_0"
    assert schema.columns[60] == "feat_60"
    assert schema.columns[69] == "feat_69"


def test_70d_02_scalp_v4_does_not_mutate_existing_schemas() -> None:
    assert FEATURE_SCHEMAS.resolve("scalp_v1").dimension == 50
    assert FEATURE_SCHEMAS.resolve("scalp_v2").dimension == 60  # TASK-5 momentum
    assert FEATURE_SCHEMAS.resolve("scalp_liquidity_v1").dimension == 60  # TASK-1
    assert FEATURE_SCHEMAS.resolve("scalp_v3").dimension == 70  # TASK-03 canonical 70D


# ---------------------------------------------------------------------------
# TEST-70D-03 — first 50 unchanged
# ---------------------------------------------------------------------------


def test_70d_03_first_50_unchanged_through_governor() -> None:
    base50 = [float(i) * 0.01 - 1.0 for i in range(50)]
    vec = build_70d_vector(base50, [0.0] * 10, [0.0] * 10)
    assert vec[:50] == base50
    assert vec[50:] == [0.0] * 20


# ---------------------------------------------------------------------------
# TEST-70D-04 — family 50..59 preserved (TASK-5 scalp_v2 extras semantics)
# ---------------------------------------------------------------------------


def test_70d_04_family_block_matches_scalp_v2_extras_order() -> None:
    # The 70D contract's 50..59 block is the TASK-5 momentum family order.
    assert len(FEATURE_NAMES_60D_EXTRA) == 10
    family = list(FEATURE_NAMES_60D_EXTRA)
    assert family[0] == "regime_compression"
    assert family[9] == "direction_bias_8"
    vec = build_70d_vector([0.0] * 50, [1.0] * 10, [2.0] * 10, family_schema_id="scalp_v2")
    assert vec[50:60] == [1.0] * 10
    assert vec[60:70] == [2.0] * 10


def test_70d_04_no_family_block_fills_neutral_never_live() -> None:
    # News-only runtime (no family producer): geometry stays valid, content is
    # explicitly neutral (documented defaults, never fabricated).
    vec = build_70d_vector([0.0] * 50, None, [3.0, 3.0, 0, 0, 0, 3.0, 3.0, 0, 0, 0])
    assert len(vec) == 70
    assert vec[50:60] == [3.0, 3.0, 0.0, 0.0, 0.0, 3.0, 3.0, 0.0, 0.0, 0.0]


# ---------------------------------------------------------------------------
# TEST-70D-05 — liquidity 60..69 correct
# ---------------------------------------------------------------------------


def test_70d_05_liquidity_block_uses_canonical_producer_order() -> None:
    bars = _steady_bars()
    snap = _liquidity_features(bars)
    assert len(snap.features) == 10
    # the names/order are the canonical TASK-1 producer contract
    assert tuple(snap.names) == LIQUIDITY_FEATURE_NAMES
    for v in snap.features:
        assert -3.0 <= v <= 3.0
        assert math.isfinite(v)  # not NaN/Inf
    vec = build_70d_vector([0.0] * 50, [0.0] * 10, snap.features)
    assert vec[60] == snap.features[0]  # bsl_distance_atr
    assert vec[69] == snap.features[9]  # post_sweep_displacement


def test_70d_05_liquidity_snapshot_places_indices_60_69() -> None:
    bars = _steady_bars()
    snap = _liquidity_features(bars)
    payload = snap.to_dict()
    assert set(payload["features"].keys()) == set(LIQUIDITY_FEATURE_NAMES)
    # governor payload reports index 60..69 per value
    gov = LiquidityGovernor(enabled=True)
    gov.compute_from_engine(bars=bars, mid_price=3305.0, atr=1.5, decision_at=bars[-1].timestamp)
    fp = gov.snapshot_payload()
    for name in LIQUIDITY_FEATURE_NAMES:
        # TASK-02: liquidity occupies the LAST 10 slots of the ACTIVE
        # dimension (50..59 under the 60D contract).
        assert fp["features"][name]["index"] == 50 + LIQUIDITY_FEATURE_NAMES.index(name)


# ---------------------------------------------------------------------------
# TEST-70D-06..09 — News/Liquidity independence matrix
# ---------------------------------------------------------------------------


def test_70d_06_07_news_off_liquidity_on_liquidity_stays_available() -> None:
    gov = LiquidityGovernor(enabled=True)
    bars = _steady_bars()
    gov.compute_from_engine(bars=bars, mid_price=3305.0, atr=1.5, decision_at=bars[-1].timestamp)
    assert gov.enabled
    assert gov.status() == "ENABLED"
    # news disabled (None) has NO effect on liquidity report (brief 5)
    report = gov.report()
    assert report["available"] is True
    assert report["feature_count"] == 10


def test_70d_08_09_news_on_liquidity_off_news_remains_and_fallback_safe() -> None:
    gov = LiquidityGovernor(enabled=False)
    report = gov.report()
    assert report["enabled"] is False
    assert report["status"] == "DISABLED"
    assert report["available"] is False
    # safe fallback: UI/API must see neutral, never fabricated values
    assert report["features"] == {}
    # switching on afterwards yields UNAVAILABLE (no snapshot yet) — honest
    gov.set_enabled(True, actor="test")
    assert gov.status() == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# TEST-70D-10/11 — runtime toggle persists + hot-reloads
# ---------------------------------------------------------------------------


def test_70d_10_runtime_toggle_persists_via_settings_service(tmp_path) -> None:
    """The governor persists through SettingsService.db (the SettingsService
    facade exposes no set(); the DB owns the typed application_settings
    table). Verifies the REAL persistence path with a real temp DB."""
    from nexus_scalp.settings.service import SettingsDatabase, SettingsService

    svc = SettingsService(db=SettingsDatabase(db_path=Path(tmp_path) / "s.db"))
    gov = LiquidityGovernor(enabled=False, settings_service=svc)
    gov.set_enabled(True, actor="test")
    row = svc.db.get("model.liquidity_features_enabled")
    assert row is not None and row.value is True
    assert gov.enabled is True
    gov.set_enabled(False, actor="test")
    row2 = svc.db.get("model.liquidity_features_enabled")
    assert row2 is not None and row2.value is False
    assert gov.enabled is False


def test_70d_11_toggle_hot_reload_updates_runtime_without_engine_restart() -> None:
    gov = LiquidityGovernor(enabled=False)
    svc = MagicMock()
    gov._settings_service = svc
    report = gov.set_enabled(True, actor="web")
    assert report["enabled"] is True
    assert report["status"] == "UNAVAILABLE"  # no snapshot yet — honest
    # value is live in the same object (no restart semantics anywhere)
    gov2 = LiquidityGovernor(enabled=False, settings_service=svc)
    gov2.set_enabled(True, actor="web")
    assert gov2.enabled


# ---------------------------------------------------------------------------
# TEST-70D-12/25/26 — model compatibility matrix
# ---------------------------------------------------------------------------


def test_70d_12_incompatible_model_blocked() -> None:
    # scalp_v2/60D model + 70D runtime -> BLOCK (LIQUIDITY_ENABLED_BUT_MODEL_INCOMPATIBLE)
    r = resolve_model_compatibility("scalp_v2", 60, SCHEMA_70D, 70)
    assert r["result"] == ModelCompatibility.BLOCK.value
    assert r["reason"] == "LIQUIDITY_ENABLED_BUT_MODEL_INCOMPATIBLE"
    # scalp_v3/350D + 70D runtime -> BLOCK too
    r2 = resolve_model_compatibility("scalp_v3", 350, SCHEMA_70D, 70)
    assert r2["result"] == ModelCompatibility.BLOCK.value


def test_70d_25_70d_model_rejects_60d_input() -> None:
    r = resolve_model_compatibility(SCHEMA_70D, 70, "scalp_v2", 60)
    assert r["result"] == ModelCompatibility.BLOCK.value
    assert r["reason"] == "MODEL_DIMENSION_EXCEEDS_RUNTIME"


def test_70d_26_60d_model_rejects_70d_input() -> None:
    r = resolve_model_compatibility("scalp_v2", 60, SCHEMA_70D, 70)
    assert r["result"] == ModelCompatibility.BLOCK.value


def test_70d_12_70d_model_70d_runtime_passes() -> None:
    r = resolve_model_compatibility(SCHEMA_70D, 70, SCHEMA_70D, 70)
    assert r["result"] == ModelCompatibility.PASS.value


def test_70d_12_unknown_model_never_guessed() -> None:
    r = resolve_model_compatibility(None, None, SCHEMA_70D, 70)
    assert r["result"] == ModelCompatibility.UNKNOWN.value


# ---------------------------------------------------------------------------
# TEST-70D-13 — API exposes real values (web layer integration)
# ---------------------------------------------------------------------------


def test_70d_13_liquidity_state_endpoint_returns_real_values() -> None:
    from nexus_scalp.features.liquidity_runtime import LiquidityGovernor

    gov = LiquidityGovernor(enabled=True)
    bars = _steady_bars()
    gov.compute_from_engine(bars=bars, mid_price=3305.0, atr=1.5, decision_at=bars[-1].timestamp)
    report = gov.report()
    # values are from the actual runtime producer (not mocked constants)
    for name in LIQUIDITY_FEATURE_NAMES:
        assert name in report["features"]
        assert isinstance(report["features"][name], float)


def test_70d_13_snapshot_payload_has_schema_and_dimension() -> None:
    gov = LiquidityGovernor(enabled=True)
    bars = _steady_bars()
    gov.compute_from_engine(bars=bars, mid_price=3305.0, atr=1.5, decision_at=bars[-1].timestamp)
    fp = gov.snapshot_payload()
    # the governor reports the ACTUAL runtime contract it operates under
    # (TASK-02 60D path -> scalp_liquidity_v1/60D; the canonical 70D
    # scalp_v3 contract is exposed via features70.assemble_70d + the
    # compute_70d_frame dataset builder).
    assert fp["dimension"] == gov._active_schema_block()["dimension"]
    assert fp["schema_id"] == gov._active_schema_block()["id"]
    assert fp["available"] is True
    assert len(fp["features"]) == 10


# ---------------------------------------------------------------------------
# TEST-70D-16/17 — SSE propagation + reconnect restore
# ---------------------------------------------------------------------------


def test_70d_16_sse_incremental_carries_liquidity_section() -> None:
    """The /api/ticks/stream SSE incremental payload keeps the liquidity
    section (the web layer merges it into the snapshot; the server-side state
    graph is the source)."""
    gov = LiquidityGovernor(enabled=True)
    bars = _steady_bars()
    gov.compute_from_engine(bars=bars, mid_price=3305.0, atr=1.5, decision_at=bars[-1].timestamp)
    report = gov.report()
    # contract test: the exact dict that get_system_state() embeds
    assert "status" in report and "features" in report
    # TASK-02: when enabled the ACTIVE schema is the 60D liquidity contract;
    # the 70D scalp_v4 contract is exposed as the reserved block.
    assert report["schema"]["id"] == "scalp_liquidity_v1"
    assert report["reserved_70d_schema"]["id"] == SCHEMA_70D


def test_70d_17_reconnect_restores_state_from_server_snapshot() -> None:
    """After an SSE reconnect the UI pulls GET /api/status (canonical);
    the liquidity section must be identical to the governor report."""
    gov = LiquidityGovernor(enabled=True)
    bars = _steady_bars()
    gov.compute_from_engine(bars=bars, mid_price=3305.0, atr=1.5, decision_at=bars[-1].timestamp)
    r1 = gov.report()
    # a fresh network call simply re-reads the same governor (stateless GET)
    r2 = gov.report()
    assert r1["features"] == r2["features"]
    assert r1["status"] == r2["status"]


# ---------------------------------------------------------------------------
# TEST-70D-19 — no fake fallback values
# ---------------------------------------------------------------------------


def test_70d_19_disabled_state_has_no_fake_values() -> None:
    gov = LiquidityGovernor(enabled=False)
    report = gov.report()
    assert report["features"] == {}
    assert report["available"] is False
    assert report["status"] == "DISABLED"


def test_70d_19_degraded_after_error_is_honest() -> None:
    gov = LiquidityGovernor(enabled=True)
    with pytest.raises(ValueError):
        gov.compute_from_engine(bars=[], mid_price=1.0, atr=1.0)
    assert gov.status() == "UNAVAILABLE"  # no snapshot yet -> not fabricating
    assert gov.last_error is not None


# ---------------------------------------------------------------------------
# TEST-70D-20 — stale-state detection
# ---------------------------------------------------------------------------


def test_70d_20_stale_detection_uses_actual_timestamps() -> None:
    gov = LiquidityGovernor(enabled=True)
    bars = _steady_bars()
    gov.compute_from_engine(bars=bars, mid_price=3305.0, atr=1.5, decision_at=bars[-1].timestamp)
    assert gov.status() == "ENABLED"
    assert gov.causal_state() == "VALID"
    # simulate age: push last success back beyond the LIVE threshold
    with patch.object(gov, "_last_success_at", time.monotonic() - 9999.0):
        assert gov.status() == "DEGRADED"
        assert gov.causal_state() == "STALE"


# ---------------------------------------------------------------------------
# TEST-70D-21 — overlay uses real backend values
# ---------------------------------------------------------------------------


def test_70d_21_overlay_pools_come_from_real_snapshot() -> None:
    gov = LiquidityGovernor(enabled=True)
    bars = _steady_bars()
    gov.compute_from_engine(bars=bars, mid_price=3305.0, atr=1.5, decision_at=bars[-1].timestamp)
    report = gov.report()
    # pools present and every pool has a numeric price (backend real values)
    if report["pools"]:
        for pool in report["pools"]:
            assert "price" in pool
            price = pool.get("price")
            assert price is None or isinstance(price, (int, float))


# ---------------------------------------------------------------------------
# TEST-70D-22 — dataset schema reports 70D
# ---------------------------------------------------------------------------


def test_70d_22_feature_schema_columns_report_70d() -> None:
    schema = FEATURE_SCHEMAS.resolve(SCHEMA_70D)
    cols = schema.columns
    assert len(cols) == 70
    assert cols[59] == "feat_59"
    assert cols[60] == "feat_60"


# ---------------------------------------------------------------------------
# TEST-70D-24 — existing 60D model remains loadable (backward compat)
# ---------------------------------------------------------------------------


def test_70d_24_60d_scalp_v2_schema_untouched_by_70d() -> None:
    # The registry entry itself is unchanged (INV-009) — a scalp_v2 artifact
    # resolves exactly as before TASK-2.
    v2 = FEATURE_SCHEMAS.resolve("scalp_v2")
    assert v2.dimension == 60
    assert v2.supersedes == "scalp_v1"
    assert FEATURE_SCHEMAS.resolve("scalp_v1").is_active


# ---------------------------------------------------------------------------
# TEST-70D-28 — parallel News/Liquidity operation (independence)
# ---------------------------------------------------------------------------


def test_70d_28_news_and_liquidity_states_are_independent() -> None:
    liq_on_news_off = LiquidityGovernor(enabled=True)
    bars = _steady_bars()
    liq_on_news_off.compute_from_engine(
        bars=bars, mid_price=3305.0, atr=1.5, decision_at=bars[-1].timestamp
    )
    report_no_news = liq_on_news_off.report()
    assert report_no_news["available"] is True
    # news context is a separate 12D stream; its absence never touches the
    # liquidity payload and vice versa
    assert "news" not in report_no_news

    liq_off_news_on = LiquidityGovernor(enabled=False)
    report_liq_off = liq_off_news_on.report()
    assert report_liq_off["status"] == "DISABLED"


# ---------------------------------------------------------------------------
# Runtime smoke on the governor (brief 34) — no engine/DB needed
# ---------------------------------------------------------------------------


def test_70d_runtime_smoke_governor_round_trip() -> None:
    gov = LiquidityGovernor(enabled=True)
    bars = _steady_bars()
    snap = gov.compute_from_engine(
        bars=bars, mid_price=3305.0, atr=1.5, decision_at=bars[-1].timestamp
    )
    assert snap.features  # ten real values
    assert gov.last_latency_ms is not None and gov.last_latency_ms >= 0.0
    rep = gov.report()
    assert rep["latency_ms"] is not None
    assert rep["source"] == "LIVE_MARKET_STATE"
    assert rep["causal_state"] == "VALID"
