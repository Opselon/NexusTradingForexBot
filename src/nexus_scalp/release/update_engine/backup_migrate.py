"""Extracted from release/updater.py (update-package split); behavior preserved."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.release.update_engine.discovery import HashVerifier
from nexus_scalp.release.update_engine.safety_guards import UpdateBlockedError


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
