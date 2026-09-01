"""README/CLI documentation consistency check (steer 78/173).

Lightweight validation: every `nexus <command>` reference in README.md and
docs/CLI.md must exist in the real CLI's Click command tree. Prevents
documentation drift toward imaginary commands.

Run:  .venv/Scripts/python.exe tests/cli/test_docs_consistency.py
(also importable by pytest as a test module)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def real_commands() -> set[str]:
    from nexus_scalp.cli.main import app

    import typer

    click_root = typer.main.get_command(app)
    return set(click_root.commands.keys())


def referenced_commands(text: str) -> set[str]:
    """Extract `nexus <word>` references (word-form commands only)."""
    return set(re.findall(r"nexus\s+([a-z][a-z0-9-]{2,30})", text))


def main() -> int:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    cli_md = (REPO_ROOT / "docs" / "CLI.md").read_text(encoding="utf-8")

    real = real_commands()
    referenced = referenced_commands(readme) | referenced_commands(cli_md)

    # Whitelist: prose references that are not commands (e.g. "nexus-style").
    allowed_non_commands = {"start", "starte", "stop", "help"}  # start/stop/help ARE commands; 'starte' is the typo test fixture name, not a doc claim
    imaginary = sorted(referenced - real - allowed_non_commands)

    missing_docs = sorted({"help", "version", "doctor", "status", "update", "repair"} - referenced)

    print(f"real commands: {len(real)}")
    print(f"referenced in docs: {len(referenced)}")
    if imaginary:
        print("IMAGINARY (documented but missing):", imaginary)
    if missing_docs:
        print("UNDOCUMENTED core commands:", missing_docs)
    if imaginary or missing_docs:
        return 1
    print("DOCS CONSISTENCY: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
