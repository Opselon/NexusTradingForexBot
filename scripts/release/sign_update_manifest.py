"""Release-pipeline signing helper: sign the release update manifest.

Runs INSIDE the GitHub Actions release job AFTER the payload artifacts are
final (post Checksums step). The private key arrives ONLY via the
``NSE_UPDATE_SIGNING_KEY`` environment variable (GitHub Actions secret,
32-byte Ed25519 seed hex). The key is never printed, never logged, never
written to the repo or the artifact.

Output: ``manifests/update-manifest.signed.json`` next to the release
manifest — published as a release asset and verified by every updater
before a payload may activate.

Determinism: signing covers the canonical (key-sorted, compact) JSON of the
manifest fields, so the same manifest always produces the same signature
bit-for-bit.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from nexus_scalp.release.signing import (
    ACTIVE_TRUST_ROOT,
    MANIFEST_SCHEMA_VERSION,
    UpdateManifestError,
    sign_manifest,
)


def _fail(msg: str) -> None:
    print(f"::error::{msg}", file=sys.stderr)
    raise SystemExit(2)


def main(argv: list[str]) -> int:
    out_dir = Path(argv[1]) if len(argv) > 1 else Path("release")
    payload = Path(argv[2]) if len(argv) > 2 else None
    version = os.environ.get("NSE_VERSION", "")
    release_id = os.environ.get("NSE_RELEASE_ID", "")
    key_hex = os.environ.get("NSE_UPDATE_SIGNING_KEY", "").strip()
    if not key_hex:
        _fail(
            "NSE_UPDATE_SIGNING_KEY is not set — a release cannot publish an "
            "UNSIGNED update manifest. Configure the repository secret (32-byte "
            "Ed25519 seed hex) and re-run."
        )
    if not version:
        _fail("NSE_VERSION is not set — refusing to sign an unidentifiable payload")
    if payload is None or not payload.is_file():
        _fail(f"payload artifact missing: {payload}")

    data = payload.read_bytes()
    manifest = {
        "schema": MANIFEST_SCHEMA_VERSION,
        "key_id": ACTIVE_TRUST_ROOT,
        "version": version.lstrip("v"),
        "platform": "windows",
        "architecture": "x64",
        "artifact_name": payload.name,
        "artifact_sha256": hashlib.sha256(data).hexdigest(),
        "artifact_size": len(data),
    }
    if release_id:
        with_release = dict(manifest)
        with_release["release_id"] = int(release_id)
        manifest = with_release
    try:
        signed = sign_manifest(manifest, key_hex)
    except UpdateManifestError as e:
        _fail(f"signing failed: {e}")

    out = out_dir / "manifests" / "update-manifest.signed.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(signed, indent=2), encoding="utf-8")
    # PROOF the key never leaks: log only derived public info.
    print(
        f"UPDATE_MANIFEST_SIGNED artifact={payload.name} sha256={manifest['artifact_sha256'][:12]}... key_id={manifest['key_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
