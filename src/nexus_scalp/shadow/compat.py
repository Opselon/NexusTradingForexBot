"""Shadow compatibility primitives (CHG-0046 / SHADOW_EVIDENCE v2).

Shared, dependency-light helpers so EVERY shadow layer (PHASE-11 challenger,
governance shadow, 70D observer) compares Champion and Shadow under IDENTICAL
semantics. Pure functions only: no adapter / order / risk / policy import
(INV-018).

D2  normalize_action   — one action vocabulary for both sides. The Champion
    action comes from the policy (ActionType: BUY/SELL/NO_TRADE/WAIT/...),
    the shadow argmax uses BUY_MARKET/SELL_MARKET (challenger.py /
    shadow70 models). Comparing raw strings fabricated disagreements
    (BUY vs BUY_MARKET). Canonical set: NO_TRADE / BUY / SELL / WAIT.
D5  vector_fingerprint — deterministic full-vector sha1 (first/last values,
    fixed formatting). The previous salted `hash(tuple(x[:5]))` was
    irreproducible across processes and insensitive to 90% of the vector.
D6  scale_like_champion — byte-identical transform to the live ScalerBundle
    (live_engine.ScalerBundle.transform): (x-mean)/std clipped to [-5,+5]
    with std pre-clamped >=1e-3 exactly like the trainer's fit
    (walk_forward_trainer._fit_scaler). The challenger must be evaluated
    under ITS OWN training transform, not a divergent epsilon variant.
D12 canonical_model_confidence — the model-vs-model confidence is the
    argmax probability of the raw 4-logit vector, NOT the policy's
    directional share (those are different quantities; comparing them
    fabricated CONFIDENCE_DIVERGENCE).
"""

from __future__ import annotations

import hashlib
import math
from typing import Sequence

#: Canonical action vocabulary for shadow comparisons (SHADOW_EVIDENCE v2).
CANONICAL_ACTIONS: tuple[str, ...] = ("NO_TRADE", "BUY", "SELL", "WAIT")

_BUY_ALIASES = frozenset({"BUY", "BUY_MARKET", "BUY_LIMIT", "BUY_STOP"})
_SELL_ALIASES = frozenset({"SELL", "SELL_MARKET", "SELL_LIMIT", "SELL_STOP"})
_TRADE_ACTIONS = frozenset({"BUY", "SELL", "BUY_MARKET", "SELL_MARKET"})


def normalize_action(action: str | None) -> str:
    """Maps ANY policy/model action alias onto the canonical 4-action set.

    Never raises: an unknown action normalizes to NO_TRADE (the safe,
    flat interpretation) — a comparison is never fabricated from a
    vocabulary mismatch.
    """
    a = str(action or "NO_TRADE").strip().upper()
    if a in _BUY_ALIASES:
        return "BUY"
    if a in _SELL_ALIASES:
        return "SELL"
    if a == "WAIT":
        return "WAIT"
    return "NO_TRADE"


def direction_of(action: str | None) -> str:
    """BUY / SELL / NONE for the canonical action (NONE = flat)."""
    a = normalize_action(action)
    if a == "BUY":
        return "BUY"
    if a == "SELL":
        return "SELL"
    return "NONE"


def is_trade_action(action: str | None) -> bool:
    """True when the action opens a directional position."""
    return str(action or "").strip().upper() in _TRADE_ACTIONS


def vector_fingerprint(values: Sequence[float], prefix: int = 16) -> str:
    """Deterministic sha1 identity of a FULL feature vector (D5).

    Fixed 8-significant-digit formatting makes the hash stable across
    processes and runs; the WHOLE vector participates (the old salted
    5-element hash made same-input proof impossible).
    """
    h = hashlib.sha1()
    for v in values:
        try:
            f = float(v)
        except Exception:
            f = 0.0
        if not math.isfinite(f):
            f = 0.0
        h.update(f"{f:.8g}".encode("ascii"))
        h.update(b"|")
    return h.hexdigest()[:prefix]


def canonical_model_confidence(probabilities: Sequence[float]) -> float:
    """Argmax probability of the raw model logit/probability vector (D12).

    Returns 0.0 for an empty/invalid vector — never NaN, never the policy's
    derived directional share.
    """
    probs = [float(p) for p in (probabilities or [])]
    if not probs:
        return 0.0
    finite = [p if math.isfinite(p) else 0.0 for p in probs]
    return max(0.0, min(1.0, max(finite)))


def scale_like_champion(x: "object", mean: "object", std: "object") -> "object":
    """Champion-identical scaler transform (D6).

    Mirrors live_engine.ScalerBundle.transform + the trainer's fit-time
    std floor: std_safe = max(std, 1e-3), then (x-mean)/std_safe clipped
    to [-5, +5]. numpy in / numpy out; callers keep their nan_to_num step.
    """
    import numpy as np

    x = np.asarray(x, dtype=np.float32)
    mean = np.asarray(mean, dtype=np.float32).reshape(-1)
    std = np.asarray(std, dtype=np.float32).reshape(-1)
    if mean.shape[0] != x.shape[-1] or std.shape[0] != x.shape[-1]:
        raise ValueError(f"scaler width {mean.shape[0]} != input width {x.shape[-1]}")
    std = np.maximum(std, np.float32(1e-3))
    return np.clip((x - mean) / std, -5.0, 5.0).astype(np.float32)
