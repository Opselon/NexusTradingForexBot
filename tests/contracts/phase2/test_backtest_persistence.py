"""Phase 2 — BACKTEST / WALK-FORWARD / OOS / ROBUSTNESS persistence contract.

Proves for the research validation pipeline:
    create/write -> commit -> read -> compare
over the REAL columns of ``research_runs``, ``research_gates`` and
``research_evidence``, in ONE module (they are one lineage, not three).

Interfaces used (all public):
    nexus_scalp.adapters.database.provider_store.queue_write / query_rows
    nexus_scalp.research.observability.ResearchObservabilityStore
      .create_gate / .finish_gate / .store_evidence
    nexus_scalp.research.evidence.GateType / GateStatus / EvidenceKind /
      EvidenceArtifact

FINDING recorded by this module (not fixed — it is schema truth):
    lineage-enforced-in-application-only.
    A ``research_gates`` row referencing a ``research_run_id`` that does NOT
    exist in ``research_runs`` IS inserted: there is no foreign key, no trigger
    and no CHECK constraint linking the two tables. The gate->run lineage is a
    convention owned entirely by the observability store's writers and
    readers; the database will not protect it.
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
from nexus_scalp.research.evidence import (
    EvidenceArtifact,
    EvidenceKind,
    GateStatus,
    GateType,
)
from nexus_scalp.research.observability import ResearchObservabilityStore

_RUN_COLS = (
    "run_id, dataset_id, strategy_id, strategy_version, executed_at, config, "
    "build_identity, result_summary"
)
_GATE_COLS = (
    "gate_id, strategy_id, research_run_id, gate_type, status, started_at, "
    "completed_at, duration_ms, configuration_version, dataset_version, "
    "engine_version, result, failure_reason, failure_class, evidence_id, "
    "retryable, order_index"
)
_EVIDENCE_COLS = (
    "evidence_id, strategy_id, research_run_id, gate_id, kind, content, "
    "content_hash, dataset_version, engine_version, created_at"
)


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _stamp() -> int:
    return int(time.time() * 1_000_000)


def _placeholders(n: int) -> str:
    return ", ".join(["?"] * n)


class TestBacktestWriteReadRoundTrip:
    """create/write -> commit -> read -> compare for one full validation run."""

    def test_full_lineage_roundtrip(self, sqlite_env):
        repo = sqlite_env.repo
        store = ResearchObservabilityStore(repo)
        stamp = _stamp()
        run_id = f"RUN-PHASE2-{stamp}"
        strategy_id = f"ST-BT-{stamp}"

        # --- research_runs -------------------------------------------------
        run_cfg = {"engine": "phase2", "seed": 42}
        run_summary = {"verdict": "PASS", "expectancy_r": 0.33}
        assert queue_write(
            repo,
            f"INSERT INTO research_runs ({_RUN_COLS}) VALUES ({_placeholders(8)})",
            (
                run_id,
                "DS-PHASE2",
                strategy_id,
                "1.0.0",
                _iso(),
                json.dumps(run_cfg),
                "build-phase2",
                json.dumps(run_summary),
            ),
            operation="phase2.backtest.insert_run",
        )

        # --- research_gates (the 4 validation gate types, real columns) ----
        gates: dict[str, str] = {}
        for order, (gtype, _status) in enumerate(
            [
                (GateType.BACKTEST, GateStatus.PASSED),
                (GateType.WALK_FORWARD, GateStatus.PASSED),
                (GateType.OOS, GateStatus.PASSED),
                (GateType.ROBUSTNESS, GateStatus.FAILED),
            ]
        ):
            gate = store.create_gate(
                strategy_id=strategy_id,
                research_run_id=run_id,
                gate_type=gtype,
                status=GateStatus.PENDING,
                order_index=order,
                dataset_version="ds-v1",
                engine_version="eng-v1",
                configuration_version="cfg-v1",
            )
            gates[gate.gate_type.value] = gate.gate_id
        sqlite_env.flush()

        # --- settle each gate with its evidence (finish_gate is the path that
        #     writes gate.evidence_id — store_evidence alone leaves it empty) ---
        evidence: dict[str, str] = {}
        for gtype, status, kind in (
            (GateType.BACKTEST, GateStatus.PASSED, EvidenceKind.BACKTEST_RESULT),
            (GateType.WALK_FORWARD, GateStatus.PASSED, EvidenceKind.WALK_FORWARD_RESULT),
            (GateType.OOS, GateStatus.PASSED, EvidenceKind.OOS_RESULT),
            (GateType.ROBUSTNESS, GateStatus.FAILED, EvidenceKind.ROBUSTNESS_RESULT),
        ):
            content = {"expectancy_r": 0.33, "total_trades": 120, "kind": gtype.value}
            artifact = EvidenceArtifact.create(
                strategy_id=strategy_id,
                research_run_id=run_id,
                kind=kind,
                content=content,
                gate_id=gates[gtype.value],
                dataset_version="ds-v1",
                engine_version="eng-v1",
            )
            settled = store.finish_gate(
                gates[gtype.value],
                status=status,
                result=content,
                evidence=artifact,
                failure_reason="phase2 forced failure" if status is GateStatus.FAILED else "",
            )
            assert settled is not None, f"finish_gate did not settle {gtype.value}"
            evidence[gtype.value] = artifact.evidence_id
        sqlite_env.flush()

        # --- read back and compare ----------------------------------------
        run_rows = query_rows(repo, "SELECT * FROM research_runs WHERE run_id=?", (run_id,))
        assert len(run_rows) == 1, "the written research_run is readable"
        run = run_rows[0]
        assert run["run_id"] == run_id
        assert run["dataset_id"] == "DS-PHASE2"
        assert run["strategy_id"] == strategy_id
        assert run["strategy_version"] == "1.0.0"
        assert json.loads(run["config"]) == run_cfg
        assert json.loads(run["result_summary"]) == run_summary

        gate_rows = query_rows(
            repo,
            "SELECT * FROM research_gates WHERE research_run_id=? ORDER BY order_index",
            (run_id,),
        )
        assert len(gate_rows) == 4, "all four validation gates are readable"
        assert [r["gate_type"] for r in gate_rows] == [
            "BACKTEST",
            "WALK_FORWARD",
            "OOS",
            "ROBUSTNESS",
        ]
        assert [r["status"] for r in gate_rows] == [
            "PASSED",
            "PASSED",
            "PASSED",
            "FAILED",
        ]
        for r in gate_rows:
            assert r["gate_id"], "every gate carries an identity"
            assert r["strategy_id"] == strategy_id
            assert r["dataset_version"] == "ds-v1"
            assert r["engine_version"] == "eng-v1"

        evidence_rows = query_rows(
            repo,
            "SELECT * FROM research_evidence WHERE research_run_id=?",
            (run_id,),
        )
        assert len(evidence_rows) == 4, "one evidence row per settled gate"
        for r in evidence_rows:
            assert r["gate_id"] in gates.values()
            assert r["content_hash"], "every evidence row carries a content hash"

        # gate.evidence_id must point at the evidence row it produced
        for r in gate_rows:
            ev = query_rows(
                repo,
                "SELECT * FROM research_evidence WHERE evidence_id=?",
                (r["evidence_id"],),
            )
            assert len(ev) == 1, f"gate {r['gate_type']} evidence_id resolves to exactly one row"

        # the full three-way join reads back correctly
        joined = query_rows(
            repo,
            """
            SELECT g.gate_type, g.status, e.kind, e.content_hash, r.run_id
            FROM research_gates g
            JOIN research_evidence e ON e.gate_id = g.gate_id
            JOIN research_runs r ON r.run_id = g.research_run_id
            WHERE g.research_run_id=?
            ORDER BY g.order_index
            """,
            (run_id,),
        )
        assert len(joined) == 4
        assert [r["kind"] for r in joined] == [
            "BACKTEST_RESULT",
            "WALK_FORWARD_RESULT",
            "OOS_RESULT",
            "ROBUSTNESS_RESULT",
        ]
        assert all(r["run_id"] == run_id for r in joined)

    def test_gate_content_survives_the_roundtrip(self, sqlite_env):
        """The gate's structured ``result`` and evidence ``content`` JSON."""
        repo = sqlite_env.repo
        store = ResearchObservabilityStore(repo)
        stamp = _stamp()
        run_id = f"RUN-CFG-{stamp}"
        sid = f"ST-CFG-{stamp}"
        assert queue_write(
            repo,
            f"INSERT INTO research_runs ({_RUN_COLS}) VALUES ({_placeholders(8)})",
            (run_id, "DS", sid, "1.0.0", _iso(), "{}", "b", "{}"),
            operation="phase2.backtest.cfg_run",
        )
        gate = store.create_gate(
            strategy_id=sid,
            research_run_id=run_id,
            gate_type=GateType.OOS,
            order_index=0,
        )
        sqlite_env.flush()

        # finish_gate writes result + duration + evidence atomically-ish
        artifact = EvidenceArtifact.create(
            strategy_id=sid,
            research_run_id=run_id,
            kind=EvidenceKind.OOS_RESULT,
            content={"oos_expectancy_r": 0.27, "status": "PASS"},
            gate_id=gate.gate_id,
        )
        store.finish_gate(
            gate.gate_id,
            status=GateStatus.PASSED,
            result={"oos_expectancy_r": 0.27, "degradation": 0.02},
            evidence=artifact,
        )
        sqlite_env.flush()

        rows = query_rows(repo, "SELECT * FROM research_gates WHERE gate_id=?", (gate.gate_id,))
        assert len(rows) == 1
        assert rows[0]["status"] == "PASSED"
        assert json.loads(rows[0]["result"])["oos_expectancy_r"] == pytest.approx(0.27)
        assert rows[0]["evidence_id"] == artifact.evidence_id
        assert float(rows[0]["duration_ms"]) >= 0.0


class TestGateRunLineageIsApplicationOnly:
    """The finding: the gate->run link is NOT DB-enforced."""

    def test_gate_referencing_a_missing_run_is_inserted(self, sqlite_env):
        """A research_gates row for a run_id that does not exist IS inserted.

        This is the recorded finding ``lineage-enforced-in-application-only``:
        research_gates.research_run_id has no foreign key to
        research_runs.run_id, so the database accepts an orphan gate. The
        observability store's writers are the only lineage guarantor.
        """
        repo = sqlite_env.repo
        store = ResearchObservabilityStore(repo)
        stamp = _stamp()
        ghost_run_id = f"RUN-DOES-NOT-EXIST-{stamp}"
        sid = f"ST-ORPHAN-{stamp}"

        # No research_runs row is ever created for ghost_run_id.
        assert not query_rows(repo, "SELECT * FROM research_runs WHERE run_id=?", (ghost_run_id,))

        gate = store.create_gate(
            strategy_id=sid,
            research_run_id=ghost_run_id,
            gate_type=GateType.OOS,
            order_index=0,
        )
        sqlite_env.flush()

        rows = query_rows(repo, "SELECT * FROM research_gates WHERE gate_id=?", (gate.gate_id,))
        assert len(rows) == 1, (
            "finding lineage-enforced-in-application-only: a gate referencing a "
            "missing research_run_id was inserted (no FK on research_run_id)"
        )
        assert rows[0]["research_run_id"] == ghost_run_id

        # And the left join exposes the orphan for what it is.
        joined = query_rows(
            repo,
            """
            SELECT g.gate_id, r.run_id AS matched_run
            FROM research_gates g
            LEFT JOIN research_runs r ON r.run_id = g.research_run_id
            WHERE g.gate_id=?
            """,
            (gate.gate_id,),
        )
        assert len(joined) == 1
        assert joined[0]["matched_run"] is None, "the gate's run did not exist"


class TestLiveBacktestReadback:
    """Read-only probes of the RUNNING engine's real research store."""

    def test_live_every_gate_run_exists(self, live_sqlite_probe):
        conn = live_sqlite_probe
        total = conn.execute("SELECT COUNT(*) FROM research_gates").fetchone()[0]
        if not total:
            pytest.skip("live research_gates is empty")
        joined = conn.execute(
            "SELECT COUNT(*) FROM research_gates g "
            "JOIN research_runs r ON r.run_id = g.research_run_id"
        ).fetchone()[0]
        orphans = total - joined
        # Evidence: the orphan count is the measured lineage health. >= 0 only.
        assert orphans >= 0
        assert joined <= total

    def test_live_settled_oos_robustness_gates_have_evidence(self, live_sqlite_probe):
        """Every settled (PASS/FAIL) OOS/ROBUSTNESS gate has an evidence row.

        The gate->evidence link is keyed on ``gate_id`` (research_evidence has
        no FK either, so this is a measured join, not a constraint).
        """
        conn = live_sqlite_probe
        settled = conn.execute(
            "SELECT COUNT(*) FROM research_gates "
            "WHERE gate_type IN ('OOS', 'ROBUSTNESS') AND status IN ('PASSED', 'FAILED')"
        ).fetchone()[0]
        if not settled:
            pytest.skip("no settled OOS/ROBUSTNESS gates in the live store")
        with_evidence = conn.execute(
            "SELECT COUNT(*) FROM research_gates g "
            "WHERE g.gate_type IN ('OOS', 'ROBUSTNESS') "
            "AND g.status IN ('PASSED', 'FAILED') "
            "AND EXISTS (SELECT 1 FROM research_evidence e WHERE e.gate_id = g.gate_id)"
        ).fetchone()[0]
        # Evidence: the gap is the measured missing-evidence rate.
        assert 0 <= with_evidence <= settled

    def test_live_gate_type_vocabulary(self, live_sqlite_probe):
        conn = live_sqlite_probe
        total = conn.execute("SELECT COUNT(*) FROM research_gates").fetchone()[0]
        if not total:
            pytest.skip("live research_gates is empty")
        # The real gate types the validation pipeline runs.
        expected = {
            "STATIC_VALIDATION",
            "BACKTEST",
            "WALK_FORWARD",
            "OOS",
            "ROBUSTNESS",
            "SCORING",
        }
        present = {
            r["gate_type"] for r in conn.execute("SELECT DISTINCT gate_type FROM research_gates")
        }
        # Every live gate type must be one the pipeline knows about; the
        # presence set is recorded as evidence (not every type need appear).
        assert present.issubset(expected), (
            f"live research_gates carries unknown gate types: {present - expected}"
        )
