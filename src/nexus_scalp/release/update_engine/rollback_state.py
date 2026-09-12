"""Extracted from release/updater.py (update-package split); behavior preserved."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.release import packaging
from nexus_scalp.release.update_engine.backup_migrate import (
    ApplicationInstaller,
)
from nexus_scalp.release.update_engine.constants import (
    _MUTATING_STATES,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_FAILED_SAFE,
    STATE_IDLE,
    STATE_QUIESCING,
    STATE_ROLLED_BACK,
)
from nexus_scalp.release.update_engine.safety_guards import (
    UpdateBlockedError,
)

#: Restore-time integrity verdicts (BUG-263).  A snapshot is UNTRUSTED until
#: its embedded release-manifest.json re-verifies; refusal never touches the
#: live app tree.
ERROR_SNAPSHOT_NO_MANIFEST = "SNAPSHOT_NO_MANIFEST"
ERROR_SNAPSHOT_INTEGRITY_FAILED = "SNAPSHOT_INTEGRITY_FAILED"


def _log_restore(event: str, msg: str, *args: Any) -> None:
    """Structured restore logging via the stdlib logger (same policy as the
    orchestrator's ``_update_log``: under the bare CLI INFO records must never
    pollute ``--json`` stdout).  Failure-isolated."""
    try:
        import logging

        logging.getLogger("nexus_scalp.release.rollback").warning(event + " " + msg, *args)
    except Exception:
        pass


class RollbackEngine:
    """Rollback restores the PRIOR application; user data is version-aware.

    Rollback never blindly restores an old DB over a successfully migrated
    newer DB — database/config rollback requires version-aware backup
    selection (section 25).  The app-tree runtime dirs (artifacts/data/logs)
    are NOT restored from the previous snapshot: they hold live user data
    and stay as-is.

    BUG-263 (P0 trust-chain): the snapshot itself is re-verified BEFORE a
    single byte is copied back.  A ``.previous-*`` tree captures the state of
    the install at swap time; anything that happened to it afterwards (malware,
    mistaken rsync, disk corruption, a hand-edited file) is invisible to the
    update trust chain unless restore re-hashes it against the
    ``release-manifest.json`` the release pipeline embedded in the tree
    (BUG-166 pre-stage / ``release.yml`` "Embed release manifest in portable
    bundle").  Verification reuses the canonical manifest reader and hasher in
    :mod:`nexus_scalp.release.packaging` — the same parsing/hashing contract
    ``verify-release`` and the in-payload gate use — so rollback cannot become
    an integrity bypass around the chain that produced the snapshot.  Nothing
    is ever copied unless the WHOLE verification passes (no partial restore);
    on any refusal the live app tree is byte-unchanged.
    """

    def __init__(self, app_root: Path, backup_dir: Path | None = None) -> None:
        self.app_root = app_root
        self.backup_dir = backup_dir

    def _integrity_refusal(self, reason: str, detail: str) -> dict[str, Any]:
        """Uniform fail-closed refusal report (never a partial restore)."""
        _log_restore(
            "SNAPSHOT_INTEGRITY_REFUSED",
            "reason=%s snapshot=%s detail=%s",
            reason,
            str(self.backup_dir),
            detail[:400],
        )
        return {
            "restored": False,
            "error_code": reason,
            "error_message": detail,
            "snapshot": str(self.backup_dir),
            "restored_items": 0,
            "skipped_user_data_items": 0,
        }

    def verify_snapshot(self) -> dict[str, Any]:
        """Read-only re-verification of the snapshot (BUG-263 gate).

        Locates the embedded release manifest (portable-root copy, or the
        CI-staged ``manifests/`` spelling), then delegates to the canonical
        packaging verifier: parse → structural path guard (traversal /
        absolute / malformed digests rejected BEFORE any file is read) →
        SHA-256 of every listed file recomputed from the snapshot bytes →
        identity cross-check against the tree's own build-info.  Never
        fabricates or copies hashes; never trusts a manifest that merely
        exists.  ``reason`` is the public verdict code:
        ``SNAPSHOT_NO_MANIFEST`` or ``SNAPSHOT_INTEGRITY_FAILED`` (spec
        contract), with the granular packaging reason kept in
        ``integrity_reason`` for diagnostics.
        """
        snapshot = self.backup_dir
        assert snapshot is not None
        manifest_path = packaging.locate_embedded_manifest(snapshot)
        if manifest_path is None:
            return {
                "valid": False,
                "reason": ERROR_SNAPSHOT_NO_MANIFEST,
                "integrity_reason": "MANIFEST_MISSING",
                "problems": [
                    f"{packaging.MANIFEST_FILE_NAME} absent from snapshot "
                    f"(root and {packaging.MANIFEST_SUBDIR}/)"
                ],
            }
        result = dict(
            packaging.verify_snapshot_integrity(
                snapshot,
                manifest_path=manifest_path,
                not_restored_prefixes=tuple(ApplicationInstaller.USER_DATA_DIRS),
            )
        )
        granular = str(result.get("reason") or "MANIFEST_MALFORMED")
        result["integrity_reason"] = granular
        if not result.get("valid"):
            result["reason"] = (
                ERROR_SNAPSHOT_NO_MANIFEST
                if granular == "MANIFEST_MISSING"
                else ERROR_SNAPSHOT_INTEGRITY_FAILED
            )
        return result

    def restore_application(self, reason: str = "update-failure") -> dict[str, Any]:
        if self.backup_dir is None or not self.backup_dir.exists():
            raise UpdateBlockedError("no previous application backup available for rollback")
        # --- BUG-263: full verification completes BEFORE the first copy. ---
        gate = self.verify_snapshot()
        if not gate.get("valid"):
            code = str(gate.get("reason") or "MANIFEST_MALFORMED")
            if code == ERROR_SNAPSHOT_NO_MANIFEST:
                detail = (
                    "rollback snapshot carries no release-manifest.json — its bytes "
                    "cannot be verified against the release trust chain; refusing to "
                    "activate it (live app tree untouched)"
                )
            else:
                problems = ", ".join(str(p) for p in (gate.get("problems") or [])[:3])
                detail = (
                    f"rollback snapshot failed integrity re-verification "
                    f"({code}): {problems or 'see log'} — live app tree untouched"
                )
            return self._integrity_refusal(code, detail)
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
            "integrity_verified_files": int(gate.get("verified_files") or 0),
            "integrity_verified_bytes": int(gate.get("verified_bytes") or 0),
        }


# ---------------------------------------------------------------------------
# Concurrency + crash recovery (sections 33/34/63)
# ---------------------------------------------------------------------------


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
