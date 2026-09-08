"""Economic promotion integrity (ECON v1) — fail-closed gate tests.

Pins the contract that promotion is blocked when economic provenance is
missing, and that the technical checklist alone can NEVER promote:

  * economic_integrity() evaluates every ECONOMIC_EVIDENCE_KEYS entry
    fail-closed (missing/None/False/non-bool all fail)
  * a full technical checklist WITHOUT economic evidence is blocked at
    promote_to_review (the historical 14-key evidence no longer suffices)
  * a frictionless-research candidate is non-promotable
    (execution_profile_production_like=False)
  * with complete economic provenance the gate passes and the transition
    proceeds exactly as before
"""

from __future__ import annotations

import pytest

from nexus_scalp.governance.engine import (
    CHECKLIST_EVIDENCE_KEYS,
    ECONOMIC_EVIDENCE_KEYS,
    ModelGovernanceEngine,
    PromotionGateError,
)
from nexus_scalp.governance.models import PromotionState
from nexus_scalp.governance.store import GovernanceStore

TECH_OK = {k: True for k in CHECKLIST_EVIDENCE_KEYS}
ECON_OK = {k: True for k in ECONOMIC_EVIDENCE_KEYS}


@pytest.fixture
def gov_engine(tmp_path):
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'econ_gov.db'}")
    store = GovernanceStore(audit_repo=repo)
    eng = ModelGovernanceEngine(store=store)
    yield eng, store, repo
    repo._queue.join()
    repo.close()


def _walk_to_shadow(eng) -> None:
    eng.transition(model_id="c1", model_version="v1", target=PromotionState.VALIDATED, actor="op")
    eng.transition(model_id="c1", model_version="v1", target=PromotionState.CHALLENGER, actor="op")
    eng.transition(model_id="c1", model_version="v1", target=PromotionState.SHADOW, actor="op")


def test_economic_integrity_fail_closed_on_missing_evidence(gov_engine) -> None:
    eng, _, _ = gov_engine
    # empty evidence: every economic key must fail
    verdict = eng.economic_integrity({})
    assert verdict["verdict"] == "NON_PROMOTABLE"
    assert set(verdict["failed"]) == set(ECONOMIC_EVIDENCE_KEYS)


def test_economic_integrity_rejects_none_false_and_non_bool(gov_engine) -> None:
    eng, _, _ = gov_engine
    for bad in (None, False, "yes", 1):
        evidence = dict(ECON_OK)
        evidence["swap_assumptions_recorded"] = bad
        verdict = eng.economic_integrity(evidence)
        assert verdict["verdict"] == "NON_PROMOTABLE"
        assert verdict["failed"] == ["swap_assumptions_recorded"]


def test_economic_integrity_passes_with_complete_provenance(gov_engine) -> None:
    eng, _, _ = gov_engine
    verdict = eng.economic_integrity(ECON_OK)
    assert verdict["verdict"] == "PROMOTABLE"
    assert verdict["ready_for_review"] is True
    assert verdict["failed"] == []


def test_technical_checklist_alone_cannot_promote(gov_engine) -> None:
    """THE regression: the historical 14-key evidence no longer promotes."""
    eng, _, _ = gov_engine
    _walk_to_shadow(eng)
    with pytest.raises(PromotionGateError) as exc:
        eng.promote_to_review(model_id="c1", model_version="v1", actor="op", evidence=dict(TECH_OK))
    # the failure names the economic keys, not the technical ones
    assert "sizing_model_recorded" in str(exc.value)


def test_frictionless_candidate_is_non_promotable(gov_engine) -> None:
    eng, _, _ = gov_engine
    _walk_to_shadow(eng)
    evidence = dict(TECH_OK)
    evidence.update(dict(ECON_OK))
    evidence["execution_profile_production_like"] = False  # frictionless research
    with pytest.raises(PromotionGateError):
        eng.promote_to_review(model_id="c1", model_version="v1", actor="op", evidence=evidence)


def test_zero_friction_defaults_are_non_promotable(gov_engine) -> None:
    """Missing friction/swap provenance (the silent-zero accident) fails."""
    eng, _, _ = gov_engine
    evidence = dict(TECH_OK)
    evidence.update(dict(ECON_OK))
    evidence["friction_assumptions_recorded"] = False
    evidence["swap_assumptions_recorded"] = False
    with pytest.raises(PromotionGateError):
        eng.promote_to_review(model_id="c1", model_version="v1", actor="op", evidence=evidence)


def test_full_provenance_promotes_end_to_end(gov_engine) -> None:
    eng, store, _ = gov_engine
    _walk_to_shadow(eng)
    evidence = dict(TECH_OK)
    evidence.update(dict(ECON_OK))
    eng.promote_to_review(model_id="c1", model_version="v1", actor="op", evidence=evidence)
    eng.approve(model_id="c1", model_version="v1", actor="operator_1", reason="ok")
    state = store.get_state("c1", "v1")
    assert state["lifecycle_state"] == "APPROVED"
    # the blocked-evidence ledger recorded nothing for this candidate
    events = store.list_events(event="PROMOTION_BLOCKED") if hasattr(store, "list_events") else []
    assert all((e.get("model_id") != "c1") for e in events)


def test_blocked_transition_records_economic_keys_in_ledger(gov_engine) -> None:
    import json as _json

    eng, store, _ = gov_engine
    _walk_to_shadow(eng)
    with pytest.raises(PromotionGateError):
        eng.promote_to_review(model_id="c1", model_version="v1", actor="op", evidence=dict(TECH_OK))
    events = [e for e in store.list_events() if e.get("event") == "PROMOTION_BLOCKED"]
    assert events, "blocked promotion must be auditable"
    raw_payload = events[-1].get("payload") or "{}"
    payload = _json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
    assert "economic_pnl_basis_present" in (payload.get("failed") or [])
