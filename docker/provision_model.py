#!/usr/bin/env python
"""Provision a PAPER-safe 70D starter model bundle into a container workspace.

Mirrors scripts/ci/runtime_gate.py provisioning: real ScalpNet(70,3) weights +
manifest.json (sha256) + model.scaler.npz (identity 70) + model.meta.json.
Idempotent: skips when a verified bundle already exists.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

WS = Path(os.environ.get("NSE_WORKSPACE", "/app"))
ART = WS / "artifacts/models/scalp/XAUUSD/70d_liquidity"


def main() -> int:
    model = ART / "model.pt"
    manifest = ART / "manifest.json"
    if model.exists() and manifest.exists():
        try:
            declared = json.loads(manifest.read_text())["model_sha256"]
            h = hashlib.sha256()
            with open(model, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() == declared:
                print(f"bundle verified, skip: {model}")
                return 0
        except Exception:
            pass  # stale/corrupt manifest -> re-provision below

    import numpy as np
    import torch

    from nexus_scalp.features.schema_contract import SCHEMA_ID
    from nexus_scalp.models.scalp_net import ScalpNet

    ART.mkdir(parents=True, exist_ok=True)
    net = ScalpNet(num_features=70, num_classes=3)
    net.eval()
    torch.save(net.state_dict(), model)

    h = hashlib.sha256()
    with open(model, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    manifest.write_text(
        json.dumps({"model_sha256": h.hexdigest(), "manifest_version": "1"}, indent=2)
    )
    np.savez(ART / "model.scaler.npz", mean=np.zeros(70), std=np.ones(70))
    (ART / "model.meta.json").write_text(
        json.dumps(
            {
                "feature_schema_id": SCHEMA_ID,
                "feature_schema_dimension": 70,
                "note": "docker entrypoint provisioned (PAPER starter)",
            },
            indent=2,
        )
    )
    print(f"provisioned starter bundle: {model}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
