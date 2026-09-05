"""Smoke CLI — nse smoke (--fast/--full/--runtime/--safety/--report).

Registered as a Typer sub-app on the canonical nexus app (app_factory).
Exit codes (brief section 17):
  0 = PASS
  1 = Smoke failure (FAIL/BLOCKED)
  2 = Invalid invocation/configuration
  3 = Environment/dependency failure
  4 = Safety gate blocked
  5 = Infrastructure unavailable

The underlying runner is offline, PAPER-only and never touches production DBs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer

from nexus_scalp.cli.app_factory import app
from nexus_scalp.cli.styling import console
from nexus_scalp.release import exit_codes as xc
from nexus_scalp.smoke.result_contract import human_summary
from nexus_scalp.smoke.runner import run_smoke

smoke_app = typer.Typer(
    name="smoke", help="Production E2E smoke (layered runtime verification).", add_completion=False
)


@smoke_app.callback(invoke_without_command=True)
def smoke_callback(
    ctx: typer.Context,
    fast: bool = typer.Option(
        False, "--fast", help="Fast tier: L0+L1+cheap safety only (no runtime boot)."
    ),
    full: bool = typer.Option(
        False, "--full", help="Full tier: all layers incl. runtime E2E (default)."
    ),
    runtime: bool = typer.Option(
        False, "--runtime", help="Runtime tier: full + explicit lifecycle/recovery emphasis."
    ),
    safety: bool = typer.Option(
        False, "--safety", help="Safety tier: L0+L1+all negative injections only."
    ),
    report: str | None = typer.Option(
        None,
        "--report",
        help="Write JSON report to this path (in addition to artifacts/forensics when --evidence).",
    ),
    json_mode: bool = typer.Option(
        False, "--json", help="Machine-readable JSON on stdout (no rich)."
    ),
    evidence: bool = typer.Option(
        False, "--evidence", help="Persist to artifacts/forensics/smoke_result.json"
    ),
) -> None:
    # Subcommand invoked? Defer to it.
    if ctx.invoked_subcommand is not None:
        return
    # Default tier is full
    tier = "full"
    flags = sum(bool(x) for x in (fast, full, runtime, safety))
    if flags > 1:
        console.print("[red]Choose one of --fast / --full / --runtime / --safety[/red]")
        raise typer.Exit(xc.EXIT_USAGE)
    if fast:
        tier = "fast"
    elif runtime:
        tier = "runtime"
    elif safety:
        tier = "safety"
    elif full:
        tier = "full"

    _run_and_emit(tier=tier, json_mode=json_mode, evidence=evidence, report_path=report)


def _run_and_emit(*, tier: str, json_mode: bool, evidence: bool, report_path: str | None) -> None:
    import contextlib
    import io as _io
    import logging as _logging

    # --json is a machine contract: engine/structlog chatter must not pollute stdout.
    # Capture stdout during the runner, then emit pure JSON after.
    buf = _io.StringIO() if json_mode else None
    # Silence structlog console handler by detaching root StreamHandlers during run
    _saved_handlers = list(_logging.getLogger().handlers)
    if json_mode:
        for h in _saved_handlers:
            if isinstance(h, _logging.StreamHandler) and not isinstance(h, _logging.FileHandler):
                _logging.getLogger().removeHandler(h)
        _logging.getLogger().addHandler(_logging.StreamHandler(sys.stderr))
    try:
        if buf is not None:
            with contextlib.redirect_stdout(buf):
                report = run_smoke(tier=tier)
        else:
            report = run_smoke(tier=tier)
    finally:
        if json_mode:
            # restore handlers
            for h in list(_logging.getLogger().handlers):
                if isinstance(h, _logging.StreamHandler) and not isinstance(
                    h, _logging.FileHandler
                ):
                    _logging.getLogger().removeHandler(h)
            for h in _saved_handlers:
                _logging.getLogger().addHandler(h)
    data = report.to_dict()

    if evidence:
        try:
            out_dir = Path("artifacts") / "forensics"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "smoke_result.json").write_text(
                json.dumps(data, indent=2, default=str), encoding="utf-8"
            )
            report.artifacts.append(str(out_dir / "smoke_result.json"))
            data["artifacts"] = report.artifacts
        except Exception:
            pass
    if report_path:
        try:
            p = Path(report_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            report.artifacts.append(str(p))
        except Exception as exc:
            console.print(f"[red]--report write failed: {exc}[/red]")
            raise typer.Exit(xc.EXIT_USAGE) from exc

    if json_mode:
        print(json.dumps(data, indent=2, default=str))
    else:
        console.print(human_summary(report))
        # also surface failures inline for operator
        fails = [c for c in report.checks if c.status == "FAIL"]
        if fails:
            console.print(
                f"\n[bold red]{len(fails)} check(s) failed — see report for actionable codes[/bold red]"
            )

    # Map overall_status -> exit code
    if report.overall_status == "PASS":
        raise typer.Exit(xc.EXIT_OK)
    # Honest environment block is still non-zero but distinct
    if report.overall_status == "BLOCKED":
        raise typer.Exit(xc.EXIT_ENVIRONMENT)
    # Safety-blocked
    if any(c.layer == "L4" and c.status == "FAIL" for c in report.checks):
        raise typer.Exit(4)
    raise typer.Exit(xc.EXIT_RUNTIME)


@smoke_app.command("report")
def smoke_report_cmd(
    input_path: str = typer.Option(
        "artifacts/forensics/smoke_result.json", "--input", help="Path to a smoke JSON report."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON on stdout."),
) -> None:
    """Render a previously persisted smoke report (no re-execution)."""
    p = Path(input_path)
    if not p.exists():
        console.print(f"[red]Report not found: {p}[/red]")
        raise typer.Exit(xc.EXIT_ENVIRONMENT)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        console.print(f"[red]Cannot parse report: {exc}[/red]")
        raise typer.Exit(xc.EXIT_USAGE) from exc
    if json_mode:
        print(json.dumps(data, indent=2, default=str))
        return
    # Re-hydrate minimal summary from JSON

    # Lightweight pretty: just dump the human summary from stored data
    overall = data.get("overall_status", "?")
    tier = data.get("tier", "?")
    run_id = data.get("run_id", "?")
    console.print(f"[bold]Smoke report[/bold]  tier={tier}  run={run_id}  status={overall}")
    console.print(
        json.dumps(
            {
                k: data.get(k)
                for k in (
                    "run_id",
                    "tier",
                    "overall_status",
                    "release_gate",
                    "duration_ms",
                    "git_commit",
                    "version",
                )
            },
            indent=2,
            default=str,
        )
    )
    fails = [c for c in data.get("checks", []) if c.get("status") == "FAIL"]
    if fails:
        console.print(f"\n[bold red]{len(fails)} failure(s):[/bold red]")
        for c in fails:
            console.print(
                f"  {c.get('id')}  {c.get('name')}  code={c.get('failure_code')}  reason={c.get('reason', '')[:500]}"
            )


def register_smoke_commands(target_app: Any = None) -> None:
    target = target_app if target_app is not None else app
    target.add_typer(
        smoke_app, name="smoke", help="Production E2E smoke (layered runtime verification)."
    )
