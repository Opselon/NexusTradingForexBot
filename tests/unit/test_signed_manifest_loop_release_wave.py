"""Finding 1 — signed update-manifest loop closure (release wave).

The release pipeline publishes update-manifest.signed.json as a release
ASSET; the updater must fetch that exact asset and only then let the plan
builder verify the Ed25519 trust root.  These tests pin the full matrix:

  valid signed manifest      -> plan allowed (UPDATE_AVAILABLE)
  missing manifest asset     -> blocked  (MISSING_SIGNATURE)
  malformed manifest         -> blocked  (MANIFEST_MALFORMED)
  wrong version              -> blocked  (VERSION_MISMATCH)
  wrong artifact SHA         -> blocked  (DIGEST_MISMATCH)
  invalid signature          -> blocked  (SIGNATURE_INVALID)
  wrong/unknown public key   -> blocked  (UNKNOWN_KEY)
  tampered artifact identity -> blocked  (ARTIFACT_MISMATCH)

Plus the fetch module itself: absent asset -> not attached; unparsable
asset -> not attached; valid asset -> attached.  All plan decisions run
through the REAL UpdatePlanBuilder with a REAL Ed25519 key pair (no mock
signature bypass) and a real ephemeral HTTP server serving the asset.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import nacl.encoding
import nacl.signing
import pytest

from nexus_scalp.release import update_engine as upd
from nexus_scalp.release.signing import trusted_keys
from nexus_scalp.release.signing.update_manifest import (
    MANIFEST_SCHEMA_VERSION,
    build_manifest,
    sign_manifest,
)
from nexus_scalp.release.update_engine.signed_manifest_fetch import (
    attach_signed_manifest,
    find_signed_manifest_asset,
)

TEST_KEY_ID = "test-root-ephemeral"
ARTIFACT_NAME = "NexusScalpEngine-9.1.0-win-x64.zip"


# ---------------------------------------------------------------------------
# fixtures: real Ed25519 key + HTTP asset server
# ---------------------------------------------------------------------------
@pytest.fixture()
def signing_key(monkeypatch) -> nacl.signing.SigningKey:
    key = nacl.signing.SigningKey.generate()
    monkeypatch.setattr(
        trusted_keys,
        "TRUSTED_UPDATE_KEYS",
        {TEST_KEY_ID: key.verify_key.encode(encoder=nacl.encoding.HexEncoder).decode()},
    )
    return key


class _AssetServer:
    """Minimal HTTP server serving one asset path with one body (or 404)."""

    def __init__(self) -> None:
        self.body = b"{}"
        self.path = "/update-manifest.signed.json"
        self.status = 200
        self.server = HTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _handler(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path != outer.path or outer.status != 200:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(outer.body)))
                self.end_headers()
                self.wfile.write(outer.body)

            def log_message(self, *a: Any) -> None:
                pass

        return H

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}{self.path}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def asset_server():
    srv = _AssetServer()
    yield srv
    srv.close()


def _payload() -> bytes:
    return b"NSE-PAYLOAD-IMAGE-" + b"x" * 64


def _signed_manifest(key: nacl.signing.SigningKey, payload: bytes) -> dict[str, Any]:
    manifest = build_manifest(
        version="9.1.0",
        platform="windows",
        architecture="x64",
        artifact_name=ARTIFACT_NAME,
        artifact_sha256=__import__("hashlib").sha256(payload).hexdigest(),
        artifact_size=len(payload),
        key_id=TEST_KEY_ID,
    )
    return sign_manifest(manifest, key.encode(encoder=nacl.encoding.HexEncoder).decode())


def _release(
    *,
    digest: str | None = "ab" * 32,
    manifest: dict[str, Any] | None = None,
    with_asset: bool = True,
    asset_url: str | None = None,
    asset_name: str | None = None,
    tag: str = "v9.1.0",
) -> dict[str, Any]:
    release: dict[str, Any] = {
        "tag_name": tag,
        "prerelease": False,
        "draft": False,
        "body": "",
        "assets": [],
    }
    asset: dict[str, Any] = {
        "name": asset_name or ARTIFACT_NAME,
        "browser_download_url": "https://example.test/payload.zip",
        "size": 1024,
    }
    if digest:
        asset["digest_sha256"] = digest
    release["assets"].append(asset)
    if with_asset:
        release["assets"].append(
            {
                "name": "update-manifest.signed.json",
                "browser_download_url": asset_url or "https://example.test/unreachable",
                "size": 10,
            }
        )
    if manifest is not None:
        release["update_manifest"] = manifest
    return release


# ---------------------------------------------------------------------------
# fetch module behavior (evidence gathering, fail-closed)
# ---------------------------------------------------------------------------
def test_find_signed_manifest_asset() -> None:
    release = _release()
    asset = find_signed_manifest_asset(release)
    assert asset is not None and asset["name"] == "update-manifest.signed.json"
    assert find_signed_manifest_asset(_release(with_asset=False)) is None


def test_attach_missing_asset_leaves_release_untouched() -> None:
    release = _release(with_asset=False)
    assert attach_signed_manifest(release, timeout=2) is False
    assert "update_manifest" not in release


def test_attach_unreachable_asset_leaves_release_untouched() -> None:
    release = _release()  # default URL is unreachable (example.test)
    assert attach_signed_manifest(release, timeout=2) is False
    assert "update_manifest" not in release


def test_attach_corrupt_json_leaves_release_untouched(asset_server) -> None:
    asset_server.body = b"{not-json"
    release = _release(asset_url=asset_server.url)
    assert attach_signed_manifest(release, timeout=5) is False
    assert "update_manifest" not in release


def test_attach_valid_json_attaches(asset_server) -> None:
    asset_server.body = json.dumps({"schema": MANIFEST_SCHEMA_VERSION}).encode()
    release = _release(asset_url=asset_server.url)
    assert attach_signed_manifest(release, timeout=5) is True
    assert release["update_manifest"]["schema"] == MANIFEST_SCHEMA_VERSION


def test_attach_non_object_json_leaves_release_untouched(asset_server) -> None:
    asset_server.body = b"[1,2,3]"
    release = _release(asset_url=asset_server.url)
    assert attach_signed_manifest(release, timeout=5) is False
    assert "update_manifest" not in release


# ---------------------------------------------------------------------------
# plan-builder decisions (REAL signature verification, real key material)
# ---------------------------------------------------------------------------
def _plan(release: dict[str, Any]) -> dict[str, Any]:
    return upd.UpdatePlanBuilder(installed_version="9.0.11").build(release)


def test_valid_signed_manifest_allows_plan(signing_key) -> None:
    payload = _payload()
    digest = __import__("hashlib").sha256(payload).hexdigest()
    release = _release(digest=digest, manifest=_signed_manifest(signing_key, payload))
    plan = _plan(release)
    assert plan["status"] == "UPDATE_AVAILABLE"
    assert plan["signature_status"] == "SIGNED_MANIFEST_OK"


def test_missing_manifest_blocks(signing_key) -> None:
    release = _release(with_asset=False)  # no asset, no manifest
    plan = _plan(release)
    assert plan["status"] == "SECURITY_BLOCKED"
    assert plan["signature_status"] == "MISSING_SIGNATURE"


def test_asset_present_but_unattached_blocks_precisely(signing_key) -> None:
    release = _release()  # asset listed but never fetched/attached
    plan = _plan(release)
    assert plan["status"] == "SECURITY_BLOCKED"
    assert plan["signature_status"] == "SIGNED_MANIFEST_ASSET_UNREACHABLE"


def test_malformed_manifest_blocks(signing_key) -> None:
    # version + artifact_name match the release/asset identity (so the
    # identity cross-checks pass) but the manifest lacks required signed
    # fields -> MANIFEST_MALFORMED from the verifier
    release = _release(manifest={"hello": "world", "version": "9.1.0", "artifact_name": ARTIFACT_NAME})
    plan = _plan(release)
    assert plan["status"] == "SECURITY_BLOCKED"
    assert plan["signature_status"] == "MANIFEST_MALFORMED"


def test_wrong_version_manifest_blocks(signing_key) -> None:
    payload = _payload()
    signed = _signed_manifest(signing_key, payload)
    signed["version"] = "9.2.0"  # re-signed for a DIFFERENT release than the tag
    signed["signature"] = sign_manifest(
        {k: v for k, v in signed.items() if k != "signature"},
        signing_key.encode(encoder=nacl.encoding.HexEncoder).decode(),
    )["signature"]
    release = _release(
        manifest=signed,
        digest=__import__("hashlib").sha256(payload).hexdigest(),
        tag="v9.1.0",
    )
    plan = _plan(release)
    assert plan["status"] == "SECURITY_BLOCKED"
    assert plan["signature_status"] == "VERSION_MISMATCH"


def test_wrong_artifact_sha_blocks(signing_key) -> None:
    payload = _payload()
    signed = _signed_manifest(signing_key, payload)
    # release metadata advertises a different digest than the signed one:
    release = _release(digest="cd" * 32, manifest=signed)
    plan = _plan(release)
    assert plan["status"] == "SECURITY_BLOCKED"
    assert plan["signature_status"] == "DIGEST_MISMATCH"


def test_invalid_signature_blocks(signing_key) -> None:
    payload = _payload()
    signed = _signed_manifest(signing_key, payload)
    signed["artifact_sha256"] = "ab" * 32  # tamper AFTER signing
    release = _release(manifest=signed)
    plan = _plan(release)
    assert plan["status"] == "SECURITY_BLOCKED"
    assert plan["signature_status"] == "SIGNATURE_INVALID"


def test_wrong_public_key_blocks(signing_key) -> None:
    """Manifest names the TRUSTED key_id but the signature was produced by an
    attacker key: sign_manifest cannot be used (it round-trip-verifies), so
    hand-forge the tampered manifest with the attacker's key bytes."""
    attacker = nacl.signing.SigningKey.generate()
    payload = _payload()
    manifest = build_manifest(
        version="9.1.0",
        platform="windows",
        architecture="x64",
        artifact_name=ARTIFACT_NAME,
        artifact_sha256=__import__("hashlib").sha256(payload).hexdigest(),
        artifact_size=len(payload),
        key_id=TEST_KEY_ID,
    )
    sig = attacker.sign(__import__(
        "nexus_scalp.release.signing.update_manifest", fromlist=["x"]
    ).manifest_signing_payload(manifest)).signature.hex()
    signed = {**manifest, "signature": sig}
    release = _release(
        manifest=signed,
        digest=__import__("hashlib").sha256(payload).hexdigest(),
    )
    plan = _plan(release)
    assert plan["status"] == "SECURITY_BLOCKED"
    # the manifest names the trusted key_id but the signature bytes were
    # produced by the attacker's key -> signature verify fails
    assert plan["signature_status"] == "SIGNATURE_INVALID"


def test_unknown_key_id_blocks() -> None:
    """Manifest signed by a real key but with a key_id absent from the
    embedded trust root: sign_manifest refuses to emit it (round-trip
    verify), so forge the signature bytes directly."""
    payload = _payload()
    manifest = build_manifest(
        version="9.1.0",
        platform="windows",
        architecture="x64",
        artifact_name=ARTIFACT_NAME,
        artifact_sha256=__import__("hashlib").sha256(payload).hexdigest(),
        artifact_size=len(payload),
        key_id="rogue-root-not-in-trust-store",
    )
    rogue = nacl.signing.SigningKey.generate()
    from nexus_scalp.release.signing.update_manifest import manifest_signing_payload

    signed = {
        **manifest,
        "signature": rogue.sign(manifest_signing_payload(manifest)).signature.hex(),
    }
    release = _release(
        manifest=signed,
        digest=__import__("hashlib").sha256(payload).hexdigest(),
    )
    plan = _plan(release)
    assert plan["status"] == "SECURITY_BLOCKED"
    assert plan["signature_status"] == "UNKNOWN_KEY"


def test_unsigned_manifest_blocks(signing_key) -> None:
    payload = _payload()
    manifest = build_manifest(
        version="9.1.0",
        platform="windows",
        architecture="x64",
        artifact_name=ARTIFACT_NAME,
        artifact_sha256=__import__("hashlib").sha256(payload).hexdigest(),
        artifact_size=len(payload),
        key_id=TEST_KEY_ID,
    )  # never signed
    release = _release(
        manifest=manifest,
        digest=__import__("hashlib").sha256(payload).hexdigest(),
    )
    plan = _plan(release)
    assert plan["status"] == "SECURITY_BLOCKED"
    assert plan["signature_status"] in ("MISSING_SIGNATURE", "SIGNATURE_INVALID")


def test_tampered_artifact_identity_blocks(signing_key) -> None:
    payload = _payload()
    signed = _signed_manifest(signing_key, payload)
    # signed for the ZIP but the release offers the setup.exe as the payload
    release = _release(
        manifest=signed,
        digest=__import__("hashlib").sha256(payload).hexdigest(),
        asset_name="NexusScalpEngine-9.1.0-win-x64-setup.exe",
    )
    plan = _plan(release)
    assert plan["status"] == "SECURITY_BLOCKED"
    assert plan["signature_status"] == "ARTIFACT_MISMATCH"


# ---------------------------------------------------------------------------
# end-to-end: published-asset representation -> fetch -> verify -> plan
# ---------------------------------------------------------------------------
def test_end_to_end_asset_fetch_to_install_plan(signing_key, asset_server) -> None:
    """The FULL loop without publishing anything: a release whose signed
    manifest exists ONLY as an HTTP asset is fetched, attached, verified
    against the trust root and converted into an UPDATE_AVAILABLE plan."""
    payload = _payload()
    digest = __import__("hashlib").sha256(payload).hexdigest()
    asset_server.body = json.dumps(_signed_manifest(signing_key, payload)).encode()
    release = _release(digest=digest, asset_url=asset_server.url)
    # exactly what UpdateOrchestrator.check() does after fetch_releases():
    assert attach_signed_manifest(release, timeout=5) is True
    plan = _plan(release)
    assert plan["status"] == "UPDATE_AVAILABLE"
    assert plan["signature_status"] == "SIGNED_MANIFEST_OK"
    assert plan["signed_manifest_key_id"] == TEST_KEY_ID
    assert plan["artifact_sha256"] == digest
    assert plan["target_version"] == "9.1.0"
    # and the same release WITHOUT the reachable asset stays blocked:
    broken = _release(digest=digest)  # unreachable default asset URL
    assert attach_signed_manifest(broken, timeout=2) is False
    plan_broken = _plan(broken)
    assert plan_broken["status"] == "SECURITY_BLOCKED"
