"""Release/update status (CHG-0043, TASK-RUNTIME-TRUTH).

ONE read-mostly aggregator for the operator questions:

    What version/commit am I running?
    Is there a newer version?  Is my install behind origin/main?
    What changed?

Design (brief sections 3/17/20):
    * NO network on the read path: ``build_release_status()`` reports the
      LAST-KNOWN truth from local records (installed-release.json,
      update-state.json, update-history.jsonl, release manifest, git).
    * ``refresh_from_github()`` is the EXPLICIT network path (bounded, one
      call) used by ``nexus update check`` / the UI refresh button — never
      by health.
    * Distinct statuses (never collapsed into "up to date"):
        VERSION_UPDATE    newer release version exists
        REVISION_AHEAD    same version family but a newer commit is known
        NO_UPDATE         running the newest known release
        UNKNOWN           no comparison evidence (no records yet)
        OFFLINE           a refresh was attempted and the network failed
    * commits_behind/ahead is reported ONLY when safely computable from
      local git metadata (dev/source installs); otherwise UNKNOWN —
      never a fabricated count.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STATUS_VERSION_UPDATE = "VERSION_UPDATE"
STATUS_REVISION_AHEAD = "REVISION_AHEAD"
STATUS_NO_UPDATE = "NO_UPDATE"
STATUS_UNKNOWN = "UNKNOWN"
STATUS_OFFLINE = "OFFLINE"


def _safe_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _update_home() -> Path:
    from nexus_scalp.release.paths import app_data_root

    return app_data_root() / "update"


def _git_commit() -> str:
    from nexus_scalp.release.metadata import get_version_info

    return str(get_version_info().get("commit") or "")


def _git_counts(remote: str = "origin/main") -> tuple[int | None, int | None]:
    """(behind, ahead) vs a remote ref — only when the ref exists locally.

    Dev/source convenience: requires a prior fetch (the caller decides when
    to refresh); never performs network I/O itself and never guesses.
    """
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", remote],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if probe.returncode != 0:
            return None, None
        out = subprocess.run(
            ["git", "rev-list", "--left-right", "--count", f"HEAD...{remote}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if out.returncode != 0:
            return None, None
        # left-right semantics: left side = HEAD-only commits (AHEAD),
        # right side = remote-only commits (BEHIND). The previous mapping
        # (behind, ahead = split) was swapped (found in the 2026-09-02
        # update-awareness UX pass via a 4-ahead local tree).
        counts = out.stdout.split()
        if len(counts) != 2:
            return None, None
        ahead, behind = int(counts[0]), int(counts[1])
        return behind, ahead
    except Exception:
        return None, None


def _history_rows(limit: int = 10) -> list[dict[str, Any]]:
    hist = _update_home() / "update-history.jsonl"
    if not hist.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in hist.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except OSError:
        pass
    return rows


def _recent_commit_titles(limit: int = 5, remote: str = "origin/main") -> list[str]:
    """Actual commit titles between HEAD and the remote ref (never invented).

    Returns [] when the ref is unknown or git is unavailable — callers must
    treat absence as UNKNOWN, not as 'no changes'.
    """
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", remote],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if probe.returncode != 0:
            return []
        out = subprocess.run(
            ["git", "log", f"HEAD..{remote}", "--pretty=format:%s", f"-n{limit}"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if out.returncode != 0:
            return []
        return [line.strip() for line in out.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def build_release_status(include_git_counts: bool = True) -> dict[str, Any]:
    """Offline-safe, last-known release/update status (never fabricates)."""
    home = _update_home()
    installed = _safe_json(home / "installed-release.json")
    state = _safe_json(home / "update-state.json")
    version_info_commit = _git_commit()

    # last-known available release (from the persisted update state when an
    # UPDATE_AVAILABLE plan was recorded; otherwise UNKNOWN)
    last_plan = state.get("last_plan") or {}
    available_version = (
        str(last_plan.get("target_version") or state.get("available_version") or "") or None
    )
    available_commit = (
        str(last_plan.get("commit_sha") or state.get("available_commit") or "") or None
    )

    # get_version_info() already implements the identity precedence
    # (frozen bundle -> its stamp; dev/source -> repo metadata, ignoring a
    # stale leftover build-info.json) — one source of truth, no re-read here.
    try:
        from nexus_scalp.release.metadata import get_version_info

        current_version = str(get_version_info().get("version") or "") or None
    except Exception:
        current_version = None

    update_status = STATUS_UNKNOWN
    if available_version and current_version:

        def _v(v: str) -> tuple[int, ...]:
            return tuple(int(p) for p in re.findall(r"\d+", v)[:3])

        try:
            if _v(available_version) > _v(current_version):
                update_status = STATUS_VERSION_UPDATE
            else:
                update_status = STATUS_NO_UPDATE
        except Exception:
            update_status = STATUS_UNKNOWN

    behind = ahead = None
    if include_git_counts:
        behind, ahead = _git_counts()

    revision_ahead = bool(
        behind == 0 and ahead and ahead > 0 and update_status in (STATUS_NO_UPDATE, STATUS_UNKNOWN)
    )
    if revision_ahead:
        update_status = STATUS_REVISION_AHEAD

    # Real change summary: actual commit titles from the remote ref when we
    # are genuinely behind; empty list = UNKNOWN (never fabricated).
    changes = _recent_commit_titles(5) if (behind or 0) > 0 else []

    return {
        "contract": "RELEASE_STATUS v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "current_version": current_version,
        "current_commit": version_info_commit or None,
        "commit_status": "RECORDED" if version_info_commit else "NOT_RECORDED",
        "installed_release": installed or None,
        "available_version": available_version,
        "available_commit": available_commit,
        "commits_behind": behind,  # None = UNKNOWN (never fabricated)
        "commits_ahead": ahead,
        "changes": changes,
        "update_status": update_status,
        "update_state": str(state.get("state") or "UNKNOWN"),
        "update_state_at": state.get("updated_at"),
        "offline_mode": True,  # this payload never required network
        "history": _history_rows(5),
    }


def refresh_from_github(timeout: int = 20) -> dict[str, Any]:
    """EXPLICIT network refresh (bounded). Returns the refreshed plan/status.

    Never called by health/doctor; the UI refresh button and
    ``nexus update check`` are the only intended callers.
    """
    try:
        from nexus_scalp.release.updater import UpdateOrchestrator

        plan = UpdateOrchestrator().check(timeout=timeout)
        status = STATUS_OFFLINE
        if plan.get("status") == "UPDATE_AVAILABLE":
            status = STATUS_VERSION_UPDATE
        elif plan.get("status") == "NO_UPDATE":
            status = STATUS_NO_UPDATE
        out = build_release_status()
        out["refresh"] = {
            "attempted": True,
            "network": "OK",
            "plan_status": plan.get("status"),
            "target_version": plan.get("target_version"),
            "correlation_id": plan.get("correlation_id"),
        }
        if status in (STATUS_VERSION_UPDATE, STATUS_NO_UPDATE):
            out["update_status"] = status
            out["available_version"] = plan.get("target_version")
            out["available_commit"] = plan.get("commit_sha")
        return out
    except Exception as exc:  # network failure is a truth, not a crash
        out = build_release_status()
        out["refresh"] = {"attempted": True, "network": "FAILED", "error": str(exc)[:200]}
        out["update_status"] = STATUS_OFFLINE
        return out
