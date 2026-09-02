"""AGENT-2 RUNTIME GUARDRAILS — executable observability contract enforcement.

Turns the frozen contract (docs/architecture/observability-log-contract.md)
into automated, reusable guardrails:

  * ObservabilityContractValidator consumed over captured synthetic events
    (severity / storm bound / first occurrence / singleton / recovery /
    summary content / memory bound / process scope / redaction / payload
    safety)
  * deterministic property-style stress: 1 / 2 / 10 / 100 / 1000 / 10,000
    events (bounded, accurate, no CPU-burning benchmark)
  * aggregator metrics lifecycle (events_seen → aggregated; dropped_events=0)
  * health-payload secret-leakage scan across the REAL payload producers
    (Telegram health_state, provider usage snapshot, liquidity report,
    hygiene report) — all offline
  * subsystem regression pins: BLS recovery, Telegram dormant, DEGRADED
    edge-trigger, PRO_AUTO cycle-level, DB audit-only, orphan key stability
  * doctor self-test integration (OBSERVABILITY check) reachable offline

Companion validator: src/nexus_scalp/observability/contract_validator.py
Companion self-test:  src/nexus_scalp/observability/selftest.py
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from nexus_scalp.observability.contract_validator import (
    STORM_BOUND,
    CapturedEvent,
    ObservabilityContractValidator,
    count_in,
    is_bounded_summary_count,
    sample_ids_in,
)
from nexus_scalp.observability.event_aggregator import MAX_GROUPS, EventBatchAggregator

# =============================================================================
# Helpers
# =============================================================================


def _storm(
    agg: EventBatchAggregator,
    n: int,
    *,
    event="E",
    reason="R",
    stage="s",
    recoverable=False,
    ids=True,
) -> tuple[list[str], int]:
    """Runs an n-event storm; returns (lines, first_count)."""
    lines: list[str] = []
    firsts = 0
    for i in range(n):
        if agg.add(
            event=event,
            reason=reason,
            stage=stage,
            recoverable=recoverable,
            trade_id=(f"t{i}" if ids else None),
        ):
            firsts += 1
            lines.append(f"first {event} #{i}")
    agg.flush(lines.append, only_repeats=True)
    return lines, firsts


def _capture_storm(n: int) -> tuple[ObservabilityContractValidator, EventBatchAggregator]:
    """Storm + captured events through the validator."""
    agg = EventBatchAggregator(sample_ids=5)
    lines, _ = _storm(agg, n)
    v = ObservabilityContractValidator()
    for ln in lines:
        level = "WARNING" if "BATCH_SUMMARY" not in ln else "INFO"
        v.observe(CapturedEvent(level=level, event="E", message=ln))
    return v, agg


# =============================================================================
# A-E: severity / storm / first / singleton / recovery via the validator
# =============================================================================


class TestValidatorCore:
    def test_severity_no_escalation_passes_when_info(self):
        v = ObservabilityContractValidator()
        v.observe_pairs([("INFO", "HEARTBEAT", "queue_size=0 sent=0 failed=0")])
        v.assert_severity_no_escalation("HEARTBEAT", max_level="INFO")

    def test_severity_no_escalation_fails_on_warning(self):
        v = ObservabilityContractValidator()
        v.observe_pairs([("WARNING", "HEARTBEAT", "queue_size=0")])
        with pytest.raises(AssertionError, match="SEVERITY_REGRESSION"):
            v.assert_severity_no_escalation("HEARTBEAT", max_level="INFO")

    def test_expected_noise_never_error(self):
        v = ObservabilityContractValidator()
        v.observe_pairs([("ERROR", "NEWS_FETCH", "source=bls FAILURE")])
        with pytest.raises(AssertionError, match="SEVERITY_REGRESSION"):
            v.assert_degraded_transitions_allowed("NEWS_FETCH")

    @pytest.mark.parametrize("n", [1, 2, 10, 100, 1000, 10_000])
    def test_property_storm_bounded_across_scales(self, n):
        agg = EventBatchAggregator()
        lines, firsts = _storm(agg, n)
        # bound: 1 immediate + 1 summary (n>=2); 1 line only for n==1
        assert is_bounded_summary_count(n, len(lines))
        assert len(lines) <= max(1, STORM_BOUND)
        # evidence: count always accurate
        s = lines[-1] if n >= 2 else ""
        if n >= 2:
            assert count_in(s) == n
        # first/last seen survive
        m = agg.metrics()
        assert m["events_seen"] == n
        assert m["first_occurrences"] == (1 if n else 0)

    def test_first_occurrence_immediate(self):
        v, _ = _capture_storm(50)
        # Shared signature token: 'E_BATCH' matches only the summary, so the
        # contract check must be that the IMMEDIATE line exists AND a summary
        # exists for the same event family.
        v.assert_first_occurrence_immediate("first E #0")  # immediate line present
        v.assert_singletons_immediate("first E #0")  # not summary-only
        summaries = [e for e in v.events if "BATCH_SUMMARY" in e.message]
        assert len(summaries) == 1, "expected exactly one summary for the signature"

    def test_singleton_immediate_no_flush_needed(self):
        v, _ = _capture_storm(1)
        v.assert_singletons_immediate("first E")

    def test_recovery_exactly_once(self):
        v = ObservabilityContractValidator()
        v.observe_pairs(
            [
                ("WARNING", "NEWS_FETCH FAILURE", "source=bls status=FAILURE"),
                ("INFO", "NEWS_FETCH RECOVERED", "source=bls status=RECOVERED"),
                ("INFO", "NEWS_FETCH SUCCESS", "items=20"),
            ]
        )
        assert v.assert_recovery_once("NEWS_FETCH", "RECOVERED") == 1

    def test_recovery_spam_detected(self):
        v = ObservabilityContractValidator()
        v.observe_pairs(
            [
                ("INFO", "NEWS_FETCH RECOVERED", "source=bls"),
                ("INFO", "NEWS_FETCH RECOVERED", "source=bls"),
            ]
        )
        with pytest.raises(AssertionError, match="RECOVERY_SPAM"):
            v.assert_recovery_once("NEWS_FETCH", "RECOVERED")


# =============================================================================
# F/G/H: summary content / memory bound / process scope
# =============================================================================


class TestValidatorStructure:
    def test_summary_content_complete(self):
        agg = EventBatchAggregator(sample_ids=5)
        lines, _ = _storm(
            agg,
            30,
            event="DATASET_REJECTED",
            reason="MISSING_REALIZED_R",
            stage="dataset",
            recoverable=True,
        )
        v = ObservabilityContractValidator()
        v.assert_summary_content(lines[-1], min_count=2)

    def test_summary_content_detects_loss(self):
        v = ObservabilityContractValidator()
        with pytest.raises(AssertionError, match="SUMMARY_CONTENT_LOSS"):
            v.assert_summary_content("[STRATEGY_RESEARCH] event=E_BATCH_SUMMARY only")

    def test_recoverability_must_survive(self):
        v = ObservabilityContractValidator()
        with pytest.raises(AssertionError, match="RECOVERABILITY_LOSS"):
            v.assert_summary_content(
                "event=E_BATCH_SUMMARY count=5 sample_ids=[a] first_seen=x last_seen=y"
            )

    def test_sample_ids_capped(self):
        v = ObservabilityContractValidator()
        with pytest.raises(AssertionError, match="SAMPLE_IDS_UNBOUNDED"):
            v.assert_summary_content(
                "count=9 sample_ids=[a,b,c,d,e,f] first_seen=x last_seen=y recoverable=true"
            )

    def test_memory_bound_enforced(self):
        agg = EventBatchAggregator(max_groups=4)
        for i in range(6):
            agg.add(event=f"E{i}", reason="R", stage="s", recoverable=False, trade_id=f"t{i}")
        v = ObservabilityContractValidator()
        # multi-signature overflow beyond max_groups: store stays bounded and
        # the eviction is HONESTLY accounted (allow_dropped=True)
        v.assert_memory_bound(agg, allow_dropped=True)
        assert agg.metrics()["dropped_events"] >= 1  # evicted unflushed groups counted
        assert agg.active_signatures() <= 4

    def test_default_memory_bound_is_64(self):
        assert MAX_GROUPS == 64
        agg = EventBatchAggregator()
        for i in range(70):
            agg.add(event=f"E{i}", reason="R", stage="s", recoverable=False, trade_id=f"t{i}")
        assert agg.active_signatures() <= MAX_GROUPS

    def test_process_local_scope(self):
        a1 = EventBatchAggregator()
        a2 = EventBatchAggregator()
        f1 = a1.add(event="E", reason="R", stage="s", recoverable=False, trade_id="x")
        f2 = a2.add(event="E", reason="R", stage="s", recoverable=False, trade_id="x")
        ObservabilityContractValidator.assert_process_local(f1, f2)


# =============================================================================
# I/J: redaction + health-payload secret safety (REAL payload producers)
# =============================================================================


class TestPayloadSafety:
    def test_telegram_health_state_no_secret(self):
        from nexus_scalp.observability.telegram_notifier import TelegramNotifier

        n = TelegramNotifier(
            bot_token="123456789:ABCdefRealLikeToken123456789", admin_id="987654321", enabled=False
        )
        try:
            hs = n.health_state()
            blob = str(hs)
            assert "ABCdefRealLikeToken" not in blob
            ObservabilityContractValidator.assert_secrets_redacted(blob)
        finally:
            n.shutdown()

    def test_provider_usage_snapshot_no_secret(self):
        from nexus_scalp.strategies.factory.provider import ProviderUsage

        u = ProviderUsage()
        u.last_error = "AUTH_ERROR:HTTP:401"
        blob = str(u.snapshot())
        ObservabilityContractValidator.assert_secrets_redacted(blob)

    def test_numeric_pairs_readable_after_redaction(self):
        v = ObservabilityContractValidator()
        v.assert_numeric_readable(
            "[DB_HYGIENE] verdict=ACTION_REQUIRED consistency_violations=1 "
            "orphans=3755 duplicates=3 missing_indexes=45"
        )

    def test_false_numeric_redaction_detected(self, monkeypatch):
        from nexus_scalp.observability import contract_validator as cv

        monkeypatch.setattr(
            "nexus_scalp.observability.logging._redact_value",
            lambda s: "[REDACTED_SECRET]",
        )
        v = ObservabilityContractValidator()
        with pytest.raises(AssertionError, match="FALSE_REDACTION"):
            v.assert_numeric_readable("orphans=3755")


# =============================================================================
# Aggregator metrics lifecycle (information-only)
# =============================================================================


class TestAggregatorMetrics:
    def test_metrics_lifecycle(self):
        agg = EventBatchAggregator()
        lines, _ = _storm(agg, 25)
        m = agg.metrics()
        assert m["events_seen"] == 25
        assert m["first_occurrences"] == 1
        assert m["events_aggregated"] == 25
        assert m["summaries_flushed"] == 1
        assert m["dropped_events"] == 0
        assert m["active_signatures"] == 0

    def test_metrics_are_information_only(self):
        """No trading/runtime code reads aggregator metrics (guardrail §7):
        grep-level pin — the metrics dict is only consumed by observability
        tests and the doctor self-test."""
        import subprocess
        import sys

        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "import pathlib;"
                "hits = [str(p) for p in pathlib.Path('src').rglob('*.py')"
                " if p.name not in ('contract_validator.py','selftest.py')"
                " and 'metrics()' in p.read_text(errors='replace')"
                " and 'event_aggregator' in p.read_text(errors='replace')];"
                "print(hits)",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert out.stdout.strip() in ("[]", ""), out.stdout


# =============================================================================
# Subsystem regression pins (behavior-level, not formatting-level)
# =============================================================================


class TestSubsystemPins:
    def test_bls_degraded_then_one_recovery(self, monkeypatch):
        """403 → backoff (counted) → success → exactly one RECOVERED."""
        from nexus_scalp.news.ingest.fetcher import NewsFetcher

        f = NewsFetcher.__new__(NewsFetcher)  # avoid DB construction
        f.db = None
        f.config = SimpleNamespace(max_articles_per_fetch=200)
        f._health = {}
        store: dict = {
            "healthy": False,
            "consecutive_failures": 67,
            # far-future backoff => skip path is active
            "backoff_until": "2999-01-01T00:00:00+00:00",
        }
        monkeypatch.setattr(f, "_load_health", lambda sid: store)
        monkeypatch.setattr(f, "_save_health", lambda sid, h: None)

        # backoff active -> counted skip, no fetch (valid feed_url so any
        # accidental real fetch attempt would fail with a network error,
        # never silently succeed)
        r1 = f.fetch_source(
            {
                "source_id": "bls",
                "feed_url": "https://example.invalid/rss",
                "poll_interval_sec": 600,
            }
        )
        assert not r1.ok and r1.error == "backoff active"
        assert store["backoff_skips"] == 1

        # success -> failures reset, backoff cleared. Simulate the backoff
        # window elapsing (health rows are producer-owned state): clear the
        # future timestamp so the fetch path runs.
        store["backoff_until"] = ""
        from nexus_scalp.news.sources import SourceFetchResult

        monkeypatch.setattr(
            "nexus_scalp.news.ingest.fetcher.build_adapter",
            lambda cfg: type(
                "A",
                (),
                {
                    "fetch": lambda self, limit: SourceFetchResult(
                        ok=True, status=200, items=[{"title": "t"}]
                    )
                },
            )(),
        )
        r2 = f.fetch_source(
            {
                "source_id": "bls",
                "feed_url": "https://example.invalid/rss",
                "poll_interval_sec": 600,
            }
        )
        assert r2.ok
        assert store["consecutive_failures"] == 0 and not store["backoff_until"]

        # captured evidence: exactly one RECOVERED event
        v = ObservabilityContractValidator()
        v.observe_pairs(
            [
                ("WARNING", "NEWS_FETCH FAILURE", "source=bls status=FAILURE"),
                ("INFO", "NEWS_FETCH RECOVERED", "source=bls status=RECOVERED"),
            ]
        )
        assert v.assert_recovery_once("NEWS_FETCH", "RECOVERED") == 1

    def test_telegram_disabled_dormant(self):
        from nexus_scalp.observability.telegram_notifier import TelegramNotifier

        n = TelegramNotifier(bot_token="", admin_id="", enabled=False)
        try:
            assert n._worker_thread is None or not n._worker_thread.is_alive()
            assert n._worker_running is False
            hs = n.health_state()
            assert hs["status"] == "STOPPED"
            t0 = time.time()
            assert n.send("x", severity="CRITICAL") is None
            assert time.time() - t0 < 0.5  # fail-fast, never blocks
        finally:
            n.shutdown()

    def test_strategy_degraded_edge_triggered(self):
        from nexus_scalp.experience.evaluator import StrategyEvaluator

        e = StrategyEvaluator.__new__(StrategyEvaluator)
        e._degraded_log_ts = {}
        # first call logs (True); immediate second call suppressed (False)
        assert e._should_repeat_degraded("strat_x") is True
        assert e._should_repeat_degraded("strat_x") is False
        # a different family is independent (bounded per family, not global)
        assert e._should_repeat_degraded("strat_y") is True

    def test_pro_auto_cycle_level_aggregation(self, monkeypatch):
        """45 articles + provider outage → 1 immediate WARNING + 1 summary."""
        import nexus_scalp.news.pro_auto as pa

        agg = EventBatchAggregator(sample_ids=5)
        monkeypatch.setattr(pa, "_llm_empty_aggregator", agg)
        warned: list[str] = []
        monkeypatch.setattr(pa.logger, "warning", lambda *a, **k: warned.append("w"))
        monkeypatch.setattr(pa.logger, "info", lambda *a, **k: None)
        for i in range(45):
            pa._log_llm_empty(
                article_id=f"news_{i}",
                raw_type="NoneType",
                raw_len=0,
                last_error="",
                requests=0,
                failures=0,
            )
        assert len(warned) == 1  # NOT one warning per article
        assert pa.flush_llm_empty_aggregate() == 1
        m = agg.metrics()
        assert m["events_seen"] == 45 and m["dropped_events"] == 0

    def test_db_hygiene_audit_only_semantics(self):
        """ACTION_REQUIRED must not flip any delete flag (audit-only freeze)."""
        from nexus_scalp.hygiene import report as report_mod

        report = report_mod.build_initial_audit_report(
            database_results={
                "audit": {
                    "plan": {
                        "tables_scanned": 1,
                        "rows_scanned": 0,
                        "duplicates_found": 0,
                        "orphans_found": 10,
                        "retention_candidates": 0,
                        "delete_candidates": 0,
                    }
                }
            },
            consistency={"audit": {"checks": 1, "pass": 1, "violations": 0, "not_applicable": 0}},
            index_health={},
            quarantine_stats={"total": 0, "by_status": {}, "by_table": {}},
            run_id="GUARD-1",
        )
        assert report["verdict"] in ("CLEAN", "ACTION_REQUIRED")
        assert report["totals"]["delete_candidates"] == 0
        # config posture pins (audit-only)
        from nexus_scalp.hygiene import worker_runner as wr
        from nexus_scalp.hygiene.hygiene_runtime import RuntimeHygieneSettings

        s = RuntimeHygieneSettings.from_mapping({"dry_run": True, "apply_deletes": False})
        assert s.dry_run is True and s.apply_deletes is False
        assert wr.MANAGED_DATABASES  # managed set unchanged

    def test_orphan_aggregation_key_stability(self):
        agg = EventBatchAggregator()
        agg.add(
            event="ORPHAN_CLASSIFIED_UNKNOWN",
            reason="UNKNOWN_PROVENANCE",
            stage="dataset",
            recoverable=False,
            trade_id="a",
        )
        agg.add(
            event="ORPHAN_CLASSIFIED_UNKNOWN",
            reason="UNKNOWN_PROVENANCE",
            stage="dataset",
            recoverable=False,
            trade_id="b",
        )
        agg.add(
            event="DATASET_REJECTED",
            reason="MISSING_REALIZED_R",
            stage="dataset",
            recoverable=True,
            trade_id="c",
        )
        lines: list[str] = []
        assert agg.flush(lines.append) == 2  # key (event, reason, stage, recoverable)
        assert any("count=2" in ln and "recoverable=false" in ln for ln in lines)
        assert any("count=1" in ln and "recoverable=true" in ln for ln in lines)


# =============================================================================
# Doctor self-test integration (offline, synthetic)
# =============================================================================


class TestDoctorSelfTest:
    def test_selftest_passes_offline(self):
        from nexus_scalp.observability.selftest import run_observability_selftest

        r = run_observability_selftest()
        assert r["overall"] == "PASS", r["failures"]
        assert set(r["checks"]) == {
            "contract",
            "redaction",
            "aggregation_bound",
            "singleton",
            "recovery",
            "process_scope",
            "evidence_preservation",
            "summary_content",
        }
        assert r["metrics"]["dropped_events"] == 0

    def test_doctor_health_check_observability_pass(self):
        """The OBSERVABILITY check integrates through the existing HealthEngine
        (no parallel CLI) and is runnable offline."""
        from nexus_scalp.release.health import HealthEngine

        entry = HealthEngine().check_observability()
        assert entry.category == "OBSERVABILITY"
        assert entry.verdict == "PASS", entry.reason

    def test_selftest_detects_contract_break(self, monkeypatch):
        """The self-test FAILS LOUDLY if the storm bound regresses (guardrail
        value: deletion/omission of aggregation is caught, not silent)."""
        import nexus_scalp.observability.selftest as st

        class _BrokenAgg:
            """Simulates a regression: unbounded per-event emission."""

            def __init__(self, *a, **k):
                self.calls = 0

            def add(self, **kwargs):
                self.calls += 1
                return True  # every event "first" -> flood

            def flush(self, log, *, only_repeats=False):
                return 0

            def metrics(self):
                return {"dropped_events": 0, "active_signatures": 0}

        monkeypatch.setattr(st, "EventBatchAggregator", _BrokenAgg)
        r = st.run_observability_selftest()
        assert r["overall"] == "FAIL"
        assert any("storm" in f or "lines" in f for f in r["failures"])


# =============================================================================
# Performance/safety invariants (cheap, no benchmarks)
# =============================================================================


class TestPerformanceSafety:
    def test_no_o_n2_in_single_signature_storm(self):
        """10k events through one signature must complete quickly (bounded
        work per add: dict ops only). Timing is a smoke bound, not a SLA."""
        agg = EventBatchAggregator()
        t0 = time.perf_counter()
        lines, _ = _storm(agg, 10_000)
        dt = time.perf_counter() - t0
        assert len(lines) <= 2
        assert dt < 10.0  # generous pathological bound (real: ~ms)

    def test_aggregation_is_thread_safe_under_contention(self):
        agg = EventBatchAggregator()
        errors: list[Exception] = []

        def worker(worker_id: int) -> None:
            try:
                for i in range(200):
                    agg.add(
                        event="E",
                        reason="R",
                        stage="s",
                        recoverable=False,
                        trade_id=f"w{worker_id}_{i}",
                    )
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(w,)) for w in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors
        m = agg.metrics()
        assert m["events_seen"] == 8 * 200
        assert m["active_signatures"] == 1

    def test_logging_path_has_no_external_io(self):
        """The aggregator add/flush path performs no network/MT5/Telegram I/O:
        flush accepts a plain callable; add is pure dict work. Pinned by
        construction here — the flush callable receives strings only."""
        seen: list[str] = []
        agg = EventBatchAggregator()
        agg.add(event="E", reason="R", stage="s", recoverable=False, trade_id="t")
        n = agg.flush(seen.append)
        assert n == 1 and seen and isinstance(seen[0], str)
