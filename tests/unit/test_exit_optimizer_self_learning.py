"""Self-learning exit optimizer: the ledger must change the published geometry.

Every test drives the real optimizer against a synthetic ledger whose correct
answer is known by construction, and asserts the SIGN of the decision — a
learner that always returns the default is indistinguishable from a broken one.
"""

from __future__ import annotations

import json

import pytest

from nexus_scalp.experience.exit_optimizer import (
    DEFAULT_GRID,
    ExitObservation,
    choose_trigger,
    counterfactual_r,
    estimate_dip_fraction,
    expected_r,
    negative_rate_at,
    normalize_regime,
    observations_from_records,
    optimize,
    write_profile,
)


def round_trip_curve_ledger() -> list[ExitObservation]:
    """Round-trips below 1.5R, clean runs above it — the floor belongs at 1.5R."""
    rows: list[ExitObservation] = []
    # 30 trades that reach ~1.0R and then reverse to a full loss.
    rows += [ExitObservation("TREND", mfe_r=1.0, final_r=-1.0) for _ in range(30)]
    # 30 trades that reach ~2.0R and keep most of it.
    rows += [ExitObservation("TREND", mfe_r=2.0, final_r=1.8) for _ in range(30)]
    return rows


def hopeless_ledger() -> list[ExitObservation]:
    """Every level round-trips: no floor should be invented from this book."""
    return [ExitObservation("RANGE", mfe_r=2.4, final_r=-1.0) for _ in range(40)]


def test_the_floor_r_is_learned_from_the_round_trip_curve():
    result = optimize(round_trip_curve_ledger(), min_samples=20)
    rec = result.recommendations["TREND"]
    assert rec.accepted, rec.reason
    # The round-trippers peak at 1.0R, so every bucket at/above 1.2R is clean:
    # the learned floor is the FIRST grid level above their MFE.
    assert rec.trigger_r == pytest.approx(1.2)
    assert "closed negative" in rec.reason


def test_a_book_that_round_trips_everywhere_keeps_the_baseline():
    result = optimize(hopeless_ledger(), min_samples=20)
    rec = result.recommendations["RANGE"]
    assert rec.accepted is False
    assert rec.trigger_r == pytest.approx(1.0)
    assert "baseline kept" in rec.reason


def test_a_thin_regime_is_not_learned_from():
    result = optimize(round_trip_curve_ledger()[:5], min_samples=30)
    rec = result.recommendations["TREND"]
    assert rec.accepted is False
    assert rec.trigger_r == pytest.approx(1.0)
    assert "insufficient sample" in rec.reason


def test_the_learned_floor_is_bounded_to_a_safe_envelope():
    """A ledger that would justify 3.0R is clamped into the operator envelope."""
    rows = [ExitObservation("TREND", mfe_r=1.0, final_r=-1.0) for _ in range(30)]
    rows += [ExitObservation("TREND", mfe_r=4.0, final_r=3.9) for _ in range(30)]
    trigger, _why = choose_trigger(rows, max_trigger=2.5)
    assert trigger <= 2.5


def test_negative_rate_at_measures_only_trades_that_reached_the_level():
    rows = [
        ExitObservation("TREND", 0.5, -1.0),
        ExitObservation("TREND", 1.4, -1.0),
        ExitObservation("TREND", 1.6, 1.5),
    ]
    reached, rate = negative_rate_at(rows, 1.2)
    assert reached == 2
    assert rate == pytest.approx(0.5)
    assert negative_rate_at(rows, 5.0) == (0, 0.0)


def test_counterfactual_recovers_a_round_trip_and_charges_optionality():
    round_tripper = ExitObservation("TREND", mfe_r=2.0, final_r=-1.0)
    assert counterfactual_r(round_tripper, 1.2, 0.05, dip_fraction=0.25) == pytest.approx(0.05)

    runner = ExitObservation("TREND", mfe_r=3.0, final_r=2.0)
    assert counterfactual_r(runner, 1.2, 0.05, dip_fraction=0.25) == pytest.approx(
        2.0 - 0.25 * (2.0 - 0.05)
    )

    untouched = ExitObservation("TREND", mfe_r=0.5, final_r=-1.0)
    assert counterfactual_r(untouched, 1.2, 0.05) == -1.0


def test_expected_r_is_a_mean_not_a_sum():
    rows = [ExitObservation("TREND", 1.0, 1.0), ExitObservation("TREND", 1.0, -1.0)]
    assert expected_r(rows, 5.0, 0.05) == pytest.approx(0.0)


def test_dip_fraction_is_estimated_from_the_ledger_and_clamped():
    givers = [ExitObservation("TREND", mfe_r=2.0, final_r=1.0) for _ in range(10)]
    assert estimate_dip_fraction(givers) == pytest.approx(0.60)
    proud = [ExitObservation("TREND", mfe_r=2.0, final_r=1.99) for _ in range(10)]
    assert estimate_dip_fraction(proud) == pytest.approx(0.05)
    assert estimate_dip_fraction([]) == pytest.approx(0.25)


def test_regime_buckets_collapse_onto_the_runtime_vocabulary():
    assert normalize_regime("volatility_expansion") == "TREND"
    assert normalize_regime("Range") == "RANGE"
    assert normalize_regime("") == "DEFAULT"


def test_records_adapter_skips_open_and_invalid_rows():
    class Ctx:
        regime = "TREND"

    class Behavior:
        mfe_r = 1.4

    class Rec:
        context = Ctx()
        behavior = Behavior()
        realized_r_multiple = 0.8
        is_closed = True

    class OpenRec(Rec):
        is_closed = False

    rows = observations_from_records(
        [Rec(), OpenRec(), {"regime": "range", "mfe_r": 0.2, "final_r": -1.0, "is_closed": True}]
    )
    assert [r.regime for r in rows] == ["TREND", "RANGE"]


def test_profile_is_published_atomically_and_reads_back(tmp_path):
    result = optimize(round_trip_curve_ledger(), min_samples=20)
    path = write_profile(tmp_path / "exit_policy.json", result)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 3
    assert payload["grid"] == list(DEFAULT_GRID)
    assert payload["regimes"]["TREND"]["trigger_r"] == result.recommendations["TREND"].trigger_r
    assert not list(tmp_path.glob("*.tmp"))


def test_optimization_is_deterministic_for_a_given_ledger():
    first = optimize(round_trip_curve_ledger(), min_samples=20)
    second = optimize(round_trip_curve_ledger(), min_samples=20)
    assert first.recommendations["TREND"].as_dict() == second.recommendations["TREND"].as_dict()
