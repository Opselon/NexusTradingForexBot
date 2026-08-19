"""Incident response CLI — `nexus incidents <command>` (TASK-12 spec 34/44/46).

Commands:
    nexus incidents list [--status] [--severity] [--category] [--limit] [--json]
    nexus incidents show <incident_id> [--json]
    nexus incidents search <query>
    nexus incidents stats [--json]
    nexus incidents report <incident_id> [--export-dir]
    nexus incidents export <incident_id> [--zip]
    nexus incidents scan            (read-only forensic baseline over audit.db)
    nexus incidents trace-why <ticket> [--what closed|learning|blocked|strategy|ui]
    nexus incidents lineage <field>

All commands are READ-ONLY except incident record creation from `scan`
(canonical incident rows — never trading data). No recovery is executed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from nexus_scalp.incidents.impact import ImpactAnalyzer, RecoveryPlanner
from nexus_scalp.incidents.models import (
    Incident,
)
from nexus_scalp.incidents.store import IncidentStore
from nexus_scalp.incidents.trace import (
    broker_ledger_divergence,
    clock_skew,
    learning_pipeline_rates,
    outcome_forensics,
    split_fill_groups,
    why_blocked,
    why_closed,
    why_no_learning,
    why_no_strategy,
    why_ui_empty,
)

console = Console()


def _repo_root() -> Path:
    return Path.cwd()


def _store() -> IncidentStore:
    from nexus_scalp.database.engine import db_path_for_domain

    db = db_path_for_domain("audit", _repo_root())
    return IncidentStore(db_path=db)


def _emit(payload: dict[str, Any], json_mode: bool, title: str = "") -> None:
    if json_mode:
        console.print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return
    if title:
        console.print(Panel.fit(title, style="bold cyan"))
    console.print(payload)


def _severity_table(incidents: list[Incident]) -> None:
    table = Table(title=f"{len(incidents)} incident(s)")
    table.add_column("ID", style="cyan")
    table.add_column("Severity", style="magenta")
    table.add_column("Category", style="yellow")
    table.add_column("Status", style="green")
    table.add_column("Component", style="white")
    table.add_column("Detected", style="dim")
    for inc in incidents:
        table.add_row(
            inc.incident_id,
            inc.severity.value,
            inc.category.value,
            inc.status.value,
            inc.component,
            inc.detected_at.isoformat(),
        )
    console.print(table)


def make_incidents_app() -> typer.Typer:
    app = typer.Typer(
        help="Incident response & forensic diagnostics (TASK-12) — read-only by default."
    )

    @app.command("list")
    def incident_list(
        status: str | None = typer.Option(None, "--status", help="status filter"),
        severity: str | None = typer.Option(None, "--severity", help="severity filter"),
        category: str | None = typer.Option(None, "--category", help="category filter"),
        limit: int = typer.Option(50, "--limit", min=1, max=500),
        json_mode: bool = typer.Option(False, "--json"),
    ) -> None:
        store = _store()
        incidents = store.list_incidents(
            status=status, severity=severity, category=category, limit=limit
        )
        if json_mode:
            _emit({"incidents": [i.as_dict() for i in incidents]}, True)
            return
        _severity_table(incidents)

    @app.command("show")
    def incident_show(
        incident_id: str = typer.Argument(...),
        json_mode: bool = typer.Option(False, "--json"),
    ) -> None:
        store = _store()
        inc = store.get(incident_id)
        if inc is None:
            console.print(f"[red]incident {incident_id} not found[/red]")
            raise typer.Exit(1)
        _emit(inc.as_dict(), json_mode, f"INCIDENT {incident_id}")

    @app.command("search")
    def incident_search(
        query: str = typer.Argument(...),
        limit: int = typer.Option(50, "--limit", min=1, max=100),
        json_mode: bool = typer.Option(False, "--json"),
    ) -> None:
        store = _store()
        incidents = store.search(query, limit=limit)
        if json_mode:
            _emit({"incidents": [i.as_dict() for i in incidents]}, True)
            return
        _severity_table(incidents)

    @app.command("stats")
    def incident_stats(json_mode: bool = typer.Option(False, "--json")) -> None:
        store = _store()
        payload = {
            "counts": store.count(),
            "by_component": store.stats_by_component(),
            "recurring": store.recurring_fingerprints(),
        }
        _emit(payload, json_mode, "INCIDENT STATS")

    @app.command("report")
    def incident_report(
        incident_id: str = typer.Argument(...),
        export_dir: str | None = typer.Option(None, "--export-dir"),
    ) -> None:
        from nexus_scalp.incidents.reports import write_incident_reports

        store = _store()
        inc = store.get(incident_id)
        if inc is None:
            console.print(f"[red]incident {incident_id} not found[/red]")
            raise typer.Exit(1)
        base = Path(export_dir) if export_dir else _repo_root() / "artifacts"
        paths = write_incident_reports(inc, base)
        console.print(f"JSON: {paths['json']}")
        console.print(f"Markdown: {paths['markdown']}")

    @app.command("export")
    def incident_export(
        incident_id: str = typer.Argument(...),
        zip_bundle: bool = typer.Option(False, "--zip"),
        export_dir: str | None = typer.Option(None, "--export-dir"),
    ) -> None:
        from nexus_scalp.incidents.reports import export_zip_bundle, write_incident_reports

        store = _store()
        inc = store.get(incident_id)
        if inc is None:
            console.print(f"[red]incident {incident_id} not found[/red]")
            raise typer.Exit(1)
        base = Path(export_dir) if export_dir else _repo_root() / "artifacts"
        paths = write_incident_reports(inc, base)
        console.print(f"JSON: {paths['json']}")
        console.print(f"Markdown: {paths['markdown']}")
        if zip_bundle:
            zip_path = export_zip_bundle(inc, base)
            console.print(f"ZIP: {zip_path}")

    @app.command("scan")
    def incident_scan(
        json_mode: bool = typer.Option(False, "--json"),
        write: bool = typer.Option(
            False, "--write", help="persist new incidents (default: dry-run)"
        ),
    ) -> None:
        """Read-only forensic baseline (spec 56): scans audit.db for known
        historical failure classes and correlates them into incidents."""
        base = _repo_root()
        db = str(base / "artifacts" / "audit.db")
        store = IncidentStore(db_path=db)
        store.ensure_schema()

        from datetime import UTC, datetime

        from nexus_scalp.incidents.correlator import IncidentCorrelator, TelemetryEvent
        from nexus_scalp.incidents.models import EventSource

        corr = IncidentCorrelator()
        events: list[TelemetryEvent] = []
        now = datetime.now(UTC)

        def ev(event_type: str, error_code: str, component: str, payload: dict[str, Any]) -> None:
            events.append(
                TelemetryEvent(
                    timestamp=now,
                    event_type=event_type,
                    component=component,
                    error_code=error_code,
                    payload=payload,
                    source=EventSource.DATABASE,
                )
            )

        # --- historical failure classes (spec 56) ---
        try:
            div = broker_ledger_divergence(db)
            if div["divergence_count"]:
                ev(
                    "ACCOUNTING_DIVERGENCE",
                    "ACCOUNTING_DIVERGENCE",
                    "accounting",
                    {
                        "divergences": div["divergence_count"],
                        "checked": div["checked_broker_trades"],
                    },
                )
            # NOTE: unmapped broker trades are the documented EXPECTED orphan
            # class (pre-BUG-045 migration-era gap, TASK-11 handoff) — NOT an
            # incident. Only mapped-trade PnL divergences are incident-worthy.
            if div["divergence_count"] and div["checked_broker_trades"] > 0:
                # already emitted above as ACCOUNTING_DIVERGENCE
                pass
        except Exception as exc:
            ev("MT5_RECON_SCAN_FAILED", "MT5_CALL_FAILED", "mt5", {"error": str(exc)[:200]})

        try:
            skew = clock_skew(db)
            if skew.get("divergence") == "TIMEBASE_DIVERGENCE":
                ev(
                    "TIMEBASE_DIVERGENCE",
                    "TIMEBASE_DIVERGENCE",
                    "mt5",
                    {"observed_skew_seconds": skew.get("observed_skew_seconds")},
                )
        except Exception as exc:
            ev("CLOCK_SCAN_FAILED", "MT5_CALL_FAILED", "mt5", {"error": str(exc)[:200]})

        try:
            of = outcome_forensics(db)
            if of["suspect_outcomes"]:
                ev(
                    "OUTCOME_SUSPECT",
                    "OUTCOME_SUSPECT",
                    "ledger",
                    {"suspect": len(of["suspect_outcomes"]), "zero": of["zero_realized_outcomes"]},
                )
        except Exception as exc:
            ev("OUTCOME_SCAN_FAILED", "OUTCOME_DISCARDED", "learning", {"error": str(exc)[:200]})

        try:
            lpr = learning_pipeline_rates(db)
            for flag in lpr["flags"]:
                ev(
                    flag,
                    flag,
                    "learning",
                    {
                        k: lpr[k]
                        for k in ("experiences", "outcomes", "research_samples", "candidates")
                    },
                )
        except Exception as exc:
            ev("LEARNING_SCAN_FAILED", "LEARNING_DATA_LOSS", "learning", {"error": str(exc)[:200]})

        try:
            sfg = split_fill_groups(db)
            if sfg["split_fill_families"]:
                ev(
                    "SPLIT_FILL_GROUPING",
                    "SPLIT_FILL_GROUPING",
                    "execution",
                    {"families": sfg["split_fill_families"], "tickets": sfg["tickets_in_families"]},
                )
        except Exception as exc:
            ev(
                "SPLIT_FILL_SCAN_FAILED",
                "CONTEXT_PROPAGATION_FAILURE",
                "execution",
                {"error": str(exc)[:200]},
            )

        result = corr.correlate(events)
        impact = ImpactAnalyzer(db_path=db)
        planner = RecoveryPlanner()
        created = 0
        for inc in result.incidents:
            inc.impact = impact.analyze(inc)
            inc.recovery_plan = planner.generate(inc)
            if write:
                store.save(inc)
                created += 1
        payload = {
            "scan": "read-only forensic baseline (spec 56)",
            "events": len(events),
            "new_incidents": result.new,
            "incidents": [i.as_dict() for i in result.incidents],
            "persisted": created if write else 0,
            "note": "no trading/system mutation performed",
        }
        _emit(payload, json_mode, "INCIDENT SCAN")

    @app.command("trace-why")
    def incident_trace_why(
        ticket: str = typer.Argument(..., help="ticket / execution / request id"),
        what: str = typer.Option("closed", "--what", help="closed|learning|blocked|strategy|ui"),
        field: str = typer.Option("strategies", "--field", help="ui field name (for --what ui)"),
    ) -> None:
        base = _repo_root()
        db = str(base / "artifacts" / "audit.db")
        if what == "closed":
            payload = why_closed(db, ticket)
        elif what == "learning":
            payload = why_no_learning(db, ticket)
        elif what == "blocked":
            payload = why_blocked(db, ticket)
        elif what == "strategy":
            payload = why_no_strategy(db)
        elif what == "ui":
            payload = why_ui_empty(db, field)
        else:
            console.print("[red]unknown --what[/red]")
            raise typer.Exit(1)
        _emit(payload, False, f"WHY {what.upper()}")

    @app.command("lineage")
    def incident_lineage(
        field: str = typer.Argument(
            ..., help="pnl|realized_r|open_positions|model_output|feature_vector|ui:<field>"
        ),
    ) -> None:
        from nexus_scalp.incidents.lineage import LineageEngine

        engine = LineageEngine()
        if field == "pnl":
            trace = engine.pnl_trace()
        elif field == "realized_r":
            trace = engine.realized_r_trace()
        elif field == "open_positions":
            trace = engine.exposure_trace()
        elif field == "model_output":
            trace = engine.model_output_trace()
        elif field.startswith("ui:"):
            trace = engine.ui_value_trace(field[3:])
        else:
            trace = engine.trace(field)
        _emit(
            {"field": trace.field, "source": trace.source, "hops": trace.hops()},
            False,
            f"LINEAGE: {field}",
        )

    return app


incidents_app = make_incidents_app()

__all__ = ["incidents_app", "make_incidents_app"]
