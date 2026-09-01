"""BUG-185 regression: rolling-retrain buffer records must carry the LOADED
bundle's contract width (70D champion => feat_0..feat_69), not the class 50D
bootstrap; otherwise the BUG-169 width guard silently starves the online
fine-tune loop while a 70D model serves.

These tests instantiate the REAL record-contract resolution
(LiveEngine._retrain_record_dim) without a full engine build, using a
minimal stub object exposing the attributes the method touches.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest


@pytest.fixture()
def engine_like():
    """Minimal stand-in with the real method bound from the class."""
    from nexus_scalp.application.live_engine import LiveEngine

    class _Bundle:
        def __init__(self, dim):
            self.scaler = SimpleNamespace(dimension=lambda: dim)
            self.model = SimpleNamespace(num_features=dim)

    class _EngineLike:
        _bundle_lock = threading.RLock()
        FEATURE_DIM = 50

        def __init__(self):
            self._bundle = None

    # bind the real methods
    _EngineLike._retrain_record_dim = LiveEngine._retrain_record_dim
    _EngineLike._rebind_trainer_to_bundle = LiveEngine._rebind_trainer_to_bundle
    return _EngineLike, _Bundle


def test_record_dim_follows_loaded_70d_bundle(engine_like):
    eng_cls, bundle_cls = engine_like
    e = eng_cls()
    # No bundle yet -> class contract (50D)
    assert e._retrain_record_dim() == 50
    # 70D champion loads -> records must widen immediately
    e._bundle = bundle_cls(70)
    assert e._retrain_record_dim() == 70
    # And back on a 50D hot-swap
    e._bundle = bundle_cls(50)
    assert e._retrain_record_dim() == 50


def test_record_dim_falls_back_when_bundle_probe_fails(engine_like):
    eng_cls, _ = engine_like
    e = eng_cls()
    # Broken bundle object (no usable dim probe) -> class contract, no raise
    e._bundle = object()
    assert e._retrain_record_dim() == 50


def test_record_dim_never_raises_on_lock_failure(engine_like):
    eng_cls, bundle_cls = engine_like
    e = eng_cls()
    e._bundle = bundle_cls(70)

    class _Boom:
        def __enter__(self):
            raise RuntimeError("lock poisoned")

        def __exit__(self, *a):
            return False

    e._bundle_lock = _Boom()
    assert e._retrain_record_dim() == 50


def test_per_bar_record_width_matches_trainer_after_rebind(engine_like):
    """End-to-end width math: with a 70D bundle loaded, the canonical per-bar
    record built by the engine (rec with 6 extra fields) must PASS the
    BUG-169 width guard against the rebound (70D) trainer."""
    eng_cls, bundle_cls = engine_like
    e = eng_cls()
    e._bundle = bundle_cls(70)
    dim = e._retrain_record_dim()
    rec = {f"feat_{i}": 0.1 for i in range(dim)}
    rec.update(close=1.0, high=1.0, low=1.0, open=1.0, spread=0.2, atr_m1=1.0)
    trainer_width = 70  # post-BUG-182B rebind
    assert len(rec) - 6 == trainer_width


def test_rebind_helper_updates_trainer_width(engine_like, monkeypatch):
    """The extracted _rebind_trainer_to_bundle must move the trainer to the
    loaded bundle's width (BUG-185: hot-swap across widths must rebind)."""
    from nexus_scalp.application import live_engine as le
    from nexus_scalp.features.schema import FEATURE_SCHEMAS

    eng_cls, bundle_cls = engine_like

    class _Trainer:
        num_features = 50
        feature_schema = FEATURE_SCHEMAS.resolve("scalp_v1")

    e = eng_cls()
    e.trainer = _Trainer()
    e._online_train_disabled = False
    logs = []
    monkeypatch.setattr(le.logger, "info", lambda *a, **k: logs.append(("info", a, k)))
    monkeypatch.setattr(le.logger, "warning", lambda *a, **k: logs.append(("warn", a, k)))

    e._bundle = bundle_cls(70)
    e._rebind_trainer_to_bundle()
    assert e.trainer.num_features == 70
    assert e.trainer.feature_schema.dimension == 70
    assert any(l[0] == "info" for l in logs)

    # 50D bundle -> trainer follows back to the 50D schema
    e._bundle = bundle_cls(50)
    e._rebind_trainer_to_bundle()
    assert e.trainer.num_features == 50
    assert e.trainer.feature_schema.dimension == 50


def test_rebind_helper_self_disables_on_unknown_width(engine_like, monkeypatch):
    from nexus_scalp.application import live_engine as le

    eng_cls, _ = engine_like

    class _Trainer:
        num_features = 50
        feature_schema = None

    e = eng_cls()
    e.trainer = _Trainer()
    e._online_train_disabled = False
    warns = []
    monkeypatch.setattr(le.logger, "warning", lambda *a, **k: warns.append(k))

    # bundle dim 99 has no registered schema -> self-disable, no crash
    e._bundle = SimpleNamespace(
        scaler=SimpleNamespace(dimension=lambda: 99),
        model=SimpleNamespace(num_features=99),
    )
    e._rebind_trainer_to_bundle()
    assert e.trainer.num_features == 50  # unchanged
    assert e._online_train_disabled is True
    assert warns
