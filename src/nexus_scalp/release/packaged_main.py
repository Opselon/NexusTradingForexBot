"""PyInstaller entrypoint for the packaged Nexus Scalp Engine distribution.

The packaged executable must ALWAYS present the release CLI (`nexus`), with
runtime data rooted at the bundled tree. The original argparse launcher
(NexusTradingForexBot.py) remains the source/IDE entrypoint; this shim is the
production entrypoint used by all release artifacts (onedir EXE, onefile CLI).
Legacy compatibility: running the packaged EXE with no arguments starts the
engine in PAPER mode (symbol XAUUSD), exactly like the launcher,
but through the safe Typer path -- LIVE never starts without confirmation.
"""

from __future__ import annotations

import contextlib
import sys

# WINDOWS-UX-001 (taskbar identity): the packaged EXE is the product's real
# Windows process. Pin the AppUserModelID + console title BEFORE the CLI is
# imported and anything renders, so the taskbar entry is created under the
# product identity instead of falling back to the bootloader image name
# (which is what made the taskbar show "python"). Failure-isolated: branding
# never blocks the boot. Non-Windows: no-op.
from nexus_scalp.platform.windows_identity import (
    apply_windows_identity,
)

apply_windows_identity()

from nexus_scalp.cli.main import app  # noqa: E402
from nexus_scalp.release.metadata import CLI_PROGRAM_NAME  # noqa: E402

if __name__ == "__main__":
    # BUG-145: packaged EXE under a double-click console (cp1252/cp437) crashed
    # with UnicodeEncodeError on the rich banner glyphs. Reconfigure stdio to
    # UTF-8 with replacement so the UI can never hard-kill the launch.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr,attr-defined]
    # Double-click / bare-EXE parity: `NexusScalpEngine.exe` with NO args
    # must start the engine (default PAPER), not print `Missing command`.
    # This matches NexusTradingForexBot.py legacy no-args-run and README
    # quick-start ("1. Run NexusScalpEngine.exe").
    if len(sys.argv) == 1:
        # Portable bare launch is ALWAYS paper+xauusd (safe) — explicit live needs `start --mode live`.
        # This also satisfies the user request: default symbol XAUUSD for now.
        sys.argv.extend(["start", "--mode", "paper"])
    # EU-RELEASE-002: canonical program name ensures the help/error output
    # matches the documented `nexus` interface even when launched via the
    # packaged NexusScalpEngine.exe executable directly.
    sys.exit(app(prog_name=CLI_PROGRAM_NAME))
