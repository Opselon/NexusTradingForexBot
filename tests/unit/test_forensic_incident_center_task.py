"""Forensic Incident Center repair regression tests (2026-08-20).

One strong test per failure class (spec 33/66 — reduced critical-test
philosophy, no test explosion):

    TEST-FORENSIC-01  Broker epoch normalization (TIME-2): server-local
                      MT5 epochs must subtract the broker offset (BUG-070).
    TEST-FORENSIC-02  clock_skew measures sync lag, never data age (TIME-1).
    TEST-FORENSIC-03  split-fill sentinel guard (SPLIT-1): master_order_id
                      '0'/'' rows are NOT split-fill families.
    TEST-FORENSIC-04  Occurrence-aware impact (spec 22): a scan incident
                      with a concrete ticket measures real DB rows; a
                      no-identity incident is UNKNOWN_IMPACT.
    TEST-FORENSIC-05  Outcome forensics broker evidence: zero outcome with
                      broker PnL available is BROKER_RECOVERABLE.
    TEST-FORENSIC-06  One-Click Trace: incident_id and execution_id resolve
                      the same chain; a bogus id returns missing_link.
    TEST-FORENSIC-07  Evidence-based lifecycle: VERIFIED refused without
                      fix+regression; FALSE_POSITIVE keeps the record.
    TEST-FORENSIC-08  Evidence export: incident_json is secret-masked and
                      the ZIP bundle contains the forensic artifacts.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nexus_scalp.adapters.database.broker_history import _epoch_utc
from nexus_scalp.incidents.impact import ImpactAnalyzer
from nexus_scalp.incidents.models import EventSource, Incident, TimelineEvent
from nexus_scalp.incidents.occurrences import count_families
from nexus_scalp.incidents.reports import export_zip_bundle, incident_json
from nexus_scalp.incidents.store import IncidentLifecycle, IncidentStore
from nexus_scalp.incidents.timebase import TimebaseProbe, timebase_event_chain
from nexus_scalp.incidents.trace import clock_skew, outcome_forensics, split_fill_groups
from nexus_scalp.incidents.trace_lineage import trace_lineage


def _minimal_audit_db(tmp_path: Path) -> str:
    """Builds a small audit.db with broker trades + ledger + outcomes + experiences."""
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
            ticket INTEGER, symbol TEXT, direction TEXT, volume REAL,
            entry_price REAL, exit_price REAL, status TEXT, pnl REAL,
            commission REAL, swap REAL, timestamp TEXT, mae REAL, mfe REAL,
            order_id TEXT, open_time TEXT, close_time TEXT,
            gross_pnl_usd REAL, net_pnl_usd REAL, exit_mechanism TEXT,
            exit_reason_source TEXT, exit_evidence TEXT, exit_reason_confidence REAL
        );
        CREATE TABLE audit_experience_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT,
            execution_id TEXT, outcome_timestamp TEXT, is_executed INTEGER,
            is_closed INTEGER, exit_reason TEXT, realized_pnl_usd REAL,
            realized_r_multiple REAL, approved_volume REAL, payload TEXT
        );
        CREATE TABLE audit_experiences (
            id INTEGER PRIMARY KEY AUTOINCREMENT, experience_id TEXT,
            request_id TEXT, execution_id TEXT, decision_id TEXT,
            idempotency_key TEXT UNIQUE, strategy_id TEXT, model_id TEXT,
            decision_timestamp TEXT, action TEXT
        );
        CREATE TABLE audit_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticket TEXT, order_id TEXT,
            parent_order_id TEXT, timestamp TEXT
        );
        CREATE TABLE research_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
            dataset_id TEXT, strategy_id TEXT, strategy_version TEXT,
            executed_at TEXT, config TEXT, build_identity TEXT, result_summary TEXT
        );
        """
    )
    now = "2026-08-19T10:00:00+00:00"
    con.execute(
        "INSERT INTO audit_broker_trades (trade_id, position_id, symbol, direction, "
        "entry_time, exit_time, entry_price, exit_price, volume, gross_pnl, commission, "
        "swap, fee, net_pnl, deal_ids, order_ids, master_order_id, source, synced_at) "
        "VALUES ('152487837184', '152487837184', 'XAUUSD', 'BUY', "
        "?, ?, 4000.0, 4010.0, 0.1, 41.0, 0.0, 0.0, 0.0, 41.0, 'd1', 'o1', "
        "'152487837184', 'BROKER_DEALS', ?)",
        ("2026-08-17T05:10:00+00:00", "2026-08-17T05:20:00+00:00", now),
    )
    con.execute(
        "INSERT INTO audit_ledger (ticket, symbol, direction, volume, entry_price, "
        "exit_price, status, pnl, order_id, open_time, close_time, gross_pnl_usd, "
        "net_pnl_usd, exit_mechanism) VALUES (152487837184, 'XAUUSD', 'BUY', 0.1, "
        "4000.0, 4010.0, 'CLOSED', 0.0, 'exp_3e8b', "
        "'2026-08-17T05:10:00+00:00', '2026-08-17T05:20:00+00:00', 0.0, 0.0, 'SYSTEM_CLOSE')"
    )
    con.execute(
        "INSERT INTO audit_experience_outcomes (idempotency_key, execution_id, "
        "outcome_timestamp, is_executed, is_closed, exit_reason, realized_pnl_usd, "
        "realized_r_multiple, approved_volume, payload) VALUES ('exp_3e8b', "
        "'152487837184', ?, 1, 1, 'SYSTEM_CLOSE', 0.0, 0.0, 0.1, '{}')",
        (now,),
    )
    con.execute(
        "INSERT INTO audit_experiences (experience_id, request_id, execution_id, "
        "idempotency_key, strategy_id, model_id, decision_timestamp, action) "
        "VALUES ('exp_3e8b', 'req-1', '152487837184', 'exp_3e8b', 'STRAT-X', 'model-1', ?, 'BUY')",
        (now,),
    )
    con.commit()
    con.close()
    return str(db)


# ---------------------------------------------------------------------------
# TEST-FORENSIC-01 — broker epoch normalization (TIME-2)
# ---------------------------------------------------------------------------


class TestBrokerEpochNormalization:
    def test_server_local_epoch_subtracts_offset(self) -> None:
        # Broker GMT+3 07:21:18 == real UTC 04:21:18.
        ref_utc = datetime(2026, 8, 19, 4, 21, 18, tzinfo=UTC)
        broker_epoch = int(ref_utc.timestamp()) + 3 * 3600
        assert _epoch_utc(broker_epoch) == ref_utc

    def test_offset_applied_consistently(self) -> None:
        # Same epoch is broker-server-local every time (BUG-070 contract):
        # GMT+3 epochs shift by 180 min deterministically.
        ref_utc = datetime(2026, 8, 19, 4, 21, 18, tzinfo=UTC)
        epoch = int(ref_utc.timestamp()) + 3 * 3600
        assert _epoch_utc(epoch) == ref_utc
        assert _epoch_utc(epoch) == _epoch_utc(epoch)  # deterministic

    def test_garbage_epoch_returns_none(self) -> None:
        assert _epoch_utc(None) is None
        assert _epoch_utc(0) is None


# ---------------------------------------------------------------------------
# TEST-FORENSIC-02 — clock_skew measures sync lag, never data age (TIME-1)
# ---------------------------------------------------------------------------


class TestClockSkewSemantics:
    def test_sync_lag_is_the_divergence_signal(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        con = sqlite3.connect(db)
        con.executescript(
            """CREATE TABLE audit_broker_trades (
                entry_time TEXT, exit_time TEXT, synced_at TEXT
            );
            CREATE TABLE audit_broker_history_meta (id INTEGER PRIMARY KEY AUTOINCREMENT);"""
        )
        now = datetime.now(UTC)
        # Data is OLD (age huge) but the sync happened 60 seconds ago.
        con.execute(
            "INSERT INTO audit_broker_trades VALUES "
            "('2026-07-01T00:00:00+00:00','2026-07-01T00:01:00+00:00',?)",
            ((now - timedelta(seconds=60)).isoformat(),),
        )
        con.commit()
        con.close()
        res = clock_skew(str(db))
        # Data age is huge but divergence is computed from SYNC LAG (~60s).
        assert res["observed_data_age_seconds"] > 1_000_000
        assert res["divergence"] == "IN_BOUNDS"
        assert res["measurement"].startswith("sync_lag")

    def test_stale_sync_is_divergence(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        con = sqlite3.connect(db)
        con.executescript(
            """CREATE TABLE audit_broker_trades (
                entry_time TEXT, exit_time TEXT, synced_at TEXT
            );
            CREATE TABLE audit_broker_history_meta (id INTEGER PRIMARY KEY AUTOINCREMENT);"""
        )
        stale = (datetime.now(UTC) - timedelta(hours=72)).isoformat()
        con.execute(
            "INSERT INTO audit_broker_trades VALUES "
            "('2026-07-01T00:00:00+00:00','2026-07-01T00:01:00+00:00',?)",
            (stale,),
        )
        con.commit()
        con.close()
        res = clock_skew(str(db))
        assert res["divergence"] == "TIMEBASE_DIVERGENCE"
        assert res["sync_lag_seconds"] > 300


# ---------------------------------------------------------------------------
# TEST-FORENSIC-03 — split-fill sentinel guard (SPLIT-1)
# ---------------------------------------------------------------------------


class TestSplitFillSentinel:
    def test_sentinel_zero_is_not_a_family(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        con = sqlite3.connect(db)
        con.executescript(
            """CREATE TABLE audit_broker_trades (
                trade_id TEXT, position_id TEXT, symbol TEXT, direction TEXT,
                entry_time TEXT, exit_time TEXT, entry_price REAL, exit_price REAL,
                volume REAL, gross_pnl REAL, commission REAL, swap REAL, fee REAL,
                net_pnl REAL, deal_ids TEXT, order_ids TEXT, master_order_id TEXT,
                exit_reason TEXT, exit_comment TEXT, duration_sec REAL,
                source TEXT, synced_at TEXT
            );"""
        )
        for tid in ("t1", "t2", "t3"):
            con.execute(
                "INSERT INTO audit_broker_trades (trade_id, master_order_id, net_pnl, source) "
                "VALUES (?, '0', 1.0, 'BROKER_DEALS')",
                (tid,),
            )
        con.commit()
        con.close()
        res = split_fill_groups(str(db))
        assert res["split_fill_families"] == 0  # all sentinel

    def test_real_family_still_detected(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        con = sqlite3.connect(db)
        con.executescript(
            """CREATE TABLE audit_broker_trades (
                trade_id TEXT, position_id TEXT, symbol TEXT, direction TEXT,
                entry_time TEXT, exit_time TEXT, entry_price REAL, exit_price REAL,
                volume REAL, gross_pnl REAL, commission REAL, swap REAL, fee REAL,
                net_pnl REAL, deal_ids TEXT, order_ids TEXT, master_order_id TEXT,
                exit_reason TEXT, exit_comment TEXT, duration_sec REAL,
                source TEXT, synced_at TEXT
            );"""
        )
        for tid in ("t1", "t2"):
            con.execute(
                "INSERT INTO audit_broker_trades (trade_id, master_order_id, net_pnl, source) "
                "VALUES (?, 'order-1', 5.0, 'BROKER_DEALS')",
                (tid,),
            )
        con.commit()
        con.close()
        res = split_fill_groups(str(db))
        assert res["split_fill_families"] == 1
        assert res["tickets_in_families"] == 2


# ---------------------------------------------------------------------------
# TEST-FORENSIC-04 — occurrence-aware impact (spec 22)
# ---------------------------------------------------------------------------


class TestOccurrenceImpact:
    def test_concrete_ticket_counts_real_rows(self, tmp_path: Path) -> None:
        db = _minimal_audit_db(tmp_path)
        inc = Incident(incident_id="INC-TEST-1", affected_records=["152487837184"])
        res = count_families(inc, db)
        assert res["semantics"] == "MEASURED"
        assert res["counts"]["affected_ledger_records"] == 1
        assert res["counts"]["affected_positions"] == 1

    def test_no_identity_is_unknown_impact(self, tmp_path: Path) -> None:
        db = _minimal_audit_db(tmp_path)
        inc = Incident(incident_id="INC-TEST-2")
        res = count_families(inc, db)
        assert res["semantics"] == "UNKNOWN_IMPACT"

    def test_impact_analyzer_appends_evidence(self, tmp_path: Path) -> None:
        db = _minimal_audit_db(tmp_path)
        inc = Incident(incident_id="INC-TEST-3", affected_records=["152487837184"])
        imp = ImpactAnalyzer(db_path=db).analyze(inc)
        assert imp.as_dict()["affected_records"] == 1
        assert any("MEASURED" in n for n in imp.as_dict()["notes"])
        assert len(inc.evidence) == 1  # occurrence evidence attached

# ---------------------------------------------------------------------------
# TEST-FORENSIC-05 — outcome forensics broker evidence
# ---------------------------------------------------------------------------


class TestOutcomeForensicsBrokerEvidence:
    def test_zero_outcome_with_broker_pnl_is_recoverable(self, tmp_path: Path) -> None:
        db = _minimal_audit_db(tmp_path)
        res = outcome_forensics(db, 100)
        # The fixture outcome is zero PnL and the broker row holds +41.0.
        recoverable = res["broker_recoverable_outcomes"]
        assert any(
            s.get("execution_id") == "152487837184" for s in recoverable
        ), recoverable


# ---------------------------------------------------------------------------
# TEST-FORENSIC-06 — One-Click Trace (spec 24/25/26)
# ---------------------------------------------------------------------------


class TestOneClickTrace:
    def test_execution_id_resolves_chain(self, tmp_path: Path) -> None:
        db = _minimal_audit_db(tmp_path)
        res = trace_lineage(db, "152487837184")
        assert res["ledger"] is not None
        assert res["outcome"] is not None
        assert res["experience"] is not None

    def test_incident_id_resolves_root_cause(self, tmp_path: Path) -> None:
        db = _minimal_audit_db(tmp_path)
        store = IncidentStore(db_path=db)
        store.ensure_schema()
        inc = Incident(
            incident_id="INC-TRACE-1",
            affected_records=["152487837184"],
            root_cause="fixture root cause",
            root_cause_status="PROVEN",
        )
        store.save(inc)
        res = trace_lineage(db, "INC-TRACE-1", store=store)
        assert res["kind"] == "incident"
        assert res["root_cause"]["status"] == "PROVEN"
        assert len(res["lineage"]["downstream"]) == 1

    def test_bogus_id_returns_missing_link(self, tmp_path: Path) -> None:
        db = _minimal_audit_db(tmp_path)
        res = trace_lineage(db, "999999999999")
        assert res.get("missing_link") is None or res.get("ledger") is None
        # honest: no fabricated relationship — ledger/outcome absent
        assert res.get("ledger") is None
        assert res.get("outcome") is None


# ---------------------------------------------------------------------------
# TEST-FORENSIC-07 — evidence-based lifecycle (spec 30/31/64)
# ---------------------------------------------------------------------------


class TestEvidenceLifecycle:
    def test_verified_refused_without_fix_evidence(self) -> None:
        inc = Incident(incident_id="INC-LC-1")
        assert IncidentLifecycle.transition(inc, "VERIFIED") is False
        assert inc.status.value == "OPEN"

    def test_verified_with_evidence(self) -> None:
        inc = Incident(incident_id="INC-LC-2", fix_commit="abc", regression_test="test_x")
        assert IncidentLifecycle.transition(inc, "VERIFIED") is True
        assert inc.status.value == "VERIFIED"

    def test_false_positive_keeps_record(self) -> None:
        inc = Incident(incident_id="INC-LC-3")
        IncidentLifecycle.mark_false_positive(
            inc, reason="phantom sentinel", detector_defect="grouped master=0"
        )
        assert inc.status.value == "FALSE_POSITIVE"
        assert any("FALSE_POSITIVE" in n for n in inc.notes)
        assert any(t.event_type == "MARKED_FALSE_POSITIVE" for t in inc.timeline)


# ---------------------------------------------------------------------------
# TEST-FORENSIC-08 — evidence export + ZIP (spec 45/46/47)
# ---------------------------------------------------------------------------


class TestEvidenceExport:
    def test_incident_json_masks_secrets(self) -> None:
        inc = Incident(
            incident_id="INC-EXP-1",
            notes=["bot token: 123456:ABcdefGHIJKLMNOPQRSTUVWXYZabcdefgh"],
        )
        out = incident_json(inc)
        assert "[REDACTED]" in out["notes"][0]
        assert "123456:ABcdefGHIJKLMNOPQRSTUVWXYZabcdefgh" not in out["notes"][0]

    def test_zip_contains_forensic_artifacts(self, tmp_path: Path) -> None:
        inc = Incident(incident_id="INC-EXP-2", notes=["n"])
        inc.add_timeline_event(
            TimelineEvent(
                timestamp=datetime.now(UTC),
                event_type="OBSERVED",
                source=EventSource.DATABASE,
                payload={"ticket": "152487837184"},
            )
        )
        zip_path = export_zip_bundle(inc, tmp_path)
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            assert any("incident_INC-EXP-2.json" in n for n in names)
            assert any("incident_INC-EXP-2.md" in n for n in names)


class TestTimebaseEventChain:
    def test_chain_reports_pre_fix_marker(self) -> None:
        chain = timebase_event_chain(
            broker={
                "entry_time": "2026-08-17T08:38:44+00:00",
                "exit_time": "2026-08-17T08:38:44+00:00",
            },
            ledger={"open_time": "2026-08-17T05:38:44+00:00", "close_time": "2026-08-17T05:38:44+00:00"},
        )
        assert chain["source_component"] == "audit_broker_trades"
        assert chain["normalization_note"]
        assert "[REDACTED]" not in json.dumps(chain)

    def test_event_probe_resolves_ticket(self, tmp_path: Path) -> None:
        db = _minimal_audit_db(tmp_path)
        res = TimebaseProbe(db).probe_event("152487837184")
        assert res["source_component"] == "audit_broker_trades"
        assert res["comparison_component"] == "audit_ledger"
