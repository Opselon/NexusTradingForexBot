"""BUG-269 regression: docker starter provisioner must mint a SERVABLE bundle.

Root cause (agents/bugs.md BUG-269): the P0 serving gate (60b785d3 / #154)
refuses fresh-init and behaviorally-degenerate weights at LOAD time
(application/live/model_bundle_store.py::_load_or_create_bundle), but
docker/provision_model.py kept minting UNTRAINED ScalpNet weights with a
matching manifest digest. The bundle classified VERIFIED (self-referential
digest check passes) and then died on the behavioral probe
(DEGENERATE:logit_std < 0.15) -> MODEL_LOAD_REJECTED -> the fresh-volume
container restart loop the provisioner exists to prevent.

Evidence shape this suite pins:
  * the OLD mint recipe is digest-VERIFIED but behavioral-DEGENERATE
    (the silent-skip trap — digest trust alone must never gate re-provision);
  * the NEW provisioner mints a bundle that passes BOTH engine serving gates;
  * a pre-existing digest-valid but unservable bundle is re-provisioned
    (repair path), a servable one is skipped byte-identically (champion
    preservation), and a tampered digest re-provisions;
  * BUG-154 determinism contract: seed BEFORE construction — two independent
    mints are byte-identical.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "docker" / "provision_model.py"

torch = pytest.importorskip("torch")


def _load_provisioner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Load the REAL provisioner module with NSE_WORKSPACE pointing at tmp."""
    monkeypatch.setenv("NSE_WORKSPACE", str(tmp_path))
    spec = importlib.util.spec_from_file_location("provision_model_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _artifact_dir(tmp_path: Path) -> Path:
    return tmp_path / "artifacts/models/scalp/XAUUSD/70d_liquidity"


def _old_mint(dir_: Path) -> Path:
    """Reproduce the pre-fix mint recipe verbatim (untrained, ambient RNG)."""
    from nexus_scalp.models.scalp_net import ScalpNet

    dir_.mkdir(parents=True, exist_ok=True)
    model = dir_ / "model.pt"
    net = ScalpNet(num_features=70, num_classes=3)
    net.eval()
    torch.save(net.state_dict(), model)
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    (dir_ / "manifest.json").write_text(
        json.dumps({"model_sha256": digest, "manifest_version": "1"}, indent=2),
        encoding="utf-8",
    )
    import numpy as np

    np.savez(dir_ / "model.scaler.npz", mean=np.zeros(70), std=np.ones(70))
    return model


def _gates(model: Path) -> tuple[bool, bool]:
    """(integrity_verified, behaviorally_healthy) — the two engine load gates."""
    from nexus_scalp.model_lifecycle.integrity import check_model_behavioral_health
    from nexus_scalp.model_lifecycle.load_integrity import verify_artifact_integrity

    try:
        verdict = verify_artifact_integrity(model)
        ok_digest = verdict.status.value == "VERIFIED"
    except Exception:
        ok_digest = False
    healthy, _detail, _metrics = check_model_behavioral_health(model, None)
    return ok_digest, bool(healthy)


# ---------------------------------------------------------------------------
# 1. The defect shape: old mint is VERIFIED-but-degenerate (silent skip trap)
# ---------------------------------------------------------------------------


def test_old_untrained_mint_passes_digest_gate_but_fails_serving_gate(
    tmp_path: Path,
) -> None:
    """RED-before evidence: the pre-fix starter WOULD be skipped by the
    digest-only idempotence check yet the engine refuses to serve it.

    The digest half is deterministic by construction (self-written manifest).
    The behavioral floor (logit_std < 0.15) has ~1e-18 odds per ambient draw;
    a random-draw outlier failing the digest half would be a NEW BUG-269 (the
    gate bypassing a supposedly-verified starter) and must fail this test.
    """
    model = _old_mint(_artifact_dir(tmp_path))
    ok_digest, healthy = _gates(model)
    assert ok_digest, "old mint must classify VERIFIED (the silent-skip trap)"
    assert not healthy, "old mint must FAIL the behavioral serving gate (BUG-269)"


# ---------------------------------------------------------------------------
# 2. The fix: provisioner output passes BOTH engine serving gates
# ---------------------------------------------------------------------------


def test_provisioner_mint_is_servable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_provisioner(tmp_path, monkeypatch)
    assert mod.main() == 0
    dir_ = _artifact_dir(tmp_path)
    model = dir_ / "model.pt"
    ok_digest, healthy = _gates(model)
    assert ok_digest and healthy
    # sidecars complete + meta contract
    meta = json.loads((dir_ / "model.meta.json").read_text(encoding="utf-8"))
    assert meta["feature_schema_id"] == "scalp_v3"
    assert meta["feature_schema_dimension"] == 70
    import numpy as np

    scaler = np.load(dir_ / "model.scaler.npz")
    assert np.asarray(scaler["mean"]).shape == (70,)
    assert float(np.abs(np.asarray(scaler["std"])).min()) > 0


# ---------------------------------------------------------------------------
# 3. Repair path: digest-valid but unservable starter gets replaced
# ---------------------------------------------------------------------------


def test_degenerate_digest_valid_bundle_is_reprovisioned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dir_ = _artifact_dir(tmp_path)
    model = _old_mint(dir_)
    before = model.read_bytes()
    mod = _load_provisioner(tmp_path, monkeypatch)
    assert mod.main() == 0
    assert model.read_bytes() != before, "degenerate starter must be re-minted"
    ok_digest, healthy = _gates(model)
    assert ok_digest and healthy


# ---------------------------------------------------------------------------
# 4. Champion preservation: servable bundle skipped byte-identically
# ---------------------------------------------------------------------------


def test_servable_bundle_is_skipped_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_provisioner(tmp_path, monkeypatch)
    assert mod.main() == 0
    dir_ = _artifact_dir(tmp_path)
    model = dir_ / "model.pt"
    bytes_before = model.read_bytes()
    mtime_before = model.stat().st_mtime_ns
    monkeypatch.setenv("NSE_WORKSPACE", str(tmp_path))
    assert mod.main() == 0, "idempotent re-run must exit 0"
    assert model.read_bytes() == bytes_before
    assert model.stat().st_mtime_ns == mtime_before, "skip must not rewrite the file"


# ---------------------------------------------------------------------------
# 5. Tampered digest: re-provision (old contract kept)
# ---------------------------------------------------------------------------


def test_tampered_digest_reprovisions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_provisioner(tmp_path, monkeypatch)
    assert mod.main() == 0
    dir_ = _artifact_dir(tmp_path)
    model = dir_ / "model.pt"
    good = model.read_bytes()
    # tamper: mutate weights without refreshing the manifest digest
    state = torch.load(model, map_location="cpu", weights_only=True)
    state["input_projection.weight"] = state["input_projection.weight"] + 1.0
    torch.save(state, model)
    assert model.read_bytes() != good
    assert mod.main() == 0
    ok_digest, healthy = _gates(model)
    assert ok_digest and healthy
    assert _gates(model)[0], "re-minted bundle must be digest-VERIFIED again"


# ---------------------------------------------------------------------------
# 6. BUG-154 determinism contract: seed BEFORE construct -> byte-stable mint
# ---------------------------------------------------------------------------


def _mint_to(path: Path) -> Path:
    spec = importlib.util.spec_from_file_location("provision_model_mint", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    path.parent.mkdir(parents=True, exist_ok=True)
    mod._mint_trained_starter(path)
    return path


def test_mint_recipe_is_deterministic(tmp_path: Path) -> None:
    """Two independent in-process mints must be byte-identical (BUG-154:
    torch.manual_seed BEFORE ScalpNet construction; construct-then-seed made
    the bundle's health machine-dependent)."""
    m1 = _mint_to(tmp_path / "x" / "model.pt")
    m2 = _mint_to(tmp_path / "y" / "model.pt")
    assert m1.read_bytes() == m2.read_bytes()


# ---------------------------------------------------------------------------
# 7. Source contract: fail-loud seam + seed-before-construct survive edits
# ---------------------------------------------------------------------------


def test_provisioner_source_contract() -> None:
    src = SCRIPT.read_text(encoding="utf-8")
    # scope the ordering contract to the mint function body (the module
    # docstring legitimately mentions ScalpNet first)
    mint_body = src[src.index("def _mint_trained_starter") :]
    seed_at = mint_body.index("torch.manual_seed(MINT_SEED)")
    ctor_at = mint_body.index("ScalpNet(")
    assert seed_at < ctor_at, "manual_seed must precede ScalpNet construction"
    # never relax-the-gate drift: the mint must re-check the SERVING gates
    assert "_is_servable(model)" in src, "mint must probe the serving gates"
    assert "PROVISIONING_ERROR" in src, "unservable mint must fail loud"
    # digest-match alone must never be the skip condition
    assert "is_servable" in src.split("bundle verified")[0], (
        "skip branch must gate on servability, not only the digest"
    )
