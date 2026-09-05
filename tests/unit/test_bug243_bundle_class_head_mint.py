"""BUG-243 regression: bundle-coherent class-head mint in LiveEngine.

Before the fix the runtime mint sites hardcoded num_classes=4, so a cold
start or mono-collapse re-mint over a canonical meta ("model_head_classes":3)
produced a 4-logit tensor the contract never declares — the exact
meta=3 / tensor=4 split MODEL_ARTIFACT_FORENSICS flagged as P0.

Red-before: the helper did not exist; the mint was a literal 4.
Green-after: declared head read from the bundle's own meta (3 canonical,
4 only when explicitly declared); missing/garbage falls back to 3.
"""

from __future__ import annotations

import json
from pathlib import Path


def _meta(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_declared_head_canonical_3(tmp_path: Path) -> None:
    from nexus_scalp.application.live_engine import LiveEngine

    p = _meta(tmp_path / "model.meta.json", {"model_head_classes": 3, "num_classes": 3})
    assert LiveEngine._declared_head_classes_for_path(p) == 3


def test_declared_head_legacy_4_only_when_declared(tmp_path: Path) -> None:
    from nexus_scalp.application.live_engine import LiveEngine

    p = _meta(tmp_path / "model.meta.json", {"model_head_classes": 4})
    assert LiveEngine._declared_head_classes_for_path(p) == 4


def test_declared_head_missing_meta_falls_back_to_contract(tmp_path: Path) -> None:
    from nexus_scalp.application.live_engine import LiveEngine

    p = tmp_path / "absent.model.meta.json"
    assert LiveEngine._declared_head_classes_for_path(p) == 3


def test_declared_head_ignores_garbage(tmp_path: Path) -> None:
    from nexus_scalp.application.live_engine import LiveEngine

    p = _meta(tmp_path / "model.meta.json", {"model_head_classes": "seven"})
    assert LiveEngine._declared_head_classes_for_path(p) == 3
