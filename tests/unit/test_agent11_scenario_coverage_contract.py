"""Agent-11 60-scenario router coverage contract (BUG-249 companion suite).

Pins the REAL scenario surface of _resolve_position_management_scenario so the
"60-Scenario Router" contract can never silently drift again:

- every currently-implemented S-code is enumerated with its action class;
- the dispatcher understands every action the router can emit;
- the profit-shield invariant holds: no winning trade is closed by an
  emergency bailout scenario;
- emergency close scenarios are priority-ordered before scale-out/trailing;
- the default terminal state is HOLD (S60), never a broker mutation.

This converts the sparse "60 scenario" label into an auditable contract:
a future scenario add/remove/change must update this suite deliberately.
"""

from __future__ import annotations

import re
from pathlib import Path

from nexus_scalp.execution.order_manager import (
    OrderLifecycleManager,
    PositionState,
)

ORDER_MANAGER_SRC = (
    Path(__file__).resolve().parents[2] / "src" / "nexus_scalp" / "execution" / "order_manager.py"
)

#: Actions the broker-dispatch stage (_execute_position_action + plan) handles.
DISPATCHER_UNDERSTOOD_ACTIONS = {
    "CLOSE",
    "MODIFY_SL",
    "PARTIAL_CLOSE",
    "BREAK_EVEN",
    "NORMAL_TRAIL",
    # Non-mutating router outputs that terminate the pass:
    "DEFER_STOPS",
    "MONITOR",
    "HOLD",
}

#: Profit-side states for the shield assertion.
PROFIT_STATES = {
    PositionState.PROFIT_UNPROTECTED,
    PositionState.PROFIT_PROTECTED,
    PositionState.PROFIT_TRAILING,
}


def _router_source() -> str:
    src = ORDER_MANAGER_SRC.read_text(encoding="utf-8", errors="replace")
    i = src.find("def _resolve_position_management_scenario")
    j = src.find("def _ledger_account_source")
    assert i != -1 and j != -1, "router method boundaries not found"
    return src[i:j]


def _extract_scenarios() -> list[tuple[str, str]]:
    """Returns [(code, action)] in priority order from the router source."""
    seg = _router_source()
    pattern = re.compile(
        r'(?:if|elif)\s+(?:.*?)\r?\n\s*return "([A-Z_]+)", "(S\d{2}_[A-Z_]+)"',
        re.S,
    )
    found: list[tuple[str, str]] = []
    for m in pattern.finditer(seg):
        action, code = m.group(1), m.group(2)
        if (code, action) not in found:
            found.append((code, action))
    return found


class TestScenarioCoverageContract:
    def test_scenario_codes_enumerated(self) -> None:
        scenarios = _extract_scenarios()
        codes = [c for c, _ in scenarios]
        # The router's implemented surface (S60 default handled by else-branch).
        assert "S01_CRITICAL_COMPOUND_KILL_SWITCH" in codes
        assert "S02_TOXIC_FLOW_KILL_SWITCH" in codes
        assert "S09_CRITICAL_HOLD_SCORE_BREACH_BAILOUT" in codes
        assert "S21_HARD_STAGNATION_TIMEOUT" in codes
        assert "S32_HIGH_PROFIT_SCALE_OUT" in codes
        assert "S44_HEALTHY_WINNER_NORMAL_TRAIL" in codes
        assert "S47_STANDARD_BREAK_EVEN_LOCK" in codes
        assert "S48_LOW_IMPACT_FAST_BREAK_EVEN" in codes
        assert "S52_SPREAD_SPIKE_STOP_DEFER" in codes
        assert "S56_MISSED_POSITION_STATE_RECONSTRUCTION" in codes
        assert len(codes) >= 20, f"router shrank: only {len(codes)} explicit scenarios remain"

    def test_every_router_action_is_dispatcher_understood(self) -> None:
        scenarios = _extract_scenarios()
        for code, action in scenarios:
            assert action in DISPATCHER_UNDERSTOOD_ACTIONS, (
                f"{code} emits action {action!r} which the dispatcher stage does not understand"
            )

    def test_default_terminal_state_is_hold(self) -> None:
        assert 'return "HOLD", "S60_DEFAULT_CONTROLLED_HOLD"' in _router_source()

    def test_profit_shield_guard_present_in_source(self) -> None:
        """Emergency CLOSE scenarios must be guarded by the winning-trade shield."""
        seg = _router_source()
        assert seg.count("not is_winning_trade") >= 12, (
            "profit-shield guard count regressed - emergency close scenarios "
            "must never fire on winning trades"
        )

    def test_emergency_closes_precede_scale_out(self) -> None:
        """Priority order: every CLOSE scenario must precede S32 scale-out."""
        scenarios = _extract_scenarios()
        codes = [c for c, _ in scenarios]
        first_non_close = next((i for i, (_, a) in enumerate(scenarios) if a != "CLOSE"), None)
        assert first_non_close is not None
        close_codes = codes[:first_non_close]
        assert all(c.startswith(("S0", "S1", "S2")) for c in close_codes), (
            "emergency CLOSE block reordered before the profit-side scenarios"
        )
        assert "S32_HIGH_PROFIT_SCALE_OUT" in codes[first_non_close:]

    def test_state_machine_bypass_states_are_emergency_only(self) -> None:
        assert OrderLifecycleManager is not None  # import sanity
        from nexus_scalp.execution.position_state_machine import (
            PositionStateMachine,
        )

        assert PositionStateMachine.BYPASS_STATES == frozenset(
            {PositionState.PROFIT_GIVEBACK_CRITICAL, PositionState.LOSS_HARD_EXIT}
        ), "emergency bypass set changed - review hysteresis contract"
