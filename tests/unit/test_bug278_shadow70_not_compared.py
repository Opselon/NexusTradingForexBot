"""BUG-278 regression — non-compared 70D shadow rows must never be reported
as Champion-vs-Shadow disagreements.

Root cause (verified against artifacts/audit.db + runtime probe):
``Shadow70Runtime.observe()`` fed its neutral default
``shadow_action="NO_TRADE"`` into ``classify_disagreement()`` on every fault
path (runtime not READY / vector rejected / inference failed). With no 70D
candidate attached at all, ticks were stamped ``NO_TRADE_DISAGREEMENT`` and
the UI table reported "the shadow disagrees with the champion" for
observations where no shadow inference ever ran.

Fix: those rows now carry ``DisagreementClass.NOT_COMPARED`` +
``Shadow70Observation.compared`` is False, the summary counts them as
``not_compared`` rather than ``disagreements``, and the store/UI histogram
and disagreement filter key off the compared condition instead of ``valid``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nexus_scalp.shadow.shadow70.models import (
    DisagreementClass,
    Shadow70CandidateContract,
)
from nexus_scalp.shadow.shadow70.runtime import Shadow70Runtime
from tests.helpers.shadow70_fixtures import vector70


def _ready_runtime(contract: Shadow70CandidateContract) -> Shadow70Runtime:
    rt = Shadow70Runtime()
    res = rt.attach(contract)
    assert res.passed, res.reason
    rt.set_inference(lambda v: [0.05, 0.85, 0.05, 0.05])
    return rt


def _observe(rt: Shadow70Runtime, snapshot_id: str, vector: list[float]) -> object:
    return rt.observe(
        vector70=vector,
        champion_action="BUY_MARKET",
        champion_probabilities=[0.2, 0.5, 0.15, 0.15],
        champion_confidence=0.5,
        snapshot_id=snapshot_id,
        timestamp=datetime.now(UTC),
        base_feature_hash="b" * 8,
        feature_schema_hash="f" * 16,
    )


def test_bug278_idle_runtime_is_not_compared(contract: Shadow70CandidateContract) -> None:
    """An IDLE runtime (no candidate, the real production state) produces
    NOT_COMPARED, never NO_TRADE_DISAGREEMENT."""
    rt = Shadow70Runtime()
    assert rt.state.value == "IDLE"
    obs = _observe(rt, "snap_bug278_idle", vector70())
    assert obs.valid is False
    assert obs.error_code == "SHADOW_BLOCKED"
    assert obs.disagreement == DisagreementClass.NOT_COMPARED
    assert obs.compared is False
    assert obs.shadow_action == "NO_TRADE"  # placeholder, not a decision
    assert obs.disagreement_or_reason == "SHADOW_BLOCKED"

    s = rt.summary()
    assert s["disagreements"] == 0, "IDLE ticks must not count as disagreements"
    assert s["not_compared"] == 1


def test_bug278_rejected_vector_is_not_compared(contract: Shadow70CandidateContract) -> None:
    """Vector-validation failure (wrong dimension) with a READY runtime +
    attached model still yields NOT_COMPARED."""
    rt = _ready_runtime(contract)
    obs = _observe(rt, "snap_bug278_badvec", [0.0] * 49)  # INV-70D-004 violation
    assert obs.valid is False
    assert obs.error_code == "SHADOW_FEATURE_INVALID"
    assert obs.disagreement == DisagreementClass.NOT_COMPARED
    assert obs.compared is False
    assert rt.summary()["disagreements"] == 0


def test_bug278_failed_inference_is_not_compared(contract: Shadow70CandidateContract) -> None:
    """A READY runtime whose model raises yields NOT_COMPARED, and recovery
    still classifies a genuine disagreement."""
    rt = Shadow70Runtime()
    rt.attach(contract)

    def boom(_v: list[float]) -> list[float]:
        raise RuntimeError("model NaN")

    rt.set_inference(boom)
    obs = _observe(rt, "snap_bug278_boom", vector70())
    assert obs.valid is False
    assert obs.error_code == "SHADOW_INFERENCE_FAILED"
    assert obs.disagreement == DisagreementClass.NOT_COMPARED
    assert obs.compared is False

    # recovery: shadow says SELL while the champion says BUY
    rt.set_inference(lambda v: [0.05, 0.05, 0.85, 0.05])
    o2 = _observe(rt, "snap_bug278_recover", vector70())
    assert o2.valid is True
    assert o2.compared is True
    assert o2.disagreement == DisagreementClass.BUY_VS_SELL
    assert o2.disagreement_or_reason == "BUY_VS_SELL"

    s = rt.summary()
    assert s["disagreements"] == 1
    assert s["not_compared"] == 1


def test_bug278_taxonomy_still_classifies_real_rows(contract: Shadow70CandidateContract) -> None:
    """The fix must not weaken the real taxonomy: a valid row with an
    identical action still AGREES, and NO_TRADE-vs-WAIT keeps its class."""
    from nexus_scalp.shadow.shadow70.models import classify_disagreement

    assert classify_disagreement("BUY_MARKET", "BUY_MARKET") == DisagreementClass.AGREEMENT
    assert classify_disagreement("BUY_MARKET", "NO_TRADE") == (
        DisagreementClass.CHAMPION_BUYS_SHADOW_NO_TRADE
    )
    assert classify_disagreement("NO_TRADE", "WAIT") == DisagreementClass.NO_TRADE_DISAGREEMENT

    rt = _ready_runtime(contract)
    obs = _observe(rt, "snap_bug278_agree", vector70())
    assert obs.valid is True
    assert obs.compared is True
    # shadow inference is BUY-heavy (0.85) and the champion is BUY_MARKET:
    # same action, so it lands in the agreement family (AGREEMENT when the
    # confidence gap is < 0.10, CONFIDENCE_DIVERGENCE otherwise) — never a
    # not-compared class.
    assert obs.disagreement in (
        DisagreementClass.AGREEMENT,
        DisagreementClass.CONFIDENCE_DIVERGENCE,
    )
    # the taxonomy summary counts only real comparisons
    s = rt.summary()
    assert s["disagreements"] + s["agreements"] == 1
    assert s["not_compared"] == 0


def test_bug278_store_histogram_excludes_not_compared(contract: Shadow70CandidateContract) -> None:
    """The persisted disagreement histogram and the disagreement-only read
    path must exclude rows that never compared anything (CHG-0046 D9 +
    BUG-278)."""
    import os
    import sqlite3
    import tempfile

    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.shadow.shadow70.store import Shadow70Store

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "audit.db")
        repo = AuditRepository(db_url=f"sqlite:///{db}")
        try:
            store = Shadow70Store(audit_repo=repo)
            # one IDLE (non-compared) + one real disagreement
            idle = Shadow70Runtime().observe(
                vector70=vector70(),
                champion_action="BUY_MARKET",
                champion_probabilities=[0.2, 0.5, 0.15, 0.15],
                champion_confidence=0.5,
                snapshot_id="snap_db_idle",
                timestamp=datetime.now(UTC),
                base_feature_hash="b" * 8,
                feature_schema_hash="f" * 16,
            )
            rt = _ready_runtime(contract)
            real = rt.observe(
                vector70=vector70(),
                champion_action="BUY_MARKET",
                champion_probabilities=[0.05, 0.05, 0.85, 0.05],
                champion_confidence=0.85,
                snapshot_id="snap_db_real",
                timestamp=datetime.now(UTC),
                base_feature_hash="b" * 8,
                feature_schema_hash="f" * 16,
            )
            rt.set_inference(lambda v: [0.05, 0.05, 0.85, 0.05])  # SELL vs BUY
            real2 = rt.observe(
                vector70=vector70(),
                champion_action="BUY_MARKET",
                champion_probabilities=[0.05, 0.05, 0.85, 0.05],
                champion_confidence=0.85,
                snapshot_id="snap_db_real2",
                timestamp=datetime.now(UTC),
                base_feature_hash="b" * 8,
                feature_schema_hash="f" * 16,
            )
            assert store.save_observation(idle)
            assert store.save_observation(real)
            assert store.save_observation(real2)
            repo._queue.join()

            counts = store.disagreement_counts()
            assert counts.get("NOT_COMPARED", 0) == 0, counts
            assert counts.get("NO_TRADE_DISAGREEMENT", 0) == 0, counts
            assert counts.get("BUY_VS_SELL", 0) == 1, counts

            unfiltered = store.disagreement_counts(valid_only=False)
            assert unfiltered.get("NOT_COMPARED", 0) == 1, unfiltered

            dis = store.list_observations(limit=50, disagreement_only=True)
            assert len(dis) == 1, [r.get("snapshot_id") for r in dis]
            assert dis[0]["snapshot_id"] == "snap_db_real2"

            all_rows = store.list_observations(limit=50)
            assert len(all_rows) == 3, "evidence is not deleted, only reclassified"
        finally:
            repo.close()
