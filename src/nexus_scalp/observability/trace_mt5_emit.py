"""Decision-trace MT5/gateway causal-evidence seam (LIVE-CAUSAL-TOPOLOGY phase 2).

PURPOSE: lane-owned enrichment seam for MT5 / gateway causal evidence
(brief §20/§21/§13/§28/§19).

The 8 broker-gateway emit call sites that could carry order_check /
order_send / broker-response causal evidence live in three files that are
ANOTHER AGENT'S uncommitted WIP in the shared checkout and in two open
foreign PRs (#436 / #437):

    src/nexus_scalp/adapters/mt5/mt5_adapter.py    (emit at 1365/1394/1423)
    src/nexus_scalp/adapters/mt5/remote_gateway.py (emit at 231/267)
    src/nexus_scalp/adapters/paper/paper_adapter.py (emit at 1512/1552/1585)

This module is the lane-owned substitute for editing them: the foreign call
sites keep their existing detail dicts, and any caller (including those sites,
once #436/#437 merge) can pass the SAME real result objects here to obtain the
frozen v2 causal field dict. The enrichment therefore lands without touching a
foreign-held file.

OWNER: lane C2, LIVE-CAUSAL-TOPOLOGY phase 2 — future edits go to the
LIVE-CAUSAL-TOPOLOGY phase-2 lane owner.

CONSUMES: real gateway/adapter result objects, DUCK-TYPED ONLY — every read is
a getattr/getitem on a name the object actually carries; there is NO isinstance
coupling to the adapter classes and NO import of them (this module must stay
importable from any context, including a checkout where the foreign files are
dirty or absent):

- direct MT5 ``mt5.order_send``/``order_check`` result: attributes ``retcode``,
  ``order`` (broker ticket), ``deal``, ``price``, ``volume``, ``comment``
  (verified at mt5_adapter.py:1365-1423 and :1596).
- remote-gateway RPC response: a MAPPING over the JSON the gateway returned —
  keys ``status`` / ``message`` / ``ticket`` / ``order_id`` / ``deal_id``
  (remote_gateway.py:231-267; the site computes res.get("status")=="SUCCESS").
- paper adapter: a bool success plus the internal simulated ticket
  (paper_adapter.py:1512-1601; detail.gateway == "paper", simulated=True).
- the request object (domain/models.py:233 TradeOrder: order_id, symbol,
  order_type, volume, price, stop_loss, take_profit, magic_number, comment)
  and the HMAC RPC payload (remote_gateway.py:211-224) for the external-request
  inspector summary (§13), SECRETS REDACTED.

PROVIDES (pure, never-raising — BUG-311):

    direct_mt5_send_evidence(order, request_dict)      -> v2 dict (SENT)
    mt5_broker_evidence(result)                        -> v2 dict (raw result)
    direct_mt5_response_evidence(result, *, order,
        request_dict, started_ns)                      -> v2 dict (response)
    remote_mt5_send_evidence(order, payload)           -> v2 dict (remote SENT)
    remote_mt5_response_evidence(res, *, order,
        payload, started_ns)                           -> v2 dict (remote resp.)
    paper_gateway_send_evidence(order, payload)        -> v2 dict (paper SENT)
    paper_gateway_response_evidence(ok, ticket, *,
        order, payload, reason, started_ns)            -> v2 dict (paper resp.)
    enrich_gateway_detail(detail, *causal_dicts)       -> merged detail dict
    extract_external_request_summary(request_dict, *, endpoint,
        duration_ms, status, attempt, error_code)      -> §13 summary dict
    mt5_v2_field_names() / MT5_EVIDENCE_FIELD_NAMES     -> frozen field list
    NULL_EVIDENCE                                      -> the all-absent dict

INVARIANTS: only values the object ACTUALLY carries are emitted — an absent key
stays absent (absence is data, §60), NEVER back-filled with "", 0, 0.0 or
False. Nothing is fabricated, extrapolated or promoted: paper is never rendered
as broker-reached (§24: provider "paper" stays verbatim), RECOMMENDED never
becomes EXECUTED (§66), a single timing endpoint never yields a duration. The
status/state words are always caller-asserted verbatim runtime facts — this
seam computes no verdict. Every helper returns a dict (possibly empty) and
NEVER raises: a poisoned or odd object degrades to partial evidence, not an
exception into the hot path.

EXTEND: add a new evidence shape by adding a function that reads the new
attribute names and returns the same frozen field dict — never rename a
published helper, never emit a field that is not in
``trace_contract.TraceEvent`` (additive-only, TRACE_SCHEMA_VERSION stays 1).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from time import perf_counter_ns
from typing import Any

# Frozen contract v2 field names this seam may produce. Mirrors the optional
# causal fields of ``trace_contract.TraceEvent`` (trace_contract.py:274-295);
# a name NOT in this tuple is never emitted, so a typo cannot reach the
# observer as an invented field. Keep this a subset of TraceEvent's frozen
# fields (additive-only; TRACE_SCHEMA_VERSION stays 1).
MT5_EVIDENCE_FIELD_NAMES: tuple[str, ...] = (
    "state",
    "reason_code",
    "error_code",
    "duration_ms",
    "position_id",
    "order_id",
    "deal_id",
    "provider",
    "destination",
    "payload_summary",
)

#: The honest answer when NO evidence could be extracted: every frozen field
#: absent. Callers that receive this render UNKNOWN / NOT OBSERVED (§60) and
#: must never treat it as a negative result (no zero-fill, no invented state).
NULL_EVIDENCE: dict[str, Any] = {}

# Key tokens that mark a request field as secret (§54): the summary records
# only the redacted marker, never the value. Mirrors the token set used by
# trace_contract._SENSITIVE_KEY_TOKENS (§13/§48).
_SENSITIVE_HINTS = (
    "authorization",
    "bearer",
    "secret",
    "signature",
    "token",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "api-key",
    "credential",
    "private",
    "cookie",
    "passphrase",
    "access_key",
    "session_key",
)

_REDACTED = "***REDACTED***"

_PROVIDERS = ("direct_mt5", "remote_mt5", "paper")
_DESTINATIONS = {
    "direct_mt5": "MT5_TERMINAL",
    "remote_mt5": "REMOTE_GATEWAY",
    "paper": "PAPER_SIMULATOR",
}


def mt5_v2_field_names() -> tuple[str, ...]:
    """The frozen v2 fields this seam can produce (contract surface)."""
    return MT5_EVIDENCE_FIELD_NAMES


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``obj.name`` / ``obj[name]`` without ever raising.

    Objects and mappings are both accepted (a poisoned or odd object yields the
    default, never an exception). Plain-name access only — no isinstance
    coupling, no adapter import, so the foreign classes may change shape
    without breaking this seam.
    """
    if obj is None:
        return default
    try:
        if isinstance(obj, Mapping):
            return obj.get(name, default)
        return getattr(obj, name, default)
    except Exception:
        return default


def _as_text(value: Any) -> str | None:
    """Non-empty trimmed string, else None (absent, never '')."""
    if value is None or isinstance(value, bool):
        return None
    try:
        text = str(value).strip()
    except Exception:
        return None
    return text or None


def _as_int(value: Any) -> int | None:
    """Whole number from a numeric-ish value, else None (never 0 for unknown).

    A bool is rejected: ``True`` must not become broker ticket ``1``.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        if isinstance(value, float):
            # A non-finite value is not evidence (never 0 fill-in).
            if math.isnan(value) or math.isinf(value):
                return None
            return int(value)
        text = str(value).strip()
        return int(text) if text else None
    except Exception:
        return None


def _duration_ms_from(started_ns: Any) -> int | None:
    """Whole milliseconds elapsed since a monotonic nanosecond instant.

    ``started_ns`` is the perf counter the caller captured immediately before
    the real broker/RPC call; the endpoint is read now. A missing or
    non-monotonic pair yields None — one endpoint is never extrapolated into a
    span (no fabricated duration, §61).
    """
    started = _as_int(started_ns)
    if started is None or started <= 0:
        return None
    try:
        elapsed = (perf_counter_ns() - started) // 1_000_000
    except Exception:
        return None
    return elapsed if elapsed >= 0 else None


def _clean(value: dict[str, Any]) -> dict[str, Any]:
    """Drop absent/empty/out-of-contract entries so only observed evidence
    survives (§60). Never raises.
    """
    try:
        return {
            k: v
            for k, v in value.items()
            if k in MT5_EVIDENCE_FIELD_NAMES
            and v is not None
            and not (isinstance(v, str | dict) and not v)
        }
    except Exception:
        return {}


def _order_id(order: Any) -> str | None:
    """The internal tracking id the caller's request object carries."""
    return _as_text(_get(order, "order_id"))


def _provider(gateway: str | None) -> str | None:
    """Gateway/provider word, verbatim (never promoted, never guessed)."""
    text = _as_text(gateway)
    return text if text in _PROVIDERS else None


def extract_external_request_summary(
    request_dict: Any,
    *,
    endpoint: Any = None,
    duration_ms: Any = None,
    status: Any = None,
    attempt: Any = None,
    error_code: Any = None,
) -> dict[str, Any]:
    """Bounded, secret-redacted summary of ONE external request (§13).

    ``request_dict`` is the payload the runtime actually sent (the MT5 request
    mapping at mt5_adapter.py:1337-1354 or the remote-gateway HMAC body at
    remote_gateway.py:211-224). Values are copied ONLY when they are plain
    scalars the runtime set; a sensitive key records the redacted marker while
    its value never enters the summary (§54/§48). Absent facts stay absent.
    Never raises.
    """
    try:
        out: dict[str, Any] = {}
        if isinstance(request_dict, Mapping):
            for key, value in request_dict.items():
                name = _as_text(key)
                if not name:
                    continue
                lowered = name.lower()
                if any(tok in lowered for tok in _SENSITIVE_HINTS):
                    out[name] = _REDACTED  # key recorded, value dropped
                    continue
                if isinstance(value, bool | int | float | str):
                    out[name] = value
                elif isinstance(value, Mapping | list | tuple | set | frozenset):
                    out[name] = f"<{type(value).__name__}:{len(value)}>"
                else:
                    out[name] = "<object>"
        ep = _as_text(endpoint)
        if ep:
            out["endpoint"] = ep
        dur = _as_int(duration_ms)
        if dur is not None:
            out["duration_ms"] = dur
        st = _as_text(status)
        if st:
            out["status"] = st
        att = _as_int(attempt)
        if att is not None:
            out["attempt"] = att
        ec = _as_text(error_code)
        if ec:
            out["error_code"] = ec
        return out
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Direct MT5 (mt5_adapter.py) — order_check / order_send result objects.
# ---------------------------------------------------------------------------


def direct_mt5_send_evidence(order: Any, request_dict: Any = None) -> dict[str, Any]:
    """Evidence for the SENT event immediately preceding a real terminal call.

    The send side can prove only that the request was dispatched: it carries
    the request's own facts and the gateway word. It must NOT claim a broker
    response, ticket or fill — those are response-side facts (§24: never imply
    MT5 was reached from an internal event). Never raises.
    """
    try:
        out: dict[str, Any] = {
            "provider": _provider("direct_mt5"),
            "destination": _DESTINATIONS["direct_mt5"],
            "state": "EXECUTING",
            "order_id": _order_id(order),
        }
        if request_dict is not None:
            summary = extract_external_request_summary(request_dict)
            if summary:
                out["payload_summary"] = summary
        return _clean(out)
    except Exception:
        return dict(NULL_EVIDENCE)


def mt5_broker_evidence(result: Any) -> dict[str, Any]:
    """Frozen v2 fields from a raw MT5 ``order_send``/``order_check`` result.

    Reads exactly the attributes the MetaTrader5 result object carries
    (``retcode``, ``order``, ``deal``, ``price``, ``volume``, ``comment`` —
    verified at mt5_adapter.py:1410-1423 and :1596). Maps none of them to a
    verdict: ``reason_code``/``error_code`` come from the retcode string the
    broker itself returned and the ticket/deal become position/deal ids.
    Never raises.
    """
    if result is None:
        return dict(NULL_EVIDENCE)
    try:
        retcode_text = _as_text(_get(result, "retcode"))
        out: dict[str, Any] = {}
        if retcode_text:
            # The broker's own return code is the honest reason/error carrier.
            out["reason_code"] = f"RETCODE_{retcode_text}"
            out["error_code"] = retcode_text
        # A broker ticket/deal of 0 is the terminal's own "no ticket" value:
        # the producer itself gates it (mt5_adapter.py:1419
        # ``int(result.order) if result.order else None``), so a zero must
        # stay absent rather than become the position id "0".
        ticket = _as_int(_get(result, "order"))
        if ticket is not None and ticket > 0:
            out["position_id"] = str(ticket)
        deal = _as_int(_get(result, "deal"))
        if deal is not None and deal > 0:
            out["deal_id"] = str(deal)
        return _clean(out)
    except Exception:
        return dict(NULL_EVIDENCE)


def direct_mt5_response_evidence(
    result: Any,
    *,
    order: Any = None,
    request_dict: Any = None,
    started_ns: Any = None,
) -> dict[str, Any]:
    """Evidence for the direct-MT5 broker RESPONSE event (ACCEPTED/REJECTED).

    ``status`` is NOT interpreted here: the caller (the emit site) asserts the
    verbatim status word from the retcode comparison it already performs
    (mt5_adapter.py:1384: ``result.retcode != mt5.TRADE_RETCODE_DONE``). This
    helper supplies the causal fields that comparison proves — broker
    ticket/deal as position/deal ids, retcode as reason/error code, observed
    latency and the request summary. A ``None`` result (terminal unreachable:
    ``mt5.order_send`` returned None, mt5_adapter.py:1385) yields the honest
    no-evidence dict; the caller's own ``reached`` fact stays in its detail.
    Never raises.
    """
    try:
        out = mt5_broker_evidence(result)
        out["provider"] = _provider("direct_mt5")
        out["destination"] = _DESTINATIONS["direct_mt5"]
        order_id = _order_id(order)
        if order_id:
            out["order_id"] = order_id
        duration = _duration_ms_from(started_ns)
        if duration is not None:
            out["duration_ms"] = duration
        if request_dict is not None:
            summary = extract_external_request_summary(request_dict)
            if summary:
                out["payload_summary"] = summary
        return _clean(out)
    except Exception:
        return dict(NULL_EVIDENCE)


# ---------------------------------------------------------------------------
# Remote gateway (remote_gateway.py) — JSON RPC responses (MAPPINGS).
# ---------------------------------------------------------------------------


def remote_mt5_send_evidence(order: Any, payload: Any = None) -> dict[str, Any]:
    """Evidence for the remote-gateway SENT event (before the real RPC).

    The remote gateway speaks HMAC HTTP, so the request summary records the
    endpoint and the NON-secret payload fields — the HMAC signature, API key
    and secret token (remote_gateway.py:502-513) appear only as redacted key
    markers (§13/§54). Never raises.
    """
    try:
        out: dict[str, Any] = {
            "provider": _provider("remote_mt5"),
            "destination": _DESTINATIONS["remote_mt5"],
            "state": "EXECUTING",
            "order_id": _order_id(order),
        }
        if payload is not None:
            summary = extract_external_request_summary(payload, endpoint="/api/v1/execute")
            if summary:
                out["payload_summary"] = summary
        return _clean(out)
    except Exception:
        return dict(NULL_EVIDENCE)


def remote_mt5_response_evidence(
    res: Any,
    *,
    order: Any = None,
    payload: Any = None,
    started_ns: Any = None,
) -> dict[str, Any]:
    """Evidence for the remote-gateway RESPONSE event.

    ``res`` is the parsed JSON mapping the gateway returned (``status`` /
    ``message`` / ``ticket`` / ``order_id`` / ``price`` — remote_gateway.py:
    231-267). The status word is caller-asserted verbatim — the site already
    computes ``success = res.get("status") == "SUCCESS"``; this helper carries
    the ids and the reason the gateway itself reported. Never raises.
    """
    try:
        order_id = _order_id(order) or _as_text(_get(res, "order_id"))
        out: dict[str, Any] = {
            "provider": _provider("remote_mt5"),
            "destination": _DESTINATIONS["remote_mt5"],
            "order_id": order_id,
        }
        reason = _as_text(_get(res, "message")) or _as_text(_get(res, "reason"))
        if reason:
            out["reason_code"] = reason
        status = _as_text(_get(res, "status"))
        if status:
            out["error_code"] = status
        ticket = _as_int(_get(res, "ticket"))
        if ticket is not None:
            out["position_id"] = str(ticket)
        deal = _as_int(_get(res, "deal_id"))
        if deal is not None:
            out["deal_id"] = str(deal)
        duration = _duration_ms_from(started_ns)
        if duration is not None:
            out["duration_ms"] = duration
        if payload is not None:
            summary = extract_external_request_summary(payload, endpoint="/api/v1/execute")
            if summary:
                out["payload_summary"] = summary
        return _clean(out)
    except Exception:
        return dict(NULL_EVIDENCE)


# ---------------------------------------------------------------------------
# Paper gateway (paper_adapter.py) — simulated fills, NEVER broker-reached.
# ---------------------------------------------------------------------------


def paper_gateway_send_evidence(order: Any, payload: Any = None) -> dict[str, Any]:
    """Evidence for the paper-gateway SENT event.

    Paper never contacts an MT5 terminal, so the provider word stays ``paper``
    and the destination is the simulator: a paper fill can never be read as
    broker-reached evidence (§24 truth rule; TraceStage.GATEWAY docs in
    trace_contract.py:52-55). Never raises.
    """
    try:
        out: dict[str, Any] = {
            "provider": _provider("paper"),
            "destination": _DESTINATIONS["paper"],
            "state": "EXECUTING",
            "order_id": _order_id(order),
        }
        if payload is not None:
            summary = extract_external_request_summary(payload)
            if summary:
                out["payload_summary"] = summary
        return _clean(out)
    except Exception:
        return dict(NULL_EVIDENCE)


def paper_gateway_response_evidence(
    ok: Any,
    ticket: Any = None,
    *,
    order: Any = None,
    payload: Any = None,
    reason: Any = None,
    started_ns: Any = None,
) -> dict[str, Any]:
    """Evidence for the paper-gateway RESPONSE event (ACCEPTED / REJECTED).

    ``ok`` is the site's own success fact (paper_adapter.py:1580
    ``sim_ticket > 0``) and ``ticket`` the internal simulated ticket. The state
    word stays the caller-asserted verbatim status; this helper only exposes
    what the simulation recorded and computes no verdict. Never raises.
    """
    try:
        out: dict[str, Any] = {
            "provider": _provider("paper"),
            "destination": _DESTINATIONS["paper"],
            "order_id": _order_id(order),
        }
        reason_text = _as_text(reason)
        if reason_text:
            out["reason_code"] = reason_text
        tick = _as_int(ticket)
        if tick is not None and tick > 0:
            out["position_id"] = str(tick)
        duration = _duration_ms_from(started_ns)
        if duration is not None:
            out["duration_ms"] = duration
        if payload is not None:
            summary = extract_external_request_summary(payload)
            if summary:
                out["payload_summary"] = summary
        return _clean(out)
    except Exception:
        return dict(NULL_EVIDENCE)


def enrich_gateway_detail(detail: Any, *causal_dicts: Any) -> dict[str, Any]:
    """Merge frozen v2 evidence into a gateway event's ``detail`` mapping.

    The foreign emit sites already build rich ``detail`` dicts (the broker
    request/response facts at mt5_adapter.py:1365-1423). This keeps those
    verbatim and layers the v2 causal fields under a reserved ``causal`` key,
    so the enrichment is visible to the UI/CLI without the foreign call site
    having to change its kwargs. Absent fields stay absent; an all-absent
    argument adds nothing. Never raises; a ``None`` detail becomes a fresh
    mapping.
    """
    try:
        out: dict[str, Any] = dict(detail) if isinstance(detail, Mapping) else {}
        merged: dict[str, Any] = {}
        for causal in causal_dicts:
            if isinstance(causal, Mapping):
                for key, value in causal.items():
                    if key in MT5_EVIDENCE_FIELD_NAMES and value is not None:
                        merged[key] = value
        if merged:
            out["causal"] = merged
        return out
    except Exception:
        return dict(detail) if isinstance(detail, Mapping) else {}
