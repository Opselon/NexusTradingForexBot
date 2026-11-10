"""Hermetic checkpoint builder for replay / shadow / behavioural suites.

Several historical suites point ``model_artifact_path`` at the live champion
(``artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt``). That file is a
RELEASE ASSET: it is not in the repository, so a fresh checkout (and every CI
runner that does not download it) cannot run those suites at all — they died
with ``FileNotFoundError`` before the first assertion.

Two dishonest repairs are available and both are refused:

* writing a synthetic ``model.pt`` into ``artifacts/`` — that would install a
  fake champion in the tree the runtime reads, i.e. the engine could boot
  serving random weights;
* ``pytest.skip`` — the suites cover real mechanics (bar-close containment,
  replay determinism, pending-order queues) that must stay covered.

So the checkpoint is minted in the TEST's own tmp directory and the suite
points its config at it. The weights are deterministic (seeded, then briefly
trained so the P0 behavioural gate does not reject them as degenerate), which
is all these suites need: they inject their own decision policy, so the model
is loaded but never consulted for a trade decision.
"""

from __future__ import annotations

from pathlib import Path


def write_trained_checkpoint(
    path: str | Path,
    *,
    num_features: int = 70,
    num_classes: int | None = None,
    seed: int = 999,
    steps: int = 30,
) -> Path:
    """Mint a deterministic, behaviourally-healthy checkpoint at ``path``.

    ``seed`` is applied BEFORE construction (``ScalpNet.__init__`` draws from
    the ambient RNG — construct-then-seed mints machine-dependent weights,
    BUG-154). The short AdamW warm-up exists so the serving/behavioural gates
    see trained (non-degenerate) logits rather than a fresh init.
    """
    import torch

    from nexus_scalp.models.scalp_net import ScalpNet

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    torch.manual_seed(seed)
    net = ScalpNet(num_features=num_features, num_classes=num_classes)
    if steps > 0:
        classes = int(net.classifier.weight.shape[0])
        net.train()
        gen = torch.Generator().manual_seed(1234)
        x = torch.randn(256, num_features, generator=gen)
        y = torch.randint(0, classes, (256,), generator=gen)
        opt = torch.optim.AdamW(net.parameters(), lr=1e-3)
        for _ in range(steps):
            opt.zero_grad()
            loss = torch.nn.functional.cross_entropy(net(x, return_logits=True), y)
            loss.backward()
            opt.step()
    net.eval()
    torch.save(net.state_dict(), out)
    return out


def write_verified_trained_bundle(
    path: str | Path,
    *,
    num_features: int = 70,
    num_classes: int | None = None,
    seed: int = 999,
    steps: int = 30,
) -> Path:
    """Write a trained, integrity-bound test bundle under a caller-owned temp path."""
    import hashlib
    import json

    import torch

    model_path = write_trained_checkpoint(
        path,
        num_features=num_features,
        num_classes=num_classes,
        seed=seed,
        steps=steps,
    )
    state = torch.load(model_path, map_location="cpu", weights_only=True)
    classes = int(state["classifier.weight"].shape[0])
    schema_id = "scalp_v3" if num_features == 70 else "scalp_v1"
    manifest = {
        "manifest_version": "1.0.0",
        "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
        "feature_schema_id": schema_id,
        "feature_schema_dimension": num_features,
        "num_classes": classes,
    }
    metadata = {
        "schema": "nexus_model_meta_v1",
        "feature_schema_id": schema_id,
        "feature_schema_dimension": num_features,
        "num_features": num_features,
        "num_classes": classes,
        "model_head_classes": classes,
    }
    (model_path.parent / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    (model_path.parent / "model.meta.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    return model_path


def copy_verified_trained_bundle(source_model: str | Path, target_model: str | Path) -> Path:
    """Copy a verified test bundle to an isolated test-owned target directory."""
    import shutil

    source, target = Path(source_model), Path(target_model)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    for sidecar in ("manifest.json", "model.meta.json"):
        shutil.copy2(source.parent / sidecar, target.parent / sidecar)
    return target
