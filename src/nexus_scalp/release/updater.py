"""GitHub-driven update engine for installed Nexus users (TASK-9).

DECOMPOSED: the implementation now lives in the ``release/update/``
package (constants/discovery/downloader/backup_migrate/health/
rollback_state/pidfile_lock/safety_guards/orchestrator); this module
is a compatibility facade re-exporting the full historical surface.

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

from nexus_scalp.release import packaging
from nexus_scalp.release.update_engine.backup_migrate import (
    ApplicationInstaller,
    BackupEngine,
    BackupPlanner,
    ConfigMigrator,
    DatabaseMigrator,
    MigrationError,
)
from nexus_scalp.release.update_engine.constants import (
    _CHECKSUM_ASSET_RE,
    _MUTATING_STATES,
    _REVOKED_MARKER_RE,
    _SOURCE_ASSET_RE,
    DEFAULT_CHANNEL,
    INSTALL_MODE_DEVELOPER,
    INSTALL_MODE_EXE,
    INSTALL_MODE_INNO,
    INSTALL_MODE_PORTABLE,
    INSTALL_MODE_SOURCE,
    INSTALL_MODE_UNKNOWN,
    SIGNED_MANIFEST_ASSET,
    STAGE_DOWNLOAD,
    STAGE_INSTALL,
    STAGE_STARTUP,
    STAGE_VERIFY,
    STATE_AVAILABLE,
    STATE_BACKING_UP,
    STATE_CHECKING,
    STATE_COMPLETED,
    STATE_DOWNLOADING,
    STATE_FAILED,
    STATE_FAILED_SAFE,
    STATE_HEALTH_CHECK,
    STATE_IDLE,
    STATE_INSTALLING,
    STATE_MIGRATING,
    STATE_QUIESCING,
    STATE_READY,
    STATE_ROLLBACK_REQUIRED,
    STATE_ROLLED_BACK,
    STATE_ROLLING_BACK,
    STATE_VERIFYING,
    STATE_VERIFYING_INSTALL,
    STATUS_DIRECT_UPDATE_UNSUPPORTED,
    STATUS_GITHUB_UNAVAILABLE,
    STATUS_INCOMPATIBLE,
    STATUS_NETWORK_ERROR,
    STATUS_NETWORK_UNAVAILABLE,
    STATUS_NO_UPDATE,
    STATUS_RELEASE_NOT_FOUND,
    STATUS_SECURITY_BLOCKED,
    STATUS_UNKNOWN,
    STATUS_UNSUPPORTED,
    STATUS_UPDATE_AVAILABLE,
    STATUS_UPDATE_REJECTED,
    SUPPORTED_CHANNELS,
    SUPPORTED_PLATFORM,
)
from nexus_scalp.release.update_engine.discovery import (
    CompatibilityGate,
    DigestResolver,
    GitHubDiscoveryError,
    HashVerifier,
    ManifestVerifier,
    SignedManifestResolver,
    UpdateDiscovery,
    UpdatePlanBuilder,
    _machine_arch,
    compare_versions,
)
from nexus_scalp.release.update_engine.downloader import (
    SafeDownloader,
)
from nexus_scalp.release.update_engine.health import (
    PostUpdateHealth,
)
from nexus_scalp.release.update_engine.orchestrator import (
    UpdateOrchestrator,
    upd_default_user_root,
)
from nexus_scalp.release.update_engine.pidfile_lock import (
    InstallModeDetector,
    SettingsGuard,
    UpdateLock,
    _current_app_root,
    _pid_alive,
)
from nexus_scalp.release.update_engine.rollback_state import (
    ReleaseLocalState,
    RollbackEngine,
    UpdateHistory,
    UpdateState,
)
from nexus_scalp.release.update_engine.safety_guards import (
    EngineGuard,
    QuiesceProtocol,
    UpdateBlockedError,
)

__all__ = [
    "DEFAULT_CHANNEL",
    "INSTALL_MODE_DEVELOPER",
    "INSTALL_MODE_EXE",
    "INSTALL_MODE_INNO",
    "INSTALL_MODE_PORTABLE",
    "INSTALL_MODE_SOURCE",
    "INSTALL_MODE_UNKNOWN",
    "SIGNED_MANIFEST_ASSET",
    "STAGE_DOWNLOAD",
    "STAGE_INSTALL",
    "STAGE_STARTUP",
    "STAGE_VERIFY",
    "STATE_AVAILABLE",
    "STATE_BACKING_UP",
    "STATE_CHECKING",
    "STATE_COMPLETED",
    "STATE_DOWNLOADING",
    "STATE_FAILED",
    "STATE_FAILED_SAFE",
    "STATE_HEALTH_CHECK",
    "STATE_IDLE",
    "STATE_INSTALLING",
    "STATE_MIGRATING",
    "STATE_QUIESCING",
    "STATE_READY",
    "STATE_ROLLBACK_REQUIRED",
    "STATE_ROLLED_BACK",
    "STATE_ROLLING_BACK",
    "STATE_VERIFYING",
    "STATE_VERIFYING_INSTALL",
    "STATUS_DIRECT_UPDATE_UNSUPPORTED",
    "STATUS_GITHUB_UNAVAILABLE",
    "STATUS_INCOMPATIBLE",
    "STATUS_NETWORK_ERROR",
    "STATUS_NETWORK_UNAVAILABLE",
    "STATUS_NO_UPDATE",
    "STATUS_RELEASE_NOT_FOUND",
    "STATUS_SECURITY_BLOCKED",
    "STATUS_UNKNOWN",
    "STATUS_UNSUPPORTED",
    "STATUS_UPDATE_AVAILABLE",
    "STATUS_UPDATE_REJECTED",
    "SUPPORTED_CHANNELS",
    "SUPPORTED_PLATFORM",
    "_CHECKSUM_ASSET_RE",
    "_MUTATING_STATES",
    "_REVOKED_MARKER_RE",
    "_SOURCE_ASSET_RE",
    "ApplicationInstaller",
    "BackupEngine",
    "BackupPlanner",
    "CompatibilityGate",
    "ConfigMigrator",
    "DatabaseMigrator",
    "DigestResolver",
    "EngineGuard",
    "GitHubDiscoveryError",
    "HashVerifier",
    "InstallModeDetector",
    "ManifestVerifier",
    "MigrationError",
    "PostUpdateHealth",
    "QuiesceProtocol",
    "ReleaseLocalState",
    "RollbackEngine",
    "SafeDownloader",
    "SettingsGuard",
    "SignedManifestResolver",
    "UpdateBlockedError",
    "UpdateDiscovery",
    "UpdateHistory",
    "UpdateLock",
    "UpdateOrchestrator",
    "UpdatePlanBuilder",
    "UpdateState",
    "_current_app_root",
    "_machine_arch",
    "_pid_alive",
    "compare_versions",
    "packaging",
    "upd_default_user_root",
]
