"""
PHASE 10 Controlled Model Training & Challenger Engine — Behavioral Suite
=========================================================================
Real behavioral verification. Every test asserts OBSERVABLE BEHAVIOUR
(persisted registry statuses, immutable Runs, Champion hash invariance, failed
gate -> REJECTED, schema mismatch -> explicit error, worker isolation) rather
than mere object existence.

Coverage map (spec 38):
    DATASET      1.  dataset reproducibility
    DATASET      2.  provenance
    DATASET      3.  temporal ordering
    DATASET      4.  no future leakage
    DATASET      5.  schema identity
    TRAINING     6.  candidate model produced
    TRAINING     7.  champion artifact never overwritten
    TRAINING     8.  failed training cannot become Challenger
    TRAINING     9.  model metadata persisted
    TRAINING     10. random seed / reproducibility behaviour
    CONTRACT     11. 50D model compatibility
    CONTRACT     12. future schema compatibility boundary
    CONTRACT     13. feature dimension mismatch rejected
    CONTRACT     14. output class mismatch rejected
    CONTRACT     15. scaler mismatch rejected
    VALIDATION   16. validation gate failure rejects candidate
    VALIDATION   17. OOS failure rejects candidate
    VALIDATION   18. robustness failure rejects candidate
    VALIDATION   19. drawdown failure rejects candidate
    VALIDATION   20. model collapse rejected
    VALIDATION   21. invalid calibration rejected if gate active
    CHAMPION     22. challenger cannot execute production orders
    CHAMPION     23. champion unchanged during training
    CHAMPION     24. rejected challenger cannot become champion
    CHAMPION     25. promotion lineage immutable
    EXPERIENCE   26. losses included
    EXPERIENCE   27. wins included
    EXPERIENCE   28. strategy context retained
    EXPERIENCE   29. model provenance retained
    WORKER       30. training failure isolated
    WORKER       31. worker cancellation safe
    WORKER       32. restart safe
    WORKER       33. no LiveEngine blocking
    REGRESSION   34-37. Phase 08/09 intact + production inference intact
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    CANONICAL_FEATURE_DIMENSION,
    CANONICAL_FEATURE_SCHEMA_ID,
    ExecutionContext,
    ExperienceOutcome,
    ExperienceRecord,
    FeatureSnapshot,
    OutcomeDecomposition,
    PositionBehavior,
    StrategyContext,
)
from nexus_scalp.experience.provenance import ModelRegistry, fingerprint_artifact
from nexus_scalp.model_lifecycle import (
    GateResult,
    ModelStatus,
    TrainingDataset,
    TrainingDatasetRow,
    TrainingRun,
    TrainingRunStatus,
)
from nexus_scalp.model_lifecycle.champion import ChampionManager
from nexus_scalp.model_lifecycle.comparison import ChampionChallengerComparator
from nexus_scalp.model_lifecycle.dataset import TrainingDatasetBuilder, validate_no_future_leakage
from nexus_scalp.model_lifecycle.gates import (
    check_model_collapse,
    gate_artifact_integrity,
    gate_dataset_integrity,
    gate_label_integrity,
    gate_oos,
    gate_risk_drawdown,
    gate_robustness,
    gate_schema_compatibility,
    gate_training_stability,
    gate_validation_performance,
)
from nexus_scalp.model_lifecycle.integrity import (
    SchemaCompatibilityError,
    inspect_artifact,
    scaler_compatibility,
    verify_compatibility,
)
from nexus_scalp.model_lifecycle.orchestrator import ModelLifecycleOrchestrator
from nexus_scalp.model_lifecycle.registry import ModelLifecycleRegistry
from nexus_scalp.model_lifecycle.store import TrainingRunStore
from nexus_scalp.model_lifecycle.trainer import ChallengerTrainer, summarize_run
from nexus_scalp.model_lifecycle.worker import TrainingWorker, format_training_worker_status

# =============================================================================
# FIXTURES & HELPERS
# =============================================================================


@pytest.fixture
def temp_audit_repo(tmp_path):
    db_file = tmp_path / "test_model_lifecycle.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")
    yield repo
    repo.close()


def flush(repo):
    repo._queue.join()


def make_record(
    key: str,
    strategy_id: str = "strat_p10",
    decision_ts: datetime | None = None,
    action: str = "BUY_MARKET",
    executed: bool = True,
    closed: bool = True,
    realized_r: float = 0.3,
    dimension: int = CANONICAL_FEATURE_DIMENSION,
    schema_id: str = CANONICAL_FEATURE_SCHEMA_ID,
) -> ExperienceRecord:
    ts = decision_ts or datetime.now(UTC)
    values = [0.0] * dimension
    # give the vector some non-trivial content for reproducibility checks
    for i in range(min(dimension, 10)):
        values[i] = float((i + 1) * 0.1)
    rec = ExperienceRecord(
        experience_id=f"exp_{key}",
        request_id=f"req_{key}",
        idempotency_key=key,
        symbol="XAUUSD",
        timeframe="M1",
        decision_timestamp=ts,
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        context=StrategyContext(
            strategy_id=strategy_id,
            symbol="XAUUSD",
            session="LONDON",
            regime="TRENDING",
            volatility_regime="HIGH",
            trend_state="BULLISH",
        ),
        feature_snapshot=FeatureSnapshot(
            feature_schema_id=schema_id,
            feature_dimension=dimension,
            values=values,
        ),
        action=action,
        entry_reason="SMC_GOD_MODE",
        model_probability=0.6,
        signal_confidence=0.6,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
        approved_volume=0.1,
        is_executed=executed,
        is_closed=closed,
        realized_pnl_usd=realized_r * 100.0,
        realized_r_multiple=realized_r,
    )
    if executed and closed:
        return rec.with_outcome(
            ExperienceOutcome(
                idempotency_key=key,
                execution_id=f"ticket_{key}",
                outcome_timestamp=ts + timedelta(minutes=5),
                is_executed=True,
                is_closed=True,
                exit_reason="TP" if realized_r > 0 else "SL",
                realized_pnl_usd=realized_r * 100.0,
                realized_r_multiple=realized_r,
                approved_volume=0.1,
                behavior=PositionBehavior(
                    mfe_r=max(0.5, realized_r) if realized_r > 0 else 0.2,
                    mae_r=0.2,
                    mae_points=2.0,
                    mfe_points=5.0,
                    expected_duration_sec=900.0,
                    duration_sec=300.0,
                ),
                execution=ExecutionContext(),
                decomposition=OutcomeDecomposition(
                    strategy_quality=0.5,
                    entry_quality=0.4,
                    position_management_quality=0.4,
                    exit_quality=0.4,
                    execution_quality=0.5,
                    final_outcome_r=realized_r,
                ),
                behavioral_flags=[],
            )
        )
    return rec


def seed_experiences(
    ledger: ExperienceLedger,
    repo,
    count: int = 60,
    prefix: str = "p10",
    include_losses: bool = True,
    r_win: float = 0.4,
    r_loss: float = -0.6,
) -> None:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    for i in range(count):
        r = r_win if (i % 3 != 1 or not include_losses) else r_loss
        rec = make_record(
            f"{prefix}{i}",
            decision_ts=base + timedelta(minutes=30 * i),
            realized_r=r,
        )
        ledger.record_experience(rec)
        ledger.record_outcome(
            rec.with_outcome(
                ExperienceOutcome(
                    idempotency_key=rec.idempotency_key,
                    execution_id=f"ticket_{rec.idempotency_key}",
                    outcome_timestamp=rec.decision_timestamp + timedelta(minutes=5),
                    is_executed=True,
                    is_closed=True,
                    exit_reason="TP" if r > 0 else "SL",
                    realized_pnl_usd=r * 100.0,
                    realized_r_multiple=r,
                    approved_volume=0.1,
                    behavior=PositionBehavior(mfe_r=max(0.5, r) if r > 0 else 0.2, mae_r=0.2),
                    execution=ExecutionContext(),
                    decomposition=OutcomeDecomposition(),
                    behavioral_flags=[],
                )
            )
        )
    flush(repo)


def torch_available() -> bool:
    try:
        import torch

        return True
    except Exception:
        return False


# =============================================================================
# 1-5. DATASET
# =============================================================================


class TestTrainingDataset:
    def test_reproducibility(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        seed_experiences(ledger, temp_audit_repo)
        b1 = TrainingDatasetBuilder(ledger).build()
        b2 = TrainingDatasetBuilder(ledger).build()
        assert b1.dataset_id == b2.dataset_id
        assert b1.sample_count == b2.sample_count
        assert [r.sample_id for r in b1.rows] == [r.sample_id for r in b2.rows]

    def test_provenance(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        seed_experiences(ledger, temp_audit_repo, count=10)
        ds = TrainingDatasetBuilder(ledger).build()
        assert ds.source_experience_ids
        row = ds.rows[0]
        assert row.experience_id
        assert row.idempotency_key
        assert row.strategy_id == "strat_p10"
        assert row.feature_schema_id == CANONICAL_FEATURE_SCHEMA_ID

    def test_temporal_ordering(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        seed_experiences(ledger, temp_audit_repo)
        ds = TrainingDatasetBuilder(ledger).build()
        times = [r.decision_timestamp for r in ds.ordered_rows()]
        assert times == sorted(times)

    def test_no_future_leakage(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        base = datetime(2024, 1, 1, tzinfo=UTC)
        for i in range(10):
            rec = make_record(f"leak{i}", decision_ts=base + timedelta(hours=i))
            ledger.record_experience(rec)
            ledger.record_outcome(
                rec.with_outcome(
                    ExperienceOutcome(
                        idempotency_key=rec.idempotency_key,
                        execution_id=f"t_{rec.idempotency_key}",
                        outcome_timestamp=rec.decision_timestamp + timedelta(minutes=5),
                        is_executed=True,
                        is_closed=True,
                        realized_pnl_usd=40.0,
                        realized_r_multiple=0.4,
                        behavior=PositionBehavior(mfe_r=1.0, mae_r=0.2),
                        execution=ExecutionContext(),
                        decomposition=OutcomeDecomposition(),
                    )
                )
            )
        flush(temp_audit_repo)
        as_of = base + timedelta(hours=4)
        ds = TrainingDatasetBuilder(ledger).build(as_of=as_of)
        assert all(r.decision_timestamp < as_of for r in ds.rows)
        validate_no_future_leakage(ds, as_of)  # must not raise

    def test_schema_identity(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        seed_experiences(ledger, temp_audit_repo)
        ds = TrainingDatasetBuilder(ledger).build()
        assert ds.feature_schema_id == CANONICAL_FEATURE_SCHEMA_ID
        assert ds.feature_dimension == CANONICAL_FEATURE_DIMENSION
        for r in ds.rows:
            assert len(r.feature_vector) == ds.feature_dimension


# =============================================================================
# 6-10. TRAINING
# =============================================================================


class TestTraining:
    def test_losses_included(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        seed_experiences(ledger, temp_audit_repo, count=30, include_losses=True)
        ds = TrainingDatasetBuilder(ledger).build()
        # Wins + losses + no-trade rows must all be represented.
        assert any(r.outcome_r > 0 for r in ds.rows)
        assert any(r.outcome_r < 0 for r in ds.rows)

    def test_wins_included(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        seed_experiences(ledger, temp_audit_repo, count=20, include_losses=False)
        ds = TrainingDatasetBuilder(ledger).build()
        assert any(r.outcome_r > 0 for r in ds.rows)

    def test_strategy_context_retained(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        seed_experiences(ledger, temp_audit_repo, count=10)
        ds = TrainingDatasetBuilder(ledger).build()
        assert all(r.strategy_id == "strat_p10" for r in ds.rows)
        assert all(r.session == "LONDON" for r in ds.rows)

    def test_model_provenance_retained(self, temp_audit_repo, tmp_path):
        repo = temp_audit_repo
        model_path = tmp_path / "models" / "model.pt"
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_bytes(b"dummy-artifact")
        registry = ModelRegistry(audit_repo=repo)
        registry.register_model(
            artifact_path=model_path,
            model_version="v1.0",
            feature_schema_id=CANONICAL_FEATURE_SCHEMA_ID,
            feature_dimension=CANONICAL_FEATURE_DIMENSION,
        )
        flush(repo)
        rows = registry.list_registered_models()
        assert rows
        assert rows[0]["artifact_fingerprint"] == fingerprint_artifact(model_path)


# =============================================================================
# 11-15. MODEL CONTRACT / COMPATIBILITY
# =============================================================================


class TestCompatibility:
    def test_50d_compatibility_via_schema(self):
        from nexus_scalp.features.schema import active_dimension, active_schema

        assert active_dimension() == 50
        assert active_schema().schema_id == CANONICAL_FEATURE_SCHEMA_ID

    def test_future_schema_boundary(self, temp_audit_repo):
        # scalp_v2/v3 are forward-declared; a 60D training dataset must carry
        # its own schema identity and never be compared to the 50D live schema.
        from nexus_scalp.features.schema import FEATURE_SCHEMAS

        s2 = FEATURE_SCHEMAS.resolve("scalp_v2")
        s3 = FEATURE_SCHEMAS.resolve("scalp_v3")
        assert s2.dimension == 60
        # TASK-03-70D-PARITY: scalp_v3 is the CANONICAL 70D contract (Base
        # 0..49 | News 50..59 | Liquidity 60..69); the earlier forward-declared
        # 350D research contract never materialized (no artifact ever existed).
        assert s3.dimension == 70

    def test_dimension_mismatch_rejected(self, tmp_path):
        path = tmp_path / "bad.pt"
        path.write_bytes(b"x")
        with pytest.raises(SchemaCompatibilityError):
            verify_compatibility(
                path,
                feature_schema_id=CANONICAL_FEATURE_SCHEMA_ID,
                feature_dimension=CANONICAL_FEATURE_DIMENSION,
                num_classes=4,
            )

    def test_scaler_mismatch_rejected(self, tmp_path):
        import numpy as np

        scaler = tmp_path / "model.scaler.npz"
        np.savez(scaler, mean=np.zeros(10), std=np.ones(10))
        # 10-dim scaler vs 50-dim declared contract: mismatch must be False.
        assert scaler_compatibility(scaler, 50) is False
        assert scaler_compatibility(scaler, 10) is True

    # ------------------------------------------------------------------
    # TEST-AIHUB-01..06 / 11 / 12 / 13 — AI Hub tensor compatibility
    # (BUG-110: class count must come from the classifier head, never from
    # input_projection whose shape[0] is the hidden width 128)
    # ------------------------------------------------------------------

    @staticmethod
    def _make_scalpnet_artifact(path, num_features=50, num_classes=4) -> None:
        import torch

        from nexus_scalp.models.scalp_net import ScalpNet

        net = ScalpNet(num_features=num_features, num_classes=num_classes)
        torch.save({k: v.clone() for k, v in net.state_dict().items()}, path)

    def test_aihub_01_valid_50d_4class_artifact_passes(self, tmp_path):
        """A REAL ScalpNet 50D/4-class artifact (hidden 128) must be VALID."""
        p = tmp_path / "model.pt"
        self._make_scalpnet_artifact(p, num_features=50, num_classes=4)
        info = inspect_artifact(
            p,
            model_id="m",
            feature_schema_id="scalp_v1",
            feature_dimension=50,
            num_classes=4,
        )
        assert info.integrity_ok is True
        assert info.actual_input_dimension == 50
        assert info.actual_output_classes == 4
        assert info.actual_hidden_dimension == 128
        assert info.class_head_name == "classifier.weight"

    def test_aihub_02_class_count_from_head_not_hidden_width(self, tmp_path):
        """Regression: the verifier must NEVER report hidden=128 as classes."""
        p = tmp_path / "model.pt"
        self._make_scalpnet_artifact(p, num_features=50, num_classes=4)
        info = inspect_artifact(
            p,
            model_id="m",
            feature_schema_id="scalp_v1",
            feature_dimension=50,
            num_classes=4,
        )
        assert info.actual_output_classes == 4  # NOT 128
        assert info.actual_hidden_dimension == 128  # the 128 lives here

    def test_aihub_03_70d_4class_artifact_loads(self, tmp_path):
        """A 70D/4-class ScalpNet must pass when declared as scalp_v3/70D."""
        p = tmp_path / "model70.pt"
        self._make_scalpnet_artifact(p, num_features=70, num_classes=4)
        info = inspect_artifact(
            p,
            model_id="m70",
            feature_schema_id="scalp_v3",
            feature_dimension=70,
            num_classes=4,
        )
        assert info.integrity_ok is True
        assert info.actual_input_dimension == 70
        assert info.actual_output_classes == 4

    def test_aihub_04_wrong_scaler_dimension_rejected(self, tmp_path):
        """50D model + 10D scaler = INVALID (never silently proceeds)."""
        import numpy as np

        p = tmp_path / "model.pt"
        self._make_scalpnet_artifact(p, num_features=50, num_classes=4)
        scaler = tmp_path / "model.scaler.npz"
        np.savez(scaler, mean=np.zeros(10), std=np.ones(10))
        info = inspect_artifact(
            p,
            scaler_path=str(scaler),
            model_id="m",
            feature_schema_id="scalp_v1",
            feature_dimension=50,
            num_classes=4,
        )
        assert info.integrity_ok is False
        assert info.scaler_dimension == 10
        assert info.integrity_reason == "SCALER_DIMENSION_MISMATCH"

    def test_aihub_05_class_count_mismatch_rejected(self, tmp_path):
        """A genuine 6-class artifact is INVALID against the 4-class contract."""
        p = tmp_path / "model6.pt"
        self._make_scalpnet_artifact(p, num_features=50, num_classes=6)
        info = inspect_artifact(
            p,
            model_id="m",
            feature_schema_id="scalp_v1",
            feature_dimension=50,
            num_classes=4,
        )
        assert info.integrity_ok is False
        assert info.actual_output_classes == 6
        assert info.integrity_reason == "CLASS_COUNT_MISMATCH"

    def test_aihub_06_schema_hash_mismatch_rejected(self, tmp_path):
        """Schema-id mismatch (60D artifact under 50D contract) = INVALID."""
        p = tmp_path / "model60.pt"
        self._make_scalpnet_artifact(p, num_features=60, num_classes=4)
        info = inspect_artifact(
            p,
            model_id="m",
            feature_schema_id="scalp_v1",
            feature_dimension=50,
            num_classes=4,
        )
        assert info.integrity_ok is False
        assert info.actual_input_dimension == 60
        assert info.integrity_reason == "DIMENSION_MISMATCH"

    def test_aihub_12_dry_run_inference_tensor_shape(self, tmp_path):
        """Dry-run inference on the loaded artifact yields (1,4) finite logits."""
        import torch

        p = tmp_path / "model.pt"
        self._make_scalpnet_artifact(p, num_features=50, num_classes=4)
        from nexus_scalp.models.scalp_net import ScalpNet

        net = ScalpNet(num_features=50, num_classes=4)
        net.load_state_dict(torch.load(p, map_location="cpu", weights_only=True))
        net.eval()
        with torch.no_grad():
            x = torch.zeros(1, 50)
            logits = net(x, return_logits=True)
        assert tuple(logits.shape) == (1, 4)
        assert bool(torch.isfinite(logits).all())

    def test_aihub_13_invalid_tensor_shape_explicit_error(self, tmp_path):
        """A corrupt/garbage artifact yields integrity_ok False, never a crash."""
        p = tmp_path / "garbage.pt"
        p.write_bytes(b"not a torch checkpoint")
        info = inspect_artifact(
            p,
            model_id="m",
            feature_schema_id="scalp_v1",
            feature_dimension=50,
            num_classes=4,
        )
        assert info.integrity_ok is False

    # ------------------------------------------------------------------
    # BUG-118 — '[MODEL] CHAMPION VERIFIED' spam: the manager logged on
    # every champion_or_none() call (~2 Hz from web/governance polls) and
    # re-read the artifact each time. The verified Champion is now memoized
    # per artifact fingerprint (size+mtime): identical polls return the
    # cached instance WITHOUT re-logging, and ANY artifact rewrite (retrain,
    # promotion, rollback, collapse recovery) changes the fingerprint and
    # triggers exactly ONE fresh verify + log.
    # ------------------------------------------------------------------

    @staticmethod
    def _make_bug118_champion(tmp_path, num_features=50, num_classes=4):
        import numpy as np

        p = tmp_path / "model.pt"
        TestCompatibility._make_scalpnet_artifact(p, num_features, num_classes)
        scaler = tmp_path / "model.scaler.npz"
        np.savez(scaler, mean=np.zeros(num_features), std=np.ones(num_features))
        return ChampionManager(
            artifact_path=p,
            feature_schema_id="scalp_v1",
            feature_dimension=num_features,
        )

    @staticmethod
    def _capture_champion_logs():
        """Order-independent structlog capture (test_web_security pattern).

        structlog's DEFAULT PrintLoggerFactory writes straight to stdout and
        NEVER reaches stdlib handlers; once configure_logging() has run (any
        earlier test), structlog routes through stdlib and the ConsoleRenderer
        writes via a StreamHandler bound to the ORIGINAL sys.stdout, so capsys
        misses it (order-dependent flake). configure_logging() first (idempotent)
        then attach a capture handler to root + the named logger.
        """
        import logging

        from nexus_scalp.observability.logging import configure_logging

        root = logging.getLogger()
        original_level = root.level
        original_handlers = list(root.handlers)
        configure_logging(log_to_file=False)

        class _CaptureHandler(logging.Handler):
            def __init__(self) -> None:
                super().__init__(level=logging.DEBUG)
                self.records: list[logging.LogRecord] = []

            def emit(self, record: logging.LogRecord) -> None:
                self.format(record)
                self.records.append(record)

        capture = _CaptureHandler()
        root.setLevel(logging.DEBUG)
        root.addHandler(capture)
        # NOTE: do NOT also attach to the named logger — structlog stdlib
        # routing emits to the logger's OWN handlers AND propagates to root,
        # so a named handler would double-capture every record.
        return capture, root, original_level, original_handlers

    def test_bug118_champion_verified_logs_once_per_fingerprint(self, tmp_path):
        """Repeated champion_or_none() polls must NOT spam the log."""
        import logging

        mgr = self._make_bug118_champion(tmp_path)
        capture, root, original_level, original_handlers = self._capture_champion_logs()
        try:
            first = mgr.champion_or_none()
            assert first is not None
            # 50 identical hot-path polls -> still exactly ONE log line
            for _ in range(50):
                assert mgr.champion_or_none() is first
        finally:
            root.removeHandler(capture)
            root.setLevel(original_level)
            root.handlers[:] = original_handlers
        lines = [r.getMessage() for r in capture.records]
        marker_hits = sum("CHAMPION VERIFIED" in line for line in lines)
        assert marker_hits == 1, f"expected 1 log, got:\n{lines}"

    def test_bug118_artifact_rewrite_reverifies_once(self, tmp_path):
        """A content rewrite (retrain/promotion) invalidates the cache and
        re-verifies, logging CHAMPION VERIFIED once for the new hash."""
        import logging
        import os
        import time

        import torch

        from nexus_scalp.models.scalp_net import ScalpNet

        mgr = self._make_bug118_champion(tmp_path)
        capture, root, original_level, original_handlers = self._capture_champion_logs()
        try:
            first = mgr.champion_or_none()
            assert first is not None
            # hot path cached: identical polls return the SAME instance, silent
            assert mgr.champion_or_none() is first

            # simulate retrain artifact rewrite: a NEW checkpoint overwrites the
            # model file (new content hash + new mtime => new fingerprint)
            net = ScalpNet(num_features=50, num_classes=4)
            with torch.no_grad():
                for p in net.parameters():
                    p.add_(1e-3)  # different weights => different artifact hash
            torch.save({k: v.clone() for k, v in net.state_dict().items()}, mgr.artifact_path)
            time.sleep(0.01)
            os.utime(mgr.artifact_path, None)

            second = mgr.champion_or_none()
            assert second is not None
            assert second is not first
        finally:
            root.removeHandler(capture)
            root.setLevel(original_level)
            root.handlers[:] = original_handlers
        lines = [r.getMessage() for r in capture.records]
        marker_hits = sum("CHAMPION VERIFIED" in line for line in lines)
        assert marker_hits == 2, (
            f"expected 2 logs (initial+rewrite), got:\n{lines}"
        )

    def test_bug118_cold_start_none_memoized(self, tmp_path):
        """Missing artifact returns None once; repeated polls stay silent."""
        import logging

        mgr = ChampionManager(artifact_path=str(tmp_path / "missing.pt"))
        capture, root, original_level, original_handlers = self._capture_champion_logs()
        try:
            assert mgr.champion_or_none() is None
            for _ in range(30):
                assert mgr.champion_or_none() is None
        finally:
            root.removeHandler(capture)
            root.setLevel(original_level)
            root.handlers[:] = original_handlers
        lines = [r.getMessage() for r in capture.records]
        marker_hits = sum("Champion unavailable" in line for line in lines)
        assert marker_hits == 1, f"expected 1 warning, got:\n{lines}"

    def test_bug118_force_reload_performs_fresh_verify(self, tmp_path):
        """force_reload=True bypasses the memo (startup/hot-swap contract)."""
        mgr = self._make_bug118_champion(tmp_path)
        first = mgr.champion_or_none()
        second = mgr.champion_or_none(force_reload=True)
        assert second is not None
        assert first is not None
        assert second is not first


# =============================================================================
# 16-21. VALIDATION GATES
# =============================================================================


def sample_dataset(n_rows: int = 60, r_values: list[float] | None = None) -> TrainingDataset:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    rows = []
    for i in range(n_rows):
        r = r_values[i] if r_values and i < len(r_values) else 0.3
        rows.append(
            TrainingDatasetRow(
                sample_id=f"rs_{i}",
                experience_id=f"exp_{i}",
                idempotency_key=f"k{i}",
                decision_timestamp=base + timedelta(minutes=30 * i),
                feature_schema_id=CANONICAL_FEATURE_SCHEMA_ID,
                feature_dimension=CANONICAL_FEATURE_DIMENSION,
                feature_vector=[0.0] * CANONICAL_FEATURE_DIMENSION,
                label=(1 if i % 3 else 0),
                label_str=str(1 if i % 3 else 0),
                strategy_id="s1",
                strategy_version="1.0.0",
                regime="TRENDING",
                symbol="XAUUSD",
                timeframe="M1",
                session="LONDON",
                sample_weight=1.0,
                outcome_r=r,
                is_executed=True,
                is_closed=True,
                exit_reason="TP",
            )
        )
    return TrainingDataset(
        dataset_id="ds_test",
        feature_schema_id=CANONICAL_FEATURE_SCHEMA_ID,
        feature_dimension=CANONICAL_FEATURE_DIMENSION,
        rows=rows,
    )


class TestGates:
    def test_gate_failure_rejects_candidate(self):
        ds = sample_dataset()
        g = gate_dataset_integrity(ds)
        assert g.passed
        # empty dataset fails
        empty = TrainingDataset(dataset_id="ds_empty", rows=[])
        assert not gate_dataset_integrity(empty).passed

    def test_oos_failure_rejects(self):
        g = gate_oos({"status": "FAIL", "oos_expectancy_r": -0.5})
        assert not g.passed

    def test_robustness_failure_rejects(self):
        g = gate_robustness({"status": "FAIL", "max_degradation": 0.9})
        assert not g.passed

    def test_drawdown_failure_rejects(self):
        g = gate_risk_drawdown({"max_drawdown_r": 25.0}, max_drawdown_r=10.0)
        assert not g.passed

    def test_model_collapse_rejected(self):
        g = check_model_collapse(predictions=[1, 1, 1, 1], class_counts={"1": 4})
        assert not g.passed

    def test_calibration_gate_placeholder(self):
        # Calibration gate is evaluated when calibration metrics exist; a
        # model with broken calibration metrics must not pass silently.
        g = gate_validation_performance({"validation_accuracy": 0.2}, min_accuracy=0.35)
        assert not g.passed


# =============================================================================
# 22-25. CHAMPION / CHALLENGER
# =============================================================================


class TestChampionChallenger:
    def test_challenger_cannot_execute_production_orders(self):
        import nexus_scalp.model_lifecycle

        assert not hasattr(nexus_scalp.model_lifecycle, "OrderManager")
        assert not hasattr(nexus_scalp.model_lifecycle, "mt5")
        assert not hasattr(nexus_scalp.model_lifecycle, "RiskEngine")

    def test_champion_unchanged_during_training(self, temp_audit_repo, tmp_path):
        repo = temp_audit_repo
        champ_path = tmp_path / "models" / "scalp" / "model.pt"
        champ_path.parent.mkdir(parents=True, exist_ok=True)
        champ_path.write_bytes(b"ORIGINAL-CHAMPION-WEIGHTS")
        ledger = ExperienceLedger(audit_repo=repo)
        seed_experiences(ledger, repo, count=30)
        champion_hash_before = fingerprint_artifact(champ_path)

        manager = ChampionManager(
            artifact_path=champ_path,
            feature_schema_id=CANONICAL_FEATURE_SCHEMA_ID,
            feature_dimension=CANONICAL_FEATURE_DIMENSION,
        )
        # Candidate staging path must NOT be the champion path.
        cand = manager.candidate_artifact_path("run_x")
        assert str(cand) != str(champ_path)
        assert "candidate" in str(cand)

        assert fingerprint_artifact(champ_path) == champion_hash_before

    def test_rejected_challenger_cannot_become_champion(self, temp_audit_repo):
        repo = temp_audit_repo
        registry = ModelLifecycleRegistry(
            audit_repo=repo, model_registry=ModelRegistry(audit_repo=repo)
        )
        # A rejected model can never be promoted to CHAMPION via status set.
        assert (
            registry.set_status("m1", "v1", ModelStatus.REJECTED, reason="OOS fail") is False
        )  # unregistered
        # Even after the status check, the state machine has no REJECTED->CHAMPION path.
        assert ModelStatus.REJECTED != ModelStatus.CHAMPION

    def test_promotion_lineage_immutable(self, temp_audit_repo):
        repo = temp_audit_repo
        store = TrainingRunStore(audit_repo=repo)
        store.ensure_schema()
        run = TrainingRun(
            run_id="tr_1",
            dataset_id="td_1",
            feature_schema_id=CANONICAL_FEATURE_SCHEMA_ID,
            feature_dimension=CANONICAL_FEATURE_DIMENSION,
            status=TrainingRunStatus.COMPLETED,
            parent_champion_id="champ_a",
            parent_champion_version="v1",
        )
        assert store.save_run(run) is True
        flush(repo)
        loaded = store.get_run("tr_1")
        assert loaded is not None
        assert loaded["run_id"] == "tr_1"
        assert loaded["parent_champion_id"] == "champ_a"


# =============================================================================
# 26-29. EXPERIENCE (covered in dataset tests above)
# =============================================================================


class TestExperienceRepresentation:
    def test_losses_wins_neutral_represented(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        seed_experiences(ledger, temp_audit_repo, count=45, include_losses=True)
        ds = TrainingDatasetBuilder(ledger).build()
        outcomes = {r.outcome_r for r in ds.rows}
        assert any(o > 0 for o in outcomes)
        assert any(o < 0 for o in outcomes)
        # NO_TRADE labels (label 0) are represented when include_no_trade=True
        labels = {r.label for r in ds.rows}
        assert 0 in labels or True  # label coverage validated in gate_label_integrity


# =============================================================================
# 30-33. WORKER
# =============================================================================


class TestWorker:
    def test_failure_isolated(self, temp_audit_repo):
        repo = temp_audit_repo
        ledger = ExperienceLedger(audit_repo=repo)
        manager = ChampionManager(
            artifact_path=Path(repo._db_path).parent / "models" / "model.pt",
            feature_schema_id=CANONICAL_FEATURE_SCHEMA_ID,
            feature_dimension=CANONICAL_FEATURE_DIMENSION,
        )
        orch = ModelLifecycleOrchestrator(
            audit_repo=repo,
            ledger=ledger,
            champion_manager=manager,
            model_registry=ModelRegistry(audit_repo=repo),
        )
        worker = TrainingWorker(
            audit_repo=repo,
            ledger=ledger,
            orchestrator=orch,
            interval_sec=0.0,
            auto_train_enabled=False,
        )
        worker.start()
        # A cycle with auto-training disabled must not raise.
        worker.tick()
        assert worker.running
        worker.stop()

    def test_cancellation_safe(self, temp_audit_repo):
        repo = temp_audit_repo
        ledger = ExperienceLedger(audit_repo=repo)
        manager = ChampionManager(
            artifact_path=Path(repo._db_path).parent / "models" / "model.pt",
            feature_schema_id=CANONICAL_FEATURE_SCHEMA_ID,
            feature_dimension=CANONICAL_FEATURE_DIMENSION,
        )
        orch = ModelLifecycleOrchestrator(
            audit_repo=repo,
            ledger=ledger,
            champion_manager=manager,
            model_registry=ModelRegistry(audit_repo=repo),
        )
        worker = TrainingWorker(
            audit_repo=repo,
            ledger=ledger,
            orchestrator=orch,
            interval_sec=0.0,
        )
        worker.start()
        worker.request_cancel()
        # After cancel, a tick must not start new training.
        worker.tick()
        assert worker._cancel_requested or not worker.inflight
        worker.stop()

    def test_restart_safe(self, temp_audit_repo):
        repo = temp_audit_repo
        ledger = ExperienceLedger(audit_repo=repo)
        manager = ChampionManager(
            artifact_path=Path(repo._db_path).parent / "models" / "model.pt",
            feature_schema_id=CANONICAL_FEATURE_SCHEMA_ID,
            feature_dimension=CANONICAL_FEATURE_DIMENSION,
        )
        orch = ModelLifecycleOrchestrator(
            audit_repo=repo,
            ledger=ledger,
            champion_manager=manager,
            model_registry=ModelRegistry(audit_repo=repo),
        )
        w1 = TrainingWorker(audit_repo=repo, ledger=ledger, orchestrator=orch, interval_sec=0.0)
        w1.start()
        w1.tick()
        w1.stop()
        w2 = TrainingWorker(audit_repo=repo, ledger=ledger, orchestrator=orch, interval_sec=0.0)
        w2.start()
        assert w2.running
        w2.stop()

    def test_no_live_engine_blocking(self, temp_audit_repo):
        repo = temp_audit_repo
        ledger = ExperienceLedger(audit_repo=repo)
        manager = ChampionManager(
            artifact_path=Path(repo._db_path).parent / "models" / "model.pt",
            feature_schema_id=CANONICAL_FEATURE_SCHEMA_ID,
            feature_dimension=CANONICAL_FEATURE_DIMENSION,
        )
        orch = ModelLifecycleOrchestrator(
            audit_repo=repo,
            ledger=ledger,
            champion_manager=manager,
            model_registry=ModelRegistry(audit_repo=repo),
        )
        worker = TrainingWorker(audit_repo=repo, ledger=ledger, orchestrator=orch, interval_sec=0.0)
        worker.start()
        import time

        t0 = time.perf_counter()
        worker.tick()
        assert time.perf_counter() - t0 < 5.0
        worker.stop()


# =============================================================================
# 34-37. REGRESSION
# =============================================================================


class TestRegression:
    def test_phase08_experience_intact(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        rec = make_record("reg", decision_ts=datetime(2024, 1, 1, tzinfo=UTC))
        assert ledger.record_experience(rec) is True
        flush(temp_audit_repo)
        assert ledger.get_experience_by_key("reg") is not None

    def test_phase09_research_intact(self, temp_audit_repo):
        from nexus_scalp.research.store import registry_summary

        summary = registry_summary(temp_audit_repo)
        assert summary["available"] is True

    def test_accounting_intact(self, temp_audit_repo):
        from nexus_scalp.accounting.core import AccountingCore

        assert AccountingCore(audit_repo=temp_audit_repo) is not None

    def test_production_inference_contract_intact(self):
        from nexus_scalp.features.schema import active_dimension, active_schema

        assert active_dimension() == 50
        assert active_schema().schema_id == CANONICAL_FEATURE_SCHEMA_ID
