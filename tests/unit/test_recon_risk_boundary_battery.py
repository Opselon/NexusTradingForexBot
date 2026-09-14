"""RECON CRITICAL GATE — Risk math boundaries (mutation-validated invariants).

Radical test reconstruction 2026-09-14. Every test here was written because a
verified grep found NO existing protection for a realistic capital-protection
defect (forensic lanes: risk engine deep audit). Each test names the defect it
catches; the anti-mutation battery scripts/testing/mutation_check.py proves
they fail when the production guard is broken.

Protected defects:
  R1  A deleted NaN/inf risk_pct guard ships a NaN lot size to the broker.
  R2  A margin_free<=0 account (legal at the Pydantic layer) sizing a real lot.
  R3  eps removal in _floor_to_step systematically under-sizes by one whole
      step at float boundaries (0.58/0.01 == 57.99999999999999).
  R4  An `<` -> `<=` flip at the equity-tier boundaries silently doubles or
      floors the tier cap for accounts sitting exactly on 100/1000/10000.
  R5  The dispatch-time fallback clamp (account=None — live whenever
      get_account_info() raises) losing HARD_MAX_LOTS parity.
  R6  Margin-clamp default-fraction drift: the bare RiskEngine(RiskConfig())
      default (10%) and the 20% hard ceiling, incl. bool-truthiness leak.

Offline, deterministic: no MT5, no network, no model artifacts, no wall clock.
"""

from __future__ import annotations

import math

import pytest

from nexus_scalp.configuration.config import RiskConfig
from nexus_scalp.domain.models import AccountInfo, SymbolInfo
from nexus_scalp.risk.risk_engine import RiskEngine

# ---------------------------------------------------------------------------
# Fixtures (canonical XAUUSD broker geometry; same shapes as the a15 battery)
# ---------------------------------------------------------------------------


def _account(equity: float, margin_free: float) -> AccountInfo:
    return AccountInfo(
        login=1,
        trade_mode=0,
        leverage=100,
        balance=equity,
        equity=equity,
        margin=0.0,
        margin_free=margin_free,
        currency="USD",
    )


def _symbol(volume_step: float = 0.01, volume_max: float = 100.0) -> SymbolInfo:
    return SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=volume_max,
        volume_step=volume_step,
        stops_level=10,
        freeze_level=0,
        trade_contract_size=100.0,
    )


def _engine(**kw) -> RiskEngine:
    return RiskEngine(RiskConfig(), **kw)


class TestSizingInputHostility:
    def test_nan_or_negative_risk_pct_never_yields_a_tradable_lot(self):
        """R1: deleted INVALID_RISK_AMOUNT/INVALID_RAW_LOTS guards would let a
        NaN lot flow through min() (nan comparisons are False) to the broker."""
        eng = _engine()
        acct = _account(10_000.0, 20_000.0)
        sym = _symbol()
        for bad in (float("nan"), float("-inf"), float("inf"), -1.0):
            volume, reason = eng.calculate_dynamic_volume(
                entry=2000.0, sl=1999.0, account=acct, symbol_info=sym, risk_pct=bad
            )
            assert volume == 0.0, f"risk_pct={bad} produced lot {volume}"
            assert reason.startswith("INVALID"), f"risk_pct={bad} reason={reason}"

    def test_zero_or_negative_free_margin_cannot_size_a_lot(self):
        """R2: AccountInfo.margin_free has NO field constraint (unlike equity),
        so a negative value is constructible; the INVALID_FREE_MARGIN gate is
        the only thing between it and the micro-account min-lot exception."""
        eng = _engine()
        sym = _symbol()
        for mf in (0.0, -500.0):
            volume, reason = eng.calculate_dynamic_volume(
                entry=2000.0,
                sl=1999.0,
                account=_account(40.0, mf),  # micro equity: exception bait
                symbol_info=sym,
                risk_pct=0.5,
            )
            assert volume == 0.0 and reason == "INVALID_FREE_MARGIN", (
                f"margin_free={mf} -> ({volume}, {reason})"
            )


class TestBrokerStepGeometry:
    def test_floor_to_step_absorbs_binary_float_error_at_the_boundary(self):
        """R3: 0.58/0.01 == 57.99999999999999 in IEEE-754. Without the eps in
        _floor_to_step the value floors a WHOLE 0.01 lot short (0.57),
        under-sizing every trade whose raw size lands on a step boundary."""
        eng = _engine()
        # values chosen so float division lands strictly BELOW the integer:
        assert eng._floor_to_step(0.58, 0.01) == 0.58
        assert eng._floor_to_step(0.29, 0.01) == 0.29
        # and a genuinely in-between value still floors DOWN (no rounding up):
        assert eng._floor_to_step(0.575, 0.01) == 0.57
        # hostile step values never produce a size:
        assert eng._floor_to_step(1.0, 0.0) == 0.0
        assert eng._floor_to_step(float("nan"), 0.01) == 0.0

    @pytest.mark.parametrize(
        ("equity", "expected_cap"),
        [
            (99.99, 0.02),
            (100.0, 0.10),
            (999.99, 0.10),
            (1000.0, 1.00),
            (9999.99, 1.00),
            (10000.0, 10.0),
        ],
    )
    def test_tier_cap_binds_at_exact_equity_boundary_exclusive_upper(self, equity, expected_cap):
        """R4: caps are `<` semantics (equity==100.0 is the 0.10 tier, NOT
        0.02). An `<=` flip moves every boundary account down (or up) a tier —
        the existing critical matrix never binds the cap at the boundaries
        (raw_lots below tier there), so this test carries the whole contract."""
        eng = _engine()
        sym = _symbol()
        # tight SL + risk_pct=9 makes raw_lots >= 1.0 for every tier, forcing
        # the tier cap (not the risk math) to be the binding constraint.
        acct = _account(equity, equity * 100)
        volume, _reason = eng.calculate_dynamic_volume(
            entry=2000.0, sl=1999.90, account=acct, symbol_info=sym, risk_pct=9.0
        )
        assert volume == pytest.approx(min(expected_cap, 10.0), abs=1e-9), (
            f"equity {equity} sized {volume}, tier cap must be {expected_cap}"
        )

    def test_tier_cap_respects_broker_volume_max_at_top_tier(self):
        """Step-6 top tier is min(10.0, volume_max): a 5.0-max symbol must cap
        at 5.00 (the risk_engine.py:218 contract get_clamped relies on)."""
        eng = _engine()
        acct = _account(10_000.0, 1_000_000.0)
        volume, _ = eng.calculate_dynamic_volume(
            entry=2000.0,
            sl=1999.90,
            account=acct,
            symbol_info=_symbol(volume_max=5.0),
            risk_pct=9.0,
        )
        assert volume == pytest.approx(5.0)


class TestDispatchFallbackClamp:
    def test_account_none_fallback_still_enforces_hard_max_lots(self):
        """R5: dispatch.py calls get_clamped_position_size(account=None)
        whenever adapter.get_account_info() raises — a LIVE condition. The
        fallback ceiling must keep HARD_MAX_LOTS parity (10.0) or every
        broker outage becomes an uncapped-size path."""
        eng = _engine()
        assert eng.get_clamped_position_size(volume=25.0, account=None, symbol_info=None) == 10.0
        assert eng.get_clamped_position_size(volume=None, account=None, symbol_info=None) == 0.0
        # symbol volume_max binds under the fallback ceiling:
        assert (
            eng.get_clamped_position_size(
                volume=25.0, account=None, symbol_info=_symbol(volume_max=3.0)
            )
            == 3.0
        )
        # and it matches the execution-layer constant:
        from nexus_scalp.execution.order_manager import HARD_MAX_LOTS

        assert HARD_MAX_LOTS == 10.0


class TestMarginClampFraction:
    def _sized(self, eng: RiskEngine, margin_free: float) -> float:
        # Binding-margin scenario: risk math wants more than margin allows.
        volume, _ = eng.calculate_dynamic_volume(
            entry=2000.0,
            sl=1999.0,
            account=_account(10_000.0, margin_free),
            symbol_info=_symbol(),
            risk_pct=5.0,
        )
        return volume

    def test_bare_default_engine_clamps_at_ten_percent(self):
        """R6a: RiskEngine(RiskConfig()) with NO explicit margin kwarg uses
        the ctor default 10.0 (risk_engine.py:49) — half the historical 20%
        hard clamp. A silent default drift to 20% doubles the margin any
        single order may consume; this pins the DEFAULT, which the bug260
        battery never covers (it always passes explicit values)."""
        eng = _engine()
        mf = 10_000.0
        vol = self._sized(eng, mf)
        # margin ceiling = (0.10 * margin_free * leverage) / (contract * entry)
        #                  = (0.10 * 10000 * 100) / (100 * 2000) = 0.5 lots,
        # below the risk-math size (5% * 10k = 500 / (1.0 * 100) = 5.0 raw)
        # -> the MARGIN clamp must be the binding constraint at 0.50.
        assert vol == pytest.approx(0.5), f"default margin fraction drifted: {vol}"

    def test_hard_twenty_percent_ceiling_survives_giant_and_broken_config(self):
        """R6b: configured usage > 20% is clamped DOWN to the hard 20%
        ceiling; bool/NaN/zero/negative configs fall back to the hard 20%
        — a broken config may only TIGHTEN or preserve, never loosen."""
        for cfg_val, expect_fraction in ((500.0, 0.20), (True, 0.20), (float("nan"), 0.20)):
            eng = _engine()
            eng.max_margin_usage_pct = cfg_val
            mf = 1_000.0
            vol = self._sized(eng, mf)
            ceiling = (expect_fraction * mf * 100) / (100 * 2000)  # lots
            assert vol <= ceiling + 1e-9, (
                f"config {cfg_val!r} allowed volume {vol} > {expect_fraction:.0%} ceiling {ceiling}"
            )

    def test_monotone_risk_invariant_every_admitted_size_respects_configured_risk(self):
        """Property grid: for every admissible sizing, monetary risk at SL
        never exceeds the configured risk percent (INV-EQUITY-RISK-CAP).
        Equivalence-class boundaries only (no arbitrary numbers)."""
        eng = _engine()
        sym = _symbol()
        for equity in (100.0, 999.99, 1000.0, 9999.99, 10_000.0):
            for sl_dist in (0.5, 2.0, 20.0):
                for risk_pct in (0.5, 1.0):
                    acct = _account(equity, equity * 100)
                    vol, _ = eng.calculate_dynamic_volume(
                        entry=2000.0,
                        sl=2000.0 - sl_dist,
                        account=acct,
                        symbol_info=sym,
                        risk_pct=risk_pct,
                    )
                    risk_usd = vol * sl_dist * 100.0
                    assert risk_usd <= equity * risk_pct / 100.0 + 1e-9, (
                        f"equity={equity} sl={sl_dist} pct={risk_pct} sized {vol} = {risk_usd} risk"
                    )
                    if vol > 0.0:
                        assert vol >= sym.volume_min
                        assert math.isfinite(vol)
                        # step-exact (broker legality):
                        assert round(vol / sym.volume_step) * sym.volume_step == pytest.approx(
                            vol, abs=1e-9
                        )
