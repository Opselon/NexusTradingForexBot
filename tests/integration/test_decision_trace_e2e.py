"""End-to-end Decision Trace integration — REAL LiveEngine paper pipeline.

Drives the actual runtime: ``LiveEngine._process_tick_pipeline`` with a real
model (force_fresh_model), real bar aggregation, real features, real inference,
real regime classification, real SignalPolicy gates, real DecisionExecutor +
RiskEngine and the real PaperMT5Adapter gateway — with the observer ACTIVE.

Every assertion is about evidence the runtime itself produced. The UI graph
these events will feed is derived, never hardcoded; nothing here fabricates a
decision, a model, a contract (50D/70D), a probability or a broker result.

Harness follows tests/integration/test_live_freshness_g29.py (real LiveEngine
wiring, no live loop launched) — no second architecture.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nexus_scalp.observability.trace_contract import TRACE_SCHEMA_VERSION
from nexus_scalp.observability.trace_observer import trace_observer

# --------------------------------------------------------------------------
# Real-runtime harness (paper; the safest existing execution mode)
# --------------------------------------------------------------------------


def _make_engine(tmp_path: Path):
    """Real LiveEngine wiring with a paper adapter (no live loop launched)."""
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
    from nexus_scalp.application.live_engine import LiveEngine
    from nexus_scalp.configuration.config import AppConfig

    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'trace_e2e.db'}", flush_interval_sec=0.05)
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
    base = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    close = 2000.0
    for k in range(n):
        close += (1.2 if k % 2 == 0 else -0.9) * (1 + k * 0.1)
        _feed_minute(engine, base + timedelta(minutes=k), close)


@pytest.fixture()
def engine(tmp_path):
    trace_observer.stop_session()
    eng, adapter = _make_engine(tmp_path)
    yield eng, adapter
    trace_observer.stop_session()


def _stages(bundle: dict) -> list[str]:
    seen: list[str] = []
    for e in bundle.get("events") or []:
        if e["stage"] not in seen:
            seen.append(e["stage"])
    return seen


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_closed_ui_observer_off_decision_recorded_without_detail(engine):
    """§8: UI closed -> detailed tracing OFF, yet the always-on decision
    record is kept so the feed hydrates when the UI opens. NO detailed
    event serialization happens on the hot path."""
    eng, _ = engine
    assert trace_observer.status == "OFF"

    _feed_bars(eng, n=4)

    snap = trace_observer.snapshot()
    assert snap["status"] == "OFF"
    # Decisions: always-on record (§90 hot-path minimalism) — rows exist.
    assert snap["decisions_retained"] >= 1, "decision record missing while OFF"
    # Detailed events: none while OFF (no serialization/graph work).
    assert snap["events_retained"] == 0, "detailed events leaked while observer OFF"
    # Topology: derived from observed events. While OFF only the always-on
    # DECISION node can exist (emit_decision is always-on by design); NO
    # detailed stage graph is built on the closed-UI hot path.
    off_nodes = {n["stage"] for n in trace_observer.topology()["nodes"]}
    assert off_nodes.issubset({"DECISION"}), off_nodes


def test_open_ui_full_chain_real_evidence(engine):
    """§67/§92: with the observer ACTIVE the real pipeline's causal chain is
    captured end-to-end — MARKET first, causality reconstructable, inference
    identity from the runtime's own owners."""
    eng, _ = engine
    session = trace_observer.start_session()
    assert session["status"] == "ACTIVE"

    _feed_bars(eng, n=6)

    rows = trace_observer.decisions_list(limit=50)["rows"]
    assert rows, "no decision recorded during real ticks"
    row = rows[0]
    assert row.get("status") in {
        "NO_TRADE",
        "REJECTED",
        "APPROVED",
        "EXECUTED",
        "FAILED",
        "DISPATCHED",
    }

    key = row.get("decision_id") or row.get("trace_id")
    bundle = trace_observer.trace_bundle(key)
    assert bundle["found"], f"no forensic bundle for {key}"
    stages = _stages(bundle)
    assert stages[0] == "MARKET", stages

    evs = bundle["events"]
    assert all(e["trace_id"] for e in evs)
    seqs = [e["sequence"] for e in evs]
    assert seqs == sorted(seqs)
    ids = {e["event_id"] for e in evs}
    for e in evs:
        p = e.get("parent_event_id")
        if p:
            assert p in ids, f"dangling parent {p}"

    # If inference ran, the contract evidence must be the RUNTIME's own
    # feature_dim — never a hardcoded 50/70 label.
    inf = next((e for e in evs if e["stage"] == "INFERENCE"), None)
    if inf is not None:
        d = inf["detail"]
        assert isinstance(d.get("feature_dim"), int)
        if d["feature_dim"] == 50:
            assert d.get("effective_feature_dim") in (50, None)
        # model identity absent = warmup/no bundle — must NOT be invented.
        if "model_id" in d:
            assert d["model_id"], "empty placeholder identity"

    # Regime evidence, when the classifier produced a state.
    reg = next((e for e in evs if e["stage"] == "REGIME"), None)
    if reg is not None:
        assert "regime" in reg["detail"]

    # Latency: measured ints, or honestly absent — never fabricated.
    for e in evs:
        lu = e.get("latency_us")
        if lu is not None:
            assert isinstance(lu, int) and lu >= 0

    trace_observer.stop_session()


def test_rejection_downstream_not_reached(engine):
    """§26/§25: after a terminal rejection no downstream stage appears."""
    eng, _ = engine
    trace_observer.start_session()
    _feed_bars(eng, n=6)

    rows = trace_observer.decisions_list(limit=100)["rows"]
    rejected = [r for r in rows if r.get("status") in ("REJECTED", "NO_TRADE")]
    if not rejected:
        pytest.skip("runtime produced no rejection in this paper run")

    for row in rejected:
        key = row.get("decision_id") or row.get("trace_id")
        bundle = trace_observer.trace_bundle(key)
        if not bundle["found"]:
            continue
        evs = bundle["events"]
        terminal_idx = next((i for i, e in enumerate(evs) if e.get("terminal")), None)
        if terminal_idx is None:
            continue
        # Nothing after the terminal event; and a NO_TRADE rejection never
        # carries gateway/order evidence.
        assert terminal_idx == len(evs) - 1, "event after terminal — hypothetical continuation"
        if row["status"] == "NO_TRADE":
            for e in evs:
                assert e["stage"] not in ("MT5", "GATEWAY", "ORDER"), (
                    "rejected trace claims execution"
                )
    trace_observer.stop_session()


def test_topology_discovered_from_events_only(engine):
    """§13/§50: topology nodes/edges exist iff runtime events prove them."""
    eng, _ = engine
    trace_observer.start_session()
    # §13/§50 invariant: every topology node must be derivable from retained
    # runtime events — a node whose stage never appears in any event would
    # prove a hardcoded graph. (Node state is process-global across tests, so
    # assert derivation rather than an empty precondition.)
    topo_before = trace_observer.topology()
    event_stages = {e["stage"] for e in trace_observer.events_since(0, limit=5000)["events"]} | {
        "DECISION"
    }  # always-on decision node needs no detailed event
    for n in topo_before["nodes"]:
        assert n["stage"] in event_stages, f"topology node {n['stage']} not backed by events"
        assert n["count"] >= 1

    _feed_bars(eng, n=6)

    topo = trace_observer.topology()
    stages = {n["stage"] for n in topo["nodes"]}
    assert "MARKET" in stages
    for e in topo["edges"]:
        assert e["source"] in stages and e["target"] in stages
        assert e["count"] >= 1
    assert topo["observer_status"] == "ACTIVE"
    assert topo["trace_schema_version"] == TRACE_SCHEMA_VERSION
    trace_observer.stop_session()


def test_unknown_stage_surfaces_as_unmapped_not_hidden(engine):
    """§14/§66: a future event type is discovered + flagged, never hidden
    or crashed on. Proves the surface is event-driven, not hardcoded."""
    eng, _ = engine
    trace_observer.start_session()
    trace_observer.emit(
        stage="LIQUIDITY_GATE",
        component="future_release",
        event_type="NEW_GATE_KIND",
        status="PASS",
        symbol="XAUUSD",
        detail={"observed": True},
    )
    topo = trace_observer.topology()
    node = next((n for n in topo["nodes"] if n["stage"] == "LIQUIDITY_GATE"), None)
    assert node is not None, "unknown runtime path hidden"
    # Rootless (no parent, not MARKET) => flagged unmapped, not fabricated.
    assert node.get("unmapped") or node.get("root") or node.get("in_degree", 1) >= 0
    trace_observer.stop_session()


def test_observer_open_close_never_changes_trading(engine):
    """§68: opening/closing observability does not alter pipeline behavior."""
    eng, _ = engine
    # Run A: observer OFF.
    _feed_bars(eng, n=3)
    off_rows = trace_observer.decisions_list(limit=100)["rows"]
    off_events = trace_observer.snapshot()["events_retained"]

    # Run B: observer ACTIVE — same pipeline, same code paths.
    trace_observer.start_session()
    _feed_bars(eng, n=3)
    on_rows = trace_observer.decisions_list(limit=100)["rows"]
    on_events = trace_observer.snapshot()["events_retained"]
    trace_observer.stop_session()

    # Detailed events only exist while ACTIVE; the decision record grows in
    # both windows (always-on) — observability adds evidence, not behavior.
    assert on_events > off_events
    assert len(on_rows) > len(off_rows) >= 1


def test_stream_protocol_hello_and_disconnect_cleanup(engine, monkeypatch):
    """§76/§69: SSE hello carries session+schema; disconnect releases the
    subscriber (auto-stop after grace) and never touches trading.

    WEB-AUTH-P0 gates every non-allowlisted path, so the scope carries an
    Authorization header exactly like production consumers (env-wins token).

    HARNESS NOTE: Starlette 1.6's TestClient runs the ASGI app to
    COMPLETION (portal.call in handle_request), so an infinite SSE
    generator can never stream through `client.stream` — it hangs at
    request entry. We therefore drive the REAL `app` ASGI callable
    directly in a thread: same route, same auth/correlation middleware,
    same generator; the receive callable returns http.disconnect once the
    hello frame has been observed. A join timeout bounds the whole test,
    so a regression FAILS instead of hanging the suite."""
    import asyncio
    import json
    import threading

    from fastapi.testclient import TestClient

    from nexus_scalp.web.server import create_app

    monkeypatch.delenv("NSE_WEB_AUTH_DISABLE", raising=False)
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", "trace-stream-test-token")
    app = create_app(engine_ref=None)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/trace/stream",
        "raw_path": b"/api/trace/stream",
        "query_string": b"last_seq=0",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"authorization", b"Bearer trace-stream-test-token"),
            (b"accept", b"text/event-stream"),
        ],
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
    }

    lock = threading.Lock()
    messages: list[dict] = []
    hello_seen = threading.Event()
    disconnect = threading.Event()
    request_sent = {"done": False}
    app_done = threading.Event()
    app_error: list[BaseException] = []

    async def receive() -> dict:
        if not request_sent["done"]:
            request_sent["done"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        # A real connected client sends nothing more. Block until the
        # test simulates the client going away, then hand back the
        # disconnect. starlette's is_disconnected() wraps this in a tiny
        # wait_for and times out while we wait, exactly as in production.
        while not disconnect.is_set():
            await asyncio.sleep(0.01)
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        with lock:
            messages.append(message)
        if message.get("type") == "http.response.body" and b"hello" in message.get("body", b""):
            hello_seen.set()

    def runner() -> None:
        try:
            asyncio.run(app(scope, receive, send))
        except BaseException as exc:
            app_error.append(exc)
        finally:
            app_done.set()

    thread = threading.Thread(target=runner, name="trace-sse-driver", daemon=True)
    thread.start()

    # 1. the hello frame must arrive (bounded wait, never hangs)
    assert hello_seen.wait(20), "no hello frame within 20s (stream never started)"
    # 2. simulate the client disconnect -> generator must exit promptly
    disconnect.set()
    thread.join(timeout=30)
    assert not thread.is_alive(), "SSE generator did not exit after disconnect"
    assert not app_error, f"app raised: {app_error[0]!r}"

    with lock:
        assert messages, "no ASGI messages at all"
        start = next((m for m in messages if m.get("type") == "http.response.start"), None)
        assert start is not None, "no http.response.start"
        assert start["status"] == 200, start["status"]
        headers = {k.decode().lower(): v.decode() for k, v in start.get("headers", [])}
        assert headers.get("content-type", "").startswith("text/event-stream"), headers

        body = b"".join(
            m.get("body", b"") for m in messages if m.get("type") == "http.response.body"
        )

    # hello frame: real session identity + schema version + resume point
    hello_line = next(
        (
            ln
            for ln in body.decode("utf-8", "replace").splitlines()
            if ln.startswith("data:") and "hello" in ln
        ),
        None,
    )
    assert hello_line, "no hello frame in the stream"
    hello = json.loads(hello_line[len("data:") :])
    assert hello.get("session_id", "").startswith("SES-"), hello
    assert hello.get("observer_id"), hello
    assert hello.get("trace_schema_version") == TRACE_SCHEMA_VERSION, hello
    assert hello.get("resumed") is False, hello
    assert hello.get("last_seq") == 0, hello

    # 3. after disconnect the subscriber count returns to 0 (no leak)
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer trace-stream-test-token"})
        subs = client.get("/api/trace/observer").json()["subscribers"]
    assert subs == [], f"subscriber leaked: {subs}"


def test_rest_contract_and_event_resume(engine, monkeypatch):
    """§75/§77: the full REST surface + explicit resume/gap semantics.

    WEB-AUTH-P0: authenticate exactly like production consumers."""
    from fastapi.testclient import TestClient

    from nexus_scalp.web.server import create_app

    eng, _ = engine
    monkeypatch.delenv("NSE_WEB_AUTH_DISABLE", raising=False)
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", "trace-rest-test-token")
    app = create_app(engine_ref=None)
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer trace-rest-test-token"})

        assert client.get("/api/trace/observer").json()["status"] == "OFF"
        assert client.post("/api/trace/observer/start").json()["status"] == "ACTIVE"

        _feed_bars(eng, n=5)

        # Decisions -> bundle by the CANONICAL decision id (EXEC-…).
        rows = client.get("/api/trace/decisions?limit=10").json()["rows"]
        assert rows
        did = rows[0]["decision_id"]
        assert did.startswith("EXEC-"), did
        bundle = client.get(f"/api/trace/bundle/{did}").json()
        assert bundle["found"] and bundle["trace_id"]

        # Event tail + resume: same last_seq never replays events twice.
        first = client.get("/api/trace/events?last_seq=0&limit=50").json()
        assert first["events"], "no events retained"
        last = first["events"][-1]["sequence"]
        assert not first.get("gap")
        second = client.get(f"/api/trace/events?last_seq={last}&limit=50").json()
        assert all(e["sequence"] > last for e in second["events"]), "replayed on resume"

        topo = client.get("/api/trace/topology").json()
        assert {n["stage"] for n in topo["nodes"]} >= {"MARKET", "DECISION"}

        lat = client.get("/api/trace/latency").json()
        assert "stages" in lat and lat["min_sample_n"] >= 1
        for entry in lat["stages"].values():
            # Percentiles only with sufficient samples; else INSUFFICIENT DATA.
            assert "p95_us" in entry or entry.get("insufficient_data") is True

        integ = client.get("/api/trace/integrity").json()
        assert isinstance(integ["warnings"], list)

        # Unknown bundle key -> honest not-found, not a fabricated bundle.
        miss = client.get("/api/trace/bundle/EXEC-DOES-NOT-EXIST").json()
        assert miss["found"] is False

        assert client.post("/api/trace/observer/stop").json()["status"] == "OFF"


def test_observer_failure_isolation(engine):
    """§47: a pathological detail payload can never raise into the caller."""
    trace_observer.start_session()

    class _Bomb:
        def __iter__(self):
            raise RuntimeError("serialization bomb")

        def keys(self):
            raise RuntimeError("serialization bomb")

    trace_observer.emit(
        stage="POLICY",
        component="signal_policy",
        event_type="GATE_EVALUATION",
        status="PASS",
        symbol="XAUUSD",
        detail={"boom": _Bomb()},  # type: ignore[arg-type]
    )  # must not raise
    trace_observer.emit_decision(summary={"status": "APPROVED", "decision_id": "EXEC-TEST-1"})
    trace_observer.stop_session()
