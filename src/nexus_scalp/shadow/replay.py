"""Shadow Challenger Replay Evidence Pipeline (CHG-0047) — core module.

Runs the VALIDATED-CHALLENGER -> SHADOW-ATTACH -> IDENTICAL-INPUTS ->
CHAMPION vs CHALLENGER INFERENCE -> PAIRED OUTCOMES -> EVIDENCE ARTIFACT
-> PROMOTION-READINESS VERDICT pipeline over DETERMINISTIC historical
replay data. Proves the hardened Shadow (CHG-0046, SHADOW_EVIDENCE v2)
can produce real, reproducible, side-aware challenger evidence without
fabricating a single metric.

MODULE LAYOUT (import-cycle free):
    replay.py (this module)   — constants, config, dataset fingerprint
    _replay_pair.py           — pair classification + session buckets
    _replay_evidence.py       — outcome walking + evidence + verdict

HOW IT WORKS (zero foreign edits):
  * The SAME deterministic bar records stream through TWO independent
    StreamingReplayEngine sessions (research/streaming_replay.py,
    CHG-0035 — used read-only): one with the CHAMPION artifact, one with
    the CHALLENGER artifact. The engine is deterministic (test-enforced
    there), so row i of both decision traces is the SAME market state.
  * Pairs are joined on timestamp + decision_index and classified on TWO
    levels: MODEL level (argmax action + argmax confidence over the raw
    4-prob head vector) and POLICY level (the frozen SignalPolicy action
    recorded in the trace).
  * Paired outcomes come from shadow.outcomes.resolve_pared: side-aware
    fills, walk-end honest R, flat=0.0, geometry from the RECORDED
    proposal (entry/SL/TP in the trace rows). Unusable geometry ->
    NOT_RECORDED (never zero). The market path is the engine's own
    bar-mode convention applied IDENTICALLY to BOTH sides.
  * Model identity, artifact hashes, schema identity, dataset
    fingerprint, git revision and configuration version are embedded in
    the artifact; a run re-identifies its challenger, so replacing the
    artifact or pointing at a different challenger changes the evidence.

RESEARCH ONLY: no order authority, no live path, no promotion. The
verdict NEVER promotes anything — it grades evidence for the human
promotion pipeline (which keeps its own OOS/walk-forward gates).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from nexus_scalp.research.streaming_replay import (  # noqa: F401  (re-export)
    BAR_MODE_SYNTHETIC_SPREAD_USD,
)

#: Minimum resolved pairs before any superiority claim is graded
#: (mirrors ShadowComparer.DEFAULT_MIN_SAMPLES semantics).
MIN_RESOLVED_PAIRS: int = 30

#: Verdict vocabulary (steer §9).
VERDICT_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
VERDICT_SUPPORTED = "CHALLENGER_SUPPORTED"
VERDICT_REJECTED = "CHALLENGER_REJECTED"

#: ΔR beyond this magnitude marks a material behavioral difference.
MATERIAL_DELTA_R: float = 0.10

#: Backward-compat re-exports resolved lazily via module ``__getattr__``
#: (PEP 562) — a module-level import here would re-create the
#: replay <-> _replay_evidence import cycle (CodeQL py/unsafe-cyclic-import).
_LAZY_REEXPORTS: dict[str, tuple[str, str]] = {
    "build_replay_evidence": ("nexus_scalp.shadow._replay_evidence", "build_replay_evidence"),
    "promotion_verdict": ("nexus_scalp.shadow._replay_evidence", "promotion_verdict"),
    "classify_pair": ("nexus_scalp.shadow._replay_pair", "classify_pair"),
    "session_of": ("nexus_scalp.shadow._replay_pair", "session_of"),
}


def __getattr__(name: str):
    if name in _LAZY_REEXPORTS:
        import importlib

        module_name, attr = _LAZY_REEXPORTS[name]
        value = getattr(importlib.import_module(module_name), attr)
        globals()[name] = value  # cache after first access
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def dataset_fingerprint(records: list[dict[str, Any]], dataset_id: str) -> str:
    """Deterministic content hash of the replay record set.

    Mirrors research.mt5_tick_dataset.dataset_fingerprint semantics
    (key-sorted canonical rows); changing ANY record content changes the
    fingerprint. Kept local so the shadow evidence is self-verifying
    without importing research cache machinery.
    """
    h = hashlib.sha256()
    h.update(dataset_id.encode("utf-8"))
    for rec in records:
        h.update("|".join(str(rec.get(k, "")) for k in sorted(rec.keys())).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class ShadowReplayConfig:
    """Frozen pipeline configuration — part of the evidence identity."""

    champion_artifact_path: str
    challenger_artifact_path: str
    champion_model_id: str
    challenger_model_id: str
    champion_model_version: str
    challenger_model_version: str
    policy_params: dict[str, Any] = field(default_factory=dict)
    git_revision: str = ""
    configuration_version: str = ""
    dataset_id: str = "replay-shadow-evidence"
    horizon_minutes: int = 120
    min_resolved_pairs: int = MIN_RESOLVED_PAIRS

    def identity(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        base: dict[str, Any] = {
            "champion_artifact_path": self.champion_artifact_path,
            "challenger_artifact_path": self.challenger_artifact_path,
            "champion_model_id": self.champion_model_id,
            "challenger_model_id": self.challenger_model_id,
            "champion_model_version": self.champion_model_version,
            "challenger_model_version": self.challenger_model_version,
            "policy_params": dict(sorted(self.policy_params.items())),
            "git_revision": self.git_revision,
            "configuration_version": self.configuration_version,
            "dataset_id": self.dataset_id,
            "horizon_minutes": self.horizon_minutes,
        }
        if extra:
            base.update(extra)
        return base

    def evidence_fingerprint(self, extra: dict[str, Any] | None = None) -> str:
        raw = repr(sorted(self.identity(extra).items())).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:32]
