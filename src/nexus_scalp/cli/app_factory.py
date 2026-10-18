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
from nexus_scalp.cli.incident_commands import incidents_app
from nexus_scalp.cli.lazy_subapps import lazy_group_cls
from nexus_scalp.release.metadata import PRODUCT_DISPLAY

# LAZY (EU-03, wave-6 perf): ai_commands (ai_providers.orchestrator),
# dependency_commands (networkx), gateway_commands (fastapi -> torch+polars)
# and model_studio_commands (model_studio_routes -> torch) are NOT imported
# here anymore — see cli/lazy_subapps.py. Importing them eagerly made even
# `nexus --version` pay the whole neural/provider/graph stack; the group
# class below registers them on the first subcommand resolution instead.

app: typer.Typer = typer.Typer(
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
    # EU-03 (wave-6 perf): the heavy sub-apps above are imported and registered
    # ON DEMAND by this group's ``get_command`` (first subcommand resolution),
    # not eagerly at import. ``nexus --version`` / ``nexus help`` never resolve
    # a subcommand, so they stay torch-free. See cli/lazy_subapps.py.
    cls=lazy_group_cls(None),
)


# ---------------------------------------------------------------------------
# `nexus --version` (EU-RELEASE-001): the universal CLI convention was a
# Typer usage error (exit 2, "No such option: --version"). Typer has no
# built-in root version flag, so it is provided by a no-command callback.
# The command form `nexus version` stays the detailed surface (--json /
# --plain + full build identity); this flag prints the SAME one-line
# identity `nexus version --plain` produces, byte-for-byte — the format is
# duplicated from cli/doctor.py::version_cmd so a divergence shows up as a
# failing test instead of two disagreeing user-visible outputs.
# ---------------------------------------------------------------------------
def _version_callback(show: bool) -> None:
    if not show:
        return
    from nexus_scalp.release.metadata import PRODUCT_DISPLAY, get_version_info

    # Same test-patchable seam `nexus version` uses (tests monkeypatch
    # get_version_info on the cli.main facade); a callback that bypassed it
    # would report a different identity than the command form.
    info = _resolve_facade_seam("get_version_info", get_version_info)()
    commit_txt = (
        str(info.get("commit"))
        if info.get("commit")
        else str(info.get("commit_status") or "NOT_RECORDED")
    )
    print(
        f"{PRODUCT_DISPLAY} version {info.get('version')} "
        f"({info.get('channel')}, {info.get('architecture')}, commit {commit_txt})"
    )
    raise typer.Exit(0)


@app.callback()
def _nexus_root(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the application version and exit (same as `nexus version --plain`).",
        is_eager=True,
        callback=_version_callback,
    ),
) -> None:
    """Nexus root options (no subcommand required)."""
    return None


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
# AI PROVIDER ECOSYSTEM (``nexus ai ...``) — same backend contracts as the API
# and the UI (Section 61: backend is the source of truth). LAZY (EU-03): the
# orchestrator import chain (~1s) is deferred to first use — see lazy_subapps.
# Dependency Intelligence (``nse dependency``) — LAZY (EU-03): networkx import
# chain deferred to first use, same invariant as ``nexus help`` must not pay.
# API PLATFORM v1 (``nexus api ...``) — same HTTP contracts as external clients.
from nexus_scalp.cli.api_commands import api_app  # noqa: E402  (registration side effect)

app.add_typer(
    api_app,
    name="api",
    help="Query the versioned /api/v1 platform of a running engine.",
)
# SMOKE — production E2E layered verification (brief sections 17/26)
from nexus_scalp.cli.smoke_commands import smoke_app as _smoke_app  # noqa: E402

app.add_typer(
    _smoke_app,
    name="smoke",
    help="Production E2E smoke (layered runtime verification).",
)
# RUNTIME SAFETY (``nexus risk status|release``) — explicit release surface for
# persisted HALT/KILL_SWITCH states (runtime-safety mission P0).
from nexus_scalp.cli.risk_commands import risk_app as _risk_app  # noqa: E402

app.add_typer(
    _risk_app,
    name="risk",
    help="Runtime safety state: inspect and explicitly release persisted halts.",
)
# MODEL STUDIO — Neural inspection, prediction, stress testing, and training
# (ML-UI-001). LAZY (EU-03, wave-6 perf): importing this module eagerly pulled
# nexus_scalp.web.model_studio_routes -> torch (~4s), which every CLI invocation
# paid even for `nexus --version`. It is now imported on first use by the lazy
# Typer group — see cli/lazy_subapps.py. Same for AI/dependency/gateway: heavy
# stacks (orchestrator/networkx/fastapi) moved to lazy registration for the
# same latency reason as the _heavy_* shims.

# PR EVIDENCE REPORTER — file-level failure intelligence inside any PR
# (``nexus pr report [--pr N] [--watch] [--json]``). Guarded like the other
# optional sub-apps so a missing dependency can never break the CLI import.
try:
    from nexus_scalp.cli.pr_commands import register_pr_commands as _register_pr_commands

    _register_pr_commands(app)
except Exception:
    pass


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
