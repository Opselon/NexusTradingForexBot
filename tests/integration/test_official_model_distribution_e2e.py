"""One offline acceptance path. Synthetic training is TEST ONLY, NEVER public.

Only transport and the ephemeral trust root are simulated. Publisher, signature,
contract, tensors, behavioral health, transaction and coordinator are real.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest


def build_test_only_trained_bundle(folder: Path) -> Path:
    """Train a small deterministic classifier, not a market model or starter."""
    import numpy as np
    import torch

    from nexus_scalp.features.schema_contract import DIMENSION, SCHEMA_ID, feature_schema_hash
    from nexus_scalp.model_provisioning.official_contract import architecture_parameters
    from nexus_scalp.models.scalp_net import ScalpNet
    from nexus_scalp.release import bootstrap as rb

    folder.mkdir(parents=True)
    threads = torch.get_num_threads()
    try:
        torch.set_num_threads(1)
        with torch.random.fork_rng():
            torch.manual_seed(42)
            x = torch.randn(96, DIMENSION).clamp(-3, 3)
            y = torch.where(x.mean(dim=1) > 0.04, 1, torch.where(x.mean(dim=1) < -0.04, 2, 0))
            model = ScalpNet(**architecture_parameters())
            optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
            model.train()
            for _ in range(35):
                optimizer.zero_grad()
                loss = torch.nn.functional.cross_entropy(model(x, return_logits=True), y)
                loss.backward()
                optimizer.step()
            torch.save(model.state_dict(), folder / "model.pt")
            np.savez(
                folder / "model.scaler.npz", mean=x.numpy().mean(axis=0), std=x.numpy().std(axis=0)
            )
            dataset_sha = hashlib.sha256(x.numpy().tobytes() + y.numpy().tobytes()).hexdigest()
    finally:
        torch.set_num_threads(threads)
    provenance = {
        "producer": {
            "python": platform.python_version(),
            "pytorch": torch.__version__,
            "git_commit": "a" * 40,
        },
        "training": {
            "command": "pytest:test-only-synthetic-classifier-never-public",
            "seed": 42,
            "dataset": {"id": "test-only-synthetic-never-public", "sha256": dataset_sha},
        },
    }
    meta = {
        "feature_schema_id": SCHEMA_ID,
        "feature_schema_hash": feature_schema_hash(),
        "feature_schema_dimension": DIMENSION,
        "num_classes": 3,
        "model_head_classes": 3,
        "architecture": "ScalpNet",
        "architecture_version": "1.0.0",
        "architecture_parameters": architecture_parameters(),
        "input_layout": "batch_features",
        "class_labels": ["NO_TRADE", "BUY", "SELL"],
        "symbol": "XAUUSD",
        "timeframe": "M1",
        "test_only_notice": "Synthetic training fixture; never publish or trade",
    }
    manifest = {
        "manifest_version": "1",
        "model_sha256": rb.sha256_file(folder / "model.pt"),
        "scaler_sha256": rb.sha256_file(folder / "model.scaler.npz"),
        **provenance,
    }
    for name, record in [
        ("model.meta.json", meta),
        ("manifest.json", manifest),
        ("trainer-manifest.json", manifest),
        ("provenance.json", provenance),
    ]:
        (folder / name).write_text(json.dumps(record), encoding="utf-8")
    compatibility = {
        "consumer": {
            "python": f"=={platform.python_version()}",
            "pytorch": f"=={torch.__version__}",
            "platforms": [f"{sys.platform}-{platform.machine().lower()}"],
        },
        "verification_runtime": {"python": "3.11", "pytorch": torch.__version__.split("+")[0]},
        "metadata_bindings": meta,
    }
    (folder / "compatibility.json").write_text(json.dumps(compatibility), encoding="utf-8")
    assert rb.bundle_status(folder / "model.pt")["state"] == rb.STATE_OK
    return folder


def test_official_model_publish_download_install_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket
    import urllib.request

    import nacl.signing

    def no_network(*args, **kwargs):
        pytest.fail("Acceptance test attempted a real network connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)

    from nexus_scalp.model_provisioning import official as off
    from nexus_scalp.model_provisioning import official_install as installer
    from nexus_scalp.model_provisioning import service as prov
    from nexus_scalp.release import paths as rpaths
    from nexus_scalp.release.signing import trusted_keys
    from scripts.release import build_official_bundle as publisher

    source = build_test_only_trained_bundle(tmp_path / "trained-test-only")
    seed = nacl.signing.SigningKey.generate()
    monkeypatch.setitem(
        trusted_keys.TRUSTED_UPDATE_KEYS, "test-e2e", seed.verify_key.encode().hex()
    )
    monkeypatch.setenv("NSE_UPDATE_SIGNING_KEY", seed.encode().hex())
    monkeypatch.setattr(trusted_keys, "ACTIVE_TRUST_ROOT", "test-e2e")
    output = tmp_path / "prepared"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_official_bundle.py",
            "--bundle-dir",
            str(source),
            "--out",
            str(output),
            "--model-version",
            "1.0.0",
        ],
    )
    assert publisher.main() == 0
    manifest_bytes = (output / "manifest.json").read_bytes()
    manifest = off.verify_bundle_manifest(json.loads(manifest_bytes))

    # Simulated GitHub publication finishes BEFORE any client request. Assets
    # retain real publisher bytes, under the exact signed immutable URLs.
    published = {
        entry["url"]: (output / name).read_bytes() for name, entry in manifest["files"].items()
    }
    stable = "https://github.com/Opselon/NexusTradingForexBot/releases/download/official-model-stable/manifest.json"
    published[stable] = manifest_bytes
    requested = []

    class Response(io.BytesIO):
        url: str
        headers: dict[str, str]

        def geturl(self):
            return self.url

    def transport(request, **kwargs):
        url = request.full_url
        requested.append(url)
        assert url in published, f"Unpublished simulated asset: {url}"
        response = Response(published[url])
        response.url = url
        response.headers = {"Content-Length": str(len(published[url]))}
        return response

    monkeypatch.setattr(urllib.request, "urlopen", transport)
    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        lambda self, request, **kwargs: transport(request, **kwargs),
    )
    workspace = tmp_path / "client"
    monkeypatch.setattr(rpaths, "get_runtime_workspace", lambda: workspace)
    monkeypatch.setattr(rpaths, "get_data_root", lambda: workspace / "data")
    monkeypatch.delenv(off.OFFICIAL_BASE_URL_ENV, raising=False)
    coordinator = prov.FirstRunCoordinator(official=off.OfficialBundleSource(base_url=stable))
    replaced = []
    real_replace = os.replace

    def record_replace(source, destination):
        replaced.append(Path(destination).name)
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", record_replace)
    result = coordinator.download_official()
    assert result["servable"] is True
    assert result["origin"] == prov.ORIGIN_OFFICIAL
    assert result["state"] == "ready"
    assert result["bundle_id"] == manifest["bundle_id"]
    assert result["model_version"] == manifest["model_version"]
    assert ".official-journal.json" in replaced, "coordinator bypassed journaled installer"
    slot = prov.serving_model_path()
    assert (slot.parent / "manifest.json").read_bytes() == manifest_bytes
    for name, entry in manifest["files"].items():
        assert hashlib.sha256((slot.parent / name).read_bytes()).hexdigest() == entry["sha256"]
    classified = prov.classify_serving_slot(slot)
    assert classified.servable and classified.origin == prov.ORIGIN_OFFICIAL
    assert requested[0] == stable
    assert set(requested[1:]) == set(published) - {stable}

    verified = off.VerifiedBundle(output, manifest)
    # Persistent OS slot lock: never unlinked (installer design) — assert
    # LIVENESS, not file absence. A competing startup must acquire cleanly
    # after the first install released ownership.
    slot_lock_path = slot.parent / ".official-install.lock"
    with installer.model_slot_lock(slot):
        pass  # liveness probe: acquisition succeeds post-transaction
    holder_script = (
        "import fcntl, os, sys, time\n"
        "path = sys.argv[1]\n"
        "os.makedirs(os.path.dirname(path), exist_ok=True)\n"
        "fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "sys.stdout.write('LOCKED\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_script, str(slot_lock_path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "LOCKED"
        with pytest.raises(off.OfficialBundleError, match="INSTALL_CONFLICT"):
            installer.install_verified_bundle(verified, slot)
        with pytest.raises(off.OfficialBundleError, match="INSTALL_CONFLICT"):
            with installer.model_slot_lock(slot):
                pytest.fail("competing startup acquired the install lock")
    finally:
        holder.kill()
        holder.wait(timeout=10)
    pid = rpaths.get_data_root() / "nexus.pid"
    pid.parent.mkdir(parents=True, exist_ok=True)
    pid.write_text(str(os.getpid()), encoding="utf-8")
    try:
        # A running PID must block acquisition; engine_boot uses the same guard.
        with pytest.raises(off.OfficialBundleError, match="ENGINE_RUNNING"):
            installer.install_verified_bundle(verified, slot)
    finally:
        pid.unlink()
    assert (slot.parent / "manifest.json").read_bytes() == manifest_bytes
