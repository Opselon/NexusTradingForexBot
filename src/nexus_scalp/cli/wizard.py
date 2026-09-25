"""First-run setup wizard + uninstall CLI.

WHERE/WHY: the interactive ``nexus install``/``nexus setup`` wizard flow
(_wizard_flow: compatibility → repair → mode selection (never silently LIVE) →
health), its config-persistence helpers and the ``nexus uninstall`` data-safety
command. Extracted verbatim from cli/main.py (CHG-0032 Step 1).

BOUNDARY: setup/uninstall ceremony only. Engine boot lives in engine_boot.py;
safety contract (PAPER/SHADOW defaults, LIVE needs explicit confirmation) is
preserved byte-identically.

USED BY: cli.main facade (registers setup/install/uninstall), cli.engine_boot
(_get_network_endpoints).

DO-NOT-PUT-HERE: start/stop commands, config validation commands (doctor.py).
"""

from __future__ import annotations

import contextlib
import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer
from rich import box
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from nexus_scalp.cli.app_factory import _resolve_facade_seam, app
from nexus_scalp.cli.styling import (
    MODE_ALIASES,
    _banner,
    _emit,
    _error_panel,
    _success_panel,
    _verdict_style,
    console,
)
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.release import environment as renv
from nexus_scalp.release import evaluate as reval
from nexus_scalp.release import exit_codes as xc
from nexus_scalp.release import health as rhealth
from nexus_scalp.release import paths as rpaths
from nexus_scalp.release import repair as rrepair
from nexus_scalp.release.metadata import get_version_info


# ---------------------------------------------------------------------------
def _package_inventory() -> list[dict[str, str]]:
    """Truthful package inventory for setup/summary (2026-09-02 UX pass).

    Reads the installed distribution metadata + importability — real versions
    only; optional groups are marked OPTIONAL and never counted as failures.
    """
    import importlib
    import importlib.metadata as im

    spec = [  # (distribution name, import name, required)
        ("pydantic", "pydantic", True),
        ("httpx", "httpx", True),
        ("yaml", "yaml", True),
        ("typer", "typer", True),
        ("rich", "rich", True),
        ("fastapi", "fastapi", False),
        ("uvicorn", "uvicorn", False),
        ("MetaTrader5", "MetaTrader5", False),
    ]
    rows: list[dict[str, str]] = []
    for dist, mod, required in spec:
        try:
            ver = im.version(dist)
        except Exception:
            ver = ""
        status = "MISSING"
        if ver:
            try:
                importlib.import_module(mod)
                status = "OK"
            except Exception:
                status = "BROKEN"
        rows.append(
            {
                "package": dist,
                "version": ver or "--",
                "status": status,
                "tier": "REQUIRED" if required else "OPTIONAL",
            }
        )
    return rows


def _wizard_flow(json_mode: bool) -> dict[str, Any]:
    console.print(_banner(subtitle="first-run setup wizard"))
    console.print(
        Panel(
            "Compatibility check → install → database → model → mode → health",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )

    env = renv.detect_environment()
    results = reval.evaluate_requirements(env)
    verdict, _lines = reval.overall_verdict(results)

    table = Table(title="Compatibility", box=box.SIMPLE_HEAD)
    table.add_column("Component", style="bold white")
    table.add_column("Result", style="bold")
    table.add_column("Detail", style="dim")
    for r in results:
        table.add_row(r.name, _verdict_style(r.verdict), r.detail)
    console.print(table)

    if verdict == "BLOCKED":
        console.print(
            _error_panel(
                "Setup blocked",
                "This machine blocks installation",
                hint="See compatibility table above",
                exit_code=xc.EXIT_ENVIRONMENT,
            )
        )
        raise typer.Exit(xc.EXIT_ENVIRONMENT) from None

    with Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[cyan]Preparing workspace…[/cyan]"),
        transient=True,
        console=console,
    ) as p:
        p.add_task("prep", total=None)
        engine = rrepair.RepairEngine()
        repaired = engine.run()
    for op in repaired:
        if op.status == "FAILED":
            console.print(
                _error_panel(
                    "Setup step failed",
                    f"{op.action} — {op.detail}",
                    hint="Run nexus repair --recreate-config or nexus doctor --fix",
                )
            )
            raise typer.Exit(1) from None

    # DATABASE STEP (first-run provider choice, 2026-09-24) — the banner above
    # advertises "… → database → …" but the flow used to jump repair → mode with
    # NO database question at all, so NSE never asked PostgreSQL-or-SQLite while
    # a silently-persisted database.provider=postgresql (actor 'db-fabric') sent
    # the runtime at a dead server. Gated inside: asks ONLY when the settings DB
    # has no database.provider row — a configured install gets ZERO new prompts.
    db_choice = run_first_run_database_choice()
    if db_choice.get("reason") == "non_interactive":
        console.print(
            "[dim]Database choice deferred (no interactive terminal) — "
            "rerun nexus setup to be asked.[/dim]"
        )

    # Mode selection — never silently LIVE.
    mode = typer.prompt("Execution mode (PAPER / SHADOW / LIVE)", default="PAPER").strip().upper()
    if mode.lower() not in MODE_ALIASES:
        mode = "PAPER"
    if mode == "LIVE":
        confirm = typer.confirm(
            "WARNING: LIVE mode places real orders and risks real capital. Continue?",
            default=False,
        )
        if not confirm:
            console.print(
                Panel("[yellow]Setup aborted — LIVE not confirmed.[/yellow]", border_style="yellow")
            )
            raise typer.Exit(1) from None

    symbol = (
        typer.prompt("Trading symbol (XAUUSD=Gold, EURUSD, GBPUSD, ...)", default="XAUUSD")
        .strip()
        .upper()
    )
    if not symbol:
        symbol = "XAUUSD"
    config_path = rpaths.get_user_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    template = rrepair.RepairEngine().template_config
    if not config_path.exists() and template.exists():
        shutil.copy2(template, config_path)
    try:
        cfg = AppConfig.load_from_yaml(config_path) if config_path.exists() else AppConfig()
    except Exception:
        cfg = AppConfig()
    cfg.execution.mode = MODE_ALIASES[mode.lower()]
    cfg.execution.symbol = symbol
    # Persist effective mode/symbol into the yaml (idempotent write of the
    # whole validated config is safer than regex surgery).
    _write_effective_config(config_path, cfg)

    health = rhealth.HealthEngine(config_path=config_path)
    verdict2, entries = health.overall()
    return {
        "mode": mode,
        "symbol": symbol,
        # First-run database step outcome (secret-free).
        "database_provider": db_choice.get("provider", ""),
        "database_prompted": bool(db_choice.get("prompted")),
        "port": 8080,
        "web_endpoints": _get_network_endpoints(port=8080),
        "health_overall": verdict2,
        "health_checks": [e.to_dict() for e in entries],
        "packages": _package_inventory(),
    }


def _write_effective_config(path: Path, cfg: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.model_dump(mode="python") if hasattr(cfg, "model_dump") else dict(cfg)
    # Minimal YAML emission via yaml; fall back to json-ish if yaml unavailable

    try:
        import yaml  # type: ignore

        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False)
    except Exception:
        # Fallback: write a tiny JSON-ish (still valid for load_from_yaml permissive path)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)


def _get_network_endpoints(port: int = 8080) -> list[str]:
    endpoints: list[str] = [f"http://localhost:{port}", f"http://127.0.0.1:{port}"]
    # Also advertise LAN ip if discoverable

    try:
        import socket as _socket

        hostname = _socket.gethostname()
        lan = _socket.gethostbyname(hostname)
        if lan and not lan.startswith("127.") and lan not in endpoints:
            endpoints.append(f"http://{lan}:{port}")
    except Exception:
        pass
    return endpoints


# ---------------------------------------------------------------------------
# First-run DATABASE PROVIDER CHOICE (2026-09-24) — dual-entry surface.
# ---------------------------------------------------------------------------
#
# WHERE/WHY: the advertised wizard flow is "compatibility → install → DATABASE
# → model → mode → health", but _wizard_flow jumped from repair straight to
# mode selection with no database step, while app_settings.db silently carried
# database.provider=postgresql (actor 'db-fabric') — so NSE never asked the
# operator PostgreSQL-or-SQLite and the runtime later chased a dead PostgreSQL
# server. This step asks the question ONCE on every entry surface: `nexus setup`
# (here, inside _wizard_flow), `nexus start` (cli/engine_boot.py) and the
# double-click launcher (NexusTradingForexBot.py).
#
# GATE (idempotency contract): it fires ONLY when the SettingsDatabase has NO
# database.provider row. An already-configured install boots with ZERO new
# prompts; non-interactive sessions (--json, CI, piped stdin) are never blocked.
#
# SECURITY CONTRACT (existing pattern, unchanged): the password is read with
# hide_input, written ONLY through the OS-backed secret store path
# (SettingsService.set_postgres_config → PG_PASSWORD_SECRET_KEY) and never
# echoed, logged or persisted anywhere else. DatabaseConfig carries the secret
# KEY name (`password_secret`), settings rows carry no secret at all, and every
# URL shown on the console goes through mask_url_password() first.
#
# NON-DESTRUCTIVE CONTRACT: a PostgreSQL config is persisted ONLY after a live
# validation succeeds. A failed validation never falls back to SQLite
# automatically — the operator gets a categorized error (UNREACHABLE /
# AUTHENTICATION FAILED / DATABASE NOT FOUND) plus an explicit choice: retry,
# or switch to SQLite (their choice, never ours).

#: Prompt label for the provider question (single source for tests/UX parity).
PROVIDER_PROMPT_LABEL = "Database provider (SQLITE / POSTGRESQL)"

_SQLITE_ANSWERS = frozenset({"", "sqlite", "sqlite3", "lite", "s", "local"})
_POSTGRES_ANSWERS = frozenset({"postgresql", "postgres", "pgsql", "pg", "p", "server"})

#: Sentinel: a prompt was cancelled (EOF / Ctrl+C / click Abort) — never a value.
_PROMPT_CANCELLED = object()


def _default_prompt(text: str, default: Any = None, hide_input: bool = False) -> Any:
    """Console prompt via typer (the LIVE-confirmation style used everywhere)."""
    return typer.prompt(text, default=default, hide_input=hide_input)


def _ask(
    ask: Callable[..., Any], text: str, *, default: Any = None, hide_input: bool = False
) -> Any:
    """One prompt that can never crash its caller: EOF/Ctrl+C/Abort → sentinel."""
    try:
        return str(ask(text, default=default, hide_input=hide_input) or "")
    except (EOFError, KeyboardInterrupt, Exception):
        return _PROMPT_CANCELLED


def _categorize_postgres_error(exc: BaseException) -> tuple[str, str]:
    """Bucket a PostgreSQL failure into an operator-facing category.

    Returns (CATEGORY, detail) with CATEGORY one of UNREACHABLE /
    AUTHENTICATION FAILED / DATABASE NOT FOUND — the three answers that tell
    the operator what to fix. The raw driver message is returned as detail
    (it never contains the password).
    """
    sqlstate = str(getattr(exc, "sqlstate", "") or "")
    msg = str(exc) or exc.__class__.__name__
    low = msg.lower()
    if (
        sqlstate in {"28000", "28001", "28P01"}
        or "password authentication failed" in low
        or "no password supplied" in low
        or ("role" in low and "does not exist" in low)
    ):
        return "AUTHENTICATION FAILED", msg
    if sqlstate == "3D000" or ("database" in low and "does not exist" in low):
        return "DATABASE NOT FOUND", msg
    return "UNREACHABLE", msg


def _validate_postgres_connection(
    cfg: Any, password: str, *, timeout: int = 8
) -> tuple[bool, str, str]:
    """Live PostgreSQL connectivity check, run BEFORE anything is persisted.

    Uses the existing driver/config path (get_driver → build_postgres_url →
    SELECT 1), with the password resolved from the OS secret store exactly the
    way the runtime resolves it. The typed password is staged in the secret
    store only for the duration of the attempt; on failure the store is
    restored to exactly what it held before, so a rejected configuration
    leaves NO settings row and NO secret behind.

    Returns (ok, category, detail); detail is always scrubbed/masked.
    """
    from nexus_scalp.database.config import PG_PASSWORD_SECRET_KEY, mask_url_password
    from nexus_scalp.database.drivers import driver_available, get_driver
    from nexus_scalp.settings.secret_store import SecureSecretStore

    def _scrub(text: str) -> str:
        return text.replace(password, "***") if password else text

    if not driver_available(cfg):
        return (
            False,
            "UNREACHABLE",
            "PostgreSQL driver unavailable — psycopg is not installed "
            "(pip install 'nexus[postgres]').",
        )
    store = SecureSecretStore()
    previous = store.get_secret(PG_PASSWORD_SECRET_KEY)
    store.set_secret(PG_PASSWORD_SECRET_KEY, password)
    ok = False
    category = "UNREACHABLE"
    detail = ""
    try:
        driver = get_driver(cfg)
        conn = driver.connect(timeout=float(timeout))
        try:
            ok = bool(driver.ping(conn=conn))
        finally:
            with contextlib.suppress(Exception):
                conn.close()
        if ok:
            # Evidence URL only — password masked before it can reach a console.
            detail = mask_url_password(cfg.build_url(password=password))
            category = ""  # success carries no failure category
        else:
            detail = "server answered but 'SELECT 1' did not return 1."
    except Exception as exc:
        category, detail = _categorize_postgres_error(exc)
        detail = _scrub(str(detail))
    finally:
        if not ok:
            # Non-destructive: put the secret store back the way we found it.
            with contextlib.suppress(Exception):
                if previous:
                    store.set_secret(PG_PASSWORD_SECRET_KEY, previous)
                else:
                    store.delete_secret(PG_PASSWORD_SECRET_KEY)
    return ok, category, detail


def _persist_sqlite_choice(svc: Any) -> None:
    """Persist an EXPLICIT SQLite choice (and clear a stale PG config row).

    First run only (this path exists solely when database.provider was absent).
    Clearing matters: load_database_config applies a leftover
    database.postgresql_config row whenever it exists, regardless of the
    provider — so a stale, never-validated row would hijack the operator's
    SQLite choice and send the runtime back to PostgreSQL.
    """
    svc.set_database_provider("sqlite", actor="first_run_setup")
    try:
        from nexus_scalp.database.config import PG_CONFIG_SETTING_KEY

        if svc.db.get(PG_CONFIG_SETTING_KEY) is not None:
            svc.db.delete(PG_CONFIG_SETTING_KEY)
            console.print(
                "[dim]Cleared a stale database.postgresql_config row "
                "(never validated, not chosen).[/dim]"
            )
    except Exception:
        pass


def _cancelled_out(reason: str = "cancelled") -> dict[str, Any]:
    console.print(
        Panel(
            "[yellow]Database choice not completed — nothing was saved. "
            "You will be asked again on the next interactive run.[/yellow]",
            border_style="yellow",
        )
    )
    return {
        "provider": "",
        "persisted": False,
        "validated": False,
        "category": "",
        "detail": "",
        "reason": reason,
    }


def _configure_postgres(svc: Any, ask: Callable[..., Any]) -> dict[str, Any]:
    """Prompt for PostgreSQL details, VALIDATE them, persist only on success.

    On failure: categorized error + explicit retry-or-SQLite choice. Never a
    silent fallback, never a persisted broken config, never an echoed password.
    """
    from nexus_scalp.database.config import DatabaseConfig, DatabaseConfigError

    out: dict[str, Any] = {
        "provider": "postgresql",
        "persisted": False,
        "validated": False,
        "category": "",
        "detail": "",
        "reason": "",
    }
    while True:
        host = _ask(ask, "PostgreSQL host", default="localhost")
        if host is _PROMPT_CANCELLED:
            return _cancelled_out()
        port_raw = _ask(ask, "PostgreSQL port", default="5432")
        if port_raw is _PROMPT_CANCELLED:
            return _cancelled_out()
        try:
            port = int(str(port_raw).strip())
        except ValueError:
            console.print(
                _error_panel(
                    "Invalid port",
                    f"'{str(port_raw).strip()}' is not a number.",
                    hint="Enter a port like 5432 (nothing was saved)",
                )
            )
            continue
        database = _ask(ask, "PostgreSQL database", default="nse_audit")
        username = _ask(ask, "PostgreSQL username", default="nse_user")
        if database is _PROMPT_CANCELLED or username is _PROMPT_CANCELLED:
            return _cancelled_out()
        password = ""
        while not password:
            password = _ask(
                ask,
                "PostgreSQL password (stored in the OS secret store — never echoed)",
                hide_input=True,
            )
            if password is _PROMPT_CANCELLED:
                return _cancelled_out()
            if not password:
                console.print(
                    "[yellow]A password is required — it is written only to the "
                    "OS secret store, never to config files or logs.[/yellow]"
                )
        cfg = DatabaseConfig.for_postgres(
            domain="audit",
            host=str(host).strip(),
            port=port,
            database=str(database).strip(),
            username=str(username).strip(),
        )
        try:
            cfg.validate()
        except DatabaseConfigError as exc:
            console.print(
                _error_panel(
                    "Invalid PostgreSQL settings",
                    str(exc),
                    hint="Re-enter the connection details (nothing was saved)",
                )
            )
            continue
        ok, category, detail = _validate_postgres_connection(cfg, password)
        if not ok:
            out.update(category=category, detail=detail, validated=False)
            console.print(
                _error_panel(
                    f"PostgreSQL validation failed — {category}",
                    detail,
                    hint="Nothing was saved. Fix the details and retry, or explicitly "
                    "switch to SQLite.",
                )
            )
            action = _ask(
                ask,
                "PostgreSQL did not validate. [R]etry / [S]witch to SQLite (your explicit choice)",
                default="R",
            )
            if action is _PROMPT_CANCELLED:
                return {**_cancelled_out(), "category": category, "detail": detail}
            if str(action).strip().lower().startswith("s"):
                # EXPLICIT operator choice — never an automatic fallback.
                _persist_sqlite_choice(svc)
                console.print(
                    Panel(
                        "[yellow]Using SQLite — your explicit choice after the failed "
                        "PostgreSQL validation.[/yellow]",
                        border_style="yellow",
                    )
                )
                return {
                    "provider": "sqlite",
                    "persisted": True,
                    "validated": False,
                    "category": category,
                    "detail": detail,
                    "reason": "explicit_sqlite_after_validation_failure",
                }
            continue  # retry the PostgreSQL details from the top
        payload = cfg.to_dict()
        payload["password"] = password
        svc.set_postgres_config(payload, actor="first_run_setup")
        svc.set_database_provider("postgresql", actor="first_run_setup")
        console.print(
            _success_panel(
                "PostgreSQL validated",
                f"Connected to {cfg.host}:{cfg.port}/{cfg.database} as {cfg.username}\n"
                f"URL (masked): {detail}\n"
                "Password stored in the OS secret store only — config rows are secret-free.",
                border="green",
            )
        )
        return {
            "provider": "postgresql",
            "persisted": True,
            "validated": True,
            "category": "",
            "detail": detail,
            "reason": "validated_and_persisted",
        }


def run_first_run_database_choice(
    settings_service: Any | None = None,
    *,
    prompt_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Interactive first-run database provider choice (idempotent, secret-free).

    Shared by all three entry surfaces: `nexus setup` (_wizard_flow),
    `nexus start` (cli/engine_boot.py) and the double-click launcher
    (NexusTradingForexBot.py).

    Gates, in order:
      1. database.provider row already present → return immediately, ZERO prompts;
      2. non-interactive session (no TTY, no injected prompt_fn) → deferred, never blocks;
      3. otherwise ask the question and persist only what the operator chose
         (PostgreSQL only after a successful live validation).

    Returns a secret-free outcome dict: prompted / provider / persisted /
    validated / category / detail / reason. Never raises.
    """
    from nexus_scalp.database.config import PROVIDER_SETTING_KEY
    from nexus_scalp.settings.service import SettingsService

    out: dict[str, Any] = {
        "prompted": False,
        "provider": "",
        "persisted": False,
        "validated": False,
        "category": "",
        "detail": "",
        "reason": "",
    }
    try:
        svc = settings_service or SettingsService()
        row = svc.db.get(PROVIDER_SETTING_KEY)
    except Exception as exc:
        out["reason"] = f"settings_db_unavailable: {exc}"
        return out
    if row is not None and row.value:
        # GATE 1 — an already-configured install must boot with ZERO new prompts.
        out.update(provider=str(row.value), reason="already_configured")
        return out
    if prompt_fn is None and not sys.stdin.isatty():
        # GATE 2 — CI / --json / piped stdin: defer, never hang a boot.
        out["reason"] = "non_interactive"
        return out

    ask = prompt_fn or _default_prompt
    out["prompted"] = True
    console.print(
        Panel(
            "Choose where NSE stores its operational data.\n\n"
            "  [bold]SQLITE[/bold]      — zero-config local file (default path, nothing else to ask)\n"
            "  [bold]POSTGRESQL[/bold]  — server host/port/database/user/password, "
            "validated BEFORE anything is saved",
            title="DATABASE (first run)",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )
    while True:
        answer = _ask(ask, PROVIDER_PROMPT_LABEL, default="SQLITE")
        if answer is _PROMPT_CANCELLED:
            return _cancelled_out(reason="cancelled") | {"prompted": True}
        norm = str(answer).strip().lower().replace("-", "").replace("_", "").replace(" ", "")
        if norm in _SQLITE_ANSWERS:
            _persist_sqlite_choice(svc)
            out.update(provider="sqlite", persisted=True, reason="sqlite_selected")
            console.print(
                _success_panel(
                    "Database: SQLite",
                    "Local file (default path) — no further questions.",
                    border="green",
                )
            )
            return out
        if norm in _POSTGRES_ANSWERS:
            out.update(_configure_postgres(svc, ask))
            out["prompted"] = True
            return out
        console.print("[yellow]Please answer SQLITE or POSTGRESQL (blank = SQLITE).[/yellow]")


@app.command("install")
@app.command("setup")
def setup_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """First-run setup wizard (compatibility → repair → mode → health)."""
    flow = _wizard_flow(json_mode=json_mode)
    if json_mode:
        flow["exit_code"] = xc.EXIT_OK
        _emit(flow, True)
        raise typer.Exit(xc.EXIT_OK) from None
    console.print(
        _success_panel(
            "Setup complete",
            f"Mode [bold]{flow['mode']}[/bold]  ·  Symbol [bold cyan]{flow['symbol']}[/bold cyan]\nHealth: [bold]{flow['health_overall']}[/bold]",
            border="green",
        )
    )
    # Dependency inventory (2026-09-02 UX pass): real installed versions,
    # OPTIONAL tier never counted as failure.
    _icon = {"OK": "[green]✓[/green]", "MISSING": "[red]✗[/red]", "BROKEN": "[yellow]⚠[/yellow]"}
    missing_required = [
        p for p in flow.get("packages", []) if p["status"] != "OK" and p["tier"] == "REQUIRED"
    ]
    if flow.get("packages"):
        dep = Table(title="Dependencies", box=box.SIMPLE_HEAD, show_lines=False)
        dep.add_column("", no_wrap=True)
        dep.add_column("Package", style="bold white", no_wrap=True)
        dep.add_column("Version", style="dim")
        dep.add_column("Tier", style="dim", no_wrap=True)
        for p in flow["packages"]:
            dep.add_row(_icon.get(p["status"], "?"), p["package"], p["version"], p["tier"])
        console.print(dep)
        if missing_required:
            console.print(
                _error_panel(
                    "Missing required dependencies",
                    ", ".join(p["package"] for p in missing_required),
                    hint="Run: nexus doctor --fix   (safe, non-destructive)",
                )
            )
    console.print(
        Panel(
            "[bold]NEXT STEPS[/bold]\n"
            "  1. Check system:   [cyan]nexus doctor[/cyan]\n"
            "  2. Start safely:   [cyan]nexus start[/cyan]  (paper mode by default)\n"
            "  3. Check updates:  [cyan]nexus update check[/cyan]",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )
    console.print(
        Panel(
            "[bold]Web Dashboard Endpoints (Port 8080):[/bold]\n"
            + "\n".join(f"  [cyan]> {ep}[/cyan]" for ep in flow["web_endpoints"]),
            border_style="cyan",
            box=box.ROUNDED,
        )
    )
    for ep in flow["web_endpoints"]:
        console.print(f"  [dim]→ {ep}[/dim]")
    raise typer.Exit(xc.EXIT_OK) from None


@app.command("uninstall")
def uninstall_cmd(
    keep_data: bool = typer.Option(
        True, "--keep-data/--remove-data", help="Keep user data on uninstall."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Uninstall helper (data safety: keep-data is the default)."""
    info = _resolve_facade_seam("get_version_info", get_version_info)()
    data_root = rpaths.get_data_root()
    msg = f"Uninstall {info['version']} ({info['channel']})  ·  data in {data_root} will be {'kept' if keep_data else 'removed'}"
    if json_mode:
        _emit(
            {
                "version": info["version"],
                "keep_data": keep_data,
                "data_root": str(data_root),
                "exit_code": xc.EXIT_OK,
            },
            True,
        )
        raise typer.Exit(xc.EXIT_OK) from None
    console.print(_banner(subtitle="uninstall"))
    console.print(Panel(msg, border_style="cyan"))
    if not keep_data:
        ok = typer.confirm(
            f"Delete ALL user data in {data_root} ? This cannot be undone.", default=False
        )
        if not ok:
            console.print("[yellow]Cancelled — data preserved.[/yellow]")
            raise typer.Exit(xc.EXIT_OK) from None
        try:
            shutil.rmtree(data_root)
            console.print("[green]User data removed.[/green]")
        except Exception as e:
            console.print(_error_panel("Could not remove data", str(e)))
            raise typer.Exit(xc.EXIT_RUNTIME) from None
    else:
        console.print(
            "[green]User data preserved — uninstall the app via Windows Settings to finish.[/green]"
        )
    raise typer.Exit(xc.EXIT_OK) from None
