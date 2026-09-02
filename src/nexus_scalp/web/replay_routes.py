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
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.web.replay_routes")

#: Bounded session registry (one process, a handful of research sessions).
_MAX_SESSIONS = 8


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


class ReplaySessionRegistry:
    """Bounded in-process registry of ReplaySession objects."""

    def __init__(self) -> None:
        self.sessions: dict[str, Any] = {}

    def get(self, replay_id: str) -> Any:
        s = self.sessions.get(replay_id)
        if s is None:
            raise HTTPException(status_code=404, detail=f"replay session not found: {replay_id}")
        return s

    def put(self, session: Any) -> str:
        while len(self.sessions) >= _MAX_SESSIONS:
            self.sessions.pop(next(iter(self.sessions)))
        self.sessions[session.replay_id] = session
        return session.replay_id

    def resolve(self, replay_id: str | None) -> Any:
        if replay_id:
            return self.get(replay_id)
        if not self.sessions:
            raise HTTPException(status_code=404, detail="no replay session active")
        return next(reversed(self.sessions.values()))


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
    """Registers the /api/replay/* routes on the server app."""

    @app.post("/api/replay/session")
    def create_replay_session(req: ReplaySessionRequest) -> dict[str, Any]:
        try:
            session = create_replay_session_from_request(req, records_loader)
        except HTTPException:
            raise
        except Exception as e:
            logger.error("[REPLAY_API] session creation failed", error=str(e))
            raise HTTPException(status_code=500, detail=str(e)) from e
        rid = registry.put(session)
        logger.info("[REPLAY_API] event=SESSION_CREATED", replay_id=rid)
        return {"ok": True, "replay_id": rid, "identity": session.identity()}

    @app.post("/api/replay/control")
    def replay_control(req: ReplayControlRequest) -> dict[str, Any]:
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
                res = session.reset() or {"ok": True}
            elif req.action == "seek":
                if req.seek_time is None:
                    raise HTTPException(status_code=422, detail="seek requires seek_time")
                res = session.seek(_parse_iso(req.seek_time.isoformat()))
            elif req.action == "checkpoint":
                snap = session.maybe_checkpoint()
                res = {"ok": True, "checkpoint": bool(snap), "clock": session.clock_iso()}
            else:  # pragma: no cover — pattern-guarded
                raise HTTPException(status_code=422, detail=f"unknown action {req.action}")
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        except Exception as e:
            logger.error("[REPLAY_API] control failed", action=req.action, error=str(e))
            raise HTTPException(status_code=500, detail=str(e)) from e
        return {"ok": True, "replay_id": session.replay_id, "result": res}

    @app.get("/api/replay/state")
    def replay_state(replay_id: str | None = None) -> dict[str, Any]:
        session = registry.resolve(replay_id)
        st = session.market_state_at_cursor()
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
        # Decision evidence comes from the ENGINE trace (bounded ring) — the
        # chart never recomputes decisions.
        engine_trace = getattr(session.engine, "_last_decision_trace", None)
        rows = engine_trace or []
        if not rows:
            raise HTTPException(status_code=404, detail="no decision trace available")
        for r in rows:
            if r.get("decision_index") == seq:
                return {"ok": True, "decision": r}
        raise HTTPException(status_code=404, detail=f"decision seq {seq} not found")

    @app.get("/api/replay/report")
    def replay_report(replay_id: str | None = None) -> dict[str, Any]:
        session = registry.resolve(replay_id)
        return {"ok": True, "report": session.report()}
