"""Replay-on-Chart API (CHG-0043, REPLAY_API v1).

Read-oriented REST surface over research/replay_session.ReplaySession so the
chart is the operator interface of the REAL historical decision pipeline.

Endpoints (all bounded, research-only, no broker surface):

* POST /api/replay/session  — create a session from an explicit contract
* POST /api/replay/control  — step/play/pause/reset/seek/checkpoint
* GET  /api/replay/state    — cursor state (engine truth; NO future data)
* GET  /api/replay/decision — one decision drill-down record
* GET  /api/replay/report   — full operator report (JSON-serializable)

NO-FUTURE-DATA RULE: /api/replay/state serves market state ONLY up to the
session cursor (event time). Future candles are never included in any payload
the chart can consume as decision state.

MT5-PARITY-FORENSICS (IMPL-D) hardening on this surface:
  C-3   /decision resolves against the run that actually executed,
        not the never-run session.engine instance (always-404 before).
  C-4   regime_enabled flows into the engine config AND into the session-side
        classifier feed, so the flag is one truth end to end (500 before).
  C-9   control responses expose an explicit exhausted / END_OF_DATA status;
        a controller status is never overwritten by the engine result splat.
  H-15  no str(e) on the wire: generic safe error envelope + correlation id,
        real exception detail logged server-side (web/errors.py pattern,
        that module is SEC-owned READ-ONLY and is reused, never edited).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.web.errors import log_web_error, new_request_id, safe_error_payload

logger = get_logger("nexus_scalp.web.replay_routes")

#: Bounded session registry (one process, a handful of research sessions).
_MAX_SESSIONS = 8

#: H-15 / F-11: HTTP status -> public-safe error code. Codes/messages are the
#: stable vocabulary of web/errors.py::ERROR_CODES (READ-ONLY); only these
#: curated strings ever reach a client, never exception text.
_SAFE_ERROR_BY_STATUS: dict[int, str] = {
    404: "RESOURCE_NOT_FOUND",
    422: "VALIDATION_ERROR",
    500: "OPERATION_FAILED",
}


class ReplaySessionRequest(BaseModel):
    """Session creation contract (brief section 2 — reproducible identity)."""

    dataset_id: str = Field(..., min_length=1, max_length=120)
    dataset_fingerprint: str = Field(..., min_length=8, max_length=64)
    symbol: str = Field(default="XAUUSD", max_length=20)
    replay_mode: str = Field(default="BAR_REPLAY", pattern="^(BAR_REPLAY|TICK_REPLAY)$")
    timeframe: str = Field(default="M1", max_length=6)
    start_time: datetime
    end_time: datetime
    git_commit: str = Field(default="", max_length=64)
    model_artifact_path: str = Field(
        default="artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt",
        max_length=400,
    )
    confidence_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    regime_enabled: bool = False
    checkpoint_every_bars: int = Field(default=200, ge=10, le=5000)


class ReplayControlRequest(BaseModel):
    action: str = Field(..., pattern="^(step_tick|step_bar|play|pause|reset|seek|checkpoint)$")
    n: int = Field(default=1, ge=1, le=10000)
    seek_time: datetime | None = None
    replay_id: str | None = Field(default=None, max_length=80)


def _parse_iso(ts: str) -> datetime:
    d = datetime.fromisoformat(ts)
    return d if d.tzinfo else d.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# H-15 / F-11: sanitized error helpers (web/errors.py pattern, applied inline)
# ---------------------------------------------------------------------------


def _client_request_id(request: Request | None) -> str:
    """Correlation id for one request: echo X-Request-ID, else mint a new one.

    Mirrors web/errors.py::request_id_from_request (that module stays
    untouched); the same id appears in the safe response envelope and in the
    server-side log line so an operator can join them.
    """
    if request is not None:
        header = request.headers.get("x-request-id")
        if header and header.strip():
            return header.strip()[:64]
    return new_request_id()


def _safe_http_error(
    status_code: int,
    request: Request | None,
    *,
    endpoint: str,
    detail: str = "",
    exc: BaseException | None = None,
    context: dict[str, Any] | None = None,
) -> JSONResponse:
    """Builds the sanitized error response for a route failure.

    PUBLIC: stable ``error.code`` + generic message + ``request_id`` — never
    ``str(exc)`` (it can embed filesystem paths / module internals).
    INTERNAL: the full exception + traceback goes to the structured log via
    ``log_web_error`` (the only place detail is written), or, for deliberate
    rejections without an exception, a single structured log line.
    """
    request_id = _client_request_id(request)
    code = _SAFE_ERROR_BY_STATUS.get(status_code, "INTERNAL_ERROR")
    payload = safe_error_payload(code=code, request_id=request_id)
    if detail:
        # Only ever used for AUTHORED rejection text (this module writes it
        # itself, never from an exception), so the client can tell WHY a
        # request was refused without leaking internals on true errors.
        payload["detail"] = f"{detail} (request_id={request_id})"
    if exc is not None:
        log_web_error(
            logger,
            endpoint,
            request_id,
            exc,
            context={"status_code": status_code, **(context or {})},
        )
    else:
        logger.warning(
            "[REPLAY_API] endpoint=%s request_id=%s status_code=%s detail=%s",
            endpoint,
            request_id,
            status_code,
            detail,
        )
    return JSONResponse(
        status_code=status_code,
        content=payload,
    )


class DatasetIdentityError(ValueError):
    """The ONLY ValueError whose text may become an HTTP 4xx ``detail``.

    Raised by the local-dataset records loader when the requested dataset
    identity is fabricated / mismatched. Its message is AUTHORED by that
    loader (ids and fingerprints it computed itself), so surfacing it is safe;
    every other ValueError is treated as an internal failure and never leaks.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _safe_rejection(request: Request | None, *, endpoint: str, detail: str) -> HTTPException:
    """4xx rejection carrying a deliberate, authored message + correlation id.

    Used only for contract violations this module raises itself (never for
    exception text), so the body stays actionable while remaining auditable
    through the logged request_id.
    """
    request_id = _client_request_id(request)
    logger.info(
        "[REPLAY_API] endpoint=%s request_id=%s rejected detail=%s",
        endpoint,
        request_id,
        detail,
    )
    return HTTPException(status_code=422, detail=f"{detail} (request_id={request_id})")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ReplaySessionRegistry:
    """Bounded in-process registry of ReplaySession objects.

    Also owns the C-3 decision-trace stashes: each controller step re-runs the
    engine over the consumed prefix, and the trace of that ACTUAL run is
    captured here keyed by replay_id so /api/replay/decision resolves against
    the engine that really executed (session.engine itself is never run).
    """

    def __init__(self) -> None:
        self.sessions: dict[str, Any] = {}
        self.traces: dict[str, dict[str, Any]] = {}

    def get(self, replay_id: str) -> Any:
        s = self.sessions.get(replay_id)
        if s is None:
            raise KeyError(replay_id)
        return s

    def put(self, session: Any) -> str:
        while len(self.sessions) >= _MAX_SESSIONS:
            evicted = next(iter(self.sessions))
            self.sessions.pop(evicted)
            self.traces.pop(evicted, None)
        self.sessions[session.replay_id] = session
        return session.replay_id

    def resolve(self, replay_id: str | None) -> Any:
        if replay_id:
            try:
                return self.get(replay_id)
            except KeyError:
                raise HTTPException(status_code=404, detail="replay session not found") from None
        if not self.sessions:
            raise HTTPException(status_code=404, detail="no replay session active")
        return next(reversed(self.sessions.values()))

    # -- C-3 trace capture -------------------------------------------------

    def capture_engine_run(self, replay_id: str, res: Any) -> None:
        """Stores the decision trace of one engine run (C-3).

        ``res`` is a controller result; when it carries the engine's
        ``result`` payload (step/seek/play after a real run) that payload's
        bounded ``decision_trace`` is the run's authoritative record. The whole
        stash is REPLACED per run because every run re-streams the consumed
        prefix from scratch — the latest run's trace is a superset of the
        cursor state the chart is looking at.
        """
        if not isinstance(res, dict):
            return
        result = res.get("result")
        if not isinstance(result, dict) or "decision_trace" not in result:
            return
        self.traces[replay_id] = {"decision_trace": list(result.get("decision_trace") or [])}

    def clear_trace(self, replay_id: str) -> None:
        """reset() wipes the session's own trace — the stash must follow."""
        self.traces.pop(replay_id, None)

    def trace(self, replay_id: str) -> list[dict[str, Any]]:
        entry = self.traces.get(replay_id) or {}
        return list(entry.get("decision_trace") or [])


# ---------------------------------------------------------------------------
# C-9: honest status semantics for control responses
# ---------------------------------------------------------------------------

_END_OF_DATA = "END_OF_DATA"


def _cursor_exhausted(session: Any) -> bool:
    """True when the controller has consumed EVERY record of the session.

    The session controller sets ``phase == ENDED`` only when it ran PAST the
    last event; a step that lands exactly on the final event leaves the phase
    PAUSED while no data remains — the classic C-9 case where ``OK`` is a lie.
    Count-based (not timestamp-based) so naive/aware mixes cannot misfire:
    ``consumed == total`` means the stream is gone.
    """
    phase = getattr(getattr(session, "phase", None), "value", None)
    if phase == "ENDED":
        return True
    try:
        return session._consumed_count() >= len(session._events)
    except (AttributeError, TypeError):
        return False


def _honest_control_status(session: Any, res: Any) -> Any:
    """C-9: guarantees an explicit END_OF_DATA once the stream is exhausted.

    The controller composes ``{..., "status": <controller>, **res}`` so the
    ENGINE's ``status`` key silently overwrote the controller's honest
    ``END_OF_DATA`` (lane report 03_laneC C-9). Rather than editing the
    engine/session (other lanes own those), the route re-asserts the truth
    derived from the cursor: exhausted -> END_OF_DATA, and the engine's
    original status is preserved as ``engine_status`` instead of dropped.
    Failed results (e.g. INVALID_SEEK, ok=False) are passed through untouched.
    """
    if not isinstance(res, dict) or res.get("ok") is False:
        return res
    if not _cursor_exhausted(session):
        return res
    engine_status = res.get("status")
    if engine_status == _END_OF_DATA:
        return res
    out = {k: v for k, v in res.items() if k != "status"}
    out["status"] = _END_OF_DATA
    if engine_status is not None:
        out["engine_status"] = engine_status
    return out


# ---------------------------------------------------------------------------
# C-4: regime plumbing helpers
# ---------------------------------------------------------------------------


def _effective_regime_enabled(session: Any) -> bool:
    """Regime truth of the session = the flag the ENGINE config actually reads.

    The session-level flag and the engine config flag are set from the same
    request value at construction, so this stays a single truth; reading the
    config keeps identity/report honest even if the session-side surface
    changes later.
    """
    return bool(getattr(getattr(session, "config", None), "regime_enabled", False))


def _session_regime(session: Any) -> dict[str, Any] | None:
    """C-4: the cursor regime block, AttributeError-free.

    ``ReplaySession.market_state_at_cursor`` reads
    ``session._regime_classifier._stable_regime`` directly and crashes with
    AttributeError when the classifier has never stabilized (a fresh or
    warmup classifier keeps it None). The cursor regime is rebuilt here from
    the classifier state defensively: unavailable until the classifier has a
    stable verdict, never an exception. The run itself already has a real
    regime per decision (the engine builds its OWN classifier from the engine
    config when ``regime_enabled`` is true — see create_replay_session_from_
    request), so ``/api/replay/state`` answers 200 in both cases.
    """
    classifier = getattr(session, "_regime_classifier", None)
    if classifier is None:
        return None
    try:
        stable = classifier._stable_regime
        return {
            "regime": getattr(stable, "value", None),
            "probability": getattr(classifier, "_stable_prob", None),
            "reason": "",
            "spread_usd": getattr(classifier, "_last_spread", None),
            "rv_5m": getattr(classifier, "_last_rv_5m", None),
            "warmup": stable is None,
        }
    except Exception:  # pragma: no cover - classifier internals must never 500 the state
        return None


def _fallback_cursor_state(session: Any) -> dict[str, Any]:
    """C-4: ``market_state_at_cursor`` minus the crashing regime call.

    Mirrors ``ReplaySession.market_state_at_cursor`` (replay_session.py:635)
    field for field; the ONLY difference is ``regime`` — the original reads
    ``current_regime()`` which dereferences ``_stable_regime.value`` and
    raises AttributeError while the classifier has never stabilized (a fresh
    classifier, or a feed sparser than the 15-tick/300s window needs — true
    for every 1-minute bar session). ``replay_session.py`` is owned by
    another lane, so the safe projection lives here instead.
    """
    clock = session.state.clock.now()
    if clock is None:
        return {"phase": session.phase.value, "clock": None, "counts": session.state.counts()}
    rec = session._records_up_to(clock, inclusive=True)[-1] if session._consumed_count() else None
    return {
        "phase": session.phase.value,
        "status": session.status.value,
        "clock": session.clock_iso(),
        "counts": session.state.counts(),
        "last_price": {
            "bid": rec.get("bid") if rec else (rec.get("close") if rec else None),
            "ask": rec.get("ask") if rec else None,
            "close": rec.get("close") if rec else None,
        }
        if rec
        else None,
        "open_position": session._open_position,
        "equity": session._equity(),
        "regime": _session_regime(session),
        "regime_transitions": list(getattr(session, "_regime_transitions", [])[-50:]),
    }


def _feed_session_regime(session: Any, res: Any) -> None:
    """C-4: feeds the session-side classifier the events the run consumed.

    ``ReplaySession._classify_regime`` exists (production classifier, causal
    event-time only) but has no caller inside the controller, so the
    classifier built at construction never ran and the cursor regime block
    was permanently UNAVAILABLE. The engine re-streams the consumed prefix on
    every run, so the trace rows of that SAME run are exactly the decisions
    the cursor has consumed — feeding them is causal (no future data) and
    matches the regime the engine itself classified. ``replay_session.py``
    is owned by another lane, so the feed is driven from the route.

    Deliberately non-fatal: a classifier hiccup must never break stepping.
    """
    if not _effective_regime_enabled(session):
        return
    if not hasattr(session, "_classify_regime"):
        return
    result = res.get("result") if isinstance(res, dict) else None
    rows = result.get("decision_trace") if isinstance(result, dict) else None
    if not rows:
        return
    try:
        from nexus_scalp.research.event_source import TickEvent

        symbol = session.contract.symbol
        for row in rows:
            ts = row.get("ts")
            bid = row.get("bid")
            ask = row.get("ask")
            if ts is None or bid is None or ask is None:
                continue
            when = datetime.fromisoformat(str(ts))
            session._classify_regime(
                TickEvent(
                    timestamp=when if when.tzinfo else when.replace(tzinfo=UTC),
                    bid=float(bid),
                    ask=float(ask),
                    symbol=symbol,
                )
            )
    except Exception as e:  # pragma: no cover - observability must never 500 a step
        logger.warning("[REPLAY_API] regime feed skipped error_type=%s", type(e).__name__)


def create_replay_session_from_request(req: ReplaySessionRequest, records_loader: Any) -> Any:
    """Builds a ReplaySession from the request contract.

    ``records_loader(contract, config)`` is provided by the server wiring and
    MUST return the raw record dicts for the requested window from the LOCAL
    dataset cache (no network, no MT5 acquisition on this path).
    """
    from nexus_scalp.research.replay_session import ReplayContract, ReplaySession
    from nexus_scalp.research.streaming_replay import (
        ReplayExecutionConfig,
        ReplaySessionConfig,
    )

    if req.end_time <= req.start_time:
        raise HTTPException(status_code=422, detail="end_time must be after start_time")
    contract = ReplayContract(
        dataset_id=req.dataset_id,
        dataset_fingerprint=req.dataset_fingerprint,
        symbol=req.symbol.upper(),
        start_time=_parse_iso(req.start_time.isoformat()),
        end_time=_parse_iso(req.end_time.isoformat()),
        replay_mode=req.replay_mode,
        timeframe=req.timeframe,
        git_commit=req.git_commit,
    )
    config = ReplaySessionConfig(
        model_artifact_path=req.model_artifact_path,
        policy_params={"confidence_threshold": req.confidence_threshold},
        decide_on="bar_close" if req.replay_mode == "BAR_REPLAY" else "every_tick",
        execution=ReplayExecutionConfig(),
        git_commit=req.git_commit or "replay-api",
        # C-4: the flag must reach THE CONFIG THE ENGINE READS — previously it
        # only reached the session, so the engine classifier stayed disabled
        # while the session claimed regime_enabled=true (split-brain) and
        # /api/replay/state then 500'd on the never-run session classifier.
        regime_enabled=req.regime_enabled,
    )
    records = records_loader(contract, config)
    if not records:
        raise HTTPException(
            status_code=422, detail="no records for the requested window in the local dataset"
        )
    return ReplaySession(
        contract,
        config,
        events=records,
        regime_enabled=req.regime_enabled,
        checkpoint_every_bars=req.checkpoint_every_bars,
    )


def register_replay_routes(
    app: FastAPI,
    registry: ReplaySessionRegistry,
    records_loader: Any,
    _err: Any = None,
) -> None:
    """Registers the /api/replay/* routes on the server app.

    ``_err`` is the server's safe-envelope factory (kept for wiring parity
    with the other route modules); this module builds its envelopes through
    ``safe_error_payload`` directly so it can bind the SAME correlation id to
    both the response and the server-side log line.
    """

    @app.post("/api/replay/session")
    def create_replay_session(req: ReplaySessionRequest, request: Request) -> Any:
        try:
            session = create_replay_session_from_request(req, records_loader)
        except HTTPException:
            # Deliberate contract rejections (bad window, empty window, bad
            # dataset identity) keep their authored detail: no exception text.
            raise
        except DatasetIdentityError as e:
            # UI-M1: the records loader is the AUTHORITY on dataset identity and
            # raises this with an AUTHORED message (the requested id /
            # fingerprint), so this is a deliberate 422 rejection, not a server
            # fault. Only this class's text is ever surfaced: it is constructed
            # by the loader itself, never from an arbitrary exception.
            return _safe_http_error(
                422,
                request,
                endpoint="/api/replay/session",
                detail=e.detail,
                context={"action": "create", "rejection": "dataset_identity"},
            )
        except ValueError:
            # An unexpected ValueError is an internal failure: the H-15 rule
            # forbids str(e) as detail (it can carry module internals), so this
            # goes to the generic 500 path with the full text server-side only.
            raise
        except Exception as e:
            # H-15 / F-11: no str(e) on the wire (it can name the missing
            # artifact path); generic envelope out, full detail to the log.
            return _safe_http_error(
                500, request, endpoint="/api/replay/session", exc=e, context={"action": "create"}
            )
        rid = registry.put(session)
        logger.info("[REPLAY_API] event=SESSION_CREATED", replay_id=rid)
        return {"ok": True, "replay_id": rid, "identity": _identity_payload(session)}

    @app.post("/api/replay/control")
    def replay_control(req: ReplayControlRequest, request: Request) -> Any:
        session = registry.resolve(req.replay_id)
        try:
            if req.action == "step_tick":
                res = session.step_tick(req.n)
            elif req.action == "step_bar":
                res = session.step_bar(req.n)
            elif req.action == "play":
                res = session.play()
            elif req.action == "pause":
                res = session.pause()
            elif req.action == "reset":
                # C-9: reset() returns None — the old `or {"ok": True}` masked
                # that; answer an explicit status instead.
                session.reset()
                res = {"ok": True, "status": "OK", "clock": session.clock_iso()}
            elif req.action == "seek":
                if req.seek_time is None:
                    raise _safe_rejection(
                        request, endpoint="/api/replay/control", detail="seek requires seek_time"
                    )
                res = session.seek(_parse_iso(req.seek_time.isoformat()))
            elif req.action == "checkpoint":
                snap = session.maybe_checkpoint()
                res = {"ok": True, "checkpoint": bool(snap), "clock": session.clock_iso()}
            else:  # pragma: no cover — pattern-guarded
                raise _safe_rejection(
                    request,
                    endpoint="/api/replay/control",
                    detail=f"unknown action {req.action}",
                )
        except HTTPException:
            # Deliberate rejections (authored text only, no exception text).
            raise
        except ValueError as e:
            # Domain rejections (clock/seek violations): generic safe code and
            # a correlation id; the real text is logged server-side only.
            return _safe_http_error(
                422,
                request,
                endpoint="/api/replay/control",
                exc=e,
                context={"action": req.action},
            )
        except Exception as e:
            # H-15 / F-11: no str(e) on the wire; generic envelope out, full
            # detail (paths, internals) to the log only.
            return _safe_http_error(
                500,
                request,
                endpoint="/api/replay/control",
                exc=e,
                context={"action": req.action},
            )
        if req.action == "reset":
            registry.clear_trace(session.replay_id)
        res = _honest_control_status(session, res)
        _feed_session_regime(session, res)
        registry.capture_engine_run(session.replay_id, res)
        return {"ok": True, "replay_id": session.replay_id, "result": res}

    @app.get("/api/replay/state")
    def replay_state(replay_id: str | None = None) -> dict[str, Any]:
        session = registry.resolve(replay_id)
        try:
            st = session.market_state_at_cursor()
        except AttributeError:
            # C-4: current_regime() derefs _stable_regime while it is still
            # None (never-classified classifier) — the 500 lane report 03 saw.
            # Same payload, regime projected defensively; never an exception.
            st = _fallback_cursor_state(session)
            logger.info(
                "[REPLAY_API] regime block projected by fallback replay_id=%s",
                session.replay_id,
            )
        # KNOWN vs UNKNOWN boundary for the chart: everything after the cursor
        # is UNKNOWN — the payload carries only counts/total, never future
        # candles or future decisions.
        consumed = session._consumed_count()
        st["known_events"] = consumed
        st["unknown_events"] = max(0, len(session._events) - consumed)
        return st

    @app.get("/api/replay/decision")
    def replay_decision(seq: int, replay_id: str | None = None) -> dict[str, Any]:
        session = registry.resolve(replay_id)
        # C-3: decision evidence comes from the ACTUALLY RUNNING engine. The
        # controller rebuilds a fresh StreamingReplayEngine per run, so the
        # long-lived session.engine is never run and its _last_decision_trace
        # is always empty (the endpoint 404'd by construction). The trace of
        # the last real run, captured at control time, is served instead; the
        # chart never recomputes decisions.
        rows = registry.trace(session.replay_id)
        if not rows:
            rows = list(getattr(session.engine, "_last_decision_trace", None) or [])
        if not rows:
            raise HTTPException(status_code=404, detail="no decision trace available")
        for r in rows:
            if r.get("decision_index") == seq:
                return {"ok": True, "decision": r}
        raise HTTPException(status_code=404, detail=f"decision seq {seq} not found")

    @app.get("/api/replay/report")
    def replay_report(replay_id: str | None = None) -> dict[str, Any]:
        session = registry.resolve(replay_id)
        report = session.report()
        report["identity"] = _identity_payload(session, report.get("identity"))
        return {"ok": True, "report": report}


def _identity_payload(session: Any, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """C-4: identity/report regime flag reflects the ENGINE config truth.

    ``session.identity()["regime_enabled"]`` reported the session-level flag;
    the engine reads its own config copy, so both are surfaced: the legacy key
    keeps its meaning while ``engine_regime_enabled`` carries the value the
    engine actually runs with. This kills the split-brain observability lie
    without touching replay_session.py (owned elsewhere).
    """
    ident = dict(base) if base is not None else dict(session.identity())
    ident["engine_regime_enabled"] = _effective_regime_enabled(session)
    return ident
