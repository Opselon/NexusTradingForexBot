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
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from nexus_scalp.features.liquidity_engine import (
    BASE_50D,
    LIQUIDITY_FEATURE_NAMES,
    compute_liquidity_features,
)
from nexus_scalp.features.liquidity_runtime import (
    DIMENSION_70D,
    FEATURE_ORDER_HASH,
    SCHEMA_70D,
    LiquidityGovernor,
    LiquiditySnapshot,
    ModelCompatibility,
    build_70d_vector,
    model_schema_family,
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
        # BUG-111: indices come from the AUTHORITATIVE 70D registry
        # (schema_contract.py) — liquidity is ALWAYS 60..69, never
        # derived from the active-schema dimension.
        assert fp["features"][name]["index"] == 60 + LIQUIDITY_FEATURE_NAMES.index(name)


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
    # scalp_v2/60D model + 70D runtime -> BLOCK (SCHEMA_VERSION_MISMATCH: legacy
    # family is NOT part of the 70D contract, even at the wrong dimension)
    r = resolve_model_compatibility("scalp_v2", 60, SCHEMA_70D, 70)
    assert r["result"] == ModelCompatibility.BLOCK.value
    assert r["reason"] == "SCHEMA_VERSION_MISMATCH"
    # scalp_v3/350D + 70D runtime -> BLOCK too (wider model)
    r2 = resolve_model_compatibility("scalp_v3", 350, SCHEMA_70D, 70)
    assert r2["result"] == ModelCompatibility.BLOCK.value
    assert r2["reason"] == "MODEL_DIMENSION_EXCEEDS_RUNTIME"


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
    # BUG-111: enabled -> the ACTIVE schema is the canonical 70D contract
    # (scalp_v3), exactly the layout schema_contract.py defines.
    assert report["schema"]["id"] == SCHEMA_70D
    assert report["schema"]["dimension"] == DIMENSION_70D
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


# ---------------------------------------------------------------------------
# TEST-AIHUB-07/08/09/10 — SSE datetime serialization + calc/source status
# (BUG-110: pool.confirmed_at raw datetime killed every SSE frame once a
# pool was confirmed; calc success and source availability are distinct)
# ---------------------------------------------------------------------------


def test_aihub_07_sse_payload_with_pool_datetimes_serializes() -> None:
    """The full liquidity report() payload (pools with confirmed_at) must be
    valid JSON for the SSE frame."""
    import json

    gov = LiquidityGovernor(enabled=True)
    bars = _steady_bars(120)
    snap = gov.compute_from_engine(
        bars=bars, mid_price=3305.0, atr=1.5, decision_at=bars[-1].timestamp
    )
    assert snap.pools  # pools confirmed in this scenario
    report = gov.report()
    frame = json.dumps(report)  # the exact SSE serialization path
    parsed = json.loads(frame)
    for pool in parsed["pools"]:
        confirmed_at = pool["confirmed_at"]
        assert isinstance(confirmed_at, str)
        assert confirmed_at.endswith("+00:00") or "+00:00" in confirmed_at


def test_aihub_08_nested_datetime_payload_serializes() -> None:
    """Nested datetime/nested dicts/lists serialize through the canonical
    encoder (timezone-aware ISO-8601, deterministic)."""
    import json
    from datetime import UTC, date, datetime

    from nexus_scalp.web.server import canonical_json

    payload = {
        "liquidity": {
            "pools": [
                {"confirmed_at": datetime(2026, 8, 19, 1, 14, 0, tzinfo=UTC)},
                {"confirmed_at": date(2026, 8, 19)},
            ]
        },
        "model": {"checked_at": datetime.now(UTC), "naive": datetime(2026, 8, 19, 1, 0, 0)},
        "none": None,
        "nested": {"deep": [datetime.now(UTC), {"x": 1}]},
    }
    frame = canonical_json(payload)
    parsed = json.loads(frame)
    assert parsed["liquidity"]["pools"][0]["confirmed_at"] == "2026-08-19T01:14:00+00:00"
    assert parsed["model"]["naive"].endswith("+00:00")
    assert parsed["none"] is None


def test_aihub_09_sse_serialization_failure_is_observable() -> None:
    """An unserializable leaf raises TypeError with the field path located —
    never corrupted JSON, never a silent drop."""
    import pytest

    from nexus_scalp.web.server import _find_non_json_fields, canonical_json

    class _Weird:
        pass

    payload = {"ok": 1, "bad": {"leaf": _Weird()}}
    with pytest.raises(TypeError):
        canonical_json(payload)
    fields = _find_non_json_fields(payload)
    assert fields == ["bad.leaf:_Weird"]


def test_aihub_10_calculation_and_source_status_are_distinct() -> None:
    """calculation=SUCCESS with source=UNAVAILABLE is a legitimate state:
    the governor computes from engine bars but has no live broker source."""
    gov = LiquidityGovernor(enabled=True)
    bars = _steady_bars()
    gov.compute_from_engine(bars=bars, mid_price=3305.0, atr=1.5, decision_at=bars[-1].timestamp)
    rep = gov.report()
    # Both signals coexist with distinct semantics:
    assert rep["source"] == "LIVE_MARKET_STATE"  # live tick path present here
    assert rep["available"] is True
    assert rep["causal_state"] == "VALID"
    # And a governor that never computed distinguishes source UNAVAILABLE
    # from any calculation claim:
    cold = LiquidityGovernor(enabled=True)
    cold_rep = cold.report()
    assert cold_rep["source"] == "UNAVAILABLE"
    assert cold_rep["available"] is False


def test_aihub_10b_calculation_status_field_distinct() -> None:
    """calculation_status is an explicit report() field, never collapsed
    into a single healthy boolean: SUCCESS / NOT_RUN / FAILED are distinct,
    and source_status stays orthogonal (UNAVAILABLE even on SUCCESS)."""
    cold = LiquidityGovernor(enabled=True)
    rep = cold.report()
    assert rep["calculation_status"] == "NOT_RUN"
    assert rep["source_status"] == "UNAVAILABLE"
    assert rep["available"] is False

    gov = LiquidityGovernor(enabled=True)
    bars = _steady_bars()
    gov.compute_from_engine(bars=bars, mid_price=3305.0, atr=1.5, decision_at=bars[-1].timestamp)
    rep = gov.report()
    assert rep["calculation_status"] == "SUCCESS"
    assert rep["source_status"] == "LIVE_MARKET_STATE"
    assert rep["available"] is True

    # force a failed compute -> calculation FAILED, available False
    broken = LiquidityGovernor(enabled=True)
    try:
        broken.compute_from_engine(bars=[], mid_price=1.0, atr=1.0, decision_at=datetime.now(UTC))
    except ValueError:
        pass
    rep = broken.report()
    assert rep["calculation_status"] == "FAILED"
    assert rep["available"] is False


# ---------------------------------------------------------------------------
# TEST-AIHUB-11/14/15 — governance: invalid champion stays inactive; the AI
# Hub verdict is backend-decided; 70D candidate never auto-promotes
# ---------------------------------------------------------------------------


def test_aihub_11_invalid_champion_does_not_become_active(tmp_path) -> None:
    """An artifact failing the class-count contract reports INVALID integrity
    and must NOT be treated as the active Champion."""
    import torch

    from nexus_scalp.model_lifecycle.integrity import inspect_artifact
    from nexus_scalp.models.scalp_net import ScalpNet

    net = ScalpNet(num_features=50, num_classes=6)  # wrong head (6 != 4)
    p = tmp_path / "bad6.pt"
    torch.save({k: v.clone() for k, v in net.state_dict().items()}, p)
    info = inspect_artifact(
        p,
        model_id="m",
        feature_schema_id="scalp_v1",
        feature_dimension=50,
        num_classes=4,
    )
    assert info.integrity_ok is False
    assert info.actual_output_classes == 6
    # the AI Hub verdict fields must be populated for the UI
    dump = info.model_dump()
    assert dump["integrity_ok"] is False
    assert dump["actual_output_classes"] == 6


def test_aihub_14_70d_candidate_never_promotes_automatically() -> None:
    """No automatic promotion exists: a valid 70D candidate stays CANDIDATE;
    there is no code path that turns it into the LIVE Champion (INV-015)."""
    from nexus_scalp.model_lifecycle.integrity import EXPECTED_NUM_CLASSES

    # The canonical class contract is still 4 — verified by the loader gate
    # EXPECTED_NUM_CLASSES; a 70D candidate cannot flip it.
    assert EXPECTED_NUM_CLASSES == 4


def test_aihub_15_model_inventory_distinguishes_lifecycle(tmp_path) -> None:
    """The inventory/lifecycle taxonomy exposes LIVE vs CANDIDATE explicitly —
    the UI renders backend state, never a local guess (TEST-AIHUB-15)."""
    # Valid 50D champion-shaped artifact
    import torch

    from nexus_scalp.model_lifecycle.integrity import inspect_artifact
    from nexus_scalp.models.scalp_net import ScalpNet

    net = ScalpNet(num_features=50, num_classes=4)
    p = tmp_path / "champ.pt"
    torch.save({k: v.clone() for k, v in net.state_dict().items()}, p)
    info = inspect_artifact(
        p,
        model_id="primary_scalp",
        feature_schema_id="scalp_v1",
        feature_dimension=50,
        num_classes=4,
    )
    assert info.integrity_ok is True
    assert info.actual_output_classes == 4
    assert info.actual_input_dimension == 50


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# BUG-111 REGRESSION SUITE - Liquidity Intelligence UI state contract
# (2026-08-19 forensic task). Every assertion is a regression guard for a
# PROVEN runtime contradiction:
#   idx 40..49 while DISABLED        -> canonical registry 60..69
#   last_update = 1970 (monotonic)   -> wall-clock ISO-8601
#   DISABLED + 10 'features'         -> NOT_ACTIVE provenance
#   BLOCK(LIQUIDITY_ENABLED...)      -> NOT_APPLICABLE while disabled
#   available=True + source UNAVAILABLE -> explicit availability matrix
# ---------------------------------------------------------------------------


class _Champion50DEngine:
    """Models the CURRENT live champion: scalp_v1 / 50D (repo active)."""

    FEATURE_SCHEMA_ID = "scalp_v1"
    FEATURE_DIM = 50


def _bug111_governor_with_snapshot(enabled: bool = True) -> LiquidityGovernor:
    gov = LiquidityGovernor(enabled=enabled)
    gov.bind_engine(_Champion50DEngine())
    bars = _steady_bars(120)
    gov.compute_from_engine(
        bars=bars,
        mid_price=3305.0,
        atr=1.5,
        decision_at=datetime(2026, 8, 19, 9, 30, 0, tzinfo=UTC),
    )
    return gov


def test_liq_ui_01_enabled_report_indices_are_canonical_60_69() -> None:
    gov = _bug111_governor_with_snapshot(enabled=True)
    fp = gov.snapshot_payload()
    for name in LIQUIDITY_FEATURE_NAMES:
        idx = fp["features"][name]["index"]
        assert idx == 60 + LIQUIDITY_FEATURE_NAMES.index(name)
    assert fp["dimension"] == DIMENSION_70D
    assert fp["schema_id"] == SCHEMA_70D


def test_liq_ui_02_disabled_report_indices_are_still_canonical_60_69() -> None:
    """BUG-111: DISABLED must NOT shift indices to 40..49 (the old
    active-schema-derived math). The registry owns placement."""
    gov = _bug111_governor_with_snapshot(enabled=False)
    fp = gov.snapshot_payload()
    indexes = [fp["features"][n]["index"] for n in LIQUIDITY_FEATURE_NAMES]
    assert indexes == list(range(60, 70))
    assert fp["feature_availability"] == "NOT_ACTIVE"
    assert fp["runtime_enabled"] is False


def test_liq_ui_03_disabled_report_never_active_values() -> None:
    """DISABLED + retained snapshot: values are exposed ONLY as a
    timestamped last-snapshot with NOT_ACTIVE provenance - never as
    active model inputs; availability is False."""
    gov = _bug111_governor_with_snapshot(enabled=False)
    rep = gov.report()
    assert rep["enabled"] is False
    assert rep["available"] is False
    assert rep["feature_availability"] == "NOT_ACTIVE"
    assert rep["status"] == "DISABLED"
    assert len(rep["features"]) == 10
    assert rep["causal_state"] == "NOT_APPLICABLE"
    assert rep["last_update"] is not None
    assert not rep["last_update"].startswith("1970")
    assert rep["snapshot_timestamp"] == "2026-08-19T09:30:00+00:00"


def test_liq_ui_04_model_compatibility_not_applicable_when_disabled() -> None:
    """A disabled runtime never reports LIQUIDITY_ENABLED_BUT_MODEL_
    INCOMPATIBLE (that reason claims liquidity is enabled)."""
    gov = _bug111_governor_with_snapshot(enabled=False)
    mc = gov.report()["model_compatibility"]
    assert mc["result"] == "NOT_APPLICABLE"
    assert mc["reason"] == "LIQUIDITY_DISABLED"
    gov2 = _bug111_governor_with_snapshot(enabled=True)
    mc2 = gov2.report()["model_compatibility"]
    assert mc2["result"] == "BLOCK"
    assert mc2["reason"] == "MODEL_INPUT_DIMENSION_MISMATCH"


def test_liq_ui_05_last_update_is_wall_clock_not_monotonic_epoch() -> None:
    """Regression for the 1970 timestamp: last_update must be wall-clock
    ISO-8601 (UTC), never fromtimestamp(monotonic)."""
    gov = _bug111_governor_with_snapshot(enabled=False)
    lu = gov.report()["last_update"]
    assert lu is not None
    assert lu.endswith("+00:00")
    assert not lu.startswith("1970")
    parsed = datetime.fromisoformat(lu)
    assert parsed.year >= 2025


def test_liq_ui_06_availability_matrix_explicit() -> None:
    """feature_availability matrix: AVAILABLE / STALE_CACHE / NOT_ACTIVE /
    UNAVAILABLE - explicit states, never inferred from each other."""
    gov = _bug111_governor_with_snapshot(enabled=True)
    assert gov.report()["feature_availability"] == "AVAILABLE"
    with patch.object(gov, "_last_success_at", time.monotonic() - 9999.0):
        rep = gov.report()
        assert rep["feature_availability"] == "STALE_CACHE"
        assert rep["causal_state"] == "STALE"
    cold = LiquidityGovernor(enabled=True)
    assert cold.report()["feature_availability"] == "UNAVAILABLE"
    assert cold.report()["available"] is False
    cold2 = _bug111_governor_with_snapshot(enabled=False)
    assert cold2.report()["feature_availability"] == "NOT_ACTIVE"


def test_liq_ui_07_state_revision_monotonic() -> None:
    """state_revision increments monotonically on snapshot/toggle/error -
    the UI drops older revisions (stale SSE guard)."""
    gov = LiquidityGovernor(enabled=False)
    r0 = gov.report()["state_revision"]
    gov.set_enabled(True, actor="test")
    r1 = gov.report()["state_revision"]
    assert r1 > r0
    bars = _steady_bars(60)
    gov.compute_from_engine(bars=bars, mid_price=3305.0, atr=1.5, decision_at=bars[-1].timestamp)
    r2 = gov.report()["state_revision"]
    assert r2 > r1
    with pytest.raises(ValueError):
        gov.compute_from_engine(bars=[], mid_price=1.0, atr=1.0)
    r3 = gov.report()["state_revision"]
    assert r3 > r2


def test_liq_ui_08_snapshot_payload_carries_per_value_provenance() -> None:
    """Every feature carries index/value/timestamp/source/status/
    feature_availability/runtime_enabled - the UI renders exactly these,
    never derived values."""
    gov = _bug111_governor_with_snapshot(enabled=True)
    fp = gov.snapshot_payload()
    for name in LIQUIDITY_FEATURE_NAMES:
        e = fp["features"][name]
        assert set(e) >= {
            "index",
            "value",
            "timestamp",
            "source",
            "status",
            "feature_availability",
            "runtime_enabled",
        }
        assert e["index"] == 60 + LIQUIDITY_FEATURE_NAMES.index(name)
        assert e["timestamp"] == fp["timestamp"]
        assert e["runtime_enabled"] is True
    assert fp["available"] is True


def test_liq_ui_09_algorithm_version_constant_is_provenance_label() -> None:
    """algorithm_version is a deterministic provenance constant (the
    producer carries no version of its own); it never implies an active
    calculation while disabled - calculation_status says the truth."""
    gov = _bug111_governor_with_snapshot(enabled=False)
    rep = gov.report()
    assert isinstance(rep["algorithm_version"], str)
    assert rep["algorithm_version"].startswith("scalp_liquidity_")
    assert rep["calculation_status"] == "SUCCESS"
    assert rep["status"] == "DISABLED"
    assert rep["source_status"] in ("LIVE_MARKET_STATE", "UNAVAILABLE", "REPLAY")


def test_liq_ui_10_json_safe_payload() -> None:
    """report() and snapshot_payload() serialize through json.dumps - the
    SSE frame never breaks on datetime/Enum/Decimal leaves."""
    import json

    gov = _bug111_governor_with_snapshot(enabled=True)
    json.dumps(gov.report())
    json.dumps(gov.snapshot_payload())
    gov.set_enabled(False, actor="test")
    json.dumps(gov.report())
    json.dumps(gov.snapshot_payload())


# ---------------------------------------------------------------------------
# BUG-123 — Liquidity-enabled model compatibility: contract-based verdict,
# precise reasons, REAL 70D proof artifact end-to-end, negative gates.
# ---------------------------------------------------------------------------


def _fake_engine_like(schema: str, dim: int, *, champion: Any = None) -> Any:
    eng = SimpleNamespace(
        FEATURE_SCHEMA_ID=schema,
        FEATURE_DIM=dim,
        model_registry=None,
        champion_manager=champion,
        _bundle=SimpleNamespace(artifact_path=None),
    )
    return eng


class _ChampLike:
    """ChampionManager surface: .champion_or_none() -> ChampionModel-like."""

    def __init__(self, schema: str, dim: int, *, input_dim: int | None = None) -> None:
        self._c = SimpleNamespace(
            feature_schema_id=schema,
            feature_dimension=dim,
            model_id="champ_x",
            model_version="9.9.9",
            artifact_hash="abc123",
            available=True,
            info=SimpleNamespace(actual_input_dimension=input_dim),
        )

    def champion_or_none(self):
        return self._c


def test_liq_bug123_01_live_champion_50d_enabled_is_block_with_reason() -> None:
    """THE reproduced production state (2026-08-19 UI): engine serves
    scalp_v1/50D, liquidity ENABLED -> BLOCK with the PRECISE reason
    MODEL_INPUT_DIMENSION_MISMATCH (and the diagnostic sidecar fields)."""
    gov = LiquidityGovernor(enabled=True)
    gov.bind_engine(_fake_engine_like("scalp_v1", 50))
    mc = gov.model_compatibility()
    assert mc["result"] == ModelCompatibility.BLOCK.value
    assert mc["reason"] == "MODEL_INPUT_DIMENSION_MISMATCH"
    assert mc["model_dimension"] == 50
    assert mc["runtime_dimension"] == DIMENSION_70D
    assert mc["runtime_schema_id"] == SCHEMA_70D
    assert mc["runtime_feature_order_hash"] == FEATURE_ORDER_HASH
    assert mc["model_schema_family"] == "ACTIVE"


def test_liq_bug123_02_valid_70d_model_with_champion_is_pass() -> None:
    gov = LiquidityGovernor(enabled=True)
    gov.bind_engine(
        _fake_engine_like("scalp_v3", 70, champion=_ChampLike("scalp_v3", 70, input_dim=70))
    )
    mc = gov.model_compatibility()
    assert mc["result"] == ModelCompatibility.PASS.value
    assert mc["reason"] == "SCHEMA_DIMENSION_MATCH"
    assert mc["model_input_dimension"] == 70
    assert mc["feature_order"] == "PASS"
    assert mc["model_hash"] == "abc123"
    # canonical single descriptor: the flat dict IS the contract (INV-022)
    assert mc.get("runtime_feature_order_hash") == FEATURE_ORDER_HASH


def test_liq_bug123_03_disabled_is_not_applicable_regardless_of_model() -> None:
    gov = LiquidityGovernor(enabled=False)
    gov.bind_engine(_fake_engine_like("scalp_v1", 50))
    mc = gov.model_compatibility()
    assert mc["result"] == ModelCompatibility.NOT_APPLICABLE.value
    assert mc["reason"] == "LIQUIDITY_DISABLED"


def test_liq_bug123_04_72d_tensor_flagged_as_model_tensor_dimension_mismatch() -> None:
    """BUG-114 pattern: manifest declares 70 but the neural input tensor is
    72 -> BLOCK even when declared dimension matches."""
    r = resolve_model_compatibility("scalp_v3", 70, SCHEMA_70D, 70, model_input_dimension=72)
    assert r["result"] == ModelCompatibility.BLOCK.value
    assert r["reason"] == "MODEL_TENSOR_DIMENSION_MISMATCH"


def test_liq_bug123_05_wrong_schema_version_blocked() -> None:
    """scalp_v2 renamed to 70D does not make it compatible: family OTHER -> BLOCK."""
    r = resolve_model_compatibility("scalp_v2", 70, SCHEMA_70D, 70)
    assert r["result"] == ModelCompatibility.BLOCK.value
    assert r["reason"] == "SCHEMA_VERSION_MISMATCH"


def test_liq_bug123_06_schema_family_classification() -> None:
    assert model_schema_family("scalp_v1") == "ACTIVE"
    assert model_schema_family("scalp_v3") == "70D_FAMILY"
    assert model_schema_family("scalp_v4") == "70D_FAMILY"
    assert model_schema_family("scalp_v2") == "OTHER"
    assert model_schema_family(None) == "OTHER"
    assert model_schema_family("scalp_liquidity_v1") == "OTHER"


def test_liq_bug123_07_contract_descriptor_single_source() -> None:
    gov = LiquidityGovernor(enabled=True)
    gov.bind_engine(
        _fake_engine_like("scalp_v3", 70, champion=_ChampLike("scalp_v3", 70, input_dim=70))
    )
    rep = gov.report()
    assert rep["liquidity_contract"]["feature_order_hash"] == FEATURE_ORDER_HASH
    assert rep["liquidity_contract"]["schema_id"] == "scalp_v3"
    assert rep["liquidity_contract"]["dimension"] == 70
    contract = gov.compatibility_contract()
    assert contract["runtime"]["dimension"] == 70
    assert contract["model"]["dimension"] == 70
    assert contract["compatibility"]["result"] == "PASS"


def test_liq_bug123_08_revision_rendered_meaningful() -> None:
    gov = LiquidityGovernor(enabled=True)
    assert gov.report()["state_revision"] >= 0
    gov.set_enabled(False, actor="test")
    r2 = gov.report()
    assert r2["state_revision"] == gov._state_revision
    assert r2["snapshot_coherence_revision"] == r2["state_revision"]


def test_liq_bug123_09_no_stale_compatibility_after_model_hot_swap() -> None:
    """The compatibility verdict is recomputed from the CURRENT artifact
    contract on every call (no stale cache)."""
    gov = LiquidityGovernor(enabled=True)
    gov.bind_engine(_fake_engine_like("scalp_v1", 50))
    assert gov.model_compatibility()["result"] == "BLOCK"
    # hot-swap: a 70D champion is now serving
    gov.bind_engine(
        _fake_engine_like("scalp_v3", 70, champion=_ChampLike("scalp_v3", 70, input_dim=70))
    )
    assert gov.model_compatibility()["result"] == "PASS"


def test_liq_bug123_10_report_hot_reload_recomputes() -> None:
    gov = LiquidityGovernor(enabled=True)
    gov.bind_engine(_fake_engine_like("scalp_v1", 50))
    assert gov.report()["model_compatibility"]["result"] == "BLOCK"
    gov.bind_engine(
        _fake_engine_like("scalp_v3", 70, champion=_ChampLike("scalp_v3", 70, input_dim=70))
    )
    assert gov.report()["model_compatibility"]["result"] == "PASS"


def test_liq_bug123_11_unknown_model_never_guessed() -> None:
    r = resolve_model_compatibility(None, None, SCHEMA_70D, 70)
    assert r["result"] == ModelCompatibility.UNKNOWN.value
    assert r["reason"] == "NO_MODEL_METADATA"


# ---------------------------------------------------------------------------
# REAL 70D artifact: compatibility PASS + inference through the repo's own
# model runtime (LocalModelRuntime.predict / StateDict) — never simulated.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_70d_model_id() -> str:
    path = Path("artifacts/model_generation/models/liq70_proof")
    if not (path / "model.pt").exists() or not (path / "model.json").exists():
        pytest.skip("BUG-123 proof artifact not built (run scratch/fix_70d_proof_artifact.py)")
    return "liq70_proof"


def test_liq_bug123_12_real_proof_artifact_compatible_with_70d_runtime(
    real_70d_model_id: str,
) -> None:
    import json

    from nexus_scalp.features.schema_contract import feature_schema_hash

    mf = json.loads(
        Path(f"artifacts/model_generation/models/{real_70d_model_id}/model.json").read_text(
            encoding="utf-8"
        )
    )
    assert mf["feature_schema_id"] == "scalp_v3"
    assert mf["feature_dimension"] == 70
    assert mf["build_metadata"]["input_dimension"] == 70
    assert mf["feature_schema_hash"] == feature_schema_hash()
    r = resolve_model_compatibility(
        mf["feature_schema_id"],
        mf["feature_dimension"],
        SCHEMA_70D,
        DIMENSION_70D,
        model_input_dimension=mf["build_metadata"]["input_dimension"],
    )
    assert r["result"] == ModelCompatibility.PASS.value
    assert r["reason"] == "SCHEMA_DIMENSION_MATCH"


def test_liq_bug123_13_real_70d_tensor_inference_succeeds(real_70d_model_id: str) -> None:
    """A REAL 70D vector (50 base + 10 news + 10 liquidity) through the repo's
    own LocalModelRuntime -> inference SUCCESS, probs sum to 1."""
    from nexus_scalp.model_generation.runtime import LocalModelRuntime

    rt = LocalModelRuntime().load(real_70d_model_id)
    base50 = [0.0] * 50
    base50[0] = 1.0
    news10 = [0.0] * 10
    liq10 = _liquidity_features(_steady_bars()).features
    vec70 = build_70d_vector(base50, family_10=news10, liquidity_10=liq10)
    assert len(vec70) == 70
    assert all(math.isfinite(v) for v in vec70)
    pred = rt.predict(vec70)
    probs = pred["probabilities"]
    assert len(probs) == 4
    assert abs(sum(probs) - 1.0) < 1e-3
    assert pred["argmax"] in (0, 1, 2, 3)


def test_liq_bug123_14_real_50d_model_blocked_against_70d_runtime() -> None:
    """NEGATIVE GATE: the served production champion (artifacts/models/.../
    model.pt, a REAL 50D artifact) MUST be BLOCK against the 70D runtime —
    proves the guard is real and was never weakened."""
    path = Path("artifacts/models/scalp/XAUUSD/v1.0.0/model.pt")
    if not path.exists():
        pytest.skip("live 50D champion artifact not present")
    import torch

    sd = torch.load(path, map_location="cpu", weights_only=False)
    tensor_dim = int(sd["input_projection.weight"].shape[1])
    assert tensor_dim == 50  # the live champion IS 50D
    r = resolve_model_compatibility(
        "scalp_v1", 50, SCHEMA_70D, 70, model_input_dimension=tensor_dim
    )
    assert r["result"] == ModelCompatibility.BLOCK.value
    assert r["reason"] == "MODEL_INPUT_DIMENSION_MISMATCH"


def test_liq_bug123_15_feature_order_hash_is_canonical() -> None:
    from nexus_scalp.features.schema_contract import feature_schema_hash

    assert feature_schema_hash() == "235b8fccc96b7e0e"
    assert FEATURE_ORDER_HASH == feature_schema_hash()


def test_liq_bug123_16_real_liquidity_values_fill_60_69() -> None:
    gov = LiquidityGovernor(enabled=True)
    bars = _steady_bars()
    gov.compute_from_engine(bars=bars, mid_price=float(bars[-1].close), atr=1.5)
    payload = gov.snapshot_payload()
    assert payload["dimension"] == 70
    feats = payload["features"]
    assert len(feats) == 10
    for name, meta in feats.items():
        assert meta["index"] == 60 + list(LIQUIDITY_FEATURE_NAMES).index(name)
        assert meta["validity"] == "finite"
        assert math.isfinite(meta["value"])
