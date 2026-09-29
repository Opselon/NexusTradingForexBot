"""DEEP-OPT L4 — regression battery: the evidence body must not be re-mirrored.

The #568 lesson generalized (contract Part 5 rule 8/9): a derived representation
must not automatically inherit the complete payload of its source.
``research_evidence.content`` was semantic-identical to ``research_gates.result``
for 18,316 of 18,316 live paired rows (17.17 MB duplicated bytes — 100% of the
column). The gate owns the outcome; the evidence row keeps identity, kind,
content_hash and lineage, and DERIVES the body on read.

These tests fail loudly if the old mirror behavior returns.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import pytest

from nexus_scalp.research.evidence import (
    EvidenceArtifact,
    EvidenceKind,
    GateStatus,
    GateType,
)
from nexus_scalp.research.observability import (
    _EVIDENCE_CONTENT_DERIVED,
    ResearchObservabilityStore,
)

_STAMP = int(time.time() * 1_000_000)


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _seed_run(repo, run_id: str, sid: str) -> None:
    from nexus_scalp.adapters.database.provider_store import queue_write

    assert queue_write(
        repo,
        "INSERT INTO research_runs (run_id, dataset_id, strategy_id, "
        "strategy_version, executed_at, config, build_identity, result_summary) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, "DS-L4", sid, "1.0.0", _iso(), "{}", "b-l4", "{}"),
        operation="l4.evidence_mirror.seed_run",
    )


def _big_backtest_payload(n: int = 240) -> dict[str, object]:
    """A representative large backtest document (the 192KB live shape)."""
    return {
        "strategy_id": f"SF-{n}",
        "dataset_id": f"DS-L4-{n}",
        "total_trades": n,
        "wins": n // 2,
        "losses": n - n // 2,
        "net_pnl_usd": 1234.5 * n,
        "expectancy_r": 0.31,
        "profit_factor": 1.8,
        "equity_curve_r": [round(i * 0.01, 4) for i in range(n)],
    }


class TestEvidenceBodyNotMirrored:
    """38/39: a settled gate must persist ONE copy of its outcome body."""

    def test_evidence_row_carries_the_derivation_sentinel(self, sqlite_env):
        repo = sqlite_env.repo
        store = ResearchObservabilityStore(repo)
        sid = f"ST-L4A-{_STAMP}"
        run_id = f"RUN-L4A-{_STAMP}"
        _seed_run(repo, run_id, sid)

        gate = store.create_gate(
            strategy_id=sid,
            research_run_id=run_id,
            gate_type=GateType.BACKTEST,
            order_index=1,
        )
        sqlite_env.flush()
        body = _big_backtest_payload()
        store.finish_gate(
            gate.gate_id,
            status=GateStatus.PASSED,
            result=body,
            evidence=EvidenceArtifact.create(
                sid,
                run_id,
                EvidenceKind.BACKTEST_RESULT,
                body,
                gate_id=gate.gate_id,
                dataset_version="ds-l4",
            ),
        )
        sqlite_env.flush()

        from nexus_scalp.adapters.database.provider_store import query_rows

        rows = query_rows(
            repo, "SELECT content FROM research_evidence WHERE gate_id=?", (gate.gate_id,)
        )
        assert len(rows) == 1, "one evidence row per settled gate"
        stored = rows[0]["content"]
        # The mirror body must NOT be persisted — the sentinel is.
        assert stored != json.dumps(body)
        assert stored == _EVIDENCE_CONTENT_DERIVED, (
            "evidence.content must carry the derivation sentinel, not a copy of "
            "the gate result (DEEP-OPT L4; the old behavior duplicated 17.17 MB)"
        )

    def test_big_payload_does_not_create_a_duplicate_body(self, sqlite_env):
        """45: a large payload must not materialize a full JSON mirror."""
        repo = sqlite_env.repo
        store = ResearchObservabilityStore(repo)
        sid = f"ST-L4B-{_STAMP}"
        run_id = f"RUN-L4B-{_STAMP}"
        _seed_run(repo, run_id, sid)
        gate = store.create_gate(
            strategy_id=sid,
            research_run_id=run_id,
            gate_type=GateType.OOS,
            order_index=1,
        )
        sqlite_env.flush()
        body = _big_backtest_payload(900)
        store.finish_gate(
            gate.gate_id,
            status=GateStatus.PASSED,
            result=body,
            evidence=EvidenceArtifact.create(
                sid,
                run_id,
                EvidenceKind.OOS_RESULT,
                body,
                gate_id=gate.gate_id,
            ),
        )
        sqlite_env.flush()

        from nexus_scalp.adapters.database.provider_store import query_rows

        ev = query_rows(
            repo, "SELECT content FROM research_evidence WHERE gate_id=?", (gate.gate_id,)
        )
        gate_rows = query_rows(
            repo, "SELECT result FROM research_gates WHERE gate_id=?", (gate.gate_id,)
        )
        assert len(ev) == 1 and len(gate_rows) == 1
        big = json.dumps(body)
        # Exactly ONE place holds the big document.
        assert ev[0]["content"] != big
        assert json.loads(gate_rows[0]["result"]) == body, "the gate keeps the outcome"

    def test_read_derives_the_body_from_the_gate(self, sqlite_env):
        """31/39: the read path still returns the full content (LOSSLESS)."""
        repo = sqlite_env.repo
        store = ResearchObservabilityStore(repo)
        sid = f"ST-L4C-{_STAMP}"
        run_id = f"RUN-L4C-{_STAMP}"
        _seed_run(repo, run_id, sid)
        gate = store.create_gate(
            strategy_id=sid,
            research_run_id=run_id,
            gate_type=GateType.ROBUSTNESS,
            order_index=1,
        )
        sqlite_env.flush()
        body = _big_backtest_payload(64)
        artifact = EvidenceArtifact.create(
            sid,
            run_id,
            EvidenceKind.ROBUSTNESS_RESULT,
            body,
            gate_id=gate.gate_id,
        )
        store.finish_gate(
            gate.gate_id,
            status=GateStatus.PASSED,
            result=body,
            evidence=artifact,
        )
        sqlite_env.flush()

        got = store.get_evidence(artifact.evidence_id)
        assert got is not None
        assert got["content"] == body, "derived content equals the gate outcome"
        assert got["content_hash"] == artifact.content_hash
        assert got["evidence_id"] == artifact.evidence_id

        listed = store.list_evidence(strategy_id=sid, limit=10)
        assert any(r["evidence_id"] == artifact.evidence_id for r in listed)
        match = next(r for r in listed if r["evidence_id"] == artifact.evidence_id)
        assert match["content"] == body, "list_evidence also derives the body"


class TestLegacyEvidenceStillReads:
    """86 rollback: rows written BEFORE the fix carry a real body and survive."""

    def test_legacy_body_row_reads_untouched(self, sqlite_env):
        from nexus_scalp.adapters.database.provider_store import query_rows, queue_write

        repo = sqlite_env.repo
        sid = f"ST-L4D-{_STAMP}"
        run_id = f"RUN-L4D-{_STAMP}"
        _seed_run(repo, run_id, sid)
        legacy_body = {"legacy": True, "total_trades": 7}
        assert queue_write(
            repo,
            "INSERT INTO research_evidence (evidence_id, strategy_id, "
            "research_run_id, gate_id, kind, content, content_hash, "
            "dataset_version, engine_version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"EV-LEGACY-{_STAMP}",
                sid,
                run_id,
                "",
                "BACKTEST_RESULT",
                json.dumps(legacy_body),
                "deadbeef",
                "ds-l4",
                "eng-l4",
                _iso(),
            ),
            operation="l4.evidence_mirror.legacy_seed",
        )
        sqlite_env.flush()
        store = ResearchObservabilityStore(repo)
        got = store.get_evidence(f"EV-LEGACY-{_STAMP}")
        assert got is not None
        assert got["content"] == legacy_body, (
            "a legacy row with no gate_id keeps its own body verbatim"
        )


class TestNegativeRedundancy:
    """43: forcing the old path cannot recreate the duplicate."""

    def test_duplicate_event_retry_creates_one_body(self, sqlite_env):
        """Retrying the same settle does not write the mirror twice."""
        repo = sqlite_env.repo
        store = ResearchObservabilityStore(repo)
        sid = f"ST-L4E-{_STAMP}"
        run_id = f"RUN-L4E-{_STAMP}"
        _seed_run(repo, run_id, sid)
        gate = store.create_gate(
            strategy_id=sid,
            research_run_id=run_id,
            gate_type=GateType.SCORING,
            order_index=1,
        )
        sqlite_env.flush()
        body = _big_backtest_payload(32)
        artifact = EvidenceArtifact.create(
            sid,
            run_id,
            EvidenceKind.SCORE_RESULT,
            body,
            gate_id=gate.gate_id,
        )
        for _ in range(3):
            store.finish_gate(
                gate.gate_id,
                status=GateStatus.PASSED,
                result=body,
                evidence=artifact,
            )
        sqlite_env.flush()

        from nexus_scalp.adapters.database.provider_store import query_rows

        rows = query_rows(
            repo, "SELECT content FROM research_evidence WHERE gate_id=?", (gate.gate_id,)
        )
        assert len(rows) == 1, "re-settling is idempotent"
        assert rows[0]["content"] == _EVIDENCE_CONTENT_DERIVED
        # And the gate still holds exactly one copy.
        gr = query_rows(repo, "SELECT result FROM research_gates WHERE gate_id=?", (gate.gate_id,))
        assert json.loads(gr[0]["result"]) == body

    def test_two_gates_share_content_but_do_not_share_rows(self, sqlite_env):
        """Different gates for the same strategy each keep their own row.

        ``EvidenceArtifact.create`` derives ``evidence_id`` from a content hash
        (``EV-<sha12>``), so two gates carrying the SAME body map to one evidence
        row by identity — a pre-existing identity property, not a regression.
        Distinct outcomes must each land their own row with no fan-out waste.
        """
        repo = sqlite_env.repo
        store = ResearchObservabilityStore(repo)
        sid = f"ST-L4F-{_STAMP}"
        run_id = f"RUN-L4F-{_STAMP}"
        _seed_run(repo, run_id, sid)
        gate_ids = []
        for order, gtype in enumerate((GateType.BACKTEST, GateType.OOS)):
            g = store.create_gate(
                strategy_id=sid,
                research_run_id=run_id,
                gate_type=gtype,
                order_index=order,
            )
            gate_ids.append(g.gate_id)
        sqlite_env.flush()
        bodies = (_big_backtest_payload(48), _big_backtest_payload(96))
        for gtype, gid, body in zip(
            (GateType.BACKTEST, GateType.OOS), gate_ids, bodies, strict=True
        ):
            kind = (
                EvidenceKind.BACKTEST_RESULT
                if gtype is GateType.BACKTEST
                else EvidenceKind.OOS_RESULT
            )
            store.finish_gate(
                gid,
                status=GateStatus.PASSED,
                result=body,
                evidence=EvidenceArtifact.create(sid, run_id, kind, body, gate_id=gid),
            )
        sqlite_env.flush()

        from nexus_scalp.adapters.database.provider_store import query_rows

        n = query_rows(
            repo,
            "SELECT COUNT(*) AS c FROM research_evidence WHERE research_run_id=?",
            (run_id,),
        )[0]["c"]
        assert n == 2, "one evidence row per distinct gate outcome"
        # Every evidence row derives from ITS OWN gate.
        for gid, body in zip(gate_ids, bodies, strict=True):
            ev = store.get_evidence(
                query_rows(
                    repo,
                    "SELECT evidence_id FROM research_evidence WHERE gate_id=?",
                    (gid,),
                )[0]["evidence_id"]
            )
            assert ev is not None and ev["content"] == body
