"""``nexus help`` subcommand.

Adds a first-class ``help`` command to the canonical Typer CLI so both
``nexus --help`` (Click-standard) and ``nexus help`` (word form, the UX
contract in docs/CLI.md) resolve to the same authoritative help surface.

Design notes:
    * Zero side effects: only renders the app's own help via Click. No
      network, no DB, no model load, no MT5.
    * Delegates to the real Click command tree (single source of truth) - it
      must never drift from ``nexus --help``.
    * Exit code 0 on success; unknown topic is a usage error (exit 2) with a
      readable error panel, never a traceback.

REGISTRATION ORDER NOTE (CHG-0032 help-order parity): this module is imported
by cli.main BEFORE the late command registration block, so ``help`` appears
near the top of the command list. Do not move the import after late commands
without re-running the --help golden test in tests/cli/.
"""

from __future__ import annotations

import typer

from nexus_scalp.cli.styling import _error_panel


def register_help_command(app: typer.Typer) -> None:
    """Attach the ``help`` command to ``app`` (called from cli.main wiring)."""

    @app.command(
        "help", context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
    )
    def help_cmd(
        ctx: typer.Context,
        topic: str | None = typer.Argument(
            None, help="Command name to show detailed help for (e.g. 'nexus help start')."
        ),
    ) -> None:
        """Show the command reference (same surface as --help)."""
        import click
        import typer as _typer

        from nexus_scalp.cli.app_factory import app as root_app

        # Resolve the real Click tree from the canonical app (single source of
        # truth: this can never drift from `nexus --help`).
        try:
            click_root = _typer.main.get_command(root_app)
        except Exception:  # pragma: no cover - defensive; help never hard-fails
            click_root = None
        if click_root is None:  # pragma: no cover
            typer.echo("help: unable to build the command reference (internal)", err=True)
            raise typer.Exit(2)

        # Word form with no topic: mirror the exact --help surface.
        if not topic:
            import sys as _sys

            # typer bundles its OWN vendored click; mixing the standalone click
            # Context with the vendored-tree command is a type-level mismatch
            # (and a real runtime hazard across click versions). Always take
            # Context from the SAME module the command tree was built with.
            from typer import _click as _typer_click

            if _sys.platform.startswith("win"):
                # Some Click/Typer combos dislike --help re-entry under the
                # shim; render into stdout without standalone re-invocation.
                _typer_click.echo(
                    click_root.get_help(_typer_click.Context(click_root, info_name="nexus"))  # type: ignore[arg-type]
                )
                raise typer.Exit(0)
            click_root.main(args=["--help"], standalone_mode=False)
            raise typer.Exit(0)

        # Topic form: render that command's own --help. The vendored-click
        # MultiCommand exposes `commands`; getattr guard keeps mypy happy.
        commands_map = getattr(click_root, "commands", None)
        cmd = commands_map.get(topic) if commands_map else None
        if cmd is None:
            from nexus_scalp.cli.styling import console as _console

            _console.print(
                _error_panel(
                    "No such command",
                    f"'nexus {topic}' does not exist.",
                    hint="Run 'nexus help' for the command list.",
                    exit_code=2,
                )
            )
            raise typer.Exit(2)

        typer.echo(cmd.get_help(click.Context(cmd, info_name=f"nexus {topic}")))
        raise typer.Exit(0)
