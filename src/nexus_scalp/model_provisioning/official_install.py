"""Stopped-engine bundle transactions; acquisition never promotes a champion.

A persistent OS lock (never age-stolen/unlinked) serializes writers and startup.
The governance PromotionLock cannot be used here: it steals live locks by age.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_local_locks = threading.local()

ALLOWED_PAYLOAD = frozenset({"model.pt", "model.meta.json", "model.scaler.npz", "dataset.parquet"})


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _verify_files(folder: Path) -> dict[str, Any]:
    from nexus_scalp.model_provisioning.official import verify_bundle_manifest

    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    verify_bundle_manifest(manifest)
    if not set(manifest["files"]) <= ALLOWED_PAYLOAD:
        raise _error("INSTALL_UNSAFE_PATH", "unsupported payload filenames")
    for name, entry in manifest["files"].items():
        path = folder / name
        if path.is_symlink() or not path.is_file():
            raise _error("FILE_MISSING", name)
        if _digest(path) != entry["sha256"]:
            raise _error("SHA256_MISMATCH", name)
        if path.stat().st_size != entry["size"]:
            raise _error("SIZE_MISMATCH", name)
    return manifest


def install_verified_bundle(
    verified: Any, serving_model_path: Path, *, origin: str = "OFFICIAL", cancel_event: Any = None
) -> dict[str, Any]:
    """Reverify an isolated copy before a serving-slot transaction."""
    from nexus_scalp.model_provisioning.official import VerifiedBundle
    from nexus_scalp.model_provisioning.states import LifecycleState

    if not isinstance(verified, VerifiedBundle) or verified.state != LifecycleState.VERIFIED:
        raise _error("INSTALL_UNVERIFIED", "a VERIFIED bundle is required")
    if origin != "OFFICIAL":
        raise _error("INSTALL_ORIGIN_INVALID", "acquisition cannot grant governance authority")
    if Path(serving_model_path).name != "model.pt":
        raise _error("INSTALL_UNSAFE_PATH", "canonical model.pt basename required")
    with model_slot_lock(serving_model_path):
        _assert_engine_stopped()
        _cancel(cancel_event)
        slot = Path(serving_model_path).parent
        _safe_paths(slot)
        _recover_locked(slot)
        _assert_replaceable(Path(serving_model_path))
        with tempfile.TemporaryDirectory(prefix=".official-stage-", dir=slot) as temp:
            stage = Path(temp)
            for name in ["manifest.json", *verified.manifest["files"]]:
                _cancel(cancel_event)
                if name not in ALLOWED_PAYLOAD | {"manifest.json"}:
                    raise _error("INSTALL_UNSAFE_PATH", name)
                source = verified.dir / name
                if source.is_symlink():
                    raise _error("INSTALL_UNSAFE_PATH", name)
                shutil.copy2(source, stage / name)
            manifest = _verify_files(stage)
            _validate_servable(stage / "model.pt")
            state = {
                "schema": "nexus_install_state_v1",
                "origin": origin,
                "bundle_id": manifest["bundle_id"],
                "model_version": manifest["model_version"],
                "model_sha256": _digest(stage / "model.pt"),
            }
            (stage / "install-state.json").write_text(json.dumps(state))
            names = [*manifest["files"], "manifest.json", "install-state.json"]
            backup = slot / ".official-backup"
            backup.mkdir(exist_ok=True)
            old = {}
            for name in names:
                target = slot / name
                old[name] = _digest(target) if target.exists() else None
                if target.exists():
                    shutil.copy2(target, backup / name)
                    _sync_file(backup / name)
                _sync_file(stage / name)
            _sync_dir(backup)
            journal = {
                "schema": 1,
                "old": old,
                "new": {n: _digest(stage / n) for n in names},
                "state": "INSTALLING",
            }
            _assert_engine_stopped()
            _cancel(cancel_event)
            _write_journal(slot, journal)
            try:
                for name in names:
                    _cancel(cancel_event)
                    os.replace(stage / name, slot / name)
                    _sync_dir(slot)
                _verify_files(slot)
                _validate_servable(serving_model_path)
                _cancel(cancel_event)
                journal["state"] = "COMMITTED"
                _write_journal(slot, journal)
            except BaseException:
                _rollback(slot, journal)
                raise
            _cleanup_transaction(slot)
    return {"installed": True, "path": str(serving_model_path), **state, "servable": True}


def _assert_replaceable(model: Path) -> None:
    # Same registry binding as model_bootstrap, but read-only and fail closed:
    # acquisition must not interpret an unavailable existing registry as empty.
    import sqlite3

    from nexus_scalp.database.config import load_database_config
    from nexus_scalp.database.provider import DatabaseProvider
    from nexus_scalp.release import bootstrap, model_bootstrap

    cfg = load_database_config("audit")
    if cfg.provider is not DatabaseProvider.SQLITE:
        raise _error("GOVERNANCE_UNAVAILABLE", "registry provider unsupported for acquisition")
    db = Path(cfg.sqlite_path or cfg.database)
    if db.exists():
        try:
            with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=3) as con:
                rows = con.execute(
                    "SELECT artifact_fingerprint, artifact_path FROM experience_model_registry WHERE lifecycle_status='CHAMPION'"
                ).fetchall()
        except sqlite3.Error as exc:
            raise _error("GOVERNANCE_UNAVAILABLE", "cannot verify champion registry") from exc
        digest = _digest(model) if model.is_file() else ""
        for fingerprint, artifact in rows:
            if (artifact and Path(artifact).resolve() == model.resolve()) or (
                fingerprint and str(fingerprint).lower() in {digest, digest[:16]}
            ):
                raise _error("GOVERNED_PROMOTION_REQUIRED", "registry CHAMPION is protected")
    if model.exists():
        result = bootstrap.bundle_status(model)
        if result["state"] == bootstrap.STATE_INVALID:
            raise _error("INSTALL_UNJUDGEABLE", "cannot judge existing serving bundle")
        if result["state"] == bootstrap.STATE_OK and not model_bootstrap._is_declared_starter(
            model
        ):
            raise _error(
                "GOVERNED_PROMOTION_REQUIRED",
                "servable foreign bundle is not replaceable by acquisition",
            )


def _safe_paths(slot: Path) -> None:
    for parent in [slot, *slot.parents]:
        if parent.is_symlink():
            raise _error("INSTALL_UNSAFE_PATH", str(parent))
    backup = slot / ".official-backup"
    for name in ALLOWED_PAYLOAD | {
        "manifest.json",
        "install-state.json",
        ".official-journal.json",
        ".official-journal.tmp",
        ".official-restore.tmp",
        ".official-backup",
    }:
        path = slot / name
        if path.is_symlink() or (path.exists() and not path.is_file() and path != backup):
            raise _error("INSTALL_UNSAFE_PATH", str(path))
    if backup.exists():
        if not backup.is_dir():
            raise _error("INSTALL_UNSAFE_PATH", str(backup))
        for path in backup.iterdir():
            if (
                path.name not in ALLOWED_PAYLOAD | {"manifest.json", "install-state.json"}
                or path.is_symlink()
                or not path.is_file()
            ):
                raise _error("INSTALL_UNSAFE_PATH", str(path))


def _cancel(event: Any) -> None:
    if event is not None and event.is_set():
        raise _error("CANCELLED", "official install cancelled")


def _assert_engine_stopped() -> None:
    from nexus_scalp.release.paths import get_data_root
    from nexus_scalp.release.update_engine.safety_guards import EngineGuard

    state = EngineGuard(pidfile=get_data_root() / "nexus.pid").engine_state()
    if state != "STOPPED":
        raise _error("ENGINE_RUNNING", f"stop engine before installation ({state})")


def serving_model_path(config: Any = None) -> Path:
    from nexus_scalp.release.bootstrap import canonical_starter_path

    return canonical_starter_path(config)


def _validate_servable(model: Path) -> None:
    from nexus_scalp.model_provisioning.official import OfficialBundleSource
    from nexus_scalp.release.bootstrap import STATE_OK, bundle_status

    OfficialBundleSource()._integrity_probe(model.parent)
    result = bundle_status(model)
    if result["state"] != STATE_OK:
        raise _error("ARTIFACT_INTEGRITY_FAILED", result["detail"])


def _sync_file(path: Path) -> None:
    # On Windows, os.fsync delegates to CRT _commit(fd) which requires write access;
    # opening with "rb" fails with OSError: [Errno 9] Bad file descriptor (EBADF).
    # Use "r+b" on Windows (or "rb" on POSIX), and handle OSError gracefully.
    try:
        mode = "r+b" if os.name == "nt" else "rb"
        with path.open(mode) as stream:
            os.fsync(stream.fileno())
    except OSError:
        pass


def _sync_dir(path: Path) -> None:
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _write_journal(slot: Path, journal: dict[str, Any]) -> None:
    temp = slot / ".official-journal.tmp"
    temp.write_text(json.dumps(journal), encoding="utf-8")
    _sync_file(temp)
    os.replace(temp, slot / ".official-journal.json")
    _sync_dir(slot)


def _cleanup_transaction(slot: Path) -> None:
    (slot / ".official-journal.json").unlink(missing_ok=True)
    _sync_dir(slot)
    backup = slot / ".official-backup"
    for name in ALLOWED_PAYLOAD | {"manifest.json", "install-state.json"}:
        (backup / name).unlink(missing_ok=True)
    if backup.exists():
        backup.rmdir()


def recover_official_install(serving_model_path: Path) -> dict[str, Any]:
    """Recover before any loader/fallback; errors MUST block engine startup."""
    with model_slot_lock(serving_model_path):
        return _recover_locked(Path(serving_model_path).parent)


def _recover_locked(slot: Path) -> dict[str, Any]:
    _safe_paths(slot)
    path = slot / ".official-journal.json"
    if not path.exists():
        return {"recovered": False}
    try:
        journal = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise _error("RECOVERY_CONFLICT", "unreadable journal") from exc
    _assert_engine_stopped()
    _check_journal(slot, journal)
    if journal["state"] == "COMMITTED":
        _cleanup_transaction(slot)
    else:
        _rollback(slot, journal)
    return {"recovered": True}


def _check_journal(slot: Path, journal: dict[str, Any]) -> None:
    allowed = ALLOWED_PAYLOAD | {"manifest.json", "install-state.json"}
    if (
        not isinstance(journal, dict)
        or journal.get("schema") != 1
        or journal.get("state") not in {"INSTALLING", "COMMITTED"}
        or not isinstance(journal.get("old"), dict)
        or not isinstance(journal.get("new"), dict)
        or not set(journal["old"]) == set(journal["new"])
        or not set(journal["old"]) <= allowed
        or not journal["old"]
    ):
        raise _error("RECOVERY_CONFLICT", "invalid journal")
    for name, old in journal["old"].items():
        target = slot / name
        saved = slot / ".official-backup" / name
        if target.is_symlink() or saved.is_symlink():
            raise _error("RECOVERY_CONFLICT", "symlink in transaction")
        current = _digest(target) if target.is_file() else None
        new = journal["new"][name]
        if current not in {old, new}:
            raise _error("RECOVERY_CONFLICT", f"{name} modified outside transaction")
        if journal["state"] == "INSTALLING" and old is not None:
            if not saved.is_file() or _digest(saved) != old:
                raise _error("RECOVERY_CONFLICT", f"{name} backup missing/corrupt")


def _rollback(slot: Path, journal: dict[str, Any]) -> None:
    _check_journal(slot, journal)
    backup = slot / ".official-backup"
    for name, digest in journal["old"].items():
        target = slot / name
        if digest is None:
            target.unlink(missing_ok=True)
        else:
            temp = slot / ".official-restore.tmp"
            shutil.copy2(backup / name, temp)
            _sync_file(temp)
            os.replace(temp, target)
        _sync_dir(slot)
    _cleanup_transaction(slot)


def _error(code: str, detail: str) -> Any:
    from nexus_scalp.model_provisioning.official import OfficialBundleError

    return OfficialBundleError(code, detail)


@contextmanager
def model_slot_lock(serving_model_path: Path) -> Iterator[None]:
    """Thread-reentrant cross-process lock; required around engine startup.

    Do not unlink this persistent file: replacing its inode splits ownership.
    Kernel releases ownership on process death, without time-based stealing.
    """
    slot = Path(serving_model_path).parent
    slot.mkdir(parents=True, exist_ok=True)
    path = slot / ".official-install.lock"
    identity = (os.getpid(), str(path.resolve()))
    held: set[tuple[int, str]] = getattr(_local_locks, "held", set())
    if identity in held:
        yield
        return
    if path.is_symlink():
        raise _error("INSTALL_UNSAFE_PATH", str(path))
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    locked = False
    try:
        try:
            if sys.platform == "win32":
                import msvcrt

                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            held.add(identity)
            _local_locks.held = held
        except OSError as exc:
            raise _error("INSTALL_CONFLICT", "model slot is locked") from exc
        yield
    finally:
        if locked:
            held.discard(identity)
            if sys.platform == "win32":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
