"""Calendar package: forward-looking economic event layer (market-context mission P0).

Public surface:
    EconomicEvent / CalendarEnvelope  — canonical contracts (ECON_EVENT v1)
    classify_event_title / is_obvious_high_impact — deterministic classifier
    FFCalendarProvider / FedFOMCProvider — verified data providers
    CalendarWorker — off-tick-path fetch + persistence + health
    EventGatePolicy / evaluate_event_window — configurable pre/post-event gate
    build_market_context — Phase-7 unification (news + calendar, separate provenance)
    weekly_liveness_report — Phase-1D observability aggregate
"""

from nexus_scalp.calendar.classify import (
    HighImpactEventType,
    classify_country_calendar_event,
    classify_event_title,
    is_obvious_high_impact,
)
from nexus_scalp.calendar.gate import (
    EventGatePolicy,
    EventGateState,
    EventGateVerdict,
    evaluate_event_window,
    next_window_open,
)
from nexus_scalp.calendar.models import (
    CALENDAR_MAX_STALE_SEC,
    EVENT_MODEL_VERSION,
    CalendarEnvelope,
    CalendarHealth,
    EconomicEvent,
    EventImportance,
    EventStatus,
    make_event_id,
)
from nexus_scalp.calendar.providers import (
    FedFOMCProvider,
    FFCalendarProvider,
    ProviderState,
)
from nexus_scalp.calendar.worker import (
    CalendarWorker,
    build_market_context,
    weekly_liveness_report,
)

__all__ = [
    "CALENDAR_MAX_STALE_SEC",
    "EVENT_MODEL_VERSION",
    "CalendarEnvelope",
    "CalendarHealth",
    "CalendarWorker",
    "EconomicEvent",
    "EventGatePolicy",
    "EventGateState",
    "EventGateVerdict",
    "EventImportance",
    "EventStatus",
    "FFCalendarProvider",
    "FedFOMCProvider",
    "HighImpactEventType",
    "ProviderState",
    "build_market_context",
    "classify_country_calendar_event",
    "classify_event_title",
    "evaluate_event_window",
    "is_obvious_high_impact",
    "make_event_id",
    "next_window_open",
    "weekly_liveness_report",
]
