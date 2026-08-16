"""`nexus` console-script shim.

Installed as ``nexus`` (and ``nse``) by the release build; delegates to the
canonical Typer CLI in ``nexus_scalp.cli.main``. Keeping this tiny import
bootstrap here means the packaged CLI entrypoint never depends on the current
working directory.
"""

from __future__ import annotations

from nexus_scalp.cli.main import app

if __name__ == "__main__":
    app()