"""Part 2 of shadow/replay.py — pair classification + evidence builder."""

from __future__ import annotations

from typing import Any

from nexus_scalp.shadow.compat import (
    canonical_model_confidence,
    direction_of,
    normalize_action,
)
from nexus_scalp.shadow.outcomes import SideGeometry
from nexus_scalp.shadow.replay import session_of
from nexus_scalp.shadow.shadow70.models import classify_disagreement


def _argmax_index(probs: list[float]) -> int:
    best, best_i = -1.0, 0
    for i, p in enumerate(probs):
        try:
            f = float(p)
        except (TypeError, ValueError):
            continue
        if f > best:
            best, best_i = f, i
    return best_i


#: 4-class argmax vocabulary (canonical model head contract, never reinterpreted).
_ARGMAX_ACTIONS = ("NO_TRADE", "BUY", "SELL", "WAIT")


def classify_pair(
    champ_row: dict[str, Any],
    chal_row: dict[str, Any],
) -> dict[str, Any]:
    """Classifies ONE paired decision on MODEL and POLICY levels.

    MODEL level: argmax over the raw 4-prob vectors (the head contract).
    POLICY level: the frozen SignalPolicy action recorded in the traces.
    A pair is INVALID when the two rows are not the same market instant
    (timestamp/decision_index mismatch) — same-input proof (steer §3).
    """
    if (
        str(champ_row.get("ts")) != str(chal_row.get("ts"))
        or int(champ_row.get("decision_index", -1)) != int(chal_row.get("decision_index", -2))
    ):
        return {
            "valid": False,
            "invalid_reason": "INPUT_MISMATCH: paired rows are not the same market instant",
        }
    champ_probs = [float(p) for p in (champ_row.get("probs") or [])]
    chal_probs = [float(p) for p in (chal_row.get("probs") or [])]
    champ_arg = _ARGMAX_ACTIONS[_argmax_index(champ_probs)]
    chal_arg = _ARGMAX_ACTIONS[_argmax_index(chal_probs)]
    champ_conf = canonical_model_confidence(champ_probs)
    chal_conf = canonical_model_confidence(chal_probs)
    # MODEL-level taxonomy via the canonical 8-class classifier.
    disagreement_model = classify_disagreement(champ_arg, chal_arg, champ_conf, chal_conf)
    # POLICY-level normalized comparison (CHG-0046 D2 semantics).
    champ_action = normalize_action(champ_row.get("action"))
    chal_action = normalize_action(chal_row.get("action"))
    champ_dir = direction_of(champ_action)
    chal_dir = direction_of(chal_action)
    if champ_action == chal_action:
        policy_cls = "AGREEMENT"
    elif champ_dir != "NONE" and chal_dir not in ("NONE", champ_dir):
        policy_cls = "DIRECTION_DISAGREEMENT"
    else:
        policy_cls = "ACTION_DISAGREEMENT"
    return {
        "valid": True,
        "invalid_reason": "",
        "ts": str(champ_row.get("ts")),
        "decision_index": int(champ_row.get("decision_index", -1)),
        "regime": str(champ_row.get("regime") or "UNKNOWN"),
        "session": session_of(champ_row.get("ts_dt"))
        if champ_row.get("ts_dt") is not None
        else "UNKNOWN",
        "champ_argmax": champ_arg,
        "chal_argmax": chal_arg,
        "champ_model_conf": champ_conf,
        "chal_model_conf": chal_conf,
        "confidence_delta": abs(champ_conf - chal_conf),
        "disagreement_model": disagreement_model.value,
        "champ_action": champ_action,
        "chal_action": chal_action,
        "policy_class": policy_cls,
        "champ_geometry": SideGeometry(
            action=champ_action,
            entry=float(champ_row.get("entry") or 0.0),
            sl=float(champ_row.get("stop_loss") or 0.0),
            tp=float(champ_row.get("take_profit") or 0.0),
        ),
        "chal_geometry": SideGeometry(
            action=chal_action,
            entry=float(chal_row.get("entry") or 0.0),
            sl=float(chal_row.get("stop_loss") or 0.0),
            tp=float(chal_row.get("take_profit") or 0.0),
        ),
    }
