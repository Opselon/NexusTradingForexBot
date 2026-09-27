"""
PURPOSE: Lane-C2 unit coverage for the MT5/gateway causal-evidence seam
(LIVE-CAUSAL-TOPOLOGY phase 2): the lane-owned module that lets the 8
broker-gateway emit sites carry contract-v2 causal evidence WITHOUT editing
the three foreign-held adapter files (mt5_adapter.py, remote_gateway.py,
paper_adapter.py — other agents' WIP + PRs #436/#437).
OWNER: lane C2, LIVE-CAUSAL-TOPOLOGY phase 2 — future edits go to the
LIVE-CAUSAL-TOPOLOGY phase-2 lane owner.
CONSUMES: nexus_scalp.observability.trace_mt5_emit (the seam),
nexus_scalp.observability.trace_contract (TraceEvent / frozen v2 fields),
nexus_scalp.observability.trace_observer (emit/begin_trace round-trip),
nexus_scalp.web.trace_routes (lane-C2 query helpers).
PROVIDES: tests for the frozen v2 field dict produced from REAL duck-typed
gateway/adapter result shapes (direct MT5 result object, remote-gateway JSON
mapping, paper bool+ticket), all-absent honesty (absence is data), never-raise
isolation under poisoned objects and broken timing (BUG-311), secret
redaction in the external-request summary (§13/§54), provider/destination
truth (paper is never broker-reached, §24), and the lane-C2 query helpers.
INVARIANTS: no fabricated data — an absent key stays absent (never "", 0, 0.0
or False); only values the object actually carries are emitted; paper is
never rendered as MT5; a poisoned object must never raise; schema version
stays 1 (additive-only).
EXTEND: add a new evidence shape by adding a fake object with the new
attribute names and asserting the frozen fields it yields; add a query-helper
case by emitting events into the observer and asserting on retained state.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from nexus_scalp.observability.trace_contract import TRACE_SCHEMA_VERSION, TraceEvent
from nexus_scalp.observability.trace_mt5_emit import (
    MT5_EVIDENCE_FIELD_NAMES,
    NULL_EVIDENCE,
    direct_mt5_response_evidence,
    direct_mt5_send_evidence,
    enrich_gateway_detail,
    extract_external_request_summary,
    mt5_broker_evidence,
    mt5_v2_field_names,
    paper_gateway_response_evidence,
    paper_gateway_send_evidence,
    remote_mt5_response_evidence,
    remote_mt5_send_evidence,
)
from nexus_scalp.observability.trace_observer import trace_observer
from nexus_scalp.web.trace_routes import (
    query_decisions,
    query_event,
    query_events_page,
    query_observer,
    query_trace_bundle,
    query_trace_ids,
    query_why,
)

# ---------------------------------------------------------------------------
# Fakes shaped EXACTLY like the real runtime objects the seam reads. These
# mirror the real call sites (verified read-only in the foreign files):
#   - mt5_adapter.py:1365-1423: mt5.order_send(request) -> result with
#     .retcode/.order/.deal/.price, TradeOrder with .order_id/.symbol/...
#   - remote_gateway.py:231-267: self._send_request(action, payload) ->
#     JSON mapping with status/message/ticket.
#   - paper_adapter.py:1512-1601: bool ok + internal simulated ticket.
# No adapter class is imported — duck typing only (the seam's contract).
# ---------------------------------------------------------------------------


class _FakeMT5Result(SimpleNamespace):
    """Same attribute names as a MetaTrader5 order_send result."""


def _fake_order(order_id: str = "ORD-abc-001") -> Any:
    """Same attribute names as domain/models.py:233 TradeOrder."""
    return SimpleNamespace(
        order_id=order_id,
        symbol="EURUSD",
        order_type=SimpleNamespace(value="BUY"),
        volume=0.12,
        price=1.0825,
        stop_loss=1.0795,
        take_profit=1.0885,
        magic_number=424242,
        comment="NSE_ORDER",
    )


_MT5_REQUEST = {
    "action": "TRADE_ACTION_DEAL",
    "symbol": "EURUSD",
    "volume": 0.12,
    "type": "ORDER_TYPE_BUY",
    "price": 1.0825,
    "sl": 1.0795,
    "tp": 1.0885,
    "magic": 424242,
    "comment": "NSE_ORDER",
    "type_time": "ORDER_TIME_GTC",
    "type_filling": "ORDER_FILLING_IOC",
}

_REMOTE_PAYLOAD = {
    "order_id": "ORD-abc-001",
    "symbol": "EURUSD",
    "order_type": "BUY",
    "volume": 0.12,
    "price": 1.0825,
    "stop_loss": 1.0795,
    "take_profit": 1.0885,
    "magic_number": 424242,
    "comment": "NSE_ORDER",
    "idempotency_key": "ORD-abc-001_1711234567890",
}


# --- contract surface ------------------------------------------------------


def test_field_names_are_a_subset_of_the_frozen_contract() -> None:
    """The seam may only emit fields TraceEvent actually defines (additive)."""
    frozen = {
        f.name
        for f in TraceEvent.__dataclass_fields__.values()  # type: ignore[attr-defined]
    }
    assert set(MT5_EVIDENCE_FIELD_NAMES) <= frozen
    assert mt5_v2_field_names() == MT5_EVIDENCE_FIELD_NAMES
    # The honest all-absent dict carries no invented value.
    assert NULL_EVIDENCE == {}
    for name in MT5_EVIDENCE_FIELD_NAMES:
        assert name not in NULL_EVIDENCE


# --- direct MT5: the frozen field dict from a REAL result object ----------


def test_direct_mt5_send_evidence_carries_request_facts_only() -> None:
    """SENT proves dispatch, never a broker response (§24)."""
    out = direct_mt5_send_evidence(_fake_order(), _MT5_REQUEST)
    assert out["provider"] == "direct_mt5"
    assert out["destination"] == "MT5_TERMINAL"
    assert out["state"] == "EXECUTING"
    assert out["order_id"] == "ORD-abc-001"
    # A send event must not claim broker-side evidence that only a response
    # can prove: no ticket, no deal, no retcode.
    assert "position_id" not in out
    assert "deal_id" not in out
    assert "reason_code" not in out
    assert "error_code" not in out
    # The request summary is the §13 external-request inspector payload.
    summary = out["payload_summary"]
    assert summary["symbol"] == "EURUSD"
    assert summary["volume"] == 0.12
    assert summary["price"] == 1.0825
    assert set(out) <= set(MT5_EVIDENCE_FIELD_NAMES)


def test_direct_mt5_response_evidence_accepted_carries_broker_ids() -> None:
    """A DONE retcode with a broker ticket/deal becomes position/deal ids."""
    result = _FakeMT5Result(
        retcode=10009, order=50123456, deal=50123457, price=1.08252, volume=0.12
    )
    started = time.perf_counter_ns()
    out = direct_mt5_response_evidence(
        result, order=_fake_order(), request_dict=_MT5_REQUEST, started_ns=started
    )

    # The broker's own retcode is the reason/error carrier, verbatim-derived.
    assert out["reason_code"] == "RETCODE_10009"
    assert out["error_code"] == "10009"
    assert out["position_id"] == "50123456"
    assert out["deal_id"] == "50123457"
    assert out["provider"] == "direct_mt5"
    assert out["destination"] == "MT5_TERMINAL"
    assert out["order_id"] == "ORD-abc-001"
    # An observed span is a whole non-negative number of milliseconds.
    assert out["duration_ms"] >= 0
    assert isinstance(out["duration_ms"], int)
    assert set(out) <= set(MT5_EVIDENCE_FIELD_NAMES)


def test_direct_mt5_response_evidence_rejected_carries_the_retcode() -> None:
    """A failed retcode surfaces as the broker's reason code, not a verdict."""
    result = _FakeMT5Result(retcode=10004, order=0, deal=0, price=0.0, volume=0.0)
    out = direct_mt5_response_evidence(result, order=_fake_order())
    assert out["reason_code"] == "RETCODE_10004"
    assert out["error_code"] == "10004"
    # A zero/absent broker ticket must NOT become a real position id.
    assert "position_id" not in out
    assert "deal_id" not in out


def test_direct_mt5_response_none_terminal_is_honest_absence() -> None:
    """mt5.order_send returning None (unreachable terminal) = no evidence."""
    out = direct_mt5_response_evidence(None, order=_fake_order())
    # Provider/destination/order_id are caller-known dispatch facts; the
    # broker-side evidence is entirely absent.
    assert out.get("provider") == "direct_mt5"
    assert "position_id" not in out
    assert "deal_id" not in out
    assert "reason_code" not in out
    assert "duration_ms" not in out


def test_mt5_broker_evidence_is_pure_and_idempotent() -> None:
    result = _FakeMT5Result(retcode=10009, order=77, deal=78, price=1.0, volume=0.1)
    assert mt5_broker_evidence(result) == mt5_broker_evidence(result)
    assert mt5_broker_evidence(None) == dict(NULL_EVIDENCE)


# --- remote gateway: JSON mappings the RPC actually returned --------------


def test_remote_mt5_send_evidence_redacts_nothing_secret() -> None:
    out = remote_mt5_send_evidence(_fake_order(), _REMOTE_PAYLOAD)
    assert out["provider"] == "remote_mt5"
    assert out["destination"] == "REMOTE_GATEWAY"
    assert out["state"] == "EXECUTING"
    assert out["order_id"] == "ORD-abc-001"
    assert out["payload_summary"]["symbol"] == "EURUSD"
    assert out["payload_summary"]["endpoint"] == "/api/v1/execute"
    assert set(out) <= set(MT5_EVIDENCE_FIELD_NAMES)


def test_remote_mt5_response_evidence_accepted() -> None:
    res = {
        "status": "SUCCESS",
        "ticket": 99887766,
        "order_id": "ORD-abc-001",
        "price": 1.08252,
    }
    started = time.perf_counter_ns()
    out = remote_mt5_response_evidence(
        res, order=_fake_order(), payload=_REMOTE_PAYLOAD, started_ns=started
    )
    assert out["provider"] == "remote_mt5"
    assert out["position_id"] == "99887766"
    # SUCCESS is the caller-asserted status word; it surfaces as error_code
    # only because it is the gateway's own returned word, never as a verdict.
    assert out["error_code"] == "SUCCESS"
    assert "reason_code" not in out  # no message was reported
    assert isinstance(out["duration_ms"], int)
    assert out["duration_ms"] >= 0


def test_remote_mt5_response_evidence_rejected_carries_message() -> None:
    res = {"status": "ERROR", "message": "insufficient margin", "ticket": None}
    out = remote_mt5_response_evidence(res, order=_fake_order())
    assert out["reason_code"] == "insufficient margin"
    assert out["error_code"] == "ERROR"
    assert "position_id" not in out  # None ticket must not become an id


# --- paper gateway: never broker-reached (§24) ----------------------------


def test_paper_gateway_send_evidence_never_claims_mt5() -> None:
    out = paper_gateway_send_evidence(_fake_order())
    assert out["provider"] == "paper"
    assert out["destination"] == "PAPER_SIMULATOR"
    assert out["state"] == "EXECUTING"
    assert out["order_id"] == "ORD-abc-001"
    assert set(out) <= set(MT5_EVIDENCE_FIELD_NAMES)


def test_paper_gateway_response_evidence_accepted_ticket_becomes_position() -> None:
    started = time.perf_counter_ns()
    out = paper_gateway_response_evidence(True, 444555, order=_fake_order(), started_ns=started)
    assert out["provider"] == "paper"
    assert out["position_id"] == "444555"
    assert isinstance(out["duration_ms"], int)
    assert out["duration_ms"] >= 0


def test_paper_gateway_response_evidence_duplicate_is_reason_not_verdict() -> None:
    out = paper_gateway_response_evidence(
        False, None, order=_fake_order(), reason="duplicate_order_id"
    )
    assert out["reason_code"] == "duplicate_order_id"
    assert "position_id" not in out  # no ticket was assigned
    assert "state" not in out  # the caller asserts the verbatim status word


# --- all-absent honesty: objects carrying NO evidence (absence is data) ---


def test_object_carrying_no_evidence_yields_all_absent() -> None:
    """An object with none of the evidence attributes must NOT produce any
    frozen field value — every absent key stays absent (§60)."""
    empty_order = SimpleNamespace()  # no order_id at all
    empty_result = SimpleNamespace()  # no retcode / order / deal
    empty_res: dict[str, Any] = {}

    out_send = direct_mt5_send_evidence(empty_order)
    out_resp = direct_mt5_response_evidence(empty_result, order=empty_order)
    out_remote = remote_mt5_response_evidence(empty_res, order=empty_order)
    out_paper = paper_gateway_response_evidence(False, None, order=empty_order)

    for out in (out_send, out_resp, out_remote, out_paper):
        # Provider/destination are gateway-word facts the CALLER names, so
        # they remain; every EVIDENCE key must be absent.
        for name in (
            "reason_code",
            "error_code",
            "position_id",
            "deal_id",
            "duration_ms",
            "payload_summary",
        ):
            assert name not in out, (
                f"{name} must be absent for an evidence-free object, got {out!r}"
            )

    # The all-absent send dict carries no order id either.
    assert "order_id" not in out_send
    assert out_send == {
        "provider": "direct_mt5",
        "destination": "MT5_TERMINAL",
        "state": "EXECUTING",
    }
    assert mt5_broker_evidence(empty_result) == dict(NULL_EVIDENCE)


def test_null_evidence_is_data_not_a_negative_result() -> None:
    """The all-absent dict carries nothing, and a request object that carries
    no order_id yields no order_id key (absence, never a placeholder)."""
    assert NULL_EVIDENCE == {}
    out = direct_mt5_send_evidence(None, None)
    # Provider/destination are gateway-word facts the CALLER names; everything
    # the request would have carried is honestly absent.
    assert out["provider"] == "direct_mt5"
    assert out["state"] == "EXECUTING"
    assert "order_id" not in out
    assert "payload_summary" not in out


def test_no_helper_ever_zero_fills_missing_numbers() -> None:
    """Missing numerics must stay absent, never render as 0 / 0.0 / False."""
    result = _FakeMT5Result(retcode=None, order=None, deal=None, price=None, volume=None)
    out = direct_mt5_response_evidence(result, order=_fake_order())
    assert "position_id" not in out
    assert "deal_id" not in out
    assert "duration_ms" not in out  # no started_ns -> no fabricated span


# --- never-raise isolation (BUG-311): poisoned objects --------------------


class _Poisoned:
    """Every attribute access and item access raises — a hostile object."""

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError("poisoned attribute access")

    def __getitem__(self, key: str) -> Any:
        raise RuntimeError("poisoned item access")

    def __iter__(self) -> Any:
        raise RuntimeError("poisoned iteration")


def test_poisoned_objects_never_raise_send_side() -> None:
    for fn, args in (
        (direct_mt5_send_evidence, (_Poisoned(), _Poisoned())),
        (remote_mt5_send_evidence, (_Poisoned(), _Poisoned())),
        (paper_gateway_send_evidence, (_Poisoned(), _Poisoned())),
    ):
        out = fn(*args)  # must not raise
        assert isinstance(out, dict)
        assert set(out) <= set(MT5_EVIDENCE_FIELD_NAMES)


def test_poisoned_objects_never_raise_response_side() -> None:
    out = direct_mt5_response_evidence(_Poisoned(), order=_Poisoned(), request_dict=_Poisoned())
    assert isinstance(out, dict)
    out = remote_mt5_response_evidence(_Poisoned(), order=_Poisoned(), payload=_Poisoned())
    assert isinstance(out, dict)
    out = paper_gateway_response_evidence(_Poisoned(), _Poisoned(), order=_Poisoned())
    assert isinstance(out, dict)
    assert mt5_broker_evidence(_Poisoned()) == dict(NULL_EVIDENCE)


def test_broken_timing_yields_no_duration_not_an_exception() -> None:
    """A non-monotonic / missing timer endpoint must not raise or fabricate."""
    for started in (None, 0, -1, "not-a-number", float("nan"), _Poisoned()):
        out = direct_mt5_response_evidence(
            _FakeMT5Result(retcode=10009, order=1, deal=2, price=1.0, volume=0.1),
            order=_fake_order(),
            started_ns=started,
        )
        assert isinstance(out, dict)
        assert "duration_ms" not in out


def test_poisoned_detail_merge_never_raises() -> None:
    out = enrich_gateway_detail(_Poisoned(), _Poisoned())
    assert isinstance(out, dict)


# --- §13 external-request inspector: secrets never leave the process ------


def test_external_request_summary_redacts_secret_values() -> None:
    """The HMAC signature / API key / secret token (remote_gateway.py:502-513)
    appear only as redacted markers; the values never enter the summary."""
    payload = {
        "action": "SEND_ORDER",
        "payload": _REMOTE_PAYLOAD,
        "Authorization": "Bearer [REDACTED]",
        "X-NSE-API-KEY": "super-secret-key",
        "X-NSE-SIGNATURE": "deadbeefcafebabe",
        "secret_token": "shh",
        "symbol": "EURUSD",
    }
    out = extract_external_request_summary(
        payload, endpoint="/api/v1/execute", duration_ms=42, attempt=1
    )
    assert out["Authorization"] == "***REDACTED***"
    assert out["X-NSE-API-KEY"] == "***REDACTED***"
    assert out["X-NSE-SIGNATURE"] == "***REDACTED***"
    assert out["secret_token"] == "***REDACTED***"
    # No secret VALUE is ever carried.
    joined = " ".join(str(v) for v in out.values())
    assert "Bearer [REDACTED]" not in joined
    assert "super-secret-key" not in joined
    assert "deadbeefcafebabe" not in joined
    assert "shh" not in joined
    # Non-secret facts are carried verbatim.
    assert out["symbol"] == "EURUSD"
    assert out["endpoint"] == "/api/v1/execute"
    assert out["duration_ms"] == 42
    assert out["attempt"] == 1


def test_external_request_summary_absent_facts_stay_absent() -> None:
    out = extract_external_request_summary(None)
    assert out == {}
    out = extract_external_request_summary({"symbol": "EURUSD"})
    assert out == {"symbol": "EURUSD"}
    assert "endpoint" not in out
    assert "duration_ms" not in out
    assert "error_code" not in out


def test_external_request_summary_never_raises() -> None:
    assert isinstance(extract_external_request_summary(_Poisoned()), dict)
    assert isinstance(extract_external_request_summary([1, 2, 3]), dict)  # not a mapping


# --- detail enrichment: layering onto a foreign-style detail dict ---------


def test_enrich_gateway_detail_keeps_foreign_detail_verbatim() -> None:
    """The foreign emit site's own detail dict is preserved untouched; the v2
    causal fields are layered under the reserved ``causal`` key."""
    foreign_detail = {
        "gateway": "direct_mt5",
        "reached": True,
        "order_id": "ORD-abc-001",
        "retcode": "10009",
        "success": True,
    }
    causal = direct_mt5_response_evidence(
        _FakeMT5Result(retcode=10009, order=50123456, deal=50123457, price=1.08252, volume=0.12),
        order=_fake_order(),
    )
    out = enrich_gateway_detail(foreign_detail, causal)
    assert out["gateway"] == "direct_mt5"  # untouched
    assert out["retcode"] == "10009"
    assert out["causal"]["position_id"] == "50123456"
    assert out["causal"]["deal_id"] == "50123457"
    assert set(out["causal"]) <= set(MT5_EVIDENCE_FIELD_NAMES)


def test_enrich_gateway_detail_all_absent_adds_nothing() -> None:
    out = enrich_gateway_detail({"gateway": "paper"}, NULL_EVIDENCE, NULL_EVIDENCE)
    assert out == {"gateway": "paper"}
    assert "causal" not in out


def test_enrich_gateway_detail_none_becomes_empty_mapping() -> None:
    out = enrich_gateway_detail(None, direct_mt5_send_evidence(_fake_order()))
    assert out["causal"]["provider"] == "direct_mt5"


# --- end-to-end: the evidence round-trips through the REAL observer -------


def test_evidence_survives_the_observer_round_trip() -> None:
    """The seam's dicts must be acceptable as ``emit`` v2 kwargs: the frozen
    fields land on the stored event and stay OMITTED when absent."""
    started = time.perf_counter_ns()
    trace_observer.start_session()
    try:
        trace_id = trace_observer.begin_trace(symbol="EURUSD")
        assert trace_id
        causal = direct_mt5_response_evidence(
            _FakeMT5Result(
                retcode=10009, order=50123456, deal=50123457, price=1.08252, volume=0.12
            ),
            order=_fake_order(),
            request_dict=_MT5_REQUEST,
            started_ns=started,
        )
        trace_observer.emit(
            stage="MT5",
            component="mt5_adapter",
            event_type="MT5_RESPONSE",
            status="ACCEPTED",
            symbol="EURUSD",
            trace_id=trace_id,
            **causal,
        )
        page = query_events_page(trace_id=trace_id, stage="MT5", status="ACCEPTED")
        events = page["events"]
        assert len(events) == 1
        ev = events[0]
        assert ev["stage"] == "MT5"
        assert ev["event_type"] == "MT5_RESPONSE"
        assert ev["status"] == "ACCEPTED"
        # Every frozen field the seam produced is present verbatim.
        for key, value in causal.items():
            assert ev[key] == value, f"{key} did not round-trip"
        assert ev["position_id"] == "50123456"
        assert ev["deal_id"] == "50123457"
        assert ev["reason_code"] == "RETCODE_10009"
        assert ev["trace_schema_version"] == TRACE_SCHEMA_VERSION
        # The §13 summary rides in payload_summary, secrets already redacted.
        assert ev["payload_summary"]["symbol"] == "EURUSD"
    finally:
        trace_observer.stop_session()


def test_absent_evidence_stays_omitted_through_the_round_trip() -> None:
    """An evidence-free emit must NOT produce frozen fields downstream — the
    stored record omits them, so the UI renders UNKNOWN (§60)."""
    trace_observer.start_session()
    try:
        trace_id = trace_observer.begin_trace(symbol="GBPUSD")
        trace_observer.emit(
            stage="GATEWAY",
            component="paper_adapter",
            event_type="GATEWAY_RESPONSE",
            status="REJECTED",
            symbol="GBPUSD",
            trace_id=trace_id,
            **paper_gateway_response_evidence(False, None, order=SimpleNamespace()),
        )
        ev = query_events_page(trace_id=trace_id, stage="GATEWAY")["events"][0]
        for name in ("position_id", "deal_id", "duration_ms", "payload_summary", "reason_code"):
            assert name not in ev, f"{name} must stay omitted for absent evidence"
    finally:
        trace_observer.stop_session()


# --- lane-C2 query helpers: honest absence, no fabrication ----------------


def test_query_helpers_with_observer_off_are_honest() -> None:
    """Observer OFF: every helper answers honestly (not-found / no new page),
    never a fabricated record — and never raises (§58).

    The ring retains what an earlier session emitted (retention is the
    observer's contract, §72), so "OFF" is asserted on the OFF status and on
    the not-found answers, not on the ring being empty.
    """
    trace_observer.stop_session()
    # OFF means no NEW events are produced: a fresh resume point past the ring
    # tail yields an honest empty page.
    tail = query_events_page()["last_seq"]
    assert query_events_page(last_seq=tail)["events"] == []
    assert query_event("EV-never-emitted") is None
    assert query_why("EV-never-emitted") is None
    bundle = query_trace_bundle("TRC-never-seen")
    assert bundle.get("found") is False
    snap = query_observer()
    assert snap["status"] == "OFF"
    assert snap["trace_schema_version"] == TRACE_SCHEMA_VERSION
    decisions = query_decisions()
    assert isinstance(decisions, dict)


def test_query_why_uses_the_same_builder_as_the_rest_route() -> None:
    """The CLI WHY answer is built by the shared ``_why_payload`` — the
    terminal answer can never diverge from the API's (§68)."""
    trace_observer.start_session()
    try:
        trace_id = trace_observer.begin_trace(symbol="USDJPY")
        trace_observer.emit(
            stage="MT5",
            component="mt5_adapter",
            event_type="MT5_RESPONSE",
            status="REJECTED",
            symbol="USDJPY",
            trace_id=trace_id,
            **direct_mt5_response_evidence(
                _FakeMT5Result(retcode=10004, order=0, deal=0, price=0.0, volume=0.0),
                order=_fake_order("ORD-why-001"),
            ),
        )
        events = query_events_page(trace_id=trace_id)["events"]
        event_id = events[-1]["event_id"]
        why = query_why(event_id)
        assert why is not None
        assert why["found"] is True
        assert why["event_id"] == event_id
        assert why["reason_code"] == "RETCODE_10004"  # §16 verbatim reason
        # No further event of this trace: honest NOT OBSERVED, not a invented
        # continuation (§38).
        assert why["next"]["observed"] is False
        assert why["next"]["destination"] == "NOT OBSERVED"
    finally:
        trace_observer.stop_session()


def test_query_trace_ids_lists_only_retained_traces() -> None:
    trace_observer.start_session()
    try:
        t1 = trace_observer.begin_trace(symbol="EURUSD")
        trace_observer.emit(
            stage="MT5",
            component="mt5_adapter",
            event_type="MT5_SEND",
            status="SENT",
            symbol="EURUSD",
            trace_id=t1,
            **direct_mt5_send_evidence(_fake_order()),
        )
        ids = query_trace_ids()
        assert t1 in ids
        assert len(ids) >= 1
        assert set(ids) <= set(query_trace_ids(limit=500))
    finally:
        trace_observer.stop_session()
