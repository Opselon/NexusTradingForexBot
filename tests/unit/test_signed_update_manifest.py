"""P0 SIGNED UPDATE MANIFEST — trust-chain security tests (TEST-UP-SIG-01..10).

The attacker model these tests protect against:

    * A compromised publisher account/token replaces the payload AND its
      checksum asset. SHA-256 agreement alone proves NOTHING — both sides
      came from the attacker. Only a manifest signed by the EMBEDDED Ed25519
      trust root can authorize an install.
    * Any manifest field mutation (version/digest/size/name) invalidates the
      signature (canonical-bytes tamper evidence).
    * Unknown keys, missing signatures, malformed manifests: REJECTED —
      never a silent fallback to unsigned updates.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import nacl.encoding
import nacl.signing
import pytest

from nexus_scalp.release.signing import (
    ACTIVE_TRUST_ROOT,
    MANIFEST_SCHEMA_VERSION,
    UpdateManifestError,
    UpdateManifestRejectedError,
    build_manifest,
    canonical_manifest_bytes,
    sign_manifest,
    verify_manifest_signature,
    verify_payload_against_manifest,
)
from nexus_scalp.release.signing import trusted_keys


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
@pytest.fixture()
def signing_key(monkeypatch) -> nacl.signing.SigningKey:
    """A REAL Ed25519 key registered as the (temporary) embedded trust root."""
    key = nacl.signing.SigningKey.generate()
    monkeypatch.setattr(
        trusted_keys,
        "TRUSTED_UPDATE_KEYS",
        {ACTIVE_TRUST_ROOT: key.verify_key.encode(encoder=nacl.encoding.HexEncoder).decode()},
    )
    return key


def _payload_bytes() -> bytes:
    return b"PK\x03\x04 NSE fake payload " * 64


def _signed_manifest(signing_key: nacl.signing.SigningKey, payload: bytes, **over: Any) -> dict[str, Any]:
    manifest = build_manifest(
        version="9.1.0",
        platform="windows",
        architecture="x64",
        artifact_name="NexusScalpEngine-9.1.0-win-x64.zip",
        artifact_sha256=hashlib.sha256(payload).hexdigest(),
        artifact_size=len(payload),
        release_id=42,
    )
    manifest.update(over)
    return sign_manifest(manifest, signing_key.encode(encoder=nacl.encoding.HexEncoder).decode())


def _payload_file(tmp_path: Path, payload: bytes) -> Path:
    p = tmp_path / "payload.zip"
    p.write_bytes(payload)
    return p


# ---------------------------------------------------------------------------
# A. valid signed update
# ---------------------------------------------------------------------------
def test_sig01_valid_signed_manifest_verifies(signing_key, tmp_path: Path) -> None:
    payload = _payload_bytes()
    signed = _signed_manifest(signing_key, payload)
    verdict = verify_manifest_signature(signed)
    assert verdict["verified"] is True
    assert verdict["key_id"] == ACTIVE_TRUST_ROOT
    # Payload binding: bytes on disk == signed digest
    ok = verify_payload_against_manifest(signed, _payload_file(tmp_path, payload))
    assert ok["payload_sha256"] == hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Canonicalization determinism
# ---------------------------------------------------------------------------
def test_sig02_same_manifest_same_canonical_bytes() -> None:
    m = build_manifest(
        version="9.1.0",
        platform="windows",
        architecture="x64",
        artifact_name="a.zip",
        artifact_sha256="ab" * 32,
        artifact_size=1,
    )
    assert canonical_manifest_bytes(m) == canonical_manifest_bytes(m)
    # insertion order must not matter
    reversed_dict = dict(reversed(list(m.items())))
    assert canonical_manifest_bytes(m) == canonical_manifest_bytes(reversed_dict)


def test_sig03_signature_deterministic_for_same_canonical_bytes(signing_key) -> None:
    m = build_manifest(
        version="9.1.0",
        platform="windows",
        architecture="x64",
        artifact_name="a.zip",
        artifact_sha256="ab" * 32,
        artifact_size=1,
    )
    key_hex = signing_key.encode(encoder=nacl.encoding.HexEncoder).decode()
    s1 = sign_manifest(m, key_hex)
    s2 = sign_manifest(m, key_hex)
    assert s1["signature"] == s2["signature"]


# ---------------------------------------------------------------------------
# B/C. tampered manifest / payload -> rejection
# ---------------------------------------------------------------------------
def test_sig04_changed_payload_rejected_even_with_matching_checksum(
    signing_key, tmp_path: Path
) -> None:
    """THE attacker model: payload X + checksum X, then malicious swap.

    The attacker recomputes NOTHING that helps: the signed digest still
    describes the ORIGINAL payload; swapped bytes must be rejected.
    """
    payload = _payload_bytes()
    signed = _signed_manifest(signing_key, payload)
    evil = b"E" * len(payload)  # same size, different bytes: digest must catch it
    with pytest.raises(UpdateManifestRejectedError) as err:
        verify_payload_against_manifest(signed, _payload_file(tmp_path, evil))
    assert err.value.reason == "DIGEST_MISMATCH"


def test_sig05_changed_manifest_field_invalidates_signature(signing_key) -> None:
    payload = _payload_bytes()
    signed = _signed_manifest(signing_key, payload)
    for field, value in (
        ("version", "9.9.9"),
        ("artifact_sha256", "cd" * 32),
        ("artifact_size", len(payload) + 1),
        ("artifact_name", "NexusScalpEngine-9.1.0-win-x64-evil.exe"),
        ("platform", "linux"),
        ("architecture", "arm64"),
    ):
        tampered = dict(signed)
        tampered[field] = value
        with pytest.raises(UpdateManifestRejectedError) as err:
            verify_manifest_signature(tampered)
        assert err.value.reason == "SIGNATURE_INVALID"


# ---------------------------------------------------------------------------
# D. invalid signature / E. unknown key / F. missing signature
# ---------------------------------------------------------------------------
def test_sig06_signature_from_wrong_key_rejected(signing_key) -> None:
    payload = _payload_bytes()
    signed = _signed_manifest(signing_key, payload)
    attacker = nacl.signing.SigningKey.generate()
    forged = dict(signed)
    unsigned = {k: v for k, v in signed.items() if k != "signature"}
    forged["signature"] = attacker.sign(canonical_manifest_bytes(unsigned)).signature.hex()
    with pytest.raises(UpdateManifestRejectedError) as err:
        verify_manifest_signature(forged)
    assert err.value.reason == "SIGNATURE_INVALID"


def test_sig07_unknown_key_id_rejected(signing_key) -> None:
    """A manifest signed by a key NOT in the embedded trust root: REJECT."""
    payload = _payload_bytes()
    stranger = nacl.signing.SigningKey.generate()
    manifest = build_manifest(
        version="9.1.0",
        platform="windows",
        architecture="x64",
        artifact_name="a.zip",
        artifact_sha256=hashlib.sha256(payload).hexdigest(),
        artifact_size=len(payload),
        key_id="stranger-root",
    )
    # Sign RAW (bypassing sign_manifest's embedded-root round-trip check) to
    # simulate an out-of-repo signer whose key the client does not trust.
    signed = dict(manifest)
    signed["signature"] = (
        stranger.sign(canonical_manifest_bytes(manifest)).signature.hex()
    )
    with pytest.raises(UpdateManifestRejectedError) as err:
        verify_manifest_signature(signed)
    assert err.value.reason == "UNKNOWN_KEY"


def test_sig08_missing_signature_rejected(signing_key) -> None:
    payload = _payload_bytes()
    signed = _signed_manifest(signing_key, payload)
    unsigned = {k: v for k, v in signed.items() if k != "signature"}
    with pytest.raises(UpdateManifestRejectedError) as err:
        verify_manifest_signature(unsigned)
    assert err.value.reason == "MISSING_SIGNATURE"


def test_sig09_malformed_manifests_rejected(signing_key) -> None:
    payload = _payload_bytes()
    signed = _signed_manifest(signing_key, payload)
    cases: list[dict[str, Any]] = [
        {},  # empty
        {**signed, "schema": 99},  # future/unknown schema
        {**signed, "artifact_sha256": "not-hex"},
        {**signed, "artifact_size": "large"},  # wrong type
        {**signed, "version": ""},  # empty required field
        {**signed, "signature": "zz" * 64},  # non-hex signature
        {**signed, "signature": "ab" * 10},  # truncated signature
    ]
    for bad in cases:
        with pytest.raises(UpdateManifestRejectedError) as err:
            verify_manifest_signature(bad)
        assert err.value.reason in (
            "MANIFEST_MALFORMED",
            "SIGNATURE_INVALID",
        )


# ---------------------------------------------------------------------------
# G. size binding + discovery-layer attacker case
# ---------------------------------------------------------------------------
def test_sig10_size_mismatch_rejected(signing_key, tmp_path: Path) -> None:
    payload = _payload_bytes()
    signed = _signed_manifest(signing_key, payload)
    truncated = payload[:-8]
    with pytest.raises(UpdateManifestRejectedError) as err:
        verify_payload_against_manifest(signed, _payload_file(tmp_path, truncated))
    assert err.value.reason in ("SIZE_MISMATCH", "DIGEST_MISMATCH")


def test_sig11_discovery_digest_disagreement_fails_safe(signing_key, tmp_path: Path) -> None:
    """The discovery layer resolves a digest from the release assets; if it
    disagrees with the SIGNED digest the update must fail safe."""
    payload = _payload_bytes()
    signed = _signed_manifest(signing_key, payload)
    with pytest.raises(UpdateManifestRejectedError) as err:
        verify_payload_against_manifest(
            signed, _payload_file(tmp_path, payload), expected_sha256="ee" * 32
        )
    assert err.value.reason == "DIGEST_MISMATCH"


def test_sig12_signer_rejects_bad_digest_upfront() -> None:
    with pytest.raises(UpdateManifestError):
        build_manifest(
            version="9.1.0",
            platform="windows",
            architecture="x64",
            artifact_name="a.zip",
            artifact_sha256="nothex",
            artifact_size=1,
        )


# ---------------------------------------------------------------------------
# UpdatePlanBuilder integration: unsigned release -> SECURITY_BLOCKED
# ---------------------------------------------------------------------------
def test_sig13_plan_builder_blocks_release_without_signed_manifest(monkeypatch) -> None:
    from nexus_scalp.release import updater as upd

    release = {
        "tag_name": "v9.1.0",
        "prerelease": False,
        "draft": False,
        "body": "",
        "assets": [
            {
                "name": "NexusScalpEngine-9.1.0-win-x64.zip",
                "browser_download_url": "https://example.test/payload.zip",
                "digest_sha256": "ab" * 32,
                "size": 1024,
            }
        ],
        # NO update_manifest at all
    }
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0").build(release)
    assert plan["status"] == "SECURITY_BLOCKED"
    assert plan.get("signature_status") in ("MISSING_SIGNATURE", "MANIFEST_MALFORMED", "SIGNATURE_INVALID")


def test_sig14_plan_builder_blocks_tampered_digest(signing_key, monkeypatch) -> None:
    """Signed manifest says digest A, release asset metadata says digest B
    (attacker edited the asset/checksum after signing) -> SECURITY_BLOCKED."""
    from nexus_scalp.release import updater as upd

    payload = _payload_bytes()
    signed = _signed_manifest(signing_key, payload)
    release = {
        "tag_name": "v9.1.0",
        "prerelease": False,
        "draft": False,
        "body": "",
        "update_manifest": signed,
        "assets": [
            {
                "name": str(signed["artifact_name"]),
                "browser_download_url": "https://example.test/payload.zip",
                "digest_sha256": "ee" * 32,  # tampered post-signing
                "size": signed["artifact_size"],
            }
        ],
    }
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0").build(release)
    assert plan["status"] == "SECURITY_BLOCKED"


def test_sig15_plan_builder_accepts_validly_signed_release(signing_key, monkeypatch) -> None:
    from nexus_scalp.release import updater as upd

    payload = _payload_bytes()
    signed = _signed_manifest(signing_key, payload)
    release = {
        "tag_name": "v9.1.0",
        "prerelease": False,
        "draft": False,
        "body": "",
        "update_manifest": signed,
        "assets": [
            {
                "name": str(signed["artifact_name"]),
                "browser_download_url": "https://example.test/payload.zip",
                "digest_sha256": signed["artifact_sha256"],
                "size": signed["artifact_size"],
            }
        ],
    }
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0").build(release)
    assert plan["status"] == "UPDATE_AVAILABLE"
    assert plan["signature_status"] == "SIGNED_MANIFEST_OK"
    assert plan["signed_manifest_key_id"] == ACTIVE_TRUST_ROOT


def test_sig16_trust_root_schema_version_is_recorded() -> None:
    from nexus_scalp.release.signing import TRUST_ROOT_SCHEMA_VERSION

    assert MANIFEST_SCHEMA_VERSION == 1
    assert TRUST_ROOT_SCHEMA_VERSION == 1
    keys = trusted_keys.trusted_public_keys()
    assert ACTIVE_TRUST_ROOT in keys
    assert all(len(v) == 64 for v in keys.values())
