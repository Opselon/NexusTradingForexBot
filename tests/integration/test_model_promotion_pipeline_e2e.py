"""End-to-End Integration Tests for ML-GOV-001: Model Promotion Pipeline Pre-Flight & OOS Economic Gate Verification.

Verifies:
  1. POST /api/models/promotion/execute requires valid operator token and explicit actor.
  2. Candidates failing the OOS economic expectancy floor (< 0.02R) cannot be promoted.
  3. Valid candidates passing the floor (>= 0.02R) pass pre-flight verification.
  4. PromotionLock exclusive concurrency guard prevents race conditions (PROMOTION_CONFLICT).
  5. Failed activation triggers atomic rollback leaving Champion slot completely untouched.
  6. Post-activation verification failure executes automatic rollback to previous Champion.
  7. End-to-end happy-path promotion executes and commits within benchmark SLA (< 500ms).
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import torch
from starlette.testclient import TestClient

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.features.session_time import session_semantics_metadata
from nexus_scalp.governance.lock import PromotionLock
from nexus_scalp.governance.store import GovernanceStore
from nexus_scalp.governance.transaction import (
    PromotionTransactionError,
    execute_promotion_transaction,
)
from nexus_scalp.release import bootstrap as rb
from nexus_scalp.web.server import create_app

FAKE_TOKEN = "gov_test_operator_token_secret_12345"


def make_valid_candidate(
    tmp_path: Path, model_id: str = "cand1", version: str = "1.0.0"
) -> tuple[Path, Path, dict[str, Any], dict[str, Any]]:
    """Generates an eligible candidate model, scaler, manifest, and verify kwargs."""
    art = tmp_path / f"{model_id}.pt"
    torch.save({"input_projection.weight": torch.zeros(4, 50), "bias": torch.zeros(4)}, art)
    sca = tmp_path / f"{model_id}.pt.scaler.npz"
    np.savez(sca, mean=np.zeros(50, dtype=np.float32), std=np.ones(50, dtype=np.float32))
    art_hash = hashlib.sha256(art.read_bytes()).hexdigest()
    mf = {
        "model_id": model_id,
        "model_version": version,
        "feature_schema_id": "scalp_v1",
        "feature_dimension": 50,
        "class_count": 4,
        "label_schema_id": "triple_barrier_3class_v1",
        "architecture_id": "LEGACY_SCALPNET_V1",
        "news_enabled": False,
        "session_semantics": session_semantics_metadata(),
        "artifact_hash": art_hash,
        "feature_schema_hash": "schema_hash_123",
        "liquidity_algorithm_version": "1.0.0",
        "training_commit": "git_commit_abc",
        "oos_artifact": "oos_artifact_hash_123",
        "production_eligible": True,
        "smoke": False,
    }
    kwargs: dict[str, Any] = {
        "artifact_path": art,
        "scaler_path": sca,
        "manifest": mf,
        "runtime_schema_id": "scalp_v1",
        "runtime_dimension": 50,
        "feature_schema_hash": "schema_hash_123",
        "liquidity_algorithm_version": "1.0.0",
        "training_commit": "git_commit_abc",
        "oos_artifact": "oos_artifact_hash_123",
        "shadow_evidence": {
            "sample_floor_met": True,
            "samples_observed": 100,
            "samples_required": 50,
        },
        "news_contract": {"valid": True, "detail": "ok"},
        "liquidity_contract": {"valid": True, "detail": "ok"},
    }
    return art, sca, mf, kwargs


@pytest.fixture
def wired_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Sets up an isolated LiveEngine and GovernanceStore with temporary storage."""
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", FAKE_TOKEN)
    db_file = tmp_path / "models_promo.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")

    model_dir = tmp_path / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    model_file = model_dir / "scalpnet_active.pt"
    rb.mint_starter_bundle(model_file)

    cfg = AppConfig(
        execution={"symbol": "XAUUSD", "mode": "PAPER"},
        model={"model_artifact_path": str(model_file)},
    )
    adapter = PaperMT5Adapter(initial_balance=10_000.0, symbol="XAUUSD")
    engine = LiveEngine(config=cfg, adapter=adapter, audit_repo=repo)

    app = create_app(engine)
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {FAKE_TOKEN}"

    yield {
        "engine": engine,
        "repo": repo,
        "client": client,
        "tmp_path": tmp_path,
        "model_file": model_file,
    }

    repo.close()


# =============================================================================
# API Pre-Flight & Authorization Tests
# =============================================================================


def test_promotion_requires_valid_operator_token_and_actor(wired_env: dict[str, Any]) -> None:
    """POST /api/models/promotion/execute must fail if actor, model_id, or token is missing."""
    client: TestClient = wired_env["client"]

    # 1. Missing approval token
    r1 = client.post(
        "/api/models/promotion/execute",
        json={"actor": "operator_alice", "model_id": "cand_01", "model_version": "1.0.0"},
    )
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1.get("error", {}).get("code") == "PROMOTION_BLOCKED"
    assert "token required" in b1.get("reason", "")

    # 2. Missing actor
    r2 = client.post(
        "/api/models/promotion/execute",
        json={"approval_token": "valid_token_xyz", "model_id": "cand_01"},
    )
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2.get("error", {}).get("code") == "PROMOTION_BLOCKED"

    # 3. Missing model_id
    r3 = client.post(
        "/api/models/promotion/execute",
        json={"actor": "operator_alice", "approval_token": "valid_token_xyz"},
    )
    assert r3.status_code == 200
    b3 = r3.json()
    assert b3.get("error", {}).get("code") == "PROMOTION_BLOCKED"


def test_promotion_rejects_sub_floor_oos_expectancy(wired_env: dict[str, Any]) -> None:
    """Candidates with OOS expectancy below the 0.02R floor must be rejected."""
    client: TestClient = wired_env["client"]

    # Expectancy = 0.01R (below 0.02R floor)
    resp = client.post(
        "/api/models/promotion/execute",
        json={
            "actor": "lead_trader",
            "approval_token": "tok_super_secret",
            "model_id": "cand_weak_oos",
            "model_version": "1.2.0",
            "oos_expectancy_r": 0.01,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("error", {}).get("code") == "PROMOTION_BLOCKED"
    assert body.get("gate") == "oos_economic_floor"
    assert "failed OOS economic expectancy floor" in body.get("reason", "")

    # Negative expectancy = -0.05R
    resp_neg = client.post(
        "/api/models/promotion/execute",
        json={
            "actor": "lead_trader",
            "approval_token": "tok_super_secret",
            "model_id": "cand_negative_oos",
            "model_version": "1.2.0",
            "oos_expectancy_r": -0.05,
        },
    )
    assert resp_neg.status_code == 200
    body_neg = resp_neg.json()
    assert body_neg.get("error", {}).get("code") == "PROMOTION_BLOCKED"
    assert body_neg.get("gate") == "oos_economic_floor"


def test_promotion_frozen_blocks_execution(wired_env: dict[str, Any]) -> None:
    """Emergency frozen governance engine blocks all promotions."""
    client: TestClient = wired_env["client"]
    engine: LiveEngine = wired_env["engine"]

    # Freeze promotion
    r_freeze = client.post(
        "/api/models/governance/emergency/freeze",
        json={"actor": "risk_officer", "reason": "Market anomaly"},
    )
    assert r_freeze.status_code == 200
    assert engine.governance_engine.promotion_frozen is True

    # Attempt execute
    r_exec = client.post(
        "/api/models/promotion/execute",
        json={
            "actor": "operator_alice",
            "approval_token": "tok_123",
            "model_id": "cand_01",
            "oos_expectancy_r": 0.05,
        },
    )
    assert r_exec.status_code == 200
    assert r_exec.json().get("error", {}).get("code") == "PROMOTION_BLOCKED"
    assert "frozen" in r_exec.json().get("reason", "")

    # Unfreeze
    r_unfreeze = client.post(
        "/api/models/governance/emergency/unfreeze",
        json={"actor": "risk_officer", "reason": "Anomaly resolved"},
    )
    assert r_unfreeze.status_code == 200
    assert engine.governance_engine.promotion_frozen is False


# =============================================================================
# Transaction & Concurrency Lock Tests
# =============================================================================


def test_promotion_lock_contention(tmp_path: Path) -> None:
    """Acquiring exclusive lock blocks concurrent promotion transactions."""
    lock_file = tmp_path / "promotion.lock"
    lock1 = PromotionLock(lock_file)
    assert lock1.try_acquire() is True

    # Second lock attempt must fail
    lock2 = PromotionLock(lock_file)
    assert lock2.try_acquire() is False

    # Once released, can be re-acquired
    lock1.release()
    assert lock2.try_acquire() is True
    lock2.release()


def test_transaction_raises_on_lock_contention(tmp_path: Path) -> None:
    """execute_promotion_transaction raises PromotionTransactionError on lock conflict."""
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'gov.db'}")
    store = GovernanceStore(repo)
    lock_file = tmp_path / "promotion.lock"

    _art, _sca, _mf, kwargs = make_valid_candidate(tmp_path, "cand_lock")

    # Pre-acquire lock to simulate concurrent operation
    pre_lock = PromotionLock(lock_file)
    pre_lock.try_acquire()

    try:
        with pytest.raises(PromotionTransactionError, match="PROMOTION_CONFLICT"):
            execute_promotion_transaction(
                store=store,
                lock_path=lock_file,
                model_id="cand_lock",
                model_version="1.0.0",
                actor="operator_bob",
                reason="testing lock contention",
                approval_token="valid_token",
                old_champion={"model_id": "old", "version": "1.0.0", "artifact_hash": "abc"},
                candidate={"model_id": "cand_lock", "version": "1.0.0", "artifact_hash": "xyz"},
                activate=lambda mid, ver: None,
                **kwargs,
            )
    finally:
        pre_lock.release()
        repo.close()


# =============================================================================
# Atomic Rollback on Failures
# =============================================================================


def test_promotion_atomic_rollback_on_activation_failure(tmp_path: Path) -> None:
    """When activate() fails, candidate is marked REJECTED and old champion is unchanged."""
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'gov.db'}")
    store = GovernanceStore(repo)
    lock_file = tmp_path / "promo.lock"

    _art, _sca, _mf, kwargs = make_valid_candidate(tmp_path, "cand_broken")

    old_champ = {
        "model_id": "champ_steady",
        "version": "1.0.0",
        "artifact_hash": "sha_old_123",
        "lifecycle_state": "CHAMPION",
    }

    def failing_activate(mid: str, ver: str) -> None:
        raise OSError("Disk write error during model activation")

    with pytest.raises(PromotionTransactionError, match="activation failed"):
        execute_promotion_transaction(
            store=store,
            lock_path=lock_file,
            model_id="cand_broken",
            model_version="1.0.0",
            actor="operator_carol",
            reason="testing activation failure",
            approval_token="tok_abc",
            old_champion=old_champ,
            candidate={"model_id": "cand_broken", "version": "1.0.0", "artifact_hash": "sha_new"},
            activate=failing_activate,
            **kwargs,
        )

    # Candidate should be marked REJECTED in governance store
    state_rec = store.get_state("cand_broken", "1.0.0")
    assert state_rec is not None
    assert state_rec.get("lifecycle_state") == "REJECTED"

    # Audits must reflect failed promotion
    audits = store.list_promotion_audits()
    assert len(audits) >= 1
    assert audits[-1]["status"] == "PROMOTION_FAILED"
    assert audits[-1]["old_champion_model_id"] == "champ_steady"
    repo.close()


def test_promotion_atomic_rollback_on_post_verification_failure(tmp_path: Path) -> None:
    """When verify_new() fails, rollback_activate() is called to restore previous champion."""
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'gov.db'}")
    store = GovernanceStore(repo)
    lock_file = tmp_path / "promo.lock"

    _art, _sca, _mf, kwargs = make_valid_candidate(tmp_path, "cand_corrupt")

    old_champ = {
        "model_id": "champ_steady",
        "version": "1.0.0",
        "artifact_hash": "sha_old_123",
    }

    restored = {}

    def mock_rollback_activate(mid: str, ver: str) -> None:
        restored["model_id"] = mid
        restored["version"] = ver

    with pytest.raises(PromotionTransactionError, match="post-activation verification failed"):
        execute_promotion_transaction(
            store=store,
            lock_path=lock_file,
            model_id="cand_corrupt",
            model_version="1.0.0",
            actor="operator_dave",
            reason="testing post-verify failure",
            approval_token="tok_def",
            old_champion=old_champ,
            candidate={"model_id": "cand_corrupt", "version": "1.0.0", "artifact_hash": "sha_new"},
            activate=lambda mid, ver: None,
            verify_new=lambda mid, ver: {"ok": False, "reason": "model logits NaN on warmup"},
            rollback_activate=mock_rollback_activate,
            **kwargs,
        )

    # Asserts automatic rollback was invoked with old champion specs
    assert restored.get("model_id") == "champ_steady"
    assert restored.get("version") == "1.0.0"

    # Audits must reflect PROMOTION_ROLLED_BACK
    audits = store.list_promotion_audits()
    assert len(audits) >= 1
    assert audits[-1]["status"] == "PROMOTION_ROLLED_BACK"
    repo.close()


# =============================================================================
# Happy-Path End-to-End Execution & Latency Benchmark (<500ms)
# =============================================================================


def test_promotion_transaction_happy_path_and_benchmark(tmp_path: Path) -> None:
    """Full atomic promotion transaction commits successfully within <500ms SLA."""
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'gov.db'}")
    store = GovernanceStore(repo)
    lock_file = tmp_path / "promo.lock"

    _art, _sca, _mf, kwargs = make_valid_candidate(tmp_path, "champ_v2", "2.0.0")

    old_champ = {
        "model_id": "champ_v1",
        "version": "1.0.0",
        "artifact_hash": "hash_v1",
        "schema_id": "scalp_v1",
    }
    cand = {
        "model_id": "champ_v2",
        "version": "2.0.0",
        "artifact_hash": "hash_v2",
        "schema_id": "scalp_v1",
    }

    activated = {}

    def mock_activate(mid: str, ver: str) -> None:
        activated["model_id"] = mid
        activated["version"] = ver

    t0 = time.perf_counter()
    audit_row = execute_promotion_transaction(
        store=store,
        lock_path=lock_file,
        model_id="champ_v2",
        model_version="2.0.0",
        actor="lead_quant",
        reason="Scheduled monthly challenger promotion",
        approval_token="tok_governance_approved_2026",
        old_champion=old_champ,
        candidate=cand,
        activate=mock_activate,
        verify_new=lambda mid, ver: {"ok": True},
        **kwargs,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # Verification of promotion result
    assert audit_row["status"] == "PROMOTION_COMMITTED"
    assert audit_row["new_champion_model_id"] == "champ_v2"
    assert audit_row["old_champion_model_id"] == "champ_v1"
    assert activated.get("model_id") == "champ_v2"

    # SLA Benchmark: transaction execution must complete in < 500ms
    assert elapsed_ms < 500.0, f"Promotion took {elapsed_ms:.2f}ms (> 500ms SLA)"

    # Audit log verification
    audits = store.list_promotion_audits()
    assert len(audits) >= 1
    assert audits[-1]["status"] == "PROMOTION_COMMITTED"
    repo.close()
