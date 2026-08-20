"""BUG-123: build the deterministic 70D scalp_v3 PROOF artifact.

The artifact is produced with the repo's OWN training/model writer primitives
(ScalpNet architecture, ModelFactory state_dict shape, ArtifactStore
save_model_artifact manifest writer) so the tensor width, manifest and scaler
are all consistent and governance-verifiable. It is a compact proof (10
epochs x 256 rows, deterministic seed) — NEVER a production trained model:
it exists only so the BUG-123 matrix tests can exercise a REAL 70D model end
to end (compatibility PASS + live-style inference through the repo's own
LocalModelRuntime.predict path).

Output: artifacts/model_generation/models/liq70_proof/model.{pt,scaler.npz}
+ model.json manifest (feature_schema_id=scalp_v3, dimension 70, tensor 70,
feature_schema_hash = canonical 235b8fcc...).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from nexus_scalp.features.schema_contract import (
    DIMENSION,
    SCHEMA_ID,
    feature_schema_hash,
)
from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.models.scalp_net import ScalpNet

ROOT = Path("artifacts/model_generation")
MODEL_ID = "liq70_proof"
SEED = 42


def main() -> None:
    rng = np.random.default_rng(SEED)
    torch.manual_seed(SEED)

    n = 256
    X = np.clip(rng.standard_normal((n, DIMENSION)).astype(np.float32), -3.0, 3.0)
    y = rng.integers(0, 4, size=n).astype(np.int64)

    model = ScalpNet(num_features=DIMENSION, num_classes=4)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    xt = torch.from_numpy(X)
    yt = torch.from_numpy(y)
    for _ in range(10):
        opt.zero_grad()
        logits = model(xt)
        loss = F.cross_entropy(logits, yt)
        loss.backward()
        opt.step()
    loss_val = float(loss.detach().item())
    assert math.isfinite(loss_val), f"non-finite training loss {loss_val}"

    # scaler: mean/std over the training batch (float32, dim = 70)
    mean = np.asarray(X.mean(axis=0), dtype=np.float32).reshape(-1)
    std = np.asarray(X.std(axis=0) + 1e-6, dtype=np.float32).reshape(-1)
    assert mean.shape == (DIMENSION,) and std.shape == (DIMENSION,)

    manifest: dict = {
        "model_id": MODEL_ID,
        "model_version": "1.0.0",
        "role": "CANDIDATE",
        "status": "TRAINED",
        "architecture_id": "LEGACY_SCALPNET_V1",
        "architecture_version": "1.0.0",
        "architecture_parameters": {"hidden_dim": 128, "num_heads": 4, "dropout_rate": 0.25},
        "feature_schema_id": SCHEMA_ID,
        "feature_schema_version": "1.0.0",
        "feature_dimension": DIMENSION,
        "label_schema_id": "triple_barrier_3class_v1",
        "label_schema_version": "1.0.0",
        "class_count": 4,
        "classes": ["NO_TRADE", "BUY_MARKET", "SELL_MARKET", "WAIT"],
        "dataset_id": "ds_proof",
        "news_enabled": False,
        "news_schema_version": "news_context_v1",
        "final_validation_result": {
            "val_accuracy": 0.0,
            "epochs": 10,
            "train_rows": n,
            "val_rows": 0,
        },
        "feature_schema_hash": feature_schema_hash(),
        "liquidity_algorithm_version": "scalp_liquidity_v1.0.0",
        "build_metadata": {
            "trainer": "BUG-123-proof-artifact",
            "news_features": [],
            "input_dimension": DIMENSION,
            "feature_schema_hash": feature_schema_hash(),
            "liquidity_algorithm_version": "scalp_liquidity_v1.0.0",
        },
        "intended_use": "BUG-123 deterministic compatibility proof; not a production model",
    }

    store = ArtifactStore(ROOT)
    result = store.save_model_artifact(
        MODEL_ID,
        model.state_dict(),
        manifest,
        scaler=(mean, std),
    )
    print("artifact saved:", result["weights_path"])
    print("manifest:", result["manifest_path"])
    print("schema:", SCHEMA_ID, "dim:", DIMENSION, "hash:", feature_schema_hash())


if __name__ == "__main__":
    main()
