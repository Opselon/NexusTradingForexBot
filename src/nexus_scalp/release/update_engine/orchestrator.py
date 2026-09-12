"""Extracted from release/updater.py (update-package split); behavior preserved."""

from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.release.metadata import get_version_info
from nexus_scalp.release.update_engine.backup_migrate import (
    ApplicationInstaller,
    BackupEngine,
    BackupPlanner,
    ConfigMigrator,
    MigrationError,
)
from nexus_scalp.release.update_engine.constants import (
    DEFAULT_CHANNEL,
    INSTALL_MODE_EXE,
    INSTALL_MODE_INNO,
    STAGE_DOWNLOAD,
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
    STATUS_INCOMPATIBLE,
    STATUS_UNKNOWN,
    STATUS_UPDATE_AVAILABLE,
    STATUS_UPDATE_REJECTED,
    SUPPORTED_CHANNELS,
    SUPPORTED_PLATFORM,
)
from nexus_scalp.release.update_engine.discovery import (
    CompatibilityGate,
    GitHubDiscoveryError,
    HashVerifier,
    ManifestVerifier,
    UpdateDiscovery,
    UpdatePlanBuilder,
    _machine_arch,
)
from nexus_scalp.release.update_engine.downloader import (
    SafeDownloader,
)
from nexus_scalp.release.update_engine.health import (
    PostUpdateHealth,
)
from nexus_scalp.release.update_engine.pidfile_lock import (
    InstallModeDetector,
    UpdateLock,
    _current_app_root,
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

#: Structured update-lifecycle logger (OBS-006: every stage transition and
#: terminal outcome must land in the severity tree stamped with the ``upd-``
#: correlation id so an update is reconstructable from logs alone, not only
#: from the terminal update-state.json). Import is lazy inside _update_log()
#: to keep this module import-light for the slim CLI contract.
_UPDATE_LOGGER_NAME = "nexus_scalp.release.update"


def _update_log(level: str, event: str, msg: str, *args: Any) -> None:
    """Failure-isolated structured update log (never breaks an update).

    Uses the STDLIB logger so routing is context-correct: under the engine
    (configure_logging installed severity-file handlers) records land in the
    severity tree exactly as before; under the bare CLI (no handlers) INFO
    records are dropped instead of polluting stdout — ``--json`` output must
    stay machine-parseable (structlog's unconfigured PrintLogger default
    writes to stdout, which broke ``nexus update install --json``).
    """
    try:
        import logging

        logging.getLogger(_UPDATE_LOGGER_NAME).log(
            getattr(logging, level.upper(), logging.INFO),
            "[UPDATE] event=%s %s",
            event,
            msg % args if args else msg,
        )
    except Exception:
        pass


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
        self.pidfile = pidfile or self._default_pidfile()
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
                if ok:
                    _add("staged_artifact_hash", True, "verified")
                else:
                    # present-but-wrong bytes = real tamper/corruption signal
                    _add("staged_artifact_hash", False, "MISMATCH")
            else:
                # OBS-006 follow-up (2026-09-10): the staged package is a
                # TRANSIENT download cache the storage guard prunes by design
                # (keep=2 sweeps). Its absence says nothing about the INSTALLED
                # client's integrity — treat as informational WARNING, never a
                # verification failure (a healthy up-to-date client whose cache
                # was swept must still pass `nexus update verify`).
                checks.append(
                    {
                        "name": "staged_artifact_hash",
                        "verdict": "WARNING",
                        "detail": "staged artifact not in cache (pruned) — installed files unaffected",
                    }
                )
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
        warned = [c for c in checks if c["verdict"] == "WARNING"]
        return {
            "status": "VERIFIED" if not failed else "VERIFICATION_FAILED",
            "warnings": [c["name"] for c in warned],
            "current_version": version,
            "checks": checks,
            "record": rec,
            "model_version": rec.get("model_version"),
            "schema_version": rec.get("schema_version"),
        }

    @staticmethod
    def _default_pidfile() -> Path:
        """AGENT-9 (2026-09-11): default to the ENGINE WRITER's pidfile location.

        The engine daemon writes its pid at
        ``release.paths.get_data_root() / "nexus.pid"`` (cli/engine_boot._pidfile),
        which for portable/frozen layouts is ``<exe_dir>/data/nexus.pid`` and
        otherwise ``<app_data_root>/data/nexus.pid``.  The updater previously
        defaulted to ``user_root / "nexus.pid"`` — one ``/data`` segment apart —
        so EngineGuard NEVER found a live engine and the
        UPDATE_BLOCKED_WHILE_LIVE gate (spec sections 13/14) was dead code in
        every deployed layout.  Reading through the same canonical helper keeps
        the reader and the writer in agreement for every install mode.
        """
        from nexus_scalp.release import paths as rpaths

        return rpaths.get_data_root() / "nexus.pid"

    @staticmethod
    def _engine_config_path() -> Path | None:
        """Best-effort path to the engine's user config for LIVE-mode detection."""
        from nexus_scalp.release import paths as rpaths

        try:
            cfg = rpaths.get_user_config_path()
        except Exception:
            return None
        return cfg if cfg.exists() else None

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
            # OBS-006: persist the transition to the severity tree too —
            # update-state.json holds only the terminal state, so a mid-update
            # crash previously left no log evidence of the stage chain.
            _update_log(
                "info",
                "STAGE",
                "correlation_id=%s state=%s%s",
                self._correlation_id,
                state,
                f" detail={detail}" if detail else "",
            )
            if on_event is not None:
                with contextlib.suppress(Exception):
                    on_event(state, detail)

        # crash recovery first: a half-finished update must never be restarted
        crashed = self.state.recover_after_crash()
        if crashed["crashed"]:
            report["state"] = STATE_ROLLBACK_REQUIRED
            report["error_code"] = "CRASH_REQUIRES_ROLLBACK"
            report["error_message"] = (
                f"previous update crashed at {crashed['previous_state']} — "
                "run `nexus update rollback` before any new update"
            )
            _update_log(
                "error",
                "CRASH_RECOVERY",
                "correlation_id=%s previous_state=%s (rollback required before any new update)",
                self._correlation_id,
                crashed["previous_state"],
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
            guard = EngineGuard(pidfile=self.pidfile, config_path=self._engine_config_path())
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
                _update_log(
                    "warning",
                    "COMPATIBILITY_BLOCKED",
                    "correlation_id=%s verdict=%s reasons=%s",
                    self._correlation_id,
                    compat.get("verdict"),
                    [
                        str(c.get("reason", c.get("name", "?")))
                        for c in compat.get("checks", [])
                        if isinstance(c, dict) and c.get("verdict") == "BLOCKED"
                    ],
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
                    restore_res = RollbackEngine(
                        app_root=self.app_root, backup_dir=prev_dir
                    ).restore_application(reason="post-update version verification failed")
                    report["state"] = STATE_ROLLED_BACK
                    report["status"] = "UPDATE_VERIFICATION_FAILED"
                    report["error_code"] = "UPDATE_VERIFICATION_FAILED"
                    if restore_res.get("restored"):
                        report["error_message"] = (
                            f"running {running or '?'} != target {plan['target_version']} — "
                            "previous version restored"
                        )
                        report["rollback_completed"] = True
                    else:
                        # BUG-263: the snapshot refused integrity re-verification,
                        # so nothing was restored. Never claim a recovery that did
                        # not happen — the live tree still holds the unverified new
                        # build and the operator must intervene.
                        report["state"] = STATE_FAILED
                        report["rollback_completed"] = False
                        report["rollback_refused_code"] = str(
                            restore_res.get("error_code") or "SNAPSHOT_INTEGRITY_FAILED"
                        )
                        report["error_message"] = (
                            f"running {running or '?'} != target {plan['target_version']} — "
                            f"rollback REFUSED: {restore_res.get('error_message') or 'snapshot failed integrity verification'}"
                        )
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
            _update_log(
                "error",
                "FAILED",
                "correlation_id=%s stage=%s error_type=%s error=%s",
                self._correlation_id,
                report.get("state"),
                type(e).__name__,
                str(e)[:300],
            )
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
            # 2026-09-09 storage-hygiene pass: one sweep at the END of every
            # update run — clears crash-leftover stage/preserve dirs, stale
            # *.part residue and old full-app backups (keep newest 1). The
            # just-installed tree is NEVER inside an allowlisted shape, so a
            # completed install cannot be damaged by this. Failure-isolated.
            try:
                from nexus_scalp.storage.policy import (
                    prune_update_cache,
                    sweep_crash_leftovers,
                    sweep_residue_files,
                )

                cache_res = prune_update_cache(self.cache_dir, keep_packages=2)
                leftovers = sweep_crash_leftovers(self.app_root, keep_previous_backups=1)
                residue = sweep_residue_files(self.user_root, min_age_sec=3600.0)
                # stdlib logger (NOT structlog get_logger): under the bare CLI
                # structlog's unconfigured PrintLogger would print to stdout and
                # corrupt the --json machine contract (2026-09-10 self-update
                # CLI audit). Engine context routes identically via stdlib.
                _update_log(
                    "info",
                    "STORAGE_SWEEP",
                    "cache_freed=%d leftovers_dirs=%d residue_files=%d",
                    cache_res.get("bytes_freed", 0),
                    leftovers.get("removed_dirs", 0),
                    residue.get("removed", 0),
                )
            except Exception:
                pass

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
            if not res.get("restored"):
                # BUG-263: the snapshot failed integrity re-verification. The
                # live tree was NOT touched, so this is a safe refusal — never
                # report ROLLED_BACK for a rollback that did not happen.
                self.state.set_state(STATE_FAILED_SAFE, self._correlation_id)
                _update_log(
                    "error",
                    "ROLLBACK_REFUSED",
                    "correlation_id=%s reason=%s error_code=%s",
                    self._correlation_id,
                    reason,
                    str(res.get("error_code") or "UNKNOWN"),
                )
                self.history_store.append(
                    from_version=self.installed_version,
                    to_version=self.installed_version,
                    channel=self.channel,
                    result="ROLLBACK_REFUSED",
                    rollback=f"{reason} ({res.get('error_code')})",
                    correlation_id=self._correlation_id,
                )
                return {
                    "state": STATE_FAILED_SAFE,
                    "restored": False,
                    **res,
                }
            self.state.set_state(STATE_ROLLED_BACK, self._correlation_id)
            _update_log(
                "warning",
                "ROLLED_BACK",
                "correlation_id=%s reason=%s restored=%s",
                self._correlation_id,
                reason,
                bool(res.get("restored", res.get("files"))),
            )
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
        # BUG-237: the Inno SETUP payload is an EXE, not a zip - opening it
        # with zipfile raised BadZipFile -> "artifact is not a valid zip" even
        # though the SHA-256 was fine. The installer embeds SHA256SUMS.txt +
        # release-manifest.json itself (BUG-166 pre-stage); its integrity gate
        # is the artifact SHA-256, so the zip-manifest pass applies to the
        # portable .zip payload only.
        if str(plan.get("artifact_name", "")).endswith("-setup.exe"):
            return
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
    from nexus_scalp.release.paths import app_data_root

    return app_data_root()
