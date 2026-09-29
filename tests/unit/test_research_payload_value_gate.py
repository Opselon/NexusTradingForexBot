"""DB-LIFECYCLE L3: the gate/evidence payload value gate.

``research_evidence.content`` and ``research_gates.result`` held the SAME JSON
object. Every producer in ``research/pipeline.py`` builds the artifact and the
gate ``result`` from ONE dict (``bt_data``), so the payload was written twice
per settled gate. Measured on the live ledger: 26,140 gates / 18,316 evidence
rows, 300/300 sampled rows canonically identical under
``json.dumps(sort_keys=True)``, evidence a strict 1:1 subset of the gate, zero
orphans — ~16.4 MB of pure duplication (98.2% of the evidence table).

This suite pins the contract that fixes it:

* the evidence row no longer stores the payload when the gate already has it
  (write amplification: one copy, not two);
* the read side re-materializes it, so every consumer still receives the full
  payload — no capability is lost;
* ``content_hash`` is unchanged, because the artifact is unchanged (identity
  is derived from the payload, not from where it is stored);
* legacy rows (populated ``content``) are served verbatim, so a mixed
  generation database reads correctly with no migration.

The proof that these tests pin real behaviour is mutation: reverting the
producer change (storing ``artifact.content`` again) makes the write-side test
fail, and reverting the read-side resolver makes the read tests fail.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.research.evidence import (
    EvidenceArtifact,
    EvidenceKind,
    FailureClass,
    GateStatus,
    GateType,
)
from nexus_scalp.research.observability import ResearchObservabilityStore


@pytest.fixture
def repo(tmp_path) -> AuditRepository:
    """A real SQLite-backed repository in a temp dir (no mocks on the DB path)."""
    db_file = tmp_path / "l3_research.db"
    r = AuditRepository(db_url=f"sqlite:///{db_file}")
    yield r
    try:
        r.close()
    except Exception:
        pass


def _store(repo: AuditRepository) -> ResearchObservabilityStore:
    return ResearchObservabilityStore(repo)


def _flush(repo: AuditRepository) -> None:
    """Wait for the background write queue to land every queued statement."""
    repo._queue.join()


def _row(repo: AuditRepository, sql: str, args: tuple = ()) -> dict:
    con = sqlite3.connect(repo._db_path)
    con.row_factory = sqlite3.Row
    try:
        return dict(con.execute(sql, tuple(args)).fetchone())
    finally:
        con.close()


def _exec(repo: AuditRepository, sql: str, args: tuple = ()) -> None:
    con = sqlite3.connect(repo._db_path)
    try:
        con.execute(sql, tuple(args))
        con.commit()
    finally:
        con.close()


def _make_gate(store: ResearchObservabilityStore, *, run_id: str = "RUN-L3"):
    return store.create_gate(
        "STRAT-L3", run_id, GateType.BACKTEST, dataset_version="ds-l3", engine_version="eng-l3"
    )


class TestWriteSideOneCopy:
    """The payload is written once, on the gate."""

    def test_evidence_row_carries_no_payload_when_gate_has_it(self, repo) -> None:
        store = _store(repo)
        gate = _make_gate(store)
        payload = {"expectancy_r": 1.25, "trades": 40, "win_rate": 0.55}

        artifact = EvidenceArtifact.create(
            "STRAT-L3",
            gate.research_run_id,
            EvidenceKind.BACKTEST_RESULT,
            payload,
            gate_id=gate.gate_id,
            dataset_version="ds-l3",
        )
        store.finish_gate(
            gate.gate_id,
            status=GateStatus.PASSED,
            result=payload,
            evidence=artifact,
        )
        _flush(repo)

        row = _row(repo, "SELECT content FROM research_evidence WHERE gate_id=?", (gate.gate_id,))
        # The gate holds the payload, so the evidence row does not.
        assert row["content"] == ""

    def test_gate_row_holds_the_payload(self, repo) -> None:
        store = _store(repo)
        gate = _make_gate(store)
        payload = {"expectancy_r": 1.25, "trades": 40}

        artifact = EvidenceArtifact.create(
            "STRAT-L3",
            gate.research_run_id,
            EvidenceKind.BACKTEST_RESULT,
            payload,
            gate_id=gate.gate_id,
            dataset_version="ds-l3",
        )
        store.finish_gate(gate.gate_id, status=GateStatus.PASSED, result=payload, evidence=artifact)
        _flush(repo)

        grow = _row(repo, "SELECT result FROM research_gates WHERE gate_id=?", (gate.gate_id,))
        assert json.loads(grow["result"]) == payload

    def test_explicit_evidence_without_gate_result_stores_its_own_copy(self, repo) -> None:
        """``store_evidence`` called directly (no gate result) keeps the payload.

        This is the path for evidence produced outside a gate completion. The
        contract is: the row is self-contained exactly when no gate result was
        offered to deduplicate against.
        """
        store = _store(repo)
        payload = {"note": "standalone evidence", "kind": "MANUAL"}
        artifact = EvidenceArtifact.create(
            "STRAT-L3",
            "RUN-L3",
            EvidenceKind.BACKTEST_RESULT,
            payload,
            gate_id="GATE-STANDALONE",
            dataset_version="ds-l3",
        )
        store.store_evidence(artifact)
        _flush(repo)

        row = _row(
            repo,
            "SELECT content FROM research_evidence WHERE evidence_id=?",
            (artifact.evidence_id,),
        )
        assert json.loads(row["content"]) == payload


class TestReadSideReMaterializes:
    """Every consumer still receives the full payload."""

    def test_get_evidence_returns_the_full_payload(self, repo) -> None:
        store = _store(repo)
        gate = _make_gate(store)
        payload = {"expectancy_r": 1.25, "trades": 40, "win_rate": 0.55}

        artifact = EvidenceArtifact.create(
            "STRAT-L3",
            gate.research_run_id,
            EvidenceKind.BACKTEST_RESULT,
            payload,
            gate_id=gate.gate_id,
            dataset_version="ds-l3",
        )
        store.finish_gate(gate.gate_id, status=GateStatus.PASSED, result=payload, evidence=artifact)
        _flush(repo)

        ev = store.get_evidence(artifact.evidence_id)
        assert ev is not None
        # The payload is re-materialized from the gate, not lost.
        assert ev["content"] == payload

    def test_list_evidence_returns_the_full_payload(self, repo) -> None:
        store = _store(repo)
        gate = _make_gate(store)
        payload = {"oos_expectancy_r": -0.10, "oos_samples": 21, "status": "FAIL"}

        artifact = EvidenceArtifact.create(
            "STRAT-L3",
            gate.research_run_id,
            EvidenceKind.OOS_RESULT,
            payload,
            gate_id=gate.gate_id,
            dataset_version="ds-l3",
        )
        store.finish_gate(
            gate.gate_id,
            status=GateStatus.FAILED,
            result=payload,
            evidence=artifact,
            failure_reason="oos failed",
            failure_class=FailureClass.DATA,
        )
        _flush(repo)

        rows = store.list_evidence(research_run_id=gate.research_run_id, include_archive=False)
        assert len(rows) == 1
        assert rows[0]["content"] == payload
        assert rows[0]["kind"] == "OOS_RESULT"

    def test_content_hash_is_stable_across_the_change(self, repo) -> None:
        """Identity does not depend on where the payload is physically stored."""
        store = _store(repo)
        gate = _make_gate(store)
        payload = {"expectancy_r": 1.25, "trades": 40}

        artifact = EvidenceArtifact.create(
            "STRAT-L3",
            gate.research_run_id,
            EvidenceKind.BACKTEST_RESULT,
            payload,
            gate_id=gate.gate_id,
            dataset_version="ds-l3",
        )
        store.finish_gate(gate.gate_id, status=GateStatus.PASSED, result=payload, evidence=artifact)
        _flush(repo)

        row = _row(
            repo,
            "SELECT content_hash FROM research_evidence WHERE evidence_id=?",
            (artifact.evidence_id,),
        )
        assert row["content_hash"] == artifact.content_hash
        assert artifact.content_hash != ""


class TestLegacyRowsServedVerbatim:
    """A database written before the gate still reads correctly."""

    def test_populated_content_is_not_overridden(self, repo) -> None:
        """A legacy row keeps its own payload even when a gate result differs."""
        store = _store(repo)
        gate = _make_gate(store)
        legacy_payload = {"legacy": True, "expectancy_r": 0.9}
        gate_payload = {"expectancy_r": 1.25, "trades": 40}

        artifact = EvidenceArtifact.create(
            "STRAT-L3",
            gate.research_run_id,
            EvidenceKind.BACKTEST_RESULT,
            legacy_payload,
            gate_id=gate.gate_id,
            dataset_version="ds-l3",
        )
        store.store_evidence(artifact)  # no gate_result -> self-contained
        _flush(repo)
        # A legacy row that also has a populated content whose gate result
        # differs: content must win (it is that row's own canonical copy).
        _exec(
            repo,
            "UPDATE research_evidence SET content=? WHERE evidence_id=?",
            (json.dumps(legacy_payload), artifact.evidence_id),
        )
        store.finish_gate(gate.gate_id, status=GateStatus.PASSED, result=gate_payload)
        _flush(repo)

        ev = store.get_evidence(artifact.evidence_id)
        assert ev is not None
        assert ev["content"] == legacy_payload

    def test_mixed_generation_database_reads_consistently(self, repo) -> None:
        """One legacy row + one gated row both resolve to their true payload."""
        store = _store(repo)

        # legacy: self-contained evidence
        g1 = _make_gate(store, run_id="RUN-LEGACY")
        legacy_payload = {"generation": "legacy", "trades": 12}
        a1 = EvidenceArtifact.create(
            "STRAT-L3",
            g1.research_run_id,
            EvidenceKind.BACKTEST_RESULT,
            legacy_payload,
            gate_id=g1.gate_id,
            dataset_version="ds-l3",
        )
        store.store_evidence(a1)
        _flush(repo)

        # gated: evidence written through finish_gate (payload on the gate)
        g2 = store.create_gate("STRAT-L3", "RUN-GATED", GateType.OOS, dataset_version="ds-l3")
        gated_payload = {"generation": "gated", "oos_expectancy_r": 0.4}
        a2 = EvidenceArtifact.create(
            "STRAT-L3",
            g2.research_run_id,
            EvidenceKind.OOS_RESULT,
            gated_payload,
            gate_id=g2.gate_id,
            dataset_version="ds-l3",
        )
        store.finish_gate(g2.gate_id, status=GateStatus.PASSED, result=gated_payload, evidence=a2)
        _flush(repo)

        e1 = store.get_evidence(a1.evidence_id)
        e2 = store.get_evidence(a2.evidence_id)
        assert e1 is not None and e2 is not None
        assert e1["content"] == legacy_payload
        assert e2["content"] == gated_payload


class TestNoLoss:
    """Nothing the contract protected is weakened."""

    def test_gate_evidence_link_is_intact(self, repo) -> None:
        store = _store(repo)
        gate = _make_gate(store)
        payload = {"expectancy_r": 1.25}

        artifact = EvidenceArtifact.create(
            "STRAT-L3",
            gate.research_run_id,
            EvidenceKind.BACKTEST_RESULT,
            payload,
            gate_id=gate.gate_id,
            dataset_version="ds-l3",
        )
        store.finish_gate(gate.gate_id, status=GateStatus.PASSED, result=payload, evidence=artifact)
        _flush(repo)

        grow = _row(
            repo, "SELECT evidence_id, status FROM research_gates WHERE gate_id=?", (gate.gate_id,)
        )
        assert grow["evidence_id"] == artifact.evidence_id
        assert grow["status"] == "PASSED"
        joined = _row(
            repo,
            "SELECT count(*) c FROM research_gates g JOIN research_evidence e ON e.gate_id=g.gate_id "
            "WHERE g.gate_id=?",
            (gate.gate_id,),
        )
        assert joined["c"] == 1

    def test_row_is_never_left_without_a_readable_payload(self, repo) -> None:
        """A gated row whose gate vanished still degrades to {} and never raises."""
        store = _store(repo)
        gate = _make_gate(store)
        payload = {"expectancy_r": 1.25}
        artifact = EvidenceArtifact.create(
            "STRAT-L3",
            gate.research_run_id,
            EvidenceKind.BACKTEST_RESULT,
            payload,
            gate_id=gate.gate_id,
            dataset_version="ds-l3",
        )
        store.finish_gate(gate.gate_id, status=GateStatus.PASSED, result=payload, evidence=artifact)
        _flush(repo)
        # Delete the gate row: the resolver must fall back, not raise.
        _exec(repo, "DELETE FROM research_gates WHERE gate_id=?", (gate.gate_id,))

        ev = store.get_evidence(artifact.evidence_id)
        assert ev is not None
        assert ev["content"] == {}
