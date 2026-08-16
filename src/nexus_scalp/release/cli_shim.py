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

from nexus_scalp.cli.main import app

if __name__ == "__main__":
    app()
