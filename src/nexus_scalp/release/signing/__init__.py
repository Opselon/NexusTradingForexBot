"""UPDATE SIGNING TRUST ROOT (P0 mission: signed update manifests).

Trust chain (P0):

    BUILD -> canonical manifest -> Ed25519 signature -> DISTRIBUTE
          -> VERIFY (embedded public key) -> payload SHA-256 (signed) -> ACTIVATE

The signature is the TRUST ROOT. The SHA-256 payload digest is integrity
evidence whose authority comes from being COVERED BY the signature. A
publisher account compromise that replaces payload + checksum alone can
never bypass verification (no valid private-key signature = REJECT).

Keys:
    * The PRIVATE signing key NEVER lives in the repository. It is provided
      to the release pipeline through the ``NSE_UPDATE_SIGNING_KEY`` secret
      (32-byte Ed25519 seed, hex-encoded).
    * The PUBLIC verification key(s) are versioned here in
      ``trusted_keys.py``, embedded in every client, and testable.

Crypto provider: PyNaCl (libsodium) — a standard, audited Ed25519
implementation. No custom cryptography is implemented here.
"""

from __future__ import annotations

from nexus_scalp.release.signing.trusted_keys import (
    ACTIVE_TRUST_ROOT,
    TRUST_ROOT_SCHEMA_VERSION,
    trusted_public_key,
    trusted_public_keys,
)
from nexus_scalp.release.signing.update_manifest import (
    MANIFEST_SCHEMA_VERSION,
    UpdateManifestError,
    UpdateManifestRejectedError,
    build_manifest,
    canonical_manifest_bytes,
    manifest_signing_payload,
    sign_manifest,
    verify_manifest_signature,
    verify_payload_against_manifest,
)

__all__ = [
    "ACTIVE_TRUST_ROOT",
    "MANIFEST_SCHEMA_VERSION",
    "TRUST_ROOT_SCHEMA_VERSION",
    "UpdateManifestError",
    "UpdateManifestRejectedError",
    "build_manifest",
    "canonical_manifest_bytes",
    "manifest_signing_payload",
    "sign_manifest",
    "trusted_public_key",
    "trusted_public_keys",
    "verify_manifest_signature",
    "verify_payload_against_manifest",
]
