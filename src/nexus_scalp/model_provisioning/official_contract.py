"""Revision 2 static contract. No torch import or artifact deserialization."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from packaging.specifiers import SpecifierSet
from packaging.version import Version

MAX_MANIFEST_BYTES = 256 * 1024
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
REQUIRED_FILES = frozenset({"model.pt", "model.scaler.npz", "model.meta.json"})
ALLOWED_FILES = REQUIRED_FILES | {"dataset.parquet"}
RELEASE_BASE = "https://github.com/Opselon/NexusTradingForexBot/releases/download"


def require(condition: bool, code: str, detail: str) -> None:
    if not condition:
        from nexus_scalp.model_provisioning.official import OfficialBundleError

        raise OfficialBundleError(code, detail)


def https_url(value: Any) -> str:
    require(
        isinstance(value, str) and bool(value), "URL_UNSUPPORTED", "explicit HTTPS URL required"
    )
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and not parsed.fragment
            and parsed.port in (None, 443)
            and not any(c.isspace() or ord(c) < 32 for c in value)
        )
    except ValueError:
        valid = False
    require(valid, "URL_UNSUPPORTED", "HTTPS URL without credentials/fragment required")
    return str(value)


def sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None


def architecture_parameters() -> dict[str, Any]:
    from nexus_scalp.features.schema_contract import DIMENSION
    from nexus_scalp.model_lifecycle.model_class_contract import TRAINED_CLASS_COUNT

    return dict(
        num_features=DIMENSION,
        num_classes=TRAINED_CLASS_COUNT,
        hidden_dim=128,
        num_heads=4,
        dropout_rate=0.25,
    )


def validate_contract(m: dict[str, Any]) -> None:
    from nexus_scalp.features.schema_contract import DIMENSION, SCHEMA_ID, feature_schema_hash
    from nexus_scalp.model_lifecycle.model_class_contract import TRAINED_CLASS_COUNT

    require(
        m.get("schema") == "nexus_model_bundle_v1"
        and type(m.get("contract_version")) is int
        and m["contract_version"] == 2,
        "SCHEMA_UNSUPPORTED",
        "contract revision 2 required",
    )
    # A trusted signature authenticates provenance; it cannot turn a known
    # development/recovery starter into a trained official model.
    from nexus_scalp.model_provisioning.service import _STARTER_MARKERS
    from nexus_scalp.release.model_bootstrap import PROVISIONER_MARKER

    note = str(m.get("note", "")).lower()
    require(
        m.get("provisioner") != PROVISIONER_MARKER
        and not any(marker.lower() in note for marker in _STARTER_MARKERS),
        "STARTER_MODEL_REJECTED",
        "development/recovery starter cannot be an official trained model",
    )
    expected = dict(
        architecture="ScalpNet",
        architecture_version="1.0.0",
        feature_schema_id=SCHEMA_ID,
        feature_schema_hash=feature_schema_hash(),
        dimension=DIMENSION,
        class_count=TRAINED_CLASS_COUNT,
        class_labels=["NO_TRADE", "BUY", "SELL"],
        symbol="XAUUSD",
        timeframe="M1",
        input_layout="batch_features",
        architecture_parameters=architecture_parameters(),
    )
    for field, value in expected.items():
        require(
            m.get(field) == value and type(m.get(field)) is type(value),
            "CONTRACT_MISMATCH",
            f"{field} incompatible with runtime contract",
        )
    require(
        "sequence_length" not in m, "CONTRACT_MISMATCH", "batch_features has no sequence length"
    )
    require(
        isinstance(m.get("bundle_id"), str) and bool(m["bundle_id"].strip()),
        "MANIFEST_MALFORMED",
        "bundle_id required",
    )
    version = m.get("model_version")
    require(
        isinstance(version, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is not None,
        "MANIFEST_MALFORMED",
        "model_version must be a stable major.minor.patch release",
    )
    files = m.get("files")
    require(
        isinstance(files, dict) and REQUIRED_FILES <= files.keys() <= ALLOWED_FILES,
        "MANIFEST_MALFORMED",
        "required flat artifact set missing or unknown filename",
    )
    total = 0
    for name, entry in files.items():
        require(isinstance(entry, dict), "MANIFEST_MALFORMED", f"{name}: invalid entry")
        require(sha256(entry.get("sha256")), "MANIFEST_MALFORMED", f"{name}: full SHA256 required")
        limit = MAX_MANIFEST_BYTES if name == "model.meta.json" else MAX_FILE_BYTES
        size = entry.get("size")
        require(
            type(size) is int and 0 < size <= limit, "MANIFEST_MALFORMED", f"{name}: invalid size"
        )
        total += size
        url = https_url(entry.get("url"))
        require(
            url == f"{RELEASE_BASE}/model-{version}/{name}",
            "URL_UNSUPPORTED",
            f"{name}: primary must identify immutable model release",
        )
        mirrors = entry.get("mirrors", [])
        require(
            isinstance(mirrors, list) and len(mirrors) <= 4,
            "MANIFEST_MALFORMED",
            f"{name}: at most four explicit mirrors",
        )
        for mirror in mirrors:
            https_url(mirror)
    require(total <= MAX_TOTAL_BYTES, "MANIFEST_MALFORMED", "bundle too large")
    for field, name in [
        ("model_sha256", "model.pt"),
        ("scaler_sha256", "model.scaler.npz"),
        ("metadata_sha256", "model.meta.json"),
    ]:
        require(
            sha256(m.get(field)) and m[field].lower() == files[name]["sha256"].lower(),
            "CONTRACT_MISMATCH",
            f"{field} does not bind files entry",
        )
    producer = m.get("producer")
    require(isinstance(producer, dict), "MANIFEST_MALFORMED", "producer required")
    require(
        isinstance(producer.get("git_commit"), str)
        and re.fullmatch(r"[0-9a-fA-F]{40}", producer["git_commit"]) is not None,
        "MANIFEST_MALFORMED",
        "producer git_commit required",
    )
    for name in ("python", "pytorch"):
        require(
            isinstance(producer.get(name), str), "MANIFEST_MALFORMED", f"producer {name} required"
        )
        Version(producer[name])
    training = m.get("training")
    require(
        isinstance(training, dict)
        and isinstance(training.get("command"), str)
        and bool(training["command"].strip())
        and type(training.get("seed")) is int,
        "MANIFEST_MALFORMED",
        "training command and seed required",
    )
    dataset = training.get("dataset")
    require(
        isinstance(dataset, dict)
        and isinstance(dataset.get("id"), str)
        and bool(dataset["id"].strip())
        and sha256(dataset.get("sha256")),
        "MANIFEST_MALFORMED",
        "training dataset identity required",
    )
    if "dataset.parquet" in files:
        require(
            dataset["sha256"].lower() == files["dataset.parquet"]["sha256"].lower(),
            "CONTRACT_MISMATCH",
            "dataset digest differs",
        )
    consumer = m.get("consumer")
    require(
        isinstance(consumer, dict), "MANIFEST_MALFORMED", "tested consumer constraints required"
    )
    for name in ("python", "pytorch"):
        require(
            isinstance(consumer.get(name), str) and bool(consumer[name]),
            "MANIFEST_MALFORMED",
            f"consumer {name} range required",
        )
        specs = SpecifierSet(consumer[name])
        exact = any(s.operator == "==" and "*" not in s.version for s in specs)
        lower = any(s.operator in (">=", ">") for s in specs)
        upper = any(s.operator in ("<=", "<") for s in specs)
        require(
            exact or (lower and upper),
            "MANIFEST_MALFORMED",
            f"consumer {name} range must be bounded",
        )
    platforms = consumer.get("platforms")
    supported = {"linux-x86_64", "linux-aarch64", "win32-amd64", "darwin-arm64", "darwin-x86_64"}
    require(
        isinstance(platforms, list)
        and bool(platforms)
        and all(isinstance(p, str) and p in supported for p in platforms),
        "MANIFEST_MALFORMED",
        "explicit tested consumer platforms required",
    )
