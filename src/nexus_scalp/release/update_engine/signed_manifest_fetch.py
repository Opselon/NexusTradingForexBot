"""Signed update-manifest asset discovery + fetch (Finding 1, release wave).

The release pipeline (release.yml) publishes ``update-manifest.signed.json``
as a GitHub release ASSET.  The updater trust root (37072a86) refuses any
release whose plan stage cannot pair a VALID Ed25519 signature with the
resolved artifact digest — but until now nothing ever FETCHED the published
asset, so every real release degraded to SECURITY_BLOCKED (fail-closed, but
the self-update chain could never complete).

This module closes the loop, fetch-side only:

  discovery : find the signed-manifest asset among the release assets
  fetch     : download its exact bytes (no redirect-without-verify games)
  inject    : parse + structurally validate and attach to the release dict
              as release["update_manifest"] so UpdatePlanBuilder can verify
              the signature against the trust root and the resolved digest.

Fail-closed contract (no weakening of MANIFEST_MALFORMED / MISSING_SIGNATURE):

  * asset absent                      -> release["update_manifest"] stays
                                         absent (plan builder blocks with
                                         MISSING_SIGNATURE/MALFORMED)
  * download failure                  -> treated like a missing manifest
                                         (SECURITY_BLOCKED at plan stage)
  * non-dict / unparsable JSON        -> not injected (plan blocks)
  * partial/corrupt download          -> not injected (plan blocks)

Nothing here ever trusts release API metadata alone; the plan builder still
verifies the Ed25519 signature over the canonical bytes against the embedded
trust root and cross-checks the covered digest against the independently
resolved artifact digest.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

#: Asset filename published by release.yml (scripts/release/sign_update_manifest.py
#: writes manifests/update-manifest.signed.json; release.yml uploads it and
#: softprops/action-gh-release publishes it under its basename).
SIGNED_MANIFEST_ASSET = "update-manifest.signed.json"


def find_signed_manifest_asset(release: dict[str, Any]) -> dict[str, Any] | None:
    """The signed-manifest asset descriptor, or None when not published."""
    for asset in release.get("assets", []) or []:
        if str(asset.get("name", "")).strip() == SIGNED_MANIFEST_ASSET:
            return asset
    return None


def fetch_signed_manifest(asset: dict[str, Any], timeout: int = 60) -> str:
    """Download the signed-manifest asset bytes as text (raises on failure)."""
    url = asset.get("browser_download_url") or asset.get("url")
    if not url:
        raise ValueError("signed-manifest asset has no download URL")
    req = urllib.request.Request(url, headers={"User-Agent": "nse-update-client"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def attach_signed_manifest(release: dict[str, Any], timeout: int = 60) -> bool:
    """Fetch + parse + attach release["update_manifest"] (fail-closed).

    Returns True when a structurally valid manifest dict was attached.
    Returns False — leaving the release dict UNMODIFIED — when the asset is
    absent, unreachable, or not a JSON object: the plan builder then blocks
    with its existing MISSING_SIGNATURE / MANIFEST_MALFORMED taxonomy.  A
    structurally valid but cryptographically invalid manifest IS attached;
    the plan builder's signature verification is the authority that rejects
    it (this module never makes trust decisions).
    """
    asset = find_signed_manifest_asset(release)
    if asset is None:
        return False
    try:
        text = fetch_signed_manifest(asset, timeout=timeout)
        parsed = json.loads(text)
    except Exception:
        return False
    if not isinstance(parsed, dict):
        return False
    release["update_manifest"] = parsed
    return True
