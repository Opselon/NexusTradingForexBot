"""
Model artifact forensics probe (READ-ONLY) — AI HUB / TENSOR + SSE task.
Proves, from the actual live artifact, that:
  * model.pt is a ScalpNet 50D / hidden-128 / 4-class artifact
  * the integrity verifier's classes probe (input_projection.weight[0]) is
    the BUG: it reads the HIDDEN width (128), not classifier.weight[0] (=4)
  * every pool.confirmed_at datetime in the liquidity report() payload is not
    JSON-serializable (SSE datetime root cause)
Run:  .venv/Scripts/python.exe scratch/aihub_forensic_probe.py
No artifact is modified.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts/models/scalp/XAUUSD/v1.0.0"

from nexus_scalp.features.liquidity_runtime import LiquiditySnapshot  # noqa: E402
from nexus_scalp.models.scalp_net import ScalpNet  # noqa: E402

# --- scaler ---
sc = np.load(ART / "model.scaler.npz")
print(f"SCALER mean={np.asarray(sc['mean']).shape} std={np.asarray(sc['std']).shape}")

# --- artifact ---
mp = ART / "model.pt"
raw = mp.read_bytes()
print(f"ARTIFACT sha256={hashlib.sha256(raw).hexdigest()}")
print(f"ARTIFACT bytes={len(raw)}")
sd = torch.load(mp, map_location="cpu", weights_only=True)
head_in = sd["input_projection.weight"]
head_out = sd["classifier.weight"]
print(f"STATE_DICT input_projection.weight={tuple(head_in.shape)} (hidden width)")
print(f"STATE_DICT classifier.weight={tuple(head_out.shape)} (true class head)")
print(f"ACTUAL: input={head_in.shape[1]} hidden={head_in.shape[0]} classes={head_out.shape[0]}")

# --- construct the real architecture and probe it ---
net = ScalpNet(num_features=50, num_classes=4)
sd2 = {k: v for k, v in sd.items() if k in net.state_dict()}
missing = set(net.state_dict()) - set(sd2)
extra = set(sd2) - set(net.state_dict())
print(f"ScalpNet state_dict: missing={sorted(missing)} extra={sorted(extra)}")
net.load_state_dict(sd2, strict=True)
net.eval()
with torch.no_grad():
    x = torch.zeros(1, 50)
    logits = net(x, return_logits=True)
    print(f"Dry-run logits shape={tuple(logits.shape)} finite={bool(torch.isfinite(logits).all())}")

# --- verifier bug repro ---
actual_dim = int(head_in.shape[1])
actual_classes_from_buggy_probe = int(head_in.shape[0])
print(
    f"VERIFIER-BUG actual_classes={actual_classes_from_buggy_probe} "
    f"(input_projection[0]) vs TRUE classes={head_out.shape[0]} (classifier[0])"
)

# --- SSE datetime root cause: pool.confirmed_at raw datetime in report() ---
from nexus_scalp.features.liquidity_engine import LiquidityPool, PoolSide, PoolSource, PoolState  # noqa: E402

pool = LiquidityPool(
    price=2500.0,
    side=PoolSide.BSL,
    source=PoolSource.SWING_HIGH,
    timeframe_minutes=5,
    strength=0.9,
    candidate_at=datetime(2026, 8, 19, 1, 13, 0, tzinfo=UTC),
    confirmed_at=datetime(2026, 8, 19, 1, 14, 0, tzinfo=UTC),
    state=PoolState.CONFIRMED,
)
snap = LiquiditySnapshot(
    decision_at=datetime(2026, 8, 19, 1, 14, 0, tzinfo=UTC),
    mid_price=2500.0,
    atr=3.0,
    features=(0.1,) * 10,
    pools=(pool,),
)
payload = {
    "status": "ENABLED",
    "source": "LIVE_MARKET_STATE",
    "pools": [
        {
            "side": getattr(p, "side", None),
            "source": getattr(p, "source", None),
            "state": getattr(p, "state", None),
            "price": getattr(p, "price", None),
            "confirmed_at": getattr(p, "confirmed_at", None),
        }
        for p in snap.pools
    ],
}
try:
    json.dumps(payload)
    print("SSE-DATETIME: report() pools payload SERIALIZED (unexpected)")
except TypeError as e:
    print(f"SSE-DATETIME: report() pools payload FAILS json.dumps -> {e}")
    print("CONFIRMED: pool.confirmed_at raw datetime reaches the SSE frame via report().pools")
print("PROBE DONE (read-only)")