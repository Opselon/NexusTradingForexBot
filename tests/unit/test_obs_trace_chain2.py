"""OBS-TRACE-2 regression tests (Agent 8, observability wave 2026-09-11).

Second provenance/auditability wave on top of test_obs_trace_chain.py. Every
production decision must be independently reconstructable and attributable
without trusting the log message itself. Defects pinned here:

1. Early-return decisions left the EXEC correlation chain broken: the
   degraded-inference return, the frequency-throttle gate, the exposure
   gates, and the AI-reversal veto all emitted proposals with an EMPTY
   execution_id, and the AI-reversal veto additionally returned BEFORE the
   [EXEC_TRACE] emit. A reversal CLOSE (and the flip order it triggers)
   could therefore not be joined back to its decision row by any id.
   Now: EVERY proposal the policy emits carries the evaluation's
   execution_id (EXEC-YYYYMMDD-HHMMSS-xxxxxx).

2. The web-auth token generation log claimed "Retrieve ONCE from this log
   line" while the logging pipeline's key-based redactor scrubs every
   secret-bearing key (token=... -> [REDACTED_SECRET]) — a log-as-authority
   claim pointing operators at evidence that cannot exist. Credentials never
   belong in logs: the value is no longer logged and the message no longer
   makes the false claim.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import torch

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.signals.policy import SignalPolicy

# ---------------------------------------------------------------------------
# Fixtures (same shapes as the existing trace/flip pin suites)
# ---------------------------------------------------------------------------


def _feature_vector(**overrides) -> FeatureVector:
    import importlib.util
    import sys
    from pathlib import Path

    helper = Path(__file__).with_name("test_policy_flip_protection_pins_bug227.py")
    spec = importlib.util.spec_from_file_location("_flip_pins_obs_trace2", helper)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_flip_pins_obs_trace2", mod)
    spec.loader.exec_module(mod)
    return mod._feature_vector(**overrides)


def _tick(ts: datetime, seq: int = 0) -> TickData:
    bid = 2000.10 + seq * 0.01
    return TickData(symbol="XAUUSD", timestamp=ts, bid=bid, ask=bid + 0.05, volume=1.0)


@pytest.fixture
def policy() -> SignalPolicy:
    return SignalPolicy()


# ---------------------------------------------------------------------------
# 1. every emitted proposal carries the evaluation's execution_id
# ---------------------------------------------------------------------------


def test_degraded_inference_proposal_carries_execution_id(policy) -> None:
    """probs=None (BUG-253 degraded state) must still emit a joinable EXEC id."""
    proposal = policy.evaluate_probabilities(
        probabilities=None,
        current_tick=_tick(datetime.now(UTC)),
        feature_vector=_feature_vector(),
    )
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.reason_code == "PROBS_UNAVAILABLE_DEGRADED"
    assert proposal.execution_id, "degraded-inference NO_TRADE must carry execution_id"
    assert proposal.execution_id.startswith("EXEC-")


def test_frequency_throttled_proposal_carries_execution_id(policy) -> None:
    base = datetime.now(UTC)
    policy._last_signal_time = base - timedelta(seconds=10.0)  # inside 60s window
    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.65, 0.04, 0.24, 0.07]]),
        current_tick=_tick(base, seq=1),
        feature_vector=_feature_vector(),
    )
    assert proposal.reason_code == "ORDER_FREQUENCY_THROTTLED"
    assert proposal.execution_id, "throttled NO_TRADE must carry execution_id"
    assert proposal.execution_id.startswith("EXEC-")


def test_exposure_gate_proposal_carries_execution_id(policy) -> None:
    """Direct unit on _evaluate_exposure_limits: the exposure-gate NO_TRADE
    carries the caller's evaluation id (execution-state block stays joinable)."""
    base = datetime.now(UTC)
    proposal = policy._evaluate_exposure_limits(
        total_exposure=1,
        active_positions_count=1,
        active_pending_count=0,
        order_manager=None,
        live_tickets=[{"symbol": "XAUUSD", "magic": 888101, "price": 2000.0}],
        target_entry_price=2000.0,
        current_tick=_tick(base, seq=2),
        regime_str="TRENDING",
        regime_conf=0.6,
        atr=1.5,
        completed_bars=None,
        now=base,
        expected_symbol="XAUUSD",
        expected_magic=888101,
        execution_id="EXEC-20260911-140000-cafeb0",
    )
    assert proposal is not None
    assert proposal.reason_code == "MAX_EXPOSURE_REACHED"
    assert proposal.execution_id == "EXEC-20260911-140000-cafeb0"


def test_ai_reversal_proposal_carries_execution_id(policy) -> None:
    """The reversal veto returns BEFORE the [EXEC_TRACE] emit — the id must
    travel on the proposal itself so the CLOSE (and its flip order) stays
    joinable to the decision row."""
    fv = _feature_vector().model_copy(update={"is_below_kumo": True, "choch_bearish": True})
    proposal = policy._evaluate_ai_reversal(
        current_tick=_tick(datetime.now(UTC), seq=3),
        feature_vector=fv,
        held_position_dirs={1: "BUY"},
        prob_buy=0.10,
        prob_sell=0.55,
        no_trade_prob=0.10,
        atr=1.5,
        regime_str="TRENDING",
        regime_conf=0.5,
        execution_id="EXEC-20260911-120000-abcdef",
    )
    assert proposal is not None
    assert proposal.action == ActionType.CLOSE_POSITION
    assert proposal.is_ai_reversal is True
    assert proposal.execution_id == "EXEC-20260911-120000-abcdef"


def test_evaluation_id_is_unique_per_call(policy) -> None:
    base = datetime.now(UTC)
    ids = set()
    for i in range(3):
        p = policy.evaluate_probabilities(
            probabilities=None,
            current_tick=_tick(base + timedelta(seconds=i), seq=i),
            feature_vector=_feature_vector(),
        )
        ids.add(p.execution_id)
    assert len(ids) == 3, "each evaluation gets its own correlation id"


# ---------------------------------------------------------------------------
# 2. the reversal decision row is joinable in the audit DB by the EXEC id
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'audit.db'}")
    r._start_background_worker()
    yield r
    r.close()


def _flush(repo: AuditRepository) -> None:
    assert repo.flush(timeout_sec=5.0), "audit worker did not drain"


def test_reversal_signal_payload_joins_by_execution_id(repo) -> None:
    """audit_signals.payload.execution_id carries the EXEC id for the reversal
    CLOSE decision — the signal->order->flip chain is reconstructable."""
    fv = _feature_vector().model_copy(update={"is_below_kumo": True, "choch_bearish": True})
    policy = SignalPolicy()
    proposal = policy._evaluate_ai_reversal(
        current_tick=_tick(datetime.now(UTC), seq=4),
        feature_vector=fv,
        held_position_dirs={1: "BUY"},
        prob_buy=0.10,
        prob_sell=0.55,
        no_trade_prob=0.10,
        atr=1.5,
        regime_str="TRENDING",
        regime_conf=0.5,
        execution_id="EXEC-20260911-130000-123456",
    )
    assert proposal is not None
    repo.current_account_source = "LIVE"
    repo.log_signal(proposal)
    _flush(repo)

    con = sqlite3.connect(repo._db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT * FROM audit_signals WHERE request_id = ?", (proposal.request_id,)
        ).fetchone()
    finally:
        con.close()
    assert row is not None, "reversal decision row missing"
    payload = json.loads(row["payload"])
    assert payload["execution_id"] == "EXEC-20260911-130000-123456"


def test_degraded_no_trade_rows_join_by_execution_id(repo, policy) -> None:
    """The degraded-inference NO_TRADE (not a guard-telemetry code) lands in
    audit_signals WITH its EXEC id — the decision row stays joinable even for
    pre-evaluation rejections."""
    proposal = policy.evaluate_probabilities(
        probabilities=None,
        current_tick=_tick(datetime.now(UTC), seq=5),
        feature_vector=_feature_vector(),
    )
    assert proposal.reason_code == "PROBS_UNAVAILABLE_DEGRADED"
    assert proposal.execution_id
    repo.log_signal(proposal)
    _flush(repo)
    con = sqlite3.connect(repo._db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT payload FROM audit_signals WHERE request_id = ?",
            (proposal.request_id,),
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    payload = json.loads(row["payload"])
    assert payload["execution_id"] == proposal.execution_id


# ---------------------------------------------------------------------------
# 3. web-auth log message makes no false claim and logs no credential value
# ---------------------------------------------------------------------------


def test_web_auth_message_makes_no_false_log_claim() -> None:
    src = open("src/nexus_scalp/web/auth.py", encoding="utf-8").read()
    assert "Retrieve ONCE" not in src, (
        "the log line never carried the token (redactor scrubs secret keys) — "
        "a retrieve-from-log claim is log-as-authority and must stay gone"
    )
    assert "token=token" not in src, "the credential value must never ride a log call"


def test_web_auth_message_points_to_real_retrieval_paths() -> None:
    src = open("src/nexus_scalp/web/auth.py", encoding="utf-8").read()
    assert "web_auth_token" in src, "secret-store name documented in the message"
    assert "NSE_WEB_AUTH_TOKEN" in src, "env override documented in the message"
