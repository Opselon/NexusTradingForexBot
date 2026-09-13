#!/usr/bin/env python
"""Provision a PAPER-safe 70D starter model bundle into a container workspace.

Mirrors scripts/ci/runtime_gate.py provisioning: real TRAINED ScalpNet(70,3)
weights + manifest.json (sha256) + model.scaler.npz (identity 70) +
model.meta.json. Idempotent: skips when a verified AND servable bundle already
exists.

BUG-269 (2026-09-13): the P0 serving gate (60b785d3 / #154 — fresh-init
detection + behavioral anti-degenerate probe in
application/live/model_bundle_store.py::_load_or_create_bundle) refuses
untrained weights at LOAD time. The previous starter mint (net =
ScalpNet(...); torch.save(state_dict)) is exactly that: digest-VERIFIED but
behaviorally DEGENERATE (probe: logit_std ~0.05 < 0.15, sensitivity ~0.008
< 0.02), so a fresh-volume container entered the MODEL_LOAD_REJECTED restart
loop this provisioner exists to prevent. The mint therefore uses the SAME
deterministic 30-step AdamW recipe as runtime_gate (seed-999 BEFORE
construction per BUG-154 — constructing first made the bundle's behavioral
health machine-dependent) and re-provisions a digest-valid but degenerate
bundle instead of skipping it (an operator-mounted trained champion passes
the probe and is left untouched, byte-for-byte).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

WS = Path(os.environ.get("NSE_WORKSPACE", "/app"))
ART = WS / "artifacts/models/scalp/XAUUSD/70d_liquidity"

#: Same determinism contract as scripts/ci/runtime_gate.py (BUG-154):
#: manual_seed BEFORE construction, fixed training data generator, 30 steps.
MINT_SEED = 999
DATA_SEED = 1234
MINT_STEPS = 30


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_servable(model: Path) -> bool:
    """True when the bundle passes the engine's LOAD-TIME serving gates.

    Runs the same two content checks _load_or_create_bundle enforces before
    weights ever serve (model_lifecycle.integrity): fresh-init canary +
    behavioral anti-degenerate probe. Imports are lazy and NOT caught here —
    a missing torch/nexus_scalp must propagate so main() keeps (never
    clobbers) a digest-verified bundle it cannot judge.
    """
    from nexus_scalp.model_lifecycle.integrity import (
        check_model_behavioral_health,
        detect_untrained_fresh_init,
    )

    is_fresh, fresh_detail = detect_untrained_fresh_init(model, None)
    if is_fresh:
        print(f"bundle is canonical fresh init ({fresh_detail}): {model}")
        return False
    healthy, detail, _metrics = check_model_behavioral_health(model, None)
    if not healthy:
        print(f"bundle fails behavioral health ({detail}): {model}")
    return bool(healthy)


def _mint_trained_starter(model: Path) -> None:
    """Deterministic TRAINED mint — byte-stable across hosts (BUG-154)."""
    import torch

    from nexus_scalp.models.scalp_net import ScalpNet

    torch.manual_seed(MINT_SEED)
    net = ScalpNet(num_features=70, num_classes=3)
    net.train()
    gen = torch.Generator().manual_seed(DATA_SEED)
    x = torch.randn(256, 70, generator=gen)
    y = torch.randint(0, 3, (256,), generator=gen)
    opt = torch.optim.AdamW(net.parameters(), lr=1e-3)
    for _ in range(MINT_STEPS):
        opt.zero_grad()
        loss = torch.nn.functional.cross_entropy(net(x, return_logits=True), y)
        loss.backward()
        opt.step()
    net.eval()
    torch.save(net.state_dict(), model)


def main() -> int:
    model = ART / "model.pt"
    manifest = ART / "manifest.json"
    if model.exists() and manifest.exists():
        try:
            declared = json.loads(manifest.read_text())["model_sha256"]
            if declared == _sha256_file(model):
                # BUG-269: digest-match alone is NOT sufficient — the P0
                # serving gate additionally refuses fresh-init / degenerate
                # weights at load. A legacy untrained starter (or any
                # digest-consistent but unservable bundle) must be
                # re-provisioned; a trained champion passes and is preserved.
                if _is_servable(model):
                    print(f"bundle verified and servable, skip: {model}")
                    return 0
                print(f"re-provisioning digest-valid but UNSERVABLE bundle: {model}")
            else:
                print(f"stale manifest digest (weights changed): {model}")
        except ImportError:
            # Cannot judge servability without torch — never clobber a
            # digest-verified bundle we cannot read.
            print("torch/nexus_scalp unavailable for probe; keeping existing bundle")
            return 0
        except Exception:
            pass  # stale/corrupt manifest -> re-provision below

    import numpy as np

    from nexus_scalp.features.schema_contract import SCHEMA_ID

    ART.mkdir(parents=True, exist_ok=True)
    _mint_trained_starter(model)

    # Fail LOUDLY at the provisioning seam (same contract as runtime_gate):
    # a future torch/RNG-semantic shift must surface here with probe detail,
    # never as a mysterious MODEL_LOAD_REJECTED boot loop.
    if not _is_servable(model):
        raise RuntimeError(
            f"provision_model PROVISIONING_ERROR: minted starter failed the "
            f"serving gates above — fix the mint recipe; never relax the "
            f"serving gate. artifact={model}"
        )

    digest = _sha256_file(model)
    manifest.write_text(
        json.dumps(
            {
                "model_sha256": digest,
                "manifest_version": "1",
                "note": "docker entrypoint provisioned (PAPER starter)",
            },
            indent=2,
        )
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
