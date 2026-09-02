"""Operational & diagnostic CLI commands.

WHERE/WHY: every doctor/health/status/test/logs/config/settings/repair/audit-purge/
diagnostics/verify-release/forensic/config-validate command plus the PHASE-13
model-artifact factory commands (dataset build, experiment create, train, inspect,
validate, replay, doctor, train-3). Extracted verbatim from cli/main.py
(CHG-0032 Step 1); the model-validate real-probability replay (BUG-175 candidate
repair, uncommitted parallel-owner WIP at slice time) moved with its function
byte-identically.

BOUNDARY: command implementations registered on ``app`` (imported from app_factory).
No app construction, no engine boot, no update-engine glue (see engine_boot.py /
update_cli.py).

USED BY: cli.main facade (module import registers all commands).

DO-NOT-PUT-HERE: new command families that belong to other domains (start/stop →
engine_boot, update/release → update_cli, setup wizard → wizard).
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import typer
from rich import box
from rich.panel import Panel
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
)
from rich.table import Table

from nexus_scalp.cli.app_factory import _resolve_facade_seam, app
from nexus_scalp.cli.styling import (
    _banner,
    _emit,
    _error_panel,
    _success_panel,
    _verdict_style,
    console,
)
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.release import diagnostics as rdiag
from nexus_scalp.release import environment as renv
from nexus_scalp.release import exit_codes as xc
from nexus_scalp.release import health as rhealth
from nexus_scalp.release import paths as rpaths
from nexus_scalp.release import repair as rrepair
from nexus_scalp.release import verify as rverify
from nexus_scalp.release.metadata import PRODUCT_DISPLAY, get_version_info


@contextlib.contextmanager
def _json_quiet() -> Any:
    """BUG-196: suppress stdout during --json computation phases.

    Eager subsystem initialization (e.g. the audit DB WAL INFO line from a
    registry-backed snapshot) must never land on stdout before the JSON
    payload - that breaks every json.loads consumer. Capture-and-discard is
    the truthful choice: those log lines are engine chatter, not operator
    output for a machine stream.
    """
    _capture = io.StringIO()
    _real_stdout = sys.stdout
    sys.stdout = _capture
    try:
        yield
    finally:
        sys.stdout = _real_stdout


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------
@app.command("version")
def version_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
    plain: bool = typer.Option(False, "--plain", help="Plain text, no ANSI."),
) -> None:
    """Show canonical version + build identity."""
    info = _resolve_facade_seam("get_version_info", get_version_info)()
    if json_mode:
        with _json_quiet():  # BUG-196: no stdout chatter before the payload
            from nexus_scalp.release.versioning import RuntimeVersionBlock

            try:
                block = RuntimeVersionBlock(web_dir=Path("Web") if Path("Web").is_dir() else None)
                info = {**info, "web_bundle": block.build()}
            except Exception:
                pass  # version truth never blocks the CLI
            # CHG-0043: one canonical snapshot consumed by version/health/doctor/web
            try:
                from nexus_scalp.release.runtime_snapshot import build_runtime_snapshot

                info["runtime_snapshot"] = build_runtime_snapshot(include_update=False)
            except Exception:
                pass  # failure-isolated: identity still emits
            try:
                from nexus_scalp.release.release_status import build_release_status

                info["release_status"] = build_release_status()
            except Exception:
                pass  # offline-safe: absence is UNKNOWN, never fabricated
        _emit(info, True)
        return
    if plain:
        commit_txt = (
            str(info.get("commit"))
            if info.get("commit")
            else str(info.get("commit_status") or "NOT_RECORDED")
        )
        print(
            f"{PRODUCT_DISPLAY} version {info['version']} ({info['channel']}, "
            f"{info['architecture']}, commit {commit_txt})"
        )
        return
    console.print(_banner(subtitle="version & build identity"))
    table = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
    table.add_column("Key", style="bold cyan")
    table.add_column("Value")
    for k in (
        "version",
        "channel",
        "platform",
        "architecture",
        "build_mode",
        "feature_schema",
        "build_timestamp",
        "commit",
        "commit_source",
        "commit_status",
    ):
        raw = info.get(k)
        if k == "commit" and not raw:
            # CHG-0043: unavailable identity is NOT_RECORDED, never n/a/None
            raw = info.get("commit_status") or "NOT_RECORDED"
        table.add_row(k.replace("_", " ").title(), str(raw) if raw else "UNKNOWN")
    console.print(table)


# ---------------------------------------------------------------------------
# doctor / health / status
# ---------------------------------------------------------------------------
def _health_entries() -> tuple[str, list[rhealth.HealthEntry]]:
    engine = rhealth.HealthEngine()
    return engine.overall()


@app.command("doctor")
def doctor_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
    verbose: bool = typer.Option(False, "--verbose", help="Show full reasons/suggestions."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI colors."),
    fix: bool = typer.Option(False, "--fix", help="Auto-repair fixable issues then re-verify."),
    yes: bool = typer.Option(False, "--yes", help="Auto-confirm repair (use with --fix)."),
) -> None:
    """Run the full system doctor (SYSTEM to ACCOUNTING).

    With --fix, fixable failures (missing dirs/config/DB/schema/logs) are
    repaired non-destructively and the doctor re-runs to confirm the fix.
    """
    if no_color:
        console.print = lambda *a, **k: print(*[str(x) for x in a])  # type: ignore[assignment]
    if json_mode and not fix:
        with _json_quiet():  # BUG-196: no stdout chatter before the payload
            verdict, entries = _health_entries()
            payload = {
                "overall": verdict,
                "checks": [e.to_dict() for e in entries],
                "environment": renv.format_hardware_block(renv.detect_environment()),
            }
            # CHG-0043: canonical snapshot + offline-safe release status so the
            # doctor answer is ONE consistent truth surface (failure-isolated).
            try:
                from nexus_scalp.release.runtime_snapshot import build_runtime_snapshot

                payload["runtime_snapshot"] = build_runtime_snapshot(include_update=False)
            except Exception:
                pass
            try:
                from nexus_scalp.release.release_status import build_release_status

                payload["release_status"] = build_release_status()
            except Exception:
                pass
        _emit(payload, True)
        return
    if not json_mode:
        # Human mode fetches its own entries: the JSON path above wraps its
        # fetch in _json_quiet() and its `verdict, entries` bindings stay
        # JSON-path-local (UnboundLocalError fix, 2026-09-02 UX pass).
        verdict, entries = _health_entries()
        console.print(_banner(subtitle="system doctor · 21 checks"))
        table = Table(title="NEXUS SYSTEM HEALTH", box=box.SIMPLE_HEAD, show_lines=False)
        table.add_column("Check", style="bold white", no_wrap=True)
        table.add_column("Status", style="bold", no_wrap=True)
        table.add_column("Detail", style="dim", overflow="fold")
        for e in entries:
            detail = e.reason
            if verbose and e.suggestion:
                detail += f"  → {e.suggestion}"
            # CHG-0043: state column carries the canonical taxonomy word so
            # DISABLED/NOT_CONFIGURED/NOT_INITIALIZED never masquerade as
            # health problems; the verdict column keeps aggregate semantics.
            state_txt = getattr(e, "state", "") or ""
            status_cell = (
                f"{_verdict_style(e.verdict)} [dim]· {state_txt}[/dim]"
                if state_txt and state_txt != "HEALTHY"
                else _verdict_style(e.verdict)
            )
            table.add_row(e.category, status_cell, detail)
        console.print(table)
        console.print(
            Panel(
                f"Overall: [bold]{verdict}[/bold]",
                border_style="green"
                if verdict in ("READY", "PASS")
                else ("yellow" if verdict == "DEGRADED" else "red"),
            )
        )

    fails = [e for e in entries if e.verdict == "FAIL"]
    warns = [e for e in entries if e.verdict == "WARN"]
    auto_fixables = {"CONFIGURATION", "DATABASE", "LOGGING"}
    fixable = [e for e in fails if e.category in auto_fixables]
    if fails and not json_mode:
        if fixable:
            console.print("[bold cyan]Fixable issues detected:[/bold cyan]")
            for e in fixable:
                console.print(f"  • {e.category}: {e.reason}")
                if e.suggestion:
                    console.print(f"    [dim]→ {e.suggestion}[/dim]")
        non_fixable = [e for e in fails if e not in fixable]
        if non_fixable:
            console.print("[bold yellow]Manual action needed:[/bold yellow]")
            for e in non_fixable:
                console.print(f"  • {e.category}: {e.reason}")
                if e.suggestion:
                    console.print(f"    [dim]→ {e.suggestion}[/dim]")
    if not json_mode:
        # Actionable closing summary (2026-09-02 UX pass): the operator must
        # never have to ask "what do I do next?". WARN rows that carry an
        # explicit suggestion are surfaced as user actions; everything else
        # maps to the truthful safe-next command.
        warn_actions = [e for e in warns if e.suggestion]
        auto_n = len(fixable)
        user_n = len([e for e in fails if e.category not in auto_fixables]) + len(warn_actions)
        lines = ["[bold]OVERALL:[/bold] " + verdict]
        lines.append(f"AUTO-FIXABLE: {auto_n}   USER ACTION: {user_n}")
        safe_now = verdict in ("READY", "PASS", "DEGRADED")
        lines.append("SAFE NOW: " + ("YES" if safe_now else "NO"))
        if auto_n:
            lines.append("[cyan]NEXT:[/cyan] nexus doctor --fix")
        elif user_n:
            first_action = (
                warn_actions[0].suggestion
                if warn_actions
                else (next((e.suggestion for e in fails if e.suggestion), None))
            )
            lines.append(
                "[cyan]NEXT:[/cyan] " + (first_action or "resolve the failing checks above")
            )
        else:
            lines.append("[cyan]NEXT:[/cyan] nexus start   (paper mode by default)")
        console.print(Panel("\n".join(lines), border_style="cyan"))

    if fix and fixable:
        if not yes:
            ok = typer.confirm("Apply non-destructive fixes and re-verify?", default=True)
            if not ok:
                console.print("[yellow]Cancelled.[/yellow]")
                raise typer.Exit(xc.EXIT_OK) from None
        console.print("\n[bold cyan]Repairing fixable issues…[/bold cyan]")
        # Map fixable categories -> RepairEngine options
        rec_dirs = any(e.category in ("CONFIGURATION", "LOGGING") for e in fixable)
        with_news = any(e.category == "NEWS" for e in fails)  # off by default
        engine2 = rrepair.RepairEngine()
        results = engine2.run(recreate_dirs=rec_dirs, with_news=with_news)
        for r in results:
            style = "green" if r.status == "OK" else ("yellow" if r.status == "SKIPPED" else "red")
            console.print(f"[{style}]{r.status:8}[/{style}] {r.action:12} {r.detail}")
        # Re-verify
        verdict2, entries2 = _health_entries()
        if json_mode:
            _emit(
                {
                    "overall": verdict2,
                    "checks": [e.to_dict() for e in entries2],
                    "repair": [r.to_dict() for r in results],
                    "environment": renv.format_hardware_block(renv.detect_environment()),
                },
                True,
            )
            raise typer.Exit(xc.EXIT_OK if verdict2 in ("READY", "PASS") else xc.EXIT_RUNTIME)
        console.print("\n[bold]Re-check after repair[/bold]")
        table2 = Table(box=box.SIMPLE_HEAD)
        table2.add_column("Check", style="bold white")
        table2.add_column("Status", style="bold")
        table2.add_column("Detail", style="dim")
        for e in entries2:
            table2.add_row(e.category, _verdict_style(e.verdict), e.reason)
        console.print(table2)
        console.print(
            Panel(
                f"Overall: [bold]{verdict2}[/bold]",
                border_style="green"
                if verdict2 in ("READY", "PASS")
                else ("yellow" if verdict2 == "DEGRADED" else "red"),
            )
        )
        fails2 = [e for e in entries2 if e.verdict == "FAIL"]
        if not fails2:
            console.print("[bold green]Repaired — system is ready.[/bold green]")
        else:
            console.print(f"[yellow]{len(fails2)} check(s) still failing — see above.[/yellow]")
        raise typer.Exit(xc.EXIT_OK if verdict2 in ("READY", "PASS") else xc.EXIT_RUNTIME)

    if json_mode and fix:
        _emit(
            {
                "overall": verdict,
                "checks": [e.to_dict() for e in entries],
                "repair": {
                    "applied": False,
                    "reason": "nothing fixable" if not fixable else "user declined",
                },
            },
            True,
        )
    if not json_mode and verdict in ("NOT READY", "FAIL"):
        raise typer.Exit(xc.EXIT_RUNTIME) from None


@app.command("health")
def health_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
    plain: bool = typer.Option(False, "--plain", help="Plain text, no ANSI."),
) -> None:
    """Quick health summary (READY / DEGRADED / NOT READY)."""
    verdict, entries = _health_entries()
    if json_mode:
        _emit(
            {
                "overall": verdict,
                "checks": [e.to_dict() for e in entries],
            },
            True,
        )
        return
    if plain:
        for e in entries:
            print(f"{e.category:18} {e.verdict:8} {e.reason}")
        print(f"\nOverall: {verdict}")
        return
    console.print(_banner(subtitle="quick health"))
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column("Check", style="bold white")
    table.add_column("Status", style="bold")
    table.add_column("Detail", style="dim")
    for e in entries:
        table.add_row(e.category, _verdict_style(e.verdict), e.reason)
    console.print(table)
    console.print(
        Panel(
            f"Overall: [bold]{verdict}[/bold]",
            border_style="green"
            if verdict == "READY"
            else ("yellow" if verdict == "DEGRADED" else "red"),
        )
    )


@app.command("status")
def status_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Full status: health + environment + version."""
    if json_mode:
        engine = rhealth.HealthEngine()
        _emit(engine.summary_dict(), True)
        return
    health_cmd(json_mode=False, plain=True)


# ---------------------------------------------------------------------------
# test
# ---------------------------------------------------------------------------
_TEST_TARGETS = {
    "quick": [
        "tests/unit/test_log_autopsy_fixes.py",
        "tests/unit/test_accounting_core.py",
        "tests/unit/test_release_system.py",
    ],
    "unit": ["tests/unit/"],
    "integration": ["tests/integration/", "--ignore=tests/integration/test_playwright_e2e.py"],
    "health": None,  # handled specially
    "release": None,  # handled specially
    "all": ["tests/", "--ignore=tests/integration/test_playwright_e2e.py"],
}


@app.command("test")
def test_cmd(
    mode: str = typer.Option(
        "quick", "--mode", "-m", help="quick | unit | integration | health | release | all"
    ),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Run the requested test suite. Never runs live broker tests."""
    if mode == "health":
        verdict, entries = _health_entries()
        data = {
            "overall": verdict,
            "checks": [e.to_dict() for e in entries],
            "results": [(e.category, e.verdict) for e in entries],
        }
        _emit(data, json_mode)
        return
    if mode == "release":
        verify_cmd(json_mode=json_mode)
        return
    target = _TEST_TARGETS.get(mode)
    if target is None:
        hint = "Use quick|unit|integration|health|release|all. Try: nexus test --mode quick"
        if json_mode:
            _emit(
                {"error": f"unknown test mode '{mode}'", "hint": hint, "exit_code": xc.EXIT_USAGE},
                True,
            )
        else:
            console.print(
                _error_panel(
                    "Invalid test mode",
                    f"unknown test mode '{mode}'",
                    hint=hint,
                    exit_code=xc.EXIT_USAGE,
                )
            )
        raise typer.Exit(xc.EXIT_USAGE) from None
    cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short", *target]
    if not json_mode:
        with Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold cyan]Running tests[/bold cyan] ({mode})…"),
            transient=True,
            console=console,
        ) as progress:
            progress.add_task("tests", total=None)
            # Still print a lightweight header so CI logs keep context
            console.print(f"[dim]pytest {' '.join(target)[:140]}[/dim]")
        # Real run after spinner header (typer runner suppresses live, so do plain run now)
        proc = subprocess.run(cmd, check=False)
        if proc.returncode == 0:
            console.print(
                _success_panel("Tests passed", f"mode: {mode}  ·  exit code 0", border="green")
            )
        else:
            console.print(
                _error_panel(
                    "Tests failed",
                    f"mode: {mode} returned {proc.returncode}",
                    hint="Run with --json for machine-readable output, or nexus doctor --fix",
                    exit_code=xc.EXIT_RUNTIME,
                )
            )
        raise typer.Exit(0 if proc.returncode == 0 else 1)
    # json path — no spinner
    proc = subprocess.run(cmd, check=False)
    _emit(
        {"mode": mode, "returncode": proc.returncode},
        json_mode,
        plain=True if proc.returncode == 0 else False,
    )
    raise typer.Exit(0 if proc.returncode == 0 else 1)


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------
def _log_files() -> list[Path]:
    dirs = [rpaths.get_logs_dir(), Path("artifacts/logs")]
    out: list[Path] = []
    seen: set[Path] = set()
    for d in dirs:
        if d.exists():
            for f in sorted(d.glob("*.log")):
                if f.resolve() not in seen:
                    seen.add(f.resolve())
                    out.append(f)
    return out


@app.command("logs")
def logs_cmd(
    tail: int = typer.Option(50, "--tail", "-n", help="Lines to show."),
    errors: bool = typer.Option(False, "--errors", help="Only ERROR/CRITICAL lines."),
    worker: bool = typer.Option(False, "--worker", help="Only worker lines."),
    export: Path | None = typer.Option(None, "--export", help="Export logs to a zip path."),
) -> None:
    """Tail / filter / export engine logs."""
    files = _log_files()
    if not files:
        console.print(
            _error_panel(
                "No logs yet",
                "No log files found.",
                hint="Start the engine once: nexus start  ·  then check again. Logs live in artifacts/logs/",
            )
        )
        return
    if export is not None:
        with zipfile.ZipFile(export, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                zf.write(f, arcname=f.name)
        console.print(_success_panel("Logs exported", str(export)))
        return
    latest = files[-1]
    lines = latest.read_text(encoding="utf-8", errors="replace").splitlines()
    if errors:
        lines = [l for l in lines if re.search(r"\b(ERROR|CRITICAL)\b", l, re.I)]
    if worker:
        lines = [l for l in lines if re.search(r"WORKER", l, re.I)]
    console.print(
        Panel(f"[dim]{latest}[/dim]  ·  last {tail} lines", border_style="cyan", box=box.ROUNDED)
    )
    for line in lines[-tail:]:
        console.print(line)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
@app.command("config")
def config_cmd(
    validate: Path | None = typer.Option(
        None, "--validate", "-c", help="Validate a specific YAML config."
    ),
    show: bool = typer.Option(False, "--show", help="Print effective user config."),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Inspect / validate the active configuration."""
    target = validate or rpaths.get_user_config_path()
    if not target.exists():
        if json_mode:
            _emit(
                {
                    "path": str(target),
                    "valid": False,
                    "error": "config not found",
                    "hint": "Run nexus setup or nexus repair",
                },
                True,
            )
        else:
            console.print(
                _error_panel(
                    "Config not found",
                    str(target),
                    hint="Run nexus setup or nexus repair first.",
                    exit_code=xc.EXIT_RUNTIME,
                )
            )
        raise typer.Exit(xc.EXIT_RUNTIME) from None
    try:
        cfg = AppConfig.load_from_yaml(target)
    except Exception as e:
        if json_mode:
            _emit({"path": str(target), "valid": False, "error": str(e)}, True)
        else:
            console.print(
                _error_panel(
                    "Invalid configuration",
                    str(e),
                    hint=f"Fix {target} or run nexus repair --recreate-config",
                    exit_code=xc.EXIT_RUNTIME,
                )
            )
        raise typer.Exit(xc.EXIT_RUNTIME) from None
    if json_mode:
        _emit(
            {
                "path": str(target),
                "valid": True,
                "symbol": cfg.execution.symbol,
                "mode": cfg.execution.mode.value,
                "schema": cfg.model.feature_schema_version,
            },
            True,
        )
        return
    console.print(_success_panel("Configuration valid", str(target), border="green"))
    console.print(
        f"Symbol: [bold cyan]{cfg.execution.symbol}[/bold cyan]  ·  Mode: [bold]{cfg.execution.mode.value}[/bold]  ·  Schema: {cfg.model.feature_schema_version}"
    )


@app.command("config-validate")
def config_validate_subcmd(
    config_path: Path = typer.Option(
        Path("configs/base.yaml"), "--config", "-c", help="Path to YAML config to validate."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Validate syntax, schema, version migration, missing keys & secret masking."""
    import yaml

    target = config_path.resolve()
    report: dict[str, Any] = {
        "path": str(target),
        "exists": target.exists(),
        "valid": False,
        "missing_keys": [],
        "secrets_masked": {
            "mt5_password": "PRES" if target.exists() else "ABS",
            "telegram_token": "PRES" if target.exists() else "ABS",
        },
        "env_validation": {
            "telegram_token_env": bool(os.getenv("NEXUS_TELEGRAM_BOT_TOKEN")),
            "telegram_admin_env": bool(os.getenv("NEXUS_TELEGRAM_ADMIN_ID")),
        },
    }
    if not target.exists():
        if json_mode:
            _emit(report, True)
        else:
            console.print(_error_panel("Config not found", str(target), hint="Run nexus setup"))
        raise typer.Exit(1) from None

    try:
        raw_data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        cfg = AppConfig.load_from_yaml(target)
        report["valid"] = True
        report["symbol"] = cfg.execution.symbol
        report["mode"] = cfg.execution.mode.value
        report["feature_schema"] = cfg.model.feature_schema_version
        # check expected top-level sections
        expected_sections = {"mt5", "execution", "risk", "model", "telemetry", "news", "rules"}
        missing_secs = sorted(expected_sections - set(raw_data.keys()))
        report["missing_sections"] = missing_secs
        if json_mode:
            _emit(report, True)
            return
        console.print(_success_panel("Config Security & Schema Validation Passed", str(target)))
        console.print(f"  · Symbol: [bold]{cfg.execution.symbol}[/bold]")
        console.print(f"  · Mode:   [bold]{cfg.execution.mode.value}[/bold]")
        console.print(f"  · Schema: [bold]{cfg.model.feature_schema_version}[/bold]")
        if missing_secs:
            console.print(f"  · [yellow]Missing optional sections: {missing_secs}[/yellow]")
        console.print("  · Secrets masked: [green]YES (no plaintext secrets leaked)[/green]")
    except Exception as e:
        report["error"] = str(e)
        if json_mode:
            _emit(report, True)
        else:
            console.print(_error_panel("Config validation failed", str(e), hint=f"Fix {target}"))
        raise typer.Exit(1) from None


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
@app.command("settings")
def settings_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Inspect the isolated user-settings store (never exposes secrets)."""
    from nexus_scalp.settings import SettingsService

    svc = SettingsService()
    try:
        status = svc.telegram_config_status()
        if json_mode:
            _emit(
                {
                    "state": svc.state.state,
                    "db_path": str(svc.db.db_path),
                    "telegram": status,
                    "settings": svc.provenance(),
                },
                True,
            )
            return
        console.print(_banner(subtitle="user settings (secrets masked)"))
        console.print(f"State    : [bold]{svc.state.state}[/bold]  ([dim]{svc.db.db_path}[/dim])")
        console.print("Telegram :")
        console.print(
            f"  configured      : {'[green]YES[/green]' if status['configured'] else '[yellow]NO[/yellow]'}"
        )
        console.print(f"  enabled         : {status['enabled']}")
        console.print(f"  token_present   : {status['token_present']}")
        console.print(f"  masked_token    : {status['masked_token'] or '(none)'}")
        console.print(f"  admin_id_present: {status['admin_id_present']}")
        console.print(f"  admin_shape_ok  : {status['admin_id_shape_valid']}")
        console.print(f"  source          : {status['source']}")
    finally:
        svc.close()


# ---------------------------------------------------------------------------
# repair
# ---------------------------------------------------------------------------
@app.command("repair")
def repair_cmd(
    database: bool = False,
    news_db: bool = typer.Option(
        True, "--news-db/--no-news-db", help="Provision artifacts/news.db too (BUG-146)."
    ),
    force_recreate: bool = typer.Option(
        False, "--recreate-config", help="Restore config from template (keeps DBs)."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
    verify: bool = typer.Option(True, "--verify/--no-verify", help="Re-run doctor after repair."),
) -> None:
    """Repair non-destructive derived state. NEVER deletes user data."""
    with Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[bold cyan]Repairing…[/bold cyan]"),
        transient=True,
        console=console,
    ) as progress:
        progress.add_task("repair", total=None)
        engine = rrepair.RepairEngine()
        results = engine.run(recreate_dirs=force_recreate, with_news=news_db)
    if json_mode:
        payload = engine.summary_dict(results)
        if verify:
            verdict, entries = _health_entries()
            payload["verify"] = {"overall": verdict, "checks": [e.to_dict() for e in entries]}
        _emit(payload, True)
        return
    console.print(_banner(subtitle="repair — what we fixed"))
    for r in results:
        style = "green" if r.status == "OK" else ("yellow" if r.status == "SKIPPED" else "red")
        console.print(f"[{style}]{r.status:8}[/{style}] {r.action:12} {r.detail}")
    if verify:
        verdict, entries = _health_entries()
        console.print("\n[bold]Verification after repair[/bold]")
        for e in entries:
            if e.verdict == "FAIL":
                console.print(f"[red]FAIL[/red]  {e.category:16} {e.reason}")
        console.print(
            Panel(
                f"Overall: [bold]{verdict}[/bold]",
                border_style="green"
                if verdict in ("READY", "PASS")
                else ("yellow" if verdict == "DEGRADED" else "red"),
            )
        )
        if verdict in ("READY", "PASS"):
            console.print("[bold green]System verified — you can run: nexus start[/bold green]")
    failed = [r for r in results if r.status == "FAILED"]
    raise typer.Exit(1 if failed else 0)


@app.command("audit-purge")
def audit_purge_cmd(
    signal_days: float = typer.Option(
        7.0, "--signal-days", help="Retention for audit_signals rows (days)."
    ),
    moving_days: float = typer.Option(
        3.0, "--moving-days", help="Retention for POSITION_MOVING events (days)."
    ),
    telemetry_days: float = typer.Option(
        13.0, "--telemetry-days", help="Retention for guard telemetry (days)."
    ),
    batch_size: int = typer.Option(500, "--batch", help="Rows per bounded delete transaction."),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Purge disposable audit telemetry older than the retention window (BUG-054).

    Deletes ONLY: old audit_signals rows, old POSITION_MOVING events and old
    guard telemetry. NEVER touches audit_ledger / experiences / autopsies /
    strategy / research tables. Safe to run while the engine is live (bounded
    batches, WAL). For scheduled use, pair with a cron/systemd timer.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    repo = AuditRepository(flush_interval_sec=0.05)
    try:
        res = repo.purge_old_audit_data(
            signal_retention_days=signal_days,
            moving_retention_days=moving_days,
            telemetry_retention_days=telemetry_days,
            batch_size=batch_size,
        )
    finally:
        repo.close()
    if json_mode:
        _emit(res, True)
        return
    console.print(
        _success_panel(
            "Audit retention purge complete",
            f"window: signals {signal_days}d · moving {moving_days}d · telemetry {telemetry_days}d",
            border="green",
        )
    )
    for table, count in (res.get("deleted") or {}).items():
        console.print(f"  {table:22} {count} rows deleted")
    if res.get("error"):
        console.print(
            _error_panel("Purge error", str(res["error"]), hint="Check DB locks and try again")
        )
        raise typer.Exit(1) from None
    console.print(f"  duration: {res.get('duration_ms', 0)} ms")


# ---------------------------------------------------------------------------
# diagnostics
# ---------------------------------------------------------------------------
@app.command("diagnostics")
@app.command("export-diagnostics")
def diagnostics_cmd() -> None:
    """Export a sanitized diagnostics archive (never contains secrets)."""
    with Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[bold cyan]Collecting diagnostics…[/bold cyan]"),
        transient=True,
        console=console,
    ) as progress:
        progress.add_task("collect", total=None)
        out = rdiag.export_diagnostics()
    console.print(_success_panel("Diagnostics exported", str(out), border="cyan"))
    console.print("[dim]Sanitized: no passwords, tokens, credentials or DB contents.[/dim]")


# ---------------------------------------------------------------------------
# verify-release
# ---------------------------------------------------------------------------
@app.command("verify-release")
def verify_cmd(
    root: Path = typer.Option(Path("."), "--root", "-r", help="Release directory to verify."),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Verify a release tree: EXE, launch, version, assets, checksums, secrets."""
    root = root.resolve()
    with Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[bold cyan]Verifying release…[/bold cyan]"),
        transient=True,
        console=console,
    ) as progress:
        progress.add_task("verify", total=None)
        result = rverify.verify_release(root)
    if json_mode:
        result["exit_code"] = xc.EXIT_OK if result["valid"] else xc.EXIT_RELEASE
        _emit(result, True)
        return
    console.print(_banner(subtitle=f"verify release · {root.name}"))
    for c in result["checks"]:
        style = "green" if c["status"] == "PASS" else ("yellow" if c["status"] == "WARN" else "red")
        console.print(f"[{style}]{c['status']:5}[/{style}] {c['check']:28} {c['detail']}")
    console.print(
        Panel(
            f"Release: [bold]{result['overall']}[/bold]",
            border_style="green" if result["valid"] else "red",
        )
    )
    raise typer.Exit(xc.EXIT_OK if result["valid"] else xc.EXIT_RELEASE)


# =============================================================================
# TASK-11/12: POST-70D FORENSIC MONITORING + DEPLOY GATE
# -----------------------------------------------------------------------------
# nexus forensic                       -> full health matrix dashboard
# nexus forensic --deploy-gate         -> canonical deploy gate (exit-code)
# nexus forensic --snapshot            -> persisted FORENSIC_HEALTH_SNAPSHOT
# nexus forensic --trend               -> current vs previous snapshot diff
# nexus forensic --gap                 -> experience->outcome gap forensics
# nexus forensic --report              -> bounded periodic Telegram report
# BUG-162 (2026-08-31): this command was accidentally deleted in 999276c,
# leaving the beforePush gate hooks calling a nonexistent command (typer exit
# 2 masqueraded as REVIEW_REQUIRED -> fail-open). Restored verbatim from
# 716c458, adapted to current _emit/console conventions. Exit-code contract
# (deploy-gate): 0 ALLOW/ALLOW_WITH_WARNING, 1 BLOCK, 2 REVIEW_REQUIRED,
# 3 FORENSIC_ENGINE_UNAVAILABLE (fail-safe block, deploy_gate.py §39).
# =============================================================================
# CHG-0032-A1 help-order parity: monolith order was verify-release(1004) ->
# update(1140) -> forensic(1639) -> release(1767). update_cli import here
# registers update; release registration is DEFERRED to the hook below
# forensic_cmd. (update_cli_parity_import)
import nexus_scalp.cli.update_cli as _update_cli_parity  # noqa: E402


@app.command("forensic")
def forensic_cmd(
    snapshot: bool = typer.Option(
        False, "--snapshot", help="Persist and print the FORENSIC_HEALTH_SNAPSHOT as JSON."
    ),
    deploy_gate: bool = typer.Option(
        False,
        "--deploy-gate",
        help="Canonical deploy gate: exit 0 allowed, 1 block, 2 review, 3 engine unavailable.",
    ),
    trend: bool = typer.Option(
        False, "--trend", help="Compare the latest snapshot against the previous (read-only)."
    ),
    gap: bool = typer.Option(False, "--gap", help="Experience->outcome gap forensics (read-only)."),
    report: bool = typer.Option(
        False, "--report", help="Run one bounded periodic Telegram forensic report cycle."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Read-only forensic health matrix and canonical deploy-gate analysis."""
    from nexus_scalp.forensics import (
        ForensicHealthEngine,
        analyze_experience_gap,
        latest_trend,
        persist_gap_report,
        run_deploy_gate,
    )

    engine = ForensicHealthEngine()

    if deploy_gate:
        result = run_deploy_gate(engine)
        payload = result.to_dict()
        payload["exit_code"] = result.exit_code
        # Always machine-readable: gate hooks parse artifacts/forensics/
        # deploy_gate_result.json (or the redirected stdout) for "decision".
        _emit(payload, True)
        if not json_mode:
            if payload["exit_code"] == 1:
                console.print(
                    _error_panel(
                        "Deployment blocked",
                        "Critical forensic checks failed:\n"
                        + "\n".join(f"  • {c}" for c in payload["blocking_checks"]),
                        hint="See artifacts/forensics/deploy_gate_result.json",
                        exit_code=payload["exit_code"],
                    )
                )
            elif payload["exit_code"] == 2:
                console.print(
                    _success_panel(
                        "Deployment requires review",
                        "DEGRADED/UNKNOWN forensic conditions — inspect before shipping.\n"
                        "[dim]Fix: see artifacts/forensics/deploy_gate_result.json[/dim]",
                        border="yellow",
                    )
                )
            elif payload["exit_code"] == 3:
                console.print(
                    _error_panel(
                        "Forensic engine unavailable",
                        str(payload.get("engine_error") or "unknown engine error"),
                        hint="Deployment cannot be verified — fail-safe block (§39)",
                        exit_code=payload["exit_code"],
                    )
                )
        raise typer.Exit(payload["exit_code"])

    if trend:
        t = latest_trend(Path("artifacts") / "forensics")
        _emit(t, as_json=json_mode, plain=not json_mode)
        return

    if gap:
        rep = analyze_experience_gap()
        persist_gap_report(rep)
        _emit(rep.to_dict(), as_json=json_mode, plain=not json_mode)
        return

    if report:
        from nexus_scalp.forensics import TelegramReportScheduler

        sched = TelegramReportScheduler()
        outcome = sched.run_once(engine)
        _emit(outcome, as_json=json_mode, plain=not json_mode)
        return

    if snapshot:
        rec = engine.snapshot(persist=True)
        _emit(rec.to_dict(), as_json=json_mode, plain=not json_mode)
        return

    dash = engine.dashboard()
    if json_mode:
        _emit(dash, True)
        return
    table = Table(title="SYSTEM FORENSIC HEALTH", box=box.SIMPLE)
    table.add_column("Group", style="bold white")
    table.add_column("Status", style="bold")
    table.add_column("Check", style="dim")
    table.add_column("Detail", style="dim")
    for group, status in dash["groups"].items():
        table.add_row(group, _verdict_style(status), "", "")
    console.print(table)
    console.print(
        Panel(
            f"Overall: [bold]{dash['overall']}[/bold]  "
            f"CRITICAL={dash['critical_count']} WARNING={dash['warning_count']} "
            f"DEGRADED={dash['degraded_count']} UNKNOWN={dash['unknown_count']}",
            border_style="red" if dash["critical_count"] else "yellow",
            title="Forensic health",
        )
    )
    problems = [
        (r["check_id"], r["status"], r["evidence"])
        for r in dash["rows"].values()
        if r["status"] not in ("PASS",)
    ]
    if problems:
        pt = Table(title="Non-passing checks (evidence)", box=box.SIMPLE)
        pt.add_column("Check", style="bold white")
        pt.add_column("Status", style="bold")
        pt.add_column("Evidence", style="dim")
        for cid, status, evidence in problems:
            pt.add_row(cid, _verdict_style(status), evidence[:160])
        console.print(pt)


# ---------------------------------------------------------------------------
# doctor parity: config-validate
# ---------------------------------------------------------------------------
# CHG-0032-A1: release now registers AFTER forensic (monolith order).
_update_cli_parity._register_release_command()


# CHG-0032-A1 help-order parity: the monolith registered this whole block
# (legacy config-validate duplicate + model-* family) AFTER start/stop/restart/run
# (engine_boot). Registration is deferred to _register_late_commands(), invoked by
# the facade after engine_boot import. Function bodies are UNCHANGED (verbatim).
def _register_late_commands() -> None:
    if any(c.name == "model-dataset-build" for c in app.registered_commands):
        return
    _late_block()


def _late_block() -> None:
    @app.command("config-validate")
    def config_validate_cmd(
        config_path: Path = typer.Option(
            Path("configs/base.yaml"), "--config", "-c", help="Path to YAML config to validate."
        ),
        json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
    ) -> None:
        """Validate syntax, schema, version migration, missing keys & secret masking."""
        import yaml

        target = config_path.resolve()
        report: dict[str, Any] = {
            "path": str(target),
            "exists": target.exists(),
            "valid": False,
            "missing_keys": [],
            "secrets_masked": {
                "mt5_password": "PRES" if target.exists() else "ABS",
                "telegram_token": "PRES" if target.exists() else "ABS",
            },
            "env_validation": {
                "telegram_token_env": bool(os.getenv("NEXUS_TELEGRAM_BOT_TOKEN")),
                "telegram_admin_env": bool(os.getenv("NEXUS_TELEGRAM_ADMIN_ID")),
            },
        }
        if not target.exists():
            if json_mode:
                _emit(report, True)
            else:
                console.print(_error_panel("Config not found", str(target), hint="Run nexus setup"))
            raise typer.Exit(1) from None

        try:
            raw_data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
            cfg = AppConfig.load_from_yaml(target)
            report["valid"] = True
            report["symbol"] = cfg.execution.symbol
            report["mode"] = cfg.execution.mode.value
            report["feature_schema"] = cfg.model.feature_schema_version
            expected_sections = {"mt5", "execution", "risk", "model", "telemetry", "news", "rules"}
            missing_secs = sorted(expected_sections - set(raw_data.keys()))
            report["missing_sections"] = missing_secs
            if json_mode:
                _emit(report, True)
                return
            console.print(_success_panel("Config Security & Schema Validation Passed", str(target)))
            console.print(f"  · Symbol: [bold]{cfg.execution.symbol}[/bold]")
            console.print(f"  · Mode:   [bold]{cfg.execution.mode.value}[/bold]")
            console.print(f"  · Schema: [bold]{cfg.model.feature_schema_version}[/bold]")
            if missing_secs:
                console.print(f"  · [yellow]Missing optional sections: {missing_secs}[/yellow]")
            console.print("  · Secrets masked: [green]YES (no plaintext secrets leaked)[/green]")
        except Exception as e:
            report["error"] = str(e)
            if json_mode:
                _emit(report, True)
            else:
                console.print(
                    _error_panel("Config validation failed", str(e), hint=f"Fix {target}")
                )
            raise typer.Exit(1) from None

    # =============================================================================
    # PHASE 13: MODEL GENERATION MIGRATION — artifact-first model factory CLI
    # -----------------------------------------------------------------------------
    # nse dataset build / experiment create / train / inspect / validate /
    # replay / doctor — all operate on filesystem artifacts (no DB required).
    # =============================================================================

    def _mg_store() -> Any:
        from nexus_scalp.model_generation import ArtifactStore, default_artifact_root

        return ArtifactStore(default_artifact_root())

    @app.command("model-dataset-build")
    def model_dataset_build(
        bars_csv: Path = typer.Option(Path(""), "--bars", "-b", help="CSV/parquet of raw bars"),
        symbol: str = typer.Option("XAUUSD", "--symbol"),
        timeframe: str = typer.Option("M5", "--timeframe"),
        schema: str = typer.Option("scalp_v1", "--schema", help="feature schema id"),
        with_news: bool = typer.Option(False, "--with-news", help="attach news context"),
        news_csv: Path = typer.Option(Path(""), "--news", "-n", help="news frame parquet/csv"),
        news_db: Path = typer.Option(
            Path(""),
            "--news-db",
            help="export the news database (artifacts/news.db) into the frame",
        ),
    ) -> None:
        """Build a dataset artifact (deterministic, artifact-first).

        News context is attached when ``--with-news``.  The news frame may be
        given explicitly (``--news``) OR exported from the News subsystem's
        database (``--news-db``, default ``artifacts/news.db``) via the
        causally-correct bridge (model_generation.news_bridge).
        """
        import polars as pl

        from nexus_scalp.features.schema import FEATURE_SCHEMAS
        from nexus_scalp.model_generation import DatasetFactory

        # BUG-176: --schema was DECLARED BUT IGNORED (the value never reached
        # DatasetFactory/SampleFactory, which default to scalp_v1), so a bogus
        # id was silently accepted and a DIFFERENT schema was built (exit 0).
        # Validate the id against the feature schema registry at parse time.
        try:
            feature_schema = FEATURE_SCHEMAS.resolve(schema)
        except KeyError:
            console.print(
                _error_panel(
                    "Unknown schema",
                    schema,
                    hint=(
                        "valid schema ids: "
                        + ", ".join(s.schema_id for s in FEATURE_SCHEMAS.list_schemas())
                    ),
                    exit_code=xc.EXIT_USAGE,
                )
            )
            raise typer.Exit(xc.EXIT_USAGE) from None

        if not bars_csv.exists():
            console.print(
                _error_panel("No bars file", str(bars_csv), hint="Pass --bars path/to/bars.csv")
            )
            raise typer.Exit(1) from None
        df = (
            pl.read_csv(bars_csv)
            if bars_csv.suffix.lower() == ".csv"
            else pl.read_parquet(bars_csv)
        )

        # BUG-176 companion: the dataset factory requires PRE-COMPUTED feature
        # columns (feat_0..feat_{n-1} per the schema) + an ATR column. Feeding it
        # plain OHLCV bars (e.g. data/raw/XAUUSD_M1.parquet) used to surface the
        # labeler's raw "ValueError: DataFrame must contain either 'atr_m1' or
        # 'atr' column." traceback. Fail fast with an actionable contract panel.
        required = [f"feat_{i}" for i in range(feature_schema.dimension)]
        missing_feat = [c for c in required if c not in df.columns]
        missing_atr = [c for c in ("atr_m1", "atr") if c not in df.columns] == ["atr_m1", "atr"]
        if missing_feat or (
            not missing_atr and "atr_m1" not in df.columns and "atr" not in df.columns
        ):
            need: list[str] = []
            if missing_feat:
                need.append(
                    f"{len(missing_feat)} feature columns ({missing_feat[0]}..{missing_feat[-1]})"
                )
            if "atr_m1" not in df.columns and "atr" not in df.columns:
                need.append("atr_m1 (or atr)")
            console.print(
                _error_panel(
                    "Raw bars are missing required pre-computed columns",
                    f"schema {feature_schema.schema_id} requires {len(required)} feature "
                    f"columns + ATR; missing: {', '.join(need)}",
                    hint=(
                        "Run the feature engine first (ScalpFeatureEngine / features pipeline) "
                        "to compute feat_* + atr columns, or export bars WITH features. "
                        "See docs/forensic-docs/modules/cli/main.md (model-dataset-build "
                        "input contract) — this command does NOT compute features from raw OHLCV."
                    ),
                    exit_code=xc.EXIT_RUNTIME,
                )
            )
            raise typer.Exit(xc.EXIT_RUNTIME) from None
        news_frame = None
        if with_news:
            # BUG-150: the empty-Path sentinel ``Path("")`` normalizes to Path(".")
            # (truthy + "exists"), so a bare --with-news used to open the CURRENT
            # DIRECTORY as the news DB and crash with sqlite3.OperationalError.
            # Resolve the default to the canonical artifacts/news.db instead.
            if str(news_db) in ("", "."):
                news_db = Path("artifacts/news.db")
            if news_db.exists():
                from nexus_scalp.model_generation.news_bridge import (
                    build_news_frame_from_db,
                    news_benchmark_readiness,
                )
                from nexus_scalp.news.database import NewsDatabase

                news_frame = build_news_frame_from_db(NewsDatabase(news_db))
                console.print(
                    f"[cyan]News frame exported from DB:[/cyan] {news_db} "
                    f"rows={news_frame.height if news_frame is not None else 0}"
                )
                if news_frame is None or news_frame.is_empty():
                    console.print(
                        Panel(
                            "[yellow]News database contains NO analysis records — the dataset "
                            "will carry all-zero news context (news ON == news OFF). "
                            "Collect real news first.[/yellow]",
                            border_style="yellow",
                        )
                    )
                else:
                    gate = news_benchmark_readiness(news_frame)
                    if not gate["ready"]:
                        console.print(
                            Panel(
                                "[yellow]NEWS READINESS GATE: NOT READY — the news frame does not "
                                "satisfy the real-data requirements (non-neutral > 0, XAUUSD > 0, "
                                "multiple events, distinct vectors). News context in this dataset "
                                "may be uninformative. Do NOT use it for a news benchmark.[/yellow]",
                                border_style="yellow",
                            )
                        )
                        console.print(f"[yellow]Failed checks: {gate['checks']}[/yellow]")
                    else:
                        console.print(
                            Panel(
                                "[green]NEWS READINESS GATE: READY — real news context.[/green]",
                                border_style="green",
                            )
                        )
            elif news_csv.exists() and str(news_csv) not in ("", "."):
                # BUG-150 companion: never treat the Path("") sentinel (-> ".") as
                # a real news file; read_parquet(".") produced a bogus parquet
                # crash. Only read when the user actually named a file.
                news_frame = (
                    pl.read_csv(news_csv)
                    if news_csv.suffix.lower() == ".csv"
                    else pl.read_parquet(news_csv)
                )
                from nexus_scalp.model_generation.news_bridge import news_benchmark_readiness

                gate = news_benchmark_readiness(news_frame)
                if not gate["ready"]:
                    console.print(
                        Panel(
                            "[yellow]NEWS READINESS GATE: NOT READY for --news file — the frame "
                            "does not satisfy the real-data requirements. "
                            "Do NOT use it for a news benchmark.[/yellow]",
                            border_style="yellow",
                        )
                    )
            else:
                console.print(
                    Panel(
                        "[yellow]--with-news given but no --news file or --news-db found; "
                        "news context will be all-zero (news ON == news OFF).[/yellow]",
                        border_style="yellow",
                    )
                )
        store = _mg_store()
        # BUG-176: the user-selected schema is now THREADed into the factory (it
        # was declared but ignored before — SampleFactory silently built scalp_v1).
        from nexus_scalp.model_generation.sample_factory import SampleFactory

        handle = DatasetFactory(
            store=store,
            sample_factory=SampleFactory(feature_schema_id=feature_schema.schema_id),
        ).build(df, symbol=symbol, timeframe=timeframe, news_frame=news_frame)
        console.print(
            _success_panel(
                "Dataset built",
                f"{handle['dataset_id']}  rows={handle['counts']['total']}",
                border="green",
            )
        )
        _emit(handle, as_json=False, plain=True)

    @app.command("model-experiment-create")
    def model_experiment_create(
        dataset_id: str = typer.Option(..., "--dataset", help="dataset artifact id"),
        template: str = typer.Option("baseline_scalpnet_v1", "--template"),
    ) -> None:
        """Create a bounded experiment on a dataset artifact."""
        from nexus_scalp.model_generation import ExperimentFactory

        store = _mg_store()
        # A dataset must EXIST before an experiment can bind to it; creating a
        # ghost experiment that later crashes `model-train` with a raw traceback
        # is a bad contract (E2E BUG-159). Fail fast with a clean user error.
        if store.read_dataset(dataset_id) is None:
            console.print(
                _error_panel(
                    "Dataset not found",
                    dataset_id,
                    hint="Run `nexus model-dataset-build` first, then pass its dataset id",
                )
            )
            raise typer.Exit(xc.EXIT_USAGE) from None
        try:
            cfg = ExperimentFactory(store=store).create(dataset_id, template=template)
        except Exception as e:
            console.print(_error_panel("Could not create experiment", str(e)))
            raise typer.Exit(xc.EXIT_RUNTIME) from None
        console.print(
            _success_panel(
                "Experiment created",
                f"{cfg.experiment_id}  arch={cfg.architecture}",
                border="green",
            )
        )
        _emit(cfg.model_dump(mode="json"), as_json=False, plain=True)

    @app.command("model-train")
    def model_train(
        experiment_id: str = typer.Option(..., "--experiment"),
        model_id: str = typer.Option("", "--model-id"),
    ) -> None:
        """Train a candidate from an experiment (never touches Champion)."""
        from nexus_scalp.model_generation import CandidateTrainer, ExperimentFactory

        store = _mg_store()
        try:
            exp = ExperimentFactory(store=store).load(experiment_id)
            frame = store.read_dataset(exp.dataset_id)
        except Exception as e:
            console.print(_error_panel("Could not load experiment/dataset", str(e)))
            raise typer.Exit(xc.EXIT_RUNTIME) from None
        res = CandidateTrainer(store=store).train_candidate(exp, frame, model_id=model_id or None)
        if res["status"] == "FAILED":
            console.print(_error_panel("Training failed", str(res.get("error", ""))))
            raise typer.Exit(1) from None
        console.print(
            _success_panel("Training complete", f"model={res.get('model_id')}", border="green")
        )
        _emit(res, as_json=False, plain=True)

    @app.command("model-inspect")
    def model_inspect(model_id: str = typer.Option(..., "--model")) -> None:
        """Inspect a model artifact manifest + integrity."""
        store = _mg_store()
        man = store.read_model_manifest(model_id)
        if not man:
            console.print(_error_panel("Model not found", model_id, hint="Check --model id"))
            raise typer.Exit(1) from None
        v = store.verify_artifact(model_id)
        style = "green" if v["ok"] else "red"
        console.print(
            Panel(
                f"[bold {style}]{'OK' if v['ok'] else 'FAIL'}[/bold {style}]  {model_id}  integrity={v['ok']}",
                border_style=style,
            )
        )
        _emit({"manifest": man, "integrity": v}, as_json=False, plain=True)

    @app.command("model-validate")
    def model_validate(
        model_id: str = typer.Option(..., "--model"),
        dataset_id: str = typer.Option(..., "--dataset"),
    ) -> None:
        """Validate a candidate artifact against its dataset (OOS/regime/collapse)."""
        from nexus_scalp.model_generation import ValidationFactory

        store = _mg_store()
        # BUG-164: read_dataset returns None for an absent artifact (never
        # raises), so this except never fired and `frame["label"]` crashed
        # with a raw "'NoneType' object is not subscriptable" traceback.
        # Fail fast with the clean dataset-not-found contract instead.
        frame = store.read_dataset(dataset_id)
        if frame is None:
            console.print(
                _error_panel(
                    "Dataset not found",
                    dataset_id,
                    hint="Run `nexus model-dataset-build` first, then pass its dataset id",
                )
            )
            raise typer.Exit(xc.EXIT_USAGE) from None
        # BUG-175: the model must actually RUN here. Passing probabilities=None
        # made every gate that needs predictions fall to 0.0 with a
        # NO_PROBABILITIES note — a fabricated REJECTED even for a good model
        # (no candidate could ever become CHALLENGER_ELIGIBLE via this command),
        # and a cross-schema width mismatch stayed invisible inside it.
        # Compute REAL probabilities from the artifact (same replay as
        # model_generation.benchmark._predict_probs) and fail fast with an
        # explicit error when the artifact cannot be loaded or the dataset
        # width does not match the model's expected input.
        import numpy as np

        labels = frame["label"].to_numpy().astype(np.int64)
        try:
            probabilities = _predict_candidate_probs(store, model_id, frame)
        except typer.Exit:
            raise
        except Exception as e:  # artifact load failure is a runtime error, never 0.0
            console.print(
                _error_panel(
                    "Model artifact could not be loaded",
                    f"{model_id}: {e}",
                    exit_code=xc.EXIT_RUNTIME,
                )
            )
            raise typer.Exit(xc.EXIT_RUNTIME) from None
        vf = ValidationFactory()
        try:
            vr = vf.validate(model_id, "cli", frame, probabilities, labels)
        except Exception as e:
            console.print(_error_panel("Validation failed", str(e)))
            raise typer.Exit(xc.EXIT_RUNTIME) from None
        color = "green" if vr.passed else "red"
        console.print(
            Panel(
                f"[bold {color}]{vr.verdict}[/bold {color}]  passed={vr.passed}", border_style=color
            )
        )
        _emit(vr.model_dump(mode="json"), as_json=False, plain=True)

    def _predict_candidate_probs(store: Any, model_id: str, frame: Any) -> Any:
        """Replays the candidate over the dataset frame -> (N, C) probabilities.

        Mirrors ``model_generation.benchmark._predict_probs``: manifest-driven
        news columns, persisted scaler transform, 2D snapshot path for
        legacy/MLP heads and a causal window path for sequence architectures.
        Raises ``ValueError`` on a dataset/model width mismatch (the CLI turns
        it into the explicit SCHEMA_MISMATCH panel) and any artifact-load error
        upward (the CLI turns it into the load-failure panel).
        """
        import numpy as np
        import torch

        from nexus_scalp.model_generation.runtime import LocalModelRuntime

        rt = LocalModelRuntime(store=store).load(model_id)
        mm = store.read_model_manifest(model_id) or {}
        news_enabled = bool(mm.get("news_enabled", False))
        base_dim = int(mm.get("feature_dimension", 0) or 0)
        metadata = mm.get("build_metadata", {}) or {}
        input_dim = int(metadata.get("input_dimension", base_dim) or base_dim)
        feat_cols = [c for c in frame.columns if c.startswith("feat_")]
        news_cols = (
            [c for c in frame.columns if c.startswith("news_") and c != "news_context_schema_id"]
            if news_enabled
            else []
        )
        width = len(feat_cols) + len(news_cols)
        if width != input_dim:
            # Fail FAST and LOUD: a width mismatch previously surfaced as a
            # silent fabricated REJECTED (oos 0.0) instead of an error.
            raise ValueError(
                f"SCHEMA_MISMATCH: model expects {input_dim} features, "
                f"dataset provides {width} "
                f"(feat={len(feat_cols)}, news={len(news_cols)}, "
                f"model_schema={mm.get('feature_schema_id', '?')})"
            )
        if width == 0:
            raise ValueError("dataset frame carries no feat_* columns to replay")

        arch = str(mm.get("architecture_id", "LEGACY_SCALPNET_V1"))
        is_seq_arch = arch == "TCN_ATTENTION_V1"
        if is_seq_arch:
            # sequence path: reuse SequenceBuilder for the same causal windows
            # the sequence trainer trained on (never a fake 2D shortcut).
            from nexus_scalp.model_generation.sequence import SequenceBuilder

            builder = SequenceBuilder(seq_len=16)
            seqdata = builder.build(frame, news_enabled=news_enabled)
            valid = seqdata["valid"]
            X = seqdata["X"][valid]
            if valid.sum() == 0:
                raise ValueError(
                    "sequence replay produced 0 valid windows (check timestamp/symbol columns)"
                )
            if rt._scaler is not None:
                mean, std = rt._scaler
                X = ((X - mean) / (std + 1e-8)).astype(np.float32)
            with torch.inference_mode():
                logits = rt._model(torch.from_numpy(X))
                probs = torch.softmax(logits, dim=-1).numpy()
            # align to the FULL frame (invalid windows get a zero row) so the
            # per-class comparison uses the SAME sample set as the 2D path
            full = np.zeros((frame.height, probs.shape[1]), dtype=np.float32)
            rows = np.where(valid)[0]
            full[rows] = probs
            return full

        X = frame.select(feat_cols + news_cols).to_numpy().astype(np.float32)
        if rt._scaler is not None:
            mean, std = rt._scaler
            X = (X - mean) / (std + 1e-8)
        with torch.inference_mode():
            logits = rt._model(torch.from_numpy(X))
            return torch.softmax(logits, dim=-1).numpy()

    @app.command("model-replay")
    def model_replay(
        dataset_id: str = typer.Option(..., "--dataset"),
        sample_id: str = typer.Option(..., "--sample"),
        model_id: str = typer.Option("", "--model"),
    ) -> None:
        """Replay one sample (historical context + optional model prediction)."""
        from nexus_scalp.model_generation import SampleReplay

        try:
            rec = SampleReplay(store=_mg_store()).replay(
                dataset_id, sample_id, model_id=model_id or None
            )
        except Exception as e:
            console.print(_error_panel("Replay failed", str(e)))
            raise typer.Exit(xc.EXIT_RUNTIME) from None
        _emit(rec, as_json=False, plain=True)

    @app.command("model-doctor")
    def model_doctor(model_id: str = typer.Option(..., "--model")) -> None:
        """Run the model doctor: integrity + load + metadata health."""
        from nexus_scalp.model_generation import LocalModelRuntime

        store = _mg_store()
        v = store.verify_artifact(model_id)
        if not v["ok"]:
            console.print(
                _error_panel(
                    "Model failed integrity",
                    f"{model_id}: {v.get('reason')}",
                    exit_code=xc.EXIT_RUNTIME,
                )
            )
            raise typer.Exit(1) from None
        try:
            rt = LocalModelRuntime(store=store).load(model_id)
            console.print(_success_panel("Model healthy", model_id, border="green"))
            _emit({"integrity": v, "health": rt.health()}, as_json=False, plain=True)
        except Exception as e:
            console.print(_error_panel("Model failed to load", str(e), exit_code=xc.EXIT_RUNTIME))
            raise typer.Exit(1) from None

    @app.command("model-train-3")
    def model_train_3(
        variant: str = typer.Option(
            "", "--variant", help="50d_main | 70d_news | 70d_liquidity (empty = all)"
        ),
        smoke: bool = typer.Option(
            False, "--smoke", help="Small quick validation run (2 folds, 1 epoch)."
        ),
        folds: int = typer.Option(34, "--folds", help="Walk-forward folds (full run)."),
        epochs: int = typer.Option(10, "--epochs", help="Epochs per fold (full run)."),
        json_mode: bool = typer.Option(False, "--json", help="JSON output."),
    ) -> None:
        """Train the 3-model matrix (50D-main / 70D+news / 70D+liquidity).

        Every variant runs the canonical purged walk-forward trainer + the
        BenchmarkRunner evidence gate, then is registered in the model lifecycle
        as CHALLENGER (shadow-eligible / hot-swappable via the shadow70 attach
        """
        # BUG-151: the pipeline lives in ``three_model`` (there has never been a
        # ``three_model_pipeline`` module) — every invocation used to crash with
        # ModuleNotFoundError before any training could start.
        from nexus_scalp.model_generation.three_model import train_all

        if variant and variant not in ("50d_main", "70d_news", "70d_liquidity"):
            msg = f"unknown variant '{variant}' (allowed: 50d_main, 70d_news, 70d_liquidity)"
            if json_mode:
                _emit({"error": msg, "exit_code": xc.EXIT_USAGE}, True)
            else:
                console.print(
                    _error_panel("Invalid variant", msg, hint="Empty --variant trains all three")
                )
            raise typer.Exit(xc.EXIT_USAGE) from None
        try:
            with Progress(
                SpinnerColumn(style="cyan"),
                TextColumn("[bold cyan]Training 3-model matrix…[/bold cyan]"),
                transient=True,
                console=console,
            ) as progress:
                progress.add_task("train", total=None)
                bars_path = Path("data/raw/XAUUSD_M1.parquet")
                if not bars_path.exists():
                    raise FileNotFoundError(
                        f"canonical bars file missing: {bars_path} — run the data pipeline first"
                    )
                import polars as pl

                bars_frame = pl.read_parquet(bars_path)
                reports = train_all(
                    bars_frame,
                    variants=[variant] if variant else None,
                    num_folds=folds,
                    epochs=epochs,
                    smoke=smoke,
                )
                by_variant = {r["variant"]: r for r in reports}
                result = {
                    "overall": "PASS"
                    if all(r.get("gate") in ("PASS", "COMPLETED", "READY") for r in reports)
                    else "EVIDENCE_WRITTEN",
                    "variants": by_variant,
                }
        except Exception as e:
            if json_mode:
                _emit({"error": str(e), "exit_code": xc.EXIT_RUNTIME}, True)
            else:
                console.print(_error_panel("Training failed", str(e), exit_code=xc.EXIT_RUNTIME))
            raise typer.Exit(xc.EXIT_RUNTIME) from None
        if json_mode:
            _emit(result, True)
            raise typer.Exit(0 if result.get("overall") == "PASS" else 1)
        for v, r in result.get("variants", {}).items():  # type: ignore[union-attr]
            style = "green" if r.get("status") == "PASS" else "red"
            console.print(f"[{style}]{r.get('status'):5}[/{style}] {v:16} {r.get('detail', '')}")
