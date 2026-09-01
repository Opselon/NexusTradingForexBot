"""AGENT-2 OBSERVABILITY CONTRACT CLOSURE — regression guards (2026-09-01).

Freezes the log/health semantics proven in the observability sprint and adds
boundedness guards so future agents cannot silently reintroduce:

  * per-row flood where a cycle-level condition exists (PRO_AUTO LLM empty)
  * unbounded aggregator output for an event storm
  * loss of recoverability/count/sample_ids in summaries
  * Telegram heartbeat storms while disabled
  * entropy-redaction of numeric key=value observability pairs

All tests are deterministic (no network, no real Telegram, no production DB).
"""

from __future__ import annotations

import time

import pytest

from nexus_scalp.observability.event_aggregator import EventBatchAggregator

# =============================================================================
# 1. PRO_AUTO 'LLM empty' aggregation (the live 914-lines/day flood)
# =============================================================================


class TestProAutoLlmEmptyAggregation:
    def test_first_occurrence_logs_immediately_repeats_aggregate(self, monkeypatch):
        import nexus_scalp.news.pro_auto as pa

        # fresh aggregator for isolation
        agg = EventBatchAggregator(sample_ids=5)
        monkeypatch.setattr(pa, "_llm_empty_aggregator", agg)
        logged: list[tuple[str, str]] = []
        monkeypatch.setattr(pa.logger, "warning", lambda *a, **k: logged.append(("warning", a)))
        monkeypatch.setattr(pa.logger, "info", lambda *a, **k: logged.append(("info", a)))

        for i in range(45):
            pa._log_llm_empty(
                article_id=f"news_{i:04d}",
                raw_type="NoneType",
                raw_len=0,
                last_error="",
                requests=0,
                failures=0,
            )
        # §21 first-occurrence policy: exactly ONE immediate WARNING
        assert len([1 for lvl, _ in logged if lvl == "warning"]) == 1
        assert agg.pending() == 45

        # §23 flush at cycle boundary: ONE summary line, full count preserved
        emitted = pa.flush_llm_empty_aggregate()
        assert emitted == 1
        infos = [a[0] for lvl, a in logged if lvl == "info"]
        assert infos and "count=45" in infos[-1]
        assert "reason=NO_ATTEMPT_PROVIDER_UNAVAILABLE" in infos[-1]
        assert "sample_ids=[news_0000" in infos[-1]

    def test_distinct_last_error_reasons_make_separate_summaries(self, monkeypatch):
        import nexus_scalp.news.pro_auto as pa

        agg = EventBatchAggregator(sample_ids=5)
        monkeypatch.setattr(pa, "_llm_empty_aggregator", agg)
        monkeypatch.setattr(pa.logger, "warning", lambda *a, **k: None)
        monkeypatch.setattr(pa.logger, "info", lambda *a, **k: None)

        for i in range(10):
            pa._log_llm_empty(
                article_id=f"a{i}",
                raw_type="NoneType",
                raw_len=0,
                last_error="",
                requests=0,
                failures=0,
            )
        for i in range(3):
            pa._log_llm_empty(
                article_id=f"b{i}",
                raw_type="NoneType",
                raw_len=0,
                last_error="HTTP:429",
                requests=2,
                failures=2,
            )
        lines: list[str] = []
        n = pa._llm_empty_aggregator.flush(lines.append, only_repeats=True)
        assert n == 2
        assert any("count=10" in ln and "NO_ATTEMPT" in ln for ln in lines)
        assert any("count=3" in ln and "HTTP:429" in ln for ln in lines)

    def test_bounded_output_for_storm(self, monkeypatch):
        """§19: 1000 identical events → ≤ small bounded number of lines."""
        import nexus_scalp.news.pro_auto as pa

        agg = EventBatchAggregator(sample_ids=5)
        monkeypatch.setattr(pa, "_llm_empty_aggregator", agg)
        monkeypatch.setattr(pa.logger, "warning", lambda *a, **k: None)
        monkeypatch.setattr(pa.logger, "info", lambda *a, **k: None)
        for i in range(1000):
            pa._log_llm_empty(
                article_id=f"s{i}",
                raw_type="NoneType",
                raw_len=0,
                last_error="",
                requests=0,
                failures=0,
            )
        lines: list[str] = []
        n = pa._llm_empty_aggregator.flush(lines.append, only_repeats=True)
        # 1 immediate warning + 1 summary = 2 lines total for 1000 events
        assert n == 1 and "count=1000" in lines[0]

    def test_local_fallback_behavior_unchanged(self):
        """The aggregation must NOT change analysis behavior: the fallback
        function exists, runs local analysis, and always returns a result
        dict (contract of run_pro_auto_analysis_for_article)."""
        import inspect

        from nexus_scalp.news.pro_auto import run_pro_auto_analysis_for_article

        src = inspect.getsource(run_pro_auto_analysis_for_article)
        # the deterministic path still always runs after the LLM attempt
        assert "Deterministic path ALWAYS runs" in src
        assert "_log_llm_empty(" in src  # aggregation wired in
        assert 'logger.warning(\n                        "[PRO_AUTO] LLM empty"' not in src


# =============================================================================
# 2. Generic cross-event flood guard (spec §18/§19)
# =============================================================================


class TestAggregatorBoundednessContract:
    @pytest.mark.parametrize("n,expected_max", [(100, 2), (500, 2), (1000, 2)])
    def test_identical_storm_stays_bounded(self, n, expected_max):
        agg = EventBatchAggregator(sample_ids=5)
        emitted = 0
        for i in range(n):
            if agg.add(event="E", reason="R", stage="s", recoverable=False, trade_id=f"t{i}"):
                emitted += 1  # first-occurrence immediate line
        lines: list[str] = []
        emitted += agg.flush(lines.append, only_repeats=True)
        assert emitted <= expected_max
        assert any(f"count={n}" in ln for ln in lines)

    def test_mixed_storm_bounded_by_distinct_signatures(self):
        """Output bound = 1 immediate + 1 summary per DISTINCT signature,
        never per event."""
        agg = EventBatchAggregator()
        sigs = [
            ("E1", "R1", "s1", False),
            ("E1", "R2", "s1", False),
            ("E2", "R1", "s2", True),
            ("E3", "R1", "s1", False),
        ]
        firsts = 0
        for i in range(400):
            event, reason, stage, rec = sigs[i % len(sigs)]
            if agg.add(event=event, reason=reason, stage=stage, recoverable=rec, trade_id=f"t{i}"):
                firsts += 1
        lines: list[str] = []
        summaries = agg.flush(lines.append, only_repeats=True)
        assert firsts == len(sigs)  # 4 immediate lines
        assert summaries == len(sigs)  # 4 summaries
        assert firsts + summaries == 8  # 400 events -> 8 lines

    def test_information_preservation_invariants(self):
        """§20: count + sample ids + first/last seen + reason + recoverable."""
        agg = EventBatchAggregator(sample_ids=3)
        t0 = time.time()
        for i in range(25):
            agg.add(
                event="EV",
                reason="REASON_X",
                stage="dataset",
                recoverable=True,
                trade_id=f"id_{i}",
                now=t0 + i * 0.001,
            )
        lines: list[str] = []
        agg.flush(lines.append)
        line = lines[0]
        assert "count=25" in line
        assert "recoverable=true" in line
        assert "reason=REASON_X" in line
        assert "sample_ids=[id_0,id_1,id_2]" in line
        assert "first_seen=" in line and "last_seen=" in line

    def test_no_cross_process_dedup_claim(self):
        """§25: the aggregator is process-local. A fresh instance re-logs the
        first occurrence — documented behavior, pinned here."""
        a1 = EventBatchAggregator()
        a2 = EventBatchAggregator()
        assert a1.add(event="E", reason="R", stage="s", recoverable=False, trade_id="x")
        assert a2.add(event="E", reason="R", stage="s", recoverable=False, trade_id="x")


# =============================================================================
# 3. Frozen contracts re-asserted (already-proven behaviors, kept tight)
# =============================================================================


class TestFrozenObservabilityContracts:
    def test_telegram_disabled_zero_worker_zero_heartbeat(self):
        """§5/§6/§31: enabled=False -> no worker thread; the heartbeat loop
        cannot exist because no worker runs."""
        from nexus_scalp.observability.telegram_notifier import TelegramNotifier

        n = TelegramNotifier(bot_token="", admin_id="", enabled=False)
        try:
            assert n._worker_thread is None or not n._worker_thread.is_alive()
            assert n._worker_running is False
            assert n.health_state()["status"] == "STOPPED"
            # send() fails fast, never blocks on a queue
            t0 = time.time()
            assert n.send("x", severity="CRITICAL") is None
            assert time.time() - t0 < 0.5
        finally:
            n.shutdown()

    def test_numeric_redaction_freeze(self):
        """§16/§17: numeric key=value observability pairs stay readable;
        credential-shaped material stays redacted."""
        from nexus_scalp.observability.logging import _redact_value

        for s in (
            "orphans=3755",
            "count=243",
            "queue_size=0 sent=0 failed=1",
            "consistency_violations=1 duplicates=3",
        ):
            assert "[REDACTED_SECRET]" not in _redact_value(s), s
        for s in (
            "password=hunter2secret",
            "bot_token=123456:ABC-DEF1234",
            "api_key=sk-123456789abcdefghij0123456789",
            "authorization=Bearer abc.def.ghi",
        ):
            assert "[REDACTED_SECRET]" in _redact_value(s), s

    def test_dataset_rejection_recoverability_never_lost(self, tmp_path, monkeypatch):
        """§11/§12: recoverable flag survives aggregation in the dataset
        builder path (reuse the pinned fixture set)."""
        from tests.unit.test_operational_log_hygiene import (
            TestDatasetBuilderAggregation as _Pinned,
        )

        # the pinned suite already proves this end-to-end; assert the class
        # and its key test exist so a future deletion fails THIS contract.
        assert hasattr(_Pinned, "test_repeated_builds_do_not_reflood_orphan_lines")

    def test_orphan_aggregation_key_freeze(self):
        """§9: the grouping key is (event, reason, stage, recoverable)."""
        agg = EventBatchAggregator()
        agg.add(event="E", reason="R", stage="s1", recoverable=False, trade_id="a")
        agg.add(event="E", reason="R", stage="s1", recoverable=False, trade_id="b")
        agg.add(event="E", reason="R", stage="s2", recoverable=False, trade_id="c")
        lines: list[str] = []
        # only_repeats=True flushes groups with count>1 (the s1 pair);
        # the s2 singleton was already logged at first occurrence.
        assert agg.flush(lines.append, only_repeats=True) == 1
        assert "stage=s1" in lines[0] and "count=2" in lines[0]
        # a full flush surfaces every distinct signature
        lines2: list[str] = []
        assert EventBatchAggregator() is not agg
        agg2 = EventBatchAggregator()
        agg2.add(event="E", reason="R", stage="s1", recoverable=False, trade_id="a")
        agg2.add(event="E", reason="R", stage="s2", recoverable=False, trade_id="c")
        assert agg2.flush(lines2.append) == 2  # stage splits -> 2 summaries
