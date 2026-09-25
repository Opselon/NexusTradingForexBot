"""Maintenance-window live-dispatch enforcement (ECON v1 phase 6, P1).

Integration tests proving the canonical predicate actually BLOCKS new
live entries at the ONE authoritative router (DispatchEngine.dispatch_order):

  1. inside the maintenance window -> new directional entry blocked
  2. outside the window -> entry proceeds to the next gate layer
  3. protective lifecycle actions are NEVER gated (CLOSE/MODIFY/CANCEL
     route through execute_lifecycle_action, untouched)
  4. the AI-reversal flip cannot bypass the guard (fresh entry gated)
  5. timezone behavior follows the canonical broker/server offset
     (BROKER_SERVER_UTC_OFFSET_MINUTES), never a local UTC hardcode
  6. missing decision timestamp fails closed (entry blocked)

The engine below stubs ONLY the composition-root surface the router reads
before the guard; the guard itself runs the REAL production predicate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.execution.lifecycle.dispatch import DispatchEngine, _is_directional_entry

# Canonical broker offset (GMT+3, providers.py truth).
OFFSET_MIN = 180  # 3h east of UTC


def _decision(action: ActionType, ts: datetime | None) -> SimpleNamespace:
    return SimpleNamespace(
        action=action,
        symbol="XAUUSD",
        proposed_entry=2400.0,
        stop_loss=2398.0,
        take_profit=2404.0,
        request_id=f"req-{ts}",
        generated_at=ts,
        confidence=0.9,
        regime="TRENDING",
    )


def _engine() -> tuple[DispatchEngine, dict[str, Any]]:
    """Router over a stub composition root. Only the pre-guard surface is
    stubbed: global_state + experience_engine + _processed_orders. The
    exposure/clamp layers are NOT reached when the guard blocks, so any
    test reaching them asserts via the recorded calls."""
    calls: dict[str, Any] = {"terminal": []}
    experience_engine = SimpleNamespace(
        ledger=SimpleNamespace(
            record_terminal_outcome=lambda outcome: (
                calls["terminal"].append({"state": outcome.decision_lifecycle, "outcome": outcome})
                or True
            )
        )
    )
    om = SimpleNamespace(
        global_state="ACTIVE",
        experience_engine=experience_engine,
        _processed_orders={},
        _resolve_entry_reason=lambda decision: "TEST",
        audit=SimpleNamespace(record_execution=lambda **kw: None, log_order=lambda **kw: None),
    )
    return DispatchEngine(om), calls, om


def _blocked_ts() -> datetime:
    """A timestamp that IS inside the window under the GMT+3 broker:
    20:10 UTC == 23:10 server (window 22:30..01:30 server)."""
    return datetime(2026, 9, 1, 20, 10, tzinfo=UTC)


def _open_ts() -> datetime:
    """12:00 UTC == 15:00 server: outside the window."""
    return datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _only_guard(monkeypatch: pytest.MonkeyPatch, om: Any) -> None:
    """Stub the layers AFTER the guard on the STUB composition root so a
    passed guard proceeds to a trivially-successful dispatch."""
    om._is_exposure_available = lambda symbol=None: True
    om._clamp_dispatch_volume = lambda volume, symbol=None: float(volume)
    om.register_entry_context = lambda **kw: None
    om.execute_order = lambda order: True
    om.mt5_adapter = SimpleNamespace(
        send_order=lambda order: True,
        execute_market_order=lambda **kw: True,
        place_pending_order=lambda **kw: True,
    )


# =============================================================================
# 1/2: blocked inside, allowed outside
# =============================================================================


def test_entry_inside_maintenance_window_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXUS_TEST_SERVER_OFFSET_MIN", str(OFFSET_MIN))
    eng, calls, om = _engine()
    _only_guard(monkeypatch, om)
    ok = eng.dispatch_order(_decision(ActionType.BUY_MARKET, _blocked_ts()), 0.1)
    assert ok is False
    # terminal outcome recorded so the experience ledger cannot hang
    assert calls["terminal"] and calls["terminal"][0]["state"] == "NOT_DISPATCHED"


def test_entry_outside_maintenance_window_passes_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXUS_TEST_SERVER_OFFSET_MIN", str(OFFSET_MIN))
    eng, calls, om = _engine()
    _only_guard(monkeypatch, om)
    ok = eng.dispatch_order(_decision(ActionType.BUY_MARKET, _open_ts()), 0.1)
    # the guard is the layer under test: outside the window it must NOT block
    assert ok is True
    assert not calls["terminal"]


def test_sell_entry_blocked_inside_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXUS_TEST_SERVER_OFFSET_MIN", str(OFFSET_MIN))
    eng, _, om = _engine()
    _only_guard(monkeypatch, om)
    assert eng.dispatch_order(_decision(ActionType.SELL_LIMIT, _blocked_ts()), 0.1) is False


# =============================================================================
# 3: protective actions never gated
# =============================================================================


@pytest.mark.parametrize(
    "action",
    [
        ActionType.CLOSE_POSITION,
        ActionType.PARTIAL_CLOSE,
        ActionType.MODIFY_SL_TP,
        ActionType.CANCEL_ORDER,
    ],
)
def test_lifecycle_actions_are_not_entries(action: ActionType) -> None:
    assert _is_directional_entry(action) is False


def test_close_position_routes_around_entry_guard() -> None:
    """A CLOSE_POSITION inside the window is not a new entry: the guard's
    action filter must not classify it as directional."""
    assert _is_directional_entry(ActionType.CLOSE_POSITION) is False


# =============================================================================
# 4: reversal flip cannot bypass
# =============================================================================


def test_reversal_flip_entry_is_gated_like_any_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """execute_ai_reversal closes first, then re-dispatches the flip through
    dispatch_order — so the fresh flip entry hits the same guard."""
    monkeypatch.setenv("NEXUS_TEST_SERVER_OFFSET_MIN", str(OFFSET_MIN))
    eng, _, om = _engine()
    _only_guard(monkeypatch, om)
    flip = _decision(ActionType.SELL_MARKET, _blocked_ts())
    assert eng.dispatch_order(flip, 0.5) is False  # flip is a directional entry


# =============================================================================
# 5: canonical time semantics (server offset), no UTC hardcode
# =============================================================================


def test_window_follows_canonical_server_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SAME instant is in-window for a GMT+3 broker and out for UTC:
    the guard derives from the canonical offset, not a fixed UTC hour."""
    from nexus_scalp.research.economics import in_maintenance_window

    ts = _blocked_ts()
    assert in_maintenance_window(ts, server_utc_offset_hours=OFFSET_MIN / 60.0)
    assert not in_maintenance_window(ts, server_utc_offset_hours=0.0)


def test_predicate_is_the_canonical_one() -> None:
    """The guard must consume research.economics — no second window impl."""
    import inspect

    from nexus_scalp.execution.lifecycle import dispatch as dispatch_mod

    src = inspect.getsource(dispatch_mod.DispatchEngine.dispatch_order)
    assert "in_maintenance_window" in src
    assert "BROKER_SERVER_UTC_OFFSET_MINUTES" in src
    # no hardcoded UTC window VALUES in executable code: strip comments and
    # docstrings, then the window strings must not appear.
    stripped = "\n".join(line.split("#")[0] for line in src.splitlines())
    assert "23:00" not in stripped
    assert "MAINTENANCE_WINDOW_START_SERVER" not in stripped  # imported, not redefined
    assert "in_maintenance_window(" in stripped


# =============================================================================
# 6: missing timestamp fails closed
# =============================================================================


def test_missing_decision_timestamp_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    eng, calls, om = _engine()
    _only_guard(monkeypatch, om)
    ok = eng.dispatch_order(_decision(ActionType.BUY_MARKET, None), 0.1)
    assert ok is False
    assert calls["terminal"]  # recorded, never silently dropped
