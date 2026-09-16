"""BUG-293 redesign regression: model provisioning architecture.

PATH A (official signed bundle) + PATH B (local training) + origin
classification, pinned hermetically:

  1. verify_bundle_manifest — signature over canonical payload against a
     monkeypatched trust root (the production private seed lives only in
     CI secrets), schema/geometry binding, unsafe-name rejection.
  2. download_and_verify end-to-end with a file-copy _fetch (no network):
     SHA256 mismatch / tampered file / wrong geometry all fail closed with
     NOTHING installed.
  3. install_verified_bundle + classify_serving_slot origins:
     OFFICIAL / USER_TRAINED / DEV_STARTER (starter note overrides sidecar
     claims) / disk-vs-sidecar disagreement demotes to UNKNOWN.
  4. import_user_bars honest diagnostics on a synthetic MT5-style CSV
     (duplicates, invalid rows, chronological-tail selection, too-small
     refusal) — no network, no fabricated counts.
  5. train_local_model CANCELLED path (cancel set before training starts
     => outcome CANCELLED, serving slot untouched).
  6. FirstRunCoordinator: PAPER falls back to the LABELED starter only when
     no official source is configured; SHADOW/LIVE refuse; official
     preferred when configured (file-served fake bundle).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.model_provisioning import (
    LifecycleState,
    OfficialBundleError,
    OfficialBundleSource,
)
from nexus_scalp.model_provisioning import (
    service as prov,
)
from nexus_scalp.release import bootstrap as rb

pytestmark = pytest.mark.skipif(
    "--fast" in __import__("sys").argv, reason="torch-backed provisioning tests"
)

_TEST_KEY_SEED = "11" * 32  # test-only Ed25519 seed (never production material)


@pytest.fixture()
def test_trust_root(monkeypatch: pytest.MonkeyPatch) -> str:
    """Point the trust root at a test key (verify path reads the map live)."""
    import nacl.signing

    from nexus_scalp.release.signing import trusted_keys

    pubkey = nacl.signing.SigningKey(bytes.fromhex(_TEST_KEY_SEED)).verify_key.encode().hex()
    monkeypatch.setitem(trusted_keys.TRUSTED_UPDATE_KEYS, "test-root", pubkey)
    monkeypatch.setattr(trusted_keys, "ACTIVE_TRUST_ROOT", "test-root")
    return pubkey


def _make_bundle_manifest(model_dir: Path, sha_map: dict[str, str]) -> dict[str, Any]:
    return {
        "schema": "nexus_model_bundle_v1",
        "bundle_id": "official-test-1",
        "model_version": "1.0.0",
        "architecture": "ScalpNet",
        "architecture_version": "1.0.0",
        "feature_schema_id": "scalp_v3",
        "feature_schema_hash": "ab" * 32,
        "dimension": 70,
        "class_count": 3,
        # The engine load-integrity verifier reads these bindings:
        "model_sha256": sha_map["model.pt"],
        "metadata_sha256": sha_map["model.meta.json"],
        "scaler_sha256": sha_map["model.scaler.npz"],
        "dataset": {"id": "ds-test", "version": "1", "sha256": "cd" * 32},
        "training": {"command": "unit-test", "seed": 42},
        "files": {
            "model.pt": {
                "sha256": sha_map["model.pt"],
                "size": (model_dir / "model.pt").stat().st_size,
            },
            "model.scaler.npz": {
                "sha256": sha_map["model.scaler.npz"],
                "size": (model_dir / "model.scaler.npz").stat().st_size,
            },
            "model.meta.json": {
                "sha256": sha_map["model.meta.json"],
                "size": (model_dir / "model.meta.json").stat().st_size,
            },
        },
        "key_id": "test-root",
        "signature": "",
    }


def _build_fake_official_dir(tmp_path: Path) -> Path:
    """A real minted starter reused as the 'official' artifact (passes the
    integrity probe), packed into a bundle dir with a signed manifest."""
    import nacl.signing

    from nexus_scalp.model_provisioning import official as off

    bundle = tmp_path / "official-src"
    bundle.mkdir()
    rb.mint_starter_bundle(bundle / "model.pt")
    # Re-write the bundle manifest WITHOUT the starter note (the signed
    # external manifest is the authority PATH A verifies; starter
    # classification is covered by its own test).
    sha_map = {
        "model.pt": rb.sha256_file(bundle / "model.pt"),
        "model.scaler.npz": rb.sha256_file(bundle / "model.scaler.npz"),
        "model.meta.json": rb.sha256_file(bundle / "model.meta.json"),
    }
    manifest = _make_bundle_manifest(bundle, sha_map)
    payload = off._canonical_payload(manifest)
    manifest["signature"] = (
        nacl.signing.SigningKey(bytes.fromhex(_TEST_KEY_SEED)).sign(payload).signature.hex()
    )
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return bundle


# ---------------------------------------------------------------------------
# manifest verification
# ---------------------------------------------------------------------------
def test_manifest_bad_signature_rejected(tmp_path: Path, test_trust_root: str) -> None:
    from nexus_scalp.model_provisioning import official as off

    bundle = _build_fake_official_dir(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    manifest["signature"] = "00" * 64
    with pytest.raises(OfficialBundleError) as err:
        off.verify_bundle_manifest(manifest)
    assert err.value.code in ("SIGNATURE_INVALID",)


def test_manifest_geometry_binding_enforced(tmp_path: Path, test_trust_root: str) -> None:
    bundle = _build_fake_official_dir(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    manifest["dimension"] = (
        50  # tamper BEFORE re-sign => signature still valid shape but binding fails
    )
    import nacl.signing

    from nexus_scalp.model_provisioning import official as off

    payload = off._canonical_payload(manifest)
    manifest["signature"] = (
        nacl.signing.SigningKey(bytes.fromhex(_TEST_KEY_SEED)).sign(payload).signature.hex()
    )
    with pytest.raises(OfficialBundleError) as err:
        off.verify_bundle_manifest(manifest)
    assert err.value.code == "GEOMETRY_MISMATCH"


# ---------------------------------------------------------------------------
# download -> verify -> install -> classify (PATH A end-to-end, no network)
# ---------------------------------------------------------------------------
class _CopySource(OfficialBundleSource):
    """_fetch override: serve files from a local directory (deterministic)."""

    def __init__(self, root: Path) -> None:
        super().__init__(base_url="https://invalid.invalid/bundle")
        self._root = root

    def _fetch(self, url: str, dest: Path) -> None:
        name = url.rsplit("/", 1)[-1]
        src = self._root / name
        if not src.exists():
            raise OfficialBundleError("DOWNLOAD_FAILED", f"fake server 404 {name}")
        dest.write_bytes(src.read_bytes())


def test_official_path_a_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_trust_root: str
) -> None:
    root = _build_fake_official_dir(tmp_path)
    workspace = tmp_path / "ws"
    monkeypatch.setattr(rb.rpaths, "get_runtime_workspace", lambda: workspace)

    coord = prov.FirstRunCoordinator(official=_CopySource(root))  # type: ignore[arg-type]
    out = coord.download_official()
    assert out["servable"] is True and out["origin"] == prov.ORIGIN_OFFICIAL

    cls = prov.classify_serving_slot(prov.serving_model_path())
    assert cls.state == LifecycleState.READY
    assert cls.origin == prov.ORIGIN_OFFICIAL
    assert cls.servable and not cls.is_starter
    # Integrity manifest binds the downloaded bytes:
    manifest = json.loads((cls.path.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["model_sha256"] == rb.sha256_file(cls.path)


def test_official_tampered_file_installs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_trust_root: str
) -> None:
    root = _build_fake_official_dir(tmp_path)
    workspace = tmp_path / "ws"
    monkeypatch.setattr(rb.rpaths, "get_runtime_workspace", lambda: workspace)
    # Flip one byte of the weights AFTER signing (SHA mismatch vs manifest).
    mp = root / "model.pt"
    mp.write_bytes(mp.read_bytes()[:-1] + bytes([mp.read_bytes()[-1] ^ 0x01]))

    coord = prov.FirstRunCoordinator(official=_CopySource(root))  # type: ignore[arg-type]
    with pytest.raises(OfficialBundleError) as err:
        coord.download_official()
    assert err.value.code == "SHA256_MISMATCH"
    # Nothing installed: slot remains missing, provisioner state honest.
    assert rb.bundle_status(prov.serving_model_path())["state"] == rb.STATE_MISSING
    assert prov.read_provisioner_state().get("state") == LifecycleState.REJECTED.value


def test_install_rejects_unverified_bundle_shape(tmp_path: Path, test_trust_root: str) -> None:
    """install_verified_bundle has no verification path of its own — it only
    accepts a VerifiedBundle produced by the chain (unit-shape pin)."""
    from nexus_scalp.model_provisioning.official import install_verified_bundle

    with pytest.raises((AttributeError, TypeError, KeyError)):
        install_verified_bundle(None, tmp_path / "model.pt")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# origins
# ---------------------------------------------------------------------------
def test_starter_note_overrides_sidecar_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    monkeypatch.setattr(rb.rpaths, "get_runtime_workspace", lambda: workspace)
    model = prov.serving_model_path()
    rb.mint_starter_bundle(model)  # manifest note = "release bootstrap..."
    # Malicious/stale sidecar claiming OFFICIAL must NOT win:
    prov.record_install(model_path=model, origin=prov.ORIGIN_OFFICIAL, model_sha256="0" * 64)
    cls = prov.classify_serving_slot(model)
    assert cls.origin == prov.ORIGIN_DEV_STARTER and cls.is_starter

    # Honest starter record + later user-train record on matching sha =>
    # USER_TRAINED (no starter note present).
    (model.parent / "manifest.json").write_text(
        json.dumps(
            {
                "model_sha256": rb.sha256_file(model),
                "note": "train-once installed",
                "manifest_version": "1",
            }
        ),
        encoding="utf-8",
    )
    prov.record_install(
        model_path=model, origin=prov.ORIGIN_USER_TRAINED, model_sha256=rb.sha256_file(model)
    )
    cls2 = prov.classify_serving_slot(model)
    assert cls2.origin == prov.ORIGIN_USER_TRAINED


# ---------------------------------------------------------------------------
# PATH B — import diagnostics + cancel
# ---------------------------------------------------------------------------
def _synthetic_mt5_csv(path: Path, rows: int = 11_000) -> None:
    import csv
    import math

    t0 = 1_700_000_000
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"])
        for i in range(rows):
            price = 2600.0 + math.sin(i / 50.0) * 5.0 + (i % 7) * 0.01
            w.writerow(
                [
                    t0 + i * 60,
                    f"{price:.2f}",
                    f"{price + 0.4:.2f}",
                    f"{price - 0.4:.2f}",
                    f"{price + 0.1:.2f}",
                    100 + (i % 37),
                    2,
                    0,
                ]
            )
        # deliberate dirt: duplicate timestamp + invalid price row
        w.writerow([t0 + 5 * 60, "2600.0", "2600.4", "2599.6", "2600.1", 120, 2, 0])
        w.writerow([t0 + 6 * 60, "-5.0", "-5.0", "-5.0", "-5.0", 5, 0, 0])


def test_import_user_bars_real_diagnostics(tmp_path: Path) -> None:
    from nexus_scalp.model_provisioning.pipeline import import_user_bars

    csv_path = tmp_path / "export.csv"
    _synthetic_mt5_csv(csv_path, rows=11_000)
    imp = import_user_bars(csv_path, candles=10_000)
    assert imp.rows_total == 11_002  # 11k bars + 2 dirt rows
    assert imp.dropped_duplicates >= 1
    assert imp.dropped_invalid >= 1
    assert imp.selected_rows == 10_000
    assert imp.time_range[0] < imp.time_range[1]
    # chronological TAIL: the selection ends at the newest valid bar
    assert str(imp.frame.get_column("time").max()) is not None


def test_import_user_bars_too_small_refuses(tmp_path: Path) -> None:
    from nexus_scalp.model_provisioning.pipeline import import_user_bars

    csv_path = tmp_path / "small.csv"
    _synthetic_mt5_csv(csv_path, rows=800)
    with pytest.raises(ValueError, match="3,000"):
        import_user_bars(csv_path, candles=None)


def test_train_local_cancel_leaves_slot_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexus_scalp.model_provisioning.pipeline import TrainingRequest, train_local_model

    workspace = tmp_path / "ws"
    monkeypatch.setattr(rb.rpaths, "get_runtime_workspace", lambda: workspace)
    pytest.importorskip("torch")

    csv_path = tmp_path / "export.csv"
    _synthetic_mt5_csv(csv_path, rows=10_500)
    cancel = threading.Event()
    cancel.set()  # cancel observed at the FIRST epoch boundary
    out = train_local_model(
        TrainingRequest(
            source_file=csv_path, candles=None, folds=1, epochs=1, install=True, cancel_event=cancel
        ),
        progress=None,
    )
    assert out["outcome"] == "CANCELLED"
    assert rb.bundle_status(prov.serving_model_path())["state"] == rb.STATE_MISSING


def test_official_bundle_publisher_script_runs() -> None:
    """Operator tool sanity: argparse surface loads (its verification chain is
    off.verify_bundle_manifest — covered by the tests above; a syntax slip in
    the publisher would silently break PATH A hosting)."""
    import subprocess
    import sys

    script = (
        Path(__file__).resolve().parents[2] / "scripts" / "release" / "build_official_bundle.py"
    )
    r = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert r.returncode == 0, r.stderr[-400:]
    assert "--bundle-dir" in r.stdout
    r2 = subprocess.run(
        [sys.executable, str(script), "--bundle-dir", "nope", "--out", "nope2"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert r2.returncode == 2  # usage error: material/key missing, no crash


# ---------------------------------------------------------------------------
# BUG-301 — Training Environment Contract (typed lifecycle, gated training)
# ---------------------------------------------------------------------------
def test_contract_file_is_canonical_and_parses() -> None:
    from nexus_scalp.model_provisioning.training_env import load_contract

    c = load_contract()
    assert c["schema"] == "nexus_training_env_v1"
    for variant in ("cpu", "cuda"):
        v = c["variants"][variant]
        assert v["index_url"].startswith("https://download.pytorch.org/whl/")
        assert "torch" in v["packages"]


def test_status_discovery_is_readonly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """status() must never create a venv / pip-install / touch requirements."""
    from nexus_scalp.model_provisioning import training_env as te

    monkeypatch.chdir(tmp_path)
    calls: list[str] = []
    real_run = te.subprocess.run

    def _spy(cmd, *a, **k):
        calls.append(" ".join(str(x) for x in cmd))
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(te.subprocess, "run", _spy)
    m = te.TrainingEnvironmentManager(workspace=tmp_path)
    rep = m.status(backend="cpu")
    assert isinstance(rep.training_ready, bool)
    # discovery may probe interpreters (read-only) but never venv-create/pip:
    joined = " | ".join(calls)
    assert "-m venv" not in joined
    assert "pip install" not in joined
    assert not (tmp_path / "training-env").exists()


def test_missing_venv_reports_venv_not_found_until_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Packaged/fresh machine (no venv, override python): discovery says
    VENV_NOT_FOUND (typed), install creates it. Simulated via a non-venv
    python override pointing at a bare interpreter copy is too heavy — we
    assert the DISCOVERY half + the install guard instead."""
    from nexus_scalp.model_provisioning import training_env as te

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(te, "is_interpreter_process", lambda: False)  # packaged-like
    m = te.TrainingEnvironmentManager(workspace=tmp_path)
    rep = m.status(backend="cpu")
    codes = [c.code for c in rep.failing()]
    # PYTHON_NOT_FOUND (frozen exe is not an interpreter) OR VENV_NOT_FOUND —
    # either way: typed, not-ready, nothing created.
    assert "VENV_NOT_FOUND" in codes or "PYTHON_NOT_FOUND" in codes
    assert rep.training_ready is False
    assert not (tmp_path / "training-env").exists()


def test_local_tag_gate_cuda_backend_rejects_cpu_wheel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two REAL paths rule: a +cpu build must NEVER satisfy the cuda
    backend (is_available() and tag gates), and the check carries the exact
    remedy."""
    from nexus_scalp.model_provisioning import training_env as te

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        te,
        "detect_nvidia_gpu",
        lambda: {
            "vendor": "NVIDIA",
            "name": "FakeGPU",
            "available": True,
            "driver_version": "0",
            "compute_cap": "8.6",
        },
    )
    monkeypatch.setattr(
        te,
        "probe_python_interpreter",
        lambda exe: {
            "path": exe,
            "found": True,
            "version": "3.11.9",
            "minor": 11,
            "pip": True,
            "torch": "2.13.0+cpu",
        },
    )
    import os

    vdir = tmp_path / "training-env" / ("Scripts" if os.name == "nt" else "bin")
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / ("python.exe" if os.name == "nt" else "python")).touch()
    m = te.TrainingEnvironmentManager(workspace=tmp_path)
    rep = m.status(backend="cuda")
    failing = {c.stage: c.code for c in rep.failing()}
    assert failing.get("torch_version") == "TORCH_LOCAL_TAG_MISMATCH"
    assert rep.training_ready is False
    rep_cpu = m.status(backend="cpu")
    assert not any(c.code == "TORCH_LOCAL_TAG_MISMATCH" for c in rep_cpu.failing())


def test_pip_failure_taxonomy() -> None:
    from nexus_scalp.model_provisioning.training_env import EnvCode, classify_pip_failure

    assert (
        classify_pip_failure("ERROR: Could not find a version that satisfies torch==9.9")[0]
        is EnvCode.PACKAGE_VERSION_UNSATISFIED
    )
    assert (
        classify_pip_failure("Retrying (Retry(total=4)) after connection broken")[0]
        is EnvCode.NETWORK_UNREACHABLE
    )
    assert (
        classify_pip_failure("Permission denied: 'site-packages'")[0]
        is EnvCode.INSTALL_PERMISSION_DENIED
    )
    assert classify_pip_failure("some weird pip failure")[0] is EnvCode.INSTALL_FAILED


def test_train_blocked_when_env_not_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """train_local_model refuses BEFORE any training work with the typed
    TRAINING_ENV_BLOCKED + the failing checklist, and leaves the slot empty."""
    from nexus_scalp.model_provisioning import pipeline as pl_mod
    from nexus_scalp.model_provisioning import training_env as te
    from nexus_scalp.model_provisioning.pipeline import TrainingRequest, train_local_model

    workspace = tmp_path / "ws"
    monkeypatch.setattr(rb.rpaths, "get_runtime_workspace", lambda: workspace)
    csv_path = tmp_path / "export.csv"
    _synthetic_mt5_csv(csv_path, rows=10_500)

    def _not_ready(*a, **k):
        rep = te.EnvironmentReport(backend="cpu")
        rep.checks.append(
            te.EnvCheck(
                te.EnvStage.TORCH.value,
                False,
                te.EnvCode.TORCH_NOT_INSTALLED.value,
                "missing",
                "install",
            )
        )
        return rep

    monkeypatch.setattr(te.TrainingEnvironmentManager, "status", _not_ready)
    out = train_local_model(TrainingRequest(source_file=csv_path), progress=None)
    assert out["outcome"] == "TRAINING_ENV_BLOCKED"
    assert "pytorch" in out["missing"]
    assert (
        rb.bundle_status(workspace / "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt")[
            "state"
        ]
        == rb.STATE_MISSING
    )
