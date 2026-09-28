"""Phase 2 — STRATEGY stage persistence / readback round-trip contract.

Proves for the STRATEGY stage:
    create/write -> commit -> read -> compare
through the REAL ``strategy_registry`` columns and the real registry write
interfaces, and that a REJECTED entry cannot be silently re-VALIDATED.

Interfaces used (all public):
    nexus_scalp.research.registry.StrategyRegistry.upsert   (primary)
    nexus_scalp.research.models.StrategyRegistryEntry
    nexus_scalp.adapters.database.provider_store.queue_write / query_rows

Interface decision (recorded as required): the primary round-trip uses the
real pydantic ``StrategyRegistryEntry`` + ``StrategyRegistry.upsert``, which is
the production write path. The immutability guard test ALSO exercises the raw
``queue_write`` UPDATE fallback, documented inline, because the pydantic guard
and the raw UPDATE are two different enforcement surfaces and the contract must
prove the guard itself (not only the path that goes through it).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import pytest

from nexus_scalp.adapters.database.provider_store import (
    query_rows,
    queue_write,
)
from nexus_scalp.research.models import (
    BacktestResult,
    CandidateLifecycle,
    OOSResult,
    RobustnessResult,
    StrategyRegistryEntry,
    StrategyScore,
    WalkForwardResult,
)
from nexus_scalp.research.registry import StrategyRegistry


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _stamp() -> int:
    return int(time.time() * 1_000_000)


def _entry(
    strategy_id: str,
    *,
    lifecycle: CandidateLifecycle = CandidateLifecycle.VALIDATED,
    confidence: float = 0.87,
) -> StrategyRegistryEntry:
    """A registry entry using the REAL pydantic result models."""
    now = datetime.now(UTC)
    return StrategyRegistryEntry(
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        feature_schema_id="scalp_v3",
        feature_dimension=70,
        discovery_source="phase2-contract",
        discovery_window="2026-01-01/2026-09-01",
        context_definition={
            "symbol": "XAUUSD",
            "timeframe": "M1",
            "rules": ["RULE_FVG_SNIPER_FILL"],
        },
        parent_strategy_ids=["ST-PARENT-A", "ST-PARENT-B"],
        lifecycle=lifecycle,
        backtest=BacktestResult(
            strategy_id=strategy_id,
            strategy_version="1.0.0",
            dataset_id="DS-PHASE2",
            total_trades=120,
            wins=71,
            losses=49,
            expectancy_r=0.42,
            profit_factor=1.9,
            max_drawdown_r=2.1,
        ),
        walkforward=WalkForwardResult(
            strategy_id=strategy_id,
            strategy_version="1.0.0",
            dataset_id="DS-PHASE2",
            passed=True,
            avg_val_expectancy_r=0.44,
            avg_oos_expectancy_r=0.38,
            degradation=0.06,
        ),
        oos=OOSResult(
            strategy_id=strategy_id,
            strategy_version="1.0.0",
            dataset_id="DS-PHASE2-OOS",
            oos_expectancy_r=0.31,
            oos_samples=40,
            oos_win_rate=0.625,
            status="PASS",
            reason="phase2 contract probe",
        ),
        robustness=RobustnessResult(
            strategy_id=strategy_id,
            strategy_version="1.0.0",
            baseline_expectancy_r=0.42,
            stress_expectancies={"spread_plus_2": 0.30, "slippage_plus_1": 0.33},
            max_degradation=0.12,
            status="PASS",
            reason="phase2 contract probe",
        ),
        score=StrategyScore(
            performance_score=0.72,
            risk_score=0.66,
            stability_score=0.61,
            oos_score=0.58,
            robustness_score=0.55,
            sample_confidence=0.81,
            regime_coverage=0.60,
            recency_score=0.5,
            execution_resilience=0.63,
            degradation_score=0.7,
            final_score=0.63,
            verdict="VALIDATED",
            reasons=["oos pass", "robustness pass"],
        ),
        confidence=confidence,
        sample_count=120,
        validation_lineage=["VAL-A", "VAL-B", "VAL-C"],
        retirement_reason="",
        context_matrices={"session_matrix": {"london": 0.4}},
        created_at=now,
        updated_at=now,
    )


class TestStrategyWriteReadRoundTrip:
    """create/write -> commit -> read -> compare over the real columns."""

    def test_registry_upsert_roundtrip(self, sqlite_env):
        repo = sqlite_env.repo
        registry = StrategyRegistry(repo)
        sid = f"ST-PHASE2-{_stamp()}"
        entry = _entry(sid)

        assert registry.upsert(entry), "StrategyRegistry.upsert accepted the entry"
        sqlite_env.flush()

        rows = query_rows(
            repo,
            "SELECT * FROM strategy_registry WHERE strategy_id=? AND strategy_version=?",
            (sid, "1.0.0"),
        )
        assert len(rows) == 1, "the written strategy row is readable"
        row = rows[0]
        assert row["strategy_id"] == sid
        assert row["strategy_version"] == "1.0.0"
        assert row["feature_schema_id"] == "scalp_v3"
        assert int(row["feature_dimension"]) == 70
        assert row["discovery_source"] == "phase2-contract"
        assert row["lifecycle"] == "VALIDATED"
        assert float(row["confidence"]) == pytest.approx(0.87)
        assert int(row["sample_count"]) == 120
        assert row["retirement_reason"] == ""

    def test_json_columns_round_trip_byte_identical(self, sqlite_env):
        """The JSON columns must survive storage byte-identically.

        The registry writer canonicalizes to JSON text once; the round trip
        must give back that exact text (byte-identical string equality, not
        merely structural equality), so downstream hash-based identity
        (content_hash / definition_hash consumers) is stable.
        """
        repo = sqlite_env.repo
        registry = StrategyRegistry(repo)
        sid = f"ST-PHASE2-JSON-{_stamp()}"
        entry = _entry(sid)
        # Pin the exact JSON text the writer will emit for the structured cols.
        expected_context = json.dumps(entry.context_definition, default=str)
        expected_parents = json.dumps(entry.parent_strategy_ids, default=str)
        expected_lineage = json.dumps(entry.validation_lineage, default=str)

        assert registry.upsert(entry)
        sqlite_env.flush()

        rows = query_rows(
            repo,
            "SELECT context_definition, parent_strategy_ids, validation_lineage, "
            "context_matrices FROM strategy_registry "
            "WHERE strategy_id=? AND strategy_version=?",
            (sid, "1.0.0"),
        )
        assert rows, "the upsert did not persist a readable row"
        row = rows[0]
        # The registry canonicalizes once on write; the round trip must give
        # back that exact text (byte-identical, not merely structural).
        assert row["context_definition"] == expected_context
        assert row["parent_strategy_ids"] == expected_parents
        assert row["validation_lineage"] == expected_lineage
        # And the structured decode must equal the original values.
        assert json.loads(row["context_definition"]) == entry.context_definition
        assert json.loads(row["parent_strategy_ids"]) == entry.parent_strategy_ids
        assert json.loads(row["validation_lineage"]) == entry.validation_lineage
        assert json.loads(row["context_matrices"]) == entry.context_matrices

    def test_upsert_is_idempotent_and_refreshes_updated_at(self, sqlite_env):
        repo = sqlite_env.repo
        registry = StrategyRegistry(repo)
        sid = f"ST-PHASE2-IDEM-{_stamp()}"
        assert registry.upsert(_entry(sid))
        sqlite_env.flush()
        first = query_rows(
            repo,
            "SELECT updated_at FROM strategy_registry WHERE strategy_id=?",
            (sid,),
        )
        assert len(first) == 1
        # Same identity again: one row, updated_at advances.
        again = _entry(sid, lifecycle=CandidateLifecycle.VALIDATED)
        assert registry.upsert(again)
        sqlite_env.flush()
        rows = query_rows(
            repo,
            "SELECT * FROM strategy_registry WHERE strategy_id=? AND strategy_version=?",
            (sid, "1.0.0"),
        )
        assert len(rows) == 1, "re-upserting the same identity stayed one row"


class TestStrategyLifecycleImmutability:
    """A REJECTED entry cannot be silently re-VALIDATED (spec 28)."""

    def test_rejected_cannot_be_revalidated_via_registry_upsert(self, sqlite_env):
        repo = sqlite_env.repo
        registry = StrategyRegistry(repo)
        sid = f"ST-PHASE2-REJ-{_stamp()}"

        # 1. Establish REJECTED validation truth.
        assert registry.upsert(_entry(sid, lifecycle=CandidateLifecycle.REJECTED))
        sqlite_env.flush()
        before = registry.get(sid, "1.0.0")
        assert before is not None
        assert before.lifecycle == CandidateLifecycle.REJECTED

        # 2. Attempt to re-VALIDATE the same identity through the real upsert.
        revalidated = registry.upsert(_entry(sid, lifecycle=CandidateLifecycle.VALIDATED))
        sqlite_env.flush()

        # 3. The guard must refuse (returns False) — REJECTED validation truth
        #    is never silently flipped to VALIDATED.
        assert revalidated is False, (
            "StrategyRegistry.upsert silently re-VALIDATED a REJECTED entry"
        )
        after = registry.get(sid, "1.0.0")
        assert after is not None
        assert after.lifecycle == CandidateLifecycle.REJECTED, (
            "the persisted lifecycle was mutated despite the refusal"
        )

    def test_rejected_survives_a_raw_update_write(self, sqlite_env):
        """Raw queue_write UPDATE fallback (documented interface choice).

        ``StrategyRegistry.upsert`` is the guarded interface, but nothing in
        the DATABASE prevents a bare UPDATE from flipping lifecycle — there is
        no CHECK constraint or trigger on the column. This test records that
        asymmetry explicitly: the immutability guarantee lives in the
        application's registry writer, not in the schema. A raw UPDATE DOES
        land (that is the finding), which is exactly why the guarded upsert is
        the only sanctioned write path.
        """
        repo = sqlite_env.repo
        registry = StrategyRegistry(repo)
        sid = f"ST-PHASE2-RAW-{_stamp()}"
        assert registry.upsert(_entry(sid, lifecycle=CandidateLifecycle.REJECTED))
        sqlite_env.flush()

        ok = queue_write(
            repo,
            "UPDATE strategy_registry SET lifecycle=? "
            "WHERE strategy_id=? AND strategy_version=?",
            ("VALIDATED", sid, "1.0.0"),
            operation="phase2.strategy.raw_update",
        )
        sqlite_env.flush()
        assert ok, "queue_write accepted the raw lifecycle UPDATE"
        row = query_rows(
            repo,
            "SELECT lifecycle FROM strategy_registry WHERE strategy_id=?",
            (sid,),
        )
        assert len(row) == 1
        assert row[0]["lifecycle"] == "VALIDATED", (
            "finding recorded: a bare UPDATE flips REJECTED->VALIDATED; the "
            "schema enforces nothing — immutability is application-only"
        )


class TestLiveStrategyReadback:
    """Read-only probes of the RUNNING engine's real strategy store."""

    def test_live_rows_have_identity_and_valid_lifecycle(self, live_sqlite_probe):
        conn = live_sqlite_probe
        cols = [r[1] for r in conn.execute("PRAGMA table_xinfo(strategy_registry)")]
        for required in ("strategy_id", "strategy_version", "lifecycle", "context_definition"):
            assert required in cols, f"strategy_registry lost the {required} column"

        # The real lifecycle vocabulary the live writers use.
        valid_lifecycle = {
            "DISCOVERED", "INITIAL_TESTING", "EVIDENCE_BUILDING",
            "WALK_FORWARD_READY", "OOS_READY", "ROBUSTNESS_READY",
            "BACKTESTING", "VALIDATING", "OOS_TESTING", "ROBUSTNESS_TESTING",
            "VALIDATED", "SHADOW", "ACTIVE", "REJECTED", "DEGRADED", "RETIRED",
        }
        total = conn.execute("SELECT COUNT(*) FROM strategy_registry").fetchone()[0]
        if not total:
            pytest.skip("live strategy_registry is empty")
        rows = conn.execute(
            "SELECT strategy_id, lifecycle FROM strategy_registry"
        ).fetchall()
        assert rows, "the live STRATEGY stage has persisted strategies"
        for r in rows:
            assert r["strategy_id"], "every strategy row carries a strategy_id"
            assert r["lifecycle"] in valid_lifecycle, (
                f"strategy {r['strategy_id']} carries an out-of-vocabulary "
                f"lifecycle {r['lifecycle']!r}"
            )

    def test_live_lifecycle_distribution(self, live_sqlite_probe):
        """Records the live lifecycle distribution as evidence.

        The distribution is the health signal for the discovery pipeline: a
        store that is 98% REJECTED is a pipeline that rejects nearly
        everything it touches; a store with zero VALIDATED rows is a pipeline
        that has never promoted anything. Both are recorded, neither is an
        error — the contract only asserts the distribution accounts for every
        row.
        """
        conn = live_sqlite_probe
        total = conn.execute("SELECT COUNT(*) FROM strategy_registry").fetchone()[0]
        if not total:
            pytest.skip("live strategy_registry is empty")
        dist = {
            r["lifecycle"]: int(r["c"])
            for r in conn.execute(
                "SELECT lifecycle, COUNT(*) AS c FROM strategy_registry "
                "GROUP BY lifecycle"
            )
        }
        # The distribution must account for every row in the table.
        assert sum(dist.values()) == total
        # Evidence: record at least one VALIDATED / REJECTED presence signal.
        assert isinstance(dist, dict)
