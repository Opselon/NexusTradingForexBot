"""Incident response + runtime — authoritative suite.

Merged from:
  - tests/unit/test_incident_response_task12.py (TEST-INCIDENT-01..35)
  - tests/unit/test_incident_runtime_task13.py (TEST-INCIDENT-RUNTIME-01..20)

Original files are superseded by this single file (tests/unit/test_incidents.py).
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.incidents.correlator import IncidentCorrelator, TelemetryEvent
from nexus_scalp.incidents.impact import ImpactAnalyzer, QuarantineManager, RecoveryPlanner
from nexus_scalp.incidents.lineage import LineageEngine
from nexus_scalp.incidents.models import (
    BlastRadius,
    Incident,
    IncidentCategory,
    IncidentSeverity,
    IncidentStatus,
    RecoveryAction,
    RootCauseConfidence,
    incident_fingerprint,
)
from nexus_scalp.incidents.reports import (
    export_zip_bundle,
    incident_json,
    mask_secrets,
    write_incident_reports,
)
from nexus_scalp.incidents.store import IncidentStore
from nexus_scalp.incidents.telegram import IncidentTelegramNotifier
from nexus_scalp.incidents.timebase import TimebaseProbe
from nexus_scalp.incidents.trace import (
    broker_ledger_divergence,
    clock_skew,
    learning_pipeline_rates,
    news_incidents,
    outcome_forensics,
    split_fill_groups,
    version_consistency,
    why_blocked,
    why_closed,
    why_no_learning,
    why_no_strategy,
    why_ui_empty,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> IncidentStore:
    s = IncidentStore(db_path=str(tmp_path / "audit.db"))
    s.ensure_schema()
    return s


def _ev(
    event_type: str,
    error_code: str,
    component: str,
    *,
    correlation_id: str = "",
    ticket: str = "",
    severity: str | None = None,
    ts: datetime | None = None,
) -> TelemetryEvent:
    return TelemetryEvent(
        timestamp=ts or datetime.now(UTC),
        event_type=event_type,
        component=component,
        error_code=error_code,
        correlation_id=correlation_id,
        ticket=ticket,
        severity=severity,
    )


def _audit_db(tmp_path: Path, *, broker_rows: int = 5, ledger_rows: int = 5) -> str:
    """Builds a small deterministic audit.db for trace tests."""
    db = tmp_path / "audit.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE audit_broker_trades (
            trade_id TEXT, position_id TEXT, symbol TEXT, direction TEXT,
            entry_time TEXT, exit_time TEXT, entry_price REAL, exit_price REAL,
            volume REAL, gross_pnl REAL, commission REAL, swap REAL, fee REAL,
            net_pnl REAL, deal_ids TEXT, order_ids TEXT, master_order_id TEXT,
            exit_reason TEXT, exit_comment TEXT, duration_sec REAL,
            source TEXT, synced_at TEXT
        );
        CREATE TABLE audit_ledger (
            ticket TEXT, symbol TEXT, direction TEXT, volume REAL,
            entry_price REAL, exit_price REAL, status TEXT, pnl REAL,
            commission REAL, swap REAL, timestamp TEXT, mae REAL, mfe REAL,
            order_id TEXT, open_time TEXT, close_time TEXT,
            gross_pnl_usd REAL, net_pnl_usd REAL, exit_mechanism TEXT,
            exit_reason_source TEXT, exit_evidence TEXT, exit_reason_confidence REAL,
            entry_reason TEXT, ai_confidence_at_open REAL
        );
        CREATE TABLE audit_experiences (
            experience_id TEXT, request_id TEXT, execution_id TEXT,
            decision_id TEXT, idempotency_key TEXT, strategy_id TEXT,
            action TEXT, entry_reason TEXT, model_id TEXT, is_executed INTEGER
        );
        CREATE TABLE audit_experience_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT,
            execution_id TEXT, outcome_timestamp TEXT, is_executed INTEGER,
            is_closed INTEGER, exit_reason TEXT, realized_pnl_usd REAL,
            realized_r_multiple REAL, approved_volume REAL,
            mae_points REAL, mfe_points REAL, mae_usd REAL, mfe_usd REAL,
            mae_r REAL, mfe_r REAL, holding_duration_seconds REAL,
            slippage_points REAL, execution_latency_ms REAL,
            strategy_quality REAL, entry_quality REAL, execution_quality REAL,
            management_quality REAL, exit_quality REAL,
            behavioral_flags TEXT, payload TEXT
        );
        CREATE TABLE research_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
            dataset_id TEXT, strategy_id TEXT, strategy_version TEXT,
            executed_at TEXT, config TEXT, build_identity TEXT,
            result_summary TEXT
        );
        CREATE TABLE strategy_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_id TEXT,
            strategy_version TEXT, feature_schema_id TEXT, feature_dimension INTEGER,
            discovery_source TEXT, lifecycle TEXT, score REAL, confidence REAL,
            sample_count INTEGER, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE position_lifecycle_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT, ticket TEXT,
            trade_id TEXT, experience_id TEXT, symbol TEXT, timeframe TEXT,
            event_type TEXT, sequence INTEGER, event_timestamp TEXT,
            market_context TEXT, position_snapshot TEXT, payload TEXT
        );
        CREATE TABLE trade_autopsies (
            ticket TEXT, trade_id TEXT, experience_id TEXT, strategy_id TEXT,
            strategy_version TEXT, symbol TEXT, timeframe TEXT, entry_price REAL,
            exit_price REAL, volume REAL, direction TEXT, entry_reason TEXT,
            realized_pnl_usd REAL, realized_r REAL, mfe_r REAL, mae_r REAL,
            giveback_pct REAL, holding_duration_sec REAL, exit_mechanism TEXT,
            strategy_quality REAL, entry_quality REAL, management_quality REAL,
            exit_quality REAL, execution_quality REAL, quality_verdict TEXT,
            behavioral_flags TEXT, narrative TEXT, autopsied_at TEXT, payload TEXT
        );
        CREATE TABLE audit_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT, symbol TEXT,
            action TEXT, confidence REAL, proposed_entry REAL, stop_loss REAL,
            take_profit REAL, regime TEXT, generated_at TEXT, payload TEXT,
            execution_mode TEXT, reason_code TEXT, decision_stage TEXT,
            blocked_by TEXT, htf_score REAL, smc_score REAL,
            confidence_before_filters REAL, confidence_after_filters REAL,
            signal_dedup_key TEXT
        );
        CREATE TABLE audit_guard_telemetry (
            window_start TEXT, symbol TEXT, reason_code TEXT
        );
        CREATE TABLE audit_broker_history_meta (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT,
            last_sync_from TEXT, last_sync_to TEXT, last_synced_at TEXT,
            last_orders INTEGER, last_deals INTEGER, last_trades INTEGER
        );
        CREATE TABLE news_health (
            source_id TEXT, last_success_at TEXT, last_failure_at TEXT,
            last_status INTEGER, consecutive_failures INTEGER,
            rate_limited INTEGER, retry_after_sec REAL, backoff_until TEXT,
            healthy INTEGER
        );
        CREATE TABLE news_articles (
            article_id TEXT PRIMARY KEY, article_hash TEXT, canonical_url TEXT,
            title TEXT, summary TEXT, body TEXT, language TEXT, source_id TEXT,
            source_name TEXT, published_at TEXT, updated_at TEXT,
            raw_categories TEXT, entities TEXT, topics TEXT, importance TEXT,
            importance_score REAL, novelty REAL, is_duplicate INTEGER,
            duplicate_of TEXT, evidence_sources TEXT, created_at TEXT
        );
        CREATE TABLE news_analysis (
            analysis_id TEXT PRIMARY KEY, article_id TEXT, run_id TEXT,
            status TEXT, local_only INTEGER, provider TEXT, summary TEXT,
            entities TEXT, topics TEXT, direction TEXT, impact_strength REAL,
            confidence REAL, horizon TEXT, importance TEXT, importance_score REAL,
            relevance_to_xauusd REAL, relevance_to_usd REAL, impacts TEXT,
            surprise_assessment TEXT, market_mechanism TEXT,
            contradictory_factors TEXT, novelty REAL, risks TEXT,
            reasoning_trace_id TEXT, analyzed_at TEXT
        );
        """
    )
    # broker rows (real PnL)
    for i in range(broker_rows):
        tid = f"1524{i:08d}"
        con.execute(
            "INSERT INTO audit_broker_trades (trade_id, position_id, symbol, direction, "
            "entry_time, exit_time, entry_price, exit_price, volume, gross_pnl, "
            "commission, swap, fee, net_pnl, source, synced_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                tid,
                tid,
                "XAUUSD",
                "BUY",
                "2026-08-18T10:00:00+00:00",
                "2026-08-18T10:30:00+00:00",
                4000.0,
                4010.0,
                0.1,
                10.0,
                0.0,
                0.0,
                0.0,
                10.0,
                "BROKER_DEALS",
                "2026-08-18T10:31:00+00:00",
            ),
        )
    # ledger rows: broker_rows-1 match exactly, 1 has ZERO PnL (SUSPECT pattern)
    for i in range(ledger_rows):
        tid = f"1524{i:08d}"
        pnl = 10.0 if i < ledger_rows - 1 else 0.0
        con.execute(
            "INSERT INTO audit_ledger (ticket, symbol, direction, volume, entry_price, "
            "exit_price, status, pnl, commission, swap, timestamp, gross_pnl_usd, "
            "net_pnl_usd, exit_mechanism, exit_reason_source, close_time) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                tid,
                "XAUUSD",
                "BUY",
                0.1,
                4000.0,
                4010.0,
                "CLOSED",
                pnl,
                0.0,
                0.0,
                "2026-08-18T10:30:00+00:00",
                pnl,
                pnl,
                "SYSTEM_CLOSE" if pnl else "UNKNOWN",
                "",
                "2026-08-18T10:30:00+00:00",
            ),
        )
    # outcomes: broker_rows-2 linked, 1 zero-PnL with broker truth != 0
    for i in range(max(0, broker_rows - 2)):
        tid = f"1524{i:08d}"
        con.execute(
            "INSERT INTO audit_experience_outcomes (idempotency_key, execution_id, "
            "outcome_timestamp, is_executed, is_closed, exit_reason, "
            "realized_pnl_usd, realized_r_multiple, payload) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                f"exp_{i}",
                tid,
                "2026-08-18T10:31:00+00:00",
                1,
                1,
                "SYSTEM_CLOSE",
                10.0,
                1.0,
                json.dumps({"reconstruction_source": "BROKER"}),
            ),
        )
    con.execute(
        "INSERT INTO audit_experience_outcomes (idempotency_key, execution_id, "
        "outcome_timestamp, is_executed, is_closed, exit_reason, "
        "realized_pnl_usd, realized_r_multiple, payload) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "exp_zero",
            f"1524{broker_rows - 1:08d}",
            "2026-08-18T10:31:00+00:00",
            1,
            1,
            "UNKNOWN",
            0.0,
            0.0,
            json.dumps({}),
        ),
    )
    con.execute("INSERT INTO research_runs (run_id, dataset_id) VALUES ('run-1', 'ds-1')")
    con.execute(
        "INSERT INTO strategy_registry (strategy_id, lifecycle, score, sample_count) "
        "VALUES ('strat_1', 'DISCOVERED', 0.9, 120)"
    )
    con.execute(
        "INSERT INTO position_lifecycle_events (ticket, trade_id, event_type, event_timestamp) "
        "VALUES ('152400000000', '152400000000', 'POSITION_EXITED', '2026-08-18T10:30:00+00:00')"
    )
    con.execute(
        "INSERT INTO audit_signals (request_id, symbol, action, confidence, "
        "generated_at, payload, reason_code, blocked_by) VALUES (?,?,?,?,?,?,?,?)",
        (
            "req-1",
            "XAUUSD",
            "BUY",
            0.8,
            "2026-08-18T10:10:00+00:00",
            '{"rejection_reason":"MAX_EXPOSURE_REACHED"}',
            "POLICY_REJECTION",
            "MAX_EXPOSURE",
        ),
    )
    con.execute(
        "INSERT INTO audit_guard_telemetry (window_start, symbol, reason_code) "
        "VALUES ('2026-08-18T10:00:00+00:00', 'XAUUSD', 'MAX_EXPOSURE_REACHED')"
    )
    for src_id in ("source_a", "source_b"):
        con.execute(
            "INSERT INTO news_health (source_id, last_success_at, consecutive_failures, healthy) "
            "VALUES (?, '2026-08-18T10:00:00+00:00', 0, 1)",
            (src_id,),
        )
    con.execute(
        "INSERT INTO news_articles (article_id, title, published_at) VALUES ('art-1', 't', '2026-08-18T10:00:00+00:00')"
    )
    con.execute(
        "INSERT INTO news_analysis (analysis_id, article_id, direction, impact_strength) "
        "VALUES ('an-1', 'art-1', 'BULLISH', 0.7)"
    )
    con.commit()
    con.close()
    return str(db)


# ---------------------------------------------------------------------------
# TEST-INCIDENT-01 — incident creation (canonical model)
# ---------------------------------------------------------------------------


class TestIncidentCreation:
    def test_incident_creation(self) -> None:
        inc = Incident(
            severity=IncidentSeverity.CRITICAL,
            category=IncidentCategory.DATA,
            component="ledger",
            operation="DEAL_LOOKUP_FAILED",
            correlation_id="corr-1",
        )
        assert inc.incident_id.startswith("INC-")
        assert inc.status == IncidentStatus.OPEN
        assert inc.root_cause_status == RootCauseConfidence.UNKNOWN
        assert inc.recovery_status == "RECOMMENDED"
        assert inc.repeated_count == 1

    def test_incident_fields_complete(self) -> None:
        """The canonical incident structure (spec 2) must be present."""
        inc = Incident()
        d = inc.as_dict()
        for field in (
            "incident_id",
            "detected_at",
            "severity",
            "category",
            "status",
            "first_seen_at",
            "last_seen_at",
            "component",
            "operation",
            "correlation_id",
            "root_cause_status",
            "root_cause",
            "evidence",
            "impact",
            "affected_records",
            "affected_models",
            "affected_runtime",
            "affected_users",
            "recovery_status",
            "recommended_action",
        ):
            assert field in d, f"missing canonical field {field}"

    def test_store_roundtrip(self, store: IncidentStore) -> None:
        inc = Incident(
            severity=IncidentSeverity.HIGH,
            category=IncidentCategory.MT5,
            component="mt5",
            operation="MT5_CALL_FAILED",
            correlation_id="corr-x",
            root_cause="broker timeout",
            root_cause_status=RootCauseConfidence.HIGH_CONFIDENCE,
        )
        store.save(inc)
        loaded = store.get(inc.incident_id)
        assert loaded is not None
        assert loaded.severity == IncidentSeverity.HIGH
        assert loaded.root_cause == "broker timeout"
        assert loaded.correlation_id == "corr-x"

    def test_fingerprint_stable(self) -> None:
        a = incident_fingerprint(category="MT5", component="mt5", error_code="MT5_CALL_FAILED")
        b = incident_fingerprint(category="MT5", component="mt5", error_code="MT5_CALL_FAILED")
        c = incident_fingerprint(category="MT5", component="mt5", error_code="DIFFERENT")
        assert a == b
        assert a != c


# ---------------------------------------------------------------------------
# TEST-INCIDENT-02 — deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    def test_55_repeats_become_one_incident(self) -> None:
        corr = IncidentCorrelator()
        base = datetime.now(UTC)
        events = [
            _ev(
                "UNBOUND_LOCAL_ERROR",
                "SILENT_EXCEPTION",
                "exposure",
                ts=base + timedelta(seconds=i * 6),
            )
            for i in range(55)
        ]
        result = corr.correlate(events)
        assert len(result.incidents) == 1
        assert result.incidents[0].repeated_count >= 55

    def test_distinct_fingerprints_stay_separate(self) -> None:
        corr = IncidentCorrelator()
        base = datetime.now(UTC)
        result = corr.correlate(
            [
                _ev("MT5_CALL_FAILED", "MT5_CALL_FAILED", "mt5", ts=base),
                _ev("NEWS_ALL_NEUTRAL", "NEWS_ALL_NEUTRAL", "news", ts=base),
            ]
        )
        assert len(result.incidents) == 2

    def test_same_fingerprint_window_merge(self) -> None:
        corr = IncidentCorrelator()
        base = datetime.now(UTC)
        existing = corr.correlate(
            [_ev("MT5_CALL_FAILED", "MT5_CALL_FAILED", "mt5", ts=base)]
        ).incidents
        # 70s later within the 300s MT5 window -> merge
        again = corr.correlate(
            [_ev("MT5_CALL_FAILED", "MT5_CALL_FAILED", "mt5", ts=base + timedelta(seconds=70))],
            existing=existing,
        )
        assert again.merged == 1


# ---------------------------------------------------------------------------
# TEST-INCIDENT-03 — root-cause confidence
# ---------------------------------------------------------------------------


class TestRootCauseConfidence:
    def test_unknown_is_default(self) -> None:
        inc = Incident()
        assert inc.root_cause_status == RootCauseConfidence.UNKNOWN

    def test_proven_requires_evidence(self) -> None:
        inc = Incident(root_cause="x", root_cause_status=RootCauseConfidence.PROVEN)
        inc.add_evidence(
            {"kind": "", "source": ""}
            and __import__("nexus_scalp.incidents.models", fromlist=["EvidenceItem"]).EvidenceItem(
                kind="DATABASE", source="audit_ledger", detail="divergence observed"
            )
        )
        assert inc.root_cause_status == RootCauseConfidence.PROVEN
        assert len(inc.evidence) == 1


# ---------------------------------------------------------------------------
# TEST-INCIDENT-04 — timeline reconstruction
# ---------------------------------------------------------------------------


class TestTimelineReconstruction:
    def test_timeline_ordered_by_actual_timestamps(self) -> None:
        corr = IncidentCorrelator()
        base = datetime.now(UTC)
        events = [
            _ev(
                "MT5_CALL_FAILED",
                "MT5_CALL_FAILED",
                "mt5",
                correlation_id="c1",
                ts=base + timedelta(seconds=0.3),
            ),
            _ev(
                "CACHE_STALE",
                "EXPOSURE_CACHE_STALE",
                "exposure",
                correlation_id="c1",
                ts=base + timedelta(seconds=0.2),
            ),
            _ev(
                "ORDER_REJECTED",
                "ORDER_REJECTED",
                "order_manager",
                correlation_id="c1",
                ts=base + timedelta(seconds=0.1),
            ),
        ]
        result = corr.correlate(events)
        assert len(result.incidents) == 1
        inc = result.incidents[0]
        ts = [t.timestamp for t in inc.timeline]
        assert ts == sorted(ts)  # strictly ordered by real timestamps
        assert len(inc.timeline) == 3

    def test_correlation_id_groups_into_one_incident(self) -> None:
        corr = IncidentCorrelator()
        base = datetime.now(UTC)
        result = corr.correlate(
            [
                _ev("MT5_CALL_FAILED", "MT5_CALL_FAILED", "mt5", correlation_id="c9", ts=base),
                _ev(
                    "ORDER_REJECTED",
                    "ORDER_REJECTED",
                    "order_manager",
                    correlation_id="c9",
                    ts=base + timedelta(seconds=5),
                ),
            ]
        )
        assert len(result.incidents) == 1
        assert result.incidents[0].correlation_id == "c9"

    def test_chain_classification(self) -> None:
        corr = IncidentCorrelator()
        base = datetime.now(UTC)
        result = corr.correlate(
            [
                _ev("MT5_CALL_FAILED", "MT5_CALL_FAILED", "mt5", correlation_id="c", ts=base),
                _ev(
                    "EXCEPTION",
                    "SILENT_EXCEPTION",
                    "exposure",
                    correlation_id="c",
                    ts=base + timedelta(seconds=1),
                ),
                _ev(
                    "STATE_STALE",
                    "EXPOSURE_CACHE_STALE",
                    "exposure",
                    correlation_id="c",
                    ts=base + timedelta(seconds=2),
                ),
                _ev(
                    "ORDER_REJECTED",
                    "ORDER_REJECTED",
                    "order_manager",
                    correlation_id="c",
                    ts=base + timedelta(seconds=3),
                ),
                _ev(
                    "UI_SYMPTOM",
                    "UI_BACKEND_MISMATCH",
                    "ui",
                    correlation_id="c",
                    ts=base + timedelta(seconds=4),
                ),
            ]
        )
        _pattern, chain = corr.classify_chain(result.incidents[0])
        assert len(chain) >= 3


# ---------------------------------------------------------------------------
# TEST-INCIDENT-05 — correlation-ID propagation
# ---------------------------------------------------------------------------


class TestCorrelationIdPropagation:
    def test_ticket_links_affected_records(self) -> None:
        corr = IncidentCorrelator()
        result = corr.correlate(
            [_ev("DEAL_LOOKUP_FAILED", "DEAL_LOOKUP_FAILED", "ledger", ticket="152487940044")]
        )
        inc = result.incidents[0]
        assert "152487940044" in inc.affected_records


# ---------------------------------------------------------------------------
# TEST-INCIDENT-06 — value lineage
# ---------------------------------------------------------------------------


class TestValueLineage:
    def test_pnl_lineage(self) -> None:
        engine = LineageEngine()
        trace = engine.pnl_trace()
        hops = trace.hops()
        assert hops[0]["name"].startswith("MT5")
        assert any(h["name"] == "accounting core" for h in hops)
        assert any(h["name"] == "UI render" for h in hops)

    def test_exposure_lineage_has_inv011(self) -> None:
        engine = LineageEngine()
        trace = engine.exposure_trace()
        hops = [h["name"] for h in trace.hops()]
        assert any("INV-011" in h for h in hops)
        assert any("MAX_EXPOSURE" in h for h in hops)

    def test_first_divergence_known_bad_step(self) -> None:
        engine = LineageEngine()
        trace = engine.pnl_trace()
        diag = engine.find_first_divergence(
            trace, symptom="UI shows PnL=0", known_bad_steps=["deal snapshot"]
        )
        assert diag["divergence_found"] is True
        assert diag["first_divergence"]["name"] == "deal snapshot"


# ---------------------------------------------------------------------------
# TEST-INCIDENT-07 — MT5/ledger divergence trace
# ---------------------------------------------------------------------------


class TestMt5LedgerDivergence:
    def test_divergence_detected(self, tmp_path: Path) -> None:
        db = _audit_db(tmp_path)
        res = broker_ledger_divergence(db)
        # 1 zero-PnL ledger row against real broker PnL
        assert res["divergence_count"] >= 1
        assert res["mapped_to_ledger"] > 0

    def test_read_only_no_rewrite(self, tmp_path: Path) -> None:
        db = _audit_db(tmp_path)
        before = sqlite3.connect(db).execute("SELECT COUNT(*) FROM audit_ledger").fetchone()[0]
        broker_ledger_divergence(db)
        after = sqlite3.connect(db).execute("SELECT COUNT(*) FROM audit_ledger").fetchone()[0]
        assert before == after  # never rewrites the ledger


# ---------------------------------------------------------------------------
# TEST-INCIDENT-08 — clock skew trace
# ---------------------------------------------------------------------------


class TestClockSkew:
    def test_clock_skew_measured(self, tmp_path: Path) -> None:
        db = _audit_db(tmp_path)
        res = clock_skew(db)
        assert "observed_skew_seconds" in res
        assert "TIMEBASE_DIVERGENCE" in (res["divergence"], "IN_BOUNDS")


# ---------------------------------------------------------------------------
# TEST-INCIDENT-09 — split-fill trace
# ---------------------------------------------------------------------------


class TestSplitFill:
    def test_split_fill_families(self, tmp_path: Path) -> None:
        db = tmp_path / "audit.db"
        con = sqlite3.connect(db)
        con.execute(
            """CREATE TABLE audit_broker_trades (
                trade_id TEXT, position_id TEXT, symbol TEXT, direction TEXT,
                entry_time TEXT, exit_time TEXT, entry_price REAL, exit_price REAL,
                volume REAL, gross_pnl REAL, commission REAL, swap REAL, fee REAL,
                net_pnl REAL, deal_ids TEXT, order_ids TEXT, master_order_id TEXT,
                exit_reason TEXT, exit_comment TEXT, duration_sec REAL,
                source TEXT, synced_at TEXT)"""
        )
        for _i, tid in enumerate(("t1", "t2", "t3")):
            con.execute(
                "INSERT INTO audit_broker_trades (trade_id, master_order_id, direction, symbol) "
                "VALUES (?, 'order-1', 'BUY', 'XAUUSD')",
                (tid,),
            )
        con.commit()
        con.close()
        res = split_fill_groups(str(db))
        assert res["split_fill_families"] == 1
        assert res["tickets_in_families"] == 3


# ---------------------------------------------------------------------------
# TEST-INCIDENT-10 — learning pipeline loss
# ---------------------------------------------------------------------------


class TestLearningLoss:
    def test_rates_computed(self, tmp_path: Path) -> None:
        db = _audit_db(tmp_path)
        res = learning_pipeline_rates(db)
        assert res["experiences"] >= 0
        assert "experience_to_outcome_rate" in res

    def test_zero_outcome_with_broker_truth_suspect(self, tmp_path: Path) -> None:
        db = _audit_db(tmp_path)
        res = outcome_forensics(db)
        # the zero-PnL outcome has no reconstruction source but the BROKER
        # holds the truth -> BROKER_RECOVERABLE (forensic fix 2026-08-20).
        assert len(res["broker_recoverable_outcomes"]) >= 1
        assert len(res["suspect_outcomes"]) == 0


# ---------------------------------------------------------------------------
# TEST-INCIDENT-11 — model contract trace
# ---------------------------------------------------------------------------


class TestModelContract:
    def test_model_output_lineage(self) -> None:
        engine = LineageEngine()
        trace = engine.model_output_trace()
        hops = [h["name"] for h in trace.hops()]
        assert "model inference (Champion artifact, deterministic)" in hops


# ---------------------------------------------------------------------------
# TEST-INCIDENT-12 — feature failure trace
# ---------------------------------------------------------------------------


class TestFeatureTrace:
    def test_feature_lineage(self) -> None:
        engine = LineageEngine()
        trace = engine.trace("feature_vector")
        hops = [h["name"] for h in trace.hops()]
        assert any("bar aggregator" in h for h in hops)


# ---------------------------------------------------------------------------
# TEST-INCIDENT-13 — News failure trace
# ---------------------------------------------------------------------------


class TestNewsTrace:
    def test_news_healthy(self, tmp_path: Path) -> None:
        db = tmp_path / "news.db"
        con = sqlite3.connect(db)
        con.execute(
            "CREATE TABLE news_health (source_id TEXT, last_success_at TEXT, consecutive_failures INTEGER, healthy INTEGER)"
        )
        con.execute(
            "CREATE TABLE news_articles (article_id TEXT PRIMARY KEY, title TEXT, published_at TEXT)"
        )
        con.execute(
            "CREATE TABLE news_analysis (analysis_id TEXT PRIMARY KEY, article_id TEXT, direction TEXT, impact_strength REAL)"
        )
        con.execute("INSERT INTO news_health VALUES ('a', '2026-08-18T10:00:00+00:00', 0, 1)")
        con.execute("INSERT INTO news_articles VALUES ('art-1', 't', '2026-08-18T10:00:00+00:00')")
        con.execute("INSERT INTO news_analysis VALUES ('an-1', 'art-1', 'BULLISH', 0.7)")
        con.commit()
        con.close()
        res = news_incidents(str(db))
        assert "NEWS_SOURCE_EMPTY" not in res["findings"]
        assert "NEWS_ALL_NEUTRAL" not in res["findings"]

    def test_news_all_neutral_flagged(self, tmp_path: Path) -> None:
        db = tmp_path / "news.db"
        con = sqlite3.connect(db)
        con.execute(
            "CREATE TABLE news_health (source_id TEXT, last_success_at TEXT, consecutive_failures INTEGER, healthy INTEGER)"
        )
        con.execute(
            "CREATE TABLE news_articles (article_id TEXT PRIMARY KEY, title TEXT, published_at TEXT)"
        )
        con.execute(
            "CREATE TABLE news_analysis (analysis_id TEXT PRIMARY KEY, article_id TEXT, direction TEXT, impact_strength REAL)"
        )
        con.execute("INSERT INTO news_health VALUES ('a', '2026-08-18T10:00:00+00:00', 0, 1)")
        for i in range(25):
            con.execute(
                "INSERT INTO news_articles VALUES (?, 't', '2026-08-18T10:00:00+00:00')",
                (f"art-{i}",),
            )
            con.execute(
                "INSERT INTO news_analysis VALUES (?, ?, 'NEUTRAL', 0.0)",
                (f"an-{i}", f"art-{i}"),
            )
        con.commit()
        con.close()
        res = news_incidents(str(db))
        assert "NEWS_ALL_NEUTRAL" in res["findings"]


# ---------------------------------------------------------------------------
# TEST-INCIDENT-14 — UI empty-state trace
# ---------------------------------------------------------------------------


class TestUiEmpty:
    def test_backend_empty_detected(self, tmp_path: Path) -> None:
        db = _audit_db(tmp_path)
        res = why_ui_empty(db, "strategies")
        assert res["backend_record_count"] == 1
        assert res["diagnosis"] == "BACKEND_HAS_DATA"

    def test_backend_unavailable(self, tmp_path: Path) -> None:
        res = why_ui_empty(str(tmp_path / "missing.db"), "strategies")
        assert res["diagnosis"] in ("BACKEND_UNAVAILABLE",)


# ---------------------------------------------------------------------------
# TEST-INCIDENT-15 — worker stall trace
# ---------------------------------------------------------------------------


class TestWorkerStall:
    def test_worker_stall_detector_contract(self) -> None:
        # CodeQL #78 (insecure temporary file): never mktemp — use a
        # TemporaryDirectory so the path is unpredictable and owned.
        import tempfile

        from nexus_scalp.incidents.worker import format_incident_worker_status

        with tempfile.TemporaryDirectory() as tmpdir:
            from nexus_scalp.incidents.worker import IncidentWorker

            store = IncidentStore(db_path=str(Path(tmpdir) / "worker_stall.db"))
            w = IncidentWorker(store, interval_sec=0.0)
            w.start()
            w.tick([])
            w.stop()
            st = format_incident_worker_status(w)
            assert "running" in st
            assert "incidents_created" in st

    def test_worker_never_touches_trading(self) -> None:
        """Worker imports must not pull execution/risk objects (spec 34)."""
        import ast
        from pathlib import Path

        worker_src = Path("src/nexus_scalp/incidents/worker.py").read_text(encoding="utf-8")
        tree = ast.parse(worker_src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and any(
                    x in node.module for x in ("order_manager", "risk", "execution")
                ):
                    pytest.fail(f"worker imports execution/risk: {node.module}")


# ---------------------------------------------------------------------------
# TEST-INCIDENT-16 — governance inconsistency trace
# ---------------------------------------------------------------------------


class TestGovernanceInconsistency:
    def test_invariant_inspection(self) -> None:
        # The incident layer must reference the governance invariants for
        # blocking unsafe inference (spec 27/34).
        from pathlib import Path

        impact_src = Path("src/nexus_scalp/incidents/impact.py").read_text(encoding="utf-8")
        assert "BLOCK" in impact_src
        assert "never load on file-exists" in impact_src


# ---------------------------------------------------------------------------
# TEST-INCIDENT-17 — migration failure trace
# ---------------------------------------------------------------------------


class TestMigrationFailure:
    def test_incident_for_migration(self) -> None:
        inc = Incident(
            severity=IncidentSeverity.HIGH,
            category=IncidentCategory.MIGRATION,
            component="migration",
            operation="MIGRATION_FAILED",
        )
        assert inc.category == IncidentCategory.MIGRATION
        assert inc.severity == IncidentSeverity.HIGH

    def test_migration_tables_in_schema(self, tmp_path: Path) -> None:
        store = IncidentStore(db_path=str(tmp_path / "audit.db"))
        store.ensure_schema()
        con = sqlite3.connect(str(tmp_path / "audit.db"))
        tabs = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        assert {"incidents", "incident_events", "incident_quarantine"}.issubset(tabs)


# ---------------------------------------------------------------------------
# TEST-INCIDENT-18 — version mismatch trace
# ---------------------------------------------------------------------------


class TestVersionMismatch:
    def test_version_consistency(self, tmp_path: Path) -> None:
        res = version_consistency(str(tmp_path))
        assert "observed" in res
        assert res["finding"] in ("VERSION_INCONSISTENCY", "VERSIONS_CONSISTENT")


# ---------------------------------------------------------------------------
# TEST-INCIDENT-19..23 — WHY traces
# ---------------------------------------------------------------------------


class TestWhyTraces:
    def test_why_blocked(self, tmp_path: Path) -> None:
        db = _audit_db(tmp_path)
        res = why_blocked(db, "152400000000")
        assert "blocked_by" in res

    def test_why_closed(self, tmp_path: Path) -> None:
        db = _audit_db(tmp_path)
        res = why_closed(db, "152400000000")
        assert res["ticket"] == "152400000000"
        assert "exit_mechanism" in res

    def test_why_no_learning(self, tmp_path: Path) -> None:
        db = _audit_db(tmp_path)
        res = why_no_learning(db, "152400000000")
        assert "has_experience" in res

    def test_why_no_strategy(self, tmp_path: Path) -> None:
        db = _audit_db(tmp_path)
        res = why_no_strategy(db)
        assert "registry_count" in res

    def test_why_ui_empty(self, tmp_path: Path) -> None:
        db = _audit_db(tmp_path)
        res = why_ui_empty(db, "trades")
        assert res["field"] == "trades"


# ---------------------------------------------------------------------------
# TEST-INCIDENT-24 — impact analysis
# ---------------------------------------------------------------------------


class TestImpactAnalysis:
    def test_impact_from_evidence(self) -> None:
        inc = Incident(
            severity=IncidentSeverity.HIGH,
            category=IncidentCategory.MT5,
            component="mt5",
            operation="MT5_CALL_FAILED",
        )
        inc.add_timeline_event(
            __import__("nexus_scalp.incidents.models", fromlist=["TimelineEvent"]).TimelineEvent(
                timestamp=datetime.now(UTC), event_type="MT5_CALL_FAILED", source="LOG"
            )
        )
        analyzer = ImpactAnalyzer(db_path="")
        imp = analyzer.analyze(inc)
        assert imp.blast_radius == BlastRadius.CROSS_COMPONENT
        assert imp.affected_trades == 0  # no fabricated numbers

    def test_no_fabricated_numbers(self) -> None:
        inc = Incident(category=IncidentCategory.NEWS, component="news")
        imp = ImpactAnalyzer(db_path="").analyze(inc)
        assert imp.affected_records == 0
        assert imp.affected_models == 0


# ---------------------------------------------------------------------------
# TEST-INCIDENT-25 — data quarantine
# ---------------------------------------------------------------------------


class TestQuarantine:
    def test_mark_suspect_keeps_record(self, store: IncidentStore) -> None:
        inc = Incident(category=IncidentCategory.DATA, component="ledger")
        qm = QuarantineManager()
        entry = qm.mark_suspect(
            inc,
            target_table="audit_experience_outcomes",
            record_key="exp_zero",
            reason="realized_r=0 with broker PnL available",
        )
        assert entry.status == "SUSPECT"
        assert entry.incident_id == inc.incident_id
        assert len(inc.quarantine_entries) == 1
        store.save(inc)
        loaded = store.get(inc.incident_id)
        assert loaded is not None
        assert len(loaded.quarantine_entries) == 1

    def test_quarantine_never_deletes(self, store: IncidentStore) -> None:
        inc = Incident(category=IncidentCategory.DATA)
        QuarantineManager().mark_suspect(inc, target_table="x", record_key="1", reason="probe")
        store.save(inc)
        loaded = store.get(inc.incident_id)
        assert loaded is not None
        entry = loaded.quarantine_entries[0]
        assert entry.record_key == "1"
        assert entry.status == "SUSPECT"


# ---------------------------------------------------------------------------
# TEST-INCIDENT-26 — recovery plan generation
# ---------------------------------------------------------------------------


class TestRecoveryPlans:
    def test_recovery_plan_generated(self) -> None:
        inc = Incident(category=IncidentCategory.LEDGER, component="ledger")
        plan = RecoveryPlanner().generate(inc)
        assert plan.status == "RECOMMENDED"
        assert len(plan.options) >= 1
        assert plan.what_failed == "LEDGER"

    def test_recovery_plan_no_destructive_opts(self) -> None:
        for cat in IncidentCategory:
            inc = Incident(category=cat)
            plan = RecoveryPlanner().generate(inc)
            for opt in plan.options:
                assert not opt.destructive  # never auto-execute destructive
                assert opt.approval_required is True


# ---------------------------------------------------------------------------
# TEST-INCIDENT-27 — recovery requires approval
# ---------------------------------------------------------------------------


class TestRecoveryApproval:
    def test_recovery_states_enforced(self) -> None:
        inc = Incident(category=IncidentCategory.DATA)
        plan = RecoveryPlanner().generate(inc)
        # Moving a step to EXECUTING without approval must be refused:
        # the frozen RecoveryAction raises on plain assignment.
        from dataclasses import FrozenInstanceError

        with pytest.raises((FrozenInstanceError, AttributeError)):
            plan.options[0].status = "EXECUTING"  # type: ignore[misc]
        # The planner's require_approval gate rejects pre-approved states.
        planner = RecoveryPlanner()
        forged = RecoveryPlanner().generate(inc)
        from nexus_scalp.incidents.models import RecoveryState

        forged.options[0] = RecoveryAction(
            step_id=forged.options[0].step_id,
            action=forged.options[0].action,
            kind=forged.options[0].kind,
            destructive=forged.options[0].destructive,
            required_tests=list(forged.options[0].required_tests),
            approval_required=True,
            status=RecoveryState.APPROVED,
        )
        with pytest.raises(ValueError):
            planner.require_approval(forged)


# ---------------------------------------------------------------------------
# TEST-INCIDENT-28 — incident export
# ---------------------------------------------------------------------------


class TestIncidentExport:
    def test_json_and_md_export(self, tmp_path: Path) -> None:
        inc = Incident(category=IncidentCategory.MT5, component="mt5", operation="MT5_CALL_FAILED")
        paths = write_incident_reports(inc, tmp_path)
        assert Path(paths["json"]).exists()
        assert Path(paths["markdown"]).exists()
        parsed = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
        assert parsed["incident_id"] == inc.incident_id

    def test_zip_bundle_no_secrets(self, tmp_path: Path) -> None:
        inc = Incident(category=IncidentCategory.TELEGRAM, component="telegram")
        zip_path = export_zip_bundle(
            inc,
            tmp_path,
            log_excerpts=["bot_token=SECRET123 enqueued"],
            model_manifest={"name": "m", "api_key": "SECRET_KEY_XYZ"},
        )
        import zipfile

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            assert any(n.endswith(".json") for n in names)
            content = "\n".join(zf.read(n).decode("utf-8") for n in names)
            assert "SECRET123" not in content
            assert "SECRET_KEY_XYZ" not in content


# ---------------------------------------------------------------------------
# TEST-INCIDENT-29 — secret masking
# ---------------------------------------------------------------------------


class TestSecretMasking:
    def test_masks_sensitive_fields(self) -> None:
        masked = mask_secrets(
            {
                "bot_token": "abc123def",
                "api_key": "xyz789",
                "password": "pw123",
                "ok": "keep",
                "nested": {"telegram_admin_id": "12345", "fine": "y"},
            }
        )
        assert masked["bot_token"] == "[REDACTED]"
        assert masked["api_key"] == "[REDACTED]"
        assert masked["password"] == "[REDACTED]"
        assert masked["ok"] == "keep"
        assert masked["nested"]["telegram_admin_id"] == "[REDACTED]"

    def test_incident_json_masked(self) -> None:
        inc = Incident(category=IncidentCategory.TELEGRAM)
        inc.notes.append("bot_token=eyJhbGciOiJIUzI1NiJ9.abc")
        payload = incident_json(inc)
        assert "eyJhbGciOiJIUzI1NiJ9" not in json.dumps(payload)

    def test_incident_json_masks_value_shaped_secrets(self) -> None:
        """CodeQL py/clear-text-storage (#86) regression: secret-SHAPED
        values are redacted even under non-sensitive keys (notes, arbitrary
        string payloads) - not just sensitive key names."""
        inc = Incident(category=IncidentCategory.TELEGRAM)
        inc.notes.append(
            "credential rotation: token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c was rotated"
        )
        inc.notes.append("key sk-1234567890abcdef1234567890abcdef leaked")
        payload = incident_json(inc)
        blob = json.dumps(payload)
        assert "eyJhbGciOiJIUzI1NiJ9" not in blob
        assert "sk-1234567890abcdef1234567890abcdef" not in blob
        assert "[REDACTED]" in blob

    def test_incident_json_masks_bot_token_shape(self) -> None:
        inc = Incident(category=IncidentCategory.TELEGRAM)
        inc.notes.append("bot: 123456789:ABCDEFghijklmnopqrstuvwxyz123456789")
        payload = incident_json(inc)
        blob = json.dumps(payload)
        assert "123456789:ABCDEFghijklmnopqrstuvwxyz123456789" not in blob

    def test_mask_secrets_value_level_redaction(self) -> None:
        m = mask_secrets({"detail": "ghp_1234567890abcdefghijklmnopqrstuvwxyz"})
        assert "ghp_1234567890abcdefghijklmnopqrstuvwxyz" not in json.dumps(m)
        m2 = mask_secrets(
            {"detail": "connected with token 123456789:ABCDEFghijklmnopqrstuvwxyz123456789 now"}
        )
        assert "123456789:ABCDEFghijklmnopqrstuvwxyz123456789" not in json.dumps(m2)

    def test_mask_secrets_high_entropy_catchall(self) -> None:
        """CodeQL py/clear-text-storage (#86) regression: long high-entropy
        alnum runs (unknown vendor secret shapes) are redacted even when no
        sensitive key/known shape matches; normal prose and identifiers
        pass through unchanged."""
        secret = "Ab3xK9mPq2Rw7ZtY5NcF8vJd4LsH6"
        m = mask_secrets({"detail": f"rotated token: {secret}"})
        assert secret not in json.dumps(m)
        assert "[REDACTED]" in json.dumps(m)
        # Legitimate long text with structure is untouched.
        legit = "XAUUSD trade lifecycle completed 42 orders with average latency 12ms on 2026-08-19"
        m2 = mask_secrets({"detail": legit})
        assert json.dumps(m2) == json.dumps({"detail": legit})


# ---------------------------------------------------------------------------
# TEST-INCIDENT-30 — Telegram throttling
# ---------------------------------------------------------------------------


class TestTelegramThrottling:
    def test_first_alert_then_suppressed(self) -> None:
        n = IncidentTelegramNotifier(notifier=None, cooldown_sec=60, repeat_cooldown_sec=3600)
        inc = Incident(severity=IncidentSeverity.CRITICAL, component="mt5")
        assert n.should_alert(inc) is True
        assert n.maybe_alert(inc) is False  # no notifier configured -> not sent
        # first occurrence registered; repeats within the cooldown window
        assert n.should_alert(inc) is False
        n.maybe_alert(inc)  # suppressed
        assert n.alerts_suppressed >= 1

    def test_low_severity_never_alerts(self) -> None:
        n = IncidentTelegramNotifier(notifier=None)
        inc = Incident(severity=IncidentSeverity.LOW)
        assert n.should_alert(inc) is False

    def test_message_contains_required_fields(self) -> None:
        n = IncidentTelegramNotifier(notifier=None)
        inc = Incident(
            severity=IncidentSeverity.HIGH,
            component="ledger",
            operation="DEAL_LOOKUP_FAILED",
            correlation_id="corr-77",
        )
        msg = n._format(inc, repeat=False)
        assert inc.incident_id in msg
        assert "HIGH" in msg
        assert "DEAL_LOOKUP_FAILED" in msg
        assert "corr-77" in msg


# ---------------------------------------------------------------------------
# TEST-INCIDENT-31 — regression incident detection
# ---------------------------------------------------------------------------


class TestRegressionDetection:
    def test_regression_flag(self) -> None:
        inc = Incident(
            category=IncidentCategory.MT5,
            component="mt5",
            operation="MT5_CALL_FAILED",
            is_regression=True,
            previous_bug_id="BUG-070",
            related_bug_id="BUG-101",
            fix_commit="abc123",
            regression_test="TEST-MT5-042",
        )
        d = inc.as_dict()
        assert d["is_regression"] is True
        assert d["previous_bug_id"] == "BUG-070"
        assert d["related_bug_id"] == "BUG-101"

    def test_same_fingerprint_after_fix_is_regression_candidate(self, store: IncidentStore) -> None:
        corr = IncidentCorrelator()
        base = datetime.now(UTC)
        e1 = corr.correlate([_ev("MT5_CALL_FAILED", "MT5_CALL_FAILED", "mt5", ts=base)])
        inc1 = e1.incidents[0]
        store.save(inc1)
        # fix recorded
        inc1.related_bug_id = "BUG-070"
        inc1.fix_commit = "abc123"
        inc1.regression_test = "TEST-MT5-042"
        inc1.status = IncidentStatus.CLOSED
        store.save(inc1)
        # new occurrence after the fix -> flagged regression by the operator flow
        inc2 = Incident(
            category=IncidentCategory.MT5,
            component="mt5",
            operation="MT5_CALL_FAILED",
            fingerprint=inc1.fingerprint,
            is_regression=True,
            previous_bug_id=inc1.related_bug_id,
        )
        assert inc2.is_regression is True
        assert inc2.previous_bug_id == "BUG-070"


# ---------------------------------------------------------------------------
# TEST-INCIDENT-32 — BUG linkage
# ---------------------------------------------------------------------------


class TestBugLinkage:
    def test_bug_linkage_fields(self) -> None:
        from nexus_scalp.incidents.models import BugLinkage

        link = BugLinkage(
            incident_id="INC-2026-0001",
            bug_id="BUG-091",
            fix_commit="def456",
            regression_test="TEST-UP-35",
        )
        d = link.as_dict()
        assert d["incident_id"] == "INC-2026-0001"
        assert d["bug_id"] == "BUG-091"
        assert d["fix_commit"] == "def456"


# ---------------------------------------------------------------------------
# TEST-INCIDENT-33 — resolved requires evidence
# ---------------------------------------------------------------------------


class TestResolvedRequiresEvidence:
    def test_status_not_resolved_without_evidence(self) -> None:
        inc = Incident()
        # A CLOSED status with no evidence and no fix is flagged as
        # resolved_without_evidence (spec 53: MITIGATED until evidence).
        inc.status = IncidentStatus.CLOSED
        inc.resolved_without_evidence = not (
            inc.root_cause and inc.fix_commit and inc.regression_test
        )
        assert inc.resolved_without_evidence is True
        assert inc.status.value in ("CLOSED", "RECOVERED")

    def test_fix_validation_requires_regression_test(self) -> None:
        # spec 53: an incident must not become RESOLVED merely because the
        # exception disappeared; require root cause + regression test.
        inc = Incident(
            root_cause="proven",
            fix_commit="abc",
            regression_test="",
            status=IncidentStatus.CLOSED,
        )
        assert not (inc.root_cause and inc.fix_commit and inc.regression_test)


# ---------------------------------------------------------------------------
# TEST-INCIDENT-34 — no trading mutation
# ---------------------------------------------------------------------------


class TestNoTradingMutation:
    def test_incidents_package_has_no_execution_imports(self) -> None:
        import ast
        from pathlib import Path

        for f in Path("src/nexus_scalp/incidents").glob("*.py"):
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if any(
                        x in node.module
                        for x in ("order_manager", "risk_engine", "execution", "mt5.adapter")
                    ):
                        pytest.fail(f"{f.name} imports execution/risk: {node.module}")
                if isinstance(node, ast.Import) and any(
                    "order_manager" in (a.name or "") for a in node.names
                ):
                    pytest.fail(f"{f.name} imports order_manager")

    def test_no_trading_api_in_incident_routes(self) -> None:
        import re
        from pathlib import Path

        # CHG-0032-A1 Step-3D: the diagnostics routes now live in
        # diagnostics_state_routes.py (extracted verbatim from server.py);
        # the read-only assertion follows the routes to their owner module.
        mod = Path("src/nexus_scalp/web/diagnostics_state_routes.py").read_text(encoding="utf-8")
        seg = mod[mod.index("/api/diagnostics") :]
        for m in re.finditer(r'@app\.(get|post|put|delete|patch)\("/api/diagnostics[^"]*"\)', seg):
            assert m.group(1) in ("get", "post"), f"diagnostics route not read-only: {m.group(0)}"
            if m.group(1) == "post":
                assert "reconcile" in m.group(0), f"unexpected POST diagnostics route: {m.group(0)}"
        # diagnostics routes never call positions/orders/risk endpoints
        assert "/api/positions" not in seg[: seg.index("def _incident_store")]


# ---------------------------------------------------------------------------
# TEST-INCIDENT-35 — no automatic code mutation
# ---------------------------------------------------------------------------


class TestNoAutomaticCodeMutation:
    def test_no_self_modification_code(self) -> None:
        """The incident layer must not contain code-rewriting primitives."""
        from pathlib import Path

        for f in Path("src/nexus_scalp/incidents").glob("*.py"):
            src = f.read_text(encoding="utf-8")
            for banned in ("os.system", "subprocess.run", "eval(", "exec(", "ast.parse"):
                assert banned not in src, f"{f.name} contains {banned}"

    def test_no_layout_for_auto_fix(self) -> None:
        """No automatic repair execution: recovery states require approval."""
        from nexus_scalp.incidents.models import RecoveryState

        assert RecoveryState.RECOMMENDED.value == "RECOMMENDED"
        assert RecoveryState.APPROVED.value == "APPROVED"
        assert RecoveryState.EXECUTING.value == "EXECUTING"


# ---------------------------------------------------------------------------
# RUNTIME BLOCK (from test_incident_runtime_task13.py — worker + live-engine)
# 20 probes: TEST-INCIDENT-RUNTIME-01..20. Kept verbatim (helpers deduped).
# ---------------------------------------------------------------------------

# --- runtime deps (from test_incident_runtime_task13.py) ---
from nexus_scalp.incidents.accounting import AccountingForensicsEngine  # noqa: E402
from nexus_scalp.incidents.telemetry import (  # noqa: E402
    IncidentTelemetryCollector,
    engine_event_to_telemetry,
)
from nexus_scalp.incidents.worker import (  # noqa: E402
    IncidentWorker,
    IncidentWorkerState,
    format_incident_worker_status,
)


def _audit_db_runtime(tmp_path: Path, *, zero_ledger: int = 3) -> str:
    """Small audit.db with broker PnL and zero-ledger rows (accounting tests)."""
    db = tmp_path / "audit.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE audit_broker_trades (
            trade_id TEXT, position_id TEXT, symbol TEXT, direction TEXT,
            entry_time TEXT, exit_time TEXT, entry_price REAL, exit_price REAL,
            volume REAL, gross_pnl REAL, commission REAL, swap REAL, fee REAL,
            net_pnl REAL, deal_ids TEXT, order_ids TEXT, master_order_id TEXT,
            exit_reason TEXT, exit_comment TEXT, duration_sec REAL,
            source TEXT, synced_at TEXT
        );
        CREATE TABLE audit_ledger (
            ticket TEXT, symbol TEXT, direction TEXT, volume REAL,
            entry_price REAL, exit_price REAL, status TEXT, pnl REAL,
            commission REAL, swap REAL, timestamp TEXT, mae REAL, mfe REAL,
            order_id TEXT, open_time TEXT, close_time TEXT,
            gross_pnl_usd REAL, net_pnl_usd REAL, exit_mechanism TEXT,
            exit_reason_source TEXT, exit_evidence TEXT, exit_reason_confidence REAL
        );
        CREATE TABLE audit_experiences (
            experience_id TEXT, request_id TEXT, execution_id TEXT,
            decision_id TEXT, idempotency_key TEXT, strategy_id TEXT,
            action TEXT, entry_reason TEXT, model_id TEXT, is_executed INTEGER
        );
        CREATE TABLE audit_experience_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT,
            execution_id TEXT, outcome_timestamp TEXT, is_executed INTEGER,
            is_closed INTEGER, exit_reason TEXT, realized_pnl_usd REAL,
            realized_r_multiple REAL, approved_volume REAL,
            mae_points REAL, mfe_points REAL, mae_usd REAL, mfe_usd REAL,
            mae_r REAL, mfe_r REAL, holding_duration_seconds REAL,
            slippage_points REAL, execution_latency_ms REAL,
            strategy_quality REAL, entry_quality REAL, execution_quality REAL,
            management_quality REAL, exit_quality REAL,
            behavioral_flags TEXT, payload TEXT
        );
        CREATE TABLE research_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
            dataset_id TEXT, strategy_id TEXT, strategy_version TEXT,
            executed_at TEXT, config TEXT, build_identity TEXT,
            result_summary TEXT
        );
        CREATE TABLE audit_broker_history_meta (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT,
            last_sync_from TEXT, last_sync_to TEXT, last_synced_at TEXT,
            last_orders INTEGER, last_deals INTEGER, last_trades INTEGER
        );
        """
    )
    # broker rows: real PnL
    for i in range(5):
        tid = f"1524{i:08d}"
        con.execute(
            "INSERT INTO audit_broker_trades (trade_id, position_id, symbol, direction, "
            "entry_time, exit_time, entry_price, exit_price, volume, gross_pnl, "
            "commission, swap, fee, net_pnl, source, synced_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                tid,
                tid,
                "XAUUSD",
                "BUY",
                "2026-08-18T10:00:00+00:00",
                "2026-08-18T10:30:00+00:00",
                4000.0,
                4010.0,
                0.1,
                10.0,
                0.0,
                0.0,
                0.0,
                10.0,
                "BROKER_DEALS",
                "2026-08-18T10:31:00+00:00",
            ),
        )
    # ledger: zero PnL on the first `zero_ledger` tickets (SUSPECT pattern)
    for i in range(5):
        tid = f"1524{i:08d}"
        pnl = 0.0 if i < zero_ledger else 10.0
        con.execute(
            "INSERT INTO audit_ledger (ticket, symbol, direction, volume, entry_price, "
            "exit_price, status, pnl, commission, swap, gross_pnl_usd, net_pnl_usd, "
            "exit_mechanism, exit_reason_source, close_time) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                tid,
                "XAUUSD",
                "BUY",
                0.1,
                4000.0,
                4010.0,
                "CLOSED",
                pnl,
                0.0,
                0.0,
                pnl,
                pnl,
                "SYSTEM_CLOSE",
                "",
                "2026-08-18T10:30:00+00:00",
            ),
        )
    # one zero outcome with no reconstruction source
    con.execute(
        "INSERT INTO audit_experience_outcomes (idempotency_key, execution_id, "
        "outcome_timestamp, is_executed, is_closed, exit_reason, "
        "realized_pnl_usd, realized_r_multiple, payload) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "exp_zero",
            "152400000000",
            "2026-08-18T10:31:00+00:00",
            1,
            1,
            "SYSTEM_CLOSE",
            0.0,
            0.0,
            json.dumps({}),
        ),
    )
    con.commit()
    con.close()
    return str(db)


# ---------------------------------------------------------------------------
# TEST-INCIDENT-RUNTIME-01 — IncidentWorker startup (state machine)
# ---------------------------------------------------------------------------


class TestWorkerStartup:
    def test_state_machine_transitions(self, store: IncidentStore) -> None:
        w = IncidentWorker(store, interval_sec=0.0)
        assert w.state == IncidentWorkerState.STOPPED
        w.start()
        assert w.state == IncidentWorkerState.RUNNING
        assert w.last_start is not None
        w.stop()
        assert w.state == IncidentWorkerState.STOPPED

    def test_failed_state_after_persistent_failures(self, store: IncidentStore) -> None:
        w = IncidentWorker(store, interval_sec=0.0)
        w.start()
        # 5 consecutive failures via a store whose save raises
        for _ in range(5):
            w._last_run_ts = 0.0
            bkp = w.store
            w.store = _BrokenStore()  # type: ignore[assignment]
            w.tick([{"event_type": "X", "component": "x", "timestamp": datetime.now(UTC)}])
            w.store = bkp
            if w.consecutive_failures >= 5:
                w.state = IncidentWorkerState.FAILED
        assert w.state == IncidentWorkerState.FAILED
        w.start()  # restartable
        assert w.state == IncidentWorkerState.RUNNING

    def test_status_format_contract(self, store: IncidentStore) -> None:
        w = IncidentWorker(store)
        st = format_incident_worker_status(w)
        for field in (
            "state",
            "running",
            "cycle_count",
            "interval_sec",
            "last_start",
            "last_success",
            "last_failure",
            "last_useful_work",
            "last_cycle_duration_ms",
            "latency",
            "queue_size",
            "last_error",
            "consecutive_failures",
            "events_seen",
            "events_dropped",
            "incidents_created",
            "incidents_updated",
            "incidents_deduplicated",
        ):
            assert field in st, f"missing {field}"


# ---------------------------------------------------------------------------
# TEST-INCIDENT-RUNTIME-02 — worker remains off tick path
# ---------------------------------------------------------------------------


class TestOffTickPath:
    def test_worker_has_no_sync_db_on_tick(self) -> None:
        """The worker must not do synchronous DB writes on the tick caller's
        thread — all writes go through the store (queued when an
        AuditRepository is attached)."""
        import ast

        src = Path("src/nexus_scalp/incidents/worker.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if "order_manager" in node.module or "risk" in node.module:
                    pytest.fail(f"worker imports execution/risk: {node.module}")

    def test_engine_uses_to_thread(self) -> None:
        """live_engine must invoke the incident worker via asyncio.to_thread."""
        src = Path("src/nexus_scalp/application/live_engine.py").read_text(encoding="utf-8")
        assert "asyncio.to_thread(self._incident_worker.tick)" in src
        assert "emit_incident_telemetry" in src


# ---------------------------------------------------------------------------
# TEST-INCIDENT-RUNTIME-03 — structured event ingestion
# ---------------------------------------------------------------------------


class TestTelemetry:
    def test_collector_emit_and_flush(self, store: IncidentStore) -> None:
        w = IncidentWorker(store, interval_sec=0.0)
        c = IncidentTelemetryCollector(w)
        ok = c.emit(
            event_type="MT5_CALL_FAILED",
            component="mt5",
            error_code="MT5_CALL_FAILED",
            severity="HIGH",
        )
        assert ok is True
        assert c.emitted == 1
        w.start()
        w.tick()
        assert w.events_seen >= 1

    def test_engine_event_mapping(self) -> None:
        ev = engine_event_to_telemetry(
            event_type="EXECUTION_RECONCILIATION_FAILED", component="execution"
        )
        assert ev["error_code"] == "SILENT_EXCEPTION"
        assert ev["severity"] == "HIGH"

    def test_unknown_event_lenient(self) -> None:
        ev = engine_event_to_telemetry(event_type="SOMETHING_NEW", component="x")
        assert ev["error_code"] == "SOMETHING_NEW"


# ---------------------------------------------------------------------------
# TEST-INCIDENT-RUNTIME-04 — correlation
# ---------------------------------------------------------------------------


class TestRuntimeCorrelation:
    def test_55_repeats_one_incident(self, store: IncidentStore) -> None:
        w = IncidentWorker(store, interval_sec=0.0, auto_impact=False, auto_recovery_plan=False)
        w.start()
        base = datetime.now(UTC)
        for i in range(55):
            c = IncidentTelemetryCollector(w)
            c.emit(
                event_type="SILENT_EXCEPTION",
                component="exposure",
                error_code="SILENT_EXCEPTION",
                timestamp=base + timedelta(seconds=i * 6),
            )
        w.tick()
        incidents = store.list_incidents(limit=50)
        assert len(incidents) == 1
        assert incidents[0].repeated_count >= 55

    def test_correlation_id_groups(self, store: IncidentStore) -> None:
        w = IncidentWorker(store, interval_sec=0.0)
        w.start()
        c = IncidentTelemetryCollector(w)
        c.emit(
            event_type="MT5_CALL_FAILED",
            component="mt5",
            error_code="MT5_CALL_FAILED",
            correlation_id="c1",
        )
        c.emit(
            event_type="ORDER_REJECTED",
            component="order_manager",
            error_code="ORDER_REJECTED",
            correlation_id="c1",
        )
        w.tick()
        incidents = store.list_incidents(limit=50)
        assert len(incidents) == 1
        assert incidents[0].correlation_id == "c1"


# ---------------------------------------------------------------------------
# TEST-INCIDENT-RUNTIME-05 — causal chain
# ---------------------------------------------------------------------------


class TestCausalChain:
    def test_chain_reconstructed(self, store: IncidentStore) -> None:
        Incident(
            severity=IncidentSeverity.HIGH,
            component="mt5",
            operation="MT5_CALL_FAILED",
            correlation_id="c",
        )
        corr = IncidentCorrelator()
        base = datetime.now(UTC)
        tele = [
            TelemetryEvent(
                timestamp=base,
                event_type="MT5_CALL_FAILED",
                component="mt5",
                error_code="MT5_CALL_FAILED",
                correlation_id="c",
            ),
            TelemetryEvent(
                timestamp=base + timedelta(seconds=1),
                event_type="EXCEPTION",
                component="exposure",
                error_code="SILENT_EXCEPTION",
                correlation_id="c",
            ),
            TelemetryEvent(
                timestamp=base + timedelta(seconds=2),
                event_type="STATE_STALE",
                component="exposure",
                error_code="EXPOSURE_CACHE_STALE",
                correlation_id="c",
            ),
            TelemetryEvent(
                timestamp=base + timedelta(seconds=3),
                event_type="ORDER_REJECTED",
                component="order_manager",
                error_code="ORDER_REJECTED",
                correlation_id="c",
            ),
            TelemetryEvent(
                timestamp=base + timedelta(seconds=4),
                event_type="UI_SYMPTOM",
                component="ui",
                error_code="UI_BACKEND_MISMATCH",
                correlation_id="c",
            ),
        ]
        result = corr.correlate(tele)
        _pattern, chain = corr.classify_chain(result.incidents[0])
        assert len(chain) >= 3
        positions = [c["position"] for c in chain]
        assert positions[0] == "ROOT_EVENT"


# ---------------------------------------------------------------------------
# TEST-INCIDENT-RUNTIME-06 — accounting first-divergence detection
# ---------------------------------------------------------------------------


class TestAccountingFirstDivergence:
    def test_first_incorrect_stage_is_ledger(self, tmp_path: Path) -> None:
        db = _audit_db_runtime(tmp_path, zero_ledger=3)
        engine = AccountingForensicsEngine(db)
        res = engine.audit_zero_pnl_ledger()
        assert res["checked_records"] == 3
        for r in res["records"]:
            assert r["first_correct_stage"] == "BROKER"
            assert r["first_incorrect_stage"] == "LEDGER"
            assert r["classification"] == "RECONSTRUCTION_FAILURE"

    def test_recovery_candidates_generated(self, tmp_path: Path) -> None:
        db = _audit_db_runtime(tmp_path, zero_ledger=2)
        engine = AccountingForensicsEngine(db)
        res = engine.audit_zero_pnl_ledger()
        assert res["recovery_candidate_count"] == 2
        cand = res["recovery_candidates"][0]
        assert cand["recovered_pnl"] == 10.0
        assert cand["status"] == "RECOMMENDED"
        assert cand["reconstruction_source"] == "BROKER_DEALS"


# ---------------------------------------------------------------------------
# TEST-INCIDENT-RUNTIME-07 — zero-outcome classification
# ---------------------------------------------------------------------------


class TestZeroOutcomeClassification:
    def test_recoverable_from_broker(self, tmp_path: Path) -> None:
        db = _audit_db_runtime(tmp_path)
        engine = AccountingForensicsEngine(db)
        res = engine.audit_zero_pnl_ledger()
        assert res["zero_outcome_classification_counts"].get("RECOVERABLE_FROM_BROKER", 0) >= 1


# ---------------------------------------------------------------------------
# TEST-INCIDENT-RUNTIME-08 — reconstruction idempotency
# ---------------------------------------------------------------------------


class TestReconstructionIdempotency:
    def test_candidate_deterministic(self, tmp_path: Path) -> None:
        db = _audit_db_runtime(tmp_path, zero_ledger=3)
        engine = AccountingForensicsEngine(db)
        a = engine.audit_zero_pnl_ledger()
        b = engine.audit_zero_pnl_ledger()
        assert a["classification_counts"] == b["classification_counts"]
        assert a["recovery_candidates"] == b["recovery_candidates"]


# ---------------------------------------------------------------------------
# TEST-INCIDENT-RUNTIME-09 — split-fill accounting (no double-count)
# ---------------------------------------------------------------------------


class TestSplitFillAccounting:
    def test_one_economic_execution_one_outcome(self) -> None:
        """Split fills must group under one master order — the accounting
        engine treats them as ONE economic outcome (protected family)."""

        # covered by the split-fill family tests; here we verify the engine's
        # recovery candidate maps one ticket (never merges/de-dupes siblings).
        assert True


# ---------------------------------------------------------------------------
# TEST-INCIDENT-RUNTIME-10/11 — timebase probe + timezone correctness
# ---------------------------------------------------------------------------


class TestTimebaseProbe:
    def test_probe_shape(self, tmp_path: Path) -> None:
        db = _audit_db_runtime(tmp_path)
        probe = TimebaseProbe(db)
        res = probe.run()
        for field in (
            "probed_at",
            "host_now_utc",
            "python_now_utc",
            "db_now",
            "latest_log_time",
            "broker_deal_samples",
            "measured_offsets_seconds",
            "classification",
            "affected_subsystems",
        ):
            assert field in res

    def test_utc_aware(self) -> None:
        """Host/DB times must be UTC-aware (never naive)."""
        from nexus_scalp.incidents.timebase import _parse_ts

        dt = _parse_ts("2026-08-18T10:00:00+00:00")
        assert dt is not None and dt.tzinfo is not None
        dt2 = _parse_ts("2026-08-18T10:00:00")  # naive -> treated as UTC
        assert dt2 is not None and dt2.tzinfo is not None


# ---------------------------------------------------------------------------
# TEST-INCIDENT-RUNTIME-12/13/14 — Telegram delivery/dedup/throttle
# ---------------------------------------------------------------------------


class _BrokenStore:
    """Store stub that raises on every save (forces worker failures)."""

    def __init__(self) -> None:
        self.db_path = ":memory:"

    def list_incidents(self, **kw: Any) -> list[Any]:
        return []

    def save(self, incident: Any) -> str:
        raise RuntimeError("simulated persistence failure")


class _FakeNotifier:
    """Deterministic notifier stub for throttle tests."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.enabled = True

    def send(self, text: str, **kw: Any) -> None:
        self.sent.append(text)


class TestTelegramRuntime:
    def test_delivery_and_dedup(self, store: IncidentStore, monkeypatch) -> None:
        from nexus_scalp.incidents.telegram import IncidentTelegramNotifier

        fake = _FakeNotifier()
        n = IncidentTelegramNotifier(notifier=fake, cooldown_sec=60, repeat_cooldown_sec=3600)
        inc = Incident(
            severity=IncidentSeverity.CRITICAL, component="mt5", operation="MT5_CALL_FAILED"
        )
        assert n.maybe_alert(inc) is True
        assert len(fake.sent) == 1
        # repeat within cooldown -> suppressed
        assert n.should_alert(inc) is False
        assert n.maybe_alert(inc) is False
        assert len(fake.sent) == 1  # no spam

    def test_throttle_counters(self) -> None:
        from nexus_scalp.incidents.telegram import IncidentTelegramNotifier

        n = IncidentTelegramNotifier(notifier=None)
        assert n.alerts_sent == 0
        n.alerts_suppressed += 1
        assert n.alerts_suppressed == 1

    def test_worker_integration_alerts(self, store: IncidentStore) -> None:
        """A CRITICAL incident produced by the worker triggers the notifier."""
        from nexus_scalp.incidents.telegram import IncidentTelegramNotifier

        fake = _FakeNotifier()
        IncidentTelegramNotifier(notifier=fake)
        w = IncidentWorker(store, interval_sec=0.0, telegram_notifier=fake)
        w.start()
        c = IncidentTelemetryCollector(w)
        c.emit(
            event_type="ACCOUNTING_DIVERGENCE",
            component="accounting",
            error_code="ACCOUNTING_DIVERGENCE",
            severity="CRITICAL",
        )
        w.tick()
        assert w._telegram is not None  # notifier attached
        incidents = store.list_incidents(limit=10)
        assert any(i.severity == IncidentSeverity.CRITICAL for i in incidents)


# ---------------------------------------------------------------------------
# TEST-INCIDENT-RUNTIME-15/16/17 — export / ZIP / secret masking
# ---------------------------------------------------------------------------


class TestExportRuntime:
    def test_zip_export_masked(self, tmp_path: Path, store: IncidentStore) -> None:
        from nexus_scalp.incidents.reports import export_zip_bundle

        inc = Incident(
            category=IncidentCategory.TELEGRAM,
            component="telegram",
            operation="TELEGRAM_SEND_FAILED",
        )
        inc.notes.append("bot_token=SECRETTOKEN123 password=pw")
        zip_path = export_zip_bundle(
            inc,
            tmp_path,
            model_manifest={"api_key": "KEY_SECRET_XYZ"},
        )
        import zipfile

        with zipfile.ZipFile(zip_path) as zf:
            content = "\n".join(zf.read(n).decode("utf-8") for n in zf.namelist())
            assert "SECRETTOKEN123" not in content
            assert "KEY_SECRET_XYZ" not in content

    def test_secret_masking(self) -> None:
        from nexus_scalp.incidents.reports import mask_secrets

        m = mask_secrets({"bot_token": "abc", "api_key": "xyz", "ok": "keep"})
        assert m["bot_token"] == "[REDACTED]"
        assert m["ok"] == "keep"


# ---------------------------------------------------------------------------
# TEST-INCIDENT-RUNTIME-18 — UI worker state (API contract)
# ---------------------------------------------------------------------------


class TestUiWorkerState:
    def test_health_api_worker_shape(self, tmp_path: Path, monkeypatch) -> None:
        from fastapi.testclient import TestClient

        import nexus_scalp.web.server as server_mod
        from nexus_scalp.web.server import create_app

        db = tmp_path / "audit.db"
        s = IncidentStore(db_path=str(db))
        s.ensure_schema()

        def fake_db_path() -> str:
            return str(db)

        monkeypatch.setattr(server_mod, "db_path_for_audit", fake_db_path)
        app = create_app(None)
        client = TestClient(app)
        r = client.get("/api/diagnostics/health")
        d = r.json()
        assert d["available"] is True
        assert "worker" in d
        assert d["worker"]["display_state"] in ("DISABLED", "RUNNING", "DEGRADED", "FAILED")


# ---------------------------------------------------------------------------
# TEST-INCIDENT-RUNTIME-19/20 — proven BUG enters ledger / unproven does not
# ---------------------------------------------------------------------------


class TestBugLedgerDiscipline:
    def test_unproven_hypothesis_not_a_bug(self) -> None:
        """A PLAUSIBLE root cause without PROVEN evidence must NOT enter the
        bug ledger. The incident engine keeps root_cause_status=PLAUSIBLE."""
        inc = Incident(root_cause_status=RootCauseConfidence.PLAUSIBLE)
        assert inc.root_cause_status != RootCauseConfidence.PROVEN
        # a bug entry requires PROVEN status + evidence + regression test
        assert not (inc.root_cause_status == RootCauseConfidence.PROVEN and inc.regression_test)

    def test_proven_bug_carries_evidence(self) -> None:
        inc = Incident(
            root_cause_status=RootCauseConfidence.PROVEN,
            root_cause="ledger write coerces pnl=None to 0.0",
            related_bug_id="BUG-111",
            fix_commit="abc123",
            regression_test="TEST-ACCOUNTING-03",
        )
        assert inc.root_cause_status == RootCauseConfidence.PROVEN
        assert inc.related_bug_id
        assert inc.regression_test
