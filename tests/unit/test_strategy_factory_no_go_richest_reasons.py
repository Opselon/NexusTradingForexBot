"""Regression tests: strategy_factory.evaluate() NO_GO must report the RICHEST
rejection (most reasons, tie-broken by lowest strategy_id), never the first
candidate in dict iteration order.

Defect (BUG-231): on the all-NO_GO path evaluate() returned candidates[0].
candidates are collected in self.strategies dict order filtered by
setup-type compatibility, so which rejection got reported depended on
registry insertion order rather than informativeness. For a setup type with
multiple compatible strategies (LIQUIDITY_SWEEP -> hunter_sweep_v1,
hunter_london_v1, hunter_smc_v1), the reported reasons could be a thin
SPREAD_TOO_WIDE-only rejection from hunter_sweep_v1 while the real, fuller
blocker (SPREAD_TOO_WIDE + REGIME_NOT_OK + SESSION_GATE) sat in a later
candidate.
"""

from __future__ import annotations

from nexus_scalp.model_generation.setup_detector import SetupDetection
from nexus_scalp.model_generation.strategy_factory import (
    HUNTER_STRATEGIES,
    StrategyFactory,
)

# LIQUIDITY_SWEEP is compatible with three strategies, in registry order:
# hunter_sweep_v1, hunter_london_v1 (session-gated), hunter_smc_v1.
COMPAT = [sid for sid, strat in HUNTER_STRATEGIES.items() if "LIQUIDITY_SWEEP" in strat.setup_types]


def _setup(quality: float = 0.95) -> SetupDetection:
    return SetupDetection(
        setup_id="s1",
        setup_type="LIQUIDITY_SWEEP",
        quality=quality,
        factors={"direction": 1},
    )


class TestNoGoReportsRichestReasons:
    def test_liquidity_sweep_has_three_compatible_strategies(self) -> None:
        """Precondition for the multi-candidate NO_GO scenarios below."""
        assert COMPAT == [
            "hunter_sweep_v1",
            "hunter_london_v1",
            "hunter_smc_v1",
        ]

    def test_richest_no_go_wins_over_first_candidate(self) -> None:
        """Wide-spread off-session row: hunter_sweep_v1 (the FIRST candidate
        in registry order) rejects with 2 reasons; hunter_london_v1 rejects
        with the same 2 PLUS SESSION_GATE. The 3-reason rejection must be
        reported, not candidates[0]."""
        row = {
            "atr_m1": 1.0,
            "spread": 0.9,  # wide vs every max_spread_atr (0.30-0.32)
            "regime": "CHOP",  # outside every regime_ok
            "session_london": 0.0,  # london gate closed
        }
        dec = StrategyFactory().evaluate(_setup(), row, None)
        assert dec.decision == "NO_GO"
        assert dec.strategy_id == "hunter_london_v1"
        reasons = set(dec.reasons)
        assert "SESSION_GATE(LONDON)" in reasons
        assert "REGIME_NOT_OK(CHOP)" in reasons
        assert any(r.startswith("SPREAD_TOO_WIDE") for r in reasons)
        assert len(reasons) == 3

    def test_tie_on_reason_count_breaks_by_lowest_strategy_id(self) -> None:
        """Candidates tied on the MINIMAL reason count -> the reported
        strategy_id must be the lexicographically lowest among those
        minimal-reason candidates, independent of registry insertion order.

        Scenario: clean in-session trend row, quality below every floor ->
        all three candidates reject with exactly QUALITY_BELOW_FLOOR (a true
        3-way tie) -> report the lexicographically lowest strategy_id."""
        row = {
            "atr_m1": 1.0,
            "spread": 0.02,
            "regime": "TRENDING",
            "session_london": 1.0,  # gate OPEN: london_v1 loses its extra reason
        }
        setup = _setup(quality=0.50)
        factory = StrategyFactory()
        dec = factory.evaluate(setup, row, None)
        assert dec.decision == "NO_GO"
        n_reasons = {
            sid: len(factory._evaluate_one(strat, setup, row).reasons)
            for sid, strat in HUNTER_STRATEGIES.items()
            if "LIQUIDITY_SWEEP" in strat.setup_types
        }
        assert set(n_reasons.values()) == {1}, n_reasons  # true 3-way tie
        assert dec.strategy_id == min(n_reasons)  # 'hunter_london_v1'
        # Legacy code returned candidates[0] = hunter_sweep_v1 (dict order);
        # the tie-break must not depend on that order.
        assert dec.strategy_id != "hunter_sweep_v1"

    def test_no_go_selection_is_dict_order_independent(self) -> None:
        """Reversing the registry insertion order must NOT change which
        candidate (strategy_id + reasons) the NO_GO path reports."""
        row = {
            "atr_m1": 1.0,
            "spread": 0.9,
            "regime": "CHOP",
            "session_london": 0.0,
        }
        setup = _setup()
        fwd = StrategyFactory().evaluate(setup, row, None)
        rev = StrategyFactory(dict(reversed(list(HUNTER_STRATEGIES.items())))).evaluate(
            setup, row, None
        )
        assert fwd.decision == rev.decision == "NO_GO"
        assert fwd.strategy_id == rev.strategy_id
        assert fwd.reasons == rev.reasons

    def test_thin_first_candidate_not_reported_when_richer_exists(self) -> None:
        """The motivating thin-vs-rich shape: first candidate (sweep_v1)
        rejects thin on spread+regime; london_v1 adds the session gate. The
        richer (later-registered) rejection wins."""
        row = {
            "atr_m1": 1.0,
            "spread": 0.9,
            "regime": "CHOP",
            "session_london": 0.0,
        }
        dec = StrategyFactory().evaluate(_setup(), row, None)
        assert len(dec.reasons) == 3
        assert dec.strategy_id == "hunter_london_v1"

    def test_go_selection_unchanged(self) -> None:
        """The fix touches only the NO_GO reporting path: a clean in-session
        row still returns a GO decision (highest-RR selection unchanged)."""
        row = {
            "atr_m1": 0.5,
            "spread": 0.02,
            "regime": "TRENDING",
            "session_london": 1.0,
        }
        dec = StrategyFactory().evaluate(_setup(), row, None)
        assert dec.decision == "GO"
        assert dec.reasons == ("HUNTER_QUALIFIED",)

    def test_no_compatible_strategy_path_unchanged(self) -> None:
        """An unknown strategy_id filter keeps the legacy NO_COMPATIBLE
        contract: caller's strategy_id echoed back, singleton reason."""
        setup = SetupDetection(
            setup_id="s2",
            setup_type="RANGING_FADE",
            quality=0.9,
            factors={"direction": -1},
        )
        row = {"atr_m1": 0.5, "spread": 0.02, "regime": "RANGING"}
        dec = StrategyFactory().evaluate(setup, row, "nope_vX")
        assert dec.decision == "NO_GO"
        assert dec.strategy_id == "nope_vX"
        assert dec.reasons == ("NO_COMPATIBLE_STRATEGY",)
