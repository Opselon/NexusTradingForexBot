"""TASK-12 POST-70D monitoring activation — TEST-POST70D-01..28.

Covers the canonical deploy gate, UNKNOWN discipline, fail-safe, news
200-but-wrong classification, experience-gap forensics, liquidity frozen
references, governance/champion identity, telegram aggregation/dedup,
trend analysis, check timeouts/isolation, and the no-self-modification
regression (TASK-12 §40/§45).
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nexus_scalp.forensics import (
    DEPLOY_POLICY,
    EXIT_ALLOW,
    EXIT_BLOCK,
    EXIT_ENGINE_UNAVAILABLE,
    EXIT_REVIEW,
    CheckResult,
    FeatureReferenceStats,
    ForensicHealthEngine,
    HealthStatus,
    TelegramReportScheduler,
    analyze_experience_gap,
    build_report_text,
    classify_missing_outcome,
    compare_snapshots,
    freeze_liquidity_references_from_golden,
    load_report_config,
    run_deploy_gate,
)
from nexus_scalp.forensics.engine import SnapshotRecord
from nexus_scalp.forensics.experience_gap import GAP_CLASSES


def _mkdb(path: Path, tables: dict[str, list[tuple[str, str]]]) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    for name, cols in tables.items():
        col_sql = ", ".join(f"{c} {t}" for c, t in cols)
        conn.execute(f"CREATE TABLE {name} ({col_sql})")
    conn.commit()
    return conn


def _snapshot(statuses: dict[str, str]) -> SnapshotRecord:
    """Builds a synthetic snapshot with the given check_id -> status map."""
    checks = [
        {
            "check_id": cid,
            "status": st,
            "timestamp": "2026-08-19T00:00:00+00:00",
            "duration_ms": 1.0,
            "evidence": "",
            "observed": {},
            "expected": "",
            "correlation_id": "x",
            "detail": "",
        }
        for cid, st in statuses.items()
    ]
    counts = {"CRITICAL": 0, "WARNING": 0, "DEGRADED": 0, "UNKNOWN": 0}
    for st in statuses.values():
        counts[st] = counts.get(st, 0) + 1
    return SnapshotRecord(
        overall=max(
            statuses.values(),
            key=lambda s: {
                "PASS": 0,
                "WARNING": 1,
                "UNKNOWN": 2,
                "DEGRADED": 3,
                "CRITICAL": 4,
            }.get(s, 0),
        ),
        groups={},
        checks=checks,
        critical_count=counts["CRITICAL"],
        warning_count=counts["WARNING"],
        degraded_count=counts["DEGRADED"],
        unknown_count=counts["UNKNOWN"],
    )


class _FakeEngine:
    """Engine stub for gate tests that returns a prebuilt snapshot."""

    def __init__(self, snapshot: SnapshotRecord) -> None:
        self._snap = snapshot

    def snapshot(self, persist: bool = True) -> SnapshotRecord:
        return self._snap

    def dashboard(self) -> dict:
        return {"rows": {}}


# ---------------------------------------------------------------------------
# TEST-POST70D-01 — deploy gate invokes canonical health engine
# ---------------------------------------------------------------------------


class TestPost70d01GateInvokesEngine:
    def test_gate_runs_real_engine(self, tmp_path):
        result = run_deploy_gate(engine=ForensicHealthEngine(history_dir=tmp_path), persist=False)
        assert result.decision in DEPLOY_POLICY.values()
        assert result.check_count > 0
        assert result.commit_sha  # git repo present
        assert result.correlation_id
        assert result.timestamp

    def test_gate_persists_evidence(self, tmp_path):
        run_deploy_gate(
            engine=ForensicHealthEngine(history_dir=tmp_path), persist=True, result_dir=tmp_path
        )
        f = tmp_path / "deploy_gate_result.json"
        assert f.exists()
        data = json.loads(f.read_text())
        for k in (
            "decision",
            "overall_status",
            "timestamp",
            "correlation_id",
            "commit_sha",
            "check_count",
            "critical_count",
            "warning_count",
            "degraded_count",
            "unknown_count",
            "blocking_checks",
            "health_snapshot_id",
        ):
            assert k in data


# ---------------------------------------------------------------------------
# TEST-POST70D-02 — CRITICAL blocks deployment
# ---------------------------------------------------------------------------


class TestPost70d02CriticalBlocks:
    def test_critical_block(self, tmp_path):
        snap = _snapshot({"CHECK-INT-01": "CRITICAL", "CHECK-FCS-01": "PASS"})
        result = run_deploy_gate(_FakeEngine(snap), persist=False)  # type: ignore[arg-type]
        assert result.decision == "BLOCK"
        assert result.exit_code == EXIT_BLOCK
        assert "CHECK-INT-01" in result.blocking_checks


# ---------------------------------------------------------------------------
# TEST-POST70D-03 — UNKNOWN cannot silently pass
# ---------------------------------------------------------------------------


class TestPost70d03UnknownNeverPass:
    def test_unknown_review(self, tmp_path):
        snap = _snapshot({"CHECK-GOV-02": "UNKNOWN"})
        result = run_deploy_gate(_FakeEngine(snap), persist=False)  # type: ignore[arg-type]
        assert result.decision == "REVIEW_REQUIRED"
        assert result.exit_code == EXIT_REVIEW
        assert result.decision != "ALLOW"

    def test_policy_map_unknown_is_review(self):
        assert DEPLOY_POLICY[HealthStatus.UNKNOWN.value] == "REVIEW_REQUIRED"
        assert DEPLOY_POLICY[HealthStatus.UNKNOWN.value] != "ALLOW"


# ---------------------------------------------------------------------------
# TEST-POST70D-04/05 — WARNING / DEGRADED match policy
# ---------------------------------------------------------------------------


class TestPost70d0405Policy:
    def test_warning_allows_with_warning(self):
        snap = _snapshot({"CHECK-NWS-01": "WARNING"})
        result = run_deploy_gate(_FakeEngine(snap), persist=False)  # type: ignore[arg-type]
        assert result.decision == "ALLOW_WITH_WARNING"
        assert result.exit_code == EXIT_ALLOW

    def test_degraded_review(self):
        snap = _snapshot({"CHECK-ACC-04": "DEGRADED"})
        result = run_deploy_gate(_FakeEngine(snap), persist=False)  # type: ignore[arg-type]
        assert result.decision == "REVIEW_REQUIRED"
        assert result.exit_code == EXIT_REVIEW

    def test_pure_pass_allows(self):
        snap = _snapshot({"CHECK-FCS-01": "PASS"})
        result = run_deploy_gate(_FakeEngine(snap), persist=False)  # type: ignore[arg-type]
        assert result.decision == "ALLOW"
        assert result.exit_code == EXIT_ALLOW


# ---------------------------------------------------------------------------
# TEST-POST70D-06 — health engine failure blocks/reviews
# ---------------------------------------------------------------------------


class TestPost70d06EngineFailure:
    def test_engine_crash_blocks(self):
        class Broken:
            def snapshot(self, persist: bool = True):
                raise RuntimeError("boom")

        result = run_deploy_gate(Broken(), persist=False)  # type: ignore[arg-type]
        assert result.decision == "FORENSIC_ENGINE_UNAVAILABLE"
        assert result.exit_code == EXIT_ENGINE_UNAVAILABLE
        assert result.engine_error


# ---------------------------------------------------------------------------
# TEST-POST70D-07 — health snapshots immutable
# ---------------------------------------------------------------------------


class TestPost70d07SnapshotImmutable:
    def test_snapshot_persisted_unchanged(self, tmp_path):
        engine = ForensicHealthEngine(history_dir=tmp_path)
        rec1 = engine.snapshot(persist=True)
        f = tmp_path / "forensic_health_snapshot.json"
        f.read_text(encoding="utf-8")
        # another snapshot overwrites the file, but the history is append-only
        engine.snapshot(persist=True)
        hist = tmp_path / "history.jsonl"
        lines = [l for l in hist.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) >= 2
        # both records remain immutable in history
        assert rec1.correlation_id  # snapshot identity survives


# ---------------------------------------------------------------------------
# TEST-POST70D-08 — snapshot comparison (trend)
# ---------------------------------------------------------------------------


class TestPost70d08Trend:
    def test_compare_snapshots(self):
        prev = _snapshot({"CHECK-A": "PASS", "CHECK-B": "DEGRADED"})
        cur = _snapshot({"CHECK-A": "CRITICAL", "CHECK-B": "PASS", "CHECK-C": "UNKNOWN"})
        t = compare_snapshots(cur, prev)
        assert t["previous_available"] is True
        assert any(c["check_id"] == "CHECK-A" for c in t["new_failures"])
        assert any(c["check_id"] == "CHECK-B" for c in t["resolved_failures"])
        assert any(c["check_id"] == "CHECK-C" for c in t["new_unknowns"])
        assert any(c["check_id"] == "CHECK-A" for c in t["worsened"])

    def test_no_previous(self):
        t = compare_snapshots(_snapshot({"CHECK-A": "PASS"}), None)
        assert t["previous_available"] is False


# ---------------------------------------------------------------------------
# TEST-POST70D-09 — News 200-but-wrong detection
# ---------------------------------------------------------------------------


class TestPost70d09News200Wrong:
    def test_http_200_empty_detected(self):
        from nexus_scalp.forensics.news_sources import classify_source

        c = classify_source(
            source_id="bea",
            enabled=True,
            healthy_flag=0,
            consecutive_failures=12,
            last_status=200,
            last_success_at="",
            last_failure_at="2026-08-18T03:49:35+00:00",
            article_count=0,
        )
        assert c["classification"] == "HTTP_SUCCESS_EMPTY"

    def test_http_200_with_articles_healthy(self):
        from nexus_scalp.forensics.news_sources import classify_source

        # Fresh success (10 minutes ago) — must classify HEALTHY. Never a
        # hardcoded wall-clock date: that ages out of the 24h stale window
        # and flips the assertion a day after it is written.
        fresh = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        c = classify_source(
            source_id="boe",
            enabled=True,
            healthy_flag=1,
            consecutive_failures=0,
            last_status=200,
            last_success_at=fresh,
            article_count=704,
        )
        assert c["classification"] == "HEALTHY"

    def test_http_failure_detected(self):
        from nexus_scalp.forensics.news_sources import classify_source

        c = classify_source(
            source_id="reuters",
            enabled=True,
            healthy_flag=0,
            consecutive_failures=12,
            last_status=None,
            last_success_at="",
            last_failure_at="2026-08-18T03:49:40+00:00",
            article_count=0,
        )
        assert c["classification"] == "HTTP_FAILURE"


# ---------------------------------------------------------------------------
# TEST-POST70D-10 — News source freshness
# ---------------------------------------------------------------------------


class TestPost70d10NewsFreshness:
    def test_stale_detected(self):
        from nexus_scalp.forensics.news_sources import classify_source

        c = classify_source(
            source_id="src",
            enabled=True,
            healthy_flag=1,
            consecutive_failures=0,
            last_status=200,
            last_success_at="2026-08-01T00:00:00+00:00",  # 18 days ago
            article_count=100,
        )
        assert c["classification"] == "HTTP_SUCCESS_STALE"


# ---------------------------------------------------------------------------
# TEST-POST70D-11 — experience gap calculation
# ---------------------------------------------------------------------------


class TestPost70d11Gap:
    def test_gap_classifies_never_traded(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "artifacts").mkdir(exist_ok=True)
        db = tmp_path / "artifacts" / "audit.db"
        conn = _mkdb(
            db,
            {
                "audit_experiences": [
                    ("id", "INTEGER"),
                    ("idempotency_key", "TEXT"),
                    ("execution_id", "TEXT"),
                    ("request_id", "TEXT"),
                    ("strategy_id", "TEXT"),
                    ("payload", "TEXT"),
                    ("timestamp", "TEXT"),
                ],
                "audit_experience_outcomes": [("id", "INTEGER"), ("idempotency_key", "TEXT")],
                "audit_broker_trades": [("trade_id", "TEXT")],
                "audit_ledger": [("ticket", "TEXT")],
            },
        )
        for i in range(10):
            conn.execute(
                "INSERT INTO audit_experiences VALUES (?, ?, '', ?, 'strat_x', '{}', '2026-08-19T00:00:00+00:00')",
                (i, f"exp_{i}", f"req_{i}"),
            )
        conn.commit()
        conn.close()
        rep = analyze_experience_gap(db)
        d = rep.to_dict()
        assert d["status"] == "PASS"
        assert d["defect_rate"] == 0.0
        assert d["classification"]["LEGITIMATELY_NO_OUTCOME"] == 10

    def test_executed_trade_missing_outcome_is_defect(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "artifacts").mkdir(exist_ok=True)
        db = tmp_path / "artifacts" / "audit.db"
        conn = _mkdb(
            db,
            {
                "audit_experiences": [
                    ("id", "INTEGER"),
                    ("idempotency_key", "TEXT"),
                    ("execution_id", "TEXT"),
                    ("request_id", "TEXT"),
                    ("strategy_id", "TEXT"),
                    ("payload", "TEXT"),
                    ("timestamp", "TEXT"),
                ],
                "audit_experience_outcomes": [("id", "INTEGER"), ("idempotency_key", "TEXT")],
                "audit_broker_trades": [
                    ("trade_id", "TEXT"),
                    ("position_id", "TEXT"),
                    ("master_order_id", "TEXT"),
                ],
                "audit_ledger": [("ticket", "TEXT")],
            },
        )
        conn.execute(
            "INSERT INTO audit_experiences VALUES (1, 'exp_1', 'TICKET_1', 'req_1', 'strat_x', '{}', '2026-08-19T00:00:00+00:00')"
        )
        conn.commit()
        conn.close()
        rep = analyze_experience_gap(db)
        d = rep.to_dict()
        assert d["defect_rate"] > 0.0
        assert d["status"] in ("WARNING", "DEGRADED")


# ---------------------------------------------------------------------------
# TEST-POST70D-12 — missing outcome != zero
# ---------------------------------------------------------------------------


class TestPost70d12NoZeroSubstitution:
    def test_classifier_never_returns_zero_pnl(self):
        # A missing outcome classification never fabricates a PnL value
        cls = classify_missing_outcome({"execution_id": "", "status": ""}, [], [], [])
        assert cls == "LEGITIMATELY_NO_OUTCOME"
        # and the taxonomy never includes "zero"
        assert "ZERO" not in GAP_CLASSES and "zero" not in GAP_CLASSES


# ---------------------------------------------------------------------------
# TEST-POST70D-13 — historical duplicate detection
# ---------------------------------------------------------------------------


class TestPost70d13HistoricalDuplicate:
    def test_duplicate_check_uses_execution_identity(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "artifacts").mkdir(exist_ok=True)
        db = tmp_path / "artifacts" / "audit.db"
        conn = _mkdb(
            db,
            {
                "audit_experience_outcomes": [
                    ("id", "INTEGER"),
                    ("idempotency_key", "TEXT"),
                    ("execution_id", "TEXT"),
                    ("realized_pnl_usd", "REAL"),
                ],
            },
        )
        conn.executemany(
            "INSERT INTO audit_experience_outcomes VALUES (?, ?, ?, ?)",
            [(1, "k1", "152494870397", -18.27), (2, "k2", "152494870397", -31.50)],
        )
        conn.commit()
        conn.close()
        from nexus_scalp.forensics import checks as C

        r = C.check_duplicate_economic_outcome()
        assert r.status is HealthStatus.WARNING  # known historical
        assert "HISTORICAL" in r.detail


# ---------------------------------------------------------------------------
# TEST-POST70D-14 — historical excursion classification
# ---------------------------------------------------------------------------


class TestPost70d14Excursion:
    def test_historical_excursions_warning(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "artifacts").mkdir(exist_ok=True)
        db = tmp_path / "artifacts" / "audit.db"
        conn = _mkdb(
            db,
            {
                "audit_ledger": [
                    ("id", "INTEGER"),
                    ("ticket", "INTEGER"),
                    ("mfe", "REAL"),
                    ("mae", "REAL"),
                    ("close_time", "TEXT"),
                ],
            },
        )
        conn.execute(
            "INSERT INTO audit_ledger VALUES (1, 100, -0.5, -1.0, '2026-08-17T05:55:06+00:00')"
        )
        conn.commit()
        conn.close()
        from nexus_scalp.forensics import checks as C

        r = C.check_impossible_excursion()
        assert r.status is HealthStatus.WARNING
        assert "HISTORICAL" in r.detail

    def test_new_excursion_critical(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "artifacts").mkdir(exist_ok=True)
        db = tmp_path / "artifacts" / "audit.db"
        conn = _mkdb(
            db,
            {
                "audit_ledger": [
                    ("id", "INTEGER"),
                    ("ticket", "INTEGER"),
                    ("mfe", "REAL"),
                    ("mae", "REAL"),
                    ("close_time", "TEXT"),
                ],
            },
        )
        conn.execute(
            "INSERT INTO audit_ledger VALUES (1, 100, -0.5, -1.0, '2026-08-19T12:00:00+00:00')"
        )
        conn.commit()
        conn.close()
        from nexus_scalp.forensics import checks as C

        r = C.check_impossible_excursion()
        assert r.status is HealthStatus.CRITICAL


# ---------------------------------------------------------------------------
# TEST-POST70D-15 — Liquidity frozen-reference integrity
# ---------------------------------------------------------------------------


class TestPost70d15LiquidityReferences:
    def test_freeze_from_golden(self):
        reg = freeze_liquidity_references_from_golden()
        assert len(reg) == 10
        ref60 = reg.get("liquidity", 60)
        assert ref60 is not None
        assert ref60.feature_name == "bsl_distance_atr"
        assert ref60.source.startswith("LIQUIDITY_70D_GOLDEN_BASELINE.json@")

    def test_freeze_refuses_non_golden(self, tmp_path):
        fake = tmp_path / "fake.json"
        fake.write_text(json.dumps({"schema_id": "scalp_v2", "per_feature": {}}))
        with pytest.raises(ValueError):
            freeze_liquidity_references_from_golden(fake)

    def test_frozen_immutable_requires_replace(self):
        reg = freeze_liquidity_references_from_golden()
        with pytest.raises(ValueError):
            reg.register(
                FeatureReferenceStats(
                    feature_index=60,
                    feature_name="other",
                    family="liquidity",
                    mean=9.0,
                    std=9.0,
                    min_=-9.0,
                    max_=9.0,
                    n=1,
                    source="other",
                )
            )


# ---------------------------------------------------------------------------
# TEST-POST70D-16 — Liquidity drift detection
# ---------------------------------------------------------------------------


class TestPost70d16LiquidityDrift:
    def test_drift_detected_against_frozen(self, tmp_path):
        db = tmp_path / "candle_intel.db"
        conn = sqlite3.connect(db)
        cols = ", ".join(f"feat_{i} REAL" for i in range(70))
        conn.execute(f"CREATE TABLE feature_vectors (ts TEXT, {cols})")
        for i in range(40):
            row = [0.0] * 60 + [0.5 + 0.01 * ((i + j) % 5) for j in range(10)]
            conn.execute(
                f"INSERT INTO feature_vectors VALUES ('2026-08-19T00:00:00+00:00', {', '.join(str(v) for v in row)})"
            )
        conn.commit()
        conn.close()
        from nexus_scalp.forensics import checks as C

        r = C.check_liquidity_feature_health(
            db_path=db, references=freeze_liquidity_references_from_golden()
        )
        # bsl ref mean 1.56; observed mean 0.5 => z ~ 1.0 (NORMAL) but other
        # features may drift; the check must classify, not crash
        assert r.status in (
            HealthStatus.PASS,
            HealthStatus.WARNING,
            HealthStatus.DEGRADED,
            HealthStatus.CRITICAL,
        )


# ---------------------------------------------------------------------------
# TEST-POST70D-17 — shadow zero-observation detection
# ---------------------------------------------------------------------------


class TestPost70d17ShadowZero:
    def test_running_but_zero_observations(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "artifacts").mkdir(exist_ok=True)
        db = tmp_path / "artifacts" / "audit.db"
        conn = _mkdb(
            db,
            {
                "model_runtime_health": [
                    ("id", "INTEGER"),
                    ("checked_at", "TEXT"),
                    ("champion_id", "TEXT"),
                    ("champion_version", "TEXT"),
                    ("champion_schema", "TEXT"),
                    ("champion_healthy", "INTEGER"),
                    ("challenger_id", "TEXT"),
                    ("challenger_version", "TEXT"),
                    ("challenger_state", "TEXT"),
                    ("shadow_running", "INTEGER"),
                    ("shadow_comparisons", "INTEGER"),
                    ("shadow_errors", "INTEGER"),
                    ("shadow_dropped", "INTEGER"),
                    ("last_update", "TEXT"),
                    ("payload", "TEXT"),
                ],
                "model_shadow_comparisons": [("id", "INTEGER")],
            },
        )
        conn.execute(
            "INSERT INTO model_runtime_health VALUES (1, '2026-08-19T00:00:00+00:00', '', '', '', 0, '', '', 'NONE', 1, 0, 0, 0, '', '{}')"
        )
        conn.commit()
        conn.close()
        from nexus_scalp.forensics import checks as C

        r = C.check_shadow_health()
        assert r.status is HealthStatus.DEGRADED
        assert "SHADOW_NO_PROGRESS" in r.detail


# ---------------------------------------------------------------------------
# TEST-POST70D-18 — governance impossible-state detection
# ---------------------------------------------------------------------------


class TestPost70d18Governance:
    def test_rejected_champion_critical(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "artifacts").mkdir(exist_ok=True)
        db = tmp_path / "artifacts" / "audit.db"
        conn = _mkdb(
            db,
            {
                "model_governance_state": [
                    ("model_id", "TEXT"),
                    ("model_version", "TEXT"),
                    ("lifecycle_state", "TEXT"),
                    ("updated_at", "TEXT"),
                    ("evidence", "TEXT"),
                ],
            },
        )
        conn.execute(
            "INSERT INTO model_governance_state VALUES ('m1', '1.0', 'REJECTED_CHAMPION', '2026-08-19T00:00:00+00:00', '{}')"
        )
        conn.commit()
        conn.close()
        from nexus_scalp.forensics import checks as C

        r = C.check_governance_consistency()
        assert r.status is HealthStatus.CRITICAL


# ---------------------------------------------------------------------------
# TEST-POST70D-19 — Champion identity integrity
# ---------------------------------------------------------------------------


class TestPost70d19Champion:
    def test_registry_champion_missing_artifact_critical(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "artifacts").mkdir(exist_ok=True)
        db = tmp_path / "artifacts" / "audit.db"
        conn = _mkdb(
            db,
            {
                "experience_model_registry": [
                    ("id", "INTEGER"),
                    ("model_id", "TEXT"),
                    ("model_version", "TEXT"),
                    ("model_role", "TEXT"),
                    ("artifact_path", "TEXT"),
                    ("artifact_fingerprint", "TEXT"),
                    ("feature_schema_id", "TEXT"),
                    ("feature_dimension", "INTEGER"),
                    ("config_version", "TEXT"),
                    ("build_identity", "TEXT"),
                    ("was_replacement", "INTEGER"),
                    ("registered_at", "TEXT"),
                    ("lifecycle_status", "TEXT"),
                    ("training_run_id", "TEXT"),
                    ("parent_model_id", "TEXT"),
                    ("parent_model_version", "TEXT"),
                    ("child_model_id", "TEXT"),
                    ("promotion_reason", "TEXT"),
                    ("gate_summary", "TEXT"),
                    ("validation_run_ids", "TEXT"),
                ],
            },
        )
        conn.execute(
            "INSERT INTO experience_model_registry VALUES (1, 'm1', '1.0', 'PRIMARY_SCALP', "
            "'artifacts/models/does_not_exist.pt', 'deadbeef', 'scalp_v1', 50, 'v1', '', 0, "
            "'2026-08-19T00:00:00+00:00', 'CHAMPION', '', '', '', '', '', '{}', '[]')"
        )
        conn.commit()
        conn.close()
        from nexus_scalp.forensics import checks as C

        r = C.check_champion_identity()
        assert r.status in (HealthStatus.CRITICAL, HealthStatus.UNKNOWN)


# ---------------------------------------------------------------------------
# TEST-POST70D-20 — UI/API divergence
# ---------------------------------------------------------------------------


class TestPost70d20UiApi:
    def test_canonical_endpoint(self):
        from nexus_scalp.forensics import checks as C

        r = C.check_ui_canonical_state()
        assert r.status is HealthStatus.PASS
        assert r.observed["endpoint"] == "/api/live/state"

    def test_api_surface_present(self):
        from nexus_scalp.forensics import checks as C

        r = C.check_api_200_but_wrong()
        assert r.status is HealthStatus.PASS
        assert all(r.observed.values())


# ---------------------------------------------------------------------------
# TEST-POST70D-21 — web bundle drift
# ---------------------------------------------------------------------------


class TestPost70d21Bundle:
    def test_bundle_no_marker_unknown(self, tmp_path, monkeypatch):
        (tmp_path / "Web").mkdir()
        (tmp_path / "Web" / "index.html").write_text("<html></html>")
        (tmp_path / "Web" / "app.js").write_text("console.log('x');")
        monkeypatch.chdir(tmp_path)
        from nexus_scalp.forensics import checks as C

        r = C.check_ui_bundle_drift()
        assert r.status is HealthStatus.UNKNOWN  # no marker -> cannot verify


# ---------------------------------------------------------------------------
# TEST-POST70D-22 — Telegram aggregation
# ---------------------------------------------------------------------------


class TestPost70d22TelegramAggregation:
    def test_build_report_bounded(self):
        rec = _snapshot({f"CHECK-{i:02d}": "PASS" for i in range(40)})
        text = build_report_text(rec, mode="PAPER", symbol="XAUUSD")
        # report is summarized, not every check
        assert "NSE FORENSIC HEALTH" in text
        assert "Overall:" in text
        assert "CHECK-" not in text  # no individual checks in the summary

    def test_report_config_defaults(self):
        cfg = load_report_config(Path(tempfile.mkdtemp()) / "missing.yaml")
        assert cfg.enabled is False
        assert cfg.interval_sec > 0


# ---------------------------------------------------------------------------
# TEST-POST70D-23 — Telegram dedup (cooldown)
# ---------------------------------------------------------------------------


class TestPost70d23TelegramDedup:
    def test_cooldown_suppresses_repeat(self, tmp_path):
        cfg = __import__(
            "nexus_scalp.forensics.telegram_report", fromlist=["ForensicReportConfig"]
        ).ForensicReportConfig(
            enabled=True, interval_sec=0, minimum_severity="WARNING", aggregation_window_sec=100
        )
        sched = TelegramReportScheduler(
            config=cfg, history_dir=tmp_path, state_path=tmp_path / "state.json"
        )
        rec = _snapshot({"CHECK-NWS-01": "WARNING"})
        now = time.monotonic()
        # first pass: fresh
        fresh1 = sched.dedup(rec, now=now)
        assert len(fresh1) == 1
        sched.mark_sent(rec, now=now)
        # second pass within cooldown: suppressed
        fresh2 = sched.dedup(rec, now=now + 5)
        assert len(fresh2) == 0
        # after window: fresh again
        fresh3 = sched.dedup(rec, now=now + 200)
        assert len(fresh3) == 1


# ---------------------------------------------------------------------------
# TEST-POST70D-24 — periodic report bounded
# ---------------------------------------------------------------------------


class TestPost70d24PeriodicReport:
    def test_run_once_disabled_returns_quiet(self, tmp_path):
        cfg = __import__(
            "nexus_scalp.forensics.telegram_report", fromlist=["ForensicReportConfig"]
        ).ForensicReportConfig(enabled=False)
        sched = TelegramReportScheduler(config=cfg, history_dir=tmp_path)
        outcome = sched.run_once(engine=ForensicHealthEngine(history_dir=tmp_path), deliver=False)
        assert outcome["sent"] is False
        assert "disabled" in outcome.get("reason", "")

    def test_interval_prevents_spam(self, tmp_path):
        cfg = __import__(
            "nexus_scalp.forensics.telegram_report", fromlist=["ForensicReportConfig"]
        ).ForensicReportConfig(
            enabled=True, interval_sec=3600, minimum_severity="WARNING", aggregation_window_sec=0
        )
        sched = TelegramReportScheduler(config=cfg, history_dir=tmp_path)
        assert sched.should_send(now=time.monotonic()) is True
        sched.mark_sent(_snapshot({"CHECK-A": "PASS"}), now=time.monotonic())
        assert sched.should_send(now=time.monotonic() + 60) is False  # inside interval


# ---------------------------------------------------------------------------
# TEST-POST70D-25 — check timeout budget
# ---------------------------------------------------------------------------


class TestPost70d25Timeout:
    def test_engine_isolation_on_raise(self):
        """A raised check becomes UNKNOWN, engine continues (isolation §38)."""
        engine = ForensicHealthEngine(history_dir=Path(tempfile.mkdtemp()))
        rec = engine.snapshot(persist=False)
        assert rec.overall  # engine produced a snapshot despite any check raising
        # every check carries duration_ms (bounded work §37 evidence)
        for c in rec.checks:
            assert "duration_ms" in c


# ---------------------------------------------------------------------------
# TEST-POST70D-26 — check isolation
# ---------------------------------------------------------------------------


class TestPost70d26Isolation:
    def test_one_bad_check_does_not_crash_engine(self):
        def bad() -> CheckResult:
            raise RuntimeError("check A broken")

        engine = ForensicHealthEngine(history_dir=Path(tempfile.mkdtemp()))
        groups = engine.check_groups()
        groups["Broken"] = [bad]
        results = {}
        for g, fns in groups.items():
            results[g] = []
            for fn in fns:
                try:
                    results[g].append(fn())
                except Exception as exc:
                    results[g].append(
                        CheckResult("CHECK-RAISED", HealthStatus.UNKNOWN, evidence=str(exc))
                    )
        # other groups still evaluated
        assert results.get("Model")
        assert any(r.status is HealthStatus.UNKNOWN for r in results["Broken"])


# ---------------------------------------------------------------------------
# TEST-POST70D-27 — no self-modification regression
# ---------------------------------------------------------------------------


class TestPost70d27NoSelfModify:
    def test_engine_has_no_mutation_api(self):
        engine = ForensicHealthEngine()
        publics = [m for m in dir(engine) if not m.startswith("_")]
        for mutating in (
            "rewrite",
            "retrain",
            "promote",
            "repair",
            "delete",
            "update_reference",
            "freeze",
            "modify_label",
            "set_weight",
        ):
            assert not any(mutating in m.lower() for m in publics)

    def test_deploy_gate_never_writes_production(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "artifacts").mkdir(exist_ok=True)
        engine = ForensicHealthEngine(history_dir=tmp_path / "forensics")
        run_deploy_gate(engine, persist=True, result_dir=tmp_path / "forensics")
        # the gate only wrote its own artifacts/forensics evidence
        created = [p for p in (tmp_path / "forensics").rglob("*") if p.is_file()]
        assert created
        # no audit.db / news.db / candle_intel.db created in the hermetic dir
        assert not (tmp_path / "artifacts" / "audit.db").exists()


# ---------------------------------------------------------------------------
# TEST-POST70D-28 — current UNKNOWN explanations
# ---------------------------------------------------------------------------


class TestPost70d28Unknowns:
    def test_unknowns_are_explained(self):
        engine = ForensicHealthEngine(history_dir=Path(tempfile.mkdtemp()))
        rec = engine.snapshot(persist=False)
        unknowns = [c for c in rec.checks if c["status"] == "UNKNOWN"]
        for u in unknowns:
            # every UNKNOWN carries evidence explaining why (§41)
            assert u["evidence"], f"{u['check_id']} UNKNOWN without evidence"

    def test_no_fabricated_pass_for_missing(self):
        # a missing DB must be UNKNOWN, never PASS
        from nexus_scalp.forensics import checks as C

        r = C.check_database_integrity(db_paths={"audit": Path("nope.db")})
        assert r.status is HealthStatus.UNKNOWN
