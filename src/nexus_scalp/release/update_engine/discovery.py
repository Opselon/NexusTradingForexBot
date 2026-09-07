"""Extracted from release/updater.py (update-package split); behavior preserved."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from nexus_scalp.release import packaging
from nexus_scalp.release.metadata import parse_version
from nexus_scalp.release.signing import (
    UpdateManifestError,
    verify_payload_against_manifest,
)
from nexus_scalp.release.update_engine.constants import (
    _CHECKSUM_ASSET_RE,
    _REVOKED_MARKER_RE,
    _SOURCE_ASSET_RE,
    DEFAULT_CHANNEL,
    STATE_CHECKING,
    STATUS_DIRECT_UPDATE_UNSUPPORTED,
    STATUS_GITHUB_UNAVAILABLE,
    STATUS_INCOMPATIBLE,
    STATUS_NETWORK_ERROR,
    STATUS_NETWORK_UNAVAILABLE,
    STATUS_NO_UPDATE,
    STATUS_RELEASE_NOT_FOUND,
    STATUS_SECURITY_BLOCKED,
    STATUS_UNKNOWN,
    STATUS_UPDATE_AVAILABLE,
    STATUS_UPDATE_REJECTED,
    SUPPORTED_CHANNELS,
    SUPPORTED_PLATFORM,
)


def compare_versions(a: str, b: str) -> int | None:
    """Deterministic semantic comparison; None when either side is invalid."""
    pa, pb = parse_version(a), parse_version(b)
    if pa is None or pb is None:
        return None
    return (pa > pb) - (pa < pb)


# ---------------------------------------------------------------------------
# GitHub discovery
# ---------------------------------------------------------------------------
class GitHubDiscoveryError(RuntimeError):
    """Raised when the GitHub Releases API cannot be queried safely."""

    def __init__(self, code: str, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after


class UpdateDiscovery:
    """Queries GitHub Releases and selects the correct release for a channel.

    The GitHub Releases API is the ONLY canonical update source for packaged
    users.  The engine never updates an installed build from a main-branch
    source archive (spec section 3).
    """

    DEFAULT_API = "https://api.github.com/repos/Opselon/NexusTradingForexBot/releases"
    USER_AGENT = "NexusScalpEngine-Update/9.x"

    #: Transient HTTP codes retried with exponential backoff (spec 16).
    _RETRYABLE_CODES = frozenset({"408", "429", "500", "502", "503", "504"})

    @classmethod
    def fetch_releases(
        cls,
        *,
        api_url: str | None = None,
        timeout: int = 20,
        user_agent: str | None = None,
        max_retries: int = 3,
    ) -> list[dict[str, Any]]:
        """GET the releases list.  Raises GitHubDiscoveryError on ANY failure.

        HTTP 404 on the repo/releases endpoint means "no releases yet" —
        which must surface as RELEASE_NOT_FOUND, never as "latest == current".
        """
        url = api_url or cls.DEFAULT_API
        last_err: GitHubDiscoveryError | None = None
        attempt = 0
        while attempt <= max_retries:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": user_agent or cls.USER_AGENT,
                    "Accept": "application/vnd.github+json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
            except urllib.error.HTTPError as e:
                retry_after = None
                try:
                    retry_after = int(e.headers.get("Retry-After", ""))
                except (TypeError, ValueError):
                    pass
                last_err = GitHubDiscoveryError(
                    str(e.code), e.reason or str(e), retry_after=retry_after
                )
                if str(e.code) in cls._RETRYABLE_CODES and attempt < max_retries:
                    delay = retry_after if retry_after is not None else min(2**attempt * 2, 30)
                    time.sleep(delay)
                    attempt += 1
                    continue
                raise last_err from last_err
            except urllib.error.URLError as e:
                last_err = GitHubDiscoveryError("", str(e.reason or e))
                if attempt < max_retries:
                    time.sleep(min(2**attempt * 2, 30))
                    attempt += 1
                    continue
                raise last_err from last_err
            except TimeoutError:
                last_err = GitHubDiscoveryError("", "timeout contacting GitHub")
                if attempt < max_retries:
                    time.sleep(min(2**attempt * 2, 30))
                    attempt += 1
                    continue
                raise last_err from last_err
            try:
                data = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as e:
                raise GitHubDiscoveryError("", f"invalid JSON from GitHub: {e}") from e
            if not isinstance(data, list):
                # GitHub returns a dict for repo-level errors (e.g. 403 abuse).
                msg = str(data.get("message", "unexpected GitHub payload"))[:200]
                raise GitHubDiscoveryError("", msg)
            return data
        assert last_err is not None
        raise last_err from last_err

    @classmethod
    def _is_revoked(cls, release: dict[str, Any]) -> bool:
        """True when the release explicitly marks itself revoked (spec 47)."""
        body = " ".join(str(release.get(k) or "") for k in ("body", "body_text", "name"))
        return bool(_REVOKED_MARKER_RE.search(body))

    @classmethod
    def _select_release(
        cls,
        releases: list[dict[str, Any]],
        channel: str,
        *,
        include_prerelease: bool = False,
    ) -> dict[str, Any] | None:
        """Highest semantically-versioned eligible release for the channel.

        Eligibility (spec 4/6/47): not draft, not revoked, valid semver tag,
        at least one asset.  stable refuses prereleases unless explicitly
        requested; beta/nightly may take them.
        """
        best: dict[str, Any] | None = None
        best_tag: tuple[int, int, int] | None = None
        for rel in releases:
            if rel.get("draft"):
                continue
            if cls._is_revoked(rel):
                continue
            tag = str(rel.get("tag_name", "")).lstrip("v")
            parsed = parse_version(tag)
            if parsed is None or not rel.get("assets"):
                continue
            if channel == "stable" and rel.get("prerelease") and not include_prerelease:
                continue
            if best_tag is None or parsed > best_tag:
                best, best_tag = rel, parsed
        return best

    @classmethod
    def release_identity(cls, release: dict[str, Any]) -> dict[str, Any]:
        """Lock the EXACT release identity before any download (spec 7)."""
        return {
            "release_id": release.get("id"),
            "tag": str(release.get("tag_name", "")),
            "version": str(release.get("tag_name", "")).lstrip("v"),
            "commit_sha": str(release.get("target_commitish") or release.get("commit") or ""),
            "published_at": str(release.get("published_at") or release.get("created_at") or ""),
            "draft": bool(release.get("draft")),
            "prerelease": bool(release.get("prerelease")),
            "revoked": cls._is_revoked(release),
            "release_notes_url": str(release.get("html_url") or ""),
            "upload_url": str(release.get("upload_url") or ""),
        }

    @classmethod
    def status_for_exception(cls, err: GitHubDiscoveryError) -> str:
        """Map a discovery failure to a truthful status (invariant 10)."""
        if err.code == "404":
            return STATUS_RELEASE_NOT_FOUND
        if err.code in ("403", "429"):
            return STATUS_GITHUB_UNAVAILABLE if err.code == "403" else STATUS_NETWORK_ERROR
        if err.code in ("500", "502", "503"):
            return STATUS_GITHUB_UNAVAILABLE
        if err.code:
            return STATUS_NETWORK_ERROR
        # No HTTP code: connection refused / DNS / timeout.  This means
        # the network (or GitHub) is unreachable — NEVER "no update" (spec 41).
        return STATUS_NETWORK_UNAVAILABLE


# ---------------------------------------------------------------------------
# Integrity primitives
# ---------------------------------------------------------------------------
class HashVerifier:
    """SHA-256 verification (invariant 1: unverified artifact cannot install)."""

    @staticmethod
    def verify_sha256(path: Path, expected: str) -> bool:
        try:
            actual = packaging.sha256_file(path)
        except OSError:
            return False
        return actual.lower() == str(expected).lower()

    @staticmethod
    def sha256_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()


class DigestResolver:
    """Resolves the authoritative SHA-256 digest of a release asset.

    GitHub release asset metadata carries NO checksum, so the digest must
    come from a checksum asset published ALONGSIDE the payload (spec 12).
    Resolution order (spec 44/46):

        1. Asset-level ``digest_sha256``/``sha256`` metadata (test feeds).
        2. A checksum asset of THIS release (sha256sums.txt / sha256.txt /
           *.sha256 / checksums.txt / digests.txt), parsed sha256sum-format;
           the payload lookup must be unique — ambiguous FAIL SAFE (spec 11).
        3. ``release_manifest`` embedded in asset metadata (build feed).

    A release with no resolvable digest stays SECURITY_BLOCKED (invariant 1).
    """

    @classmethod
    def _looks_like_checksum_asset(cls, name: str) -> bool:
        return bool(_CHECKSUM_ASSET_RE.search(name))

    @classmethod
    def _fetch_checksum_text(cls, asset: dict[str, Any], timeout: int = 60) -> str:
        url = asset.get("browser_download_url") or asset.get("url")
        if not url:
            return ""
        req = urllib.request.Request(url, headers={"User-Agent": UpdateDiscovery.USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")

    @classmethod
    def _parse_sha256sums(cls, text: str) -> dict[str, str]:
        """Parse sha256sum-format: '<hex>  <name>' per line.
        Path-relative names are indexed; base names also indexed."""
        out: dict[str, str] = {}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            digest, name = parts[0], parts[1].strip().lstrip("*")
            if len(digest) != 64:
                continue
            digest = digest.lower()
            for key in (name, name.rsplit("/", 1)[-1]):
                if key in out and out[key] != digest:
                    # Same payload listed twice with DIFFERENT digests
                    # (duplicate line in one checksum file): fail safe.
                    out[key] = "*CONFLICT*"
                else:
                    out[key] = digest
        return out

    @classmethod
    def resolve_from_release(
        cls,
        release: dict[str, Any],
        asset: dict[str, Any],
        *,
        timeout: int = 60,
    ) -> tuple[str | None, list[str]]:
        """Return (digest_or_None, decisions).  Ambiguity -> (None, ...)."""
        decisions: list[str] = []
        inline = str(asset.get("digest_sha256") or asset.get("sha256") or "").strip()
        if inline:
            decisions.append("digest from asset metadata")
            return inline.lower(), decisions
        checksum_assets = [
            a
            for a in release.get("assets", [])
            if cls._looks_like_checksum_asset(str(a.get("name", "")))
        ]
        if not checksum_assets:
            decisions.append("no checksum asset published for this release")
            return None, decisions
        target_name = str(asset.get("name", ""))
        base = target_name.rsplit("/", 1)[-1]
        matches: list[str] = []
        for ca in checksum_assets:
            try:
                txt = cls._fetch_checksum_text(ca, timeout=timeout)
            except Exception as e:
                decisions.append(f"checksum asset {ca.get('name')} unreadable: {e}")
                continue
            table = cls._parse_sha256sums(txt)
            hit = table.get(target_name) or table.get(base)
            if hit == "*CONFLICT*":
                decisions.append(
                    f"checksum asset {ca.get('name')} lists {base} with CONFLICTING digests — fail safe"
                )
                continue
            if hit:
                matches.append(hit)
                decisions.append(f"digest found in checksum asset {ca.get('name')}")
        if not matches:
            decisions.append("checksum assets present but payload not listed")
            return None, decisions
        uniq = sorted(set(matches))
        if len(uniq) != 1:
            decisions.append(
                f"conflicting digests across checksum assets ({len(uniq)} values) — fail safe"
            )
            return None, decisions
        return uniq[0], decisions

    @classmethod
    def resolve_from_upload_url(
        cls,
        upload_url: str,
        asset: dict[str, Any],
        *,
        timeout: int = 60,
    ) -> tuple[str | None, list[str]]:
        """GitHub uploads endpoint fallback (spec 46).
        Derives the release-assets API URL from upload_url and re-resolves."""
        decisions: list[str] = []
        if "{?name,label}" not in upload_url:
            return None, decisions
        api_assets = upload_url.split("{", 1)[0]  # .../releases/123/assets
        try:
            assets = cls._release_assets_json(api_assets, timeout=timeout)
        except Exception as e:
            decisions.append(f"release assets API unreadable: {e}")
            return None, decisions
        if not isinstance(assets, list):
            return None, decisions
        return cls.resolve_from_release({"assets": assets, "body": ""}, asset, timeout=timeout)

    @classmethod
    def _release_assets_json(cls, url: str, timeout: int = 20) -> Any:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": UpdateDiscovery.USER_AGENT,
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))


class ManifestVerifier:
    """Release-manifest verification (artifact list + hashes)."""

    @staticmethod
    def verify_manifest(manifest_path: Path, base_dir: Path | None = None) -> dict[str, Any]:
        return packaging.verify_manifest(manifest_path, base_dir)


# ---------------------------------------------------------------------------
# Compatibility gate (section 7)
# ---------------------------------------------------------------------------
class CompatibilityGate:
    """Deterministic COMPATIBLE / WARNING / BLOCKED gate, pre-download."""

    def check_disk_space(self, target_dir: Path, required_bytes: int) -> dict[str, Any]:
        try:
            usage = shutil.disk_usage(target_dir)
            free = usage.free
        except OSError as e:
            return {"verdict": "UNKNOWN", "reason": f"disk undetermined: {e}"}
        if free < required_bytes:
            return {
                "verdict": "BLOCKED",
                "reason": f"only {free // (1024**2)} MB free, need {required_bytes // (1024**2)} MB",
                "required_bytes": required_bytes,
                "free_bytes": free,
            }
        if free < required_bytes * 2:
            return {
                "verdict": "WARNING",
                "reason": f"only {free // (1024**2)} MB free after update",
                "required_bytes": required_bytes,
                "free_bytes": free,
            }
        return {"verdict": "PASS", "reason": "sufficient disk space", "free_bytes": free}

    def check(
        self,
        *,
        architecture: str,
        os_name: str,
        required_bytes: int,
        target_dir: Path,
        minimum_version: str | None,
        target_version: str,
        installed_version: str,
        installed_commit: str | None = None,
        target_commit: str | None = None,
    ) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        arch_l = (architecture or "").lower()
        if arch_l in ("arm64", "aarch64"):
            checks.append(
                {
                    "name": "architecture",
                    "verdict": "BLOCKED",
                    "reason": "windows ARM64 is unsupported by the dependency stack "
                    "(PyTorch/Polars/MetaTrader5 ship no ARM64 wheels)",
                }
            )
        elif arch_l not in ("x64", "amd64", "x86_64"):
            checks.append(
                {
                    "name": "architecture",
                    "verdict": "BLOCKED",
                    "reason": f"unknown arch {architecture}",
                }
            )
        else:
            checks.append({"name": "architecture", "verdict": "PASS", "reason": architecture})
        if "windows" not in (os_name or "").lower():
            checks.append(
                {"name": "os", "verdict": "BLOCKED", "reason": f"unsupported OS {os_name}"}
            )
        else:
            checks.append({"name": "os", "verdict": "PASS", "reason": os_name})
        disk = self.check_disk_space(target_dir, required_bytes)
        checks.append({"name": "disk", **disk})
        if minimum_version and compare_versions(installed_version, minimum_version) == -1:
            checks.append(
                {
                    "name": "minimum_version",
                    "verdict": "BLOCKED",
                    "reason": f"installed {installed_version} < minimum supported {minimum_version}",
                }
            )
        else:
            checks.append({"name": "minimum_version", "verdict": "PASS", "reason": "ok"})
        cmp = compare_versions(target_version, installed_version)
        if cmp is None:
            checks.append(
                {"name": "version", "verdict": "BLOCKED", "reason": "unparseable versions"}
            )
        elif cmp < 0:
            checks.append(
                {"name": "version", "verdict": "BLOCKED", "reason": "downgrade — never silent"}
            )
        elif cmp == 0 and installed_commit and target_commit and installed_commit != target_commit:
            checks.append(
                {
                    "name": "commit",
                    "verdict": "WARNING",
                    "reason": f"same version, different commits ({installed_commit} vs {target_commit})",
                }
            )
        else:
            checks.append(
                {
                    "name": "version",
                    "verdict": "PASS",
                    "reason": f"{installed_version} -> {target_version}",
                }
            )
        blocked = [c for c in checks if c["verdict"] == "BLOCKED"]
        warnings = [c for c in checks if c["verdict"] == "WARNING"]
        verdict = "BLOCKED" if blocked else ("WARNING" if warnings else "COMPATIBLE")
        return {"verdict": verdict, "checks": checks}


# ---------------------------------------------------------------------------
# Update plan (deterministic decision core)
# ---------------------------------------------------------------------------
class UpdatePlanBuilder:
    """Builds the update plan from a discovered release descriptor.

    Pure and offline-testable: all network I/O happens in UpdateDiscovery;
    all filesystem mutation happens in the orchestrator/installer stages.
    """

    def __init__(
        self,
        installed_version: str,
        channel: str = DEFAULT_CHANNEL,
        architecture: str | None = None,
        installed_commit: str | None = None,
        include_prerelease: bool = False,
        allow_downgrade: bool = False,
    ) -> None:
        self.installed_version = installed_version
        self.channel = channel if channel in SUPPORTED_CHANNELS else DEFAULT_CHANNEL
        # On non-Windows CI hosts (e.g. macos-latest ARM64) the machine arch is
        # ARM64 but the product only ships windows-x64. Defaulting to the host
        # arch would make every UpdatePlanBuilder() plan INCOMPATIBLE on macOS,
        # breaking the channel/digest/migration tests which are arch-agnostic.
        # Explicit architecture="ARM64" still gates correctly.
        if architecture is not None:
            self.architecture = architecture
        elif sys.platform == "win32":
            self.architecture = _machine_arch()
        else:
            self.architecture = "x64"
        self.installed_commit = installed_commit
        self.include_prerelease = include_prerelease
        self.allow_downgrade = allow_downgrade

    def build(self, release: dict[str, Any] | None) -> dict[str, Any]:
        decisions: list[str] = []
        base: dict[str, Any] = {
            "state": STATE_CHECKING,
            "status": STATUS_UNKNOWN,
            "channel": self.channel,
            "platform": SUPPORTED_PLATFORM,
            "architecture": self.architecture,
            "current_version": self.installed_version,
            "target_version": self.installed_version,
            "artifact_name": None,
            "artifact_sha256": None,
            "artifact_url": None,
            "artifact_size": None,
            "release_notes_url": None,
            "minimum_supported_version": None,
            "migration_required_from": None,
            "database_schema": None,
            "config_schema": None,
            "model_runtime_schema": None,
            "migration_required": False,
            "downgrade_blocked": False,
            "model_policy": (
                "UNCHANGED — application updates never promote or replace model artifacts; "
                "model updates require their own validated lifecycle"
            ),
            "decisions": decisions,
            "ready": False,
        }
        if release is None:
            base["status"] = STATUS_RELEASE_NOT_FOUND
            decisions.append("GitHub Releases API unreachable or empty — no update claim made")
            return base

        # 1. channel policy (never silently switch a stable user to beta/nightly)
        if self.channel == "stable" and release.get("prerelease") and not self.include_prerelease:
            decisions.append(
                f"{release.get('tag_name')} is a pre-release; stable channel refuses it "
                "(use --include-prerelease to opt in explicitly)"
            )
            base["status"] = STATUS_NO_UPDATE
            return base

        # 2. exact release identity locked BEFORE any download (spec 7)
        identity = UpdateDiscovery.release_identity(release)
        if identity["draft"]:
            decisions.append("release is a DRAFT — never eligible")
            base["status"] = STATUS_NO_UPDATE
            return base
        if identity["revoked"]:
            decisions.append(
                f"release {identity['tag']} is explicitly marked REVOKED — never installs, "
                "even though it is newer (spec section 47)"
            )
            base["status"] = STATUS_UPDATE_REJECTED
            return base
        base.update(identity)

        # 3. version identity
        tag = str(release.get("tag_name", "")).lstrip("v")
        if not tag:
            decisions.append("release descriptor missing tag_name")
            base["status"] = STATUS_RELEASE_NOT_FOUND
            return base
        cmp = compare_versions(tag, self.installed_version)
        if cmp is None:
            decisions.append(f"cannot compare versions {self.installed_version} vs {tag}")
            base["status"] = STATUS_INCOMPATIBLE
            return base
        if cmp < 0:
            if not self.allow_downgrade:
                decisions.append(
                    f"target {tag} is OLDER than installed {self.installed_version} — "
                    "downgrade blocked unless --allow-downgrade is explicit"
                )
                base["status"] = STATUS_NO_UPDATE
                base["downgrade_blocked"] = True
                return base
            decisions.append(
                f"target {tag} is OLDER than installed {self.installed_version} — "
                "--allow-downgrade explicit opt-in accepted; compatibility still verified"
            )
        if cmp == 0:
            decisions.append(f"already at {self.installed_version}; no newer release")
            base["status"] = STATUS_NO_UPDATE
            return base
        base["target_version"] = tag

        # 3. architecture gate (section 8: never download an incompatible artifact)
        if self.architecture.upper() in ("ARM64", "AARCH64", "ARM"):
            decisions.append(
                "windows ARM64 is UNSUPPORTED by the dependency stack — no compatible artifact exists"
            )
            base["status"] = STATUS_INCOMPATIBLE
            return base

        # 4. migration-path gate (section 31)
        min_ver = release.get("minimum_supported_version")
        mig_from = release.get("migration_required_from")
        base["minimum_supported_version"] = min_ver
        base["migration_required_from"] = mig_from
        if min_ver and compare_versions(self.installed_version, str(min_ver)) == -1:
            if mig_from and compare_versions(self.installed_version, str(mig_from)) == -1:
                decisions.append(
                    f"direct update from {self.installed_version} unsupported — "
                    f"staged path required: {self.installed_version} -> {mig_from} -> {tag}"
                )
                base["status"] = STATUS_DIRECT_UPDATE_UNSUPPORTED
                return base
            decisions.append(
                f"minimum supported version is {min_ver}; installed {self.installed_version}"
            )
            base["status"] = STATUS_INCOMPATIBLE
            return base

        # 5. asset selection — packaged payloads only, never source archives
        asset = self._select_asset(release, decisions)
        if asset is None:
            base["status"] = STATUS_INCOMPATIBLE
            return base
        base["artifact_name"] = str(asset.get("name"))
        base["artifact_url"] = asset.get("browser_download_url") or asset.get("url")
        base["artifact_size"] = asset.get("size")
        base["release_notes_url"] = release.get("html_url")

        # 6. digest resolution (spec 10/12: hash ABOVE everything else).
        #    GitHub asset metadata carries NO checksum — the digest MUST
        #    come from a published checksum asset / manifest (BUG-122).
        digest, digest_decisions = DigestResolver.resolve_from_release(release, asset)
        decisions.extend(digest_decisions)
        if not digest:
            decisions.append(
                "release asset lacks a resolvable SHA-256 digest — update will refuse "
                "(no silent fallback, spec section 66)"
            )
            base["status"] = STATUS_SECURITY_BLOCKED
            return base
        base["artifact_sha256"] = str(digest).lower()

        # 6b. SIGNED UPDATE MANIFEST (P0 trust root). The resolved digest is
        #     integrity evidence whose authority must come from a trusted
        #     Ed25519 signature over the canonical manifest. A release without
        #     a valid signed manifest is SECURITY_BLOCKED: payload+checksum
        #     replacement by a compromised publisher account can NEVER
        #     authorize an install (no silent unsigned fallback).
        signed_manifest = release.get("update_manifest") or {}
        try:
            sig_verdict = verify_payload_against_manifest(
                signed_manifest,
                None,  # plan stage: metadata-only (payload bound at download)
                expected_sha256=str(digest).lower(),
            )
        except UpdateManifestError as sig_err:
            decisions.append(
                f"signed update manifest REJECTED "
                f"({getattr(sig_err, 'reason', 'SIGNATURE_INVALID')}) "
                "— the signature is the trust root; refusing without it"
            )
            base["status"] = STATUS_SECURITY_BLOCKED
            base["signature_status"] = getattr(sig_err, "reason", "SIGNATURE_INVALID")
            return base
        base["signature_status"] = "SIGNED_MANIFEST_OK"
        base["signed_manifest_key_id"] = sig_verdict["key_id"]

        # 7. schema metadata from the attached release-manifest.json
        manifest = asset.get("release_manifest") or {}
        base["database_schema"] = manifest.get("database_schema")
        base["config_schema"] = manifest.get("config_schema")
        base["model_runtime_schema"] = manifest.get("model_runtime_schema")
        base["migration_required"] = bool(
            manifest.get("database_schema") or manifest.get("config_schema")
        )

        # 8. model/client compatibility tuple (spec 48/49).
        min_client = str(release.get("minimum_client_version") or "")
        min_model = str(
            release.get("minimum_model_version") or manifest.get("minimum_model_version") or ""
        )
        base["minimum_client_version"] = min_client or None
        base["minimum_model_version"] = min_model or None
        base["model_version"] = manifest.get("model_version")
        base["model_sha256"] = manifest.get("model_sha256")
        base["schema_version"] = manifest.get("schema_version")
        base["feature_dimension"] = manifest.get("feature_dimension")
        if min_client and compare_versions(self.installed_version, min_client) == -1:
            decisions.append(
                f"release requires client >= {min_client}; installed "
                f"{self.installed_version} — model/client matrix gate"
            )
            base["status"] = STATUS_INCOMPATIBLE
            return base

        decisions.append(f"release {tag} offers {base['artifact_name']} for {self.architecture}")
        decisions.append("Ed25519-signed manifest + SHA-256 verified before install")
        base["status"] = STATUS_UPDATE_AVAILABLE
        base["ready"] = True
        return base

    def _select_asset(self, release: dict[str, Any], decisions: list[str]) -> dict[str, Any] | None:
        assets = release.get("assets", [])
        packaged: list[dict[str, Any]] = []
        for a in assets:
            name = str(a.get("name", ""))
            if _SOURCE_ASSET_RE.search(name):
                decisions.append(f"asset {name} is a source archive — never a production payload")
                continue
            if "win-x64" in name or "windows-x64" in name:
                packaged.append(a)
        if not packaged:
            decisions.append(
                f"no packaged artifact for {self.architecture} on {release.get('tag_name')} — "
                "source archives are not used for end-user updates"
            )
            return None
        # BUG-237: prefer the PORTABLE ZIP over the Inno setup.exe - the zip
        # installs headless on every mode (portable/EXE/Inno) and carries
        # build-info.json + release-manifest.json for full verification; the
        # setup.exe is a secondary payload (GUI-launched, Inno-verified).
        for a in packaged:
            if str(a.get("name", "")).endswith(".zip"):
                return a
        for a in packaged:
            if "-setup.exe" in str(a.get("name", "")):
                return a
        return packaged[0]


def _machine_arch() -> str:
    import platform

    m = platform.machine().lower()
    if m in ("arm64", "aarch64"):
        return "ARM64"
    if m in ("x86_64", "amd64"):
        return "x64"
    return m or "unknown"
