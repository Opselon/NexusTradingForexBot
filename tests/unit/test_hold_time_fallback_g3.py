"""G3 (audit rev2) — hold-age wall-clock fallback removed.

``OrderLifecycleManager._arbitrate_decision`` used to derive the hold age
from the HOST WALL CLOCK when the management loop did not thread ``now``:
``datetime.now(UTC) if entry_time.tzinfo else datetime.now()``. DST shifts
and NTP corrections jump that clock, so the computed age could go negative
or wildly large ("Age: -10781.6s") and corrupt every hold-time penalty.

Contract after the fix (TASK-AUDREV-G3-HOLD-CLOCK):

- ``now`` provided  -> numeric age, behavior UNCHANGED.
- ``now is None``   -> duration_sec = 0.0 (conservative: inside the 60s
  grace window, never a corrupted age) + a rate-limited WARNING with
  ``event=HOLD_AGE_FALLBACK`` naming the ticket, whether the entry is
  tracked, and whether entry_time is tz-aware.

Test discipline (BUG-112/118): the MODULE logger is monkeypatched — never
caplog (root logger capture), never real time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import Mock

import pytest

import nexus_scalp.execution.order_manager as om_module
from nexus_scalp.execution.order_manager import OrderLifecycleManager


class _LogProbe:
    """Stands in for the module structlog logger; records warning calls."""

    def __init__(self) -> None:
        self.warnings: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def warning(self, *args: Any, **kwargs: Any) -> None:
        self.warnings.append((args, kwargs))

    def debug(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        pass

    def info(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        pass

    def error(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        pass


def _make_manager() -> OrderLifecycleManager:
    """Real manager instance with inert adapters (no broker/audit I/O)."""
    return OrderLifecycleManager(adapter=Mock(), audit_repo=None)


def _call_arbitrate(
    manager: OrderLifecycleManager,
    ticket: int,
    *,
    now: datetime | None,
    entry_time: datetime | None,
) -> tuple[str, str]:
    """Invoke the arbitrage decision with the minimal state it touches."""
    if entry_time is not None:
        manager._entry_timestamps[ticket] = entry_time
    manager._initial_risks[ticket] = 50.0
    manager._recovery_ledger.reset(ticket) if hasattr(manager._recovery_ledger, "reset") else None
    return manager._arbitrate_decision(
        ticket=ticket,
        pos=Mock(),
        legacy_action="HOLD",
        legacy_scenario="S60_NONE",
        adaptive_state=None,
        current_pnl_usd=0.0,
        evidence={},
        now=now,
    )


def test_now_provided_numeric_behavior_unchanged() -> None:
    """now provided -> age is the numeric now-minus-entry delta, no warning."""
    om = _make_manager()
    probe = _LogProbe()
    monkey_logger = om_module
    monkey_logger.logger = probe  # restored by fixture below

    entry = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)
    now = entry + timedelta(seconds=600)
    action, scenario = _call_arbitrate(om, 9001, now=now, entry_time=entry)

    assert (action, scenario) == ("HOLD", "S60_DEFAULT_CONTROLLED_HOLD")
    assert probe.warnings == []


def test_now_none_entry_present_zero_age_and_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """now None + tracked entry -> conservative 0.0 + rate-limited WARNING."""
    om = _make_manager()
    probe = _LogProbe()
    monkeypatch.setattr(om_module, "logger", probe)

    entry = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)  # 600s ago (naive caller)
    action, _scenario = _call_arbitrate(om, 9002, now=None, entry_time=entry)

    assert action == "HOLD"  # 0.0 < 60s grace -> no time-based exit pressure
    assert len(probe.warnings) == 1
    args, kwargs = probe.warnings[0]
    msg = " ".join(str(a) for a in args) + " " + str(kwargs)
    assert "HOLD_AGE_FALLBACK" in msg
    assert "9002" in msg  # ticket number present

    # rate-limited: an immediate second decision must NOT re-warn
    _call_arbitrate(om, 9003, now=None, entry_time=entry)
    assert len(probe.warnings) == 1


def test_now_none_no_entry_zero_age_no_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """now None + untracked ticket -> 0.0 via the no-entry branch, no warning."""
    om = _make_manager()
    probe = _LogProbe()
    monkeypatch.setattr(om_module, "logger", probe)

    action, scenario = _call_arbitrate(om, 9004, now=None, entry_time=None)

    assert (action, scenario) == ("HOLD", "S60_DEFAULT_CONTROLLED_HOLD")
    assert probe.warnings == []
