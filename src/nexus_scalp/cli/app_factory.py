"""Typer application factory — THE canonical ``nexus`` CLI object.

WHERE/WHY: constructs the ``app`` (Typer) at IMPORT TIME and attaches every
sub-application: ``db`` + ``db-portability`` (TASK-10/11), ``incidents`` (TASK-12),
``analyze`` (G29) and ``dependency``. Importing this module builds the full command
tree — same side effect the monolithic cli/main.py always had (release/packaged_main.py
and release/cli_shim.py rely on import-time construction; CHG-0032 Step 1 keeps that
contract byte-identical).

BOUNDARY: app wiring ONLY. Command bodies live in the sibling modules (doctor,
update_cli, engine_boot, wizard) which import ``app`` from here and register
themselves via decorators. Do NOT put engine/boot logic or presentation code here.

USED BY: cli.doctor, cli.update_cli, cli.engine_boot, cli.wizard, cli.main (facade),
release.packaged_main, release.cli_shim, pyproject entry point ``nse``.

DO-NOT-PUT-HERE: command implementation bodies, styling/palette code, update-engine glue.
"""

from __future__ import annotations

import sys
from typing import Any

import typer

from nexus_scalp.cli.analyze_commands import register_analyze_commands
from nexus_scalp.cli.db_commands import db_app, make_portability_app
from nexus_scalp.cli.dependency_commands import register_dependency_commands
from nexus_scalp.cli.incident_commands import incidents_app
from nexus_scalp.release.metadata import PRODUCT_DISPLAY

app = typer.Typer(
    name="nexus",
    help=(
        f"{PRODUCT_DISPLAY} — your trading desk in one command.\n\n"
        "Start safe:  nexus start                          (PAPER, no broker)\n"
        "Check health: nexus doctor / nexus status\n"
        "Stay current: nexus update check  →  nexus update --dry-run  →  nexus update\n"
        "Need help:   nexus help  ·  nexus help <command>\n"
        "Tip: every command supports --help and --json."
    ),
    add_completion=False,
    rich_markup_mode="rich",
)

# ---------------------------------------------------------------------------
# DB migration & schema management (TASK-10) — same canonical engine as startup
# ---------------------------------------------------------------------------
# TASK-10 ``db`` group; TASK-11 hygiene registers as a SUBCOMMAND of ``db``
# so the spec surface is ``nexus db hygiene status|plan|run|pause|resume|history``.
app.add_typer(db_app, name="db", help="Database schema migration and management.")

# DATABASE PORTABILITY (``nexus db-portability ...``) — SQLite <-> PostgreSQL workflow.
app.add_typer(
    make_portability_app(),
    name="db-portability",
    help="DATABASE PORTABILITY: provider status, config, SQLite->PostgreSQL migration.",
)
# TASK-12 incident response & forensic diagnostics (``nexus incidents ...``).
app.add_typer(
    incidents_app,
    name="incidents",
    help="Incident investigation and forensic diagnostics (read-only by default).",
)
# G29: Enterprise Code Analyzer (``nse analyze``)
register_analyze_commands(app)
# Dependency Intelligence (``nse dependency``)
register_dependency_commands(app)
# API PLATFORM v1 (``nexus api ...``) — same HTTP contracts as external clients.
from nexus_scalp.cli.api_commands import api_app  # noqa: E402  (registration side effect)

app.add_typer(
    api_app,
    name="api",
    help="Query the versioned /api/v1 platform of a running engine.",
)


def _resolve_facade_seam(name: str, default: Any) -> Any:
    """Late-binding seam for test monkeypatching through the cli.main facade.

    Historical contract (CHG-0032 Step 1): tests patch attributes on the
    ``nexus_scalp.cli.main`` module (e.g. get_version_info, _run_engine,
    _spawn_daemon). After the decomposition the command bodies live in sibling
    modules, so they resolve these seams AT CALL TIME from the facade when it is
    present in ``sys.modules`` - cycle-free (no static import of cli.main; the
    import graph stays acyclic with the facade as leaf) and patch-transparent
    (unpatched callers resolve to ``default``).
    """
    module = sys.modules.get("nexus_scalp.cli.main")
    if module is not None and hasattr(module, name):
        return getattr(module, name)
    return default
