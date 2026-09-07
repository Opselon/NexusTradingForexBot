"""Focused tests: calendar layer (ECON_EVENT v1) + news drift monitor.

Market-context mission P0 (Phase 12: focused tests, not volume).
Run: ./.venv/Scripts/python.exe -m pytest tests/unit/test_market_context_calendar_drift.py -p no:cacheprovider
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nexus_scalp.calendar.classify import (
    classify_country_calendar_event,
    classify_event_title,
    is_obvious_high_impact,
)
from nexus_scalp.calendar.gate import (
    EventGatePolicy,
    EventGateState,
    evaluate_event_window,
    next_window_open,
)
from nexus_scalp.calendar.models import (
    CalendarEnvelope,
    CalendarHealth,
    EconomicEvent,
    EventImportance,
    EventStatus,
    make_event_id,
)
from nexus_scalp.calendar.providers import FedFOMCProvider, FFCalendarProvider
from nexus_scalp.calendar.worker import (
    CalendarWorker,
    build_market_context,
    weekly_liveness_report,
)
from nexus_scalp.news.database import NewsDatabase
from nexus_scalp.news.drift import DriftVerdict, news_drift_check

# ---------------------------------------------------------------------------
# Deterministic classifier (Phase 5A)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("USD Core CPI m/m rises", "core_cpi"),
        ("Nonfarm Payrolls plunge", "nfp"),
        ("Non-Farm Payrolls report", "nfp"),
        ("Employment Situation Summary", "nfp"),
        ("FOMC Statement and Federal Funds Rate Decision", "fomc_decision"),
        ("ECB Main Refinancing Rate", "ecb_decision"),
        ("Bank of England Governor speaks", None),
        ("Company opens new office in Ohio", None),
        ("Venmo tuition payments spike", None),
    ],
)
def test_classify_event_title(title: str, expected: str | None) -> None:
    match = classify_event_title(title)
    assert (match.event_type.value if match.event_type else None) == expected


def test_is_obvious_high_impact_strong_only() -> None:
    assert is_obvious_high_impact("CPI y/y above forecast")
    assert not is_obvious_high_impact("GDP second estimate")  # non-strong identity
    assert not is_obvious_high_impact("Local bakery opens")


def test_classify_country_event_importance_promotion() -> None:
    etype, imp = classify_country_calendar_event("Nonfarm Payrolls", "USD", "Medium")
    assert etype is not None and etype.value == "nfp"
    assert imp == EventImportance.HIGH  # strong identity promotes
    etype2, imp2 = classify_country_calendar_event("Retail Sales m/m", "EUR", "Medium")
    assert imp2 == EventImportance.MEDIUM  # non-strong keeps provider value


# ---------------------------------------------------------------------------
# Contracts (Phase 6A/6C)
# ---------------------------------------------------------------------------


def test_event_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        EconomicEvent(
            event_id="e1",
            event_type="cpi",
            title="CPI",
            country="USD",
            scheduled_at=datetime(2026, 9, 10, 12, 30),  # naive!
            importance=EventImportance.HIGH,
            source="probe",
        )


def test_make_event_id_deterministic() -> None:
    t = datetime(2026, 9, 10, 12, 30, tzinfo=UTC)
    a = make_event_id("ff", "CPI y/y", t)
    b = make_event_id("ff", "cpi  Y/Y  ", t)
    assert a == b  # normalized title + stamp
    c = make_event_id("ff", "CPI y/y", t + timedelta(minutes=1))
    assert a != c


def test_envelope_stale_semantics() -> None:
    now = datetime.now(UTC)
    fresh = CalendarEnvelope(
        provider="p",
        health=CalendarHealth.GOOD,
        last_success_at=now - timedelta(hours=2),
        events=(),
    )
    stale = CalendarEnvelope(
        provider="p",
        health=CalendarHealth.GOOD,
        last_success_at=now - timedelta(days=10),
        events=(),
    )
    never = CalendarEnvelope(provider="p", health=CalendarHealth.INVALID, events=())
    assert not fresh.is_stale()
    assert stale.is_stale()
    assert never.is_stale()  # never fetched => stale by definition


# ---------------------------------------------------------------------------
# Providers (parse contracts — offline, fixture-driven)
# ---------------------------------------------------------------------------


def test_ff_parse_fixture() -> None:
    p = FFCalendarProvider(url="http://unused")
    rows = [
        {
            "title": "Core CPI m/m",
            "country": "USD",
            "date": "2026-09-10T08:30:00-04:00",  # explicit provider offset
            "impact": "High",
            "forecast": "0.2%",
            "previous": "0.2%",
            "actual": "0.3%",
        },
        {"title": "", "country": "USD", "date": "2026-09-10T08:30:00-04:00"},  # dropped
    ]
    events = p._parse(rows, datetime.now(UTC))
    assert len(events) == 1
    ev = events[0]
    assert ev.scheduled_at.utcoffset() == timedelta(0)  # normalized to UTC
    assert ev.scheduled_at.hour == 12  # 08:30-04:00 == 12:30Z
    assert ev.status == EventStatus.RELEASED  # actual present
    assert ev.event_type == "core_cpi"


def test_fomc_parse_fixture_year_segmentation() -> None:
    p = FedFOMCProvider(url="http://unused")
    html = """
    <p><a href="#1">2026</a> | <a href="#2">2025</a> Future Year: <a href="#3">2027</a></p>
    <div class="panel-heading"><h4><a id="1">2026 FOMC Meetings</a></h4></div>
    <div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>January</strong></div>
      <div class="fomc-meeting__date">27-28</div></div>
    <div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>October</strong></div>
      <div class="fomc-meeting__date">27-28</div></div>
    <div class="panel-heading"><h4><a id="2">2025 FOMC Meetings</a></h4></div>
    <div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>March</strong></div>
      <div class="fomc-meeting__date">17-18</div></div>
    <div class="panel-heading"><h4><a id="3">2027 FOMC Meetings</a></h4></div>
    <div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>January</strong></div>
      <div class="fomc-meeting__date">26-27</div></div>
    """
    now = datetime(2026, 9, 7, tzinfo=UTC)
    events = p._parse(html, now)
    stamps = [e.scheduled_at for e in events]
    # last-day-of-meeting stamps only; 2025 (past) filtered by scheduled<=now
    assert datetime(2026, 10, 28, 18, 0, tzinfo=UTC) in stamps
    assert datetime(2027, 1, 27, 18, 0, tzinfo=UTC) in stamps
    assert all(s > now for s in stamps)
    assert all(s.year in (2026, 2027) for s in stamps)
    assert all(e.importance == EventImportance.HIGH for e in events)


# ---------------------------------------------------------------------------
# Event gate (Phase 6D/6E/9)
# ---------------------------------------------------------------------------


def _envelope_with_event(scheduled: datetime, *, health=CalendarHealth.GOOD) -> CalendarEnvelope:
    ev = EconomicEvent(
        event_id="ev_t",
        event_type="cpi",
        title="CPI y/y",
        country="USD",
        scheduled_at=scheduled,
        importance=EventImportance.HIGH,
        source="probe",
        retrieved_at=scheduled,
    )
    return CalendarEnvelope(
        provider="probe",
        health=health,
        last_success_at=scheduled - timedelta(minutes=5),
        events=(ev,),
    )


def test_gate_pre_and_post_windows() -> None:
    now = datetime.now(UTC)
    pre = evaluate_event_window(
        envelope=_envelope_with_event(now + timedelta(minutes=10)),
        now=now,
        policy=EventGatePolicy(pre_event_min=15, post_event_min=15),
    )
    assert pre.state == EventGateState.PRE_EVENT
    post = evaluate_event_window(
        envelope=_envelope_with_event(now - timedelta(minutes=10)),
        now=now,
        policy=EventGatePolicy(pre_event_min=15, post_event_min=15),
    )
    assert post.state == EventGateState.POST_EVENT
    clear = evaluate_event_window(
        envelope=_envelope_with_event(now + timedelta(hours=3)),
        now=now,
        policy=EventGatePolicy(),
    )
    assert clear.state == EventGateState.CLEAR


def test_gate_stale_calendar_fail_safe_modes() -> None:
    now = datetime.now(UTC)
    stale_env = CalendarEnvelope(
        provider="p",
        health=CalendarHealth.DEGRADED,
        last_success_at=now - timedelta(days=30),
        events=(),
    )
    blocked = evaluate_event_window(
        envelope=stale_env, now=now, policy=EventGatePolicy(stale_mode="block_high_impact")
    )
    assert blocked.state == EventGateState.UNKNOWN_RISK
    observed = evaluate_event_window(
        envelope=stale_env, now=now, policy=EventGatePolicy(stale_mode="observe")
    )
    assert observed.state == EventGateState.CLEAR
    assert observed.reason == "CALENDAR_STALE_OBSERVE"


def test_gate_ignores_low_impact_and_other_currencies() -> None:
    now = datetime.now(UTC)
    env = CalendarEnvelope(
        provider="p",
        health=CalendarHealth.GOOD,
        last_success_at=now,
        events=(
            EconomicEvent(
                event_id="e1",
                event_type="",
                title="Something",
                country="JPY",
                scheduled_at=now + timedelta(minutes=5),
                importance=EventImportance.HIGH,
                source="p",
                retrieved_at=now,
            ),
        ),
    )
    verdict = evaluate_event_window(
        envelope=env, now=now, policy=EventGatePolicy(currencies=("USD",))
    )
    assert verdict.state == EventGateState.CLEAR


def test_next_window_open() -> None:
    now = datetime.now(UTC)
    env = _envelope_with_event(now + timedelta(minutes=10))
    resume = next_window_open(envelope=env, now=now, policy=EventGatePolicy(post_event_min=15))
    assert resume is not None
    assert abs((resume - now).total_seconds() / 60.0 - 25.0) < 0.01  # 10 pre + event + 15 post


# ---------------------------------------------------------------------------
# Worker (throttle + persistence; live fetch NOT hit in unit tests)
# ---------------------------------------------------------------------------


def test_worker_throttle_and_persistence(tmp_path: Path) -> None:
    db = NewsDatabase(str(tmp_path / "news.db"))

    class StubProvider:
        provider_id = "stub"

        class _State:
            consecutive_failures = 0
            fetch_count = 1

            def to_dict(self) -> dict[str, object]:
                return {"provider_id": "stub", "fetch_count": self.fetch_count}

        state = _State()

        def fetch(self, now=None):
            return _envelope_with_event(datetime.now(UTC) + timedelta(minutes=30))

    worker = CalendarWorker(db, providers=[StubProvider()], refresh_interval_sec=900.0)
    assert worker.tick() is True
    assert worker.tick() is False  # throttled
    summary = worker.health_summary()
    assert summary["calendar_health"] == "GOOD"
    assert summary["event_count"] == 1
    with db._connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0]
        assert n == 1


def test_weekly_liveness_report_classifications(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    rows = [
        {
            "source_id": "alive",
            "last_success_at": (now - timedelta(minutes=5)).isoformat(),
            "consecutive_failures": 0,
            "healthy": 1,
        },
        {"source_id": "deadsrc", "last_success_at": "", "consecutive_failures": 118, "healthy": 0},
        {
            "source_id": "stalesrc",
            "last_success_at": (now - timedelta(days=5)).isoformat(),
            "consecutive_failures": 0,
            "healthy": 1,
        },
    ]
    report = weekly_liveness_report(news_db_health=rows, calendar_worker=None, now=now)
    by_id = {s["source_id"]: s["health"] for s in report["sources"]}
    assert by_id["alive"] == "ALIVE"
    assert by_id["deadsrc"] == "DEAD"
    assert by_id["stalesrc"] == "STALE"
    assert report["dead_sources"] == ["deadsrc"]


def test_build_market_context_separate_legs() -> None:
    ctx = build_market_context(news_context=None, calendar_worker=None)
    assert "generated_at" in ctx  # never raises on missing legs


# ---------------------------------------------------------------------------
# Drift monitor (Phase 2C)
# ---------------------------------------------------------------------------


def test_drift_insufficient_data_is_explicit() -> None:
    ref = [[0.0] * 70 for _ in range(10)]
    res = news_drift_check(reference_window=ref, current_window=[[0.0] * 70 for _ in range(10)])
    assert res.verdict == DriftVerdict.INSUFFICIENT_DATA
    assert "no action" in res.detail


def test_drift_identical_is_normal() -> None:
    ref = [[0.0] * 70 for _ in range(40)]
    res = news_drift_check(reference_window=ref, current_window=[[0.0] * 70 for _ in range(40)])
    assert res.verdict == DriftVerdict.NORMAL
    assert res.psi == 0.0


def test_drift_full_shift_is_critical() -> None:
    ref = [[0.0] * 70 for _ in range(40)]
    cur = [[0.0] * 50 + [1.5] * 10 + [0.0] * 10 for _ in range(40)]
    res = news_drift_check(reference_window=ref, current_window=cur)
    assert res.verdict == DriftVerdict.CRITICAL


def test_drift_mixed_reference_scales() -> None:
    import random

    rng = random.Random(7)
    ref: list[list[float]] = []
    for _ in range(100):
        block = [0.0] * 10
        if rng.random() < 0.2:
            block = [round(rng.uniform(0.0, 1.0), 3) for _ in range(10)]
        ref.append([0.0] * 50 + block + [0.0] * 10)
    same: list[list[float]] = []
    for _ in range(100):
        block = [0.0] * 10
        if rng.random() < 0.2:
            block = [round(rng.uniform(0.0, 1.0), 3) for _ in range(10)]
        same.append([0.0] * 50 + block + [0.0] * 10)
    shifted: list[list[float]] = []
    for _ in range(100):
        block = [0.0] * 10
        if rng.random() < 0.4:
            block = [round(rng.uniform(0.5, 1.5), 3) for _ in range(10)]
        shifted.append([0.0] * 50 + block + [0.0] * 10)
    r_same = news_drift_check(reference_window=ref, current_window=same)
    r_shift = news_drift_check(reference_window=ref, current_window=shifted)
    assert r_same.verdict == DriftVerdict.NORMAL
    assert r_shift.verdict == DriftVerdict.CRITICAL
    assert r_shift.psi > r_same.psi
