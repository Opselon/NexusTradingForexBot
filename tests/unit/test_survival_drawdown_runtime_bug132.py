"""
Unit Tests - Survival/Drawdown guard reads the runtime snapshot (BUG-132)
=========================================================================
The engine HALTS on max drawdown using the persisted/UI value, not the
bootstrap YAML default. Regression: a UI save of 95% must take effect on
the survival guard without restart (previously 2.0% bootstrap killed a
live engine at 15.9% post-withdrawal drawdown even though 95 was persisted).
"""

from __future__ import annotations

from types import SimpleNamespace

from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.domain.models import AccountInfo


class _FakeSnapshot:
    def __init__(self, dd_limit: float) -> None:
        self.risk = SimpleNamespace(max_account_drawdown_pct=dd_limit)


class _FakeRuntimeConfig:
    def __init__(self, dd_limit: float) -> None:
        self._snap = _FakeSnapshot(dd_limit)

    def get_snapshot(self):
        return self._snap


def _make_engine(dd_limit: float) -> SimpleNamespace:
    # bootstrap config keeps the OLD default (2.0) so the test proves the
    # guard reads the SNAPSHOT, not the bootstrap AppConfig.
    engine = SimpleNamespace(
        runtime_config=_FakeRuntimeConfig(dd_limit),
        config=SimpleNamespace(risk=SimpleNamespace(max_account_drawdown_pct=2.0)),
        _last_balance=33288.22,
        _last_active_position_count=0,
        _peak_equity=39601.37,
        _consecutive_losses=0,
        _survival_mode_active=False,
        notifier=None,
        _running=True,
        # Runtime-safety mission: the halt path is the canonical
        # trigger_runtime_halt entrypoint; record the call so the unit keeps
        # testing the DRAWDOWN POLICY in isolation while asserting the halt
        # routes through the persisted-state mechanism.
        _runtime_risk_state="RUNNING",
        _halt_reason="",
        _halt_triggered_at="",
        _halt_calls=[],
    )

    def _record_halt(**kwargs):
        engine._halt_calls.append(kwargs)
        engine._running = False

    engine.trigger_runtime_halt = _record_halt
    engine._update_survival_state = LiveEngine._update_survival_state.__get__(
        engine, SimpleNamespace
    )
    return engine


ACCOUNT = AccountInfo(
    login=10011755849,
    trade_mode=3,
    leverage=100,
    margin=0.0,
    balance=33288.22,
    equity=33288.22,
    margin_free=33288.22,
)


def test_survival_guard_uses_persisted_dd_limit_no_halt() -> None:
    """15.94% drawdown vs persisted 95% -> neither survival nor halt."""
    engine = _make_engine(95.0)
    engine._update_survival_state(ACCOUNT, 0)
    assert engine._running is True
    assert engine._survival_mode_active is False


def test_survival_guard_still_halts_on_low_limit() -> None:
    """Same drawdown vs 2.0% (bootstrap default) -> halt + survival (old behavior)."""
    engine = _make_engine(2.0)
    engine._update_survival_state(ACCOUNT, 0)
    assert engine._running is False
    assert engine._survival_mode_active is True


def test_drawdown_halt_routes_through_persisted_state_entrypoint() -> None:
    """Runtime-safety mission: the drawdown halt goes through
    trigger_runtime_halt (the persisted-state entrypoint), not a bare
    ``self._running = False`` that a restart would silently forget."""
    engine = _make_engine(2.0)
    engine._update_survival_state(ACCOUNT, 0)
    assert len(engine._halt_calls) == 1
    call = engine._halt_calls[0]
    assert call["state"] == "HALTED"
    assert call["source"] == "SURVIVAL_DRAWDOWN_GUARD"
    assert "Max drawdown exceeded" in call["reason"]


def test_survival_guard_halts_when_drawdown_exceeds_snapshot() -> None:
    """drawdown 15.94 > snapshot limit 5.0 -> halt (limit is the snapshot)."""
    engine = _make_engine(5.0)
    engine._update_survival_state(ACCOUNT, 0)
    assert engine._running is False
    assert engine._survival_mode_active is True


def test_survival_mode_only_when_below_snapshot_limit() -> None:
    """drawdown 15.94 > 30*0.5 (survival) and < 30 (no halt)."""
    engine = _make_engine(30.0)
    engine._update_survival_state(ACCOUNT, 0)
    assert engine._running is True
    assert engine._survival_mode_active is True


def test_detached_runtime_config_falls_back_to_bootstrap() -> None:
    """When runtime_config is absent (unit edge), the bootstrap default applies."""
    engine = SimpleNamespace(
        runtime_config=None,
        config=SimpleNamespace(risk=SimpleNamespace(max_account_drawdown_pct=2.0)),
        _last_balance=33288.22,
        _last_active_position_count=0,
        _peak_equity=39601.37,
        _consecutive_losses=0,
        _survival_mode_active=False,
        notifier=None,
        _running=True,
        _runtime_risk_state="RUNNING",
        _halt_reason="",
        _halt_triggered_at="",
        _halt_calls=[],
    )

    def _record_halt(**kwargs):
        engine._halt_calls.append(kwargs)
        engine._running = False

    engine.trigger_runtime_halt = _record_halt
    engine._update_survival_state = LiveEngine._update_survival_state.__get__(
        engine, SimpleNamespace
    )
    engine._update_survival_state(ACCOUNT, 0)
    assert engine._running is False  # 15.94 > 2.0 bootstrap -> halt
