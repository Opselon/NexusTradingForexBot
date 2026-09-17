"""Defensive official contract tests; all artifacts/network are isolated fixtures."""

from __future__ import annotations

import copy
import hashlib
import json
import platform
import sys

import nacl.signing
import pytest

from nexus_scalp.features.schema_contract import DIMENSION, SCHEMA_ID, feature_schema_hash
from nexus_scalp.model_provisioning import official as off


@pytest.fixture
def signing_key(monkeypatch):
    from nexus_scalp.release.signing import trusted_keys

    key = nacl.signing.SigningKey.generate()
    monkeypatch.setattr(
        trusted_keys, "TRUSTED_UPDATE_KEYS", {"test": key.verify_key.encode().hex()}
    )
    return key


def sign(manifest, key):
    manifest["signature"] = key.sign(off._canonical_payload(manifest)).signature.hex()
    return manifest


def manifest_fixture(key):
    digest = hashlib.sha256(b"fixture").hexdigest()
    files = {
        name: {
            "sha256": digest,
            "size": 7,
            "url": f"https://github.com/Opselon/NexusTradingForexBot/releases/download/model-1.0.0/{name}",
        }
        for name in ("model.pt", "model.scaler.npz", "model.meta.json")
    }
    return sign(
        {
            "schema": off.BUNDLE_MANIFEST_SCHEMA,
            "contract_version": 2,
            "bundle_id": "official-xauusd-1.0.0",
            "model_version": "1.0.0",
            "architecture": "ScalpNet",
            "architecture_version": "1.0.0",
            "architecture_parameters": {
                "num_features": DIMENSION,
                "num_classes": 3,
                "hidden_dim": 128,
                "num_heads": 4,
                "dropout_rate": 0.25,
            },
            "input_layout": "batch_features",
            "class_count": 3,
            "class_labels": ["NO_TRADE", "BUY", "SELL"],
            "feature_schema_id": SCHEMA_ID,
            "feature_schema_hash": feature_schema_hash(),
            "dimension": DIMENSION,
            "symbol": "XAUUSD",
            "timeframe": "M1",
            "model_sha256": digest,
            "scaler_sha256": digest,
            "metadata_sha256": digest,
            "producer": {"python": "3.11.1", "pytorch": "2.14.0+cpu", "git_commit": "a" * 40},
            "training": {
                "command": "nexus train-once",
                "seed": 17,
                "dataset": {"id": "test-only", "sha256": digest},
            },
            "consumer": {
                "python": ">=3.11,<3.15",
                "pytorch": ">=2.14,<2.15",
                "platforms": [f"{sys.platform}-{platform.machine().lower()}"],
            },
            "files": files,
            "key_id": "test",
            "signature": "",
        },
        key,
    )


def test_valid_revision_two(signing_key):
    manifest = manifest_fixture(signing_key)
    assert off.verify_bundle_manifest(manifest) == manifest


@pytest.mark.parametrize(
    "field,value",
    [
        ("contract_version", 1),
        ("contract_version", None),
        ("feature_schema_hash", "0" * 16),
        ("architecture_version", "9.0.0"),
        ("input_layout", "sequence_features"),
        ("class_labels", ["SELL", "BUY", "NO_TRADE"]),
        ("symbol", "EURUSD"),
        ("timeframe", "M5"),
        ("architecture_parameters", {"hidden_dim": 64}),
        ("producer", {}),
        ("consumer", {"python": ">=3.11", "pytorch": "*", "platforms": []}),
        ("training", {}),
        ("model_sha256", "0" * 64),
        ("dimension", True),
    ],
)
def test_signed_contract_mismatch_rejected(signing_key, field, value):
    manifest = manifest_fixture(signing_key)
    manifest[field] = value
    sign(manifest, signing_key)
    with pytest.raises(off.OfficialBundleError):
        off.verify_bundle_manifest(manifest)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m["files"].pop("model.scaler.npz"),
        lambda m: m["files"].update({"extra.bin": copy.deepcopy(m["files"]["model.pt"])}),
        lambda m: m["files"]["model.pt"].update(sha256="z" * 64),
        lambda m: m["files"]["model.pt"].update(size=-1),
        lambda m: m["files"]["model.pt"].update(size=True),
        lambda m: m["files"]["model.pt"].update(url="http://example.test/model.pt"),
        lambda m: m["files"]["model.pt"].update(
            url="https://github.com/Opselon/NexusTradingForexBot/releases/download/official-model-stable/model.pt"
        ),
        lambda m: m["files"]["model.pt"].update(mirrors=["http://example.test/model.pt"]),
    ],
)
def test_signed_file_contract_rejected(signing_key, mutate):
    manifest = manifest_fixture(signing_key)
    mutate(manifest)
    sign(manifest, signing_key)
    with pytest.raises(off.OfficialBundleError):
        off.verify_bundle_manifest(manifest)


def test_default_discovery(monkeypatch):
    monkeypatch.delenv(off.OFFICIAL_BASE_URL_ENV, raising=False)
    assert (
        off.OfficialBundleSource().manifest_url()
        == "https://github.com/Opselon/NexusTradingForexBot/releases/download/official-model-stable/manifest.json"
    )


class MemorySource(off.OfficialBundleSource):
    def __init__(self, manifest, content):
        super().__init__("https://example.test")
        self.manifest = manifest
        self.content = content
        self.requests = []

    def _fetch(self, url, dest):
        self.requests.append(url)
        if url == self.manifest_url():
            dest.write_text(json.dumps(self.manifest))
        elif url in self.content:
            dest.write_bytes(self.content[url])
        else:
            raise off.OfficialBundleError("DOWNLOAD_FAILED", "fixture unavailable")


def byte_source(signing_key):
    manifest = manifest_fixture(signing_key)
    content = {entry["url"]: b"fixture" for entry in manifest["files"].values()}
    return MemorySource(manifest, content)


def test_mirror_bytes_verified_and_owned_staging(signing_key, tmp_path, monkeypatch):
    source = byte_source(signing_key)
    entry = source.manifest["files"]["model.pt"]
    entry["mirrors"] = ["https://drive.usercontent.google.com/download?id=test-only"]
    source.content[entry["mirrors"][0]] = source.content.pop(entry["url"])
    sign(source.manifest, signing_key)
    monkeypatch.setattr(source, "_integrity_probe", lambda path: None)
    bundle = source.download_and_verify(tmp_path)
    assert bundle.dir != tmp_path and bundle.dir.parent == tmp_path
    assert bundle.model_path().read_bytes() == b"fixture"
    assert entry["mirrors"][0] in source.requests


def test_failed_download_cleans_only_owned_staging(signing_key, tmp_path):
    source = byte_source(signing_key)
    source.content[source.manifest["files"]["model.pt"]["url"]] = b"changed"
    keep = tmp_path / "keep.txt"
    keep.write_text("unrelated")
    with pytest.raises(off.OfficialBundleError):
        source.download_and_verify(tmp_path)
    assert list(tmp_path.iterdir()) == [keep]


def test_cancelled_before_download(signing_key, tmp_path):
    import threading

    cancel = threading.Event()
    cancel.set()
    source = byte_source(signing_key)
    with pytest.raises(off.OfficialBundleError, match="CANCELLED"):
        source.download_and_verify(tmp_path, cancel_event=cancel)
    assert source.requests == []
    assert list(tmp_path.iterdir()) == []


def test_http_transport_refuses_plain_http(tmp_path, monkeypatch):
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("network forbidden"))
    with pytest.raises(off.OfficialBundleError, match="URL_UNSUPPORTED"):
        off._http_get("http://example.test/manifest.json", tmp_path / "out")


def test_redirect_downgrade_refused():
    import urllib.request

    from nexus_scalp.model_provisioning import official_transport as transport

    with pytest.raises(off.OfficialBundleError, match="URL_UNSUPPORTED"):
        transport.HTTPSRedirectHandler().redirect_request(
            urllib.request.Request("https://example.test"),
            None,
            302,
            "Found",
            {},
            "http://example.test",
        )


def test_archive_disabled(tmp_path, monkeypatch):
    source = off.OfficialBundleSource("drive:test-only")
    monkeypatch.setattr(source, "_fetch", lambda *a: pytest.fail("network forbidden"))
    with pytest.raises(off.OfficialBundleError, match="LAYOUT_UNSUPPORTED"):
        source.download_bundle_archive(tmp_path)
    assert list(tmp_path.iterdir()) == []


def stage_tensors(tmp_path, signing_key, *, seed=42):
    import numpy as np
    import torch

    from nexus_scalp.models.scalp_net import ScalpNet

    manifest = manifest_fixture(signing_key)
    with torch.random.fork_rng():
        torch.manual_seed(seed)
        model = ScalpNet(**manifest["architecture_parameters"])
    torch.save(model.state_dict(), tmp_path / "model.pt")
    np.savez(tmp_path / "model.scaler.npz", mean=np.zeros(DIMENSION), std=np.ones(DIMENSION))
    meta = {
        k: manifest[k]
        for k in (
            "architecture",
            "architecture_version",
            "architecture_parameters",
            "input_layout",
            "class_labels",
            "feature_schema_id",
            "feature_schema_hash",
            "symbol",
            "timeframe",
        )
    }
    meta.update(feature_schema_dimension=DIMENSION, num_classes=3)
    (tmp_path / "model.meta.json").write_text(json.dumps(meta))
    refresh_staged(tmp_path, manifest, signing_key)
    return manifest


def refresh_staged(path, manifest, key):
    for name, entry in manifest["files"].items():
        data = (path / name).read_bytes()
        entry.update(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
    for field, name in [
        ("model_sha256", "model.pt"),
        ("scaler_sha256", "model.scaler.npz"),
        ("metadata_sha256", "model.meta.json"),
    ]:
        manifest[field] = manifest["files"][name]["sha256"]
    sign(manifest, key)
    (path / "manifest.json").write_text(json.dumps(manifest))


def test_missing_torch_never_verified(tmp_path, signing_key, monkeypatch):
    stage_tensors(tmp_path, signing_key)
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(off.OfficialBundleError, match="VERIFICATION_PENDING"):
        off.OfficialBundleSource()._integrity_probe(tmp_path)


@pytest.mark.parametrize("change", ["metadata", "scaler", "tensor", "fresh"])
def test_staging_runtime_rejects_incompatible_artifacts(tmp_path, signing_key, change):
    import numpy as np
    import torch

    m = stage_tensors(tmp_path, signing_key)
    if change == "metadata":
        meta_path = tmp_path / "model.meta.json"
        meta = json.loads(meta_path.read_text())
        meta["class_labels"] = ["BUY", "SELL", "NO_TRADE"]
        meta_path.write_text(json.dumps(meta))
    elif change == "scaler":
        np.savez(tmp_path / "model.scaler.npz", mean=np.zeros(DIMENSION), std=np.zeros(DIMENSION))
    elif change == "tensor":
        weights = torch.load(tmp_path / "model.pt", weights_only=True)
        weights.pop("classifier.weight")
        torch.save(weights, tmp_path / "model.pt")
    refresh_staged(tmp_path, m, signing_key)
    with pytest.raises(off.OfficialBundleError, match="ARTIFACT_INTEGRITY_FAILED"):
        off.OfficialBundleSource()._integrity_probe(tmp_path)


def test_unsupported_consumer_runtime(tmp_path, signing_key):
    m = stage_tensors(tmp_path, signing_key)
    m["consumer"]["python"] = ">=3.1,<3.2"
    refresh_staged(tmp_path, m, signing_key)
    with pytest.raises(off.OfficialBundleError, match="RUNTIME_UNSUPPORTED"):
        off.OfficialBundleSource()._integrity_probe(tmp_path)


@pytest.mark.parametrize("field", ["training", "feature_schema_hash", "consumer", "extension"])
def test_unsigned_tamper_rejected(signing_key, field):
    m = manifest_fixture(signing_key)
    m[field] = {"tampered": True}
    with pytest.raises(off.OfficialBundleError, match="SIGNATURE_INVALID"):
        off.verify_bundle_manifest(m)


def test_manifest_404_is_not_published(tmp_path, monkeypatch):
    source = off.OfficialBundleSource()

    def missing(url, dest):
        raise off.OfficialBundleError("HTTP_NOT_FOUND", "404")

    monkeypatch.setattr(source, "_fetch", missing)
    with pytest.raises(off.OfficialBundleError, match="OFFICIAL_MODEL_NOT_PUBLISHED"):
        source.download_and_verify(tmp_path)
    assert not list(tmp_path.iterdir())


def test_duplicate_json_keys_rejected(tmp_path):
    from nexus_scalp.model_provisioning.official_staging import read_json

    path = tmp_path / "manifest.json"
    path.write_text('{"schema":"first","schema":"second"}')
    with pytest.raises(off.OfficialBundleError, match="MANIFEST_MALFORMED"):
        read_json(path)


def test_transport_caps_body_and_removes_partial(tmp_path, monkeypatch):
    import io
    import urllib.request

    class Response(io.BytesIO):
        headers: dict[str, str]

        def __init__(self, data):
            super().__init__(data)
            self.headers = {}

        def geturl(self):
            return "https://example.test/model.pt"

    class Opener:
        def open(self, *args, **kwargs):
            return Response(b"12345")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Opener())
    dest = tmp_path / "model.pt"
    with pytest.raises(off.OfficialBundleError, match="DOWNLOAD_TOO_LARGE"):
        off._http_get("https://example.test/model.pt", dest, max_bytes=4)
    assert not dest.exists()


def test_behavioral_gate_rejects_other_random_seed(tmp_path, signing_key):
    stage_tensors(tmp_path, signing_key, seed=19)
    with pytest.raises(off.OfficialBundleError, match="behavioral gate"):
        off.OfficialBundleSource()._integrity_probe(tmp_path)


def test_actual_runtime_loader_accepts_test_trained_fixture(tmp_path, signing_key):
    """Tiny synthetic classifier is test-only; never an official release artifact."""
    import torch

    from nexus_scalp.models.scalp_net import ScalpNet

    m = stage_tensors(tmp_path, signing_key)
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(42)
        model = ScalpNet(**m["architecture_parameters"])
        optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
        generator = torch.Generator().manual_seed(901)
        x = torch.randn(96, DIMENSION, generator=generator)
        y = torch.where(x.mean(dim=1) > 0, 1, 2)
        for _ in range(24):
            optimizer.zero_grad()
            loss = torch.nn.functional.cross_entropy(model(x, return_logits=True), y)
            loss.backward()
            optimizer.step()
        torch.save(model.state_dict(), tmp_path / "model.pt")
    refresh_staged(tmp_path, m, signing_key)
    off.OfficialBundleSource()._integrity_probe(tmp_path)


def test_canonical_payload_covers_extensions_not_signature():
    manifest = {"schema": "nexus_model_bundle_v1", "training": {"seed": 17}, "signature": "ignored"}
    assert json.loads(off._canonical_payload(manifest)) == {
        "schema": "nexus_model_bundle_v1",
        "training": {"seed": 17},
    }
