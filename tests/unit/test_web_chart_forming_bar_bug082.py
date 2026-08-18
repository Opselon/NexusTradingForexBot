"""
Regression tests — BUG-082: web layer serves the STILL-FORMING M1 minute as
is_complete=True (chart history + live state), while the engine aggregator
correctly treats it as forming (BUG-058 reseed semantics).

The feature vector itself is unaffected (the engine computes on completed
bars only), but every external consumer of /api/chart/history and
/api/live/state (UI overlays, forensic parity recomputes, any client that
reconstructs features from chart bars) sees the forming bar as final.

The stub engine forces the ENGINE_STATE fallback path (adapter returns no
broker history) so the route exercises the aggregator branch where the
forming bar must be flagged is_complete=False.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.web.server import create_app


class _StubAudit:
    def get_recent_predictions(self, limit: int = 40) -> list[Any]:
        return []

    def get_system_stats(self) -> dict[str, Any]:
        return {}


class _StubAggregator:
    """Aggregator with 60 completed bars + a forming bar at 'now'."""

    def __init__(self) -> None:
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        self.completed = [
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=now - timedelta(minutes=60 - i),
                open=2000.0 + i * 0.1,
                high=2000.0 + i * 0.1 + 0.8,
                low=2000.0 + i * 0.1 - 0.7,
                close=2000.0 + i * 0.1,
                tick_volume=100 + i,
                is_complete=True,
            )
            for i in range(60)
        ]
        self.forming = BarData(
            symbol="XAUUSD",
            timeframe="M1",
            timestamp=now,
            open=2006.0,
            high=2006.5,
            low=2005.8,
            close=2006.2,
            tick_volume=42,
            is_complete=False,
        )

    def get_completed_bars(self) -> list[BarData]:
        return list(self.completed)

    def get_current_forming_bar(self) -> BarData:
        return self.forming


class _StubBundleLock:
    def __enter__(self) -> _StubBundleLock:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


class _StubEngine:
    def __init__(self) -> None:
        self.aggregator = _StubAggregator()
        self.config = type(
            "Cfg", (), {"execution": type("E", (), {"symbol": "XAUUSD", "timeframe": "M1"})()}
        )()
        self.adapter = type("A", (), {"get_rate_history": lambda *a, **k: []})()
        self.audit = _StubAudit()
        self._bundle_lock = _StubBundleLock()
        self._last_fv = None
        self._last_probs = None
        self._last_proposal = None


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(engine_ref=_StubEngine()))


def test_chart_history_forming_minute_flag(client: TestClient) -> None:
    """BUG-082 regression: ENGINE_STATE path marks the forming bar is_complete=False."""
    resp = client.get("/api/chart/history")
    assert resp.status_code == 200
    payload = resp.json()
    bars = payload["bars"]
    assert bars, "expected bars in response"
    assert payload["source"] == "ENGINE_STATE"
    # 60 completed bars + 1 forming bar
    assert len(bars) == 61
    assert bars[-1]["is_complete"] is False, "the forming minute must NOT be marked complete"
    assert bars[-2]["is_complete"] is True
    assert bars[-1]["time"] == payload["bars"][-1]["time"]


def test_chart_history_forming_bar_not_in_completed_tail(client: TestClient) -> None:
    """The forming bar's OHLC must not be appended to the completed tail."""
    resp = client.get("/api/chart/history")
    bars = resp.json()["bars"]
    # last completed bar is index 59 (0-based); forming at index 60
    assert bars[59]["is_complete"] is True
    assert bars[60]["is_complete"] is False
