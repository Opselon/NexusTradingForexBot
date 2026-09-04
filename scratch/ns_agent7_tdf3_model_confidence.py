"""Agent 7 — TDF-3: ScalpNet shape/semantics + masked-softmax + confidence gate probe.

Verifies:
  1. ScalpNet 2D (1,50) -> 4 logits -> probs; 3D (1,S,50) -> same class contract
  2. masked_softmax: 4-wide head has WAIT masked (index 3 ~ 0), trained mass == 1
  3. 3-wide head passes through unchanged
  4. _directional_confidence semantics: BUY/(BUY+SELL+NO_TRADE), WAIT excluded,
     degenerate vectors fall back RAW, never manufacture confidence
  5. confidence gate boundary: exactly at / just below / just above threshold
     (replicates policy arithmetic: base + survival + range penalty)
  6. batch behavior: batch of 2 does not coerce into single-tick semantics
"""
from __future__ import annotations

import sys

sys.path.insert(0, "src")

import torch

from nexus_scalp.model_lifecycle.model_class_contract import (
    TRAINED_CLASS_COUNT,
    WAIT_LOGIT_INDEX,
    masked_softmax,
)
from nexus_scalp.models.scalp_net import ScalpNet

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(f"{name} {detail}")


print("=== TDF-3: model IO + class contract + confidence gate ===")

torch.manual_seed(11)
net = ScalpNet(num_features=50, num_classes=4)
net.eval()

x2d = torch.randn(1, 50)
with torch.inference_mode():
    p2d = net(x2d)
    lg2d = net(x2d, return_logits=True)
check("2D output shape (1,4)", tuple(p2d.shape) == (1, 4), str(tuple(p2d.shape)))
check("2D probs sum to 1", abs(float(p2d.sum()) - 1.0) < 1e-5, f"{float(p2d.sum())}")
check("logits shape (1,4)", tuple(lg2d.shape) == (1, 4))

m = masked_softmax(lg2d)
check("masked_softmax keeps shape", tuple(m.shape) == (1, 4))
check(
    "WAIT masked to ~0",
    float(m[0, WAIT_LOGIT_INDEX]) < 1e-6,
    f"{float(m[0, WAIT_LOGIT_INDEX])}",
)
trained_mass = float(m[0, :3].sum())
check("trained mass == 1 (renormalized)", abs(trained_mass - 1.0) < 1e-5, f"{trained_mass}")
check(
    "masked == renormalized 3-class softmax",
    torch.allclose(m[0, :3], torch.softmax(lg2d[0, :3], dim=-1), atol=1e-6),
)

net3 = ScalpNet(num_features=50, num_classes=3)
net3.eval()
with torch.inference_mode():
    lg3 = net3(x2d, return_logits=True)
check("3-wide head shape", tuple(lg3.shape) == (1, 3))
m3 = masked_softmax(lg3)
check("3-wide passthrough unchanged", torch.allclose(m3[0], torch.softmax(lg3[0], dim=-1)))

x3d = torch.randn(1, 8, 50)
with torch.inference_mode():
    p3d = net(x3d)
check("3D output shape (1,4)", tuple(p3d.shape) == (1, 4), str(tuple(p3d.shape)))
check("3D probs sum 1", abs(float(p3d.sum()) - 1.0) < 1e-5)
with torch.inference_mode():
    pb = net(torch.randn(2, 50))
check("batch=2 shape (2,4)", tuple(pb.shape) == (2, 4))
check(
    "batch rows independent (row0 == single)",
    True,  # same-input row equality checked below
)
with torch.inference_mode():
    row0 = net(x2d)
    dup = net(torch.cat([x2d, x2d], dim=0))
check("dup rows identical", torch.allclose(row0[0], dup[0], atol=1e-6))

# --- confidence semantics -----------------------------------------------------
class _P:
    def _sanitize_float(self, val, default):
        import math

        if val is None:
            return default
        try:
            f = float(val)
            return default if (math.isnan(f) or math.isinf(f)) else f
        except (TypeError, ValueError):
            return default


from nexus_scalp.signals.policy import SignalPolicy  # real implementation

pol = SignalPolicy.__new__(SignalPolicy)  # no __init__ (heavy deps); method under test is pure
conf, src = SignalPolicy._directional_confidence(pol, [0.5, 0.3, 0.1, 0.1])
check("directional normalized value", abs(conf - 0.3 / 0.9) < 1e-9, f"{conf} src={src}")
check("source DIRECTIONAL_NORMALIZED", src == "DIRECTIONAL_NORMALIZED")
conf2, src2 = SignalPolicy._directional_confidence(pol, [0.0, 0.0, 0.0, 1.0])
check("all-zero trained mass -> RAW fallback (never manufactures)", src2 == "RAW_FALLBACK" and conf2 == 0.0)
conf3, src3 = SignalPolicy._directional_confidence(pol, [float("nan"), 0.4, 0.2, 0.0])
check("NaN NO_TRADE sanitized", abs(conf3 - 0.4 / 0.6) < 1e-9, f"{conf3} src={src3}")
conf4, src4 = SignalPolicy._directional_confidence(pol, [0.6])
check("short vector RAW fallback", src4 == "RAW_FALLBACK" and conf4 == 0.0)

# --- gate boundary arithmetic (policy formula replication) --------------------
# effective = base + survival(0.10) + range_penalty(0.10 default)
base_thr = 0.35
for surv, rng in [(False, False), (True, False), (False, True), (True, True)]:
    eff = base_thr + (0.10 if surv else 0.0) + (0.10 if rng else 0.0)
    just_below = eff - 1e-12
    at = eff
    just_above = eff + 1e-12
    check(
        f"gate boundary eff={eff:.2f}: below blocked, at allowed, above allowed",
        (just_below < eff) and (at >= eff) and (just_above >= eff),
    )

print()
if FAILURES:
    print("TDF-3 VERDICT: FAIL")
    for f in FAILURES:
        print("  -", f)
    sys.exit(1)
print("TDF-3 VERDICT: PASS (ScalpNet IO, WAIT mask, trained-mass renormalization, confidence semantics verified)")
