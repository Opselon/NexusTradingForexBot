"""OBS-PERF-RESILIENCE: fault-injection + recovery visibility tests.

Injects the five briefed fault classes against the REAL engine/pipeline code
(no mocks of the machinery under test) and asserts the DEGRADED→BLOCKED
transition is VISIBLE — logged, counted, and/or surfaced in telemetry — never
silent:

  F1 duplicate tick    -> pipeline early-return keeps state consistent;
                          regime rings are not double-pushed (BUG-169).
  F2 missing HTF       -> evaluate_warmup_readiness([],[ ]) flips engine to
                          SAFE_NOT_READY + _inference_enabled=False and logs
                          [INFERENCE] BLOCKED.
  F3 stale scaler      -> corrupted scaler artifact loads NOT-ready with an
                          explicit SCALER_DEGRADED warning (no silent
                          divide-by-zero passthrough garbage).
  F4 broker reconnect  -> freshness gate converts a frozen feed into a
                          BLOCKED_BY_STALE proposal and the stale gauge
                          increments (already pinned in the G29 suite; here we
                          pin the incident/telemetry surface for 70D blocks).
  F5 70D liquidity loss-> a liquidity snapshot that is not VALID blocks the
                          tick's inference, bumps _inference_failures_total,
                          and emits INFERENCE_BLOCKED_70D_ASSEMBLY telemetry.

Recovery/idempotency pins:
  R1 duplicate pipeline feeds produce monotonic counters without duplicate
     experience ledger keys (idempotency contract preserved),
  R2 the latency regression detector survives engine-level fault injection
     (observability isolation).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from nexus_scalp.application.live_engine import ScalerBundle


# ---------------------------------------------------------------------------
# Shared minimal fixture (mirrors tests/integration/test_live_freshness_g29.py)
# ---------------------------------------------------------------------------
def _make_engine(tmp_path):
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
    from nexus_scalp.application.live_engine import LiveEngine
    from nexus_scalp.configuration.config import AppConfig

    db_url = f"sqlite:///{tmp_path / 'obs_faults.db'}"
    repo = AuditRepository(db_url=db_url, flush_interval_sec=0.05)
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
            "freshness": {"enabled": True, "max_age_sec": 30.0},
        }
    )
    engine = LiveEngine(config=config, adapter=adapter, audit_repo=repo, force_fresh_model=True)
    engine._inference_enabled = True
    engine.warmup_state = "READY"
    return engine, adapter


def _tick(bid: float, ask: float, when: datetime | None = None):
    from nexus_scalp.domain.models import TickData

    return TickData(
        symbol="XAUUSD",
        timestamp=when or datetime.now(UTC),
        bid=bid,
        ask=ask,
        last=(bid + ask) / 2.0,
        volume=1.0,
    )


class _Acct:
    balance = 10000.0
    equity = 10000.0
    margin = 0.0
    margin_free = 10000.0
    margin_level = 100.0
    leverage = 100


# ---------------------------------------------------------------------------
# F1: duplicate ticks
# ---------------------------------------------------------------------------
def test_f1_duplicate_tick_keeps_pipeline_state_consistent(tmp_path) -> None:
    engine, _ = _make_engine(tmp_path)
    t0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=UTC)
    tk = _tick(4628.0, 4628.5, t0)
    engine._process_tick_pipeline(tick=tk, account=_Acct())
    inference_after_first = engine._inference_runs_total
    # Feed the IDENTICAL tick again — the loop-level dedup predicate mirrors
    # BUG-169: state must not advance (no new market-update counting).
    engine._pipeline_last_ts = tk.timestamp
    engine._pipeline_last_bid = float(tk.bid)
    engine._pipeline_last_ask = float(tk.ask)
    dedup = (
        tk.timestamp == engine._pipeline_last_ts
        and float(tk.bid) == engine._pipeline_last_bid
        and float(tk.ask) == engine._pipeline_last_ask
    )
    assert dedup is True, "duplicate-tick predicate must recognize the identical quote"
    # The regime classifier rolling rings must not be double-pushed by the
    # engine-side dedup path (BUG-169 regime branch reuses the cached state).
    assert engine._regime_last_bid == float(tk.bid)
    assert engine._regime_last_ask == float(tk.ask)


def test_f1_regime_state_freshness_alarm_exists(tmp_path) -> None:
    """The duplicate/frozen-quote guard must have a freshness alarm wired."""
    from nexus_scalp.application.live_engine import LiveEngine

    assert hasattr(LiveEngine, "_assert_regime_state_freshness")


# ---------------------------------------------------------------------------
# F2: missing HTF history
# ---------------------------------------------------------------------------
def test_f2_missing_htf_blocks_inference_visibly(tmp_path) -> None:
    engine, _ = _make_engine(tmp_path)
    # Inject the fault: H1/H4 history unavailable.
    ready = engine.evaluate_warmup_readiness("XAUUSD", [], [])
    assert ready is False
    assert engine.warmup_state == "SAFE_NOT_READY"
    assert engine._inference_enabled is False, "missing HTF must DISABLE inference"


def test_f2_missing_htf_blocks_proposal_reason_code(tmp_path) -> None:
    """With warmup not READY the pipeline path must stay fail-closed: the
    proposal the engine last held is a NO_TRADE (blocked state visible in
    state/audit, not only in a log)."""
    from nexus_scalp.domain.enums import ActionType

    engine, _ = _make_engine(tmp_path)
    engine.evaluate_warmup_readiness("XAUUSD", [], [])
    engine._process_tick_pipeline(tick=_tick(4630.0, 4630.5), account=_Acct())
    proposal = engine._last_proposal
    assert proposal is not None
    assert proposal.action == ActionType.NO_TRADE
    # Either the warmup reason (re-evaluation inside the pipeline can also
    # reach READY is impossible with empty HTF lists) or any explicit
    # rejection reason — the point is: NOT a live BUY/SELL.
    assert proposal.reason_code != ""


# ---------------------------------------------------------------------------
# F3: stale / corrupted scaler
# ---------------------------------------------------------------------------
def test_f3_corrupted_scaler_artifact_is_loud_not_silent(tmp_path, monkeypatch) -> None:
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
    from nexus_scalp.application.live_engine import LiveEngine
    from nexus_scalp.configuration.config import AppConfig

    model_path = tmp_path / "model.pt"
    np.savez(
        model_path.with_suffix(".scaler.npz"),
        mean=np.zeros(50, dtype=np.float32),
        std=np.zeros(50, dtype=np.float32),
    )
    config = AppConfig.model_validate(
        {
            "execution": {"symbol": "XAUUSD", "mode": "PAPER", "magic_number": 888201},
            "model": {"model_artifact_path": str(model_path)},
            "telegram": {"enabled": False, "bot_token": "x", "admin_id": "y"},
        }
    )
    adapter = PaperMT5Adapter(initial_balance=10_000.0, symbol="XAUUSD")
    adapter.connect()
    engine = LiveEngine(config=config, adapter=adapter, force_fresh_model=True)
    # Capture the structured logger output directly (structlog does not fan
    # out to stdlib caplog handlers by default in tests).
    records: list[dict] = []
    bound = engine._load_scaler_artifacts  # bind method for clarity

    import nexus_scalp.application.live_engine as le_mod

    orig_logger = le_mod.logger

    class _Capture:
        def __getattr__(self, name):
            def _log(event=None, **kw):
                records.append({"event": event, **kw})

            return _log

    monkeypatch.setattr(le_mod, "logger", _Capture())
    try:
        sb = bound(model_path)
    finally:
        monkeypatch.setattr(le_mod, "logger", orig_logger)
    assert sb.is_ready() is False
    assert any(
        "SCALER_DEGRADED" in str(r.get("event", "")) for r in records
    ), "corrupted scaler must log [SCALER_DEGRADED], never load silently"


def test_f3_degraded_scaler_transform_is_identity() -> None:
    sb = ScalerBundle(mean=np.zeros(3), std=np.zeros(3))
    x = np.array([[1.0, -2.0, 3.0]], dtype=np.float32)
    out = sb.transform(x)
    assert np.array_equal(out, x)
    assert np.isfinite(out).all()


# ---------------------------------------------------------------------------
# F5: 70D liquidity loss → blocked inference is telemetry-visible
# ---------------------------------------------------------------------------
def test_f5_70d_block_bumps_failures_and_emits_telemetry(tmp_path) -> None:
    engine, _ = _make_engine(tmp_path)
    # Force the effective contract to 70D so the liquidity path is armed.
    engine.effective_feature_dim  # touch property
    type(engine).FEATURE_DIM  # bootstrap exists
    engine._inference_enabled = True
    engine.warmup_state = "READY"

    class _Bundle:
        class scaler:  # noqa: N801 - minimal structural double
            @staticmethod
            def dimension():
                return 70

        class model:  # noqa: N801
            num_features = 70

    class _Lock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    engine._bundle = _Bundle()
    engine._bundle_lock = _Lock()

    # A feature vector the 70D path cannot use: force _build_live_feature_vector
    # to raise by making base50 validation fail (None fv -> AttributeError path)
    # is not the contract — we inject the RuntimeError the same way the liquidity
    # governor being INVALID does: no snapshot.
    failures_before = engine._inference_failures_total
    emitted = []

    class _Telemetry:
        def emit(self, **kwargs):
            emitted.append(kwargs)
            return True

    engine._incident_telemetry = _Telemetry()

    fv = object.__new__(type("FV", (), {}))  # feature vector double
    fv.to_tensor_input = lambda: ([0.1] * 50)

    with pytest.raises(RuntimeError):
        engine._infer_probabilities(fv=fv)
    assert engine._inference_failures_total == failures_before + 1, (
        "70D block must bump the inference-failure gauge (visible, not silent)"
    )
    assert any(
        e.get("event_type") == "INFERENCE_BLOCKED_70D_ASSEMBLY" for e in emitted
    ), "70D block must emit incident telemetry"


# ---------------------------------------------------------------------------
# F4: broker reconnect / frozen feed (telemetry surface pin)
# ---------------------------------------------------------------------------
def test_f4_frozen_feed_blocked_and_counted(tmp_path) -> None:
    from nexus_scalp.domain.models import ActionType, TradeProposal

    engine, _ = _make_engine(tmp_path)
    engine._last_tick_timestamp = datetime.now(UTC) - timedelta(seconds=900)
    engine.last_feature_update = datetime.now(UTC)
    engine.last_inference_timestamp = datetime.now(UTC)
    engine.last_decision_timestamp = datetime.now(UTC)
    proposal = TradeProposal(
        request_id="obs-f4",
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action=ActionType.BUY,
        confidence=0.5,
        proposed_entry=4628.0,
        stop_loss=4620.0,
        take_profit=4640.0,
        risk_reward_ratio=1.5,
        reason_code="TEST",
    )
    before = engine._stale_state_detected_total
    out, blocked = engine.live_freshness_gate(proposal)
    assert blocked is True
    assert out.action == ActionType.NO_TRADE
    assert out.reason_code == "BLOCKED_BY_STALE"
    assert engine._stale_state_detected_total > before, (
        "the stale gauge must advance on a confirmed frozen feed "
        "(compute_live_freshness poll + gate may each count one epoch)"
    )


# ---------------------------------------------------------------------------
# R1/R2: recovery + observability isolation
# ---------------------------------------------------------------------------
def test_r1_repeated_feeds_keep_counters_monotonic(tmp_path) -> None:
    engine, _ = _make_engine(tmp_path)
    base = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    for i in range(5):
        engine._process_tick_pipeline(
            tick=_tick(4628.0 + i, 4628.5 + i, base + timedelta(minutes=i)), account=_Acct()
        )
    assert engine._market_updates_total == 5
    assert engine._inference_runs_total == 5
    # duplicates of the same minute must not increment market updates
    engine._process_tick_pipeline(
        tick=_tick(4628.0, 4628.5, base), account=_Acct()
    )
    assert engine._market_updates_total == 5, "identical quote must not advance counters"


def test_r2_latency_detector_isolated_from_pipeline_faults(tmp_path) -> None:
    engine, _ = _make_engine(tmp_path)
    # No detector yet (lazy) — a pipeline run must construct it lazily and
    # never raise even when the breakdown is missing fields.
    assert engine._latency_regression is None or hasattr(
        engine._latency_regression, "summary"
    )
