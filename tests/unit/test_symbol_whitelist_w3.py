"""OPERATOR RULING (2026-09-09): XAUUSD-only until multi-symbol approved.

Tests for the SYMBOL_WHITELIST_GATE added on top of the C4 symbol-aware
unlock: policy must refuse candidates for any tick symbol outside
execution.enabled_symbols (default ["XAUUSD"], reason SYMBOL_NOT_ENABLED,
decision_stage SYMBOL_WHITELIST_GATE) BEFORE any candidate machinery runs.
Red-before: post-C4 policy accepted any tick symbol (ticket matching was
symbol-aware with no whitelist).

xdist-safe: no global state; fixtures reused from the BUG-229 suite.
"""

from __future__ import annotations

import torch

from nexus_scalp.configuration.config import ExecutionConfig
from nexus_scalp.signals.policy import SignalPolicy
from tests.unit.test_no_trade_default_reason_codes import (
    _feature_vector,
    _tick,
)


def test_default_policy_is_xauusd_only() -> None:
    policy = SignalPolicy()
    assert policy.enabled_symbols == ["XAUUSD"]


def test_config_default_matches_ruling() -> None:
    cfg = ExecutionConfig()
    assert cfg.enabled_symbols == ["XAUUSD"]


def test_xauusd_tick_passes_symbol_gate() -> None:
    policy = SignalPolicy()
    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.34, 0.33, 0.33, 0.0]]),
        current_tick=_tick(),
        feature_vector=_feature_vector(),
        regime_state=None,
    )
    assert proposal.decision_stage != "SYMBOL_WHITELIST_GATE"


def test_eurusd_tick_blocked_by_default() -> None:
    policy = SignalPolicy()
    tick = _tick()
    eurusd_tick = tick.model_copy(update={"symbol": "EURUSD"})
    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.10, 0.80, 0.05, 0.05]]),
        current_tick=eurusd_tick,
        feature_vector=_feature_vector(symbol="EURUSD"),
        regime_state=None,
    )
    assert proposal.action.value == "NO_TRADE"
    assert proposal.reason_code == "SYMBOL_NOT_ENABLED"
    assert proposal.decision_stage == "SYMBOL_WHITELIST_GATE"
    assert proposal.risk_checks["tick_symbol"] == "EURUSD"
    assert proposal.risk_checks["enabled_symbols"] == ["XAUUSD"]


def test_explicit_whitelist_allows_second_symbol() -> None:
    policy = SignalPolicy(enabled_symbols=["XAUUSD", "EURUSD"])
    tick = _tick()
    eurusd_tick = tick.model_copy(update={"symbol": "EURUSD"})
    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.10, 0.80, 0.05, 0.05]]),
        current_tick=eurusd_tick,
        feature_vector=_feature_vector(symbol="EURUSD"),
        regime_state=None,
    )
    assert proposal.decision_stage != "SYMBOL_WHITELIST_GATE"


def test_lowercase_symbol_still_matches_whitelist() -> None:
    # Broker symbol-case drift must not bypass the gate (compare uppercased).
    policy = SignalPolicy()
    tick = _tick()
    lower_tick = tick.model_copy(update={"symbol": "xauusd"})
    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.34, 0.33, 0.33, 0.0]]),
        current_tick=lower_tick,
        feature_vector=_feature_vector(symbol="xauusd"),
        regime_state=None,
    )
    assert proposal.decision_stage != "SYMBOL_WHITELIST_GATE"
