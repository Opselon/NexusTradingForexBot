"""Issue #689 — STALE ENGINE: BLOCKED_BY_STALE with a healthy broker.

Symptom reported by the operator: ``nexus health`` and ``nexus doctor`` are
clean, the broker is connected, yet shadow-mode proposals are all reported as
``BLOCKED_BY_STALE``.

Root cause (two independent defects, both in the freshness chain):

1. **Clock-domain leak (the reported latch).** The decision stage stamp was
   copied from ``proposal.generated_at``, which the live path builds from the
   BROKER's ``tick.timestamp``. ``stage_freshness`` intentionally fails closed
   on a future stamp (a negative age is a timebase defect), so a broker feed
   running even ~1s ahead of this host latched ``overall`` to ``STALE`` for as
   long as the lead persisted, and every proposal was downgraded to
   ``NO_TRADE / BLOCKED_BY_STALE``. The decision stamp is a *local* SLO and
   must come from the local clock.

2. **Gate ordering.** ``live_freshness_gate`` ran BEFORE the instrumentation
   block that refreshes the stage stamps, so the gate judged the current
   tick's proposal against the previous cycle's stamps. The live path now
   passes the current tick's local decision timestamp to the gate.

These tests pin both behaviours on the pure service plus the engine seams, so
the fix cannot regress into a silent always-allowed gate: a genuinely frozen
chain must still be blocked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from nexus_scalp.application.live_freshness import (
    LiveFreshnessService,
    LiveFreshnessSnapshot,
)
from nexus_scalp.domain.enums import ActionType

# A broker feed running slightly ahead of this host (venue clock skew).
BROKER_LEAD = timedelta(seconds=1.5)
MAX_AGE_SEC = 5.0


def _snapshot(**overrides: Any) -> LiveFreshnessSnapshot:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "freshness_max_age_sec": MAX_AGE_SEC,
        "last_tick_timestamp": now,
        "last_feature_update": now,
        "last_inference_timestamp": now,
        "last_decision_timestamp": now,
        "tick_sequence": 7,
        "feature_sequence": 7,
        "inference_sequence": 7,
        "decision_sequence": 7,
        "monotonic_tick_ms": 1_700_000_000_000,
        "last_raw_market_hash": "raw",
        "last_feature_hash": "feat",
        "last_model_input_hash": "min",
        "last_model_output_hash": "mout",
        "market_updates_total": 7,
        "feature_builds_total": 7,
        "inference_runs_total": 7,
        "inference_failures_total": 0,
        "decision_updates_total": 7,
        "stale_state_detected_total": 0,
    }
    base.update(overrides)
    return LiveFreshnessSnapshot(**base)


class _FakeProposal:
    def __init__(self) -> None:
        self.action = ActionType.BUY
        self.confidence = 0.71
        self.reason_code = "MOMENTUM"
        self.model_copy_calls = 0

    def model_copy(self, update: dict[str, Any]) -> _FakeProposal:
        self.model_copy_calls += 1
        clone = _FakeProposal()
        for key, value in update.items():
            setattr(clone, key, value)
        return clone


def _engine_stub() -> Any:
    """Minimal engine exposing only the freshness surface under test."""
    from nexus_scalp.application.live_engine import LiveEngine

    eng = LiveEngine.__new__(LiveEngine)
    now = datetime.now(UTC)
    eng._freshness_max_age_sec = MAX_AGE_SEC
    eng._last_tick_timestamp = now
    eng.last_feature_update = now
    eng.last_inference_timestamp = now
    # Previous cycle's decision stamp, slightly in the past (normal cadence).
    eng.last_decision_timestamp = now - timedelta(milliseconds=200)
    eng._tick_sequence = 5
    eng._feature_sequence = 5
    eng._inference_sequence = 5
    eng._decision_sequence = 4
    eng._monotonic_tick_ms = 1_700_000_000_000
    eng._last_raw_market_hash = "raw"
    eng._last_feature_hash = "feat"
    eng._last_model_input_hash = "min"
    eng._last_model_output_hash = "mout"
    eng._market_updates_total = 5
    eng._feature_builds_total = 5
    eng._inference_runs_total = 5
    eng._inference_failures_total = 0
    eng._decision_updates_total = 4
    eng._stale_state_detected_total = 0
    return eng


# ---------------------------------------------------------------------------
# 1. The gate must not be fed a broker-domain timestamp.
# ---------------------------------------------------------------------------


def test_broker_ahead_tick_would_latch_stale_but_local_stamp_stays_fresh() -> None:
    """Documents the failure mode: a FUTURE stamp is STALE by policy.

    This is the trap that produced issue #689 — the policy is correct, the
    clock domain was not. The next test proves the engine no longer feeds the
    broker stamp into it.
    """
    svc = LiveFreshnessService()
    future = datetime.now(UTC) + BROKER_LEAD

    broker_domain = svc.compute_freshness(_snapshot(last_decision_timestamp=future))
    assert broker_domain["overall"] == "STALE"
    assert broker_domain["decision"]["state"] == "STALE"
    assert broker_domain["decision"]["age_ms"] < 0

    local_domain = svc.compute_freshness(_snapshot(last_decision_timestamp=datetime.now(UTC)))
    assert local_domain["overall"] == "FRESH"


def test_gate_accepts_current_tick_local_decision_timestamp() -> None:
    """The engine gate must judge the CURRENT tick's local decision clock."""
    eng = _engine_stub()
    proposal = _FakeProposal()

    out, blocked = eng.live_freshness_gate(proposal, decision_ts=datetime.now(UTC))

    assert blocked is False, "healthy chain + local timestamp must not be blocked"
    assert out is proposal
    assert proposal.model_copy_calls == 0
    assert eng._stale_state_detected_total == 0


def test_gate_still_blocks_a_genuinely_frozen_chain() -> None:
    """Anti-regression: the fix must not turn the gate into a no-op."""
    eng = _engine_stub()
    eng._last_tick_timestamp = datetime.now(UTC) - timedelta(seconds=900)
    eng.last_feature_update = datetime.now(UTC) - timedelta(seconds=900)
    eng.last_inference_timestamp = datetime.now(UTC) - timedelta(seconds=900)
    proposal = _FakeProposal()

    out, blocked = eng.live_freshness_gate(proposal, decision_ts=datetime.now(UTC))

    assert blocked is True
    assert out.action == ActionType.NO_TRADE
    assert out.reason_code == "BLOCKED_BY_STALE"
    assert out.confidence == 0.0


def test_gate_without_decision_ts_is_backwards_compatible() -> None:
    """Web/API callers that omit decision_ts keep the previous behaviour."""
    eng = _engine_stub()
    proposal = _FakeProposal()

    out, blocked = eng.live_freshness_gate(proposal)

    assert blocked is False
    assert out is proposal


def test_snapshot_uses_injected_decision_ts_over_stored_stamp() -> None:
    """The engine snapshot builder must honour the injected local stamp."""
    eng = _engine_stub()
    injected = datetime.now(UTC)

    snap = eng._build_freshness_snapshot(decision_ts=injected)
    assert snap.last_decision_timestamp == injected

    snap_default = eng._build_freshness_snapshot()
    assert snap_default.last_decision_timestamp == eng.last_decision_timestamp


# ---------------------------------------------------------------------------
# 2. The live path must not copy the broker's generated_at into the stamp.
# ---------------------------------------------------------------------------


def test_tick_pipeline_decision_stamp_uses_local_clock() -> None:
    """Source contract: the decision stage stamp is the local ``now_utc``.

    Guards against re-introducing ``getattr(proposal, "generated_at", ...)``,
    which is broker-domain on the warmup/blocked path.
    """
    import inspect
    import re

    from nexus_scalp.application.live import tick_pipeline

    src = inspect.getsource(tick_pipeline)
    assert "self.om.last_decision_timestamp = now_utc" in src
    assert 'self.om.last_decision_timestamp = getattr(proposal, "generated_at"' not in src
    # Allow ruff to wrap the call across lines.
    assert re.search(
        r"live_freshness_gate\(\s*proposal,\s*decision_ts=datetime\.now\(UTC\)\s*\)", src
    ), "the live path must pass the current tick's local decision timestamp to the gate"


@pytest.mark.parametrize("lead_seconds", [0.5, 1.5, 30.0, 3600.0])
def test_no_broker_lead_can_latch_the_gate(lead_seconds: float) -> None:
    """Any broker clock lead — not just a sub-second one — must be harmless."""
    eng = _engine_stub()
    broker_lead = datetime.now(UTC) + timedelta(seconds=lead_seconds)
    proposal = _FakeProposal()

    # The proposal itself still carries broker time (audit fidelity) ...
    proposal.generated_at = broker_lead
    # ... but the gate is judged on the local decision clock.
    out, blocked = eng.live_freshness_gate(proposal, decision_ts=datetime.now(UTC))

    assert blocked is False
    assert out is proposal
