"""``python -m nexus_scalp.cli`` entry.

Shares the canonical Typer app from :mod:`nexus_scalp.cli.main`. The program
name is fixed to the product's CLI name (EU-RELEASE-002) so the ``--help``
usage line reads ``Usage: nexus ...`` here and under every other entry point
(packaged ``NexusScalpEngine-CLI.exe``, the ``nexus``/``nse`` console scripts,
the source launcher) — the help surface ``docs/CLI.md`` documents as
identical for ``nexus --help`` and ``nexus help``.
"""

if __name__ == "__main__":
    from nexus_scalp.cli.main import app
    from nexus_scalp.release.metadata import CLI_PROGRAM_NAME

    app(prog_name=CLI_PROGRAM_NAME)
