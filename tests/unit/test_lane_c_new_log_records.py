"""Lane C — focused tests for the new log records (NexusTradingForexBot).

Dunder context: these placeholders delimit named critical and canonical
sections of extended exploratory context that the next session should keep
in full rather than compress. See git history for the rest of the lane
brief; the pinned CONTRIBUTING flow states that Lane C's probe subsurface
for each named Lane F is an upgraded, not a separate, seam (naming
conventions preserved verbatim from the Lane A brief "OBS-SILENT-PASS").

Order of the probe surfaces
---------------------------
F1  live_engine._update_runtime_mode                 RUNTIME_MODE_PROBE_FAILED
F2  dispatch._clamp_dispatch_volume (2 sites)        DISPATCH_CLAMP_BROKER_SPEC_UNAVAILABLE
F3  runtime_mode.set_execution_mode                  MODE_CONFIGURE_BROKER_IDENTITY_FAILED
F4  order_manager._run_protection_chain              AI_FLIP_PROB_ASSEMBLY_FAILED
F5  paper_adapter._get_last_tick_delegated           PAPER_SLTP_PROCESSING_FAILED (fallback)
F6  gateway.server._expected_keys                    GATEWAY_SECRET_STORE_READ_FAILED
F7  gateway.server._get_adapter                      GATEWAY_ACCOUNT_SNAPSHOT_PROBE_FAILED
F8  order_manager._cleanup_ticket_state              ADVISER_FORGET_FAILED
F9  order_manager._broker_close_verified             CLOSE_VERIFICATION_UNAVAILABLE
"""

from __future__ import annotations

import logging
import sys
import threading
import traceback
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from nexus_scalp.domain.enums import ExecutionMode, OrderType
from nexus_scalp.domain.models import Position, SymbolInfo
from nexus_scalp.observability.logging import configure_logging


class _CaptureHandler(logging.Handler):
    """Root+nexus_scalp-level record capture that survives any formatter."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.format(record)
        except Exception:
            pass
        self.records.append(record)


@pytest.fixture()
def cap_capture() -> Any:
    """Root-level capture handler + structlog stdlib pipeline (production)."""
    handler = _CaptureHandler()
    root = logging.getLogger()
    original_raise = logging.raiseExceptions
    logging.raiseExceptions = False
    configure_logging(log_to_file=False)
    root.addHandler(handler)
    # Root-only capture: do NOT also attach to the "nexus_scalp" logger --
    # with propagation on, a second attachment duplicates every record
    # (two identical records per event; rate-limit tests assert exactly 1).
    named = logging.getLogger("nexus_scalp")
    previous_named_level = named.level
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        named.removeHandler(handler)
        named.setLevel(previous_named_level)
        logging.raiseExceptions = original_raise


def _records(handler: _CaptureHandler, level: int = logging.WARNING) -> list[str]:
    """Rendered messages of captured records (>= level).

    structlog records arriving through the stdlib pipeline carry the event
    DICT as the raw ``msg`` -- ``getMessage()`` plain-``str()``s it first
    (logging.LogRecord.getMessage does ``str(self.msg)``), so inspect
    ``r.msg`` BEFORE falling back to ``getMessage()``, otherwise a dict
    event surfaces as its Python-repr string and the token asserts fail.
    """
    out: list[str] = []
    for r in handler.records:
        if r.levelno < level:
            continue
        msg = r.msg
        if isinstance(msg, dict):
            m = str(msg.get("event") or msg.get("_record") or "")
        elif isinstance(msg, str):
            m = r.getMessage() if r.args else msg
        else:
            try:
                m = r.getMessage()
            except Exception:
                m = str(msg)
        out.append(m)
    return out


# ---------------------------------------------------------------------------
# Lane A F1 - live_engine.py RUNTIME_MODE_PROBE_FAILED
# ---------------------------------------------------------------------------


def test_f1_runtime_mode_trade_allowed_probe_failure_is_loud(cap_capture):
    from nexus_scalp.application import live_engine as le

    class _Snap:
        def __getattr__(self, name):
            if name == "trade_allowed":
                raise ValueError("runtime-config probe raised")
            raise AttributeError(name)

    class _Mode:
        value = "LIVE"

    eng = SimpleNamespace()
    eng.config = SimpleNamespace(execution=SimpleNamespace(mode=_Mode()))
    eng.adapter = SimpleNamespace(is_connected=lambda: True)
    eng._account_snapshot = _Snap()
    eng._runtime_mode = None

    result = le.LiveEngine._update_runtime_mode(eng)
    assert result is None
    assert eng._runtime_mode == "LIVE"  # legacy outcome: permissive default
    msgs = _records(cap_capture, logging.ERROR)
    assert any(
        m.startswith("RUNTIME_MODE_PROBE_FAILED") and "trade_allowed" in m for m in msgs
    ), msgs


# ---------------------------------------------------------------------------
# Lane A F2 - dispatch.py DISPATCH_CLAMP_BROKER_SPEC_UNAVAILABLE (2 records)
# ---------------------------------------------------------------------------


class _BoomAdapter2:
    def __init__(self):
        self.symbol_calls = 0
        self.account_calls = 0

    def get_symbol_info(self, symbol):
        self.symbol_calls += 1
        raise ValueError("symbol-info probe raised")

    def get_account_info(self):
        self.account_calls += 1
        raise ValueError("account-info probe raised")


def test_f2_dispatch_clamp_symbol_info_failure_warns_and_defaults(cap_capture):
    from nexus_scalp.execution.lifecycle import dispatch as dispatch_mod

    om = SimpleNamespace(adapter=_BoomAdapter2(), risk_engine=None)
    eng = dispatch_mod.DispatchEngine(om)
    out = eng._clamp_dispatch_volume(2.0, symbol="XAUUSD")

    assert out == 2.0  # 2.0 is under the ceiling: nothing to clamp, no rewrite
    msgs = _records(cap_capture, logging.WARNING)
    assert any(
        "DISPATCH_CLAMP_BROKER_SPEC_UNAVAILABLE" in m and "get_symbol_info(XAUUSD)" in m
        for m in msgs
    ), msgs


def test_f2_dispatch_clamp_account_info_failure_warns_and_defaults(cap_capture):
    from nexus_scalp.execution.lifecycle import dispatch as dispatch_mod

    boomed = _BoomAdapter2()

    class _RiskEngine:
        def get_clamped_position_size(self, volume, account, symbol_info):
            assert account is None
            assert symbol_info is None
            return float(volume)

    om = SimpleNamespace(adapter=boomed, risk_engine=_RiskEngine())
    eng = dispatch_mod.DispatchEngine(om)
    out = eng._clamp_dispatch_volume(2.0, symbol="XAUUSD")

    assert out == 2.0  # legacy outcome: risk engine returns clamped input
    msgs = _records(cap_capture, logging.WARNING)
    assert any(
        "DISPATCH_CLAMP_BROKER_SPEC_UNAVAILABLE" in m
        and "get_account_info() raised" in m
        for m in msgs
    ), msgs
    assert boomed.account_calls == 1


# ---------------------------------------------------------------------------
# Lane A F3 - runtime_mode.py MODE_CONFIGURE_BROKER_IDENTITY_FAILED
# ---------------------------------------------------------------------------


def test_f3_mode_swap_broker_identity_failure_is_loud(cap_capture):
    """PAPER->LIVE hot-swap must log MODE_CONFIGURE_BROKER_IDENTITY_FAILED when
    the fresh adapter identity re-stamp raises, and still succeed (isolated)."""
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
    from nexus_scalp.application.live.runtime_mode import RuntimeModeService

    class _BoomIdentMT5:
        current_account_source = None

        def __init__(self, account=None, password=None, server=None,
                     timeout=5000, retries=3):
            pass

        def configure_broker_identity(self, magic=None, bot_symbol=None):
            raise RuntimeError("identity stamp boom")

        def connect(self):
            return False

        def disconnect(self):
            pass

    class _Exec:
        mode = ExecutionMode.PAPER
        symbol = "XAUUSD"
        magic_number = 999101

    class _OM:
        adapter = None
        mt5_adapter = None

    eng = SimpleNamespace()
    eng.config = SimpleNamespace(execution=_Exec(), mt5=SimpleNamespace(
        account=None, password=None, server=None, timeout_ms=1000, retries=0),
        paper_data=None)
    eng.adapter = PaperMT5Adapter()
    eng.order_manager = _OM()
    eng._mode_override = None
    eng._runtime_mode = "PAPER"
    eng._last_balance = 10_000.0
    eng._invalidate_cross_mode_state = lambda *a, **k: None
    eng._update_runtime_mode = lambda *a, **k: None

    with patch(
        "nexus_scalp.adapters.mt5.mt5_adapter.DirectMT5Adapter",
        _BoomIdentMT5,
    ):
        res = RuntimeModeService.set_execution_mode(eng, ExecutionMode.LIVE)
    assert res["success"] is True  # legacy outcome: swap proceeds, error-isolated
    assert res["mode"] == "LIVE"
    assert eng.order_manager.mt5_adapter is not None
    msgs = _records(cap_capture, logging.ERROR)
    assert any(
        m.startswith("MODE_CONFIGURE_BROKER_IDENTITY_FAILED")
        and "identity stamp boom" in m
        and "STALE magic/symbol" in m
        for m in msgs
    ), msgs


# ---------------------------------------------------------------------------
# Lane A F4 - order_manager._run_protection_chain AI_FLIP_PROB_ASSEMBLY_FAILED
# ---------------------------------------------------------------------------


def _fresh_om() -> Any:
    """Minimal OrderLifecycleManager via __new__ + real state ledgers.

    ``_run_protection_chain`` touches exactly these owners each pass; every
    tracker starts empty and the seeded per-ticket telemetry keys reproduce
    the production ``__init__``/``_new_position_state`` initialization.
    """
    from nexus_scalp.execution import order_manager as om_mod

    om = om_mod.OrderLifecycleManager.__new__(om_mod.OrderLifecycleManager)
    om.algo_config = None
    om._states = om_mod.TicketStateStore()
    om._protection_ledger = om_mod.PositionProtectionLedger()
    om._tracking = om_mod.PositionTrackingLedger()
    om._telemetry = om_mod.TelemetryThrottle()
    om._hold_scores = om_mod.HoldScoreLedger()
    om._recovery_ledger = om_mod.RecoveryBudgetLedger()
    om._state_machine = om_mod.PositionStateMachine(lambda: (2.5, 10))
    om._tickets_cache = om_mod.TicketsCache()
    om._live_tickets_lock = threading.RLock()
    om._pending_lifecycle_instance = SimpleNamespace(drop_ticket=lambda t: None)
    om._time_in_profit_sec[777] = 0.0
    om._time_in_drawdown_sec[777] = 0.0
    om._peak_profit_usd[777] = 0.0
    om._peak_drawdown_usd[777] = 0.0
    return om


class _BoomProbs:
    def squeeze(self):
        raise ValueError("bad tensor shape")


class _Tick:
    bid = 1999.0
    ask = 2000.2


_XAUUSD = SymbolInfo(
    symbol="XAUUSD", digits=2, point=0.01, tick_size=0.01, tick_value=1.0,
    volume_min=0.01, volume_max=10.0, volume_step=0.01, stops_level=0,
    freeze_level=0, trade_contract_size=100.0)


def test_f4_ai_flip_prob_assembly_failure_is_error_logged(cap_capture):
    from nexus_scalp.execution.position_states import PositionState

    om = _fresh_om()
    pos = Position(
        ticket=777, symbol="XAUUSD", type=OrderType.BUY, volume=0.1,
        price_open=2000.0, sl=1990.0, tp=2020.0, profit=-5.0, magic=123)

    from nexus_scalp.execution.protection_ledger import PositionProtectionState

    prot = PositionProtectionState()
    prot.close_requested = True
    handled = om._run_protection_chain(
        pos=pos, ticket=777, now=__import__("datetime").datetime.now(
            __import__("datetime").UTC), current_time=1000.0,
        protection=prot, price_current=1999.0, hold_score=0, base_hold_score=0,
        invalidate_reasons=[], atr=1.0, spread=0.2, min_stop_gap=0.1,
        symbol_info=_XAUUSD, smart_metrics={}, evidence={}, pnl_features={},
        probs=_BoomProbs(), feature_vector=None, regime_state=None,
        current_tick=_Tick(), holding_duration=0.0,
        debounced_state=PositionState.LOSS_EARLY, prot_score=0)
    assert handled is True  # legacy outcome (close already accepted -> skip-rest)
    msgs = _records(cap_capture, logging.ERROR)
    assert any(
        m.startswith("AI_FLIP_PROB_ASSEMBLY_FAILED")
        and "ticket=777" in m
        and "bad tensor shape" in m
        for m in msgs
    ), msgs


# ---------------------------------------------------------------------------
# Lane A F5 - paper_adapter PAPER_SLTP_PROCESSING_FAILED (stdlib fallback)
# ---------------------------------------------------------------------------


def test_f5_paper_tick_processing_failure_falls_back_to_stdlib_logging(caplog, monkeypatch):
    """process_tick_execution raising AND obs get_logger raising must reach the
    stdlib fallback error; loyalty: the delegated tick is still returned."""
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter

    paper = PaperMT5Adapter()

    class _Market:
        def __init__(self, seed=None):
            pass  # production module is absent in-tree; simulated via sys.modules

        def next_tick(self, symbol):
            return {"bid": 2000.0, "ask": 2000.3, "volume": 1.0, "flags": 6}

    fake = ModuleType("nexus_scalp.market_data.paper_stress")
    fake.PaperStressMarket = _Market

    monkeypatch.setitem(sys.modules, "nexus_scalp.market_data.paper_stress", fake)
    with caplog.at_level(logging.ERROR):
        with patch.object(
            PaperMT5Adapter, "process_tick_execution",
            side_effect=RuntimeError("tick path boom"),
        ), patch(
            "nexus_scalp.observability.logging.get_logger",
            side_effect=RuntimeError("obs logger down"),
        ):
            tick = paper._get_last_tick_delegated("XAUUSD", seed=7)
    assert tick is not None  # legacy outcome: tick survives
    assert tick.bid == 2000.0
    msgs = [r.getMessage() for r in caplog.records]
    assert any(
        m.startswith("PAPER_SLTP_PROCESSING_FAILED (fallback)")
        and "tick path boom" in m
        and "XAUUSD" in m
        for m in msgs
    ), msgs


# ---------------------------------------------------------------------------
# Lane A F6 - gateway/server.py GATEWAY_SECRET_STORE_READ_FAILED
# ---------------------------------------------------------------------------


def test_f6_gateway_secret_store_failure_warns(cap_capture, monkeypatch):
    """SecureSecretStore raising must warn and fall through to env/defaults."""
    from nexus_scalp.gateway import server as srv

    class _Boom:
        def get_secret(self, name):
            raise ValueError("secret store probe raised")

    monkeypatch.delenv("NSE_GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("NSE_GATEWAY_SECRET", raising=False)
    monkeypatch.setenv("NSE_GATEWAY_ALLOW_DEFAULTS", "1")
    srv.set_allow_live(False)
    with patch(
        "nexus_scalp.settings.secret_store.SecureSecretStore", _Boom,
    ):
        key, secret = srv._expected_keys()
    assert key == srv.DEFAULT_API_KEY  # legacy outcome: defaults allowed when permitted
    assert secret == srv.DEFAULT_SECRET
    msgs = _records(cap_capture, logging.WARNING)
    assert any(
        m.startswith("GATEWAY_SECRET_STORE_READ_FAILED")
        and "secret store probe raised" in m
        for m in msgs
    ), msgs


# ---------------------------------------------------------------------------
# Lane A F7 - gateway/server.py GATEWAY_ACCOUNT_SNAPSHOT_PROBE_FAILED
# ---------------------------------------------------------------------------


def test_f7_gateway_demo_guard_probe_failure_fails_closed_loudly(cap_capture):
    """An unreadable account tier must fail closed (disconnect + raise) AND
    log GATEWAY_ACCOUNT_SNAPSHOT_PROBE_FAILED."""
    from nexus_scalp.gateway import server as srv

    srv.reset_adapter_for_tests()
    calls: dict[str, int] = {"connect": 0, "snap": 0, "disconnect": 0}

    class _Adapter:
        def connect(self):
            calls["connect"] += 1
            return True

        def get_account_snapshot(self):
            calls["snap"] += 1
            raise ValueError("snapshot probe raised")

        def disconnect(self):
            calls["disconnect"] += 1

    with patch(
        "nexus_scalp.adapters.mt5.mt5_adapter.DirectMT5Adapter", _Adapter,
    ):
        with pytest.raises(Exception):
            srv._get_adapter()
    # legacy outcomes: one connect, one probe attempt, fail-closed disconnect
    assert calls == {"connect": 1, "snap": 1, "disconnect": 1}
    assert srv.reset_adapter_for_tests() is None
    msgs = _records(cap_capture, logging.ERROR)
    assert any(
        m.startswith("GATEWAY_ACCOUNT_SNAPSHOT_PROBE_FAILED")
        and "snapshot probe raised" in m
        and "NOT VERIFIED" in m
        for m in msgs
    ), msgs


# ---------------------------------------------------------------------------
# Lane A F8 - order_manager._cleanup_ticket_state ADVISER_FORGET_FAILED
# ---------------------------------------------------------------------------


def test_f8_adviser_forget_failure_warns_rate_limited(cap_capture):
    """adviser.forget raising must warn ADVISER_FORGET_FAILED, stay
    non-fatal, and rate-limit an immediate repeat (no 2nd warn)."""
    from nexus_scalp.execution import order_manager as om_mod

    class _BoomForget:
        calls: list[int] = []

        def forget(self, ticket):
            _BoomForget.calls.append(ticket)
            raise RuntimeError("boom-from-forget")

    om = _fresh_om()
    om._position_adviser = _BoomForget()
    om._adviser_forget_warn_times = {}

    om._cleanup_ticket_state(555)
    om._cleanup_ticket_state(555)
    om._cleanup_ticket_state(555)
    msgs = _records(cap_capture, logging.WARNING)
    assert len([m for m in msgs if m.startswith("ADVISER_FORGET_FAILED")]) == 1, msgs
    assert len(_BoomForget.calls) == 3  # legacy outcome: non-fatal, still called


def test_f8_adviser_forget_success_produces_no_record(cap_capture):
    """The happy path must produce no ADVISER_FORGET_FAILED record."""
    from nexus_scalp.execution import order_manager as om_mod

    class _QuietAdviser:
        def forget(self, ticket):
            pass

    om = _fresh_om()
    om._position_adviser = _QuietAdviser()
    om._adviser_forget_warn_times = {}

    om._cleanup_ticket_state(1)
    msgs = _records(cap_capture, logging.WARNING)
    assert not any(m.startswith("ADVISER_FORGET_FAILED") for m in msgs), msgs
    assert om._adviser_forget_warn_times == {}


# ---------------------------------------------------------------------------
# Lane A F9 - order_manager._broker_close_verified CLOSE_VERIFICATION_UNAVAILABLE
# ---------------------------------------------------------------------------


def test_f9_broker_close_verification_unavailable_warns_and_trusts(cap_capture):
    """positions probe raising must warn CLOSE_VERIFICATION_UNAVAILABLE and
    trust the adapter's close result (legacy outcome: verified True)."""
    from nexus_scalp.execution import order_manager as om_mod

    class _BoomAdapter:
        def get_positions(self, symbol=None):
            raise RuntimeError("positions probe down")

    om = om_mod.OrderLifecycleManager.__new__(om_mod.OrderLifecycleManager)
    om.adapter = _BoomAdapter()
    verified = om._broker_close_verified(555)
    assert verified is True  # legacy outcome: trust, never treat as open
    msgs = _records(cap_capture, logging.WARNING)
    assert any(
        m.startswith("CLOSE_VERIFICATION_UNAVAILABLE")
        and "ticket=555" in m
        and "positions probe down" in m
        for m in msgs
    ), msgs


def test_f9_broker_close_verification_present_no_record(cap_capture):
    """A healthy probe that CONFIRMS absence must not warn."""
    from nexus_scalp.execution import order_manager as om_mod

    class _Adapter:
        def get_positions(self, symbol=None):
            return []

    om = om_mod.OrderLifecycleManager.__new__(om_mod.OrderLifecycleManager)
    om.adapter = _Adapter()
    verified = om._broker_close_verified(555)
    assert verified is True
    msgs = _records(cap_capture, logging.WARNING)
    assert not any(m.startswith("CLOSE_VERIFICATION_UNAVAILABLE") for m in msgs), msgs


# ---------------------------------------------------------------------------
# F9 negative: a live-but-not-gone position must NOT be reported verified
# ---------------------------------------------------------------------------


def test_f9_broker_close_verification_failed_open_when_live_position_seen(cap_capture):
    """If the probe succeeds and still sees the ticket, the close is NOT verified."""
    from nexus_scalp.execution import order_manager as om_mod

    class _StillLive:
        ticket = 555

    class _Adapter:
        def get_positions(self, symbol=None):
            return [_StillLive()]

    om = om_mod.OrderLifecycleManager.__new__(om_mod.OrderLifecycleManager)
    om.adapter = _Adapter()
    verified = om._broker_close_verified(555)
    assert verified is False
    msgs = _records(cap_capture, logging.WARNING)
    assert not any(m.startswith("CLOSE_VERIFICATION_UNAVAILABLE") for m in msgs), msgs
