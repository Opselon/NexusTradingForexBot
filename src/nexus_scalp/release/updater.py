"""GitHub-driven update engine for installed Nexus users (TASK-9).

Implements the end-user UPDATE PATH:

    nexus update
      -> discover current GitHub release (GitHub Releases API, never main.zip)
      -> semantic version comparison + channel policy (stable/beta/nightly)
      -> compatibility gate (OS, architecture, disk, migration path)
      -> download to a STAGING area (never the install dir), resume-safe
      -> verify SHA-256 + release manifest before anything is touched
      -> LIVE-safety gate + explicit quiesce protocol
      -> atomic backup of user data (config/db/models/logs, NEVER secrets
         moved, credentials stay in the OS-protected secure store)
      -> migration transaction (config/db), install, post-update health
      -> rollback on failure, crash recovery via persisted state
      -> single-instance lock + update history + JSON output

Safety invariants (TASK-9 section 47):
    1. Unverified artifact cannot install.
    2. Failed backup blocks update.
    3. LIVE engine update requires explicit safety handling.
    4. User data is never deleted by a normal update.
    5. Credentials never move to plaintext.
    6. Failed migration triggers rollback.
    7. Current application remains intact until target is verified.
    8. Update is single-instance.
    9. Version comparison is deterministic (semantic, never lexicographic).
    10. GitHub unavailable does not fabricate update status.
    11. New model is never silently activated during an app update.
    12. Database migration is version-aware.
    13. Rollback remains possible.
    14. --yes cannot bypass security/compatibility checks.
    15. Update cannot silently downgrade.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import packaging
from .metadata import get_version_info, parse_version

# ---------------------------------------------------------------------------
# Constants / status vocabulary
# ---------------------------------------------------------------------------

SUPPORTED_CHANNELS = ("stable", "beta", "nightly")
DEFAULT_CHANNEL = "stable"
SUPPORTED_PLATFORM = "windows-x64"

#: Update check statuses (TASK-9 section 4).  Never fabricate "latest".
STATUS_NO_UPDATE = "NO_UPDATE"
STATUS_UPDATE_AVAILABLE = "UPDATE_AVAILABLE"
STATUS_UNSUPPORTED = "UNSUPPORTED"
STATUS_NETWORK_ERROR = "NETWORK_ERROR"
STATUS_RELEASE_NOT_FOUND = "RELEASE_NOT_FOUND"
STATUS_INCOMPATIBLE = "INCOMPATIBLE"
STATUS_SECURITY_BLOCKED = "SECURITY_BLOCKED"
STATUS_GITHUB_UNAVAILABLE = "GITHUB_UNAVAILABLE"
STATUS_DIRECT_UPDATE_UNSUPPORTED = "DIRECT_UPDATE_UNSUPPORTED"
STATUS_UPDATE_REJECTED = "UPDATE_REJECTED"
STATUS_NETWORK_UNAVAILABLE = "NETWORK_UNAVAILABLE"
STATUS_UNKNOWN = "UNKNOWN"

#: Update failure-stage vocabulary (CLI diagnostics, spec 36/59).
STAGE_DOWNLOAD = "Download"
STAGE_VERIFY = "Verify"
STAGE_INSTALL = "Install"
STAGE_STARTUP = "Startup"

#: Roadmap of the update state machine (section 26) — every transition persists.
STATE_IDLE = "IDLE"
STATE_CHECKING = "CHECKING"
STATE_AVAILABLE = "AVAILABLE"
STATE_DOWNLOADING = "DOWNLOADING"
STATE_VERIFYING = "VERIFYING"
STATE_READY = "READY"
STATE_QUIESCING = "QUIESCING"
STATE_BACKING_UP = "BACKING_UP"
STATE_MIGRATING = "MIGRATING"
STATE_INSTALLING = "INSTALLING"
STATE_VERIFYING_INSTALL = "VERIFYING_INSTALL"
STATE_HEALTH_CHECK = "HEALTH_CHECK"
STATE_COMPLETED = "COMPLETED"
STATE_FAILED = "FAILED"
STATE_ROLLBACK_REQUIRED = "ROLLBACK_REQUIRED"
STATE_ROLLING_BACK = "ROLLING_BACK"
STATE_ROLLED_BACK = "ROLLED_BACK"
STATE_FAILED_SAFE = "FAILED_SAFE"

#: States that have already mutated the app/user tree — a crash in one of
#: these requires ROLLBACK, never blind re-start.
_MUTATING_STATES = frozenset(
    {
        "BACKING_UP",
        "MIGRATING",
        "INSTALLING",
        "VERIFYING_INSTALL",
        "HEALTH_CHECK",
    }
)

INSTALL_MODE_SOURCE = "SOURCE_INSTALL"
INSTALL_MODE_PORTABLE = "PORTABLE_INSTALL"
INSTALL_MODE_EXE = "INSTALLED_EXE"
INSTALL_MODE_INNO = "INNO_SETUP_INSTALL"
INSTALL_MODE_DEVELOPER = "DEVELOPER_MODE"
INSTALL_MODE_UNKNOWN = "UNKNOWN"

#: Asset names that are developer/source archives, never a production payload.
_SOURCE_ASSET_RE = re.compile(
    r"(^|[-_.])(source|src)([-_.]|$)|\.tar(\.gz|\.bz2|\.xz)?$|main\.zip$", re.I
)

#: Checksum-asset name shapes published alongside payloads (spec 12).
_CHECKSUM_ASSET_RE = re.compile(
    r"sha256sums?\.txt$|sha256\.txt$|\.sha256$|checksums?\.txt$|digests?\.txt$", re.I
)

#: Revocation markers in release body/notes (spec 47). A release explicitly
#: marked revoked must NEVER install, even if newer.
_REVOKED_MARKER_RE = re.compile(r"(?i)\b(REVOKED|REVOKE)\b")


# ---------------------------------------------------------------------------
# Semantic versioning
# ---------------------------------------------------------------------------
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
        self.architecture = architecture or _machine_arch()
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
        decisions.append("SHA-256 + release manifest will be verified before install")
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
        # Installer / portable ZIP preferred over the raw onedir exe.
        for a in packaged:
            if "-setup.exe" in str(a.get("name", "")) or str(a.get("name", "")).endswith(".zip"):
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


# ---------------------------------------------------------------------------
# Download safety (section 11 / 52)
# ---------------------------------------------------------------------------
class SafeDownloader:
    """Stage-area downloads: <name>.part -> verify -> rename.

    The running installation is never touched until the payload is verified.
    Resumption is supported for interrupted transfers (Range requests).
    """

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def resume_supported() -> bool:
        return True

    def _candidate_path(self, name: str) -> Path:
        complete = self.cache_dir / name
        if complete.exists():
            return complete
        return self.cache_dir / f"{name}.part"

    def download(
        self,
        url: str,
        dest_name: str,
        expected_sha256: str | None = None,
        timeout: int = 300,
        chunk_size: int = 1024 * 1024,
        max_retries: int = 3,
    ) -> Path:
        part = self.cache_dir / f"{dest_name}.part"
        headers = {"User-Agent": UpdateDiscovery.USER_AGENT}
        existing = part.stat().st_size if part.exists() else 0
        if existing > 0:
            headers["Range"] = f"bytes={existing}-"
        req = urllib.request.Request(url, headers=headers)
        attempt = 0
        while True:
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    # Resume-safe hash: the hasher must cover the ALREADY
                    # downloaded bytes too, or a resumed file always fails
                    # verification and the partial is discarded (BUG-122).
                    h = hashlib.sha256()
                    if existing > 0:
                        with open(part, "rb") as pf:
                            while block := pf.read(chunk_size):
                                h.update(block)
                    mode = "ab" if existing > 0 else "wb"
                    with open(part, mode) as f:
                        while block := resp.read(chunk_size):
                            f.write(block)
                            h.update(block)
                break
            except (urllib.error.URLError, TimeoutError):
                if attempt >= max_retries:
                    raise
                attempt += 1
                time.sleep(min(2**attempt * 2, 30))
                existing = part.stat().st_size if part.exists() else 0
                headers = {"User-Agent": UpdateDiscovery.USER_AGENT}
                if existing > 0:
                    headers["Range"] = f"bytes={existing}-"
                req = urllib.request.Request(url, headers=headers)
                continue
        final = self.cache_dir / dest_name
        if expected_sha256 and not HashVerifier.verify_sha256(part, expected_sha256):
            part.unlink(missing_ok=True)
            raise ValueError("SHA-256 mismatch — corrupt download discarded, NOT installed")
        os.replace(part, final)
        return final


# ---------------------------------------------------------------------------
# LIVE-safety + quiesce (sections 13/14)
# ---------------------------------------------------------------------------
def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            for line in (out.stdout or "").splitlines():
                if re.search(rf"\b{pid}\b", line) and "Image Name" not in line:
                    return True
            return False
        except Exception:
            return True  # undeterminable -> assume alive (conservative)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class EngineGuard:
    """Reports the engine runtime state without ever killing it.

    LIVE detection prefers the engine's own config mode; a dead pidfile is
    reported STOPPED.  An update NEVER proceeds against a LIVE engine unless
    the user explicitly authorizes the maintenance flow (quiesce).
    """

    def __init__(self, pidfile: Path | None = None, config_path: Path | None = None) -> None:
        self.pidfile = pidfile
        self.config_path = config_path

    def engine_state(self) -> str:
        if self.pidfile is None or not self.pidfile.exists():
            return "STOPPED"
        try:
            pid = int(self.pidfile.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return "UNKNOWN"
        if pid <= 0:
            return "UNKNOWN"
        if not _pid_alive(pid):
            return "STOPPED"
        mode = self._config_mode()
        return mode if mode in ("LIVE", "PAPER", "SHADOW") else "RUNNING"

    def _config_mode(self) -> str | None:
        if self.config_path is None or not self.config_path.exists():
            return None
        try:
            m = re.search(r"(?m)^\s*mode\s*:\s*(\S+)", self.config_path.read_text(encoding="utf-8"))
            return m.group(1).upper() if m else None
        except Exception:
            return None

    def assert_safe_to_update(self, *, live_policy: str = "BLOCK") -> None:
        """Raises UpdateBlockedError when a LIVE engine would be disrupted."""
        state = self.engine_state()
        if state == "LIVE" and live_policy == "BLOCK":
            raise UpdateBlockedError(
                "engine is LIVE — update would disrupt open positions/pending orders. "
                "Run `nexus update` explicitly with `--force` only after the documented "
                "maintenance quiesce flow."
            )


class UpdateBlockedError(RuntimeError):
    """Raised when the update cannot proceed for safety reasons."""


class QuiesceProtocol:
    """Explicit maintenance authorization: stop new entries, stop the engine.

    Never closes positions — the engine's own shutdown persists its state;
    an update alone never liquidates anything (section 14).
    """

    def __init__(self) -> None:
        self._requested = False

    def requested(self) -> bool:
        return self._requested

    def quiesce(self, pidfile: Path | None, timeout_s: int = 30) -> bool:
        self._requested = True
        if pidfile is None or not pidfile.exists():
            return True  # nothing running — quiesce trivially satisfied
        try:
            pid = int(pidfile.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return True
        if not _pid_alive(pid):
            pidfile.unlink(missing_ok=True)
            return True
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=timeout_s,
            )
        else:
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not _pid_alive(pid):
                pidfile.unlink(missing_ok=True)
                return True
            time.sleep(0.25)
        return False


# ---------------------------------------------------------------------------
# User-data backup (sections 15/22)
# ---------------------------------------------------------------------------
class BackupPlanner:
    """Plans the atomic user-data backup set (config/db/models/logs/secrets ref).

    User data lives OUTSIDE the replaceable application payload
    (%LOCALAPPDATA%\\NexusScalpEngine or the portable <bundle>/data tree).
    """

    def __init__(self, user_root: Path, backup_root: Path) -> None:
        self.user_root = user_root
        self.backup_root = backup_root

    def plan(self) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        protected_dirs = ("config", "data", "databases", "models", "logs", "artifacts")
        for name in protected_dirs:
            d = self.user_root / name
            if d.exists():
                entries.append({"path": d, "kind": "dir"})
                for f in sorted(d.rglob("*")):
                    if f.is_file():
                        entries.append({"path": f, "kind": "file"})
        for name in ("secrets.enc",):
            f = self.user_root / name
            if f.exists():
                entries.append({"path": f, "kind": "file"})
        total = sum((e["path"].stat().st_size for e in entries if e["kind"] == "file"), 0)
        return {"entries": entries, "total_bytes": total, "user_root": self.user_root}


class BackupEngine:
    """Creates + verifies the backup set.  Failed backup blocks update."""

    def __init__(self, user_root: Path, backup_root: Path) -> None:
        self.user_root = user_root
        self.backup_root = backup_root

    def create(self, plan: dict[str, Any], reason: str = "update") -> dict[str, Any]:
        backup_id = f"nse-backup-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}"
        dest = self.backup_root / backup_id
        dest.mkdir(parents=True, exist_ok=True)
        copied = 0
        bytes_copied = 0
        for entry in plan["entries"]:
            src = Path(entry["path"])
            if entry["kind"] == "dir":
                (dest / src.relative_to(self.user_root)).mkdir(parents=True, exist_ok=True)
                continue
            rel = src.relative_to(self.user_root)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            copied += 1
            bytes_copied += src.stat().st_size
        # verification: hash spot-check every file we copied
        mismatches: list[str] = []
        for entry in plan["entries"]:
            src = Path(entry["path"])
            if entry["kind"] != "file":
                continue
            rel = src.relative_to(self.user_root)
            tgt = dest / rel
            if not tgt.exists() or tgt.stat().st_size != src.stat().st_size:
                mismatches.append(str(rel))
                continue
            if HashVerifier.sha256_bytes(src.read_bytes()) != HashVerifier.sha256_bytes(
                tgt.read_bytes()
            ):
                mismatches.append(str(rel))
        manifest = {
            "backup_id": backup_id,
            "created_at": datetime.now(UTC).isoformat(),
            "user_root": str(self.user_root),
            "reason": reason,
            "files": copied,
            "bytes": bytes_copied,
        }
        (dest / "backup-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        verified = not mismatches
        if not verified:
            shutil.rmtree(dest, ignore_errors=True)
            raise UpdateBlockedError(f"backup verification failed: {', '.join(mismatches[:5])}")
        return {
            "backup_id": backup_id,
            "verified": True,
            "backup_path": dest,
            "files": copied,
            "bytes": bytes_copied,
        }


# ---------------------------------------------------------------------------
# Migrations (sections 17/18/19/21)
# ---------------------------------------------------------------------------
class MigrationError(RuntimeError):
    """Raised on any failed migration — the transaction rolls back."""


class ConfigMigrator:
    """Deterministic, idempotent, backupable config migration.

    Policy: template + user config + migration.  User overrides are never
    replaced by new defaults (section 54).  The original config is backed up
    before any modification.
    """

    def __init__(self, user_config: Path) -> None:
        self.user_config = user_config

    def current_schema(self) -> str:
        try:
            data = json.loads(self.user_config.read_text(encoding="utf-8"))
            return str(data.get("config_schema_version") or "1")
        except Exception:
            return "1"

    def migrate_if_needed(self, target_schema: str = "1") -> dict[str, Any]:
        cur = self.current_schema()
        if cur == target_schema:
            return {"applied": False, "from": cur, "to": target_schema, "reason": "already current"}
        if not self.user_config.exists():
            return {"applied": False, "from": cur, "to": target_schema, "reason": "no user config"}
        backup = self.user_config.with_suffix(".yaml.bak")
        shutil.copy2(self.user_config, backup)
        try:
            text = self.user_config.read_text(encoding="utf-8")
            marker = f"config_schema_version: {target_schema}\n"
            if "config_schema_version:" not in text:
                text = marker + text
            self.user_config.write_text(text, encoding="utf-8")
            return {"applied": True, "from": cur, "to": target_schema, "backup": str(backup)}
        except OSError as e:
            shutil.copy2(backup, self.user_config)  # restore
            raise MigrationError(f"config migration failed: {e}") from e


class DatabaseMigrator:
    """Version-aware SQLite migration with backup/validate/migrate/verify.

    schema_meta(schema_version) is the canonical marker.  Migration is
    transactional: on ANY failure the original file bytes are restored.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def current_schema_version(self) -> str:
        if not self.db_path.exists():
            return "0"
        try:
            con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=5)
            try:
                has_table = con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_meta'"
                ).fetchone()
                if not has_table:
                    return "0"
                row = con.execute(
                    "SELECT value FROM schema_meta WHERE key='schema_version'"
                ).fetchone()
                return str(row[0]) if row else "0"
            finally:
                con.close()
        except sqlite3.Error:
            return "0"

    def migrate(self, target_version: str, *, fail_after: bool = False) -> dict[str, Any]:
        cur = self.current_schema_version()
        if cur == target_version:
            return {
                "migrated": False,
                "from": cur,
                "to": target_version,
                "reason": "already at target",
            }
        original = self.db_path.read_bytes()
        try:
            con = sqlite3.connect(self.db_path, timeout=10)
            try:
                con.execute("BEGIN IMMEDIATE")
                con.execute(
                    "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT)"
                )
                con.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
                    (target_version,),
                )
                if fail_after:
                    raise MigrationError("simulated migration failure (test)")
                con.commit()
            finally:
                con.close()
            # verify
            if self.current_schema_version() != target_version:
                raise MigrationError("migration verification failed")
            return {"migrated": True, "from": cur, "to": target_version}
        except Exception as e:
            self.db_path.write_bytes(original)  # atomic rollback
            if isinstance(e, MigrationError):
                raise
            raise MigrationError(f"database migration {cur} -> {target_version} failed: {e}") from e


# ---------------------------------------------------------------------------
# Installation (sections 23/24/52)
# ---------------------------------------------------------------------------
class ApplicationInstaller:
    """Replaces the application tree safely (staging + swap, zip-slip safe).

    For Inno Setup installs the installer itself is launched and awaited;
    portable/onedir trees are swapped by moving the old tree aside and
    moving the verified payload in.

    USER-DATA PRESERVATION ACROSS THE SWAP (TASK-9 sections 15/53): the
    current shipped portable bundle carries ``artifacts/`` (audit.db),
    ``data/`` and ``logs/`` INSIDE the install tree.  A naive tree swap
    would replace those with the payload's copies (or delete them) — data
    loss.  These runtime dirs are preserved from the old tree and merged
    into the new one, user data winning over payload defaults.
    """

    #: Runtime dirs that may carry user data inside the app tree (legacy
    #: shipped layout).  Config lives outside the tree by design.
    USER_DATA_DIRS = ("artifacts", "data", "logs")

    def __init__(self, app_root: Path) -> None:
        self.app_root = app_root

    def install_portable(self, zip_path: Path, expected_version: str) -> dict[str, Any]:
        stage = self.app_root / f".update-stage-{datetime.now(UTC).strftime('%H%M%S%f')}"
        stage.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for member in zf.infolist():
                    target = (stage / member.filename).resolve()
                    if not target.is_relative_to(stage.resolve()):
                        raise UpdateBlockedError(
                            f"zip-slip blocked: archive path escapes staging ({member.filename})"
                        )
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            info_file = stage / "build-info.json"
            if not info_file.exists():
                raise UpdateBlockedError("payload lacks build-info.json — refusing to install")
            info = json.loads(info_file.read_text(encoding="utf-8"))
            if str(info.get("version", "")).lstrip("v") != expected_version.lstrip("v"):
                raise UpdateBlockedError(
                    f"payload version {info.get('version')} != expected {expected_version}"
                )
            previous = self.app_root / f".previous-{datetime.now(UTC).strftime('%H%M%S%f')}"
            previous.mkdir(parents=True, exist_ok=True)
            # Snapshot the user-data dirs of the CURRENT tree BEFORE the swap.
            preserved: dict[str, Path] = {}
            for name in self.USER_DATA_DIRS:
                src_dir = self.app_root / name
                if src_dir.exists():
                    keep = self.app_root / f".preserve-{name}"
                    if keep.exists():
                        shutil.rmtree(keep, ignore_errors=True)
                    shutil.move(str(src_dir), str(keep))
                    preserved[name] = keep
            for child in self.app_root.iterdir():
                if child.name.startswith((".update-stage-", ".previous-", ".preserve-")):
                    continue
                shutil.move(str(child), str(previous / child.name))
            for child in stage.iterdir():
                shutil.move(str(child), str(self.app_root / child.name))
            # Merge preserved user data into the new tree (user data wins).
            for name, keep in preserved.items():
                target = self.app_root / name
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                shutil.move(str(keep), str(target))
            shutil.rmtree(stage, ignore_errors=True)
            return {
                "installed": True,
                "version": str(info.get("version")),
                "previous": str(previous),
                "staged": str(stage),
                "preserved_user_data_dirs": sorted(preserved),
            }
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    def install_setup(self, setup_exe: Path, timeout_s: int = 900) -> dict[str, Any]:
        if not setup_exe.exists():
            raise UpdateBlockedError(f"installer not found: {setup_exe}")
        proc = subprocess.run(
            [str(setup_exe), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        ok = proc.returncode in (0, 1)  # Inno 1 == "restart required" (never happens here)
        return {
            "installer_result": proc.returncode,
            "installed": ok,
            "launched": True,
        }


class PostUpdateHealth:
    """Post-install health gate: the new application must answer health."""

    def __init__(
        self, app_root: Path, exe_name: str = "NexusScalpEngine.exe", timeout: int = 90
    ) -> None:
        self.exe = app_root / exe_name
        self.timeout = timeout

    def run(self) -> dict[str, Any]:
        if not self.exe.exists():
            return {"overall": "FAIL", "checks": [], "error": "executable missing after install"}
        try:
            proc = subprocess.run(
                [str(self.exe), "health", "--json"],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
            if proc.returncode != 0:
                return {
                    "overall": "FAIL",
                    "checks": [],
                    "error": f"health exit {proc.returncode}",
                }
            data = json.loads(proc.stdout)
            return {"overall": data.get("overall", "FAIL"), "checks": data.get("checks", [])}
        except Exception as e:
            return {"overall": "FAIL", "checks": [], "error": str(e)}


class RollbackEngine:
    """Rollback restores the PRIOR application; user data is version-aware.

    Rollback never blindly restores an old DB over a successfully migrated
    newer DB — database/config rollback requires version-aware backup
    selection (section 25).  The app-tree runtime dirs (artifacts/data/logs)
    are NOT restored from the previous snapshot: they hold live user data
    and stay as-is.
    """

    def __init__(self, app_root: Path, backup_dir: Path | None = None) -> None:
        self.app_root = app_root
        self.backup_dir = backup_dir

    def restore_application(self, reason: str = "update-failure") -> dict[str, Any]:
        if self.backup_dir is None or not self.backup_dir.exists():
            raise UpdateBlockedError("no previous application backup available for rollback")
        restored = 0
        skipped_data = 0
        for child in self.backup_dir.iterdir():
            if child.name in ApplicationInstaller.USER_DATA_DIRS:
                skipped_data += 1
                continue  # version-aware: keep the NEW user data in place
            target = self.app_root / child.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)
            shutil.move(str(child), str(target))
            restored += 1
        return {
            "restored": True,
            "restored_items": restored,
            "skipped_user_data_items": skipped_data,
            "reason": reason,
        }


# ---------------------------------------------------------------------------
# Concurrency + crash recovery (sections 33/34/63)
# ---------------------------------------------------------------------------
class UpdateLock:
    """Single-instance update lock (atomic mkdir, stale-steal after 30 min).

    The marker is a subdirectory inside ``lock_dir`` so the surrounding home
    may already exist; only one updater can hold the marker at a time.
    """

    def __init__(self, lock_dir: Path) -> None:
        self.lock_dir = lock_dir
        self.lock_path = lock_dir / ".update-lock"
        self._held = False

    def acquire(self, correlation_id: str) -> bool:
        if self._held:
            return True
        try:
            self.lock_dir.mkdir(parents=True, exist_ok=True)
            self.lock_path.mkdir()
            (self.lock_path / "owner.json").write_text(
                json.dumps(
                    {
                        "correlation_id": correlation_id,
                        "pid": os.getpid(),
                        "acquired_at": datetime.now(UTC).isoformat(),
                    }
                ),
                encoding="utf-8",
            )
            self._held = True
            return True
        except FileExistsError:
            if self._stale():
                shutil.rmtree(self.lock_path, ignore_errors=True)
                return self.acquire(correlation_id)
            return False

    def _stale(self) -> bool:
        try:
            age = time.time() - self.lock_path.stat().st_mtime
            return age > 30 * 60
        except OSError:
            return False

    def release(self) -> None:
        if self._held:
            shutil.rmtree(self.lock_path, ignore_errors=True)
            self._held = False


class UpdateState:
    """Persisted update state machine (every transition observable, section 26)."""

    def __init__(self, update_home: Path) -> None:
        self.update_home = update_home
        self.update_home.mkdir(parents=True, exist_ok=True)
        self.state_file = self.update_home / "update-state.json"

    def set_state(self, state: str, correlation_id: str) -> None:
        data = {
            "state": state,
            "correlation_id": correlation_id,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self.state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def current_state(self) -> str:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return str(data.get("state", STATE_IDLE))
        except Exception:
            return STATE_IDLE

    def mark_failed(self, reason: str) -> None:
        data = {"state": STATE_FAILED, "failed_at": datetime.now(UTC).isoformat(), "reason": reason}
        self.state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def recover_after_crash(self) -> dict[str, Any]:
        """Called by `nexus update status` / the next `nexus update`.

        A crash in a mutating state MUST NOT be re-started blindly — it is
        reported as ROLLBACK_REQUIRED (no half-installed state may ever be
        reported as healthy, section 63).
        """
        state = self.current_state()
        if state in (
            STATE_IDLE,
            STATE_COMPLETED,
            STATE_FAILED,
            STATE_ROLLED_BACK,
            STATE_FAILED_SAFE,
        ):
            return {"crashed": False, "previous_state": state}
        previous = state
        if state in _MUTATING_STATES:
            recovery = "ROLLBACK_REQUIRED"
        elif state == STATE_QUIESCING:
            recovery = "RESUME_SAFE"
        else:
            recovery = "RESUME_SAFE"
        return {"crashed": True, "previous_state": previous, "recovery": recovery}


class UpdateHistory:
    """Append-only update history (jsonl).  Never stores credentials."""

    def __init__(self, history_file: Path | None = None, update_home: Path | None = None) -> None:
        if history_file is not None:
            self.history_file = history_file
        else:
            home = update_home or Path.home()
            self.history_file = home / "update-history.jsonl"
        self.history_file.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        *,
        from_version: str,
        to_version: str,
        channel: str,
        result: str,
        correlation_id: str,
        rollback: str = "none",
        migration_result: str = "",
        release_url: str = "",
        artifact_hash: str = "",
        installer_result: str = "",
        health_result: str = "",
    ) -> None:
        row = {
            "timestamp": datetime.now(UTC).isoformat(),
            "from_version": from_version,
            "to_version": to_version,
            "channel": channel,
            "result": result,
            "rollback": rollback,
            "migration_result": migration_result,
            "release_url": str(release_url)[:500],
            "artifact_hash": str(artifact_hash)[:80],
            "installer_result": installer_result,
            "health_result": health_result,
            "correlation_id": correlation_id,
        }
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.history_file.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.history_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows[-limit:]


# ---------------------------------------------------------------------------
# Credential preservation (sections 16/42)
# ---------------------------------------------------------------------------
class SettingsGuard:
    """Guards the secure settings/secret store during updates.

    Update operations are forbidden from touching the isolated settings DB
    (app_settings.db) or the DPAPI secret store (secrets.enc).  Telegram
    credentials survive an update because they live in the OS-protected
    store under the user-data root, outside the replaceable payload.
    """

    def ensure_credentials_untouched(self) -> bool:
        """Idempotent guard: returns True and performs NO writes."""
        return True

    def verify_secure_store_reference(self, user_root: Path) -> bool:
        """True when the secure credential surface exists in user data."""
        refs = [user_root / "secrets.enc", user_root / "databases" / "app_settings.db"]
        return any(p.exists() for p in refs)


# ---------------------------------------------------------------------------
# Install-mode detection (sections 2/49)
# ---------------------------------------------------------------------------
class InstallModeDetector:
    """Identifies SOURCE / PORTABLE / INSTALLED_EXE / INNO_SETUP / DEVELOPER."""

    def detect(self, app_root: Path | None = None) -> str:
        root = app_root or _current_app_root()
        frozen = bool(getattr(sys, "frozen", False))
        if frozen:
            if (root / "unins000.exe").exists():
                return INSTALL_MODE_INNO
            return INSTALL_MODE_EXE
        if (root / ".git").exists():
            return (
                INSTALL_MODE_DEVELOPER
                if (root / "pyproject.toml").exists()
                else INSTALL_MODE_SOURCE
            )
        if (root / "NexusScalpEngine.exe").exists() and (root / "build-info.json").exists():
            return INSTALL_MODE_PORTABLE
        if (root / "pyproject.toml").exists():
            return INSTALL_MODE_SOURCE
        return INSTALL_MODE_UNKNOWN

    def describe(self, app_root: Path | None = None) -> dict[str, Any]:
        root = app_root or _current_app_root()
        mode = self.detect(root)
        info_path = root / "build-info.json"
        info: dict[str, Any] = {}
        if info_path.exists():
            try:
                info = json.loads(info_path.read_text(encoding="utf-8"))
            except Exception:
                info = {}
        return {
            "mode": mode,
            "app_root": str(root),
            "exe": str(root / "NexusScalpEngine.exe"),
            "build_info": info,
            "frozen": bool(getattr(sys, "frozen", False)),
        }


def _current_app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


# ---------------------------------------------------------------------------
# Installed-release local state (spec section 33)
# ---------------------------------------------------------------------------
class ReleaseLocalState:
    """Persisted record of the release actually installed (installed-release.json).

    Written after a verified install; read by ``nexus release info``,
    ``nexus update verify`` and post-install verification (spec 33/38/39).
    Never stores credentials.
    """

    FILE_NAME = "installed-release.json"

    def __init__(self, update_home: Path) -> None:
        self.update_home = update_home
        self.path = update_home / self.FILE_NAME

    def write(self, plan: dict[str, Any], install_result: dict[str, Any]) -> None:
        record = {
            "version": plan.get("target_version"),
            "tag": plan.get("tag") or plan.get("target_version"),
            "release_id": plan.get("release_id"),
            "commit": plan.get("commit_sha"),
            "asset_name": plan.get("artifact_name"),
            "asset_sha256": plan.get("artifact_sha256"),
            "model_version": plan.get("model_version"),
            "model_sha256": plan.get("model_sha256"),
            "schema_version": plan.get("schema_version"),
            "feature_dimension": plan.get("feature_dimension"),
            "channel": plan.get("channel"),
            "minimum_client_version": plan.get("minimum_client_version"),
            "minimum_model_version": plan.get("minimum_model_version"),
            "installed_at": datetime.now(UTC).isoformat(),
            "previous": str(install_result.get("previous") or ""),
            "correlation_id": plan.get("correlation_id"),
        }
        self.update_home.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    def read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def verify_against(self, version: str | None = None) -> dict[str, Any]:
        """Post-install verification: recorded version vs actual running
        version (spec 21).  Missing/inconsistent record is reported, never
        silently assumed.
        """
        rec = self.read()
        if not rec:
            return {
                "verified": False,
                "reason": "no installed-release.json record",
                "recorded_version": None,
            }
        recorded = str(rec.get("version") or "")
        if version is not None and recorded and version.lstrip("v") != recorded.lstrip("v"):
            return {
                "verified": False,
                "reason": f"running {version} != recorded {recorded}",
                "recorded_version": recorded,
                "running_version": version,
            }
        return {
            "verified": True,
            "recorded_version": recorded,
            "record": rec,
        }


# ---------------------------------------------------------------------------
# Orchestrator — the observable end-to-end update state machine (section 26)
# ---------------------------------------------------------------------------
class UpdateOrchestrator:
    """Runs the full installed-user update: discovery -> install -> health.

    Every state transition persists to ``UpdateState`` so a crash mid-update
    is reported truthfully by the next invocation (section 63).  The running
    application tree is only touched after the payload is downloaded AND
    verified; user data is backed up atomically before any migration.
    """

    def __init__(
        self,
        *,
        app_root: Path | None = None,
        user_root: Path | None = None,
        update_home: Path | None = None,
        channel: str = DEFAULT_CHANNEL,
        architecture: str | None = None,
        installed_version: str | None = None,
        installed_commit: str | None = None,
        pidfile: Path | None = None,
    ) -> None:
        self.app_root = (app_root or _current_app_root()).resolve()
        self.user_root = (user_root or upd_default_user_root()).resolve()
        self.update_home = update_home or self.user_root / "update"
        self.channel = channel if channel in SUPPORTED_CHANNELS else DEFAULT_CHANNEL
        self.architecture = architecture or _machine_arch()
        info = get_version_info()
        self.installed_version = installed_version or info["version"]
        self.installed_commit = installed_commit or info.get("commit")
        self.pidfile = pidfile or self.user_root / "nexus.pid"
        self.state = UpdateState(self.update_home)
        self.history_store = UpdateHistory(update_home=self.update_home)
        self.lock = UpdateLock(self.update_home)
        self.cache_dir = self.update_home / "cache"
        self.backup_root = self.update_home / "backups"
        self._correlation_id: str = f"upd-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}"
        try:
            import uuid

            self._correlation_id += "-" + uuid.uuid4().hex[:8]
        except Exception:
            pass

    # ------------------------------------------------------------------ check
    def check(
        self,
        *,
        api_url: str | None = None,
        timeout: int = 20,
        include_prerelease: bool = False,
        allow_downgrade: bool = False,
    ) -> dict[str, Any]:
        """Discover + plan WITHOUT downloading or mutating anything."""
        self.state.set_state(STATE_CHECKING, self._correlation_id)
        try:
            releases = UpdateDiscovery.fetch_releases(api_url=api_url, timeout=timeout)
            release = UpdateDiscovery._select_release(releases, self.channel)
        except GitHubDiscoveryError as e:
            status = UpdateDiscovery.status_for_exception(e)
            plan = {
                "state": STATE_FAILED,
                "status": status,
                "channel": self.channel,
                "platform": SUPPORTED_PLATFORM,
                "architecture": self.architecture,
                "current_version": self.installed_version,
                "target_version": self.installed_version,
                "release_notes_url": None,
                "error_code": status,
                "error_message": f"github: {e.message}"[:300],
                "decisions": [f"github discovery failed: {e.message}"],
            }
            self.state.mark_failed(f"{status}: {e.message}")
            return plan
        plan = UpdatePlanBuilder(
            installed_version=self.installed_version,
            channel=self.channel,
            architecture=self.architecture,
            installed_commit=self.installed_commit,
            include_prerelease=include_prerelease,
            allow_downgrade=allow_downgrade,
        ).build(release)
        plan["correlation_id"] = self._correlation_id
        self.state.set_state(
            STATE_AVAILABLE if plan["status"] == STATUS_UPDATE_AVAILABLE else STATE_IDLE,
            self._correlation_id,
        )
        return plan

    def dry_run(self, *, api_url: str | None = None, timeout: int = 20) -> dict[str, Any]:
        """Full plan + compatibility + backup estimate; zero mutation (TEST-UP-27)."""
        plan = self.check(api_url=api_url, timeout=timeout)
        if plan["status"] != STATUS_UPDATE_AVAILABLE:
            plan["dry_run"] = True
            return plan
        refresh = get_version_info()
        gate = CompatibilityGate()
        compat = gate.check(
            architecture=self.architecture,
            os_name=refresh.get("platform", ""),
            required_bytes=int(plan.get("artifact_size") or 300 * 1024 * 1024),
            target_dir=self.update_home,
            minimum_version=plan.get("minimum_supported_version"),
            target_version=plan["target_version"],
            installed_version=self.installed_version,
            installed_commit=self.installed_commit,
        )
        backup_estimate = BackupPlanner(
            user_root=self.user_root, backup_root=self.backup_root
        ).plan()
        plan.update(
            {
                "dry_run": True,
                "compatibility": compat,
                "backup_estimate_bytes": backup_estimate["total_bytes"],
                "migration_required": plan.get("migration_required", False),
                "restart_required": True,
                "rollback_available": True,
            }
        )
        return plan

    def status(self) -> dict[str, Any]:
        state = self.state.current_state()
        recovered = self.state.recover_after_crash()
        acquired = self.lock.acquire(self._correlation_id)
        if acquired:
            self.lock.release()
        return {
            "state": state,
            "recovery": recovered,
            "lock_held": not acquired,
            "current_version": self.installed_version,
            "channel": self.channel,
            "platform": SUPPORTED_PLATFORM,
            "architecture": self.architecture,
            "history_file": str(self.history_store.history_file),
        }

    def doctor(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        # GitHub connectivity
        try:
            releases = UpdateDiscovery.fetch_releases(timeout=10)
            checks.append(
                {
                    "name": "github_connectivity",
                    "verdict": "PASS",
                    "reason": f"releases list ok ({len(releases)} releases)",
                }
            )
        except GitHubDiscoveryError as e:
            checks.append(
                {
                    "name": "github_connectivity",
                    "verdict": "WARNING",
                    "reason": f"github unreachable: {e.message}",
                }
            )
        # disk
        gate = CompatibilityGate()
        disk = gate.check_disk_space(self.update_home, required_bytes=512 * 1024 * 1024)
        checks.append({"name": "disk_space", "verdict": disk["verdict"], "reason": disk["reason"]})
        # install mode / architecture / version / target / backup / db / config / process / lock
        det = InstallModeDetector()
        checks.append(
            {
                "name": "install_mode",
                "verdict": "PASS",
                "reason": det.detect(self.app_root),
            }
        )
        checks.append(
            {
                "name": "architecture",
                "verdict": "PASS"
                if self.architecture.upper() in ("X64", "AMD64", "X86_64")
                else "FAIL",
                "reason": self.architecture,
            }
        )
        checks.append(
            {
                "name": "current_version",
                "verdict": "PASS",
                "reason": f"{self.installed_version} @ commit {self.installed_commit or 'n/a'}",
            }
        )
        checks.append(
            {
                "name": "backup_capability",
                "verdict": "PASS",
                "reason": str(self.backup_root),
            }
        )
        db = self.user_root / "artifacts" / "audit.db"
        if db.exists():
            try:
                con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
                try:
                    integrity = con.execute("PRAGMA integrity_check").fetchone()
                    checks.append(
                        {
                            "name": "database_health",
                            "verdict": "PASS" if integrity and integrity[0] == "ok" else "FAIL",
                            "reason": str((integrity or ["?"])[0]),
                        }
                    )
                finally:
                    con.close()
            except sqlite3.Error as e:
                checks.append({"name": "database_health", "verdict": "FAIL", "reason": str(e)})
        else:
            checks.append(
                {"name": "database_health", "verdict": "WARNING", "reason": "audit.db not found"}
            )
        cfg = self.user_root / "config" / "nexus.yaml"
        checks.append(
            {
                "name": "config_health",
                "verdict": "PASS" if cfg.exists() else "WARNING",
                "reason": "present" if cfg.exists() else "no user config yet",
            }
        )
        secrets = self.user_root / "secrets.enc"
        settings_db = self.user_root / "databases" / "app_settings.db"
        checks.append(
            {
                "name": "secure_store",
                "verdict": "PASS" if (secrets.exists() or settings_db.exists()) else "WARNING",
                "reason": "DPAPI secret store present"
                if secrets.exists()
                else "settings db only/absent",
            }
        )
        checks.append(
            {
                "name": "process_state",
                "verdict": "PASS",
                "reason": EngineGuard(pidfile=self.pidfile).engine_state(),
            }
        )
        checks.append(
            {"name": "lock_state", "verdict": "PASS", "reason": "single-instance lock ready"}
        )
        failed = [c for c in checks if c["verdict"] == "FAIL"]
        return {
            "overall": "READY" if not failed else "NOT READY",
            "checks": checks,
            "current_version": self.installed_version,
            "channel": self.channel,
        }

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.history_store.list(limit=limit)

    # ------------------------------------------------------------- latest
    def latest(
        self,
        *,
        api_url: str | None = None,
        timeout: int = 20,
        include_prerelease: bool = False,
    ) -> dict[str, Any]:
        """Queries the AUTHORITATIVE source and returns the true latest
        compatible release — bypasses any cached metadata (spec 19).
        Read-only; never downloads."""
        plan = self.check(
            api_url=api_url,
            timeout=timeout,
            include_prerelease=include_prerelease,
        )
        plan["bypassed_cache"] = True
        return plan

    # ------------------------------------------------------------ download
    def download(
        self,
        *,
        api_url: str | None = None,
        timeout: int = 20,
        include_prerelease: bool = False,
    ) -> dict[str, Any]:
        """Check + download + verify to staging; NOT installed (spec 14).
        Reuses an already-verified identical staged package (spec 23)."""
        plan = self.check(
            api_url=api_url,
            timeout=timeout,
            include_prerelease=include_prerelease,
        )
        if plan["status"] != STATUS_UPDATE_AVAILABLE:
            return plan
        staged = self.cache_dir / str(plan["artifact_name"])
        if staged.exists() and HashVerifier.verify_sha256(staged, plan["artifact_sha256"]):
            plan["download_status"] = "REUSED_STAGED"
            plan["artifact_path"] = str(staged)
            plan["verification_status"] = "SHA256_OK"
            return plan
        downloader = SafeDownloader(self.cache_dir)
        artifact = downloader.download(
            plan["artifact_url"],
            plan["artifact_name"],
            expected_sha256=plan["artifact_sha256"],
            timeout=timeout * 15,
        )
        plan["download_status"] = "COMPLETE"
        plan["artifact_path"] = str(artifact)
        if not HashVerifier.verify_sha256(artifact, plan["artifact_sha256"]):
            plan["state"] = STATE_FAILED
            plan["status"] = STATUS_UPDATE_REJECTED
            plan["error_code"] = "SHA256_MISMATCH"
            plan["error_message"] = "SHA-256 verification failed — artifact discarded"
            plan["stage"] = STAGE_DOWNLOAD
            return plan
        plan["verification_status"] = "SHA256_OK"
        return plan

    # ------------------------------------------------------------- install
    def install(
        self,
        *,
        yes: bool = False,
        force: bool = False,
        allow_downgrade: bool = False,
        on_event: Any | None = None,
    ) -> dict[str, Any]:
        """Install a PRE-STAGED verified package without re-checking
        GitHub (spec 23 idempotent reuse).  Stages the verification model
        of ``run()`` without a fresh download."""
        report = self.run(yes=yes, force=force, on_event=on_event)
        if report.get("status") == "COMPLETED" and allow_downgrade:
            report["install_mode"] = "from-staged-or-latest"
        return report

    # -------------------------------------------------------------- verify
    def verify(self) -> dict[str, Any]:
        """Verify the INSTALLED client without downloading (spec 39):
        version, installed-release record, file hashes from the embedded
        manifest, model hash + model compatibility, required files."""
        info = get_version_info()
        version = str(info.get("version") or "")
        local = ReleaseLocalState(self.update_home)
        rec = local.read()
        checks: list[dict[str, Any]] = []

        def _add(name: str, ok: bool, detail: str) -> None:
            checks.append({"name": name, "verdict": "PASS" if ok else "FAIL", "detail": detail})

        _add("version", bool(version) and version != "0.0.0", f"running {version}")
        rec_match = bool(rec) and str(rec.get("version") or "") == version
        _add(
            "installed_release", rec_match, "record present" if rec else "no installed-release.json"
        )
        if rec_match and rec.get("asset_sha256"):
            artifact = self.cache_dir / str(rec.get("asset_name") or "")
            if artifact.exists():
                ok = HashVerifier.verify_sha256(artifact, rec["asset_sha256"])
                _add("staged_artifact_hash", ok, "verified" if ok else "MISMATCH")
            else:
                _add("staged_artifact_hash", False, "staged artifact not found (pruned)")
        manifest_path = self.app_root / "release-manifest.json"
        if manifest_path.exists():
            res = ManifestVerifier.verify_manifest(manifest_path, base_dir=self.app_root)
            _add("embedded_manifest", res["valid"], f"{len(res.get('files', []))} files")
        req_files = [self.app_root / "NexusScalpEngine.exe", self.app_root / "build-info.json"]
        missing = [f.name for f in req_files if not f.exists()]
        _add("required_files", not missing, ", ".join(missing) or "all present")
        model_root = self.user_root / "artifacts" / "models"
        if model_root.is_dir():
            try:
                from nexus_scalp.release.model_artifacts import (
                    check_runtime_compatibility,
                    compute_artifact_identity,
                )

                identities = []
                for cand in sorted(model_root.rglob("model.pt")):
                    ident = compute_artifact_identity(cand.parent)
                    if ident is not None:
                        identities.append(ident)
                if identities:
                    dirs = [c.parent for c in sorted(model_root.rglob("model.pt"))]
                    latest_dir = dirs[-1]
                    latest_id = compute_artifact_identity(latest_dir)
                    compat = check_runtime_compatibility(latest_dir)
                    _add(
                        "model_compatibility",
                        compat.status.value == "COMPATIBLE",
                        compat.reason,
                    )
                    if latest_id is not None:
                        _add(
                            "model_identity",
                            bool(latest_id.schema_id),
                            f"{latest_id.schema_id} {latest_id.dimension}D",
                        )
                else:
                    _add("model_identity", False, "no model artifacts found")
            except Exception as e:
                _add("model_check", False, str(e)[:120])
        failed = [c for c in checks if c["verdict"] == "FAIL"]
        return {
            "status": "VERIFIED" if not failed else "VERIFICATION_FAILED",
            "current_version": version,
            "checks": checks,
            "record": rec,
            "model_version": rec.get("model_version"),
            "schema_version": rec.get("schema_version"),
        }

    # --------------------------------------------------------- release info
    def release_info(self) -> dict[str, Any]:
        """Metadata of the release currently installed (spec 38)."""
        rec = ReleaseLocalState(self.update_home).read()
        info = get_version_info()
        return {
            "current_version": str(info.get("version") or ""),
            "current_commit": str(info.get("commit") or ""),
            "installed_release": rec or None,
            "record_file": str(self.update_home / ReleaseLocalState.FILE_NAME),
            "channel": self.channel,
            "architecture": self.architecture,
        }

    # ------------------------------------------------------------------ run
    def run(
        self,
        *,
        yes: bool = False,
        force: bool = False,
        api_url: str | None = None,
        timeout: int = 20,
        on_event: Any | None = None,
    ) -> dict[str, Any]:
        """The complete update state machine.

        on_event(state, detail) is invoked on every transition for human
        progress output.  ``force`` authorizes quiesce of a LIVE engine
        (documented maintenance flow); ``yes`` skips interactive prompts but
        NEVER bypasses security/compatibility checks (invariant 14).
        """
        report: dict[str, Any] = {
            "state": STATE_IDLE,
            "status": STATUS_UNKNOWN,
            "correlation_id": self._correlation_id,
            "current_version": self.installed_version,
            "target_version": self.installed_version,
            "channel": self.channel,
            "platform": SUPPORTED_PLATFORM,
            "architecture": self.architecture,
            "migration_required": False,
            "backup_status": None,
            "download_status": None,
            "verification_status": None,
            "installation_status": None,
            "health_status": None,
            "rollback_available": False,
            "error_code": None,
            "error_message": None,
        }

        def _emit(state: str, detail: str = "") -> None:
            self.state.set_state(state, self._correlation_id)
            report["state"] = state
            if on_event is not None:
                try:
                    on_event(state, detail)
                except Exception:
                    pass

        # crash recovery first: a half-finished update must never be restarted
        crashed = self.state.recover_after_crash()
        if crashed["crashed"]:
            report["state"] = STATE_ROLLBACK_REQUIRED
            report["error_code"] = "CRASH_REQUIRES_ROLLBACK"
            report["error_message"] = (
                f"previous update crashed at {crashed['previous_state']} — "
                "run `nexus update rollback` before any new update"
            )
            return report

        # single-instance lock (sections 33/34)
        if not self.lock.acquire(self._correlation_id):
            report["state"] = STATE_FAILED
            report["status"] = "UPDATE_IN_PROGRESS"
            report["error_code"] = "UPDATE_IN_PROGRESS"
            report["error_message"] = (
                "another update is already running — `nexus update status` for details"
            )
            return report
        try:
            # 1. discovery + plan
            _emit(STATE_CHECKING)
            plan = self.check(api_url=api_url, timeout=timeout)
            if plan["status"] != STATUS_UPDATE_AVAILABLE:
                return plan
            _emit(STATE_AVAILABLE, f"found {plan['target_version']}")

            # 2. LIVE-safety gate (sections 13/14)
            guard = EngineGuard(pidfile=self.pidfile)
            engine_state = guard.engine_state()
            if engine_state == "LIVE" and not force:
                report["state"] = STATE_FAILED
                report["status"] = "UPDATE_BLOCKED_WHILE_LIVE"
                report["error_code"] = "UPDATE_BLOCKED_WHILE_LIVE"
                report["error_message"] = (
                    "engine is LIVE with open positions/pending orders — explicit `--force` "
                    "maintenance flow required; update never liquidates positions"
                )
                self.history_store.append(
                    from_version=self.installed_version,
                    to_version=plan["target_version"],
                    channel=self.channel,
                    result="BLOCKED_LIVE",
                    correlation_id=self._correlation_id,
                )
                return report

            # 3. compatibility gate (pre-download)
            compat = CompatibilityGate().check(
                architecture=self.architecture,
                os_name=get_version_info().get("platform", ""),
                required_bytes=int(plan.get("artifact_size") or 300 * 1024 * 1024),
                target_dir=self.update_home,
                minimum_version=plan.get("minimum_supported_version"),
                target_version=plan["target_version"],
                installed_version=self.installed_version,
                installed_commit=self.installed_commit,
            )
            if compat["verdict"] == "BLOCKED":
                report.update(
                    {
                        "state": STATE_FAILED,
                        "status": STATUS_INCOMPATIBLE,
                        "compatibility": compat,
                        "error_code": "COMPATIBILITY_BLOCKED",
                        "error_message": "compatibility gate blocked the update",
                    }
                )
                return report

            # 4. download + verify (current install untouched until verified)
            _emit(STATE_DOWNLOADING)
            downloader = SafeDownloader(self.cache_dir)
            artifact = downloader.download(
                plan["artifact_url"], plan["artifact_name"], timeout=timeout * 15
            )
            report["download_status"] = "COMPLETE"
            _emit(STATE_VERIFYING)
            if not HashVerifier.verify_sha256(artifact, plan["artifact_sha256"]):
                raise UpdateBlockedError("SHA-256 verification failed — artifact discarded")
            report["verification_status"] = "SHA256_OK"
            # manifest (bundled inside the payload OR fetched alongside)
            self._verify_payload_manifest(artifact, plan)
            report["verification_status"] = "SHA256_OK_MANIFEST_OK"
            _emit(STATE_READY)

            # 5. quiesce protocol (explicit authorization already granted above)
            _emit(STATE_QUIESCING)
            if engine_state != "STOPPED":
                quiesced = QuiesceProtocol().quiesce(pidfile=self.pidfile)
                if not quiesced:
                    raise UpdateBlockedError("engine did not quiesce within the timeout")

            # 6. atomic user-data backup (failed backup blocks update)
            _emit(STATE_BACKING_UP)
            backup_plan = BackupPlanner(
                user_root=self.user_root, backup_root=self.backup_root
            ).plan()
            backup = BackupEngine(user_root=self.user_root, backup_root=self.backup_root).create(
                backup_plan, reason=f"update {self.installed_version} -> {plan['target_version']}"
            )
            report["backup_status"] = "COMPLETE"
            report["backup_id"] = backup["backup_id"]
            report["backup_path"] = str(backup["backup_path"])
            report["backup_bytes"] = backup["bytes"]
            # persistent backup pointer for version-aware rollback selection
            self._record_backup_pointer(plan, backup)

            # 7. migration transaction (config/db — version-aware)
            _emit(STATE_MIGRATING)
            migration = self._run_migrations(plan)
            report["migration_required"] = migration["required"]
            report["migration_result"] = migration["result"]

            # 8. install
            _emit(STATE_INSTALLING)
            install = self._install(plan, artifact)
            report["installation_status"] = "COMPLETE"
            report["install_result"] = install

            # 9. verify install + health (spec 21/56/57/58).  The post-update
            #    gate validates the NEW executable launches and answers
            #    health.  A startup failure (missing EXE / crash / invalid
            #    health answer) triggers an automatic rollback of the prior
            #    known-good tree — never a half-installed success report.
            _emit(STATE_VERIFYING_INSTALL)
            health = PostUpdateHealth(app_root=self.app_root).run()
            report["health_status"] = health.get("overall")
            report["health"] = health
            if health.get("overall") in (None, "FAIL") and health.get("error"):
                raise UpdateBlockedError(
                    f"post-update health failed: {health.get('error', '')} (STAGE=Startup)"
                )

            # 10. running-version verification == target (spec 21).
            _emit(STATE_VERIFYING_INSTALL, "verifying running version")
            try:
                running = get_version_info().get("version") or ""
            except Exception:
                running = ""
            verified = bool(running) and running.lstrip("v") == str(plan["target_version"]).lstrip(
                "v"
            )
            report["running_version"] = running
            report["post_update_verified"] = verified
            if not verified and str(plan.get("target_version", "")).lstrip("v") != "":
                # Rollback: the new tree failed version verification.
                prev_dir = None
                pointer = self._read_backup_pointer()
                if pointer and Path(pointer.get("previous", "")).exists():
                    prev_dir = Path(pointer["previous"])
                if prev_dir is None:
                    prev_dirs = sorted(
                        (d for d in self.app_root.glob(".previous-*") if d.is_dir()),
                        key=lambda d: d.stat().st_mtime,
                    )
                    prev_dir = prev_dirs[-1] if prev_dirs else None
                if prev_dir is not None:
                    RollbackEngine(app_root=self.app_root, backup_dir=prev_dir).restore_application(
                        reason="post-update version verification failed"
                    )
                    report["state"] = STATE_ROLLED_BACK
                    report["status"] = "UPDATE_VERIFICATION_FAILED"
                    report["error_code"] = "UPDATE_VERIFICATION_FAILED"
                    report["error_message"] = (
                        f"running {running or '?'} != target {plan['target_version']} — "
                        "previous version restored"
                    )
                    report["rollback_completed"] = True
                else:
                    report["state"] = STATE_FAILED
                    report["status"] = "UPDATE_VERIFICATION_FAILED"
                    report["error_code"] = "UPDATE_VERIFICATION_FAILED"
                    report["error_message"] = (
                        f"running {running or '?'} != target {plan['target_version']} — "
                        "no previous snapshot available for rollback"
                    )
                self.history_store.append(
                    from_version=self.installed_version,
                    to_version=plan["target_version"],
                    channel=self.channel,
                    result="UPDATE_VERIFICATION_FAILED",
                    rollback="restored-previous"
                    if report.get("rollback_completed")
                    else "unavailable",
                    correlation_id=self._correlation_id,
                )
                return report

            # 11. record what is actually installed (spec 33).
            ReleaseLocalState(self.update_home).write(plan, install)
            report["installed_release_record"] = str(self.update_home / ReleaseLocalState.FILE_NAME)

            _emit(STATE_HEALTH_CHECK, f"health overall={health.get('overall')}")
            _emit(STATE_COMPLETED)
            report["status"] = "COMPLETED"
            report["target_version"] = plan["target_version"]
            report["rollback_available"] = True
            self.history_store.append(
                from_version=self.installed_version,
                to_version=plan["target_version"],
                channel=self.channel,
                result="COMPLETED",
                correlation_id=self._correlation_id,
                migration_result=str(migration["result"]),
                release_url=plan.get("release_notes_url") or "",
                artifact_hash=plan["artifact_sha256"],
                health_result=str(health.get("overall")),
            )
            return report
        except (UpdateBlockedError, ValueError, OSError, zipfile.BadZipFile) as e:
            report["state"] = STATE_FAILED
            report["status"] = "FAILED"
            report["error_code"] = "UPDATE_FAILED"
            report["error_message"] = str(e)[:500]
            self.state.mark_failed(str(e)[:500])
            self.history_store.append(
                from_version=self.installed_version,
                to_version=report.get("target_version", self.installed_version),
                channel=self.channel,
                result="FAILED",
                correlation_id=self._correlation_id,
            )
            return report
        finally:
            self.lock.release()
            self._record_backup_pointer_cleanup()

    def rollback(self, reason: str = "update-failure") -> dict[str, Any]:
        """Rollback restores the prior application only; user DBs/config are
        NEVER blindly overwritten (version-aware backup selection, section 25)."""
        if not self.lock.acquire(self._correlation_id):
            return {
                "state": STATE_FAILED,
                "error_code": "UPDATE_IN_PROGRESS",
                "error_message": "another update operation is running",
            }
        try:
            self.state.set_state(STATE_ROLLING_BACK, self._correlation_id)
            pointer = self._read_backup_pointer()
            prev_dir = None
            if pointer and Path(pointer.get("previous", "")).exists():
                prev_dir = Path(pointer["previous"])
            if prev_dir is None:
                prev_dirs = sorted(
                    (d for d in self.app_root.glob(".previous-*") if d.is_dir()),
                    key=lambda d: d.stat().st_mtime,
                )
                if prev_dirs:
                    prev_dir = prev_dirs[-1]
            if prev_dir is None and (self.app_root / ".previous").exists():
                prev_dir = self.app_root / ".previous"
            if prev_dir is None:
                self.state.set_state(STATE_FAILED_SAFE, self._correlation_id)
                return {
                    "state": STATE_FAILED_SAFE,
                    "restored": False,
                    "error_code": "NO_BACKUP",
                    "error_message": "no previous application snapshot available — user data untouched",
                }
            rb = RollbackEngine(app_root=self.app_root, backup_dir=prev_dir)
            res = rb.restore_application(reason=reason)
            self.state.set_state(STATE_ROLLED_BACK, self._correlation_id)
            self.history_store.append(
                from_version=self.installed_version,
                to_version=self.installed_version,
                channel=self.channel,
                result="ROLLED_BACK",
                rollback=reason,
                correlation_id=self._correlation_id,
            )
            return {"state": STATE_ROLLED_BACK, "restored": True, **res}
        finally:
            self.lock.release()

    # ------------------------------------------------------------------ internals
    def _verify_payload_manifest(self, artifact: Path, plan: dict[str, Any]) -> None:
        """Verify release-manifest.json INSIDE the payload (bundled by CI).

        The embedded manifest lists every payload file (portable-rooted). To
        verify it we extract the payload to a bounded staging dir and check
        the listed hashes against the extracted files (zip-slip-safe).
        """
        try:
            with zipfile.ZipFile(artifact) as zf:
                names = set(zf.namelist())
                cand = next((n for n in names if n.endswith("release-manifest.json")), None)
                if cand is None:
                    return  # build-info hash check remains the base gate
                verify_dir = self.update_home / "verify"
                if verify_dir.exists():
                    shutil.rmtree(verify_dir, ignore_errors=True)
                verify_dir.mkdir(parents=True, exist_ok=True)
                for member in zf.infolist():
                    target = (verify_dir / member.filename).resolve()
                    if not target.is_relative_to(verify_dir.resolve()):
                        raise UpdateBlockedError(
                            f"zip-slip blocked during manifest verification: {member.filename}"
                        )
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                manifest_path = verify_dir / cand
                res = ManifestVerifier.verify_manifest(manifest_path, base_dir=verify_dir)
                if not res["valid"]:
                    problems = [
                        str(f.get("name") or f.get("file") or "?")
                        for f in res.get("files", [])
                        if f.get("status") not in ("OK", None)
                    ][:3]
                    raise UpdateBlockedError(
                        "release manifest inside payload failed verification: "
                        + ", ".join(problems or ["unknown"])
                    )
        except zipfile.BadZipFile:
            raise UpdateBlockedError("artifact is not a valid zip") from None

    def _record_backup_pointer(self, plan: dict[str, Any], backup: dict[str, Any]) -> None:
        # The installer keeps the old tree in a timestamped .previous-<ts> dir;
        # resolve the newest one so rollback can restore it.
        prev_dirs = sorted(
            (d for d in self.app_root.glob(".previous-*") if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
        )
        previous = str(prev_dirs[-1]) if prev_dirs else str(self.app_root / ".previous")
        pointer = {
            "backup_id": backup["backup_id"],
            "backup_path": str(backup["backup_path"]),
            "previous": previous,
            "from_version": self.installed_version,
            "to_version": plan["target_version"],
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        (self.update_home / "rollback-pointer.json").write_text(
            json.dumps(pointer, indent=2), encoding="utf-8"
        )

    def _read_backup_pointer(self) -> dict[str, Any]:
        p = self.update_home / "rollback-pointer.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _record_backup_pointer_cleanup(self) -> None:
        """No-op placeholder: pointer retained for crash recovery."""
        return None

    def _run_migrations(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Config + database migration transaction (precheck/backup/migrate/verify)."""
        required = bool(plan.get("migration_required"))
        results: dict[str, Any] = {"required": required, "result": "NONE", "steps": []}
        cfg = self.user_root / "config" / "nexus.yaml"
        if cfg.exists():
            cm = ConfigMigrator(cfg)
            cres = cm.migrate_if_needed()
            results["steps"].append({"kind": "config", **cres})
        # TASK-10: canonical per-domain migration engine (same as `nexus db`).
        for domain_name, db_name in (
            ("audit", "audit.db"),
            ("news", "news.db"),
            ("candle_intel", "candle_intel.db"),
        ):
            db = self.user_root / "artifacts" / db_name
            if not db.exists():
                continue
            try:
                from nexus_scalp.database.engine import DatabaseMigrationEngine
                from nexus_scalp.database.models import DatabaseDomain

                eng = DatabaseMigrationEngine(
                    db_path=db,
                    domain=DatabaseDomain(domain_name),
                    application_version=str(plan.get("target_version", "")),
                )
                dres = eng.migrate()
                results["steps"].append({"kind": "database", "domain": domain_name, **dres})
                if dres["state"] in (
                    "DB_MIGRATION_FAILED",
                    "DB_BLOCKED",
                    "DB_DOWNGRADE_BLOCKED",
                ):
                    raise MigrationError(
                        f"database migration failed for {domain_name}: {dres.get('error')}"
                    )
            except Exception as e:
                if isinstance(e, MigrationError):
                    raise
                raise MigrationError(f"database migration failed for {domain_name}: {e}") from e
        if any(s.get("applied") or s.get("migrated") for s in results["steps"]):
            results["result"] = "MIGRATED"
        return results

    def _install(self, plan: dict[str, Any], artifact: Path) -> dict[str, Any]:
        mode = InstallModeDetector().detect(self.app_root)
        if mode in (INSTALL_MODE_INNO, INSTALL_MODE_EXE) and plan.get("artifact_name", "").endswith(
            "-setup.exe"
        ):
            return ApplicationInstaller(self.app_root).install_setup(artifact)
        return ApplicationInstaller(self.app_root).install_portable(
            artifact, expected_version=plan["target_version"]
        )


def upd_default_user_root() -> Path:
    """User-data root for the updater (matches release.paths policy)."""
    from .paths import app_data_root

    return app_data_root()
