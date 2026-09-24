"""
PURPOSE: Causal-core contract tests for the NSE Decision Trace v2 extension:
frozen optional causal fields + TraceState vocabulary on TraceEvent, their
round-trip through to_dict (None omitted = absence is data), threading through
trace_observer.begin_trace/emit (root/parent chain, duration, provenance
marking, secret redaction), observer failure isolation (BUG-311: a poisoned
payload must never raise into the caller) and the bounded rings.
OWNER: lane A of LIVE-CAUSAL-TOPOLOGY wave — future edits go to the
LIVE-CAUSAL-TOPOLOGY lane owner.
CONSUMES: src/nexus_scalp/observability/trace_contract.py (TraceEvent,
TraceState, TraceMode, TRACE_SCHEMA_VERSION, coerce_provenance),
src/nexus_scalp/observability/trace_observer.py (begin_trace/emit API).
PROVIDES: tests for v2 field round-trip, root/parent propagation,
inferred-marking, redaction, never-raise isolation, bounded buffers.
INVARIANTS: no fabricated data — omitted keys must stay omitted; schema version
stays 1; a causal edge without observed linkage is a provenance gap, never an
invented parent (§9/§56/§60).
EXTEND: add a new frozen field by appending it to TraceEvent (None default) +
its to_dict branch + _CAUSAL_V2_FIELDS + begin_trace/emit kwargs; never remove
or rename without a TRACE_SCHEMA_VERSION bump.
"""

from __future__ import annotations

from nexus_scalp.observability.trace_contract import (
    TRACE_SCHEMA_VERSION,
    TraceEvent,
    TraceMode,
    TraceState,
    coerce_provenance,
    compute_duration_ms,
)
from nexus_scalp.observability.trace_observer import TraceObserver, trace_observer

# The 15 frozen TraceState constants (brief §4 vocabulary).
_FROZEN_STATES = (
    "IDLE",
    "RECEIVED",
    "PROCESSING",
    "WAITING",
    "COMPLETED",
    "PASSED",
    "REJECTED",
    "FAILED",
    "BLOCKED",
    "SKIPPED",
    "TIMEOUT",
    "STALE",
    "CANCELLED",
    "EXECUTING",
    "CONFIRMED",
)

_FROZEN_MODES = ("LIVE", "PAPER", "SHADOW", "REPLAY", "BACKTEST", "TRAINING")


def _event(**kwargs) -> TraceEvent:
    base = dict(
        event_id="EV-00000001",
        trace_id="TRC-1",
        sequence=1,
        timestamp="2026-01-05T12:00:00.000+00:00",
        monotonic_ns=1,
        stage="MARKET",
        component="tick_pipeline",
        event_type="MARKET_EVENT",
        status="OBSERVED",
    )
    base.update(kwargs)
    return TraceEvent(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Contract vocabulary + schema version
# --------------------------------------------------------------------------


def test_schema_version_stays_1_for_additive_v2_fields():
    """Additive-only: the frozen v2 fields must not bump the schema version
    (any removal/rename later bumps to 2)."""
    assert TRACE_SCHEMA_VERSION == 1
    assert _event().trace_schema_version == 1


def test_trace_state_vocabulary_is_the_frozen_15():
    for name in _FROZEN_STATES:
        assert getattr(TraceState, name) == name, name
    assert {v for k, v in vars(TraceState).items() if k.isupper()} == set(_FROZEN_STATES)


def test_trace_mode_vocabulary_is_the_frozen_6():
    for name in _FROZEN_MODES:
        assert getattr(TraceMode, name) == name, name
    assert {v for k, v in vars(TraceMode).items() if k.isupper()} == set(_FROZEN_MODES)


# --------------------------------------------------------------------------
# to_dict round-trip: absence is data
# --------------------------------------------------------------------------


def test_v1_event_dict_unchanged_by_v2_fields():
    """A v1 event (no causal fields set) serializes to exactly the v1 key set
    — no new key ever appears with a fabricated default."""
    d = _event().to_dict()
    assert set(d) == {
        "event_id",
        "trace_id",
        "sequence",
        "timestamp",
        "monotonic_ns",
        "stage",
        "component",
        "event_type",
        "status",
        "terminal",
        "trace_schema_version",
    }


def test_v2_fields_round_trip_with_none_omitted():
    full = _event(
        request_id="REQ-1",
        root_event_id="EV-00000001",
        source="MARKET_TICK",
        destination="FEATURES",
        state=TraceState.RECEIVED,
        started_at="2026-01-05T12:00:00.000+00:00",
        completed_at="2026-01-05T12:00:00.250+00:00",
        duration_ms=250,
        reason_code="FEATURE_SNAPSHOT_VALID",
        error_code=None,
        mode=TraceMode.PAPER,
        provider="internal",
        model="nse-50d",
        position_id="POS-9",
        order_id="ORD-9",
        deal_id="DEAL-9",
        execution_id="EXEC-9",
        snapshot_id="SNAP-9",
        payload_summary={"fields": 42},
        freshness="FRESH",
        provenance="observed",
    )
    d = full.to_dict()
    # Present keys keep their values; the one None key stays OMITTED.
    assert d["request_id"] == "REQ-1"
    assert d["root_event_id"] == "EV-00000001"
    assert d["source"] == "MARKET_TICK"
    assert d["destination"] == "FEATURES"
    assert d["state"] == TraceState.RECEIVED
    assert d["duration_ms"] == 250
    assert d["reason_code"] == "FEATURE_SNAPSHOT_VALID"
    assert d["mode"] == TraceMode.PAPER
    assert d["payload_summary"] == {"fields": 42}
    assert d["freshness"] == "FRESH"
    assert d["provenance"] == "observed"
    assert "error_code" not in d, "None must be omitted, never rendered as null-default"

    # Round-trip: reconstructing from the dict yields the same event.
    again = TraceEvent(**d)  # type: ignore[arg-type]
    assert again.to_dict() == d
    assert again.request_id == full.request_id
    assert again.provenance == "observed"


def test_empty_optional_values_stay_omitted():
    """'' / {} are placeholders, not evidence — omitted like None."""
    d = _event(source="", state="", payload_summary={}, provenance="").to_dict()
    for key in ("source", "state", "payload_summary", "provenance"):
        assert key not in d, key


def test_provenance_only_observed_or_inferred():
    assert coerce_provenance("observed") == "observed"
    assert coerce_provenance("inferred") == "inferred"
    for bad in ("guessed", "timestamp", "", None, 5, True):
        assert coerce_provenance(bad) is None, bad
    # An invalid word is DROPPED at construction — never stored, never
    # silently promoted to 'observed'.
    assert "provenance" not in _event(provenance="guessed").to_dict()


def test_duration_derived_only_from_both_endpoints():
    # Both endpoints -> derived.
    e = _event(
        started_at="2026-01-05T12:00:00.000+00:00",
        completed_at="2026-01-05T12:00:00.480+00:00",
    )
    assert e.duration_ms == 480
    # One endpoint -> absent (no extrapolated span).
    one = _event(started_at="2026-01-05T12:00:00.000+00:00")
    assert one.duration_ms is None
    assert "duration_ms" not in one.to_dict()
    # Garbage endpoints -> absent, never an exception.
    assert _event(started_at="not-a-date", completed_at="also-not").duration_ms is None
    assert compute_duration_ms(None, "2026-01-05T12:00:00+00:00") is None
    # Caller-supplied duration wins (backend figure, not re-derived).
    assert _event(duration_ms=7, started_at="x", completed_at="y").duration_ms == 7


def test_payload_summary_is_sanitized_and_redacted():
    d = _event(
        payload_summary={
            "fields": 42,
            "Authorization": "Bearer abc",
            "api_key": "sk-live-123",
            "nested": {"password": "hunter2"},
        }
    ).to_dict()["payload_summary"]
    assert d["fields"] == 42
    assert d["Authorization"] == "***REDACTED***"
    assert d["api_key"] == "***REDACTED***"
    assert d["nested"]["password"] == "***REDACTED***"
    assert "abc" not in str(d) and "hunter2" not in str(d)


# --------------------------------------------------------------------------
# Observer: root/parent chain, duration, never-raise, bounded buffers
# --------------------------------------------------------------------------


def _observer() -> TraceObserver:
    obs = TraceObserver()
    obs.start_session()
    return obs


def test_begin_trace_stores_source_mode_and_root():
    obs = _observer()
    try:
        tid = obs.begin_trace(symbol="XAUUSD", source="MARKET_TICK", mode="PAPER")
        assert tid, "ACTIVE observer must open a trace"
        evs = obs.trace_bundle(tid)["events"]
        assert len(evs) == 1
        root = evs[0]
        assert root["stage"] == "MARKET"
        assert root["source"] == "MARKET_TICK"
        assert root["mode"] == "PAPER"
        # The first event is its own root; None keys never serialized.
        assert root["root_event_id"] == root["event_id"]
        assert "parent_event_id" not in root
        assert "provenance" not in root
    finally:
        obs.stop_session()


def test_emit_threads_causal_fields_and_parent_chain():
    obs = _observer()
    try:
        tid = obs.begin_trace(symbol="XAUUSD", source="MARKET_TICK")
        a = obs.emit(
            stage="INFERENCE",
            component="inference",
            event_type="PREDICTION",
            status="OBSERVED",
            request_id="REQ-77",
            provider="openrouter",
            model="qwen",
            started_at="2026-01-05T12:00:00.000+00:00",
            completed_at="2026-01-05T12:00:00.300+00:00",
            provenance="observed",
        )
        b = obs.emit(
            stage="DECISION",
            component="signal_policy",
            event_type="DECISION",
            status="REJECTED",
            reason_code="POLICY_EXPECTANCY_TOO_LOW",
            error_code=None,
            terminal=True,
        )
        evs = obs.trace_bundle(tid)["events"]
        root_id = evs[0]["event_id"]
        by_id = {e["event_id"]: e for e in evs}
        # parent = own last event, chain-only, never cross-trace.
        assert by_id[a]["parent_event_id"] == root_id
        assert by_id[b]["parent_event_id"] == a
        # root propagated to every event of the trace.
        assert all(e["root_event_id"] == root_id for e in evs)
        # causal fields round-trip; None stays omitted.
        assert by_id[a]["request_id"] == "REQ-77"
        assert by_id[a]["duration_ms"] == 300
        assert by_id[a]["provenance"] == "observed"
        assert by_id[b]["reason_code"] == "POLICY_EXPECTANCY_TOO_LOW"
        assert "error_code" not in by_id[b]
        # terminal closes the trace: no event may follow it.
        assert evs[-1]["event_id"] == b and evs[-1]["terminal"] is True
    finally:
        obs.stop_session()


def test_inferred_provenance_marked_not_silently_observed():
    obs = _observer()
    try:
        tid = obs.begin_trace(symbol="XAUUSD")
        inferred = obs.emit(
            stage="REGIME",
            component="regime_classifier",
            event_type="STATE",
            status="OBSERVED",
            provenance="inferred",
        )
        bogus = obs.emit(
            stage="POLICY",
            component="signal_policy",
            event_type="GATE",
            status="PASS",
            provenance="guessed",
        )
        evs = {e["event_id"]: e for e in obs.trace_bundle(tid)["events"]}
        assert evs[inferred]["provenance"] == "inferred"
        # An unprovable provenance word is dropped, not stored as observed.
        assert "provenance" not in evs[bogus]
    finally:
        obs.stop_session()


def test_root_never_leaks_across_traces():
    obs = _observer()
    try:
        t1 = obs.begin_trace(symbol="XAUUSD")
        t2 = obs.begin_trace(symbol="XAUUSD")
        e1 = obs.emit(
            stage="INFERENCE", component="c", event_type="e", status="OBSERVED", trace_id=t1
        )
        e2 = obs.emit(
            stage="INFERENCE", component="c", event_type="e", status="OBSERVED", trace_id=t2
        )
        b1 = {e["event_id"]: e for e in obs.trace_bundle(t1)["events"]}
        b2 = {e["event_id"]: e for e in obs.trace_bundle(t2)["events"]}
        root1 = b1[next(k for k in b1 if "parent_event_id" not in b1[k])]["event_id"]
        root2 = b2[next(k for k in b2 if "parent_event_id" not in b2[k])]["event_id"]
        assert root1 != root2
        assert b1[e1]["root_event_id"] == root1
        assert b2[e2]["root_event_id"] == root2
        # Each chain links only to its own trace's events.
        assert b2[e2]["parent_event_id"] in b2
        assert b2[e2]["parent_event_id"] not in b1
    finally:
        obs.stop_session()


def test_caller_named_root_wins_over_derivation():
    obs = _observer()
    try:
        tid = obs.begin_trace(symbol="XAUUSD")
        eid = obs.emit(
            stage="FEATURES",
            component="c",
            event_type="e",
            status="OBSERVED",
            root_event_id="EV-EXTERNAL-ROOT",
        )
        evs = {e["event_id"]: e for e in obs.trace_bundle(tid)["events"]}
        assert evs[eid]["root_event_id"] == "EV-EXTERNAL-ROOT"
    finally:
        obs.stop_session()


def test_poisoned_payload_never_raises_into_the_caller():
    """§71/§72 (BUG-311): observer failure is isolated — the trading caller
    gets an event id or '', never an exception."""

    class _Bomb:
        def __iter__(self):
            raise RuntimeError("serialization bomb")

        def keys(self):
            raise RuntimeError("serialization bomb")

        def __str__(self):
            raise RuntimeError("serialization bomb")

    obs = _observer()
    try:
        tid = obs.begin_trace(symbol="XAUUSD")
        eid = obs.emit(
            stage="POLICY",
            component="signal_policy",
            event_type="GATE_EVALUATION",
            status="PASS",
            detail={"boom": _Bomb()},  # type: ignore[arg-type]
            payload_summary={"boom": _Bomb()},  # type: ignore[arg-type]
            provenance="observed",
        )
        assert isinstance(eid, str) and eid
        # The chain is still intact and the trace usable afterwards.
        evs = obs.trace_bundle(tid)["events"]
        assert evs and evs[-1]["event_id"] == eid
        # Bogus argument types degrade, never raise.
        assert isinstance(
            obs.emit(
                stage="X",
                component="c",
                event_type="e",
                status="S",
                started_at=12345,  # type: ignore[arg-type]
                completed_at=object(),  # type: ignore[arg-type]
                duration_ms="soon",  # type: ignore[arg-type]
            ),
            str,
        )
        # OFF => immediate no-op returning '' (hot-path contract).
        obs.stop_session()
        assert obs.begin_trace(symbol="XAUUSD") == ""
        assert obs.emit(stage="X", component="c", event_type="e", status="S") == ""
    except Exception as exc:  # pragma: no cover - the contract under test
        raise AssertionError(f"observer raised into the caller: {exc!r}") from exc


def test_emit_decision_carries_trace_root():
    obs = _observer()
    try:
        tid = obs.begin_trace(symbol="XAUUSD")
        obs.emit(stage="INFERENCE", component="c", event_type="e", status="OBSERVED")
        obs.emit_decision(
            summary={"status": "REJECTED", "decision_id": "EXEC-T1", "trace_id": tid},
            terminal=True,
        )
        evs = obs.trace_bundle(tid)["events"]
        decision = next(e for e in evs if e["stage"] == "DECISION")
        root_id = evs[0]["event_id"]
        assert decision["root_event_id"] == root_id
        assert decision["parent_event_id"] == evs[-2]["event_id"]
    finally:
        obs.stop_session()


def test_buffers_stay_bounded_under_event_flood():
    """§63: no unbounded growth — rings cap at their declared capacity and
    the counters report what was retained (never a silent lie)."""
    obs = _observer()
    try:
        flood = 7000  # > _EVENTS_RING (5000)
        for _ in range(flood):
            obs.emit(
                stage="FEATURES",
                component="c",
                event_type="e",
                status="OBSERVED",
                symbol="XAUUSD",
            )
        snap = obs.snapshot()
        assert snap["events_retained"] <= snap["events_ring_capacity"]
        assert snap["counters"]["events_emitted"] == flood
        # Trace map bounded too: opening more traces than _TRACES_MAX keeps
        # only the newest ones.
        for _ in range(250):  # > _TRACES_MAX (200)
            obs.begin_trace(symbol="XAUUSD")
        assert len(obs._trace_events) <= 200  # bound under test (private by design)
        assert len(obs._trace_root) <= 200  # bound under test (private by design)
        # Resume semantics unchanged: a client that last saw seq 1 and lets the
        # ring evict history is told so (gap), and never replays events it
        # already has.
        replay_tail = obs.events_since(1, limit=10)
        assert all(e["sequence"] > 1 for e in replay_tail["events"])
        assert replay_tail["gap"] is True, "evicted history must be reported as a gap"
    finally:
        obs.stop_session()


def test_singleton_exposes_same_v2_surface():
    """The process-wide singleton used by the runtime accepts the frozen
    kwargs (it is the object every guarded call site binds to)."""
    assert trace_observer.status in {"OFF", "ACTIVE", "STARTING", "STOPPING", "ERROR"}
    import inspect

    emit_params = set(inspect.signature(trace_observer.emit).parameters)
    for name in (
        "request_id",
        "root_event_id",
        "source",
        "destination",
        "state",
        "started_at",
        "completed_at",
        "duration_ms",
        "reason_code",
        "error_code",
        "mode",
        "provider",
        "model",
        "position_id",
        "order_id",
        "deal_id",
        "execution_id",
        "snapshot_id",
        "payload_summary",
        "freshness",
        "provenance",
    ):
        assert name in emit_params, name
    begin_params = set(inspect.signature(trace_observer.begin_trace).parameters)
    for name in ("source", "mode", "state", "request_id", "provenance", "payload_summary"):
        assert name in begin_params, name
    # Calling with the frozen kwargs while OFF must be a no-op, not an error.
    assert trace_observer.begin_trace(symbol="XAUUSD", source="MARKET_TICK", mode="LIVE") == ""
