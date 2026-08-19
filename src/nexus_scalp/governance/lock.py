"""
Promotion Lock
==============
TASK-08 / 70D governance: cross-process promotion serialization (spec 37).

Two agents/processes must not be able to promote simultaneously. This module
provides an exclusive-create lock file (same pattern as the database migration
engine) plus a process-local re-entrant guard, so a concurrent promotion
attempt reports PROMOTION_CONFLICT instead of a partial overwrite.

The lock is crash-safe: a stale lock file (dead process) is detected by PID
liveness and reclaimed. The lock lifetime is short (bounded by the promotion
transaction itself).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.governance.lock")

#: A lock file older than this is considered stale (seconds).
LOCK_STALE_AFTER_SEC: float = 120.0


class PromotionLockError(RuntimeError):
    """Raised when the promotion lock cannot be acquired (PROMOTION_CONFLICT)."""


class PromotionLock:
    """Exclusive-create cross-process promotion lock.

    Usage::

        with PromotionLock(lock_path) as lock:
            if not lock.acquired:
                raise PromotionLockError("PROMOTION_CONFLICT")
            ... promotion transaction ...
    """

    def __init__(self, lock_path: Path | str) -> None:
        self.path = Path(lock_path)
        self.acquired = False
        self._pid = os.getpid()

    # ------------------------------------------------------------------

    def try_acquire(self) -> bool:
        """Attempts the exclusive create. Returns True when held by us."""
        if self.acquired:
            return True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(self._pid).encode())
            os.close(fd)
            self.acquired = True
            return True
        except FileExistsError:
            self._reclaim_if_stale()
            return False
        except OSError as e:
            logger.error("[MODEL_GOVERNANCE] promotion lock acquire failed", error=str(e))
            return False

    def _reclaim_if_stale(self) -> None:
        """Reclaims a lock file whose owner PID is dead (crash safety)."""
        try:
            raw = self.path.read_text(encoding="utf-8", errors="replace").strip()
            pid = int(raw) if raw.isdigit() else -1
            alive = False
            if pid > 0:
                try:
                    os.kill(pid, 0)  # signal 0 = existence probe (Windows OK)
                    alive = True
                except OSError:
                    alive = False
            age = time.time() - self.path.stat().st_mtime
            if (not alive or age > LOCK_STALE_AFTER_SEC) and self.path.exists():
                self.path.unlink(missing_ok=True)
                logger.warning(
                    "[MODEL_GOVERNANCE] stale promotion lock reclaimed",
                    pid=pid,
                    age_sec=round(age, 1),
                )
        except Exception:
            return

    def release(self) -> None:
        if self.acquired:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            self.acquired = False

    def __enter__(self) -> PromotionLock:
        self.try_acquire()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()
