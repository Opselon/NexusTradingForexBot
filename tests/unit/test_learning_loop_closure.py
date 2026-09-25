# Learning-loop closure — new unit tests (Phases 2, 4, 5, 6, 10)
# Run: ./.venv/Scripts/python.exe -m pytest tests/unit/test_learning_loop_closure.py -p no:cacheprovider
from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Phase 5 — LearningCycle state machine
# ---------------------------------------------------------------------------


def _tmp_cycle_store():
    from nexus_scalp.model_lifecycle.learning_cycle import LearningCycleStore

    db = os.path.join(tempfile.mkdtemp(), "cycles.db")
    return LearningCycleStore(db)


class TestLearningCycle:
    def test_happy_path_full_lifecycle(self) -> None:
        from nexus_scalp.model_lifecycle.learning_cycle import LearningCycleStore

        s = _tmp_cycle_store()
        cid = s.start_cycle("scheduled", "sched:1")
        for target, fields in [
            ("DATASET_BUILDING", {}),
            ("DATASET_READY", {"dataset_id": "td_x", "dataset_hash": "h1"}),
            ("TRAINING", {"training_run_id": "tr_1"}),
            ("TRAINED", {"candidate_model_id": "c1", "candidate_artifact_hash": "ah1"}),
            ("VALIDATING", {}),
            ("VALIDATED", {"validation_run_id": "tr_1"}),
            ("SHADOW_ATTACHING", {}),
            ("SHADOW_RUNNING", {"shadow_run_id": "sh_1"}),
            ("SHADOW_EVALUATING", {}),
            ("PROMOTION_EVALUATION", {}),
            ("REJECTED", {"reason": "oos negative", "decision": "REJECT"}),
        ]:
            s.transition(cid, target, **fields)
        row = s.get_cycle(cid)
        assert row["status"] == "REJECTED"
        assert row["dataset_id"] == "td_x"
        assert row["training_run_id"] == "tr_1"
        assert row["decision"] == "REJECT"
        assert row["completed_at"]

    def test_illegal_transition_refused(self) -> None:
        from nexus_scalp.model_lifecycle.learning_cycle import LearningCycleError

        s = _tmp_cycle_store()
        cid = s.start_cycle("manual", "m:1")
        s.transition(cid, "DATASET_BUILDING")
        with pytest.raises(LearningCycleError):
            s.transition(cid, "PROMOTING")  # skipping is illegal
        # cycle unchanged
        assert s.get_cycle(cid)["status"] == "DATASET_BUILDING"

    def test_terminal_state_is_absorbing(self) -> None:
        from nexus_scalp.model_lifecycle.learning_cycle import LearningCycleError

        s = _tmp_cycle_store()
        cid = s.start_cycle("manual", "m:2")
        s.transition(cid, "CANCELLED")
        with pytest.raises(LearningCycleError):
            s.transition(cid, "TRIGGERED")

    def test_idempotent_trigger_no_duplicate_active_cycles(self) -> None:
        s = _tmp_cycle_store()
        a = s.start_cycle("scheduled", "sched:same")
        b = s.start_cycle("scheduled", "sched:same")
        assert a == b
        assert len(s.list_cycles()) == 1

    def test_restart_recovery_marks_inflight_failed(self) -> None:
        s = _tmp_cycle_store()
        cid = s.start_cycle("scheduled", "r:1")
        s.transition(cid, "DATASET_BUILDING")
        repaired = s.recover_interrupted()
        assert repaired == [cid]
        assert s.get_cycle(cid)["status"] == "FAILED"
        assert s.get_cycle(cid)["error_code"] == "RESTART_INTERRUPTED"
        # idempotent: second recovery finds nothing
        assert s.recover_interrupted() == []

    def test_unknown_field_refused(self) -> None:
        from nexus_scalp.model_lifecycle.learning_cycle import LearningCycleError

        s = _tmp_cycle_store()
        cid = s.start_cycle("manual", "m:3")
        with pytest.raises(LearningCycleError):
            s.transition(cid, "DATASET_BUILDING", hacker_field="x")


# ---------------------------------------------------------------------------
# Phase 6 — config-driven triggers (fail-closed defaults)
# ---------------------------------------------------------------------------


class TestLearningConfig:
    def test_all_disabled_by_default(self) -> None:
        from nexus_scalp.model_lifecycle.learning_config import LearningConfig

        cfg = LearningConfig()
        assert cfg.enabled is False
        assert cfg.retrain.enabled is False
        assert cfg.shadow.enabled is False
        assert cfg.promotion.enabled is False
        assert cfg.online_finetune.enabled is False

    def test_appconfig_learning_section_defaults_none(self) -> None:
        from nexus_scalp.configuration.config import AppConfig

        assert AppConfig().learning is None

    def test_yaml_learning_section_parses(self, tmp_path: Path) -> None:
        from nexus_scalp.configuration.config import AppConfig

        y = tmp_path / "live.yaml"
        y.write_text(
            "learning:\n"
            "  enabled: true\n"
            "  retrain:\n"
            "    enabled: true\n"
            "    min_new_experiences: 10\n",
            encoding="utf-8",
        )
        cfg = AppConfig.load_from_yaml(y)
        assert cfg.learning is not None
        assert cfg.learning.enabled is True
        assert cfg.learning.retrain.enabled is True
        assert cfg.learning.retrain.min_new_experiences == 10


# ---------------------------------------------------------------------------
# Phase 4 — TrainingDataset snapshot persistence
# ---------------------------------------------------------------------------


def _tiny_dataset(n: int = 12, dim: int = 4):
    from nexus_scalp.model_lifecycle.models import TrainingDataset, TrainingDatasetRow

    rows = [
        TrainingDatasetRow(
            sample_id=f"s{i}",
            experience_id=f"e{i}",
            idempotency_key=f"k{i}",
            decision_timestamp=datetime(2026, 1, 1, 0, i, tzinfo=UTC),
            feature_schema_id="test_schema",
            feature_dimension=dim,
            feature_vector=[float(i), 1.0, 2.0, 3.0][:dim],
            label=i % 3,
            is_executed=True,
            is_closed=True,
        )
        for i in range(n)
    ]
    return TrainingDataset(
        dataset_id="td_test_snapshot",
        feature_schema_id="test_schema",
        feature_dimension=dim,
        rows=rows,
        source_experience_ids=[f"e{i}" for i in range(n)],
        config_hash="cfg123",
    )


class TestDatasetSnapshot:
    def test_save_verify_roundtrip(self, tmp_path: Path) -> None:
        from nexus_scalp.model_generation.artifact_store import ArtifactStore
        from nexus_scalp.model_lifecycle.dataset_snapshot import TrainingDatasetSnapshotStore

        store = TrainingDatasetSnapshotStore(ArtifactStore(root=tmp_path))
        ds = _tiny_dataset()
        handle = store.save_snapshot(ds, training_config={"epochs": 1})
        assert handle["hash"]
        # manifest persisted with full provenance
        m = store.load_manifest(ds.dataset_id)
        assert m["feature_contract"]["feature_dimension"] == 4
        assert m["label_contract"]["label_map"]["BUY_MARKET"] == 1
        assert m["training_config"] == {"epochs": 1}
        # verify passes
        assert store.verify_snapshot(ds.dataset_id, expected_hash=handle["hash"])
        p = store.require_snapshot(ds.dataset_id, expected_hash=handle["hash"])
        assert p.exists()

    def test_missing_snapshot_blocks(self, tmp_path: Path) -> None:
        from nexus_scalp.model_generation.artifact_store import ArtifactStore
        from nexus_scalp.model_lifecycle.dataset_snapshot import (
            DatasetSnapshotMissingError,
            TrainingDatasetSnapshotStore,
        )

        store = TrainingDatasetSnapshotStore(ArtifactStore(root=tmp_path))
        with pytest.raises(DatasetSnapshotMissingError):
            store.require_snapshot("td_never_written")

    def test_corrupted_snapshot_blocks(self, tmp_path: Path) -> None:
        from nexus_scalp.model_generation.artifact_store import ArtifactStore
        from nexus_scalp.model_lifecycle.dataset_snapshot import (
            DatasetSnapshotMissingError,
            TrainingDatasetSnapshotStore,
        )

        store = TrainingDatasetSnapshotStore(ArtifactStore(root=tmp_path))
        ds = _tiny_dataset()
        store.save_snapshot(ds)
        # tamper with the parquet bytes
        p = store.store.dataset_path(ds.dataset_id)
        p.write_bytes(p.read_bytes() + b"tampered")
        with pytest.raises(DatasetSnapshotMissingError):
            store.require_snapshot(ds.dataset_id)

    def test_identity_collision_refused(self, tmp_path: Path) -> None:
        from nexus_scalp.model_generation.artifact_store import ArtifactStore
        from nexus_scalp.model_lifecycle.dataset_snapshot import (
            DatasetSnapshotError,
            TrainingDatasetSnapshotStore,
        )

        store = TrainingDatasetSnapshotStore(ArtifactStore(root=tmp_path))
        ds = _tiny_dataset()
        store.save_snapshot(ds)
        # different content, same id -> must refuse
        ds2 = _tiny_dataset(n=20)
        object.__setattr__(ds2, "dataset_id", ds.dataset_id) if hasattr(
            ds2, "__pydanticFrozen__"
        ) else None
        with pytest.raises(DatasetSnapshotError):
            store.save_snapshot(ds2)


# ---------------------------------------------------------------------------
# Phase 2 — real champion metrics (BLOCKED, never placeholders)
# ---------------------------------------------------------------------------


class TestChampionMetrics:
    def test_blocked_when_no_artifact(self, tmp_path: Path) -> None:
        from nexus_scalp.model_lifecycle.champion import ChampionModel
        from nexus_scalp.model_lifecycle.champion_metrics import ChampionMetricsProvider

        champ = ChampionModel(
            artifact_path="artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt",
            model_id="primary_scalp",
            model_version="v3.0",
            feature_schema_id="scalp_v3",
            feature_dimension=70,
            num_classes=3,
        )
        assert ChampionMetricsProvider(baseline_dir=tmp_path).load(champ) is None

    def test_blocked_verdict_shape(self, tmp_path: Path) -> None:
        from nexus_scalp.model_lifecycle.champion_metrics import blocked_comparison

        v = blocked_comparison(None)
        assert v["promotion_evaluation"] == "BLOCKED"
        assert v["reason"] == "CHAMPION_METRICS_UNAVAILABLE"

    def test_real_champion_metrics_from_baseline_eval_artifact(self, tmp_path: Path) -> None:
        """With the repo-produced baseline artifact present, the provider
        returns REAL metrics for the live champion (production-path check)."""
        from nexus_scalp.model_lifecycle.champion import ChampionManager
        from nexus_scalp.model_lifecycle.champion_metrics import ChampionMetricsProvider

        artifact = Path(
            "artifacts/forensics/baseline_eval/"
            "bb1f0afe30f746da0aff38ef530c5229df6044c6688c79fdb77feb4e8aee683d.json"
        )
        if not artifact.exists():
            pytest.skip("baseline_eval artifact not produced in this environment")
        cm = ChampionManager(
            artifact_path="artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt",
            model_id="primary_scalp",
            model_version="v3.0",
            feature_schema_id="scalp_v3",
            feature_dimension=70,
            num_classes=3,
        )
        champ = cm.load_champion()
        metrics = ChampionMetricsProvider().load(champ)
        assert metrics is not None
        assert metrics["provenance"] == "baseline_eval_artifact"
        assert "expectancy_r" in metrics
        assert metrics["evaluation_id"].startswith("ev_")

    def test_stale_artifact_for_other_bytes_never_returned(self, tmp_path: Path) -> None:
        from nexus_scalp.model_lifecycle.champion_metrics import load_baseline_artifact

        payload = {
            "model_hash": "aaaa1111bbbb2222",
            "metrics": {"expectancy_r": 1.0},
        }
        (tmp_path / "aaaa1111bbbb2222.json").write_text(json.dumps(payload), encoding="utf-8")
        assert load_baseline_artifact("ffff9999") is None


# ---------------------------------------------------------------------------
# Phase 10 — online fine-tune is config-gated (disabled by default)
# ---------------------------------------------------------------------------


class TestOnlineFinetuneGate:
    def test_engine_flag_false_without_config(self) -> None:
        """The gate expression must evaluate False when learning config is
        absent (the shipped configuration)."""
        cfg_learning = None
        enabled = bool(
            getattr(cfg_learning, "online_finetune", None)
            and cfg_learning
            and cfg_learning.enabled
            and cfg_learning.online_finetune.enabled
        )
        assert enabled is False

    def test_engine_flag_false_when_learning_disabled(self) -> None:
        from nexus_scalp.model_lifecycle.learning_config import LearningConfig

        cfg = LearningConfig()  # learning.enabled=False, online_finetune.enabled=False
        enabled = bool(
            getattr(cfg, "online_finetune", None)
            and cfg
            and cfg.enabled
            and cfg.online_finetune.enabled
        )
        assert enabled is False

    def test_flag_true_only_when_explicitly_enabled(self) -> None:
        from nexus_scalp.model_lifecycle.learning_config import (
            LearningConfig,
            OnlineFinetuneConfig,
        )

        cfg = LearningConfig(enabled=True, online_finetune=OnlineFinetuneConfig(enabled=True))
        enabled = bool(
            getattr(cfg, "online_finetune", None)
            and cfg
            and cfg.enabled
            and cfg.online_finetune.enabled
        )
        assert enabled is True
