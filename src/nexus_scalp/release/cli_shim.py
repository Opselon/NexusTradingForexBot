"""`nexus` console-script shim.

Installed as ``nexus`` (and ``nse``) by the release build; delegates to the
canonical Typer CLI in ``nexus_scalp.cli.main``. Keeping this tiny import
bootstrap here means the packaged CLI entrypoint never depends on the current
working directory.

NOTE: do NOT wrap ``app()`` in ``sys.exit()`` — Typer raises SystemExit itself
(0 for --help/version, 2 for usage errors, and the code our commands raise).
Wrapping it in sys.exit() would turn PyInstaller's frozen exit propagation
into an exit code of 1 on --help.
"""

from __future__ import annotations

import contextlib
import sys

# WINDOWS-UX-001: the frozen CLI EXE is a product process on the user's
# taskbar too. Pin the AppUserModelID before the CLI renders so the taskbar
# entry groups with the engine under one stable identity instead of the
# bootloader image name. Non-Windows: no-op. Failure-isolated.
from nexus_scalp.platform.windows_identity import (
    apply_windows_identity,
)

apply_windows_identity()

from nexus_scalp.cli.main import app  # noqa: E402
from nexus_scalp.release.metadata import CLI_PROGRAM_NAME  # noqa: E402

if __name__ == "__main__":
    # BUG-145/147: frozen consoles default to legacy code pages (cp1252/437).
    # Reconfigure stdio to UTF-8 with replacement so rich banner glyphs can
    # never hard-kill the launch (double-click + CLI parity).
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr,attr-defined]
    # EU-RELEASE-002: pin the program name so the usage/help surface always
    # reads `nexus` (docs/CLI.md contract), never the entry-point filename
    # this shim was invoked as (console script path, NexusScalpEngine-CLI.exe).
    app(prog_name=CLI_PROGRAM_NAME)
