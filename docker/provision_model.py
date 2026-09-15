#!/usr/bin/env python
"""Provision a PAPER-safe 70D starter model bundle into a container workspace.

Thin docker-entrypoint wrapper over the SINGLE canonical starter-mint seam
``nexus_scalp.release.model_bootstrap`` (BUG-296): the same code path
``nexus repair --model`` runs, so the container starter and the CLI starter
cannot drift apart (mirrors scripts/ci/runtime_gate.py provisioning: real
TRAINED ScalpNet(70,3) seed-999 weights + manifest.json (sha256) +
model.scaler.npz (identity 70) + model.meta.json). Idempotent: skips when a
verified AND servable starter already exists.

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

import os
import sys
from pathlib import Path

from nexus_scalp.release.model_bootstrap import (
    ProvisioningError,
    mint_trained_starter,
    provision,
)

WS = Path(os.environ.get("NSE_WORKSPACE", "/app"))
ART = WS / "artifacts/models/scalp/XAUUSD/70d_liquidity"

#: re-export under the historical private name (BUG-269 test seam): the mint
#: implementation now lives in nexus_scalp.release.model_bootstrap.
_mint_trained_starter = mint_trained_starter


def main() -> int:
    model = ART / "model.pt"
    try:
        result = provision(model, note="docker entrypoint provisioned (PAPER starter)")
    except ProvisioningError as e:
        print(str(e), file=sys.stderr)
        return 1
    except ImportError:
        # Container without nexus_scalp importable is a broken image, not a
        # provisioning decision — fail loudly like before.
        raise
    status = result["status"]
    if status == "SKIPPED":
        print(f"bundle verified and servable, skip: {model}")
    elif status == "KEPT":
        print(f"{result['detail']}: {model}")
    elif status == "REFUSED":
        # Governed champion mounted into the volume: never touched.
        print(f"{result['detail']}: {model}", file=sys.stderr)
    else:
        print(f"provisioned starter bundle: {model}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
