"""Regression pins for hunter metadata persistence (sample_pipeline seam).

Pins three audited defects at the sample_maker / strategy_factory boundary:

1. reasons round-trip: attach_hunter_metadata() must ALWAYS store reasons as
   a tuple of strings (json.dumps -> json.loads keeps it a LIST of strings on
   the reader side; a non-iterable reasons value must never leak in).
2. hunter-disabled path: with an empty/no-op hunter dict (SampleFactory
   hunter_enabled=False -> self.hunter=None -> hunter_meta={}), metadata
   defaults must be NO_GO-safe, not crash, and must JSON-serialize.
3. GO direction must never be FABRICATED: a GO decision for a setup whose
   factors lack a real direction (+1/-1) persists direction=None, never a
   silent fabricated "SELL" (the previous behavior — factors.get("direction",
   0) > 0 ? BUY : SELL — stamped SELL onto directionless rows, poisoning the
   direction column of training data).
"""

from nexus_scalp.model_generation.sample_maker import attach_hunter_metadata
from nexus_scalp.model_generation.setup_detector import SetupDetection
from nexus_scalp.model_generation.strategy_factory import (
    HunterStrategy,
    StrategyFactory,
)

ROW = {"atr_m1": 1.0, "spread": 0.02, "regime": "TRENDING"}


def _strat(**kw) -> HunterStrategy:
    base = dict(strategy_id="t_v1", setup_types=("FVG",))
    base.update(kw)
    return HunterStrategy(**base)


# ---------------------------------------------------------------------------
# Q1 — reasons serialization round-trip
# ---------------------------------------------------------------------------


def test_reasons_stored_as_tuple_of_strings() -> None:
    hunter = {"decision": "GO", "reasons": ("HUNTER_QUALIFIED",)}
    m = attach_hunter_metadata({"metadata": {}}, hunter)
    assert isinstance(m["entry_reasons"], tuple)
    assert m["entry_reasons"] == ("HUNTER_QUALIFIED",)


def test_reasons_list_input_normalized_to_tuple() -> None:
    hunter = {"decision": "NO_GO", "reasons": ["A", "B"]}
    m = attach_hunter_metadata({"metadata": {}}, hunter)
    assert m["entry_reasons"] == ("A", "B")
    assert isinstance(m["entry_reasons"], tuple)


def test_reasons_missing_defaults_empty_tuple() -> None:
    m = attach_hunter_metadata({"metadata": {}}, {})
    assert m["entry_reasons"] == ()


def test_reasons_none_value_does_not_crash() -> None:
    m = attach_hunter_metadata({"metadata": {}}, {"decision": "NO_GO", "reasons": None})
    assert m["entry_reasons"] == ()


def test_json_round_trip_preserves_reason_strings() -> None:
    import json

    hunter = {"decision": "GO", "reasons": ("HUNTER_QUALIFIED",), "direction": "BUY"}
    m = attach_hunter_metadata({"metadata": {}}, hunter)
    rt = json.loads(json.dumps(m))
    assert list(rt["entry_reasons"]) == ["HUNTER_QUALIFIED"]  # list of strings survives


# ---------------------------------------------------------------------------
# Q2 — hunter-disabled path must produce NO_GO defaults, not crash
# ---------------------------------------------------------------------------


def test_disabled_hunter_defaults_are_no_go_safe() -> None:
    m = attach_hunter_metadata({"metadata": {}}, {})
    assert m["entry_decision"] == "NO_GO"
    assert m["setup_tier"] == "NO_TRADE"
    assert m["setup_quality"] == 0.0
    assert m["hunter_strategy_id"] == ""
    assert m["direction"] is None


def test_disabled_hunter_metadata_json_serializes() -> None:
    import json

    m = attach_hunter_metadata({"metadata": {}}, {})
    assert json.loads(json.dumps(m))["entry_decision"] == "NO_GO"


# ---------------------------------------------------------------------------
# Q3 — direction must never be fabricated on GO rows
# ---------------------------------------------------------------------------


def test_go_without_direction_factor_persists_none_not_sell() -> None:
    sf = StrategyFactory(strategies={"t_v1": _strat(direction_alignment=False)})
    setup = SetupDetection(setup_id="s1", setup_type="FVG", quality=0.9, factors={"fvg_sig": 0.9})
    dec = sf.evaluate(setup, ROW, None)
    assert dec.decision == "GO"
    assert dec.direction is None  # was "SELL" (fabricated) before the fix


def test_go_with_positive_direction_persists_buy() -> None:
    sf = StrategyFactory(strategies={"t_v1": _strat(direction_alignment=False)})
    setup = SetupDetection(setup_id="s1", setup_type="FVG", quality=0.9, factors={"direction": 1.0})
    dec = sf.evaluate(setup, ROW, None)
    assert dec.decision == "GO"
    assert dec.direction == "BUY"


def test_go_with_negative_direction_persists_sell() -> None:
    sf = StrategyFactory(strategies={"t_v1": _strat(direction_alignment=False)})
    setup = SetupDetection(
        setup_id="s1", setup_type="FVG", quality=0.9, factors={"direction": -1.0}
    )
    dec = sf.evaluate(setup, ROW, None)
    assert dec.decision == "GO"
    assert dec.direction == "SELL"


def test_no_go_reasons_preserved_with_missing_direction() -> None:
    sf = StrategyFactory()  # default strategies: alignment ON -> NO_GO path
    setup = SetupDetection(setup_id="s1", setup_type="FVG", quality=0.9, factors={"fvg_sig": 0.9})
    dec = sf.evaluate(setup, ROW, None)
    assert dec.decision == "NO_GO"
    assert "NO_DIRECTION_ALIGNMENT" in dec.reasons
    assert dec.direction is None  # no fabricated SELL on the NO_GO either


def test_detector_signatures_still_map_to_directions() -> None:
    """Real pipeline path: a directionful setup keeps GO + correct direction."""
    from nexus_scalp.model_generation.sample_maker import HunterSampleMaker

    maker = HunterSampleMaker()
    res = maker.analyze_row(
        {
            "fvg_sig": 0.9,
            "close_location_value": 0.8,
            "htf_h4_trend": 1.0,
            "atr_m1": 1.0,
            "spread": 0.02,
            "regime": "TRENDING",
        },
        "2026-09-03T10:00:00+00:00",
    )
    assert res["decision"] == "GO"
    assert res["direction"] == "BUY"
    assert res["reasons"] == ("HUNTER_QUALIFIED",)
