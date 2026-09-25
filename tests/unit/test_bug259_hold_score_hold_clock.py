"""BUG-259 regression: hold-score Time-in-Loss penalty clock domain.

The hold-value score's Time-in-Loss decay (up to -30) derived its
holding-duration denominator from the HOST WALL CLOCK
(``datetime.now()`` / ``datetime.now(UTC)``) in
execution/lifecycle/scoring.py, while the management loop already threads
the broker/tick-domain timestamp (``now = current_tick.timestamp``) into
every other time computation (order_manager.py manage loop,
_evaluate_minimum_loss_optimization, _arbitrate_decision — the G3 fix).
Any host-vs-tick skew corrupts the ``time_loss / holding_duration > 0.70``
gate: a host clock behind the tick domain near-zeroes the duration and
fires a spurious -30; the opposite skew inflates it and the penalty can
never fire. These tests pin the tick-threaded clock plus the conservative
no-``now`` fallback (G3 HOLD_AGE_FALLBACK pattern).

Test discipline (BUG-112/118, mirrors tests/unit/test_hold_time_fallback_g3.py):
the MODULE logger is monkeypatched — never caplog, never real time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest

import nexus_scalp.execution.lifecycle.scoring as scoring_module
from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import Position, TickData
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


def _make_om() -> OrderLifecycleManager:
    return OrderLifecycleManager(adapter=Mock(), audit_repo=None)


def _make_pos(ticket: int = 3001) -> Position:
    return Position(
        ticket=ticket,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=1.0,
        price_open=2330.0,
        sl=2320.0,
        tp=2350.0,
        profit=-50.0,
        magic=888101,
    )


def _seed(om: OrderLifecycleManager, entry_time: datetime) -> None:
    om._entry_timestamps[3001] = entry_time
    om._time_in_drawdown_sec[3001] = 16.0


def _score(om: OrderLifecycleManager, now: datetime | None) -> tuple[int, list[str]]:
    return om._calculate_hold_value_score(
        pos=_make_pos(),
        price_current=2330.0,
        features=None,
        impact_price_delta=0.25,
        atr=1.0,
        now=now,
    )


def test_tick_threaded_now_drives_penalty_not_wall_clock() -> None:
    """A 20s holding duration (per the TICK domain) with 16s in drawdown fires
    the -30 penalty. The old code derived the duration from the host wall
    clock; the new code derives it ONLY from the caller-threaded tick stamp."""
    om = _make_om()
    tick_now = datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC)
    _seed(om, tick_now - timedelta(seconds=20))
    score, reasons = _score(om, now=tick_now)
    assert score == 70, f"expected 100-30=70, got {score} ({reasons})"
    assert any("TIME_IN_LOSS_DECAY_PENALTY" in r for r in reasons)


def test_penalty_ratio_follows_tick_domain_exclusively() -> None:
    """Move tick now 10s forward (20s -> 30s duration): ratio 16/30 < 0.70 so
    the penalty disappears — proving the denominator is the tick domain, not
    the wall clock (pre-fix, the wall clock owned the denominator)."""
    om = _make_om()
    tick_now = datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC)
    _seed(om, tick_now - timedelta(seconds=20))
    score2, reasons2 = _score(om, now=tick_now + timedelta(seconds=10))
    assert score2 == 100, f"expected no penalty at 16/30s ratio, got {score2} ({reasons2})"
    assert not any("TIME_IN_LOSS_DECAY_PENALTY" in r for r in reasons2)


def test_no_now_conservative_zero_no_penalty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct callers without ``now`` (legacy signature): duration is
    conservatively 0.0 -> the >0.70 gate can never fire on wall-clock
    arithmetic (G3 conservative-zero semantics)."""
    probe = _LogProbe()
    monkeypatch.setattr(scoring_module, "logger", probe)
    om = _make_om()
    _seed(om, datetime.now(UTC) - timedelta(seconds=3600))
    score, reasons = _score(om, now=None)
    assert score == 100, f"expected no penalty without now, got {score} ({reasons})"
    assert not any("TIME_IN_LOSS_DECAY_PENALTY" in r for r in reasons)


def test_no_now_fallback_warns_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The conservative fallback is LOUD: exactly one HOLD_AGE_FALLBACK
    warning per 300s window (G3 rate-limit pattern), never per call."""
    probe = _LogProbe()
    monkeypatch.setattr(scoring_module, "logger", probe)
    om = _make_om()
    _seed(om, datetime.now(UTC) - timedelta(seconds=3600))
    for _ in range(5):
        _score(om, now=None)
    assert len(probe.warnings) == 1, (
        f"expected exactly 1 rate-limited warning, got {len(probe.warnings)}"
    )
    args, kwargs = probe.warnings[0]
    msg = " ".join(str(a) for a in args) + " " + str(kwargs)
    assert "HOLD_AGE_FALLBACK" in msg
    assert "3001" in msg  # ticket number present


def test_manage_loop_threads_tick_now_into_hold_score() -> None:
    """End-to-end: manage_active_positions threads now=tick.timestamp into the
    scoring engine; the ratio gate sees tick-domain durations."""
    om = _make_om()
    captured: dict[str, Any] = {}

    real_score = om._calculate_hold_value_score

    def spy(*args: Any, **kwargs: Any) -> tuple[int, list[str]]:
        captured["now"] = kwargs.get("now")
        return real_score(*args, **kwargs)

    om._calculate_hold_value_score = spy  # type: ignore[method-assign]

    tick_ts = datetime.now(UTC) - timedelta(seconds=30)
    tick = TickData(symbol="XAUUSD", timestamp=tick_ts, bid=2330.0, ask=2330.2, volume=1.0)
    om.adapter = MagicMock()
    om.adapter.get_positions.return_value = [_make_pos(3001)]
    try:
        om.manage_active_positions("XAUUSD", tick, probs=None)
    except Exception:
        # Later management stages may probe Mock adapter surfaces; the BUG-259
        # contract under test is ONLY which `now` reached hold scoring.
        pass

    assert captured.get("now") == tick_ts, (
        f"management loop must thread tick.timestamp into hold scoring, got {captured.get('now')!r}"
    )


def test_delegate_signature_forwards_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """The manager's compat delegate (_calculate_hold_value_score) forwards
    positional AND keyword forms to the scoring engine unchanged."""
    om = _make_om()
    seen: dict[str, Any] = {}
    real = om._scoring._calculate_hold_value_score

    def spy(*args: Any, **kwargs: Any) -> tuple[int, list[str]]:
        seen["kwargs"] = kwargs
        return real(*args, **kwargs)

    monkeypatch.setattr(om._scoring, "_calculate_hold_value_score", spy)
    probe = _LogProbe()
    monkeypatch.setattr(scoring_module, "logger", probe)
    stamp = datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC)
    om._calculate_hold_value_score(
        _make_pos(4002),
        2330.0,
        None,
        0.25,
        1.0,
        now=stamp,
    )
    assert seen["kwargs"].get("now") == stamp


def test_module_has_no_wall_clock_in_hold_score() -> None:
    """Static guard: the scoring module no longer derives hold durations from
    the wall clock (the BUG-259 defect shape must not regress)."""
    import inspect

    src = inspect.getsource(scoring_module.PositionScoringEngine._calculate_hold_value_score)
    assert "datetime.now" not in src, "hold-value score must never read the wall clock"
    assert "time.monotonic" in src or "now - entry_time" in src, (
        "duration must come from the threaded tick timestamp"
    )
