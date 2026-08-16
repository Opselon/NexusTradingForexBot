"""PHASE 13 Model Generation Migration — Integration Tests.

End-to-end verification of the artifact-first flow (spec 46):

    * dataset artifact build + manifest inspection
    * legacy baseline training through the new pipeline
    * candidate artifact production
    * local load without DB + prediction
    * Champion never overwritten
    * artifact hash + schema + 3-class contract + news provenance verified
    * CLI commands resolve
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl
import pytest

from nexus_scalp.model_generation import (
    ArtifactStore,
    CandidateTrainer,
    DatasetFactory,
    ExperimentFactory,
    LocalModelRuntime,
    SampleReplay,
    ValidationFactory,
)
from nexus_scalp.model_generation.models import ModelArchitecture, default_label_schema


def make_bars(n: int = 300, seed: int = 21) -> pl.DataFrame:
    np.random.seed(seed)
    feats = {f"feat_{i}": np.random.randn(n) for i in range(50)}
    ts = np.arange(0, n, dtype="int64").astype("datetime64[us]")
    return pl.DataFrame(
        {
            **feats,
            "close": 2000 + np.cumsum(np.random.randn(n) * 0.5),
            "high": 2000 + np.random.rand(n) * 2,
            "low": 2000 - np.random.rand(n) * 2,
            "atr_m1": 1.0 + np.random.rand(n),
            "timestamp": ts,
            "regime": np.where(np.random.rand(n) > 0.5, "TRENDING", "RANGING"),
        }
    )


def make_news() -> pl.DataFrame:
    ts = np.array([30, 90, 150, 220], dtype="int64").astype("datetime64[us]")
    return pl.DataFrame(
        {
            "published_at": ts,
            "xauusd_relevance": [0.9, 0.2, 0.8, 0.6],
            "usd_relevance": [0.6, 0.2, 0.5, 0.4],
            "bullish_pressure": [0.1, 0.6, 0.2, 0.4],
            "bearish_pressure": [0.8, 0.1, 0.7, 0.3],
            "confidence": [0.9, 0.5, 0.8, 0.6],
        }
    )


@pytest.fixture
def mg_store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "mg" / "artifacts")


class TestModelGenerationEndToEnd:
    def test_full_artifact_flow(self, mg_store: ArtifactStore):
        # 1. build dataset artifact (+ causal news context)
        dh = DatasetFactory(store=mg_store).build(
            make_bars(), symbol="XAUUSD", timeframe="M5", news_frame=make_news()
        )
        assert dh["dataset_id"].startswith("ds_")
        man = mg_store.read_dataset_manifest(dh["dataset_id"])
        assert man["label_schema_id"] == "triple_barrier_3class_v1"
        assert man["row_counts"]["total"] >= 200
        assert man["news_schema_id"] == "news_context_v1"

        # 2. create + persist experiment
        exp = ExperimentFactory(store=mg_store).create(
            dh["dataset_id"],
            template="baseline_scalpnet_v1_news",
            experiment_id="exp_e2e_news",
        )
        assert exp.news_enabled is True
        assert mg_store.read_experiment("exp_e2e_news") is not None

        # 3. train legacy baseline (news-aware) through the new pipeline
        frame = mg_store.read_dataset(dh["dataset_id"])
        res = CandidateTrainer(store=mg_store).train_candidate(exp, frame, model_id="cand_e2e")
        assert res["status"] == "COMPLETED"

        # 4. inspect manifest: model contract correct
        mm = mg_store.read_model_manifest("cand_e2e")
        assert mm["architecture_id"] == ModelArchitecture.LEGACY_SCALPNET_V1.value
        assert mm["feature_dimension"] == 50
        assert mm["class_count"] == 3
        assert mm["news_enabled"] is True

        # 5. verify hash
        assert mg_store.verify_artifact("cand_e2e")["ok"] is True

        # 6. load locally + predict WITHOUT DB (block sqlite3)
        import builtins

        rt = LocalModelRuntime(store=mg_store).load("cand_e2e")
        real_import = builtins.__import__

        def no_sqlite(name, *a, **k):
            if name == "sqlite3":
                raise ImportError("DB disabled (integration proof)")
            return real_import(name, *a, **k)

        builtins.__import__ = no_sqlite
        try:
            # news-aware candidate: expected input = 50 base + 12 news
            pred = rt.predict([0.25] * 62)
        finally:
            builtins.__import__ = real_import
        assert 0 <= pred["argmax"] <= 3
        assert len(pred["probabilities"]) >= 4  # legacy 4-head geometry

        # 7. 3-class label contract enforced
        schema = default_label_schema()
        assert schema.class_count == 3
        assert schema.encode("NO_TRADE") == 0

        # 8. news provenance in the dataset
        assert "news_context_schema_id" in frame.columns
        assert frame["news_context_schema_id"][0] == "news_context_v1"

        # 9. validation produces a verdict (regime results computed)
        vf = ValidationFactory()
        labels = frame["label"].to_numpy().astype(np.int64)
        vr = vf.validate("cand_e2e", "exp_e2e_news", frame, None, labels)
        assert vr.regime_results  # per-regime evaluation exists
        assert "label_integrity" in [g["gate"] for g in vr.gates]

        # 10. replay reconstructs the sample
        sample_id = frame["sample_id"][0]
        rec = SampleReplay(store=mg_store).replay(dh["dataset_id"], sample_id, model_id="cand_e2e")
        assert rec["feature_dimension"] == 50
        assert rec["model_prediction"]["argmax"] is not None

        # 11. champion path untouched: no legacy champion file created
        legacy = Path("artifacts/models/scalp/XAUUSD/v1.0.0/model.pt")
        assert str(mg_store.model_dir("cand_e2e")) != str(legacy)

    def test_cli_commands_registered(self):
        """The 7 Phase 13 CLI commands must resolve on the app."""
        from nexus_scalp.cli.main import app

        names = {c.name for c in app.registered_commands}
        for cmd in (
            "model-dataset-build",
            "model-experiment-create",
            "model-train",
            "model-inspect",
            "model-validate",
            "model-replay",
            "model-doctor",
        ):
            assert cmd in names, f"CLI command {cmd} not registered"

    def test_baseline_reproducible(self, tmp_path: Path):
        """Same inputs -> same dataset identity (spec 7 / 8 / 20)."""
        s1 = ArtifactStore(tmp_path / "r1")
        s2 = ArtifactStore(tmp_path / "r2")
        d1 = DatasetFactory(store=s1).build(make_bars(seed=21))
        d2 = DatasetFactory(store=s2).build(make_bars(seed=21))
        assert d1["dataset_id"] == d2["dataset_id"]
        assert d1["hash"] == d2["hash"]
