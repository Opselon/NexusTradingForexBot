"""S1 (release-security): production FETCH of update-manifest.signed.json.

The fail-closed signature gate in ``UpdatePlanBuilder.build`` §6b was
pinned by test_signed_update_manifest (sig13/14/15) but was UNSATISFIABLE
in production: nothing ever set ``release["update_manifest"]`` from a real
GitHub release. These tests pin the fetch wiring added by
``SignedManifestResolver`` — deterministic, monkeypatched ``urlopen``, zero
network:

  F1  present + valid signed asset      -> plan proceeds to the EXISTING
       signature verification (UPDATE_AVAILABLE / SIGNED_MANIFEST_OK);
  F2  signed asset absent               -> SECURITY_BLOCKED, fail-closed
       reason distinguishable in decisions (names the asset);
  F3  asset body malformed JSON         -> SECURITY_BLOCKED (same posture);
  F4  asset fetch HTTP 404              -> SECURITY_BLOCKED (same posture);
  F5  descriptor already carries a manifest -> resolver makes NO network call;
  F6  absent asset logs a WARNING naming the asset (never secrets);
  F7  offline CLI --signed-manifest symmetric plumbing (attach + usage guard).
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import nacl.encoding
import nacl.signing
import pytest

from nexus_scalp.release import updater as upd
from nexus_scalp.release.signing import (
    ACTIVE_TRUST_ROOT,
    build_manifest,
    sign_manifest,
    trusted_keys,
)
from nexus_scalp.release.update_engine.constants import SIGNED_MANIFEST_ASSET

PAYLOAD_DIGEST = hashlib.sha256(b"s1-payload").hexdigest()
CHECKSUM_URL = "https://example.test/SHA256SUMS.txt"
SIGNED_URL = f"https://example.test/{SIGNED_MANIFEST_ASSET}"
ZIP_URL = "https://example.test/NexusScalpEngine-9.1.0-win-x64.zip"


@pytest.fixture()
def signing_key(monkeypatch: pytest.MonkeyPatch) -> nacl.signing.SigningKey:
    """A REAL Ed25519 key registered as the (temporary) embedded trust root."""
    key = nacl.signing.SigningKey.generate()
    monkeypatch.setattr(
        trusted_keys,
        "TRUSTED_UPDATE_KEYS",
        {ACTIVE_TRUST_ROOT: key.verify_key.encode(encoder=nacl.encoding.HexEncoder).decode()},
    )
    return key


def _signed_bytes(signing_key: nacl.signing.SigningKey) -> bytes:
    manifest = build_manifest(
        version="9.1.0",
        platform="windows",
        architecture="x64",
        artifact_name="NexusScalpEngine-9.1.0-win-x64.zip",
        artifact_sha256=PAYLOAD_DIGEST,
        artifact_size=1024,
        release_id=42,
    )
    key_hex = signing_key.encode(encoder=nacl.encoding.HexEncoder).decode()
    return json.dumps(sign_manifest(manifest, key_hex)).encode("utf-8")


def _release(with_signed_asset: bool = True) -> dict[str, Any]:
    release: dict[str, Any] = {
        "tag_name": "v9.1.0",
        "prerelease": False,
        "draft": False,
        "body": "",
        "assets": [
            {
                "name": "NexusScalpEngine-9.1.0-win-x64.zip",
                "browser_download_url": ZIP_URL,
                "digest_sha256": PAYLOAD_DIGEST,
                "size": 1024,
            },
        ],
    }
    if with_signed_asset:
        release["assets"].append(
            {
                "name": SIGNED_MANIFEST_ASSET,
                "browser_download_url": SIGNED_URL,
                "size": 640,
            }
        )
    return release


class _FakeResp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *a: object) -> bool:
        return False


def _serve(
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, bytes | Exception],
) -> list[str]:
    """Route monkeypatched urlopen by requested URL; records the URLs seen."""
    seen: list[str] = []

    def fake_urlopen(req: Any, timeout: float | None = None) -> _FakeResp:
        url = str(req.full_url if hasattr(req, "full_url") else req)
        seen.append(url)
        handler = routes.get(url)
        if isinstance(handler, Exception):
            raise handler
        if handler is None:
            raise urllib.error.HTTPError(url, 404, "not found", None, None)  # type: ignore[arg-type]
        return _FakeResp(handler)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def _http_404(url: str = SIGNED_URL) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, 404, "not found", None, None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# F1 — present + valid: the fetched asset feeds the EXISTING verification
# ---------------------------------------------------------------------------
def test_s1_fetch_valid_signed_asset_produces_update_available(
    signing_key: nacl.signing.SigningKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _serve(monkeypatch, {SIGNED_URL: _signed_bytes(signing_key)})
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0").build(_release())
    assert SIGNED_URL in seen
    assert plan["status"] == "UPDATE_AVAILABLE"
    assert plan["signature_status"] == "SIGNED_MANIFEST_OK"
    assert plan["signed_manifest_key_id"] == ACTIVE_TRUST_ROOT


def test_s1_tampered_signed_asset_still_blocked(
    signing_key: nacl.signing.SigningKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fetch attaches the document; §6b remains the authority: a
    post-signing digest edit (attacker re-uploaded the asset body) fails."""
    body = json.loads(_signed_bytes(signing_key))
    body["artifact_sha256"] = "ee" * 32
    _serve(monkeypatch, {SIGNED_URL: json.dumps(body).encode()})
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0").build(_release())
    assert plan["status"] == "SECURITY_BLOCKED"
    assert plan["signature_status"] == "SIGNATURE_INVALID"


# ---------------------------------------------------------------------------
# F2 — absent asset: fail-closed, MISSING distinguishable in the decisions
# ---------------------------------------------------------------------------
def test_s1_absent_asset_blocks_with_named_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _serve(monkeypatch, {})  # ANY fetch attempt 404s — none should happen
    release = _release(with_signed_asset=False)
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0").build(release)
    assert plan["status"] == "SECURITY_BLOCKED"
    assert "update_manifest" not in release  # set NOTHING — no partial trust
    assert any(SIGNED_MANIFEST_ASSET in d for d in plan["decisions"]), plan["decisions"]
    assert any("not published" in d for d in plan["decisions"])
    assert seen == []  # nothing to fetch was fetched


# ---------------------------------------------------------------------------
# F3 — malformed JSON asset: same fail-closed posture
# ---------------------------------------------------------------------------
def test_s1_malformed_json_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, {SIGNED_URL: b"{not json!!"})
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0").build(_release())
    assert plan["status"] == "SECURITY_BLOCKED"
    assert any(SIGNED_MANIFEST_ASSET in d and "not valid JSON" in d for d in plan["decisions"])


def test_s1_json_non_object_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, {SIGNED_URL: b"[1,2,3]"})
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0").build(_release())
    assert plan["status"] == "SECURITY_BLOCKED"
    assert any("not a JSON object" in d for d in plan["decisions"])


# ---------------------------------------------------------------------------
# F4 — 404 on the asset URL: same fail-closed posture
# ---------------------------------------------------------------------------
def test_s1_http_404_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, {SIGNED_URL: _http_404()})
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0").build(_release())
    assert plan["status"] == "SECURITY_BLOCKED"
    assert any("could not be fetched" in d and "HTTP 404" in d for d in plan["decisions"])


def test_s1_network_error_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, {SIGNED_URL: urllib.error.URLError("connection reset")})
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0").build(_release())
    assert plan["status"] == "SECURITY_BLOCKED"
    assert any("could not be fetched" in d for d in plan["decisions"])


# ---------------------------------------------------------------------------
# F5 — a descriptor that already carries a manifest is never re-fetched
# ---------------------------------------------------------------------------
def test_s1_preattached_manifest_short_circuits_fetch(
    signing_key: nacl.signing.SigningKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    signed = json.loads(_signed_bytes(signing_key))
    seen = _serve(monkeypatch, {})
    release = _release()
    release["update_manifest"] = signed
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0").build(release)
    assert plan["status"] == "UPDATE_AVAILABLE"
    assert seen == []  # zero network calls


# ---------------------------------------------------------------------------
# F6 — absence is logged loudly (WARNING naming the asset; never secrets)
# ---------------------------------------------------------------------------
def test_s1_absent_asset_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="nexus_scalp.release.update"):
        upd.UpdatePlanBuilder(installed_version="9.0.0").build(_release(with_signed_asset=False))
    msgs = [r.getMessage() for r in caplog.records]
    assert any("[UPDATE]" in m and SIGNED_MANIFEST_ASSET in m and "absent" in m for m in msgs), msgs


# ---------------------------------------------------------------------------
# F7 — offline CLI: --signed-manifest symmetric plumbing
# ---------------------------------------------------------------------------
def _invoke_cli(args: list[str]):
    from typer.testing import CliRunner

    from nexus_scalp.cli.main import app

    return CliRunner().invoke(app, args)


def test_s1_cli_signed_manifest_option_attaches(
    signing_key: nacl.signing.SigningKey,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nexus_scalp.release import exit_codes as xc

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "nexus_scalp.cli.main.get_version_info",
        lambda: {
            "version": "9.0.0",
            "architecture": "x64",
            "channel": "stable",
            "commit": None,
            "platform": "windows",
        },
    )
    _serve(monkeypatch, {})  # offline mode must NOT reach the network
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(_release(with_signed_asset=False)), encoding="utf-8")
    signed_file = tmp_path / SIGNED_MANIFEST_ASSET
    signed_file.write_bytes(_signed_bytes(signing_key))
    res = _invoke_cli(
        [
            "update",
            "--manifest",
            str(manifest),
            "--signed-manifest",
            str(signed_file),
            "--json",
        ]
    )
    data = json.loads(res.stdout)
    assert data["status"] == "UPDATE_AVAILABLE", data
    assert data["signature_status"] == "SIGNED_MANIFEST_OK"
    assert res.exit_code == xc.EXIT_UPDATE  # available -> actionable (existing mapping)


def test_s1_cli_manifest_feed_keeps_embedded_update_manifest(
    signing_key: nacl.signing.SigningKey,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The descriptor rebuild must no longer DROP an embedded signed manifest."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "nexus_scalp.cli.main.get_version_info",
        lambda: {
            "version": "9.0.0",
            "architecture": "x64",
            "channel": "stable",
            "commit": None,
            "platform": "windows",
        },
    )
    _serve(monkeypatch, {})
    feed = _release(with_signed_asset=False)
    feed["update_manifest"] = json.loads(_signed_bytes(signing_key))
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(feed), encoding="utf-8")
    res = _invoke_cli(["update", "--manifest", str(manifest), "--json"])
    data = json.loads(res.stdout)
    assert data["status"] == "UPDATE_AVAILABLE", data


def test_s1_cli_signed_manifest_requires_manifest(tmp_path: Path) -> None:
    from nexus_scalp.release import exit_codes as xc

    res = _invoke_cli(["update", "--signed-manifest", str(tmp_path / "x.json"), "--json"])
    assert res.exit_code == xc.EXIT_USAGE
