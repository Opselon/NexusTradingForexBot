"""Regression suite for the /debug truth contract (DBG-TRUTH-001).

Every test here pins a defect observed on the LIVE debug console where the
payload reported a state the backend never actually evaluated:

  * REGIME gate rendered PASS/UNKNOWN when no regime state exists at all
    ("we know nothing" was shown as "no unsafe regime").
  * RISK + EXECUTION gates rendered UNAVAILABLE / PASS while the engine was
    PERSISTED_HALTED and refusing every trade (the operator saw "risk did
    not run" while trading was blocked).
  * NEWS gate rendered PASS/NO_VERDICT while news was ENABLED but no context
    existed (a missing context was shown as a healthy gate).
  * MSLIE reported available=false with NO reason field, even though the
    engine knows compute_count / last_error / construction state.
  * workers.last_success was set to workers.last_cycle_start: every cycle was
    reported as succeeded the instant it STARTED. No worker class publishes
    last_success. ``last_failure_at`` was read from an attribute no worker
    writes.
  * decision/blocked_by/reason_code/confidence_* were null with no marker, so
    the UI could not distinguish "no decision yet" from "field dropped".
  * runtime_risk_state_effective leaked a bound method repr into the payload
    (LiveEngine.runtime_risk_state is a METHOD, not an attribute).
  * the audit DB probe treated a PostgreSQL provider URI in _db_path as a
    filesystem path (PG-DBPATH-BOOT-001 inversion).

The tests use the real worker classes and a real MarketStructureEngine so
they fail if the snapshot fabricates a value the producer never wrote.

Contract pinned (per gate): actual / threshold / status / reason, and the
reason is a REAL cause string, never "".
"""

from __future__ import annotations

from datetime import UTC, datetime
from json import dumps as json_dumps
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import pytest

from nexus_scalp.mslie import MarketStructureEngine
from nexus_scalp.web.debug_snapshot import build_debug_snapshot


@pytest.fixture
def halted_engine() -> SimpleNamespace:
    """The LIVE shape: persisted safety halt, no proposal, no inference.

    Mirrors the observed runtime (PERSISTED_HALTED in runtime_risk_state,
    _running=False so no tick pipeline / proposal / regime ever ran). Used to
    prove the debug contract reports the halt instead of blank gates.
    """
    return _engine(halted=True)


@pytest.fixture
def running_engine() -> SimpleNamespace:
    """A healthy engine with no proposal yet (cold, post-warmup)."""
    return _engine(halted=False)


def _engine(*, halted: bool) -> SimpleNamespace:
    state = "HALTED" if halted else "RUNNING"
    halt_reason = (
        "PERSISTED_HALTED: Max drawdown exceeded: 23.45% > limit 5.00% (triggered_at=2026-09-24)"
        if halted
        else ""
    )
    eng = SimpleNamespace(
        config=SimpleNamespace(
            execution=SimpleNamespace(symbol="XAUUSD", mode=SimpleNamespace(value="LIVE")),
            algo=SimpleNamespace(min_risk_reward_ratio=1.8),
            risk=SimpleNamespace(
                risk_per_trade_pct=1.0,
                max_concurrent_positions=1,
                max_spread_points=60.0,
                max_account_drawdown_pct=5.0,
            ),
            model=SimpleNamespace(confidence_threshold=0.35),
            base_dir="artifacts",
        ),
        risk_engine=SimpleNamespace(
            _kill_switch_active=False,
            max_allowed_lots=2.0,
            min_risk_reward_ratio=1.8,
        ),
        order_manager=_fake_order_manager(),
        liquidity_governor=None,
        # No proposal / probs / regime / tick on the halted runtime.
        _last_proposal=None,
        _last_probs=None,
        _last_regime_state=None,
        _last_news_gate=None,
        _last_news_engine_context=None,
        _news_enabled=True,
        _inference_enabled=not halted,
        warmup_state="WARMING_UP" if halted else "READY",
        _running=not halted,
        _runtime_risk_state=state,
        _halt_reason=halt_reason,
        _halt_triggered_at="2026-09-24T18:40:43+00:00" if halted else "",
        _survival_mode_active=False,
        _account_freshness="MISSING",
        _consecutive_losses=0,
        _peak_equity=39601.37,
        _hot_path_circuit=SimpleNamespace(
            consecutive_error_count=0, max_consecutive_errors=10, error_window_sec=600.0
        ),
        audit=SimpleNamespace(
            _db_path="postgresql://localhost:5432/nexusdb" if halted else "artifacts/audit.db",
            _db_url="postgresql://localhost:5432/nexusdb"
            if halted
            else "sqlite:///artifacts/audit.db",
            _is_sqlite=not halted,
            audit_batch_failures=0,
            audit_dead_letter_rows=0,
            dead_letter_pruned_rows=0,
            audit_salvaged_rows=0,
            telemetry_dropped=0,
            financial_queue_backpressure=0,
            financial_events_overflowed=0,
            financial_events_failed=0,
            financial_overflow_recovered=0,
            financial_overflow_failed=0,
            overflow_pending_count=lambda: 0,
        ),
        news_engine=None,
        news_worker=None,
        telegram_notifier=None,
        accounting_worker=None,
        history_sync_worker=None,
        intelligence_worker=None,
        research_worker=None,
        training_worker=None,
        shadow_worker=None,
        _shadow70_worker=None,
        mslie_engine=MarketStructureEngine(symbol="XAUUSD", timeframe="M1"),
        _last_mslie_vector=None,
        _last_fv=None,
        _bundle=None,
        _bundle_lock=_FakeLock(),
        champion_manager=None,
        research_registry=None,
        adapter=_fake_adapter(),
        signal_policy=SimpleNamespace(_last_active_direction=None),
        spread_session_sketch=None,
        runtime_mode="LIVE",
        effective_feature_dim=70,
        effective_feature_schema_id="scalp_v3",
        FEATURE_DIM=50,
        FEATURE_SCHEMA_ID="scalp_v1",
        FEATURE_SCHEMA_HASH=None,
    )
    # LiveEngine.runtime_risk_state is a METHOD (effective accessor). The
    # snapshot must call it, not read the attribute.
    if halted:

        def _effective() -> str:
            return "HALTED"

    else:

        def _effective() -> str:
            return "RUNNING"

    eng.runtime_risk_state = _effective
    return eng


class _FakeLock:
    def __enter__(self) -> _FakeLock:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def _fake_order_manager() -> SimpleNamespace:
    return SimpleNamespace(
        count_total_exposure=lambda: (0, 0),
        _is_exposure_available=lambda: True,
        get_active_live_tickets=lambda: [],
        _last_reconcile_attempt=None,
        global_state="NORMAL",
        _consecutive_failures=0,
        _processed_orders={},
    )


def _fake_adapter() -> SimpleNamespace:
    snap = SimpleNamespace(
        available=True,
        balance=29842.89,
        equity=29842.89,
        margin_free=29842.89,
        margin=0.0,
        margin_level=0.0,
        floating_pnl=0.0,
    )
    return SimpleNamespace(
        is_connected=lambda: True,
        get_account_snapshot=lambda: snap,
        get_account_info=lambda: None,
        get_all_positions=lambda symbol=None: [],
        get_positions=lambda symbol=None: [],
        get_pending_orders=lambda symbol=None: [],
        connection_state=lambda: SimpleNamespace(to_dict=lambda: {"connected": True}),
    )


def _userinfo(dsn: str | None) -> str:
    if not dsn:
        return ""
    return urlsplit(dsn).username or ""


def _gates(snap: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {g["name"]: g for g in snap["policy"]["gates"]}


# ---------------------------------------------------------------------------
# REGIME: unknown must never read as a pass
# ---------------------------------------------------------------------------


class TestRegimeGateTruth:
    def test_no_regime_state_is_not_evaluated_not_pass(
        self, halted_engine: SimpleNamespace
    ) -> None:
        snap = build_debug_snapshot(halted_engine, None)
        g = _gates(snap)["REGIME"]
        assert g["status"] == "NOT_EVALUATED"
        assert g["actual"] is None
        assert g["reason"], "REGIME gate must carry a real reason, not a blank"
        assert "HALTED" in g["reason"]

    def test_present_regime_state_still_passes_when_safe(
        self, running_engine: SimpleNamespace
    ) -> None:
        running_engine._last_regime_state = SimpleNamespace(
            regime_type=SimpleNamespace(value="TRENDING"),
            decision_diagnostics=lambda: {"fired": []},
        )
        snap = build_debug_snapshot(running_engine, None)
        g = _gates(snap)["REGIME"]
        assert g["status"] == "PASS"
        assert g["actual"] == "TRENDING"


# ---------------------------------------------------------------------------
# RISK / EXECUTION: a persisted halt must be visible in the gate trace
# ---------------------------------------------------------------------------


class TestRiskGateHaltTruth:
    def test_halt_makes_risk_gate_blocked_with_real_reason(
        self, halted_engine: SimpleNamespace
    ) -> None:
        snap = build_debug_snapshot(halted_engine, None)
        g = _gates(snap)["RISK"]
        assert g["status"] == "BLOCKED"
        assert g["actual"] is False
        assert g["reason"] == halted_engine._halt_reason
        assert "Max drawdown" in g["reason"]

    def test_halt_makes_execution_gate_blocked(self, halted_engine: SimpleNamespace) -> None:
        snap = build_debug_snapshot(halted_engine, None)
        g = _gates(snap)["EXECUTION"]
        assert g["status"] == "BLOCKED"
        assert g["reason"] == halted_engine._halt_reason

    def test_running_engine_without_proposal_reports_not_evaluated(
        self, running_engine: SimpleNamespace
    ) -> None:
        snap = build_debug_snapshot(running_engine, None)
        for name in ("RISK", "EXECUTION"):
            assert _gates(snap)[name]["status"] == "NOT_EVALUATED", name
            assert _gates(snap)[name]["reason"], name

    def test_effective_risk_state_is_called_not_leaked(
        self, halted_engine: SimpleNamespace
    ) -> None:
        """runtime_risk_state is a method on LiveEngine; the payload must
        carry its RETURN value, never a bound-method repr."""
        snap = build_debug_snapshot(halted_engine, None)
        assert snap["risk"]["runtime_risk_state_effective"] == "HALTED"
        assert "bound method" not in snap["risk"]["runtime_risk_state_effective"]
        assert "function" not in snap["risk"]["runtime_risk_state_effective"]

    def test_degraded_state_is_reported_neither_pass_nor_blank(self) -> None:
        eng = _engine(halted=False)
        eng._runtime_risk_state = "RUNNING"
        eng._loss_freeze_active = True

        def _eff() -> str:
            return "DEGRADED"

        eng.runtime_risk_state = _eff
        snap = build_debug_snapshot(eng, None)
        g = _gates(snap)["RISK"]
        assert g["status"] == "NOT_EVALUATED"
        assert "DEGRADED" in g["reason"]


# ---------------------------------------------------------------------------
# NEWS: enabled + no context is not a pass
# ---------------------------------------------------------------------------


class TestNewsGateTruth:
    def test_enabled_with_no_context_is_not_applied(self, halted_engine: SimpleNamespace) -> None:
        snap = build_debug_snapshot(halted_engine, None)
        g = _gates(snap)["NEWS"]
        assert g["status"] == "NOT_APPLIED"
        assert g["actual"] == "UNAVAILABLE"
        assert "context_state=" in g["reason"]

    def test_disabled_news_reports_disabled(self, halted_engine: SimpleNamespace) -> None:
        halted_engine._news_enabled = False
        snap = build_debug_snapshot(halted_engine, None)
        g = _gates(snap)["NEWS"]
        assert g["status"] == "NOT_APPLIED"
        assert g["actual"] == "DISABLED"
        assert g["reason"] == "news gate disabled"

    def test_available_context_without_proposal_reports_no_verdict(
        self, running_engine: SimpleNamespace
    ) -> None:
        running_engine.news_engine = SimpleNamespace(
            current_context=lambda: SimpleNamespace(
                available=True, state=SimpleNamespace(value="NORMAL")
            )
        )
        snap = build_debug_snapshot(running_engine, None)
        g = _gates(snap)["NEWS"]
        assert g["status"] == "NOT_APPLIED"
        assert g["actual"] == "NO_VERDICT"
        assert "NORMAL" in g["reason"]

    def test_real_verdict_is_rendered_verbatim(self, running_engine: SimpleNamespace) -> None:
        running_engine._last_news_gate = SimpleNamespace(
            decision="CAUTION", blocked=True, reason="high impact event active"
        )
        snap = build_debug_snapshot(running_engine, None)
        g = _gates(snap)["NEWS"]
        assert g["status"] == "BLOCKED"
        assert g["actual"] == "CAUTION"
        assert g["reason"] == "high impact event active"


# ---------------------------------------------------------------------------
# MSLIE: a reason must always accompany available=false
# ---------------------------------------------------------------------------


class TestMslieReasonTruth:
    def test_never_driven_engine_reports_not_initialized(
        self, halted_engine: SimpleNamespace
    ) -> None:
        snap = build_debug_snapshot(halted_engine, None)
        m = snap["mslie"]
        assert m["available"] is False
        assert m["reason"] == "MSLIE_NOT_INITIALIZED"
        assert m["state"] == "NOT_READY"
        es = m["engine_status"]
        assert es["compute_count"] == 0

    def test_missing_engine_reports_not_attached(self, running_engine: SimpleNamespace) -> None:
        running_engine.mslie_engine = None
        snap = build_debug_snapshot(running_engine, None)
        assert snap["mslie"]["reason"] == "MSLIE_ENGINE_NOT_ATTACHED"

    def test_driven_engine_reports_ready(self, running_engine: SimpleNamespace) -> None:
        bars = [
            SimpleNamespace(
                timestamp=datetime(2026, 9, 28, 12, 0, tzinfo=UTC),
                open=2400.0,
                high=2403.0,
                low=2398.0,
                close=2402.0,
                tick_volume=20,
            )
            for _ in range(40)
        ]
        running_engine.mslie_engine.analyze_market(bars)
        snap = build_debug_snapshot(running_engine, None)
        m = snap["mslie"]
        assert m["available"] is True
        assert m["state"] == "READY"
        assert m["engine_status"]["compute_count"] >= 1

    def test_error_after_compute_reports_degraded(self, running_engine: SimpleNamespace) -> None:
        running_engine.mslie_engine._last_error = "boom"
        running_engine.mslie_engine._last_error_at = 1.0
        running_engine.mslie_engine._error_count = 1
        running_engine.mslie_engine._compute_count = 3
        snap = build_debug_snapshot(running_engine, None)
        assert snap["mslie"]["reason"] == "MSLIE_DEGRADED"


# ---------------------------------------------------------------------------
# WORKERS: last_success must never be fabricated from last_cycle_start
# ---------------------------------------------------------------------------


class TestWorkerTruth:
    def test_no_worker_last_success_is_fabricated(self, running_engine: SimpleNamespace) -> None:
        """No worker class in the repo publishes last_success. The snapshot
        must leave it null (or derive it from a real success stamp), never
        copy last_cycle_start into it."""
        from nexus_scalp.accounting.worker import AccountingWorker

        running_engine.accounting_worker = AccountingWorker(
            core=SimpleNamespace(), interval_sec=60.0
        )
        snap = build_debug_snapshot(running_engine, None)
        w = snap["workers"]["workers"]["accounting"]
        assert w["state"] == "IDLE"
        assert w["last_start"] is None
        assert w["last_success"] is None, "last_success must not be fabricated"
        assert w["last_failure"] is None

    def test_cycle_started_but_not_finished_is_not_a_success(
        self, running_engine: SimpleNamespace
    ) -> None:
        started = datetime.now(UTC)
        running_engine.accounting_worker = SimpleNamespace(
            running=False,
            cycle_count=0,
            last_cycle_start=started,
            last_cycle_duration=0.0,
            last_error="connection refused",
        )
        snap = build_debug_snapshot(running_engine, None)
        w = snap["workers"]["workers"]["accounting"]
        assert w["last_success"] is None
        assert w["last_start"] is not None
        assert w["last_error"] == "connection refused"

    def test_missing_worker_reports_reason(self, running_engine: SimpleNamespace) -> None:
        snap = build_debug_snapshot(running_engine, None)
        for name in ("accounting", "shadow", "shadow70", "telegram"):
            w = snap["workers"]["workers"][name]
            assert w.get("state") == "UNAVAILABLE"
            assert w.get("reason") == "WORKER_NOT_ATTACHED", name

    def test_shadow70_status_shape_is_preserved(self, running_engine: SimpleNamespace) -> None:
        running_engine._shadow70_worker = SimpleNamespace(
            status=lambda: {
                "running": True,
                "persisted": 5,
                "queue_size": 2,
                "last_flush_at": "2026-09-28T00:00:00+00:00",
            }
        )
        snap = build_debug_snapshot(running_engine, None)
        w = snap["workers"]["workers"]["shadow70"]
        assert w["state"] == "RUNNING"
        assert w["cycle"] == 5
        assert w["queue"] == 2
        assert w["last_success"] == "2026-09-28T00:00:00+00:00"


# ---------------------------------------------------------------------------
# DECISION: empty fields must carry an explicit marker
# ---------------------------------------------------------------------------


class TestDecisionMarkerTruth:
    def test_no_proposal_reports_no_decision_yet(self, halted_engine: SimpleNamespace) -> None:
        snap = build_debug_snapshot(halted_engine, None)
        p = snap["policy"]
        assert p["decision"] == "NO_DECISION_YET"
        assert p["decision_state"].startswith("NO_PROPOSAL_")
        assert "HALTED" in p["decision_state"]

    def test_every_unevaluated_gate_has_a_nonblank_reason(
        self, halted_engine: SimpleNamespace
    ) -> None:
        snap = build_debug_snapshot(halted_engine, None)
        for g in snap["policy"]["gates"]:
            if g["status"] in ("NOT_EVALUATED", "NOT_APPLIED", "UNAVAILABLE"):
                assert g["reason"], f"gate {g['name']} has no reason"
                assert g["reason"].startswith("NO_") or "context_state=" in g["reason"]

    def test_proposal_present_reports_real_decision(self, running_engine: SimpleNamespace) -> None:
        running_engine._last_proposal = SimpleNamespace(
            action=SimpleNamespace(value="NO_TRADE"),
            confidence=0.20,
            risk_reward_ratio=1.0,
            decision_stage="CONFIDENCE_GATE",
            blocked_by="CONFIDENCE_GATE",
            reason_code="INSUFFICIENT_CONFIDENCE",
            confidence_before_filters=0.31,
            confidence_after_filters=0.20,
            request_id="REQ-1",
            risk_allowed=False,
            rejection_reason="confidence below threshold",
            generated_at=datetime(2026, 9, 28, 12, 0, tzinfo=UTC),
            guardian_status=None,
        )
        running_engine._last_regime_state = SimpleNamespace(
            regime_type=SimpleNamespace(value="RANGING_MEAN_REVERSION"),
            decision_diagnostics=lambda: {},
        )
        snap = build_debug_snapshot(running_engine, None)
        p = snap["policy"]
        assert p["decision"] == "NO_TRADE"
        assert p["decision_stage"] == "CONFIDENCE_GATE"
        assert p["blocked_by"] == "CONFIDENCE_GATE"
        assert p["confidence_before_filters"] == 0.31
        assert p["reason_code"] == "INSUFFICIENT_CONFIDENCE"
        # A rejected proposal still gets real PASS/FAIL verdicts, not blanks.
        assert _gates(snap)["CONFIDENCE"]["status"] == "FAIL"
        assert _gates(snap)["R:R"]["status"] == "FAIL"


# ---------------------------------------------------------------------------
# DATABASE: a provider URI in _db_path must not be treated as a file path
# ---------------------------------------------------------------------------


class TestAuditDbProviderTruth:
    def test_postgres_uri_in_db_path_is_not_probed_as_file(
        self, halted_engine: SimpleNamespace
    ) -> None:
        snap = build_debug_snapshot(halted_engine, None)
        a = snap["database"]["databases"]["audit"]
        assert a["provider"] == "postgresql"
        assert a["health"] == "UNVERIFIED"
        assert a["reason"] == "POSTGRES_DOMAIN_NOT_FILE_BASED"
        # The connection TARGET must survive masking (host/port/db), while
        # credentials are stripped: an UNVERIFIED row that names no target
        # is useless to an operator.
        assert a["dsn"] == "postgresql://localhost:5432/nexusdb"
        assert ":" not in _userinfo(a["dsn"]), "credentials must not leak"

    def test_sqlite_path_is_probed_as_file(self, running_engine: SimpleNamespace) -> None:
        snap = build_debug_snapshot(running_engine, None)
        a = snap["database"]["databases"]["audit"]
        assert a.get("provider") != "postgresql"
        assert a["health"] in ("READY", "MISSING"), "sqlite probe must run, not be skipped"
        # A file-based provider exposes a real filesystem probe, never a DSN.
        assert "dsn" not in a or a["dsn"] is None

    def test_credentials_are_redacted_from_the_dsn(self, running_engine: SimpleNamespace) -> None:
        running_engine.audit._db_path = "postgresql://nexus:S3cr3t@db.internal:5432/audit"
        running_engine.audit._db_url = "postgresql://nexus:S3cr3t@db.internal:5432/audit"
        snap = build_debug_snapshot(running_engine, None)
        a = snap["database"]["databases"]["audit"]
        assert a["dsn"] == "postgresql://db.internal:5432/audit"
        assert "S3cr3t" not in a["dsn"]
        assert "S3cr3t" not in json_dumps(snap, default=str)

    def test_no_audit_path_reports_unavailable(self, running_engine: SimpleNamespace) -> None:
        running_engine.audit._db_path = ""
        running_engine.audit._db_url = "sqlite:///:memory:"
        snap = build_debug_snapshot(running_engine, None)
        a = snap["database"]["databases"]["audit"]
        assert a["health"] in ("UNAVAILABLE", "MISSING")


# ---------------------------------------------------------------------------
# No gate may ship an empty reason with a non-PASS status
# ---------------------------------------------------------------------------


class TestGateContractCompleteness:
    @pytest.mark.parametrize("engine_name", ["halted_engine", "running_engine"])
    def test_every_gate_has_four_fields_and_reason(
        self, request: pytest.FixtureRequest, engine_name: str
    ) -> None:
        eng = request.getfixturevalue(engine_name)
        snap = build_debug_snapshot(eng, None)
        for g in snap["policy"]["gates"]:
            for field in ("name", "actual", "threshold", "status", "reason"):
                assert field in g, f"gate {g.get('name')} missing {field}"
            assert g["status"] in {
                "PASS",
                "FAIL",
                "BLOCKED",
                "UNAVAILABLE",
                "NOT_EVALUATED",
                "NOT_APPLIED",
            }, g
            # PASS on a PASS-threshold gate may legitimately have no extra
            # reason; every other status must explain itself.
            if g["status"] != "PASS":
                assert g["reason"], f"gate {g['name']} status={g['status']} with no reason"

    def test_snapshot_is_json_serializable(self, halted_engine: SimpleNamespace) -> None:
        import json

        snap = build_debug_snapshot(halted_engine, None)
        # Must round-trip with default=str (the route's serialize_enums path).
        text = json.dumps(snap, default=str)
        again = json.loads(text)
        assert again["policy"]["decision"] == "NO_DECISION_YET"
        assert "bound method" not in text
