"""BUG-141: artifact width-contract guard for model.pt writers.

The 2026-08-27 corruption class: a desynced runtime state persisted a 50D
checkpoint over artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt while
the bundle's declared contract (meta.json + scaler.npz) stayed 70D. The
integrity gate then (correctly) rejected the champion and the freshness gate
blocked every proposal (BLOCKED_BY_STALE) for the whole session.

Regression contract:
  * _declared_contract_dim_for_path resolves the DECLARED width from
    meta.json -> scaler.npz -> checkpoint, None on cold-start.
  * _save_model_weights_atomic REFUSES a width-contract-mismatched write
    (artifact preserved byte-exact, no .tmp residue, CRITICAL logged).
  * Compatible writes still succeed.
  * force_fresh seeding honors the path's declared contract (never mints a
    50D file into a declared-70D path) while cold-start bootstrap stays 50D.

Tests exercise the real LiveEngine methods via __new__ (no heavy ctor) and
ONLY temp-copy artifacts — never the real serving artifacts.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.models.scalp_net import ScalpNet

REPO = Path(__file__).resolve().parents[2]
ART = REPO / "artifacts" / "models" / "scalp" / "XAUUSD"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stub() -> SimpleNamespace:
    """Minimal engine stand-in: bundle lock + the REAL declaration resolver."""
    s = SimpleNamespace(_bundle_lock=threading.Lock())
    s._declared_contract_dim_for_path = lambda p: LiveEngine._declared_contract_dim_for_path(
        None, p
    )
    return s


def _engine() -> LiveEngine:
    eng = LiveEngine.__new__(LiveEngine)
    eng._bundle_lock = threading.Lock()
    return eng


@pytest.fixture()
def tmp_70d_bundle() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="bug141_test_"))
    dst = tmp / "70d_liquidity"
    dst.mkdir()
    for name in ("model.pt", "model.scaler.npz", "model.meta.json"):
        src = ART / "70d_liquidity" / name
        if not src.exists():
            pytest.skip("70D artifact bundle not present on this machine")
        shutil.copy(src, dst / name)
    yield dst / "model.pt"
    shutil.rmtree(tmp, ignore_errors=True)


class TestDeclaredContractDim:
    def test_141_01_declared_from_meta_70d(self) -> None:
        assert (
            LiveEngine._declared_contract_dim_for_path(None, ART / "70d_liquidity" / "model.pt")
            == 70
        )

    def test_141_02_declared_from_meta_50d(self) -> None:
        assert LiveEngine._declared_contract_dim_for_path(None, ART / "v1.0.0" / "model.pt") == 50

    def test_141_03_cold_start_returns_none(self, tmp_path: Path) -> None:
        assert (
            LiveEngine._declared_contract_dim_for_path(None, tmp_path / "none" / "model.pt") is None
        )

    def test_141_04_scaler_fallback_when_no_meta(self, tmp_path: Path) -> None:
        d = tmp_path / "scaler_only"
        d.mkdir()
        np.savez(
            d / "model.scaler.npz",
            mean=np.zeros(70, dtype=np.float32),
            std=np.ones(70, dtype=np.float32),
        )
        assert LiveEngine._declared_contract_dim_for_path(None, d / "model.pt") == 70


class TestSaveWidthGuard:
    def test_141_05_refuses_50d_write_into_declared_70d_path(self, tmp_70d_bundle: Path) -> None:
        before = _sha(tmp_70d_bundle)
        m50 = ScalpNet(num_features=50, num_classes=4)
        m50.eval()
        LiveEngine._save_model_weights_atomic(_stub(), m50, tmp_70d_bundle)
        assert _sha(tmp_70d_bundle) == before, "guard failed: artifact was overwritten"
        assert not list(tmp_70d_bundle.parent.glob("*.tmp")), "tmp residue left behind"

    def test_141_06_allows_compatible_70d_write(self, tmp_70d_bundle: Path) -> None:
        m70 = ScalpNet(num_features=70, num_classes=4)
        m70.eval()
        LiveEngine._save_model_weights_atomic(_stub(), m70, tmp_70d_bundle)
        sd = torch.load(tmp_70d_bundle, map_location="cpu")
        assert tuple(sd["input_projection.weight"].shape) == (128, 70)

    def test_141_07_unrestricted_when_path_has_no_declaration(self, tmp_path: Path) -> None:
        m50 = ScalpNet(num_features=50, num_classes=4)
        m50.eval()
        target = tmp_path / "cold" / "model.pt"
        target.parent.mkdir()
        LiveEngine._save_model_weights_atomic(_stub(), m50, target)
        assert target.exists()


class TestForceFreshSeeding:
    def test_141_08_force_fresh_seeds_declared_70d_path(self, tmp_70d_bundle: Path) -> None:
        tmp_70d_bundle.unlink()  # remove checkpoint, keep meta+scaler declaration
        eng = _engine()
        m = LiveEngine._load_or_initialize_model_weights(eng, tmp_70d_bundle, force_fresh=True)
        assert m.input_projection.weight.shape[1] == 70

    def test_141_09_force_fresh_cold_start_still_50d(self, tmp_path: Path) -> None:
        eng = _engine()
        m = LiveEngine._load_or_initialize_model_weights(
            eng, tmp_path / "cold" / "model.pt", force_fresh=True
        )
        assert m.input_projection.weight.shape[1] == 50
