"""Signed update manifests (P0 trust root).

An update manifest is a small JSON document describing EXACTLY one release
payload (name, platform, architecture, size, SHA-256 digest, release
identity). It is signed with Ed25519 over a DETERMINISTIC canonical
serialization of its fields, so:

    * same manifest  -> same canonical bytes (bit-stable signing/verify);
    * the signature covers the payload digest (checksum is not the root);
    * any field mutation invalidates the signature (tamper-evident);
    * verification uses ONLY the embedded trusted public keys.

Wire format (the signed manifest document distributed next to the payload):

    {
      "schema": 1,
      "key_id": "2026-09-root",
      "version": "9.1.0",
      "platform": "windows",
      "architecture": "x64",
      "artifact_name": "NexusScalpEngine-9.1.0-win-x64.zip",
      "artifact_sha256": "<64 hex>",
      "artifact_size": 123456,
      "release_id": 42,
      "signature": "<128 hex Ed25519 over the canonical signing payload>"
    }

Canonicalization: the SIGNING PAYLOAD is the UTF-8 encoding of a
key-sorted, separator-free JSON object containing every field EXCEPT
``signature`` (floats/None are banned from signed fields to keep the
encoding deterministic). The detached signature covers those bytes only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Final

from nexus_scalp.release.signing.trusted_keys import (
    ACTIVE_TRUST_ROOT,
    TRUST_ROOT_SCHEMA_VERSION,
    trusted_public_key,
)

MANIFEST_SCHEMA_VERSION: Final[int] = 1

#: Fields that MUST be present in a manifest (signature covers all of these).
REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "version",
    "platform",
    "architecture",
    "artifact_name",
    "artifact_sha256",
    "artifact_size",
)

_HEX64 = set("0123456789abcdef")
_HEX128 = _HEX64


class UpdateManifestError(RuntimeError):
    """Base class for update-manifest failures."""


class UpdateManifestRejectedError(UpdateManifestError):
    """A manifest was REJECTED (malformed / untrusted signature / mismatch).

    ``reason`` is a stable machine-readable code:
        MANIFEST_MALFORMED, MISSING_SIGNATURE, UNKNOWN_KEY,
        SIGNATURE_INVALID, DIGEST_MISMATCH, SIZE_MISMATCH.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def _require_str(manifest: dict[str, Any], field: str) -> str:
    value = manifest.get(field)
    if not isinstance(value, str) or not value:
        raise UpdateManifestRejectedError(
            "MANIFEST_MALFORMED", f"field {field} must be a non-empty string"
        )
    return value


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """Deterministic canonical bytes of the UNSIGNED manifest fields.

    Sorted keys, compact separators, UTF-8, ensure_ascii. None values and
    nested non-string-key dicts are rejected so the encoding is unambiguous.
    """
    fields: dict[str, Any] = {k: v for k, v in manifest.items() if k != "signature"}
    for k, v in fields.items():
        if v is None:
            raise UpdateManifestRejectedError("MANIFEST_MALFORMED", f"field {k} must not be null")
    try:
        text = json.dumps(
            fields,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as e:
        raise UpdateManifestRejectedError(
            "MANIFEST_MALFORMED", f"non-canonicalizable manifest: {e}"
        ) from e
    return text.encode("utf-8")


def manifest_signing_payload(manifest: dict[str, Any]) -> bytes:
    """The exact bytes the Ed25519 signature must cover.

    Binds the trust-root schema version + the canonical manifest so a
    signature can never be replayed across schema changes.
    """
    return (
        b"nse-update-manifest\x00"
        + str(TRUST_ROOT_SCHEMA_VERSION).encode()
        + b"\x00"
        + canonical_manifest_bytes(manifest)
    )


def build_manifest(
    *,
    version: str,
    platform: str,
    architecture: str,
    artifact_name: str,
    artifact_sha256: str,
    artifact_size: int,
    release_id: int | None = None,
    key_id: str = ACTIVE_TRUST_ROOT,
) -> dict[str, Any]:
    """Builds a manifest dict for one payload (unsigned; use sign_manifest)."""
    digest = str(artifact_sha256).lower()
    if len(digest) != 64 or not set(digest) <= _HEX64:
        raise UpdateManifestRejectedError(
            "MANIFEST_MALFORMED", "artifact_sha256 must be 64 hex chars"
        )
    if int(artifact_size) < 0:
        raise UpdateManifestRejectedError("MANIFEST_MALFORMED", "artifact_size must be >= 0")
    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA_VERSION,
        "key_id": key_id,
        "version": str(version),
        "platform": str(platform),
        "architecture": str(architecture),
        "artifact_name": str(artifact_name),
        "artifact_sha256": digest,
        "artifact_size": int(artifact_size),
    }
    if release_id is not None:
        manifest["release_id"] = int(release_id)
    return manifest


def sign_manifest(manifest: dict[str, Any], private_key_hex: str) -> dict[str, Any]:
    """Signs the canonical manifest fields with an Ed25519 private seed.

    ``private_key_hex`` is the 32-byte signing seed (64 hex chars). Returns
    the manifest WITH the ``signature`` field attached. The private key is
    never logged and never written anywhere by this function.
    """
    import nacl.encoding
    import nacl.signing

    seed = bytes.fromhex(private_key_hex.strip())
    if len(seed) != 32:
        raise UpdateManifestError("private key must be a 32-byte Ed25519 seed (64 hex chars)")
    signing_key = nacl.signing.SigningKey(seed)
    payload = manifest_signing_payload(manifest)
    sig = signing_key.sign(payload).signature.hex()
    out = dict(manifest)
    out["signature"] = sig
    # signed/verify round-trip check against the embedded PUBLIC key map so a
    # release pipeline cannot publish a manifest its own client would reject.
    verify_manifest_signature(out)
    return out


def verify_manifest_signature(manifest: dict[str, Any]) -> dict[str, Any]:
    """Verifies a signed manifest against the EMBEDDED trusted keys.

    Fail-closed classification (never a silent fallback):
        MANIFEST_MALFORMED   schema/fields wrong shape
        MISSING_SIGNATURE    no signature field
        UNKNOWN_KEY          key_id not in the embedded trust root
        SIGNATURE_INVALID    signature does not verify over the payload
    Returns a small verdict dict; raises UpdateManifestRejected otherwise.
    """
    if not isinstance(manifest, dict):
        raise UpdateManifestRejectedError("MANIFEST_MALFORMED", "manifest must be a JSON object")
    try:
        schema = int(manifest.get("schema", -1))
    except (TypeError, ValueError):
        raise UpdateManifestRejectedError(
            "MANIFEST_MALFORMED", "schema must be an integer"
        ) from None
    if schema != MANIFEST_SCHEMA_VERSION:
        raise UpdateManifestRejectedError(
            "MANIFEST_MALFORMED", f"unsupported manifest schema {schema}"
        )
    for field in ("version", "platform", "architecture", "artifact_name"):
        _require_str(manifest, field)
    digest = manifest.get("artifact_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or not set(digest.lower()) <= _HEX64:
        raise UpdateManifestRejectedError(
            "MANIFEST_MALFORMED", "artifact_sha256 must be 64 hex chars"
        )
    size = manifest.get("artifact_size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise UpdateManifestRejectedError(
            "MANIFEST_MALFORMED", "artifact_size must be a non-negative integer"
        )
    key_id = _require_str(manifest, "key_id")
    signature = manifest.get("signature")
    if not isinstance(signature, str) or not signature:
        raise UpdateManifestRejectedError("MISSING_SIGNATURE", "no signature field")
    if len(signature) != 128 or not set(signature.lower()) <= _HEX128:
        raise UpdateManifestRejectedError("SIGNATURE_INVALID", "signature is not 64-byte hex")
    public_hex = trusted_public_key(key_id)
    if public_hex is None:
        raise UpdateManifestRejectedError(
            "UNKNOWN_KEY", f"key_id {key_id!r} is not in the embedded trust root"
        )
    import nacl.signing

    verify_key = nacl.signing.VerifyKey(bytes.fromhex(public_hex))
    payload = manifest_signing_payload(manifest)
    try:
        verify_key.verify(payload, bytes.fromhex(signature))
    except Exception as err:
        raise UpdateManifestRejectedError(
            "SIGNATURE_INVALID", f"Ed25519 verify failed: {type(err).__name__}"
        ) from err
    return {
        "verified": True,
        "key_id": key_id,
        "covered_digest": str(manifest["artifact_sha256"]).lower(),
    }


def verify_payload_against_manifest(
    manifest: dict[str, Any],
    payload_path: Path | str | None,
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Signature -> payload binding: signature is verified FIRST, then the
    on-disk payload is hashed and compared against the SIGNED digest (and
    against ``expected_sha256`` from the discovery layer when provided —
    both must agree; disagreement fails safe).

    Returns the verdict dict with the verified digest. Raises
    UpdateManifestRejected on ANY failure; never activates a payload whose
    bytes were not covered by a valid trusted signature.
    """
    verdict = verify_manifest_signature(manifest)
    signed_digest = str(verdict["covered_digest"])
    if expected_sha256 is not None and str(expected_sha256).lower() != signed_digest:
        raise UpdateManifestRejectedError(
            "DIGEST_MISMATCH",
            "discovery digest != signed manifest digest (fail safe)",
        )
    if payload_path is None:
        # METADATA-ONLY verification (plan/discovery stage): the signature
        # covers the digest; the payload itself is bound at download time.
        return verdict
    import hashlib

    h = hashlib.sha256()
    size = 0
    with open(payload_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    declared_size = manifest.get("artifact_size")
    if isinstance(declared_size, int) and declared_size >= 0 and size != declared_size:
        raise UpdateManifestRejectedError(
            "SIZE_MISMATCH", f"payload size {size} != manifest {declared_size}"
        )
    actual = h.hexdigest()
    if actual != signed_digest:
        raise UpdateManifestRejectedError(
            "DIGEST_MISMATCH",
            f"payload sha256 {actual[:12]} != signed digest {signed_digest[:12]}",
        )
    return {**verdict, "payload_sha256": actual, "payload_size": size}


def sign_manifest_file(manifest_path: Path | str, private_key_hex: str) -> Path:
    """Signs a manifest JSON file in place (release-pipeline helper)."""
    p = Path(manifest_path)
    manifest = json.loads(p.read_text(encoding="utf-8"))
    signed = sign_manifest(manifest, private_key_hex)
    p.write_text(json.dumps(signed, indent=2), encoding="utf-8")
    return p


def _dev_only_key_from_env() -> str | None:
    """TEST-ONLY signing key source. Production pipelines must pass the key
    explicitly from a secret; this helper exists so tests can exercise the
    real signer without ever touching repository or runtime state. It reads
    ONLY the process environment of the calling process."""
    return os.environ.get("NSE_TEST_ONLY_SIGNING_KEY")
