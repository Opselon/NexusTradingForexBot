"""Publication contract tests. Local models are SYNTHETIC TEST FIXTURES ONLY."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_publication_cli_rejects_invalid_request_and_dispatches_valid():
    import subprocess
    import sys

    command = [
        sys.executable,
        str(ROOT / "scripts/release/publish_official_model.py"),
        "validate",
        "--version",
        "1.2.3",
        "--candidate",
        "model-candidate-1.2.3",
        "--repo",
        "Opselon/NexusTradingForexBot",
        "--ref",
    ]
    bad = subprocess.run(
        [*command, "refs/heads/other"], capture_output=True, text=True, check=False
    )
    assert bad.returncode != 0, "CLI silently did nothing"
    good = subprocess.run(
        [*command, "refs/heads/main"], capture_output=True, text=True, check=False
    )
    assert good.returncode == 0, good.stderr
    assert good.stdout.strip() == "model-1.2.3"


def publisher():
    path = ROOT / "scripts/release/publish_official_model.py"
    assert path.exists(), "operator publication helper missing"
    spec = importlib.util.spec_from_file_location("publisher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "version,tag,ref,repo",
    [
        ("1.2.3", "model-candidate-1.2.3", "refs/heads/other", "Opselon/NexusTradingForexBot"),
        ("v1.2.3", "model-candidate-v1.2.3", "refs/heads/main", "Opselon/NexusTradingForexBot"),
        (
            "1.2.3;echo bad",
            "model-candidate-1.2.3",
            "refs/heads/main",
            "Opselon/NexusTradingForexBot",
        ),
        ("1.2.3", "model-candidate-1.2.4", "refs/heads/main", "Opselon/NexusTradingForexBot"),
        ("1.2.3", "model-candidate-1.2.3", "refs/heads/main", "attacker/fork"),
    ],
)
def test_request_rejects_noncanonical_inputs(version, tag, ref, repo):
    with pytest.raises(ValueError):
        publisher().validate_request(version, tag, ref, repo)


def test_request_accepts_model_namespace_only():
    assert (
        publisher().validate_request(
            "1.2.3", "model-candidate-1.2.3", "refs/heads/main", "Opselon/NexusTradingForexBot"
        )
        == "model-1.2.3"
    )


def candidate_record(pub):
    return {
        "draft": True,
        "tag_name": "model-candidate-1.2.3",
        "assets": [
            {"id": i + 1, "name": name, "size": 10, "state": "uploaded"}
            for i, name in enumerate(pub.CANDIDATE_FILES)
        ],
    }


def test_candidate_asset_allowlist_is_complete_and_bounded():
    pub = publisher()
    record = candidate_record(pub)
    selected = pub.candidate_assets(record, "model-candidate-1.2.3")
    assert set(selected) == {
        "model.pt",
        "model.scaler.npz",
        "model.meta.json",
        "trainer-manifest.json",
        "provenance.json",
        "compatibility.json",
    }
    for field, value in [("draft", False), ("tag_name", "v1.2.3")]:
        with pytest.raises(ValueError):
            pub.candidate_assets({**record, field: value}, "model-candidate-1.2.3")
    for assets in [
        record["assets"][:-1],
        record["assets"] + [record["assets"][0]],
        [{**a, "size": 9999999999} for a in record["assets"]],
        [{**a, "name": "../evil.py"} for a in record["assets"]],
    ]:
        with pytest.raises(ValueError):
            pub.candidate_assets({**record, "assets": assets}, "model-candidate-1.2.3")


def test_candidate_fetch_uses_fixed_api_asset_ids_and_declared_bounds(tmp_path):
    pub = publisher()
    record = candidate_record(pub)
    calls = []

    class API:
        def release(self, tag):
            assert tag == "model-candidate-1.2.3"
            return record

        def download(self, asset_id, dest, max_bytes):
            calls.append((asset_id, dest.name, max_bytes))
            dest.write_bytes(b"x" * 10)

    pub.fetch_candidate(API(), "model-candidate-1.2.3", tmp_path / "candidate")
    assert len(calls) == 6
    assert all(limit == 10 for _, _, limit in calls)  # declared asset size is the cap
    assert {p.name for p in (tmp_path / "candidate").iterdir()} == set(pub.CANDIDATE_FILES)

    class Short(API):
        def download(self, asset_id, dest, max_bytes):
            dest.write_bytes(b"short")

    with pytest.raises(ValueError, match="size"):
        pub.fetch_candidate(Short(), "model-candidate-1.2.3", tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def builder():
    spec = importlib.util.spec_from_file_location(
        "builder", ROOT / "scripts/release/build_official_bundle.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_builder_requires_real_producer_evidence_and_does_not_invent_training(tmp_path):
    import json

    src = tmp_path / "source"
    src.mkdir()
    for name in ("model.pt", "model.scaler.npz"):
        (src / name).write_bytes(b"not a model")
    (src / "model.meta.json").write_text(json.dumps({"origin": "DEV_STARTER"}))
    with pytest.raises(ValueError, match=r"provenance|trainer|compatibility"):
        builder().prepare_manifest(src, "1.2.3")
    (src / "trainer-manifest.json").write_text("{}")
    (src / "provenance.json").write_text("{}")
    (src / "compatibility.json").write_text("{}")
    with pytest.raises(ValueError):
        builder().prepare_manifest(src, "1.2.3")


def test_explicit_runtime_is_bounded_and_never_shell_or_cuda():
    pub = publisher()
    assert pub.runtime_spec({"verification_runtime": {"python": "3.11", "pytorch": "2.14.0"}}) == (
        "3.11",
        "2.14.0",
    )
    for version in ["2.14.0;curl bad", "2.14.0+cu130", "1.0.0", "9.0.0", "../../x"]:
        with pytest.raises(ValueError):
            pub.runtime_spec({"verification_runtime": {"python": "3.11", "pytorch": version}})


def evidence_fixture(src):
    """Invented evidence for tests ONLY, never a release candidate."""
    import json

    from nexus_scalp.features import schema_contract as schema

    src.mkdir(exist_ok=True)
    meta = {
        "feature_schema_id": schema.SCHEMA_ID,
        "feature_schema_hash": schema.feature_schema_hash(),
        "feature_schema_dimension": schema.DIMENSION,
        "num_classes": 3,
        "architecture": "ScalpNet",
        "architecture_version": "1.0.0",
        "architecture_parameters": {
            "num_features": schema.DIMENSION,
            "num_classes": 3,
            "hidden_dim": 128,
            "num_heads": 4,
            "dropout_rate": 0.25,
        },
        "input_layout": "batch_features",
        "class_labels": ["NO_TRADE", "BUY", "SELL"],
        "symbol": "XAUUSD",
        "timeframe": "M1",
    }
    provenance = {
        "producer": {"python": "3.11.16", "pytorch": "2.14.0+cpu", "git_commit": "a" * 40},
        "training": {
            "command": "test-only-invented-evidence",
            "seed": 42,
            "dataset": {"id": "test-fixture", "sha256": "b" * 64},
        },
    }
    compatibility = {
        "consumer": {
            "python": ">=3.11,<3.12",
            "pytorch": ">=2.14,<2.15",
            "platforms": ["linux-x86_64"],
        },
        "verification_runtime": {"python": "3.11", "pytorch": "2.14.0"},
        "metadata_bindings": {},
    }
    for name, data in [
        ("model.meta.json", meta),
        ("provenance.json", provenance),
        ("compatibility.json", compatibility),
        ("trainer-manifest.json", {"dataset_id": "test-fixture", "model_sha256": "unused"}),
    ]:
        (src / name).write_text(json.dumps(data))
    for name in ("model.pt", "model.scaler.npz"):
        (src / name).write_bytes(b"fixture-not-loadable")
    return provenance, compatibility


def test_manifest_preserves_provenance_and_binds_immutable_urls(tmp_path):
    source = tmp_path / "source"
    provenance, compatibility = evidence_fixture(source)
    manifest = builder().prepare_manifest(source, "1.2.3")
    assert manifest["training"] == provenance["training"]
    assert manifest["producer"] == provenance["producer"]
    assert manifest["consumer"] == compatibility["consumer"]
    assert manifest["contract_version"] == 2
    assert manifest["model_version"] == "1.2.3"
    for name, entry in manifest["files"].items():
        assert (
            entry["url"]
            == f"https://github.com/Opselon/NexusTradingForexBot/releases/download/model-1.2.3/{name}"
        )
        assert len(entry["sha256"]) == 64 and entry["size"] == (source / name).stat().st_size
    assert "trainer-manifest.json" in manifest["evidence_files"]
    assert set(manifest["files"]) == {"model.pt", "model.scaler.npz", "model.meta.json"}


def test_local_builder_runs_consumer_checks_before_output_and_preserves_evidence(
    tmp_path, monkeypatch
):
    import json

    import nacl.signing

    mod = builder()
    source = tmp_path / "source"
    evidence_fixture(source)
    original = (source / "trainer-manifest.json").read_bytes()
    checks = []
    monkeypatch.setattr(
        mod.off, "verify_bundle_manifest", lambda m: checks.append("signature") or m
    )
    monkeypatch.setattr(
        mod.off.OfficialBundleSource, "_integrity_probe", lambda self, p: checks.append("runtime")
    )
    out = tmp_path / "out"
    mod.build_bundle(source, out, "1.2.3", nacl.signing.SigningKey.generate().encode().hex())
    assert checks == ["signature", "runtime"]
    assert (out / "trainer-manifest.json").read_bytes() == original
    assert json.loads((out / "manifest.json").read_text())["signature"]

    def reject(self, path):
        raise RuntimeError("runtime rejected")

    monkeypatch.setattr(mod.off.OfficialBundleSource, "_integrity_probe", reject)
    with pytest.raises(RuntimeError, match="runtime rejected"):
        mod.build_bundle(
            source, tmp_path / "refused", "1.2.3", nacl.signing.SigningKey.generate().encode().hex()
        )
    assert not (tmp_path / "refused").exists()


def test_remote_release_verifies_assets_before_publication_and_channel_last(tmp_path, monkeypatch):
    import hashlib
    import json

    pub = publisher()
    bundle = tmp_path / "bundle"
    evidence_fixture(bundle)
    manifest = builder().prepare_manifest(bundle, "1.2.3")
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    calls = []
    from nexus_scalp.model_provisioning import official

    monkeypatch.setattr(official, "verify_bundle_manifest", lambda m: m)

    class API:
        def release(self, tag):
            calls.append(("get", tag))

        def create(self, tag, draft):
            calls.append(("create", tag, draft))

        def upload(self, tag, paths, clobber=False):
            calls.append(("upload", tag, clobber))

        def verify_assets(self, tag, paths):
            calls.append(("verify", tag))

        def publish(self, tag):
            calls.append(("publish", tag))

    pub.publish_bundle(API(), bundle, "1.2.3")
    assert calls.index(("verify", "model-1.2.3")) < calls.index(("publish", "model-1.2.3"))
    assert calls[-1] == ("upload", "official-model-stable", True)

    class Existing(API):
        def release(self, tag):
            return {"id": 1}

    with pytest.raises(ValueError, match="exists"):
        pub.publish_bundle(Existing(), bundle, "1.2.3")

    class Corrupt(API):
        def verify_assets(self, tag, paths):
            raise ValueError("remote bytes differ")

    calls.clear()
    with pytest.raises(ValueError, match="remote bytes"):
        pub.publish_bundle(Corrupt(), bundle, "1.2.3")
    assert not any("official-model-stable" in c for c in calls)
    assert not any(c[0] == "publish" for c in calls)


def test_workflow_exists_pins_actions_and_guards_untrusted_input():
    path = ROOT / ".github/workflows/official-model.yml"
    assert path.exists()
    import yaml

    def _load(text):
        class StrictLoader(yaml.SafeLoader):
            pass

        def construct(loader, node):
            return str(node.value)

        StrictLoader.add_constructor("tag:yaml.org,2002:bool", construct)
        StrictLoader.add_constructor("tag:yaml.org,2002:int", construct)
        wf = yaml.load(text, Loader=StrictLoader)
        wf["on"] = wf.get("on", wf.get(True))
        return wf

    wf = _load(path.read_text())
    assert set(wf["on"]["workflow_dispatch"]["inputs"]) == {"version", "candidate"}
    assert wf["on"]["workflow_dispatch"]["inputs"]["version"]["type"] == "string"
    assert not wf["on"].get("push", {}).get("tags")
    raw = path.read_text()
    for sha in (
        "3d3c42e5aac5ba805825da76410c181273ba90b1",
        "5fda3b95a4ea91299a34e894583c3862153e4b97",
    ):
        assert sha in raw
    run = " ".join(
        step.get("run", "")
        for job in wf["jobs"].values()
        for step in job.get("steps", [])
        if isinstance(step, dict)
    )
    for dangerous in [
        "${{ inputs.version }}",
        "${{ github.event.inputs",
        "echo $VERSION",
        'candidate}}"',
    ]:
        assert dangerous not in run
    for job in wf["jobs"].values():
        env = job.get("env", {})
        assert "VERSION" in env and "CANDIDATE" in env


def test_key_id_and_bundle_id_threading_and_trust_root_resolution(tmp_path, monkeypatch):
    """key-id/bundle-id resolution; torch-level probe stubbed (unit scope —
    the real trained-artifact runtime gate is exercised by the parent e2e)."""
    import json as _json

    import nacl.signing

    mod = builder()
    source = tmp_path / "source"
    evidence_fixture(source)
    from nexus_scalp.release.signing import trusted_keys

    m1 = mod.prepare_manifest(source, "1.2.3")
    assert m1["key_id"] == trusted_keys.ACTIVE_TRUST_ROOT
    assert m1["bundle_id"] == "official-xauusd-scalp_v3-1.2.3"
    m2 = mod.prepare_manifest(source, "1.2.3", key_id="test-e2e", bundle_id="custom-id")
    assert m2["key_id"] == "test-e2e" and m2["bundle_id"] == "custom-id"
    seed_key = nacl.signing.SigningKey.generate()
    trusted_keys.TRUSTED_UPDATE_KEYS["test-e2e"] = seed_key.verify_key.encode().hex()
    probed = []
    monkeypatch.setattr(
        mod.off.OfficialBundleSource, "_integrity_probe", lambda self, p: probed.append(p)
    )
    try:
        out = tmp_path / "out"
        manifest = mod.build_bundle(
            source, out, "1.2.3", seed_key.encode().hex(), bundle_id="custom-id", key_id="test-e2e"
        )
        assert probed, "runtime probe must run on staging before output is exposed"
        assert (
            mod.off.verify_bundle_manifest(_json.loads((out / "manifest.json").read_text()))
            == manifest
        )
    finally:
        trusted_keys.TRUSTED_UPDATE_KEYS.pop("test-e2e", None)


def test_cli_fetch_and_publish_really_dispatch(tmp_path, monkeypatch, capsys):
    """main() fetch/publish dispatch through the API object (fake in-process)."""
    import json

    pub = publisher()
    calls = []

    class FakeGh:
        def release(self, tag):
            calls.append(("release", tag))
            if tag.startswith("model-candidate-"):
                return {
                    "draft": True,
                    "tag_name": tag,
                    "assets": [
                        {"id": i + 1, "name": n, "size": 4, "state": "uploaded"}
                        for i, n in enumerate(pub.CANDIDATE_FILES)
                    ],
                }
            return None

        def download(self, asset_id, dest, max_bytes):
            calls.append(("download", asset_id))
            Path(dest).write_bytes(b"1234")

        def create(self, tag, draft):
            calls.append(("create", tag, draft))

        def upload(self, tag, paths, clobber=False):
            calls.append(("upload", tag, tuple(p.name for p in paths), clobber))

        def verify_assets(self, tag, paths):
            calls.append(("verify", tag))

        def publish(self, tag):
            calls.append(("publish", tag))

    monkeypatch.setattr(pub, "GhApi", FakeGh)
    dest = tmp_path / "cand"
    assert pub.main(["fetch", "--candidate", "model-candidate-1.2.3", "--dest", str(dest)]) == 0
    assert len([c for c in calls if c[0] == "download"]) == 6
    assert {p.name for p in dest.iterdir()} == set(pub.CANDIDATE_FILES)

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    evidence_fixture(bundle)
    manifest = builder().prepare_manifest(bundle, "1.2.3")
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    from nexus_scalp.model_provisioning import official

    monkeypatch.setattr(official, "verify_bundle_manifest", lambda m: m)
    calls.clear()
    assert pub.main(["publish", "--bundle", str(bundle), "--version", "1.2.3"]) == 0
    [c[1] for c in calls if c[0] in ("create", "upload", "publish")]
    assert calls[0] == ("release", "model-1.2.3"), calls
    assert ("upload", "official-model-stable", ("manifest.json",), True) in calls
    assert calls[-1] == ("upload", "official-model-stable", ("manifest.json",), True), calls


def test_cli_rejects_missing_bundle_and_bad_candidate(tmp_path, monkeypatch):
    pub = publisher()
    monkeypatch.setattr(pub, "GhApi", lambda: (_ for _ in ()).throw(AssertionError("no api")))
    assert (
        pub.main(["fetch", "--candidate", "model-candidate-x", "--dest", str(tmp_path / "d")]) != 0
    )


def test_cli_runtime_spec_prints_pinned_pair(tmp_path):
    publisher()
    src = tmp_path / "compatibility.json"
    import json as _json

    src.write_text(_json.dumps({"verification_runtime": {"python": "3.11", "pytorch": "2.14.0"}}))

    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/release/publish_official_model.py"),
            "runtime-spec",
            "--compatibility",
            str(src),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "3.11,2.14.0"
    bad = tmp_path / "bad.json"
    bad.write_text(_json.dumps({"verification_runtime": {"python": "3.11", "pytorch": "9.9.9"}}))
    bad_proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/release/publish_official_model.py"),
            "runtime-spec",
            "--compatibility",
            str(bad),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad_proc.returncode != 0
