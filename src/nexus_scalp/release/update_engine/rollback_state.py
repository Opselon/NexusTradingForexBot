"""Extracted from release/updater.py (update-package split); behavior preserved."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
