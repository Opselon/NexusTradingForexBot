"""``nexus risk`` — operator CLI for the persisted runtime safety state.

Runtime-safety mission (P0): the smallest safe internal release surface
consistent with the repo architecture. No HTTP endpoint, no Telegram verb —
a local, authenticated-by-presence operator action on the machine that owns
the audit DB.

Commands:
    nexus risk status    — show the persisted runtime risk state row.
    nexus risk release   — explicitly release a persisted HALT/KILL_SWITCH.
                           Requires --confirm and stamps actor/audit trail.
                           The engine picks the release up on its next boot;
                           a running engine that restored a halt stays idle
                           until restarted after the release (fail safe).

Contract: HALTED / KILL_SWITCH can ONLY be cleared here (or via
AuditRepository.release_runtime_risk_state by an authenticated caller).
Restart / reconnect / reload can never reach this code path.
"""

from __future__ import annotations

import typer
from rich.panel import Panel

from nexus_scalp.cli.app_factory import app
from nexus_scalp.cli.styling import console
from nexus_scalp.release import exit_codes as xc

risk_app = typer.Typer(
    name="risk", help="Runtime safety state: inspect and explicitly release persisted halts."
)

_STATE_STYLES = {
    "RUNNING": "green",
    "HALTED": "red",
    "KILL_SWITCH": "red bold",
}

_STATE_TRUTH = {
    "RUNNING": "Trading permitted (no persisted halt).",
    "HALTED": "PERSISTED SAFETY HALT — trading refused at every boot until explicitly released.",
    "KILL_SWITCH": "PERSISTED KILL SWITCH — trading refused at every boot until explicitly released.",
}


def _load_row() -> dict | None:
    """Reads the persisted row through a read-only AuditRepository handle."""
    repo = _repo_handle()
    try:
        return repo.get_runtime_risk_state()
    finally:
        repo.close()


@risk_app.command("status")
def status_cmd() -> None:
    """Show the persisted runtime risk state (single canonical row)."""
    row = _load_row()
    if row is None:
        console.print(
            Panel(
                "[green]RUNNING (no persisted row — fresh install / never halted)[/green]",
                title="Runtime Risk State",
                border_style="green",
            )
        )
        return
    state = str(row.get("state", "")).upper()
    style = _STATE_STYLES.get(state, "yellow")
    lines = [
        f"[bold]state:[/bold]        {state}",
        f"reason:       {row.get('reason') or 'NOT_RECORDED'}",
        f"source:       {row.get('source') or 'NOT_RECORDED'}",
        f"triggered_at: {row.get('triggered_at') or 'NOT_RECORDED'}",
        f"equity:       {row.get('equity')}  balance: {row.get('balance')}  peak: {row.get('peak_equity')}",
        f"release_required: {bool(row.get('release_required'))}",
        f"released_at:  {row.get('released_at') or '—'}",
        f"release_actor: {row.get('release_actor') or '—'}",
        "",
        _STATE_TRUTH.get(state, "UNKNOWN persisted state — fail closed (trading refused)."),
    ]
    console.print(Panel("\n".join(lines), title="Runtime Risk State", border_style=style))
    if state not in ("RUNNING",):
        raise typer.Exit(xc.EXIT_RUNTIME) from None


def _repo_handle() -> "AuditRepository":
    """Opens an AuditRepository against the engine's audit DB.

    Resolution mirrors the engine exactly:
      1. ``NEXUS_AUDIT_DB`` (operator/engine override — checked FIRST);
      2. the canonical DatabaseConfig (settings DB / artifacts workspace).
    """
    import os

    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    env_db = os.environ.get("NEXUS_AUDIT_DB", "").strip()
    if env_db:
        return AuditRepository(db_url=f"sqlite:///{env_db}")
    from nexus_scalp.database.config import load_database_config

    try:
        return AuditRepository(config=load_database_config("audit"))
    except Exception:
        return AuditRepository()


@risk_app.command("release")
def release_cmd(
    confirm: bool = typer.Option(
        False,
        "--confirm",
        help="Explicit confirmation. REQUIRED. Without it the release is refused.",
    ),
    actor: str = typer.Option(
        "operator-cli", "--actor", help="Who is releasing (stamped into the audit trail)."
    ),
    note: str = typer.Option(
        "", "--note", help="Optional release note appended to the persisted reason."
    ),
) -> None:
    """Explicitly release a persisted HALT/KILL_SWITCH (audited, durable).

    Only an explicit release can re-enable trading after a persisted safety
    event. Restart/reconnect/reload NEVER clears the state — this command does.
    """

    row = _load_row()
    if row is None:
        console.print(_no_halt_panel("No persisted safety state — nothing to release."))
        return
    state = str(row.get("state", "")).upper()
    if state not in ("HALTED", "KILL_SWITCH"):
        console.print(
            _no_halt_panel(f"Persisted state is {state or 'UNKNOWN'} — no halt to release.")
        )
        return
    if not confirm:
        console.print(
            _refusal_panel(
                f"Release REFUSED: {state} is armed. Re-run with --confirm to release "
                "and re-enable trading after an explicit operator review."
            )
        )
        raise typer.Exit(xc.EXIT_USAGE) from None

    repo = _repo_handle()
    try:
        ok = repo.release_runtime_risk_state(actor=actor, note=note)
    finally:
        repo.close()
    if not ok:
        console.print(
            _refusal_panel("Release FAILED — the persisted state is unchanged. Check logs.")
        )
        raise typer.Exit(xc.EXIT_RUNTIME) from None
    console.print(
        Panel(
            f"[green]RELEASED[/green] {state} by {actor}.\n"
            "The persisted state is now RUNNING. Restart the engine to resume trading\n"
            "(a running engine that restored the halt stays idle until restarted).",
            title="Runtime Risk State",
            border_style="green",
        )
    )


def _no_halt_panel(message: str) -> Panel:
    return Panel(f"[yellow]{message}[/yellow]", title="Runtime Risk State", border_style="yellow")


def _refusal_panel(message: str) -> Panel:
    return Panel(f"[red]{message}[/red]", title="Release Refused", border_style="red")


app.add_typer(risk_app)
