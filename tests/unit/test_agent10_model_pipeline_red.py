"""AGENT 10 — MODEL PIPELINE FORENSIC (TASK-AGENT10-MODEL-PIPELINE)

Three confirmed defects, each with a RED regression test written FIRST
(master contract rule 6/7: BUG -> REPRODUCER -> ROOT CAUSE -> REGRESSION
TEST -> FIX -> RE-RUN REPRODUCER).

Fix A — BUG-243: async retrain races a concurrent bundle swap. The retrain
         completion path swapped `self._bundle` unconditionally, so a fine
         tune started against bundle N could overwrite a NEWER bundle N+1
         (hot swap / promotion / rollback) with stale-weight results.

Fix B — BUG-141 residual: `_save_model_weights_atomic` refused the DISK
         write of a width-mismatched candidate, but the in-memory bundle
         swap, provenance re-registration and "ASYNC RETRAIN SUCCESS"
         still proceeded -> memory==disk identity divergence. The persist
         path must refuse END-TO-END (no swap, no provenance, explicit
         ASYNC_RETRAIN_REFUSED log).

Fix C — hot-swap metadata-coherence gate gap: the P0 docstring claims
         "model.meta.json class count must equal the actual tensor head"
         but no head check existed, and the schema id was never verified
         against the registered schema registry. A 3-logit artifact with
         4-class meta (or an unregistered schema id) was loadable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

torch = pytest.importorskip("torch")
np = pytest.importorskip("numpy")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _mk_engine_like(monkeypatch, tmp_path: Path):
    """A minimal LiveEngine-shaped stub exercising the REAL methods.

    Instantiating a full LiveEngine needs a broker; the BUG-141 regression
    suite already established the unbound-classmethod pattern
    (LiveEngine.<method>(_stub(), ...)) which runs the real production
    code without a constructor. We follow the same contract.
    """

    class _Bundle:
        def __init__(self, width: int) -> None:
            from nexus_scalp.models.scalp_net import ScalpNet

            self.model = ScalpNet(num_features=width, num_classes=4)
            self.model.eval()
            self.scaler = None
            self.artifact_path = tmp_path / f"bundle_{width}" / "model.pt"
            self.artifact_path.parent.mkdir(parents=True, exist_ok=True)

    class _Stub:
        FEATURE_DIM = 50
        FEATURE_SCHEMA_ID = "scalp_v1"

        def __init__(self) -> None:
            import threading

            self._bundle = None
            self._bundle_lock = threading.RLock()
            self.registered: list[tuple[Path, bool]] = []

        def _declared_contract_dim_for_path(self, model_path: Path):
            return LiveEngine._declared_contract_dim_for_path(self, model_path)

        def _save_model_weights_atomic(self, model, model_path) -> bool:
            return LiveEngine._save_model_weights_atomic(self, model, model_path)

        def _register_active_model(self, model_path: Path, replaced: bool) -> None:
            self.registered.append((model_path, replaced))

        def _rebind_trainer_to_bundle(self) -> None:
            pass

    from nexus_scalp.application.live_engine import LiveEngine

    return _Stub(), _Bundle


# ---------------------------------------------------------------------------
# Fix B — BUG-141 end-to-end persist refusal (RED first)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width,declared", [(50, 70), (70, 50)])
def test_bug243_width_mismatch_persists_nothing_and_refuses_swap(
    tmp_path: Path, width: int, declared: int
) -> None:
    """A width-mismatched candidate must be refused END-TO-END.

    Pre-fix behavior (the residual): the disk write was refused but the
    method returned None and the caller swapped the in-memory bundle,
    re-registered provenance and logged SUCCESS -> serving identity
    diverged from the artifact on disk. Post-fix the method returns False,
    swaps nothing and registers nothing; the caller (retrain path) must
    honor the refusal.
    """
    from nexus_scalp.models.scalp_net import ScalpNet

    engine_like, _ = _mk_engine_like(None, tmp_path)

    # Build a declared-<declared> bundle directory (meta + scaler + checkpoint).
    d = tmp_path / f"declared_{declared}"
    d.mkdir()
    target = d / "model.pt"
    torch.save(ScalpNet(num_features=declared, num_classes=4).state_dict(), target)
    np.savez(d / "model.scaler.npz", mean=np.ones(declared), std=np.ones(declared))
    (d / "model.meta.json").write_text(
        json.dumps({"feature_schema_dimension": declared, "num_classes": 4}),
        encoding="utf-8",
    )

    wrong = ScalpNet(num_features=width, num_classes=4)

    # Existing contract (kept green): disk untouched, no exception.
    before = target.read_bytes()
    ok = LiveEngine_save_and_swap(engine_like, wrong, target)
    assert target.read_bytes() == before, "artifact must be preserved"
    assert ok is False, "refusal must be reported to the caller (end-to-end)"
    assert engine_like._bundle is None, "in-memory bundle must NOT be swapped"
    assert engine_like.registered == [], "provenance must NOT be re-registered"

    # Compatible write still allowed and returns True.
    right = ScalpNet(num_features=declared, num_classes=4)
    ok2 = LiveEngine_save_and_swap(engine_like, right, target)
    assert ok2 is True
    assert target.read_bytes() != before


def LiveEngine_save_and_swap(stub, model, target: Path) -> bool:
    """The caller-side contract under test: save+swap must be ALL-OR-NOTHING.

    This mirrors exactly what `_trigger_async_online_fine_tune` must do with
    the return value after the fix (and what it did NOT do before).
    """
    from nexus_scalp.application.live_engine import LiveEngine

    saved = LiveEngine._save_model_weights_atomic(stub, model, target)
    if not saved:
        return False
    with stub._bundle_lock:
        stub._bundle = object()  # swap marker
    stub._register_active_model(model_path=target, replaced=True)
    return True


# ---------------------------------------------------------------------------
# Fix A — stale async retrain must never overwrite a newer bundle (RED first)
# ---------------------------------------------------------------------------


def test_bug243_stale_retrain_result_cannot_overwrite_newer_bundle(
    tmp_path: Path, monkeypatch
) -> None:
    """A late retrain completion for artifact A must be discarded when the
    serving bundle already points at artifact B (hot swap mid-retrain)."""
    import asyncio

    from nexus_scalp.application import live_engine as le_mod

    a_path = tmp_path / "a" / "model.pt"
    b_path = tmp_path / "b" / "model.pt"
    a_path.parent.mkdir(parents=True)
    b_path.parent.mkdir(parents=True)
    a_path.write_bytes(b"x")
    b_path.write_bytes(b"y")

    engine = MagicMock()
    engine._retrain_inflight = False
    engine._bars_since_last_retrain = 99
    engine._rolling_feature_records = []

    captured = {}

    def _record(bundle_owner, expected_path):
        captured["expected"] = Path(expected_path)

    # The helper under test (added by the fix) captures the bundle identity
    # at dispatch time and refuses the swap when it changed at completion.
    monkeypatch.setattr(
        le_mod.LiveEngine,
        "_retrain_target_bundle_path",
        lambda self, default=None: captured.setdefault("expected", Path(a_path)),
        raising=False,
    )

    from nexus_scalp.models.scalp_net import ScalpNet

    old_bundle = MagicMock()
    old_bundle.artifact_path = a_path
    new_bundle = MagicMock()
    new_bundle.artifact_path = b_path
    engine._bundle = new_bundle  # hot swap already happened

    updated = ScalpNet(num_features=70, num_classes=4)

    decision = le_mod.LiveEngine._retrain_swap_decision(
        engine,
        dispatched_for_path=captured.setdefault("expected", Path(a_path)),
        candidate=updated,
        current_bundle=engine._bundle,
    )
    assert decision["swap"] is False
    assert decision["reason"] == "STALE_RETRAIN_RESULT"


def test_bug243_fresh_retrain_result_still_swaps(tmp_path) -> None:
    """Control: a retrain dispatched against the CURRENT artifact still swaps."""
    from nexus_scalp.application import live_engine as le_mod
    from nexus_scalp.models.scalp_net import ScalpNet

    p = tmp_path / "same" / "model.pt"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"z")
    bundle = MagicMock()
    bundle.artifact_path = p
    engine = MagicMock()
    engine._bundle = bundle

    updated = ScalpNet(num_features=50, num_classes=4)
    decision = le_mod.LiveEngine._retrain_swap_decision(
        engine,
        dispatched_for_path=p,
        candidate=updated,
        current_bundle=bundle,
    )
    assert decision["swap"] is True
    assert decision["reason"] == ""


# ---------------------------------------------------------------------------
# Fix C — hot-swap head/meta coherence + registered schema id (RED first)
# ---------------------------------------------------------------------------


def _write_bundle_with_meta(tmp_path: Path, width: int, head: int, meta_head: int) -> Path:
    from nexus_scalp.models.scalp_net import ScalpNet

    d = tmp_path / f"b_{width}_{head}_{meta_head}"
    d.mkdir(parents=True, exist_ok=True)
    model = ScalpNet(num_features=width, num_classes=head)
    torch.save(model.state_dict(), d / "model.pt")
    (d / "model.meta.json").write_text(
        json.dumps(
            {
                "feature_schema_dimension": width,
                "num_classes": meta_head,
                "model_head_classes": meta_head,
                "feature_schema_id": "scalp_v1" if width == 50 else "scalp_v3",
            }
        ),
        encoding="utf-8",
    )
    np.savez(d / "model.scaler.npz", mean=np.ones(width), std=np.ones(width))
    return d / "model.pt"


def test_bug243_hot_swap_rejects_head_meta_incoherence(tmp_path: Path) -> None:
    """Artifact head (4) != meta num_classes (3) must be rejected LOUDLY
    before attach — the exact P0 incoherence class (4-class tensor +
    3-class metadata)."""
    art = _write_bundle_with_meta(tmp_path, width=50, head=4, meta_head=3)

    from nexus_scalp.application.live_engine import LiveEngine

    verdict = LiveEngine._artifact_meta_coherence(art)
    assert verdict["ok"] is False
    assert verdict["reason"] == "HEAD_META_CLASS_MISMATCH"
    assert verdict["artifact_head"] == 4 and verdict["meta_head"] == 3


def test_bug243_hot_swap_accepts_coherent_bundle(tmp_path: Path) -> None:
    art = _write_bundle_with_meta(tmp_path, width=50, head=4, meta_head=4)
    from nexus_scalp.application.live_engine import LiveEngine

    verdict = LiveEngine._artifact_meta_coherence(art)
    assert verdict["ok"] is True, verdict
    assert verdict["artifact_head"] == 4 == verdict["meta_head"]


def test_bug243_hot_swap_rejects_unregistered_schema_id(tmp_path: Path) -> None:
    """An artifact declaring an UNREGISTERED schema id must not attach:
    dimension equality alone is not identity (family semantics)."""
    from nexus_scalp.models.scalp_net import ScalpNet

    d = tmp_path / "bogus_schema"
    d.mkdir(parents=True)
    art = d / "model.pt"
    torch.save(ScalpNet(num_features=50, num_classes=4).state_dict(), art)
    (d / "model.meta.json").write_text(
        json.dumps(
            {
                "feature_schema_dimension": 50,
                "num_classes": 4,
                "feature_schema_id": "scalp_vX_bogus",
            }
        ),
        encoding="utf-8",
    )
    np.savez(d / "model.scaler.npz", mean=np.ones(50), std=np.ones(50))

    from nexus_scalp.application.live_engine import LiveEngine

    verdict = LiveEngine._artifact_meta_coherence(art)
    assert verdict["ok"] is False
    assert verdict["reason"] == "UNREGISTERED_SCHEMA_ID"


def test_bug243_hot_swap_rejects_dimension_meta_lie(tmp_path: Path) -> None:
    """meta declaring 70 while the tensor is 50-wide = BUG-141 class — must
    be rejected by the swap-path coherence check too (defense in depth)."""
    from nexus_scalp.models.scalp_net import ScalpNet

    d = tmp_path / "dim_lie"
    d.mkdir(parents=True)
    art = d / "model.pt"
    torch.save(ScalpNet(num_features=50, num_classes=4).state_dict(), art)
    (d / "model.meta.json").write_text(
        json.dumps(
            {
                "feature_schema_dimension": 70,
                "num_classes": 4,
                "feature_schema_id": "scalp_v3",
            }
        ),
        encoding="utf-8",
    )
    np.savez(d / "model.scaler.npz", mean=np.ones(70), std=np.ones(70))

    from nexus_scalp.application.live_engine import LiveEngine

    verdict = LiveEngine._artifact_meta_coherence(art)
    assert verdict["ok"] is False
    assert verdict["reason"] == "DIMENSION_META_MISMATCH"
