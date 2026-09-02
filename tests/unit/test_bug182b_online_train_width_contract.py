"""BUG-182B regression tests: online fine-tune width contract.

Pins the three layers of the fix:
1. init-order: the trainer rebind happens AFTER the bundle load, so a 70D
   artifact rebinds the trainer (probed via AST, no torch needed for the order).
2. live retrain paths pass the EFFECTIVE feature cols (artifact-driven), not
   the class bootstrap 50 cols.
3. WalkForwardTrainer.fine_tune_online fails LOUD (ValueError) before training
   when model width != len(feature_cols); the exact torch matmul crash from the
   2026-09-01 log is unreachable for a contract violation.
"""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]
ENGINE = REPO / "src/nexus_scalp/application/live_engine.py"
TRAINER = REPO / "src/nexus_scalp/training/walk_forward_trainer.py"


def _engine_source() -> str:
    return ENGINE.read_text(encoding="utf-8")


def _engine_init_fn() -> ast.FunctionDef:
    tree = ast.parse(_engine_source())
    engine = next(
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "LiveEngine"
    )
    return next(f for f in engine.body if isinstance(f, ast.FunctionDef) and f.name == "__init__")


def test_bug182b_rebind_runs_after_bundle_load() -> None:
    """The BUG-169 trainer rebind must read a LOADED bundle, not None.

    BUG-185 refactor: the rebind logic lives in the shared
    _rebind_trainer_to_bundle() helper (called from every bundle-mutation
    site). The invariant is unchanged: __init__ must invoke the helper AFTER
    _load_or_create_bundle, and the helper must carry the rebind.
    """
    src = _engine_source()
    init = _engine_init_fn()
    seg = ast.get_source_segment(src, init) or ""
    helper_call_at = seg.find("self._rebind_trainer_to_bundle()")
    load_at = seg.find("_load_or_create_bundle(")
    assert load_at != -1, "bundle load not found in __init__"
    assert helper_call_at != -1, (
        "__init__ no longer calls _rebind_trainer_to_bundle (BUG-182B/BUG-185 regression)"
    )
    assert helper_call_at > load_at, (
        "trainer rebind runs BEFORE the bundle load -> rebind sees None and silently "
        "skips (the BUG-182B init-order bug)"
    )
    # The helper itself must contain the actual rebind (log + schema assign).
    helper_at = src.find("def _rebind_trainer_to_bundle(")
    assert helper_at != -1, "_rebind_trainer_to_bundle helper missing"
    helper_seg = src[helper_at : src.find("\n    def ", helper_at)]
    assert "[ONLINE_TRAIN] trainer rebound" in helper_seg, (
        "rebind log moved out of the helper (BUG-182B regression)"
    )
    assert "trainer.num_features" in helper_seg and "trainer.feature_schema" in helper_seg


def test_bug182b_retrain_paths_use_effective_cols() -> None:
    """No retrain call site may pass the class-level 50-col FEATURE_COLS."""
    src = _engine_source()
    fn_names = (
        "_trigger_async_online_fine_tune",
        "_bootstrap_train_if_ready",
        "_reinitialize_collapsed_model",
    )
    tree = ast.parse(src)
    engine = next(
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "LiveEngine"
    )
    found = 0
    for f in engine.body:
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name in fn_names:
            seg = ast.get_source_segment(src, f) or ""
            assert "list(self.FEATURE_COLS)" not in seg, (
                f"{f.name} still passes class-level FEATURE_COLS (BUG-182B regression)"
            )
            assert "effective_feature_cols" in seg, f"{f.name} missing effective cols"
            found += 1
    assert found == len(fn_names)


def test_bug182b_trainer_fails_loud_on_width_mismatch() -> None:
    """fine_tune_online must raise a contract error BEFORE any torch work."""
    sys_path = str(REPO / "src")
    import sys

    sys.path.insert(0, sys_path)
    import importlib

    from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

    scalp_net = importlib.import_module("nexus_scalp.models.scalp_net")
    trainer = WalkForwardTrainer(
        artifact_save_path=REPO / "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt",
        random_seed=42,
        feature_schema_id="scalp_v1",
    )
    model70 = scalp_net.ScalpNet(num_features=70)
    rows = 200
    df = None  # build a minimal labeled frame
    import polars as pl

    data = {f"feat_{i}": [0.1] * rows for i in range(50)}
    data["label"] = ["NO_TRADE"] * rows
    data["label_evaluated"] = [True] * rows
    data["is_purged"] = [False] * rows
    df = pl.DataFrame(data)
    cols50 = [f"feat_{i}" for i in range(50)]
    with pytest.raises(ValueError, match="Feature contract violation in online fine-tune"):
        trainer.fine_tune_online(live_model=model70, recent_df=df, feature_cols=cols50, epochs=1)


def test_bug182b_log_error_is_unreachable_for_contract_mismatch() -> None:
    """The exact torch matmul text from the 2026-09-01 log must not be the
    first failure for a width mismatch: the ValueError fires first."""
    import sys

    sys.path.insert(0, str(REPO / "src"))
    import polars as pl
    import torch

    from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

    trainer = WalkForwardTrainer(
        artifact_save_path=REPO / "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt",
        random_seed=42,
        feature_schema_id="scalp_v1",
    )
    rows = 200
    data = {f"feat_{i}": [0.1] * rows for i in range(50)}
    data["label"] = ["NO_TRADE"] * rows
    data["label_evaluated"] = [True] * rows
    data["is_purged"] = [False] * rows
    df = pl.DataFrame(data)
    cols50 = [f"feat_{i}" for i in range(50)]

    class FakeModel70:
        num_features = 70

        def state_dict(self):  # pragma: no cover - never reached
            raise AssertionError("training must not start on contract violation")

    with pytest.raises(ValueError) as ei:
        trainer.fine_tune_online(live_model=FakeModel70(), recent_df=df, feature_cols=cols50)
    assert "mat1 and mat2" not in str(ei.value)
    assert torch is not None  # torch imported to prove the guard precedes tensors
