"""EU-03 lazy sub-app registration — keep ``nexus help`` / ``--version`` slim.

WHERE/WHY: ``nexus_scalp.cli.model_studio_commands`` pulls
``nexus_scalp.web.model_studio_routes``, which imports ``torch`` (~4s warm,
worse cold); ``nexus_scalp.cli.gateway_commands`` pulls fastapi (which itself
imports torch + polars); ``nexus_scalp.cli.ai_commands`` pulls
``nexus_scalp.ai_providers.orchestrator`` (~1s) and ``dependency_commands``
pulls networkx (~0.5s) — all AT IMPORT TIME. Importing them eagerly from
``app_factory`` made every CLI invocation, even ``nexus --version``, pay the
full neural/provider/graph stack. That violates the same EU-03 invariant
``engine_boot._heavy_*`` shims uphold: ``nexus help`` must not pay the
torch/polars/networkx import cost.

HOW (see ORDER below for the Click mechanics): those sub-apps are now imported
and registered the first time the CLI actually DISPATCHES a subcommand or
materializes the full command tree — never merely to print the version or the
help. Command bodies are UNCHANGED; only WHERE they get imported from moved.

ORDER (Typer 0.9+ / Click 8.x, verified by tests/unit/test_cli_lazy_imports.py):
  ``app(prog_name='nexus')`` -> ``typer.main.get_command(app)`` builds ONE
  Click ``TyperGroup`` from ``app.registered_commands``/``registered_groups``
  -> ``Group.invoke``: eager ``--version``/``--help`` option callbacks fire
  FIRST and exit; only then does the group ``resolve_command`` look the
  subcommand up. ``LazyRootGroup`` therefore registers on the FIRST lookup that
  misses — after ``--version``/``--help`` have already had their chance to exit,
  and before any subcommand (``nexus model-quality --help`` included) resolves.
  The lookup that misses rebuilds the command dict in place, so resolution and
  ``--help`` rendering see the freshly imported commands in the same run.
"""

from __future__ import annotations

from typing import Any


def _heavy_model_studio() -> None:
    """EU-03 shim: import + register ``nexus model-*``/``dataset-*``/``position-*``.

    Defers ``nexus_scalp.web.model_studio_routes`` (torch) into the first
    ``nexus model-quality``-style invocation. The command module registers on
    the canonical ``app`` the moment it is imported (import-side-effect
    registration, unchanged from the eager era).
    """
    try:
        import nexus_scalp.cli.model_studio_commands  # noqa: F401  (side effect: registers)
    except Exception as exc:  # pragma: no cover - guarded like the eager block was
        from nexus_scalp.cli.styling import console

        console.print(f"[yellow]Model Studio commands unavailable: {exc}[/yellow]")


def _heavy_ai_commands() -> None:
    """EU-03 shim: import + register ``nexus ai ...`` (provider/market stack)."""
    try:
        import nexus_scalp.cli.ai_commands as _ai
        from nexus_scalp.cli.app_factory import app

        _ai.register_ai_commands(app)
    except Exception as exc:  # pragma: no cover - guarded like the eager block was
        from nexus_scalp.cli.styling import console

        console.print(f"[yellow]AI commands unavailable: {exc}[/yellow]")


def _heavy_dependency_commands() -> None:
    """EU-03 shim: import + register ``nexus dependency ...`` (networkx)."""
    try:
        import nexus_scalp.cli.dependency_commands as _dep
        from nexus_scalp.cli.app_factory import app

        _dep.register_dependency_commands(app)
    except Exception as exc:  # pragma: no cover - guarded like the eager block was
        from nexus_scalp.cli.styling import console

        console.print(f"[yellow]Dependency commands unavailable: {exc}[/yellow]")


def _heavy_gateway_commands() -> None:
    """EU-03 shim: import + register ``nexus gateway ...`` (fastapi+torch chain)."""
    try:
        import nexus_scalp.cli.gateway_commands  # noqa: F401  (side effect: registers)
    except Exception as exc:  # pragma: no cover
        from nexus_scalp.cli.styling import console

        console.print(f"[yellow]Gateway commands unavailable: {exc}[/yellow]")


_LAZY_GROUPS: tuple[Any, ...] = (
    _heavy_model_studio,
    _heavy_ai_commands,
    _heavy_dependency_commands,
    _heavy_gateway_commands,
)


_DONE: dict[str, bool] = {"ran": False}


def register_lazy_subapps() -> None:
    """Import-and-register the heavy sub-apps exactly once per process.

    Called from the lazy group's ``get_command`` (see ``app_factory``) the
    first time Click resolves a subcommand that is not already registered,
    and guarded so re-entry is a no-op.
    """
    if _DONE["ran"]:
        return
    for register in _LAZY_GROUPS:
        try:
            register()
        except Exception:
            # Same isolation contract the eager try/except blocks had: one
            # missing optional subsystem must never break the rest of the CLI.
            continue
    _DONE["ran"] = True


# ---------------------------------------------------------------------------
# Typer/Click hook: Typer builds the Click Group once per process from
# ``registered_commands`` (typer.main.get_group_from_info), and Click resolves
# the subcommand through Group.get_command BEFORE the root callback body runs.
# A root-callback registration therefore lands too late — the subcommand is
# already unknown. The class below overrides ``get_command`` so the heavy
# sub-apps are imported + registered on the FIRST unknown-subcommand lookup,
# which is exactly where Click performs the resolution. ``--version`` /
# ``--help`` / ``nexus help`` never resolve a real subcommand through this
# path, so they stay torch-free.
# ---------------------------------------------------------------------------


def _command_names_on_disk() -> list[str] | None:
    """Names of commands a lazy module WILL register, without importing it.

    WHY: ``nexus --help`` / ``nexus help`` render the full command list, and
    Click enumerates it via ``Group.list_commands``. Registering the heavy
    modules there (the pre-wave-6 behavior) made every help pay torch (~4s)
    — the exact EU-03 invariant this file exists to uphold. The table in
    ``_lazy_manifest`` is the static registration contract of each lazy
    module, so the help listing can show every command while the import
    stays deferred.

    Returns None when the table is unavailable (caller keeps the fallback).
    """
    try:
        from nexus_scalp.cli._lazy_manifest import LAZY_COMMAND_NAMES

        return list(LAZY_COMMAND_NAMES)
    except Exception:
        return None


class _ManifestCommand:
    """A Click-command stand-in rendered from manifest metadata (no imports).

    WHY: typer's rich help path (``rich_utils.rich_format_help``) enumerates
    the command list and calls ``get_command(name)`` PER listed command, then
    reads ``name``/``short_help``/``help``/``deprecated``/``hidden`` off the
    result to build the Commands panel. Returning None makes typer silently
    DROP the command from ``nexus --help``; building the real Click command
    resolves its callback — i.e. imports torch. This stand-in answers exactly
    the attributes that panel reads, so the help listing is complete and
    byte-identical to the eager era, torch-free. It is never invoked: a real
    dispatch always goes through Click's ``resolve_command`` first, which
    materializes the genuine command in ``get_command`` below.
    """

    def __init__(self, name: str, help_text: str) -> None:
        self.name = name
        self.short_help = help_text
        self.help = help_text
        self.deprecated = False
        self.hidden = False
        #: rich panel grouping — default panel, same as every eager command.
        self.rich_help_panel = None  # type: ignore[assignment]


def _manifest_lookup(name: str) -> _ManifestCommand | None:
    """Return a manifest stand-in for a lazy command, or None if unknown."""
    from nexus_scalp.cli._lazy_manifest import LAZY_COMMAND_SHORT_HELP

    help_text = LAZY_COMMAND_SHORT_HELP.get(name)
    if help_text is None:
        return None
    return _ManifestCommand(name, help_text)


def lazy_group_cls(cls: Any) -> Any:
    """Return a TyperGroup subclass that registers heavy sub-apps on demand.

    ``cls`` is the TyperGroup subclass Typer would have used (``Typer(cls=)``).
    Passing it in keeps the override composable with any other group class.

    Typer materializes this group ONCE per process (``get_group_from_info``)
    from ``app.registered_commands``. Importing a command module later adds it
    to the Typer app's registry but NOT to this already-built Click group, so
    after registering we re-materialize the app's command table into ourselves
    (``_refresh`` below). ``get_command`` is the single hook Click uses for
    subcommand resolution, so a lazy import + refresh there lands exactly
    before dispatch; ``--version`` exits before any of this and stays
    torch-free.
    """
    base = cls if cls is not None else _default_typer_group()

    class LazyTyperGroup(base):  # type: ignore[misc, valid-type]
        #: Set only while our ``format_help``/``rich_format_help`` overrides
        #: are drawing the command panel. ``get_command`` returns the
        #: torch-free manifest stand-in for unknown lazy names while this is
        #: True; a real dispatch never sees it raised.
        _rendering_help = False

        def get_command(self, ctx: Any, name: str) -> Any:
            cmd = super().get_command(ctx, name)
            if cmd is not None:
                return cmd
            # EU-03: Click/Typer help rendering (rich_format_help) walks
            # get_command() for every name list_commands() advertised, purely
            # to draw the one-line summary. Importing the heavy modules there
            # made `nexus help` pay torch (~4s). For a help-only resolution we
            # return a lightweight stand-in whose short help comes from the
            # manifest, so the listing is complete and correct torch-free. A
            # real dispatch imports the module and refreshes, then resolves.
            if self._rendering_help:
                return _manifest_lookup(name)
            register_lazy_subapps()
            _refresh_commands(self)
            return super().get_command(ctx, name)

        def format_help(self, *a: Any, **kw: Any) -> Any:
            self._rendering_help = True
            try:
                return super().format_help(*a, **kw)
            finally:
                self._rendering_help = False

        def rich_format_help(self, *a: Any, **kw: Any) -> Any:
            self._rendering_help = True
            try:
                return super().rich_format_help(*a, **kw)
            finally:
                self._rendering_help = False

        def list_commands(self, ctx: Any) -> Any:
            # EU-03: ``nexus --help`` / ``nexus help`` enumerate the full
            # command tree here. Registering the heavy modules in this hook
            # (the pre-wave-6 behavior) made every help pay torch (~4s) —
            # the exact invariant this module exists to uphold. The names are
            # advertised from a static manifest instead, so the listing shows
            # every command while the import stays deferred; a real dispatch
            # lands in ``get_command`` and imports then. ``--version`` exits
            # before any of this and stays torch-free.
            names = list(super().list_commands(ctx))
            extra = _command_names_on_disk()
            if extra:
                seen = set(names)
                for n in extra:
                    if n not in seen:
                        names.append(n)
                        seen.add(n)
            # Every listed name is needed for `nexus --help`, so the disk scan
            # is harmless (no imports, no torch). It runs unconditionally.
            return names

    return LazyTyperGroup


def _refresh_commands(group: Any) -> None:
    """Re-materialize the Typer app's registered commands into a built group.

    ``typer.main.get_group_from_info`` is the only canonical builder, so this
    builds a FRESH group from the same app and copies its command table over.
    Idempotent: once the heavy modules are imported their commands are already
    here, and the guard in ``register_lazy_subapps`` prevents re-importing.

    NOTE: this is deliberately called only AFTER ``register_lazy_subapps`` has
    imported the heavy modules (the one-shot guard above). Building a group
    from Typer's ``registered_commands`` resolves every command's signature and
    docstring, which for the model-studio commands walks into torch — doing it
    before the import would merely re-pay the same cost twice.
    """
    app = _root_app()
    if app is None:
        return
    from typer.main import get_group_from_info
    from typer.models import TyperInfo

    fresh = get_group_from_info(
        TyperInfo(app),
        pretty_exceptions_short=app.pretty_exceptions_short,
        suggest_commands=app.suggest_commands,
        rich_markup_mode=app.rich_markup_mode,
    )
    for name, cmd in fresh.commands.items():
        if name not in group.commands:
            group.commands[name] = cmd


def _root_app() -> Any:
    """Return the root Typer app (imported lazily to keep this module light)."""
    try:
        from nexus_scalp.cli.app_factory import app

        return app
    except Exception:
        return None


def _default_typer_group() -> Any:
    from typer.main import TyperGroup

    return TyperGroup
