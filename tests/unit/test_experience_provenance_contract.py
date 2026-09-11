"""EXP-PROV-1 regression tests (Agent 8, 2026-09-11 cross-lane handoff).

Contract under test (two-representation experience snapshot, no new schema):

  * record.provenance              = the SERVING model identity (may be the
    70D scalp_v3 contract) — never rewritten by the snapshot;
  * record.feature_snapshot        = the CAPTURED base-family tensor
    (``fv.to_tensor_input()`` — the protected scalp_v1 base 0..49 per
    features/schema_contract.py), labeled with the registry schema that
    matches the CAPTURED width.

The proven defect: under a 70D champion the writer stamped
``feature_snapshot.feature_schema_id="scalp_v3"`` (registry: 70D) onto a
50-value snapshot — an impossible (schema, dimension, len(values)) triple
that violated the coexistence contract pinned by test_experience_intelligence
test_33 and made every forensic reader mis-resolve the snapshot width.

Historical rows are NEVER rewritten by this fix: the 972/975 legacy rows are
classified as provenance-incomplete (see the disposition constants below and
the taskboard row); only NEW writes gain consistent identity.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TradeProposal
from nexus_scalp.experience.intelligence import ExperienceIntelligenceEngine
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import ModelProvenance, StrategyContext
from nexus_scalp.experience.provenance import ModelRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'audit.db'}")
    r._start_background_worker()
    yield r
    r.close()


def _flush(repo: AuditRepository) -> None:
    assert repo.flush(timeout_sec=5.0), "audit worker did not drain"


def _make_engine(
    repo: AuditRepository, provenance: ModelProvenance
) -> ExperienceIntelligenceEngine:
    from nexus_scalp.experience.evaluator import StrategyEvaluator
    from nexus_scalp.experience.retriever import ExperienceRetriever

    ledger = ExperienceLedger(audit_repo=repo)
    evaluator = StrategyEvaluator(audit_repo=repo)
    retriever = ExperienceRetriever(ledger=ledger)
    return ExperienceIntelligenceEngine(
        ledger=ledger,
        evaluator=evaluator,
        retriever=retriever,
        enabled=True,
        provenance=provenance,
    )


def _proposal(request_id: str = "req-prov-0001") -> TradeProposal:
    return TradeProposal(
        request_id=request_id,
        execution_id="EXEC-20260911-150000-abcdef",
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action=ActionType.BUY,
        confidence=0.8,
        proposed_entry=2000.0,
        stop_loss=1999.0,
        take_profit=2002.0,
        risk_reward_ratio=2.0,
        reason_code="MODEL_SIGNAL",
        regime="TRENDING",
    )


def _fv(captured_dim: int) -> Mock:
    fv = Mock()
    fv.to_tensor_input = Mock(return_value=[0.1] * captured_dim)
    return fv


def _stored_row(repo: AuditRepository, idempotency_key: str) -> dict[str, Any]:
    import json

    con = sqlite3.connect(repo._db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT payload FROM audit_experiences WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
    finally:
        con.close()
    assert row is not None, "experience row missing"
    payload = json.loads(row["payload"])
    return payload


# ---------------------------------------------------------------------------
# 1. 70D serving + 50D captured base tensor -> consistent two-representation row
# ---------------------------------------------------------------------------


def test_70d_provenance_with_50d_snapshot_is_labeled_honestly(repo) -> None:
    """THE regression: serving scalp_v3/70 while capturing the base-50 tensor
    must NOT stamp scalp_v3 onto the snapshot. Provenance keeps the serving
    identity; the snapshot is labeled with the schema of the captured width."""
    engine = _make_engine(
        repo,
        ModelProvenance(
            model_id="primary_scalp_scalp_v3_70d",
            model_version="v1.0",
            feature_schema_id="scalp_v3",
            feature_dimension=70,
        ),
    )
    engine._record_decision_experience(
        proposal=_proposal(),
        context=StrategyContext(strategy_id="strat_prov70"),
        feature_vector=_fv(50),
        decision_id="exp_dec_prov70",
    )
    _flush(repo)

    payload = _stored_row(repo, "exp_req-prov-0001")
    snap = payload["feature_snapshot"]
    prov = payload["provenance"]

    # Snapshot describes the CAPTURED tensor: scalp_v1 / 50 / 50 values.
    assert snap["feature_schema_id"] == "scalp_v1", (
        "captured base-50 tensor must be labeled scalp_v1, never the serving schema"
    )
    assert snap["feature_dimension"] == 50
    assert len(snap["values"]) == 50
    # (schema, dimension, len(values)) triple is internally consistent.
    assert snap["feature_dimension"] == len(snap["values"])

    # Provenance keeps the SERVING identity — model attribution is not lost.
    assert prov["feature_schema_id"] == "scalp_v3"
    assert prov["feature_dimension"] == 70
    assert prov["model_id"] == "primary_scalp_scalp_v3_70d"


def test_snapshot_schema_resolves_in_the_registry(repo) -> None:
    """The stamped snapshot schema id must resolve in FEATURE_SCHEMAS with a
    dimension equal to the captured width (no invented schema ids)."""
    from nexus_scalp.features.schema import FEATURE_SCHEMAS

    engine = _make_engine(
        repo,
        ModelProvenance(
            model_id="primary_scalp_scalp_v3_70d",
            model_version="v1.0",
            feature_schema_id="scalp_v3",
            feature_dimension=70,
        ),
    )
    engine._record_decision_experience(
        proposal=_proposal("req-prov-0002"),
        context=StrategyContext(strategy_id="strat_resolve"),
        feature_vector=_fv(50),
        decision_id="exp_dec_resolve",
    )
    _flush(repo)
    payload = _stored_row(repo, "exp_req-prov-0002")
    snap = payload["feature_snapshot"]
    schema = FEATURE_SCHEMAS.resolve(snap["feature_schema_id"])
    assert schema.dimension == len(snap["values"])


def test_registry_dimension_lookup_maps_50_to_scalp_v1() -> None:
    """The writer's reverse lookup is the registry's: 50 -> scalp_v1."""
    from nexus_scalp.features.schema import schema_for_dimension

    s = schema_for_dimension(50)
    assert s is not None and s.schema_id == "scalp_v1" and s.dimension == 50


# ---------------------------------------------------------------------------
# 2. 50D serving unchanged: snapshot and provenance agree, existing rows valid
# ---------------------------------------------------------------------------


def test_50d_serving_behavior_unchanged(repo) -> None:
    """A scalp_v1/50 champion keeps producing scalp_v1/50 snapshots — current
    50D production behavior is byte-identical."""
    engine = _make_engine(
        repo,
        ModelProvenance(
            model_id="primary_scalp_scalp_v1_50d",
            model_version="v1.0",
            feature_schema_id="scalp_v1",
            feature_dimension=50,
        ),
    )
    engine._record_decision_experience(
        proposal=_proposal("req-prov-0003"),
        context=StrategyContext(strategy_id="strat_50d"),
        feature_vector=_fv(50),
        decision_id="exp_dec_50d",
    )
    _flush(repo)
    payload = _stored_row(repo, "exp_req-prov-0003")
    assert payload["feature_snapshot"]["feature_schema_id"] == "scalp_v1"
    assert payload["feature_snapshot"]["feature_dimension"] == 50
    assert payload["provenance"]["feature_schema_id"] == "scalp_v1"
    assert payload["provenance"]["feature_dimension"] == 50


# ---------------------------------------------------------------------------
# 3. honest fallback: unregistered captured width never invents a schema id
# ---------------------------------------------------------------------------


def test_unregistered_captured_width_keeps_provenance_id_with_warning(repo) -> None:
    """A captured width no registered schema declares keeps the legacy
    provenance id (fail-honest, never a fabricated identity) and logs a loud
    WARNING so the drift is observable.

    Capture uses a dedicated root-logger handler: structlog writes through
    stdlib and caplog does not reliably see the configured pipeline (same
    discipline as test_perf_exec_trace_throttle.py)."""
    import logging

    from nexus_scalp.observability.logging import configure_logging

    configure_logging(log_level="WARNING", log_to_file=False)

    class _WarnCapture(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.messages: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - trivial
            self.messages.append(record.getMessage())

    handler = _WarnCapture()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        engine = _make_engine(
            repo,
            ModelProvenance(
                model_id="primary_scalp_scalp_v3_70d",
                model_version="v1.0",
                feature_schema_id="scalp_v3",
                feature_dimension=70,
            ),
        )
        engine._record_decision_experience(
            proposal=_proposal("req-prov-0004"),
            context=StrategyContext(strategy_id="strat_unknown_width"),
            feature_vector=_fv(123),  # no registered schema has dimension 123
            decision_id="exp_dec_unknown",
        )
        _flush(repo)
    finally:
        root.removeHandler(handler)

    payload = _stored_row(repo, "exp_req-prov-0004")
    # Legacy fallback: provenance id (honest absence of a better identity),
    # with the mismatch now loudly visible in the log.
    assert payload["feature_snapshot"]["feature_schema_id"] == "scalp_v3"
    assert any("SNAPSHOT_SCHEMA_UNRESOLVED" in m for m in handler.messages), handler.messages


# ---------------------------------------------------------------------------
# 4. downstream consumers stay compatible with the corrected identity
# ---------------------------------------------------------------------------


def test_ledger_retrieval_and_census_after_fix(repo) -> None:
    """The row is retrievable under its own (snapshot) schema; the census sees
    the honest scalp_v1/50D entry even though the serving model was 70D."""
    engine = _make_engine(
        repo,
        ModelProvenance(
            model_id="primary_scalp_scalp_v3_70d",
            model_version="v1.0",
            feature_schema_id="scalp_v3",
            feature_dimension=70,
        ),
    )
    engine._record_decision_experience(
        proposal=_proposal("req-prov-0005"),
        context=StrategyContext(strategy_id="strat_census"),
        feature_vector=_fv(50),
        decision_id="exp_dec_census",
    )
    _flush(repo)

    ledger = engine.ledger
    rows = ledger.get_experiences_for_strategy("strat_census")
    assert len(rows) == 1
    rec = rows[0]
    assert rec.feature_schema_id == "scalp_v1"
    assert rec.feature_dimension == 50
    assert len(rec.feature_snapshot.values) == 50
    # Model attribution survives the round-trip.
    assert rec.provenance.model_id == "primary_scalp_scalp_v3_70d"

    census = ledger.get_schema_distribution()
    assert census.get("scalp_v1/50D") == 1
    assert census.get("scalp_v3/50D") is None, (
        "the impossible (70D schema, 50 values) census entry must never exist again"
    )


def test_training_dataset_builder_accepts_fixed_rows(repo) -> None:
    """The model-lifecycle fine-tune path builds rows from the snapshot by ROW
    dimension; a corrected 70D-served row (scalp_v1/50 snapshot) passes the
    len(values)==dim check and keeps its own schema id."""
    from nexus_scalp.model_lifecycle.dataset import TrainingDatasetBuilder

    engine = _make_engine(
        repo,
        ModelProvenance(
            model_id="primary_scalp_scalp_v3_70d",
            model_version="v1.0",
            feature_schema_id="scalp_v3",
            feature_dimension=70,
        ),
    )
    engine._record_decision_experience(
        proposal=_proposal("req-prov-0006"),
        context=StrategyContext(strategy_id="strat_ft"),
        feature_vector=_fv(50),
        decision_id="exp_dec_ft",
    )
    _flush(repo)

    builder = TrainingDatasetBuilder(ledger=engine.ledger)
    rec = engine.ledger.get_experiences_for_strategy("strat_ft")[0]
    row = builder._row_from_record(
        rec, label=1, dataset_id="ds_x", include_no_trade=True, weight_no_trade=0.25
    )
    assert row is not None, "corrected row must remain trainable"
    assert row.feature_dimension == len(row.feature_vector) == 50


def test_hash_binds_snapshot_to_its_own_schema(repo) -> None:
    """feature_hash is computed over (captured schema id, width, values) so a
    base-50 snapshot under a 70D model cannot collide with a genuine
    scalp_v1/50D row hashed under the same values."""
    from nexus_scalp.experience.ledger import ExperienceLedger

    engine = _make_engine(
        repo,
        ModelProvenance(
            model_id="primary_scalp_scalp_v3_70d",
            model_version="v1.0",
            feature_schema_id="scalp_v3",
            feature_dimension=70,
        ),
    )
    engine._record_decision_experience(
        proposal=_proposal("req-prov-0007"),
        context=StrategyContext(strategy_id="strat_hash"),
        feature_vector=_fv(50),
        decision_id="exp_dec_hash",
    )
    _flush(repo)
    snap = _stored_row(repo, "exp_req-prov-0007")["feature_snapshot"]
    expected = ExperienceLedger.compute_feature_hash(snap["values"], "scalp_v1")
    assert snap["feature_hash"] == expected
    # And the hash is DIFFERENT from the same values hashed under scalp_v3:
    assert snap["feature_hash"] != ExperienceLedger.compute_feature_hash(snap["values"], "scalp_v3")


# ---------------------------------------------------------------------------
# 5. historical disposition constants (no mutation — classification only)
# ---------------------------------------------------------------------------


def test_historical_972_rows_are_classified_not_rewritten() -> None:
    """Documents the historical-row disposition: the legacy writer produced
    (scalp_v3, 50) rows between 2026-08-24 and the fix. They are provenance-
    incomplete: feature_hash was computed under the serving schema id, the 20
    non-base dimensions were never captured, and reconstruction from
    assumptions is forbidden. No code path rewrites them; consumers must treat
    them as base-50 evidence with a mislabeled schema id."""
    # The fix contains no UPDATE/DELETE against audit_experiences.
    src = open("src/nexus_scalp/experience/intelligence.py", encoding="utf-8").read()
    assert "UPDATE audit_experiences" not in src
    assert "DELETE FROM audit_experiences" not in src
