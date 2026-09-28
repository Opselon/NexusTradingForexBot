"""RISK-LIVE: /risk gauges must show live truth, not boot defaults.

Regressions fixed here (diagnosed against a live HALTED engine on 2026-09-28):

RISK-LIVE-001  debug_snapshot._risk_section emitted a BOUND-METHOD repr for
  runtime_risk_state_effective because LiveEngine.runtime_risk_state is a
  method (the derived state incl. DEGRADED), not a property. The frontend
  uppercased the repr, so GuardianHero / Safety status rendered UNKNOWN while
  the persisted state was HALTED.

RISK-LIVE-002  RuntimeLoop.run() refreshed the account snapshot, peak equity
  and account freshness only INSIDE the armed tick loop. A persisted safety
  halt refuses trading before that point, so a halted-but-broker-connected
  engine reported account_freshness MISSING and a null drawdown forever.

RISK-LIVE-003  /api/v1/risk/status read the BOOTSTRAP engine.config limits
  while GET /api/config exposed the authoritative runtime snapshot — the same
  persisted values the operator set. The drawdown limit on /risk (5.0) did not
  match /config (6.25), so the gauge compared live risk against an obsolete
  budget.

Contract preserved everywhere: a missing value or limit stays null/indeterminate
— nothing here fabricates a 0 or a PASS.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from nexus_scalp.web.debug_snapshot import _risk_section


class _Account:
    """Minimal stand-in for adapters.base.AccountSnapshot."""

    def __init__(self, **kw: Any) -> None:
        self.available = True
        for k, v in kw.items():
            setattr(self, k, v)


class _FakeAdapter:
    def __init__(self, account: Any, snapshot: Any) -> None:
        self._account = account
        self._snapshot = snapshot
        self.snapshot_calls = 0

    def get_account_info(self) -> Any:
        return self._account

    def get_account_snapshot(self) -> Any:
        self.snapshot_calls += 1
        return self._snapshot


class _RuntimeConfigStore:
    """Mirrors RuntimeConfigStore.get_snapshot().to_app_config().risk."""

    def __init__(self, risk_cfg: Any, version: int) -> None:
        self._risk = risk_cfg
        self._version = version

    def get_snapshot(self) -> Any:
        return SimpleNamespace(
            to_app_config=lambda: SimpleNamespace(risk=self._risk),
        )

    def get_version(self) -> int:
        return self._version


def _engine(**kw: Any) -> SimpleNamespace:
    """LiveEngine-shaped object; runtime_risk_state stays a METHOD like the real one."""
    base = dict(
        risk_engine=SimpleNamespace(
            _kill_switch_active=False, max_allowed_lots=2.0, min_risk_reward_ratio=1.5
        ),
        _survival_mode_active=False,
        _runtime_risk_state="HALTED",
        _halt_reason="PERSISTED_HALTED: Max drawdown exceeded: 23.45% > limit 5.00%",
        _halt_triggered_at="2026-09-24T18:14:12+0000",
        _hot_path_circuit=SimpleNamespace(
            consecutive_error_count=0, max_consecutive_errors=10, error_window_sec=30.0
        ),
        _account_freshness="MISSING",
        audit=SimpleNamespace(
            audit_batch_failures=0,
            audit_dead_letter_rows=0,
            dead_letter_pruned_rows=0,
            audit_salvaged_rows=0,
            telemetry_dropped=0,
            financial_queue_backpressure=0,
            financial_events_overflowed=0,
            financial_events_failed=0,
            financial_overflow_recovered=0,
            financial_overflow_failed=0,
            overflow_pending_count=lambda: 0,
        ),
        _consecutive_losses=0,
        config=SimpleNamespace(
            risk=SimpleNamespace(
                risk_per_trade_pct=1.0,
                max_concurrent_positions=1,
                max_spread_points=60,
                max_account_drawdown_pct=5.0,  # bootstrap (stale) value
            )
        ),
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# RISK-LIVE-001
# ---------------------------------------------------------------------------


def test_effective_risk_state_is_a_state_string_not_a_method_repr() -> None:
    """The bug: getattr(engine,'runtime_risk_state') returned a bound method."""
    eng = _engine()

    def runtime_risk_state(self: Any) -> str:
        return "HALTED"

    eng.runtime_risk_state = runtime_risk_state.__get__(eng, type(eng))  # type: ignore[method-assign]

    out = _risk_section(eng)
    eff = out["runtime_risk_state_effective"]
    assert isinstance(eff, str)
    assert "<bound method" not in eff, eff
    assert eff == "HALTED"
    # Raw persisted state is still echoed separately.
    assert out["runtime_risk_state"] == "HALTED"


def test_effective_risk_state_reports_degraded_when_derived() -> None:
    eng = _engine(_runtime_risk_state="RUNNING")

    def runtime_risk_state(self: Any) -> str:
        return "DEGRADED"

    eng.runtime_risk_state = runtime_risk_state.__get__(eng, type(eng))  # type: ignore[method-assign]
    assert _risk_section(eng)["runtime_risk_state_effective"] == "DEGRADED"


def test_effective_risk_state_falls_back_when_call_raises() -> None:
    eng = _engine(_runtime_risk_state="KILL_SWITCH")

    def runtime_risk_state(self: Any) -> str:
        raise RuntimeError("audit store gone")

    eng.runtime_risk_state = runtime_risk_state.__get__(eng, type(eng))  # type: ignore[method-assign]
    # A failing derivation must not mask the persisted safety state.
    assert _risk_section(eng)["runtime_risk_state_effective"] == "KILL_SWITCH"


def test_effective_risk_state_defaults_when_engine_lacks_the_method() -> None:
    # An engine object without the surface at all (test doubles, partial fakes).
    out = _risk_section(_engine())
    assert out["runtime_risk_state_effective"] == "RUNNING"


def test_risk_section_marks_account_unavailable_when_no_snapshot() -> None:
    eng = _engine(adapter=_FakeAdapter(account=None, snapshot=None), _peak_equity=0.0)
    out = _risk_section(eng)
    assert out["account"]["available"] is False
    assert out["account"]["drawdown_pct"] is None


# ---------------------------------------------------------------------------
# RISK-LIVE-002
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observable_account_state_refreshes_before_halt_guard() -> None:
    """The halt path must still populate the account snapshot + freshness.

    This exercises the RuntimeLoop._refresh_observable_account_state helper
    directly (the loop's own run() would block on the halt forever). It is the
    contract that a HALTED engine still exposes measured broker state.
    """
    from nexus_scalp.application.live.runtime_loop import RuntimeLoop

    snapshot = _Account(
        balance=29842.89, equity=29842.89, margin=0.0, margin_free=29842.89, margin_level=0.0
    )
    account = SimpleNamespace(login=10011755849, balance=29842.89, equity=29842.89)
    adapter = _FakeAdapter(account=account, snapshot=snapshot)

    om = _engine(
        adapter=adapter, _peak_equity=0.0, _last_account_info=None, _last_account_refresh=0.0
    )
    om._account_last_successful_refresh = 0.0
    om._account_snapshot = None
    # The real engine resolves peak equity from the audit store; stub it so the
    # helper's guarded call works without a live DB.
    om._restore_peak_equity = lambda acct: setattr(
        om, "_peak_equity", float(getattr(acct, "equity", 0.0))
    )  # type: ignore[method-assign]

    loop = RuntimeLoop(om)
    loop._refresh_observable_account_state()

    assert om._account_snapshot is snapshot
    assert om._account_freshness == "FRESH"
    assert om._last_account_info is account
    assert om._peak_equity == pytest.approx(29842.89)
    assert adapter.snapshot_calls == 1


@pytest.mark.asyncio
async def test_observable_refresh_never_raises_on_broker_fault() -> None:
    """A telemetry read must not influence the safety decision after it."""
    from nexus_scalp.application.live.runtime_loop import RuntimeLoop

    class _Boom:
        def get_account_info(self) -> Any:
            raise RuntimeError("IPC timeout -10005")

        def get_account_snapshot(self) -> Any:
            raise RuntimeError("IPC timeout -10005")

    om = _engine(adapter=_Boom(), _account_freshness="MISSING")
    loop = RuntimeLoop(om)
    # Returns normally; freshness stays MISSING (honest), not FRESH.
    loop._refresh_observable_account_state()
    assert om._account_freshness == "MISSING"


# ---------------------------------------------------------------------------
# RISK-LIVE-003
# ---------------------------------------------------------------------------


def test_risk_status_prefers_runtime_config_over_bootstrap() -> None:
    """The /status limit block must match GET /api/config's authority."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from nexus_scalp.web.api_v1 import risk as risk_route

    runtime_risk = SimpleNamespace(
        max_account_drawdown_pct=6.25,  # persisted operator value
        risk_per_trade_pct=1.0,
        max_concurrent_positions=1,
        max_spread_points=60,
        max_margin_usage_pct=10.0,
        max_allowed_lots=2.0,
        enforce_stop_loss=True,
    )
    eng = _engine()
    eng.runtime_config = _RuntimeConfigStore(runtime_risk, version=2)  # type: ignore[attr-defined]

    app = FastAPI()
    app.state.engine = eng
    app.include_router(risk_route.router)

    with TestClient(app) as client:
        r = client.get("/api/v1/risk/status")
    assert r.status_code == 200
    cfg = r.json()["data"]["risk_config"]
    # The authoritative (persisted, operator-set) limit — not the stale 5.0.
    assert cfg["max_account_drawdown_pct"] == 6.25
    assert cfg["config_source"] == "runtime"
    assert cfg["configuration_version"] == 2


def test_risk_status_falls_back_to_bootstrap_without_store() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from nexus_scalp.web.api_v1 import risk as risk_route

    eng = _engine()  # no runtime_config attribute
    app = FastAPI()
    app.state.engine = eng
    app.include_router(risk_route.router)

    with TestClient(app) as client:
        cfg = client.get("/api/v1/risk/status").json()["data"]["risk_config"]
    # Honest fallback: labelled, so the UI never presents it as effective.
    assert cfg["max_account_drawdown_pct"] == 5.0
    assert cfg["config_source"] == "bootstrap"


def test_risk_status_config_block_null_when_engine_config_broken() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from nexus_scalp.web.api_v1 import risk as risk_route

    eng = _engine()
    del eng.config  # serialization fails -> null, never invented limits

    app = FastAPI()
    app.state.engine = eng
    app.include_router(risk_route.router)

    with TestClient(app) as client:
        data = client.get("/api/v1/risk/status").json()["data"]
    assert data["risk_config"] is None
