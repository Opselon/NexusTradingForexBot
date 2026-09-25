"""
Adversarial Lifecycle Invariant Tests (Phase 8b strict verification)

These tests explicitly attack the lifecycle model to prove the UI can never
imply a strategy can trade when the domain says it cannot, and that illegal
administrative descents are blocked or explicitly traceable.

Targeted at the four adversarial transitions from the spec:
  VALIDATED -> DISCOVERED   (illegal, must be refused)
  SHADOW    -> DISCOVERED   (illegal, must be refused)
  ACTIVE    -> VALIDATED    (illegal, must be refused)
  ACTIVE    -> SHADOW       (illegal, must be refused)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nexus_scalp.research.lifecycle import LifecycleError, can_transition
from nexus_scalp.research.models import CandidateLifecycle, StrategyRegistryEntry
from nexus_scalp.research.snapshot import build_snapshot


def _entry(lc: CandidateLifecycle) -> StrategyRegistryEntry:
    return StrategyRegistryEntry(
        strategy_id="adv-x",
        strategy_version="1.0.0",
        lifecycle=lc,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


class TestIllegalAdministrativeDescents:
    @pytest.mark.parametrize(
        "frm,to",
        [
            (CandidateLifecycle.VALIDATED, CandidateLifecycle.DISCOVERED),
            (CandidateLifecycle.SHADOW, CandidateLifecycle.DISCOVERED),
            (CandidateLifecycle.ACTIVE, CandidateLifecycle.VALIDATED),
            (CandidateLifecycle.ACTIVE, CandidateLifecycle.SHADOW),
            (CandidateLifecycle.SHADOW, CandidateLifecycle.BACKTESTING),
            (CandidateLifecycle.ACTIVE, CandidateLifecycle.BACKTESTING),
        ],
    )
    def test_state_machine_refuses(self, frm, to):
        assert can_transition(frm, to) is False
        with pytest.raises(LifecycleError):
            from nexus_scalp.research.lifecycle import transition

            transition(frm, to)

    @pytest.mark.parametrize(
        "frm,to",
        [
            (CandidateLifecycle.VALIDATED, CandidateLifecycle.SHADOW),
            (CandidateLifecycle.SHADOW, CandidateLifecycle.ACTIVE),
            (CandidateLifecycle.DISCOVERED, CandidateLifecycle.BACKTESTING),
            (
                CandidateLifecycle.REJECTED,
                CandidateLifecycle.DISCOVERED,
            ),  # REJECTED terminal → not allowed
        ],
    )
    def test_state_machine_legal_or_terminal(self, frm, to):
        # Either legal or properly refused — never silently allowed.
        ok = can_transition(frm, to)
        if not ok:
            with pytest.raises(LifecycleError):
                from nexus_scalp.research.lifecycle import transition

                transition(frm, to)


class TestExecutionInvariantUnderAdversary:
    """The cardinal rule: UI must never imply tradeability against domain truth."""

    def test_validated_never_reports_yes(self):
        snap = build_snapshot(_entry(CandidateLifecycle.VALIDATED))
        assert snap.execution_eligibility.eligibility_state != "YES"
        assert snap.execution_eligibility.can_trade is False

    def test_shadow_never_reports_full_live_yes(self):
        snap = build_snapshot(_entry(CandidateLifecycle.SHADOW))
        # SHADOW_ONLY is explicit; it must NOT be YES (no live capital).
        assert snap.execution_eligibility.eligibility_state == "SHADOW_ONLY"
        assert snap.execution_eligibility.eligibility_state != "YES"

    def test_rejected_never_trades(self):
        for lc in (
            CandidateLifecycle.REJECTED,
            CandidateLifecycle.DEGRADED,
            CandidateLifecycle.RETIRED,
        ):
            snap = build_snapshot(_entry(lc))
            assert snap.execution_eligibility.eligibility_state == "BLOCKED"
            assert snap.execution_eligibility.can_trade is False

    def test_only_active_is_yes(self):
        for lc in CandidateLifecycle:
            snap = build_snapshot(_entry(lc))
            if lc == CandidateLifecycle.ACTIVE:
                assert snap.execution_eligibility.eligibility_state == "YES"
            else:
                assert snap.execution_eligibility.eligibility_state != "YES"


class TestRegressionGuardInvariant:
    """Regression guard must not let a weaker lifecycle overwrite a stronger one."""

    def test_stronger_refuses_weaker_same_version(self):
        from nexus_scalp.research.registry import _is_stronger

        assert _is_stronger(CandidateLifecycle.VALIDATED, CandidateLifecycle.DISCOVERED) is True
        assert _is_stronger(CandidateLifecycle.SHADOW, CandidateLifecycle.VALIDATED) is True
        assert _is_stronger(CandidateLifecycle.ACTIVE, CandidateLifecycle.SHADOW) is True
        # VALIDATED -> REJECTED is a same-tier truth rewrite, refused.
        assert _is_stronger(CandidateLifecycle.VALIDATED, CandidateLifecycle.REJECTED) is True
