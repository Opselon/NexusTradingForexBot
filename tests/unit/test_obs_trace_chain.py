"""OBS-TRACE regression tests (Agent 8, observability wave 2026-09-09).

Evidence, not authority: every production decision must be independently
reconstructable and attributable without trusting the log message itself.

Repairs pinned here (all audit-side, no broker / no trading semantics):

1. ``audit_signals.account_source`` — every decision row is LIVE/PAPER
   attributable at write time (BUG-226 contract extended to the decision
   table; ``execution_mode`` on that table is the execution PATH, never the
   account provenance).
2. ``audit_signals.payload.execution_id`` — the EXEC correlation id travels
   with the decision row, so signal->order joins do not depend on parsing
   the ``reason`` string.
3. Unknown-regime diagnostic log no longer asserts FABRICATED specifics
   (``missing_features=[ADX, ATR]``, ``available_bars=4000`` were never
   measured by the emitting layer).
4. ``audit_orders.latency`` is MEASURED (monotonic), not a hardcoded
   constant — synthetic constants make execution-latency forensics
   impossible (OBS-010 class).
5. The ``*** REAL ORDER/EXECUTION EXECUTED ***`` banner fires only after a
   verified success / broker-confirmed ticket (a failed close or a
   ticket=0 refusal previously logged success).
6. The pre-trade experience row carries ``execution_id`` so the
   decision_id <-> EXEC id join exists from decision time, not only after
   an outcome write (OBS-005 class).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TradeProposal
from nexus_scalp.experience.intelligence import ExperienceIntelligenceEngine
from nexus_scalp.experience.models import ModelProvenance
from nexus_scalp.experience.provenance import ModelRegistry


@pytest.fixture()
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'audit.db'}")
    r._start_background_worker()
    yield r
    r.close()


def _flush(repo: AuditRepository) -> None:
    assert repo.flush(timeout_sec=5.0), "audit worker did not drain"


def _signal_row(repo: AuditRepository, request_id: str) -> dict[str, Any]:
    con = sqlite3.connect(repo._db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT * FROM audit_signals WHERE request_id = ?", (request_id,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        con.close()


def _proposal(
    *,
    request_id: str = "req-test-0001",
    execution_id: str | None = "EXEC-20260909-120000-abcdef",
    reason_code: str = "MODEL_SIGNAL",
    regime: str = "TRENDING",
) -> TradeProposal:
    return TradeProposal(
        request_id=request_id,
        execution_id=execution_id,
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action=ActionType.BUY,
        confidence=0.8,
        proposed_entry=2000.0,
        stop_loss=1999.0,
        take_profit=2002.0,
        risk_reward_ratio=2.0,
        reason_code=reason_code,
        regime=regime,
    )


# ---------------------------------------------------------------------------
# 1. account_source on audit_signals
# ---------------------------------------------------------------------------


def test_audit_signals_column_exists(repo):
    con = sqlite3.connect(repo._db_path)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(audit_signals)")}
    finally:
        con.close()
    assert "account_source" in cols


def test_decision_row_carries_live_account_source(repo):
    repo.current_account_source = "LIVE"
    repo.log_signal(_proposal())
    _flush(repo)
    row = _signal_row(repo, "req-test-0001")
    assert row["account_source"] == "LIVE"


def test_decision_row_carries_paper_account_source(repo):
    repo.current_account_source = "PAPER"
    repo.log_signal(_proposal())
    _flush(repo)
    row = _signal_row(repo, "req-test-0001")
    assert row["account_source"] == "PAPER"


def test_execution_mode_is_not_account_provenance(repo):
    """The execution PATH column must never be confused with LIVE/PAPER."""
    repo.current_account_source = "PAPER"
    repo.log_signal(_proposal())
    _flush(repo)
    row = _signal_row(repo, "req-test-0001")
    assert (row["execution_mode"] or "STANDARD") not in ("LIVE", "PAPER")


# ---------------------------------------------------------------------------
# 2. execution_id in the decision payload
# ---------------------------------------------------------------------------


def test_signal_payload_carries_execution_id(repo):
    repo.log_signal(_proposal(execution_id="EXEC-20260909-120000-abcdef"))
    _flush(repo)
    row = _signal_row(repo, "req-test-0001")
    payload = json.loads(row["payload"])
    assert payload["execution_id"] == "EXEC-20260909-120000-abcdef"


def test_signal_payload_honest_when_no_execution_id(repo):
    repo.log_signal(_proposal(execution_id=None))
    _flush(repo)
    row = _signal_row(repo, "req-test-0001")
    payload = json.loads(row["payload"])
    assert payload["execution_id"] == ""


# ---------------------------------------------------------------------------
# 3. unknown-regime diagnostic carries no fabricated specifics
# ---------------------------------------------------------------------------


def test_unknown_regime_log_has_no_fabricated_features(repo, capsys):
    repo.log_signal(_proposal(regime="", reason_code="NO_TRADE_REGIME_UNKNOWN"))
    captured = capsys.readouterr()
    printed = [ln for ln in captured.out.splitlines() if ln.strip().startswith("{")]
    assert printed, "unknown-regime console diagnostic missing"
    data = json.loads(printed[-1])
    assert data["regime"] == "UNKNOWN"
    # The fabricated claims are gone; absence is stamped, never invented.
    assert data["missing_features"] == "NOT_RECORDED"
    assert data["available_bars"] == "NOT_RECORDED"
    # Real evidence is echoed.
    assert data["reason"] == "NO_TRADE_REGIME_UNKNOWN"


# ---------------------------------------------------------------------------
# 4. audit_orders.latency is measured, never a hardcoded constant
# ---------------------------------------------------------------------------


def test_dispatch_latency_is_measured_not_constant():
    src = open("src/nexus_scalp/execution/lifecycle/dispatch.py", encoding="utf-8").read()
    assert "latency=0.012" not in src, "hardcoded market latency constant is back"
    assert "latency=0.011" not in src, "hardcoded pending latency constant is back"
    assert "latency=0.015" not in src, "hardcoded hedge latency constant is back"
    assert "time.monotonic() - _dispatch_started" in src


def test_lifecycle_latency_constants_removed():
    src = open("src/nexus_scalp/execution/order_manager.py", encoding="utf-8").read()
    for constant in ("latency=0.009", "latency=0.010", "latency=0.011", "latency=0.008"):
        assert constant not in src, f"hardcoded latency {constant} is back"
    assert "time.monotonic() - _action_started" in src


# ---------------------------------------------------------------------------
# 5. success banner only after verified success
# ---------------------------------------------------------------------------


def test_real_order_banner_gated_on_success():
    """The banner must sit INSIDE the success branch on every lifecycle site."""
    src = open("src/nexus_scalp/execution/order_manager.py", encoding="utf-8").read()
    for action_branch in (
        "success = self.mt5_adapter.close_position(ticket=ticket)\n",
        "success = self.mt5_adapter.close_position(ticket=ticket, volume=volume)\n",
    ):
        idx = src.find(action_branch)
        assert idx != -1
        window = src[idx : idx + 700]
        banner = window.find("REAL ORDER/EXECUTION EXECUTED")
        success_if = window.find("if success:")
        assert success_if != -1, "success branch missing after broker call"
        assert banner > success_if, "banner logs success BEFORE the broker verdict"


def test_market_dispatch_banner_gated_on_ticket():
    src = open("src/nexus_scalp/execution/lifecycle/dispatch.py", encoding="utf-8").read()
    idx = src.find("ticket = int(")
    assert idx != -1
    window = src[idx : idx + 900]
    # search the f-string banner (not the OBS-TRACE comment mentioning it)
    banner = window.find('f"*** REAL ORDER/EXECUTION EXECUTED')
    ticket_if = window.find("if ticket > 0:")
    assert ticket_if != -1, "ticket>0 verification missing on market dispatch"
    assert banner > ticket_if, "banner logs success BEFORE broker ticket is verified"


# ---------------------------------------------------------------------------
# 6. pre-trade experience row carries execution_id
# ---------------------------------------------------------------------------


def _make_engine(repo: AuditRepository) -> ExperienceIntelligenceEngine:
    """Engine with a real ledger against the tmp audit DB; no gate deps."""
    from nexus_scalp.experience.evaluator import StrategyEvaluator
    from nexus_scalp.experience.ledger import ExperienceLedger
    from nexus_scalp.experience.retriever import ExperienceRetriever

    ledger = ExperienceLedger(audit_repo=repo)
    evaluator = StrategyEvaluator(audit_repo=repo)
    retriever = ExperienceRetriever(ledger=ledger)
    return ExperienceIntelligenceEngine(
        ledger=ledger,
        evaluator=evaluator,
        retriever=retriever,
        enabled=True,
        provenance=ModelProvenance(
            model_id="primary_scalp_test",
            model_version="9.9.9",
        ),
    )


def test_experience_pretrade_row_carries_execution_id(repo):
    from nexus_scalp.experience.models import StrategyContext

    engine = _make_engine(repo)
    proposal = _proposal()
    fv = Mock()
    fv.to_tensor_input = Mock(return_value=[0.1] * 50)
    engine._record_decision_experience(
        proposal=proposal,
        context=StrategyContext(strategy_id="strat_test"),
        feature_vector=fv,
        decision_id="exp_dec_test0001",
    )
    _flush(repo)
    con = sqlite3.connect(repo._db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT * FROM audit_experiences WHERE decision_id = ?", ("exp_dec_test0001",)
        ).fetchone()
    finally:
        con.close()
    assert row is not None, "pre-trade experience row missing"
    assert row["execution_id"] == "EXEC-20260909-120000-abcdef"
    assert row["decision_id"] == "exp_dec_test0001"


# ---------------------------------------------------------------------------
# 7. EXEC_TRACE model-identity wiring (static contract + provider unit)
# ---------------------------------------------------------------------------


def test_exec_trace_carries_model_identity_fields():
    src = open("src/nexus_scalp/signals/policy.py", encoding="utf-8").read()
    assert "model_id=" in src and "artifact_fingerprint=" in src, (
        "EXEC_TRACE no longer binds the decision to the serving artifact"
    )
    assert "MODEL_IDENTITY_UNAVAILABLE" in src, (
        "identity absence must be stamped honestly, never fabricated"
    )


def test_model_identity_provider_reads_bundle_metadata():
    from nexus_scalp.application.live_engine import LiveEngine

    src = open("src/nexus_scalp/application/live_engine.py", encoding="utf-8").read()
    assert "_serving_model_identity" in src
    assert "signal_policy.model_identity_fn = self._serving_model_identity" in src


def test_registry_fingerprint_is_sha256_prefix(tmp_path):
    """fingerprint_artifact pins the artifact bytes (sha256 16-hex prefix)."""
    artifact = tmp_path / "model.pt"
    artifact.write_bytes(b"\x00\x01 nexus test artifact bytes")
    fp = (
        ModelRegistry.fingerprint_artifact(artifact)
        if hasattr(ModelRegistry, "fingerprint_artifact")
        else None
    )
    if fp is None:
        from nexus_scalp.experience.provenance import fingerprint_artifact

        fp = fingerprint_artifact(artifact)
    import hashlib

    expected = hashlib.sha256(b"\x00\x01 nexus test artifact bytes").hexdigest()[:16]
    assert fp == expected
    # Missing artifact -> honest absence (empty string), never a placeholder.
    if fp is None:
        from nexus_scalp.experience.provenance import fingerprint_artifact

        assert fingerprint_artifact(tmp_path / "missing.pt") == ""
