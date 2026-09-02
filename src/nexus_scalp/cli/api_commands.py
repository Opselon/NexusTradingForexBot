"""``nexus api`` CLI group — developer access to the /api/v1 platform.

CONTRACT RULE: consumes the SAME HTTP contracts as external clients through
``nexus_scalp.api_client.NexusApiClient`` — no business logic duplicated here.
Default base URL is the running dashboard (http://127.0.0.1:8080); override via
``--base-url`` or the ``NEXUS_API_BASE`` environment variable.
"""

from __future__ import annotations

import json
import os
from typing import Any

import typer

from nexus_scalp.api_client import DEFAULT_BASE_URL, NexusApiClient, NexusApiError

api_app = typer.Typer(
    help="Query the Nexus API platform (/api/v1) of a running engine.",
    add_completion=False,
    no_args_is_help=True,
)


def _client(base_url: str | None) -> NexusApiClient:
    resolved = base_url or os.environ.get("NEXUS_API_BASE") or DEFAULT_BASE_URL
    return NexusApiClient(resolved)


def _emit(envelope: dict[str, Any]) -> None:
    typer.echo(json.dumps(envelope, indent=2, default=str))


def _run(fn: Any, base_url: str | None) -> None:
    try:
        _emit(fn())
    except NexusApiError as exc:
        typer.secho(
            f"error [{exc.code}] {exc.message} (request_id={exc.request_id})",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2) from exc


def _meta_base(envelope: dict[str, Any]) -> str:
    meta = envelope.get("meta", {})
    return str(meta.get("request_id", "-"))


@api_app.command("status")
def api_status(
    base_url: str = typer.Option(None, "--base-url", envvar="NEXUS_API_BASE", help="API base URL."),
) -> None:
    """High-level operational status (health + version + mode + freshness)."""
    c = _client(base_url)
    try:
        envelope = c.system_status()
    except NexusApiError as exc:
        typer.secho(f"error [{exc.code}] {exc.message}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    data = envelope.get("data", {})
    typer.echo(f"request_id: {_meta_base(envelope)}")
    typer.echo(f"health    : {data.get('health_verdict')}")
    version = data.get("version") or {}
    typer.echo(f"version   : {version.get('version')} ({version.get('commit')})")
    runtime = data.get("runtime") or {}
    typer.echo(f"engine    : running={runtime.get('engine_running')} mode={runtime.get('mode')}")
    typer.echo(f"freshness : {runtime.get('freshness_overall')}")


@api_app.command("health")
def api_health(
    base_url: str = typer.Option(None, "--base-url", envvar="NEXUS_API_BASE"),
) -> None:
    """Structured health state with per-layer checks."""
    _run(_client(base_url).system_health, base_url)


@api_app.command("version")
def api_version(
    base_url: str = typer.Option(None, "--base-url", envvar="NEXUS_API_BASE"),
) -> None:
    """Version/build/revision identity."""
    _run(_client(base_url).system_version, base_url)


@api_app.command("capabilities")
def api_capabilities(
    base_url: str = typer.Option(None, "--base-url", envvar="NEXUS_API_BASE"),
) -> None:
    """Discover the v1 surface: domains, endpoint counts, pagination model."""
    _run(_client(base_url).capabilities, base_url)


@api_app.command("mode")
def api_mode(
    base_url: str = typer.Option(None, "--base-url", envvar="NEXUS_API_BASE"),
) -> None:
    """Configured/effective execution mode."""
    _run(_client(base_url).runtime_mode, base_url)


@api_app.command("signals")
def api_signals(
    base_url: str = typer.Option(None, "--base-url", envvar="NEXUS_API_BASE"),
) -> None:
    """Latest signal from the audit ledger."""
    _run(_client(base_url).signals_latest, base_url)


@api_app.command("decisions")
def api_decisions(
    decision_id: str = typer.Option(None, "--id", help="Detail/gates for one decision id."),
    gates: bool = typer.Option(False, "--gates", help="Show gate trace for --id."),
    latest: bool = typer.Option(False, "--latest", help="Latest decision summary."),
    page_size: int = typer.Option(20, "--page-size", min=1, max=200),
    base_url: str = typer.Option(None, "--base-url", envvar="NEXUS_API_BASE"),
) -> None:
    """Decision history: latest, list, or one decision (with gates)."""
    c = _client(base_url)
    try:
        if decision_id:
            _emit(c.decision_gates(decision_id) if gates else c.decision_detail(decision_id))
        elif latest:
            _emit(c.decisions_latest())
        else:
            _emit(c.decisions(page_size=page_size))
    except NexusApiError as exc:
        typer.secho(
            f"error [{exc.code}] {exc.message} (request_id={exc.request_id})",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2) from exc


@api_app.command("model")
def api_model(
    base_url: str = typer.Option(None, "--base-url", envvar="NEXUS_API_BASE"),
) -> None:
    """Serving model identity + state."""
    _run(_client(base_url).model_identity, base_url)


@api_app.command("features")
def api_features(
    base_url: str = typer.Option(None, "--base-url", envvar="NEXUS_API_BASE"),
) -> None:
    """Active feature contract (canonical SSoT registry)."""
    _run(_client(base_url).features_contract, base_url)


@api_app.command("diagnostics")
def api_diagnostics(
    run: bool = typer.Option(False, "--run", help="Run the bounded observability selftest first."),
    base_url: str = typer.Option(None, "--base-url", envvar="NEXUS_API_BASE"),
) -> None:
    """Latest diagnostics (optionally running the selftest)."""
    c = _client(base_url)
    try:
        if run:
            result = c.run_diagnostics()
            verdict = result.get("data", {})
            typer.echo(f"selftest: {'PASS' if verdict.get('ok', verdict) else verdict}")
        _emit(c.get("/api/v1/system/diagnostics"))
    except NexusApiError as exc:
        typer.secho(
            f"error [{exc.code}] {exc.message} (request_id={exc.request_id})",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2) from exc


@api_app.command("get")
def api_get(
    path: str = typer.Argument(..., help="v1 path, e.g. /api/v1/incidents or incidents"),
    page: int = typer.Option(None, "--page", min=1),
    page_size: int = typer.Option(None, "--page-size", min=1, max=200),
    base_url: str = typer.Option(None, "--base-url", envvar="NEXUS_API_BASE"),
) -> None:
    """GET any v1 path and print the full envelope."""
    params: dict[str, Any] = {}
    if page is not None:
        params["page"] = page
    if page_size is not None:
        params["page_size"] = page_size
    c = _client(base_url)
    try:
        _emit(c.get(path, params=params or None))
    except NexusApiError as exc:
        typer.secho(
            f"error [{exc.code}] {exc.message} (request_id={exc.request_id})",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2) from exc


@api_app.command("smoke")
def api_smoke(
    base_url: str = typer.Option(None, "--base-url", envvar="NEXUS_API_BASE"),
) -> None:
    """Domain-by-domain smoke over a RUNNING API; exits non-zero on failure."""
    c = _client(base_url)
    domains: list[tuple[str, Any]] = [
        ("system", c.system_status),
        ("runtime", c.runtime_mode),
        ("signals", c.signals_latest),
        ("decisions", c.decisions_latest),
        ("model", c.model_status),
        ("features", c.features_contract),
        ("research", c.research_status),
        ("shadow", c.shadow_status),
        ("incidents", lambda: c.incidents(page_size=5)),
        ("database", c.database_status),
        ("observability", c.observability_metrics),
        ("capabilities", c.capabilities),
    ]
    failures: list[str] = []
    for name, fn in domains:
        try:
            envelope = fn()
            data = envelope.get("data") if isinstance(envelope, dict) else None
            ok = data is not None or isinstance(envelope, dict)
            typer.secho(
                f"  {'✓' if ok else '!'} {name}",
                fg=typer.colors.GREEN if ok else typer.colors.YELLOW,
            )
            if not ok:
                failures.append(name)
        except NexusApiError as exc:
            if exc.code in {"ENGINE_UNAVAILABLE", "DEPENDENCY_UNAVAILABLE", "RESOURCE_NOT_FOUND"}:
                typer.secho(
                    f"  ○ {name} ({exc.code} — truthful unavailability)", fg=typer.colors.YELLOW
                )
            else:
                typer.secho(f"  ✗ {name}: [{exc.code}] {exc.message}", fg=typer.colors.RED)
                failures.append(name)
        except Exception as exc:
            typer.secho(f"  ✗ {name}: {type(exc).__name__}: {exc}", fg=typer.colors.RED)
            failures.append(name)
    if failures:
        typer.secho(f"API_SMOKE = FAIL ({', '.join(failures)})", fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.secho("API_SMOKE = PASS", fg=typer.colors.GREEN)
