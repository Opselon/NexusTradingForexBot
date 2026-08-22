"""PyInstaller entrypoint for the packaged Nexus Scalp Engine distribution.

The packaged executable must ALWAYS present the release CLI (`nexus`), with
runtime data rooted at the bundled tree. The original argparse launcher
(NexusTradingForexBot.py) remains the source/IDE entrypoint; this shim is the
production entrypoint used by all release artifacts (onedir EXE, onefile CLI).
Legacy compatibility: running the packaged EXE with no arguments starts the
engine in the configured (default PAPER) mode, exactly like the launcher,
but through the safe Typer path -- LIVE never starts without confirmation.
"""

from __future__ import annotations

import sys

from nexus_scalp.cli.main import app

if __name__ == "__main__":
    # Double-click / bare-EXE parity: `NexusScalpEngine.exe` with NO args
    # must start the engine (default PAPER), not print `Missing command`.
    # This matches NexusTradingForexBot.py legacy no-args-run and README
    # quick-start ("1. Run NexusScalpEngine.exe").
    if len(sys.argv) == 1:
        sys.argv.append("start")
    sys.exit(app())
