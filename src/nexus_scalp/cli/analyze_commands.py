"""CLI command module for `nse analyze` (Central Diagnostics Engine)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from nexus_scalp.diagnostics.engine import DiagnosticEngine


def register_analyze_commands(app: typer.Typer) -> None:
    @app.command("analyze")
    def analyze(
        json_output: bool = typer.Option(
            False, "--json", help="Emit strict JSON output only to stdout."
        ),
        strict: bool = typer.Option(
            False, "--strict", help="Treat warnings as errors (exit code 2)."
        ),
        file_path: list[str] | None = typer.Option(
            None, "--file", help="Analyze specific file or path(s)."
        ),
        tool: str | None = typer.Option(None, "--tool", help="Run a specific analyzer tool only."),
        category: str | None = typer.Option(
            None, "--category", help="Filter diagnostics by category."
        ),
    ) -> None:
        """Run centralized code diagnostics (Ruff, Pyright, Pylint, Bandit)."""
        engine = DiagnosticEngine(Path.cwd())

        if tool:
            # Filter analyzers to match requested tool
            engine.analyzers = [a for a in engine.analyzers if a.name.lower() == tool.lower()]
            if not engine.analyzers:
                if json_output:
                    print(json.dumps({"error": f"unknown analyzer tool: {tool}"}))
                else:
                    print(f"Error: Unknown analyzer tool '{tool}'")
                raise typer.Exit(code=4)

        report = engine.analyze(target_paths=file_path)

        if category:
            report.diagnostics = [
                d for d in report.diagnostics if d.category.lower() == category.lower()
            ]
            # Re-tally summary
            report.summary = {"errors": 0, "warnings": 0, "info": 0, "security": 0}
            for d in report.diagnostics:
                if d.severity == "error":
                    report.summary["errors"] += 1
                elif d.severity == "warning":
                    report.summary["warnings"] += 1
                else:
                    report.summary["info"] += 1
                if d.category == "security":
                    report.summary["security"] += 1

        # Strict mode escalation
        if strict and report.summary["warnings"] > 0:
            report.summary["errors"] += report.summary["warnings"]
            report.summary["warnings"] = 0
            if report.status in ("warnings", "passed"):
                report.status = "failed"

        # Determine exit code
        # 0 = clean, 1 = warnings, 2 = errors, 3 = infrastructure failure
        exit_code = 0
        if report.infrastructure["failures"]:
            exit_code = 3
        elif report.summary["errors"] > 0:
            exit_code = 2
        elif report.summary["warnings"] > 0 or report.summary["info"] > 0:
            exit_code = 1
        else:
            exit_code = 0

        if json_output:
            payload = report.to_dict()
            payload["exit_code"] = exit_code
            sys.stdout.write(json.dumps(payload, indent=2, default=str) + "\n")
            sys.stdout.flush()
            raise typer.Exit(code=exit_code)

        # Rich IDE Diagnostics Panel output (non-JSON mode)
        console = Console()
        console.print(
            Panel.fit(
                "[bold cyan]NEXUS CODE ANALYZER — Diagnostics Engine[/bold cyan]",
                border_style="cyan",
            )
        )

        # Analyzer Status Table
        table = Table(title="Analyzer Status", box=None)
        table.add_column("Analyzer", style="bold")
        table.add_column("Status")
        table.add_column("Version")
        table.add_column("Diagnostics")

        for name, info in report.analyzers.items():
            st = info.get("execution_status", "NOT_INSTALLED")
            ver = info.get("version", "—")
            count = info.get("diagnostics_count", 0)
            if st == "COMPLETED":
                status_str = "[green]Available[/green]"
            elif st == "NOT_INSTALLED":
                status_str = "[dim]Not Installed[/dim]"
            else:
                status_str = f"[red]{st}[/red]"
            table.add_row(name.capitalize(), status_str, ver, str(count))

        console.print(table)
        console.print()

        # Summary Panel
        s = report.summary
        infra = report.infrastructure
        summary_text = (
            f"Errors: [bold red]{s['errors']}[/bold red]   "
            f"Warnings: [bold yellow]{s['warnings']}[/bold yellow]   "
            f"Info: [bold blue]{s['info']}[/bold blue]   "
            f"Security: [bold magenta]{s['security']}[/bold magenta]\n"
            f"Status: [bold]{report.status.upper()}[/bold]"
        )
        if infra["failures"]:
            summary_text += f"\n[red]Infrastructure Failures: {len(infra['failures'])}[/red]"
        if infra["unavailable"]:
            summary_text += (
                f"\n[dim]Unavailable/Not Installed: {', '.join(infra['unavailable'])}[/dim]"
            )

        console.print(
            Panel(summary_text, title="Summary", border_style="cyan" if exit_code <= 1 else "red")
        )

        # Diagnostics Listing
        if report.diagnostics:
            console.print("\n[bold]Diagnostics[/bold]")
            for d in report.diagnostics[:100]:  # Cap display at 100
                sev_color = (
                    "red"
                    if d.severity == "error"
                    else ("yellow" if d.severity == "warning" else "blue")
                )
                console.print(
                    f"[bold]{d.file}:{d.line}:{d.column}[/bold]  "
                    f"[{sev_color}]{d.severity.upper()}[/{sev_color}]  "
                    f"[bold cyan]{d.tool.upper()}[/bold cyan] [yellow]{d.code}[/yellow]\n"
                    f"  {d.message}"
                )
            if len(report.diagnostics) > 100:
                console.print(
                    f"\n[dim]... and {len(report.diagnostics) - 100} more diagnostics omitted from console view.[/dim]"
                )

        raise typer.Exit(code=exit_code)
