"""Versioned Ed25519 public-key trust root for update-manifest signatures.

The verification keys are EMBEDDED in the client (this file). They are the
only authority a client trusts for update manifests; the SHA-256 payload
digest is trusted only when a signature from one of these keys covers it.

Key rotation: append a NEW key_id -> public key mapping, publish manifests
signed by the new key (key_id inside the manifest selects it), keep the old
key ONLY while older installed clients may still receive manifests signed
with it, then remove it. Rotation must never weaken verification: a
manifest whose key_id is unknown is REJECTED, not downgraded.
"""

from __future__ import annotations

from typing import Final

#: Schema version of the trust-root layout itself (change log below).
#: v1: initial Ed25519 root (PyNaCl / libsodium), hex-encoded 32-byte keys.
TRUST_ROOT_SCHEMA_VERSION: Final[int] = 1

#: key_id -> Ed25519 public key (64-hex-char). key_id is an opaque stable
#: identifier (e.g. "2026-09-root") printed in every signed manifest so a
#: client can select the right verification key WITHOUT network access.
TRUSTED_UPDATE_KEYS: Final[dict[str, str]] = {
    # Initial production trust root. Public half of the release signing key
    # (private seed lives ONLY in the NSE_UPDATE_SIGNING_KEY GitHub secret;
    # operator escrow copy outside the repository).
    "2026-09-root": "8d6be6286be938b1fe0076129191791f8f596169d8e8a350c5fe844adc9e82ae",
}

#: Key the release pipeline signs with TODAY (must be a key in the map).
ACTIVE_TRUST_ROOT: Final[str] = "2026-09-root"


def trusted_public_key(key_id: str) -> str | None:
    """Returns the hex public key for a manifest key_id, or None (unknown)."""
    key = TRUSTED_UPDATE_KEYS.get(key_id)
    if key is None:
        return None
    return key


def trusted_public_keys() -> dict[str, str]:
    """Snapshot of all currently trusted public keys (id -> hex)."""
    return dict(TRUSTED_UPDATE_KEYS)
