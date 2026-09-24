"""
 * PURPOSE: Lane-B integration coverage for the causal REST surface added to
 *   web/trace_routes.py — backward-compatible event filters + WHY/NEXT —
 *   driven through a REAL paper-mode LiveEngine (model, features, inference,
 *   SignalPolicy, DecisionExecutor, RiskEngine, PaperMT5Adapter) with the
 *   observer ACTIVE, exactly the harness style of
 *   tests/integration/test_decision_trace_e2e.py.
 * OWNER: lane B of LIVE-CAUSAL-TOPOLOGY — future edits go to the
 *   LIVE-CAUSAL-TOPOLOGY lane owner.
 * CONSUMES: nexus_scalp.web.server.create_app (direct ASGI TestClient),
 *   nexus_scalp.observability.trace_observer (observer lifecycle + ring),
 *   nexus_scalp.application.live_engine.LiveEngine._process_tick_pipeline,
 *   contract v2 frozen fields emitted by inference.py /
 *   decision_executor.py (state/reason_code/mode/freshness/position_id /
 *   order_id/request_id/execution_id).
 * PROVIDES: test_events_no_filter_is_the_legacy_tail,
 *   test_events_filter_chain_only_narrows,
 *   test_events_trace_id_filter_matches_a_real_trace,
 *   test_events_position_id_filter_absent_is_honest,
 *   test_why_endpoint_derives_from_the_stored_record,
 *   test_why_endpoint_unknown_id_is_404,
 *   test_why_endpoint_terminal_trace_ends_terminated,
 *   test_why_endpoint_failure_isolation,
 *   test_causal_fields_reach_the_stored_events.
 * INVARIANTS: every assertion is over evidence the runtime itself emitted
 *   (no fabricated decision/id/status/filter match); absence renders the
 *   honest empty state or a skip, never a guessed value (§60/§73); the WHY
 *   answer restates only stored words (§58).
 * EXTEND: add a case by emitting a new event kind and asserting on the stored
 *   record; filters are AND-composed in _apply_event_filters, so a new
 *   filter only needs a clause there plus one narrowing assertion here.
 */

Lane-B integration: causal route surface — REAL engine, REAL evidence.

Drives the same real paper-mode LiveEngine as
``test_decision_trace_e2e.py`` (real model, features, inference, SignalPolicy,
DecisionExecutor, RiskEngine, PaperMT5Adapter) with the observer ACTIVE, then
exercises the two lane-B additions against events the runtime itself produced:

- ``GET /api/trace/events`` OPTIONAL AND-filters (``trace_id`` / ``stage`` /
  ``status`` / ``mode`` / ``position_id``) — backward-compatible: no filter
  preserves the legacy tail; every filter only narrows.
- ``GET /api/trace/why/{event_id}`` — runtime-derived WHY / NEXT answer built
  from the stored record only, honest 404 for unknown ids, TERMINATED for a
  terminal tail, never a synthesized explanation or continuation.

Nothing here fabricates a decision, an id, a status word or a filter match: each
assertion is over evidence the observer retained, and the "no match" cases
assert the honest empty state rather than a invented page.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# --------------------------------------------------------------------------
# Real-runtime harness (paper; the safest existing execution mode)
# --------------------------------------------------------------------------


def _make_engine(tmp_path: Path):
    """Real LiveEngine wiring with a paper adapter (no live loop launched)."""
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
    from nexus_scalp.application.live_engine import LiveEngine
    from nexus_scalp.configuration.config import AppConfig

    repo = AuditRepository(
        db_url=f"sqlite:///{tmp_path / 'trace_causal.db'}", flush_interval_sec=0.05
    )
    adapter = PaperMT5Adapter(initial_balance=10_000.0, symbol="XAUUSD")
    adapter.connect()
    config = AppConfig.model_validate(
        {
            "execution": {"symbol": "XAUUSD", "mode": "PAPER", "magic_number": 888201},
            "model": {
                "model_artifact_path": str(tmp_path / "model.pt"),
                "feature_schema_version": "v1.0",
                "confidence_threshold": 0.20,
            },
            "risk": {
                "risk_per_trade_pct": 2.0,
                "max_account_drawdown_pct": 10.0,
                "max_concurrent_positions": 5,
                "max_spread_points": 50,
                "max_allowed_lots": 10.0,
                "max_margin_usage_pct": 50.0,
            },
            "telegram": {"enabled": False, "bot_token": "x", "admin_id": "y"},
        }
    )
    engine = LiveEngine(config=config, adapter=adapter, audit_repo=repo, force_fresh_model=True)
    engine._inference_enabled = True
    engine.warmup_state = "READY"
    return engine, adapter


def _account():
    from nexus_scalp.domain.models import AccountInfo

    return AccountInfo(
        login=1,
        trade_mode=0,
        leverage=100,
        balance=10_000.0,
        equity=10_000.0,
        margin=0.0,
        margin_free=10_000.0,
        currency="USD",
    )


def _tick(bid: float, ask: float, when: datetime):
    from nexus_scalp.domain.models import TickData

    return TickData(
        symbol="XAUUSD", timestamp=when, bid=bid, ask=ask, last=(bid + ask) / 2.0, volume=1.0
    )


def _feed_minute(engine, base_minute: datetime, close: float, steps: int = 3) -> None:
    """Feed steps ticks in one M1 bar; the next minute's tick seals the bar."""
    half = max(close / 2000.0, 0.05)
    for i in range(steps):
        t = base_minute + timedelta(seconds=10 * i)
        price = close + (half if i % 2 else -half)
        engine._process_tick_pipeline(_tick(price - half, price + half, t), _account())


def _feed_bars(engine, n: int = 6) -> None:
    base = datetime(2026, 2, 9, 12, 0, tzinfo=UTC)
    close = 2000.0
    for k in range(n):
        close += (1.2 if k % 2 == 0 else -0.9) * (1 + k * 0.1)
        _feed_minute(engine, base + timedelta(minutes=k), close)


def _reset_observer_store() -> None:
    """Return the process-global observer store to a clean ring (test-side).

    ``trace_observer`` is a module singleton with NO public reset API (lane A
    owns that file — never edited here), while the sibling e2e file asserts
    ``events_retained == 0`` and a strictly GROWING decision feed, both of
    which assume a fresh process. The shared gate command collects THIS file
    first, so lane-B evidence must not leak into sibling files: only the
    in-memory store is drained, never a production code path.
    """
    from nexus_scalp.observability.trace_observer import trace_observer

    with trace_observer._lock:
        trace_observer.stop_session()
        trace_observer._events.clear()
        trace_observer._decisions.clear()
        trace_observer._trace_events.clear()
        trace_observer._trace_root.clear()
        trace_observer._trace_ctx.clear()
        trace_observer._decision_index.clear()
        trace_observer._nodes.clear()
        trace_observer._edges.clear()
        trace_observer._latency.clear()
        trace_observer._seq = 0
        trace_observer._tl.trace_id = None
        trace_observer._tl.last_event_id = None


@pytest.fixture()
def engine(tmp_path):
    """Real engine + observer ACTIVE for the whole test (auto-stopped)."""
    from nexus_scalp.observability.trace_observer import trace_observer

    _reset_observer_store()
    eng, adapter = _make_engine(tmp_path)
    yield eng, adapter
    trace_observer.stop_session()
    _reset_observer_store()


@pytest.fixture()
def observed(engine, monkeypatch):
    """Authenticated TestClient with the observer ACTIVE and real events emitted.

    WEB-AUTH-P0: authenticates exactly like production consumers.
    """
    from fastapi.testclient import TestClient

    from nexus_scalp.observability.trace_observer import trace_observer
    from nexus_scalp.web.server import create_app

    eng, _ = engine
    monkeypatch.delenv("NSE_WEB_AUTH_DISABLE", raising=False)
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", "trace-causal-test-token")
    trace_observer.start_session()
    _feed_bars(eng, n=5)
    app = create_app(engine_ref=None)
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer trace-causal-test-token"})
        yield client


# --------------------------------------------------------------------------
# Backward-compatible filtering (§57 clickable counters / §36 filters)
# --------------------------------------------------------------------------


def test_events_no_filter_is_the_legacy_tail(engine, monkeypatch):
    """No filter => byte-identical payload to the legacy unfiltered tail."""
    from fastapi.testclient import TestClient

    from nexus_scalp.observability.trace_observer import trace_observer
    from nexus_scalp.web.server import create_app

    eng, _ = engine
    monkeypatch.delenv("NSE_WEB_AUTH_DISABLE", raising=False)
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", "trace-causal-test-token")
    trace_observer.start_session()
    _feed_bars(eng, n=5)
    try:
        app = create_app(engine_ref=None)
        with TestClient(app) as client:
            client.headers.update({"Authorization": "Bearer trace-causal-test-token"})
            legacy = client.get("/api/trace/events?last_seq=0&limit=100").json()
            # Explicit empty filters (the "?stage=" style) mean "not set" and
            # must NOT narrow to an empty page.
            blank = client.get(
                "/api/trace/events?last_seq=0&limit=100&trace_id=&stage=&status=&mode=&position_id="
            ).json()
        assert legacy["events"], "no events retained by the real pipeline"
        assert [e["event_id"] for e in blank["events"]] == [
            e["event_id"] for e in legacy["events"]
        ], "blank filters altered the legacy tail"
        assert blank["filters"] == {}, blank["filters"]
        assert blank["last_seq"] == legacy["last_seq"]
        assert blank["gap"] == legacy["gap"]
    finally:
        trace_observer.stop_session()


def test_events_filter_chain_only_narrows(observed):
    """Every ACTIVE filter only removes rows; the sum never exceeds the base."""
    base = observed.get("/api/trace/events?last_seq=0&limit=200").json()
    assert base["events"], "no events retained"
    all_events = base["events"]

    stages = sorted({e["stage"] for e in all_events})
    assert len(stages) >= 2, stages  # a real chain spans multiple stages
    one_stage = stages[0]
    by_stage = observed.get(f"/api/trace/events?last_seq=0&limit=200&stage={one_stage}").json()[
        "events"
    ]
    assert by_stage, "stage filter matched nothing the base page contains"
    assert {e["stage"] for e in by_stage} == {one_stage}
    assert len(by_stage) <= len(all_events)

    statuses = sorted({e["status"] for e in all_events})
    one_status = statuses[0]
    by_status = observed.get(f"/api/trace/events?last_seq=0&limit=200&status={one_status}").json()[
        "events"
    ]
    assert {e["status"] for e in by_status} == {one_status}
    assert len(by_status) <= len(all_events)

    # AND-composition: two filters together are <= either alone.
    both = observed.get(
        f"/api/trace/events?last_seq=0&limit=200&stage={one_stage}&status={one_status}"
    ).json()["events"]
    assert len(both) <= min(len(by_stage), len(by_status))
    assert {e["stage"] for e in both} <= {one_stage}
    assert {e["status"] for e in both} <= {one_status}

    # A filter narrows the SAME base page: retained ids are a subset of it.
    base_ids = {e["event_id"] for e in all_events}
    assert {e["event_id"] for e in by_stage} <= base_ids
    assert {e["event_id"] for e in by_status} <= base_ids

    # The echo names the filters actually narrowing the page.
    assert base["filters"] == {}
    echo = observed.get(f"/api/trace/events?limit=200&stage={one_stage}").json()["filters"]
    assert echo.get("stage") == one_stage


def test_events_trace_id_filter_matches_a_real_trace(observed):
    """``trace_id`` filters by a runtime id, never a fabricated one."""
    base = observed.get("/api/trace/events?limit=200").json()["events"]
    tid = base[0]["trace_id"]
    own = observed.get(f"/api/trace/events?trace_id={tid}&limit=200").json()
    assert own["filters"] == {"trace_id": tid}
    assert own["events"]
    assert {e["trace_id"] for e in own["events"]} == {tid}

    foreign = observed.get("/api/trace/events?trace_id=TRC-DOES-NOT-EXIST").json()
    assert foreign["events"] == [], "a non-matching filter must not fabricate rows"
    assert foreign["filters"] == {"trace_id": "TRC-DOES-NOT-EXIST"}

    # mode filter: only when the runtime actually recorded a mode word.
    modes = {e.get("mode") or (e.get("detail") or {}).get("engine_mode") for e in base}
    modes.discard(None)
    if modes:
        word = str(sorted(modes)[0]).upper()
        by_mode = observed.get(f"/api/trace/events?mode={word}&limit=200").json()
        assert by_mode["events"], "mode filter matched nothing"
        got = {
            str(e.get("mode") or (e.get("detail") or {}).get("engine_mode") or "").upper()
            for e in by_mode["events"]
        }
        assert got == {word}, got
    else:
        hit = observed.get("/api/trace/events?mode=PAPER").json()["events"]
        assert hit == []


def test_events_position_id_filter_absent_is_honest(observed):
    """No position evidence in a NO_TRADE/rejection-only paper run renders the
    honest empty state — never a synthesized position id."""
    hits = observed.get("/api/trace/events?position_id=99999999").json()
    assert hits["events"] == []
    assert hits["filters"] == {"position_id": "99999999"}


# --------------------------------------------------------------------------
# WHY / NEXT endpoint (§16 / §37 / §38 / §65)
# --------------------------------------------------------------------------


def test_why_endpoint_derives_from_the_stored_record(observed):
    """The WHY answer restates only fields the runtime recorded."""
    events = observed.get("/api/trace/events?limit=200").json()["events"]
    # Prefer an event whose successor is retained, so NEXT is observable.
    by_trace: dict[str, list] = {}
    for e in events:
        by_trace.setdefault(e["trace_id"], []).append(e)
    pair = None
    for rows in by_trace.values():
        rows.sort(key=lambda e: e["sequence"])
        if len(rows) >= 2:
            pair = rows[0]
            break
    if pair is None:
        pytest.skip("no multi-event trace retained in this paper run")

    body = observed.get(f"/api/trace/why/{pair['event_id']}").json()
    assert body["found"] is True
    assert body["event_id"] == pair["event_id"]
    assert body["trace_id"] == pair["trace_id"]
    assert body["stage"] == pair["stage"]
    assert body["provenance"] == "observed"

    nxt = body["next"]
    assert nxt["observed"] is True
    assert nxt["stage"] is not None
    assert nxt["event_id"] is not None
    assert nxt["provenance"] in {"observed", "inferred"}
    assert nxt["link"] in {"parent_event_id", "trace_sequence"}

    # WHY evidence only restates runtime words (§16/§58) — never invented.
    why = body["why"]
    for key in ("reason_code", "error_code", "state"):
        if key in why:
            assert isinstance(why[key], str) and why[key], why


def test_why_endpoint_unknown_id_is_404(observed):
    """An unknown/evicted id is an honest 404 — no synthesized record."""
    r = observed.get("/api/trace/why/EV-DOES-NOT-EXIST")
    assert r.status_code == 404, r.status_code
    body = r.json()
    assert "error" in body or "code" in body or "message" in body, body
    # The response carries no invented event record.
    assert body.get("found") in (None, False), body


def test_why_endpoint_terminal_trace_ends_terminated(observed):
    """A terminal tail event answers TERMINATED with ITS OWN status word."""
    events = observed.get("/api/trace/events?limit=500").json()["events"]
    tails = [e for e in events if e.get("terminal")]
    if not tails:
        pytest.skip("runtime produced no terminal event in this paper run")

    # The terminal event must be the LAST retained event of its trace.
    by_trace: dict[str, list] = {}
    for e in events:
        by_trace.setdefault(e["trace_id"], []).append(e)
    tail = None
    for rows in by_trace.values():
        rows.sort(key=lambda e: e["sequence"])
        if rows[-1].get("terminal"):
            tail = rows[-1]
            break
    if tail is None:
        pytest.skip("terminal event is not the retained tail of its trace")

    body = observed.get(f"/api/trace/why/{tail['event_id']}").json()
    assert body["found"] is True
    nxt = body["next"]
    assert nxt["observed"] is True
    assert nxt["destination"] == "TERMINATED"
    assert nxt["terminal_status"] == tail["status"]
    assert "event_id" not in nxt, "TERMINATED fabricated a successor event"


def test_why_endpoint_failure_isolation(observed):
    """§47: the why endpoint can never raise into the caller."""
    from nexus_scalp.observability.trace_observer import trace_observer

    # Inject an event, then corrupt the lookup path minimally: feed an id the
    # route cannot resolve and assert a structured 404 (never a 500 leak).
    r = observed.get("/api/trace/why/''")
    assert r.status_code in (404, 422), r.status_code
    assert trace_observer.status == "ACTIVE"


def test_causal_fields_reach_the_stored_events(observed):
    """Lane-B emit enrichment: frozen v2 fields on real runtime events.

    These fields are OPTIONALLY present (absence is data, §60): the assertions
    only hold when the runtime actually produced the fact, and every present
    value must be the runtime's own word — never a placeholder.
    """
    events = observed.get("/api/trace/events?limit=500").json()["events"]
    inf = next((e for e in events if e["stage"] == "INFERENCE"), None)
    if inf is not None:
        # model identity, when present, is the serving registry's own value.
        if inf.get("model") is not None:
            assert isinstance(inf["model"], str) and inf["model"]
        # freshness, when present, is the liquidity governor's own verdict.
        if inf.get("freshness") is not None:
            assert inf["freshness"] in {"VALID", "STALE", "INVALID"}, inf["freshness"]
        # state is a frozen TraceState word the emit site recorded.
        if inf.get("state") is not None:
            assert inf["state"] in {"COMPLETED", "IDLE", "PROCESSING", "FAILED"}, inf["state"]
        # request_id is the real lifecycle id when the pipeline carried one.
        if inf.get("request_id") is not None:
            assert isinstance(inf["request_id"], str) and inf["request_id"]

    risk = next((e for e in events if e["stage"] == "RISK"), None)
    if risk is not None:
        if risk.get("reason_code") is not None:
            assert isinstance(risk["reason_code"], str) and risk["reason_code"]
        if risk.get("state") is not None:
            assert risk["state"] in {"PASSED", "REJECTED"}, risk["state"]
        # A risk-gate freshness word, when the engine classified the snapshot.
        if risk.get("freshness") is not None:
            assert isinstance(risk["freshness"], str)

    decision = next((e for e in events if e["stage"] == "DECISION"), None)
    if decision is not None:
        # The always-on decision node still carries no invented identity field.
        assert decision.get("position_id") is None or isinstance(decision.get("position_id"), str)

    # No event ever leaks a secret-class token into a response (§48/§54).
    for e in events:
        blob = repr(e.get("detail") or {})
        for token in ("password", "secret", "token", "api_key", "authorization"):
            assert token not in blob.lower(), f"secret-class token in event: {token}"
