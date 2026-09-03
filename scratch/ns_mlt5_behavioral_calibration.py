"""MLFIX-T5 calibration probe: behavioral model-health gate evidence run.

Reproducible read-only probe. Measures the behavioral health metrics of:
  * fresh random init (seed 42)
  * epsilon-diverged random (fresh + Gaussian perturbation)
  * intentionally degraded (shuffled weights)
  * on-disk trained references (50d_main, 70d_news)
  * the live champion artifact (70d_liquidity = a4b95406)

Emits the calibration table used in model_lifecycle/behavioral_health.py and
proves the champion / fresh / epsilon / degraded classes FAIL the gate.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

import numpy as np
import torch

from nexus_scalp.models.scalp_net import ScalpNet

OUT: dict[str, dict] = {}
np.random.seed(7)
torch.manual_seed(7)


def measure(sd: dict, dim: int, n_cls: int = 4) -> dict:
    """Single deterministic behavioral probe (same metrics as integrity.py)."""
    model = ScalpNet(num_features=dim, num_classes=n_cls)
    model.load_state_dict(sd)
    model.eval()

    rng = np.random.default_rng(7)
    probes = np.vstack(
        [
            np.zeros((1, dim), dtype=np.float32),
            np.full((1, dim), 3.0, dtype=np.float32),
            np.full((1, dim), -3.0, dtype=np.float32),
            np.clip(rng.standard_normal((64, dim)), -5, 5).astype(np.float32),
        ]
    )
    with torch.no_grad():
        logits = model(torch.tensor(probes), return_logits=True).numpy()
        probs = model(torch.tensor(probes), return_logits=False).numpy()
    return {
        "logit_std_mean": round(float(logits.std(axis=0).mean()), 4),
        "max_prob_mean": round(float(probs.max(axis=1).mean()), 4),
        "wait_mass_mean": round(float(probs[:, 3].mean()), 4) if n_cls >= 4 else 0.0,
        "margin_sensitivity": round(
            float(abs((probs[1, 1] - probs[1, 2]) - (probs[2, 1] - probs[2, 2]))), 4
        ),
    }


def moved_tensors(sd: dict, dim: int, n_cls: int = 4) -> int:
    torch.manual_seed(42)
    ref = ScalpNet(num_features=dim, num_classes=n_cls).state_dict()
    return sum(1 for k in ref if float((sd[k] - ref[k]).abs().max()) > 1e-8)


# 1. Fresh random init
torch.manual_seed(42)
fresh = ScalpNet(num_features=70, num_classes=4).state_dict()
OUT["fresh_init"] = {
    **measure(fresh, 70),
    "moved_tensors": moved_tensors(fresh, 70),
}

# 2. Epsilon-diverged random
torch.manual_seed(42)
eps_model = ScalpNet(num_features=70, num_classes=4)
torch.manual_seed(7)
with torch.no_grad():
    for v in eps_model.parameters():
        v.add_(torch.randn_like(v) * 0.002)
eps_sd = eps_model.state_dict()
OUT["epsilon_diverged"] = {
    **measure(eps_sd, 70),
    "moved_tensors": moved_tensors(eps_sd, 70),
}

# 3. Intentionally degraded (shuffled weights)
torch.manual_seed(42)
deg_model = ScalpNet(num_features=70, num_classes=4)
with torch.no_grad():
    for _k, v in deg_model.state_dict().items():
        if v.dim() >= 1:
            idx = torch.randperm(v.numel())
            v.copy_(v.flatten()[idx].view_as(v))
deg_sd = deg_model.state_dict()
OUT["degraded_shuffled"] = {
    **measure(deg_sd, 70),
    "moved_tensors": moved_tensors(deg_sd, 70),
}

# 4. On-disk trained references (trained-but-weak, must PASS)
for label, path, dim in [
    ("trained_50d_main", "artifacts/models/scalp/XAUUSD/50d_main/model.pt", 50),
    ("trained_70d_news", "artifacts/models/scalp/XAUUSD/70d_news/model.pt", 70),
]:
    sd = torch.load(path, map_location="cpu", weights_only=False)
    OUT[label] = {
        **measure(sd, dim),
        "moved_tensors": moved_tensors(sd, dim),
    }

# 5. Live champion (a4b95406)
champ = "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt"
if Path(champ).exists():
    sd = torch.load(champ, map_location="cpu", weights_only=False)
    OUT["champion_70d_liquidity"] = {
        **measure(sd, 70),
        "moved_tensors": moved_tensors(sd, 70),
    }

# Gate evaluation summary
t = {
    "logit_std_min": 0.15,
    "max_prob_floor": 0.35,
    "wait_mass_ceiling": 0.30,
    "sensitivity_floor": 0.02,
}
summary = {}
for label, m in OUT.items():
    fails = []
    if m["logit_std_mean"] < t["logit_std_min"]:
        fails.append("logit_std")
    if m["max_prob_mean"] < t["max_prob_floor"]:
        fails.append("max_prob")
    if m["wait_mass_mean"] > t["wait_mass_ceiling"]:
        fails.append("wait_mass")
    if m["margin_sensitivity"] < t["sensitivity_floor"]:
        fails.append("sensitivity")
    summary[label] = "PASS" if not fails else f"FAIL:{'+'.join(fails)}"

print(json.dumps({"thresholds": t, "measurements": OUT, "verdicts": summary}, indent=2))
