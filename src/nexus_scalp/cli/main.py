"""Nexus Scalp Engine CLI Management Console — compatibility facade.

WHERE/WHY: the historical ``nexus_scalp.cli.main`` module was decomposed
(CHG-0032-A1 Step 1) into responsibility modules: app_factory (Typer app + sub-app
wiring), styling (console/palette/output helpers), doctor (operational/diagnostic/
model commands), update_cli (update + release metadata), wizard (setup/uninstall)
and engine_boot (start/stop/restart/run). This facade keeps the import contract for
the 12+ importers: ``from nexus_scalp.cli.main import app`` (release/packaged_main.py
EXE entry + release/cli_shim.py console script + tests) and attribute access via
``import nexus_scalp.cli.main as cmain`` (monkeypatching in tests). Importing this
module still builds the FULL command tree (import-time construction preserved).

BOUNDARY: re-exports + registration side effects ONLY — no logic lives here.
New code must import from the specific sibling module, not the facade.

USED BY: release/packaged_main.py (packaged EXE), release/cli_shim.py (``nexus``/
``nse`` console scripts), web/server.py tests, forensics gate hooks
(``python -m nexus_scalp.cli.main forensic --deploy-gate``), 10+ test files.

DO-NOT-PUT-HERE: any command body, helper, or styling code.
"""

from __future__ import annotations

# Facade leaf rule: cli/* modules must NOT import cli.main — the facade imports
# THEM (below) and stays the import-graph leaf. Registration order mirrors the
# original monolith so command help order is preserved.
import os  # re-export: tests use cmain.os
import subprocess  # re-export: tests use cmain.subprocess
import time  # re-export: tests use cmain.time

# Full-path module imports: each import REGISTERS its commands on app (side
# effect) and gives the dependency graph the same module edges the monolith had.
import nexus_scalp.cli.doctor as doctor  # noqa: PLR0402  (full path = graph edge)
import nexus_scalp.cli.engine_boot as engine_boot  # noqa: PLR0402  (full path = graph edge)
import nexus_scalp.cli.update_cli as update_cli  # noqa: PLR0402  (re-exports only; command registration happens via doctor's parity import)
import nexus_scalp.cli.wizard as wizard  # noqa: PLR0402  (full path = graph edge)

# CHG-0032-A1 help-order parity: the monolith registered the legacy config-validate
# duplicate + model-* family AFTER start/stop/restart/run. doctor defers that block;
# it registers here, after engine_boot commands.
doctor._register_late_commands()
from nexus_scalp.cli.app_factory import app
from nexus_scalp.cli.engine_boot import _pidfile, _run_engine, _spawn_daemon
from nexus_scalp.cli.styling import console
from nexus_scalp.cli.update_cli import _update_exit_code, _update_orchestrator
from nexus_scalp.cli.wizard import _get_network_endpoints
from nexus_scalp.release import evaluate as reval  # re-export: tests patch cmain.reval
from nexus_scalp.release.metadata import get_version_info  # re-export: tests patch this seam

__all__ = [
    "_get_network_endpoints",
    "_pidfile",
    "_run_engine",
    "_spawn_daemon",
    "_update_exit_code",
    "_update_orchestrator",
    "app",
    "console",
    "doctor",
    "engine_boot",
    "get_version_info",
    "os",
    "reval",
    "subprocess",
    "time",
    "update_cli",
    "wizard",
]


if __name__ == "__main__":
    app()
