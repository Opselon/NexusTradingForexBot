#!/usr/bin/env python3
"""Build a signed nexus_model_bundle_v1 revision-2 release bundle locally.

Requires six candidate assets documented in docs/OFFICIAL_MODEL_PUBLICATION.md.
Training/producer evidence is supplied truthfully by the owner, never generated
by the publisher. NSE_UPDATE_SIGNING_KEY stays in the process environment.
All consumer signature/schema/scaler/runtime/health checks must pass.
No remote write occurs in this builder; use the main-only Actions workflow.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from nexus_scalp.model_provisioning import official as off  # noqa: E402
from nexus_scalp.release import bootstrap as rb  # noqa: E402


def _sha(path: Path) -> str:
    return rb.sha256_file(path)


def prepare_manifest(src: Path, version: str, bundle_id: str = "", key_id: str = "") -> dict:
    for name in ("trainer-manifest.json", "provenance.json", "compatibility.json"):
        if not (src / name).is_file():
            raise ValueError(f"missing {name}; true producer evidence is required")
    provenance = json.loads((src / "provenance.json").read_text())
    if not provenance.get("producer") or not provenance.get("training"):
        raise ValueError("provenance requires true producer and training evidence")
    from nexus_scalp.features import schema_contract as schema
    from nexus_scalp.release.signing import trusted_keys

    key_id = key_id or trusted_keys.ACTIVE_TRUST_ROOT

    compatibility = json.loads((src / "compatibility.json").read_text())
    meta = json.loads((src / "model.meta.json").read_text())
    names = (
        "model.pt",
        "model.scaler.npz",
        "model.meta.json",
        "trainer-manifest.json",
        "provenance.json",
        "compatibility.json",
    )
    files = {
        name: {
            "sha256": _sha(src / name),
            "size": (src / name).stat().st_size,
            "url": f"https://github.com/Opselon/NexusTradingForexBot/releases/download/model-{version}/{name}",
            "mirrors": [],
        }
        for name in names
    }
    manifest = {
        "schema": off.BUNDLE_MANIFEST_SCHEMA,
        "contract_version": 2,
        "bundle_id": bundle_id or f"official-xauusd-scalp_v3-{version}",
        "model_version": version,
        "feature_schema_id": schema.SCHEMA_ID,
        "feature_schema_hash": schema.feature_schema_hash(),
        "dimension": schema.DIMENSION,
        "class_count": 3,
        "producer": provenance["producer"],
        "training": provenance["training"],
        "consumer": compatibility["consumer"],
        "files": {k: v for k, v in files.items() if k in names[:3]},
        "evidence_files": {k: v for k, v in files.items() if k in names[3:]},
        "key_id": key_id,
        "signature": "",
        "model_sha256": files["model.pt"]["sha256"],
        "scaler_sha256": files["model.scaler.npz"]["sha256"],
        "metadata_sha256": files["model.meta.json"]["sha256"],
    }
    for key in (
        "architecture",
        "architecture_version",
        "architecture_parameters",
        "input_layout",
        "class_labels",
        "symbol",
        "timeframe",
    ):
        manifest[key] = meta[key]
    return manifest


def build_bundle(
    src: Path, out: Path, version: str, seed: str, bundle_id: str = "", key_id: str = ""
) -> dict:
    """Build locally, fail closed before exposing output; never publish remotely."""
    import tempfile

    import nacl.signing

    if out.exists():
        raise ValueError("output already exists; refusing overwrite")
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".official-build-", dir=out.parent))
    try:
        for name in (
            "model.pt",
            "model.scaler.npz",
            "model.meta.json",
            "trainer-manifest.json",
            "provenance.json",
            "compatibility.json",
        ):
            source = src / name
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"missing/unsafe {name}")
            shutil.copyfile(source, staging / name)
        manifest = prepare_manifest(staging, version, bundle_id=bundle_id, key_id=key_id)
        manifest["signature"] = (
            nacl.signing.SigningKey(bytes.fromhex(seed))
            .sign(off._canonical_payload(manifest))
            .signature.hex()
        )
        off.verify_bundle_manifest(manifest)
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8"
        )
        off.OfficialBundleSource()._integrity_probe(staging)
        os.replace(staging, out)
        return manifest
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build and verify a signed official bundle locally; does not publish."
    )
    ap.add_argument("--bundle-dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--model-version", required=True)
    ap.add_argument(
        "--bundle-id",
        default="",
        help="official bundle id (default: official-xauusd-scalp_v3-<version>)",
    )
    ap.add_argument("--key-id", default="", help="signing key_id (default: ACTIVE_TRUST_ROOT)")
    args = ap.parse_args()
    seed = os.environ.get("NSE_UPDATE_SIGNING_KEY", "").strip()
    if not args.bundle_dir.is_dir() or not seed:
        print("usage error: bundle material or signing key missing", file=sys.stderr)
        return 2
    try:
        build_bundle(
            args.bundle_dir,
            args.out,
            args.model_version,
            seed,
            bundle_id=args.bundle_id,
            key_id=args.key_id,
        )
    except Exception as exc:
        print(f"REFUSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"Verified local bundle: {args.out}. No remote publication performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
