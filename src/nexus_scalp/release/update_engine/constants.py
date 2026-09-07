"""Status vocabulary, channel/platform constants, and detection regexes
for the GitHub-driven update engine (TASK-9). Single source of truth —
every update submodule imports these from here."""

from __future__ import annotations

import re

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
