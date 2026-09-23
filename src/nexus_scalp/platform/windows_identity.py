"""Windows taskbar / shell application identity for the NSE desktop process.

WHY (WINDOWS-UX-001, taskbar identity): NSE is a console application whose
real, running process on a user's desktop is either ``python.exe`` /
``pythonw.exe`` (source / IDE launch) or the PyInstaller bootloader process
of ``NexusScalpEngine.exe`` (packaged launch). Windows derives the taskbar
button's *label, icon and grouping* from the process's AppUserModelID, and
when an application never sets one, the shell falls back to the hosting
executable image name. That is why a normally-launched NSE shows up on the
taskbar as ``python`` / ``Python`` instead of as the product. Setting the
window title alone does not change the taskbar label — the shell keys the
taskbar entry on the AppUserModelID, not on ``SetWindowText``.

This module is the ONE canonical place that owns the identity:

    TASKBAR_APP_ID     stable, deterministic AppUserModelID
    TASKBAR_APP_NAME   visible taskbar/window text  ("NexusTraderBot")
    apply_windows_identity()   the single startup invocation

Contract (``apply_windows_identity``):
  * no-op on non-Windows platforms (import-safe everywhere, Linux CI green);
  * guarded ``ctypes`` binding, never an unconditional Windows-only import;
  * failure-isolated — a branding call can NEVER block the engine boot;
  * must run BEFORE the console window is first shown / banner rendered,
    because the shell pins the taskbar entry at window creation;
  * idempotent and stable across launches (same ID every time, so restarts
    and relaunches group into ONE taskbar entry — no ghost "Python" group).

The visible name is deliberately the short product label ``NexusTraderBot``.
The AppUserModelID is a stable internal identifier (``Opselon.NexusTraderBot``)
and is never displayed to the user; relaunch/taskbar grouping keys off it.
"""

from __future__ import annotations

import contextlib
import sys
from typing import Any

#: Stable, deterministic Windows AppUserModelID. Company-qualified, reversed
#: per Windows convention, unique to this product, and identical on every
#: launch (restarts/relaunches therefore group into ONE taskbar entry).
TASKBAR_APP_ID = "Opselon.NexusTraderBot"

#: Visible taskbar / console-window label. This is the user-facing product
#: name; the AppUserModelID above stays internal and is never displayed.
TASKBAR_APP_NAME = "NexusTraderBot"

_logger: Any = None


def _mod_logger() -> Any:
    """Resolve the module logger lazily (never a Windows-only import at top)."""
    global _logger  # noqa: PLW0603 - lazy singleton; the alternative is a
    # circular import at module load (observability.logging imports the whole
    # logging stack), which this module MUST avoid: it runs at the very first
    # line of every entrypoint, before the engine bootstraps.
    if _logger is None:
        from nexus_scalp.observability.logging import get_logger

        _logger = get_logger("nexus_scalp.platform.windows_identity")
    return _logger


def _set_taskbar_identity() -> bool:
    """Set the AppUserModelID + console window title. Windows-only, private.

    Returns True when the shell accepted the AppUserModelID. Never raises.
    """
    import ctypes

    shell = ctypes.windll.shell32  # type: ignore[attr-defined]
    kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]

    hres = int(shell.SetCurrentProcessExplicitAppUserModelID(TASKBAR_APP_ID))
    # The console title is what the taskbar shows as the window text once the
    # AppUserModelID has pinned the entry; keep both in the same call so the
    # visible label and the grouping identity can never disagree.
    with contextlib.suppress(Exception):
        kernel.SetConsoleTitleW(TASKBAR_APP_NAME)  # type: ignore[attr-defined]
    return hres == 0  # S_OK


def apply_windows_identity() -> bool:
    """Establish the Windows taskbar identity for THIS process.

    Call exactly once, as early as startup allows — before the console window
    is shown and before any GUI/taskbar registration happens. Safe to call on
    any platform: non-Windows returns ``False`` without touching anything.

    Returns ``True`` only when the AppUserModelID was actually applied to the
    running Windows process (so callers can report honest evidence).
    """
    if sys.platform != "win32":
        return False
    try:
        return _set_taskbar_identity()
    except Exception as exc:
        try:
            _mod_logger().warning("Windows taskbar identity not applied (non-fatal): %s", exc)
        except Exception:
            pass
        return False


def current_app_user_model_id() -> str | None:
    """Read back the process AppUserModelID (verification path).

    Returns the ID string set by ``apply_windows_identity`` (or by an
    external caller), or ``None`` on non-Windows / when the shell reports
    no explicit ID. Never raises.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        shell = ctypes.windll.shell32  # type: ignore[attr-defined]
        ptr = ctypes.c_wchar_p()
        # Returns S_OK (0) on success and leaves ptr NULL otherwise.
        if int(shell.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(ptr))) != 0:
            return None
        value = ptr.value
        if value:
            # The shell allocates the string with CoTaskMemAlloc; free it to
            # avoid a handle leak on every readback.
            ctypes.windll.ole32.CoTaskMemFree(ptr)  # type: ignore[attr-defined]
        return value or None
    except Exception:
        return None


__all__ = [
    "TASKBAR_APP_ID",
    "TASKBAR_APP_NAME",
    "apply_windows_identity",
    "current_app_user_model_id",
]
