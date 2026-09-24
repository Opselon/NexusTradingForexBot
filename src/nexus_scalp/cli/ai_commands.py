"""CLI for the AI provider ecosystem (ECOSYSTEM-001, Section 29).

    nexus ai provider list
    nexus ai provider status <id>
    nexus ai provider test <id>
    nexus ai provider health <id>
    nexus ai provider configure <id> --endpoint ... --model ... --timeout 20
    nexus ai provider enable <id>
    nexus ai provider disable <id>
    nexus ai provider set-primary <id>
    nexus ai provider set-secondary <id>
    nexus ai provider set-fallback <id>
    nexus ai provider switch <id> --mode HYBRID
    nexus ai model list <id>
    nexus ai model test <id> <model>
    nexus ai position evaluate [--simulated]
    nexus ai position compare [--simulated]
    nexus ai position shadow <id>
    nexus ai decision inspect <decision_id>
    nexus ai decision trace <decision_id>

CLI and UI operate on the SAME backend contracts (Section 61): every command
here calls the same ProviderOrchestrator the API routes install, so there is one
source of truth and no duplicated selection logic.
"""

from __future__ import annotations

import json
from typing import Any

import typer

from nexus_scalp.ai_providers.orchestrator import ProviderOrchestrator
from nexus_scalp.ai_providers.registry import ActivationState
from nexus_scalp.ai_providers.sample import SIMULATED_SAMPLE_REQUEST
from nexus_scalp.settings.secret_store import SecureSecretStore

ai_app = typer.Typer(name="ai", help="AI provider ecosystem: providers, models, decisions.")
provider_app = typer.Typer(name="provider", help="Provider management.")
model_app = typer.Typer(name="model", help="Model management.")
position_app = typer.Typer(name="position", help="Position evaluation.")
decision_app = typer.Typer(name="decision", help="Decision trace and inspection.")

ai_app.add_typer(provider_app, name="provider")
ai_app.add_typer(model_app, name="model")
ai_app.add_typer(position_app, name="position")
ai_app.add_typer(decision_app, name="decision")


def _orchestrator() -> ProviderOrchestrator:
    """The shared backend instance (same contract the API installs)."""
    from nexus_scalp.web.ai_providers_routes import get_ai_provider_orchestrator

    return get_ai_provider_orchestrator()


def _emit(data: Any, json_out: bool) -> None:
    if json_out:
        typer.echo(json.dumps(data, indent=2, default=str))
    else:
        typer.echo(_pretty(data))


def _pretty(data: Any, depth: int = 0) -> str:
    """Compact human-readable rendering without rich tables (keeps output stable
    for --json parity tests)."""
    pad = "  " * depth
    if isinstance(data, dict):
        lines = []
        for k, v in data.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:")
                lines.append(_pretty(v, depth + 1))
            else:
                lines.append(f"{pad}{k}: {v}")
        return "\n".join(lines)
    if isinstance(data, list):
        if not data:
            return f"{pad}(none)"
        return "\n".join(_pretty(v, depth) for v in data)
    return f"{pad}{data}"


# ---------------------------------------------------------------------------
# provider
# ---------------------------------------------------------------------------


@provider_app.command("list")
def provider_list(json_out: bool = typer.Option(False, "--json", help="Machine-readable output.")):
    """List all providers with health and activation (Section 19)."""
    orch = _orchestrator()
    state = orch.to_public_dict()
    rows = []
    for p in orch.list_providers():
        health = p.get("health") or {}
        rows.append(
            {
                "provider": p.get("provider_id"),
                "name": p.get("provider_name"),
                "enabled": p.get("enabled"),
                "model": p.get("model"),
                "health": health.get("circuit_breaker_state", "n/a") if health else "not built",
                "latency_ms": health.get("latency_ms") if health else None,
                "has_secret": p.get("has_secret"),
            }
        )
    _emit(
        {
            "providers": rows,
            "active": state.get("active_provider"),
            "mode": state.get("decision_mode"),
        },
        json_out,
    )


@provider_app.command("status")
def provider_status(
    provider_id: str = typer.Argument(..., help="Provider id."),
    json_out: bool = typer.Option(False, "--json"),
):
    """One provider's status and health (Section 31)."""
    _emit(_orchestrator().provider_status(provider_id), json_out)


@provider_app.command("test")
def provider_test(
    provider_id: str = typer.Argument(...),
    json_out: bool = typer.Option(False, "--json"),
):
    """Run the test suite for a provider (Section 22). Never trades."""
    _emit(_orchestrator().test_provider(provider_id).__dict__, json_out)


@provider_app.command("health")
def provider_health(
    provider_id: str = typer.Argument(...),
    json_out: bool = typer.Option(False, "--json"),
):
    """Health probe for a provider (Sections 31, 65)."""
    _emit(_orchestrator().provider_status(provider_id), json_out)


@provider_app.command("configure")
def provider_configure(
    provider_id: str = typer.Argument(...),
    endpoint: str = typer.Option(None, "--endpoint"),
    model: str = typer.Option(None, "--model"),
    timeout: float = typer.Option(None, "--timeout"),
    retries: int = typer.Option(None, "--max-retries"),
    name: str = typer.Option(None, "--name"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Configure a provider (Section 20). Secrets are set via the UI or the
    secure store, never passed on the command line (Section 37)."""
    values: dict[str, Any] = {}
    if endpoint is not None:
        values["endpoint"] = endpoint
    if model is not None:
        values["model"] = model
    if timeout is not None:
        values["timeout"] = timeout
    if retries is not None:
        values["max_retries"] = retries
    if name is not None:
        values["provider_name"] = name
    if not values:
        raise typer.BadParameter("no configuration values supplied")
    ok = _orchestrator().configure_provider(provider_id, values, actor="cli")
    _emit(
        {
            "configured": ok,
            "provider_id": provider_id,
            "restart": ProviderOrchestrator.restart_requirement("model"),
        },
        json_out,
    )


@provider_app.command("enable")
def provider_enable(
    provider_id: str = typer.Argument(...), json_out: bool = typer.Option(False, "--json")
):
    _emit(
        {
            "enabled": _orchestrator().configure_provider(
                provider_id, {"enabled": True}, actor="cli"
            )
        },
        json_out,
    )


@provider_app.command("disable")
def provider_disable(
    provider_id: str = typer.Argument(...), json_out: bool = typer.Option(False, "--json")
):
    _emit(
        {
            "disabled": _orchestrator().configure_provider(
                provider_id, {"enabled": False}, actor="cli"
            )
        },
        json_out,
    )


def _set_role(provider_id: str, role: str, json_out: bool) -> None:
    orch = _orchestrator()
    cur = orch.to_public_dict()
    state = ActivationState(
        primary_provider=provider_id
        if role == "primary"
        else cur.get("active_provider") or "internal_nse_ml",
        secondary_provider=provider_id if role == "secondary" else cur.get("secondary_provider"),
        fallback_provider=provider_id if role == "fallback" else cur.get("fallback_provider"),
        decision_mode=cur.get("decision_mode", "INTERNAL_ONLY"),
        shadow_provider=cur.get("shadow_provider"),
    )
    _emit(
        {"set": role, "ok": orch.set_activation(state, actor="cli"), **state.to_public_dict()},
        json_out,
    )


@provider_app.command("set-primary")
def provider_set_primary(
    provider_id: str = typer.Argument(...), json_out: bool = typer.Option(False, "--json")
):
    _set_role(provider_id, "primary", json_out)


@provider_app.command("set-secondary")
def provider_set_secondary(
    provider_id: str = typer.Argument(...), json_out: bool = typer.Option(False, "--json")
):
    _set_role(provider_id, "secondary", json_out)


@provider_app.command("set-fallback")
def provider_set_fallback(
    provider_id: str = typer.Argument(...), json_out: bool = typer.Option(False, "--json")
):
    _set_role(provider_id, "fallback", json_out)


@provider_app.command("switch")
def provider_switch(
    provider_id: str = typer.Argument(...),
    mode: str = typer.Option("HYBRID", "--mode"),
    secondary: str = typer.Option(None, "--secondary"),
    fallback: str = typer.Option(None, "--fallback"),
    json_out: bool = typer.Option(False, "--json"),
):
    """The explicit switch flow (Section 26): preconditions enforced first."""
    _emit(
        _orchestrator().switch(
            primary=provider_id, secondary=secondary, fallback=fallback, mode=mode, actor="cli"
        ),
        json_out,
    )


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------


@model_app.command("list")
def model_list(
    provider_id: str = typer.Argument(...), json_out: bool = typer.Option(False, "--json")
):
    """Models a provider offers (Section 21)."""
    _emit(
        {"provider_id": provider_id, "models": _orchestrator().list_models(provider_id)}, json_out
    )


@model_app.command("test")
def model_test(
    provider_id: str = typer.Argument(...),
    model: str = typer.Argument(...),
    json_out: bool = typer.Option(False, "--json"),
):
    """Test one model against the canonical response contract (Section 22)."""
    orch = _orchestrator()
    orch.configure_provider(provider_id, {"model": model}, actor="cli")
    result = orch.test_provider(provider_id)
    _emit(result.__dict__, json_out)


# ---------------------------------------------------------------------------
# position
# ---------------------------------------------------------------------------


@position_app.command("evaluate")
def position_evaluate(
    json_out: bool = typer.Option(False, "--json"),
    simulated: bool = typer.Option(True, "--simulated/--no-simulated"),
):
    """Evaluate a position snapshot (Section 22). Uses SIMULATED TEST DATA by
    default, so this can never place a real trade (Section 56)."""
    outcome = _orchestrator().evaluate_position(SIMULATED_SAMPLE_REQUEST)
    _emit(outcome.to_dict(), json_out)


@position_app.command("compare")
def position_compare(
    json_out: bool = typer.Option(False, "--json"),
    simulated: bool = typer.Option(True, "--simulated/--no-simulated"),
):
    """Side-by-side provider comparison (Section 24)."""
    from nexus_scalp.web.ai_providers_routes import CompareRequest, route_compare

    req = CompareRequest(
        snapshot=SIMULATED_SAMPLE_REQUEST if simulated else None, simulated=simulated
    )
    _emit(route_compare(req), json_out)


@position_app.command("shadow")
def position_shadow(
    provider_id: str = typer.Argument(...),
    json_out: bool = typer.Option(False, "--json"),
):
    """Set a shadow provider: it evaluates but cannot influence execution
    (Section 25)."""
    orch = _orchestrator()
    cur = orch.to_public_dict()
    state = ActivationState(
        primary_provider=cur.get("active_provider") or "internal_nse_ml",
        secondary_provider=cur.get("secondary_provider"),
        fallback_provider=cur.get("fallback_provider"),
        decision_mode="SHADOW",
        shadow_provider=provider_id,
    )
    _emit({"shadow": provider_id, "ok": orch.set_activation(state, actor="cli")}, json_out)


# ---------------------------------------------------------------------------
# decision
# ---------------------------------------------------------------------------


@decision_app.command("inspect")
def decision_inspect(
    decision_id: str = typer.Argument(...),
    json_out: bool = typer.Option(False, "--json"),
):
    """Inspect a decision (Section 41). Prefers the durable ledger."""
    store = _decision_store()
    if store is not None:
        try:
            row = store.get(decision_id)
            if row is not None:
                _emit(row, json_out)
                return
        except Exception as exc:  # noqa: BLE001 - degrade to the in-memory ring
            typer.echo(f"warning: durable read failed: {exc}", err=True)
    for d in _orchestrator().history(200):
        if d.get("decision_id") == decision_id:
            _emit(d, json_out)
            return
    typer.echo(f"decision {decision_id} not found", err=True)
    raise typer.Exit(1)


@decision_app.command("list")
def decision_list(
    limit: int = typer.Option(20, "--limit", help="How many recent decisions."),
    json_out: bool = typer.Option(False, "--json"),
):
    """Recent decisions (durable ledger when configured)."""
    store = _decision_store()
    if store is not None:
        try:
            rows = store.list_recent(limit=limit)
            if rows:
                _emit({"decisions": rows, "source": "durable"}, json_out)
                return
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"warning: durable read failed: {exc}", err=True)
    _emit(
        {"decisions": _orchestrator().history(limit), "source": "in_memory"},
        json_out,
    )


def _decision_store() -> Any:
    """Durable decision store, or ``None`` when persistence is off.

    Isolated by ``NEXUS_DECISIONS_DB`` for tests, same convention as the
    settings DB. Never raises: a bad path degrades to in-memory history.
    """
    from nexus_scalp.ai_providers.store import ProviderDecisionStore
    from nexus_scalp.settings.paths import decisions_db_path

    try:
        return ProviderDecisionStore(db_path=decisions_db_path())
    except Exception as exc:  # noqa: BLE001 - CLI must never die on a bad DB path
        typer.echo(f"warning: decision store unavailable: {exc}", err=True)
        return None


@decision_app.command("trace")
def decision_trace(
    decision_id: str = typer.Argument(...),
    json_out: bool = typer.Option(False, "--json"),
):
    """Full decision trace: provider, model, evidence, policy, risk, outcome."""
    decision_inspect(decision_id=decision_id, json_out=json_out)


# ---------------------------------------------------------------------------
# backtest
# ---------------------------------------------------------------------------


backtest_app = typer.Typer(
    name="backtest", help="Replay MT5 history through providers (no orders)."
)
ai_app.add_typer(backtest_app, name="backtest")


def _replay_engine(provider: str, mode: str) -> Any:
    from nexus_scalp.ai_providers.mt5_mcp import SECRET_NAME_MT5_KEY, MT5MCPClient
    from nexus_scalp.ai_providers.replay import ReplayEngine

    orch = _orchestrator()
    store = SecureSecretStore()
    key = None
    try:
        key = store.get_secret(SECRET_NAME_MT5_KEY)
    except Exception:
        key = None
    client = MT5MCPClient(api_key=key)
    return ReplayEngine(orchestrator=orch, client=client)


@backtest_app.command("health")
def backtest_health(json_out: bool = typer.Option(False, "--json")):
    """Is the MT5 MCP terminal reachable (Sections 31, 71)? Read-only."""
    from nexus_scalp.ai_providers.replay import BacktestConfig

    _emit(_replay_engine("internal_nse_ml", "INTERNAL_ONLY").health(BacktestConfig()), json_out)


@backtest_app.command("run")
def backtest_run(
    provider: str = typer.Option(
        "internal_nse_ml", "--provider", help="Provider to replay through."
    ),
    symbol: str = typer.Option("XAUUSD", "--symbol"),
    period: str = typer.Option("M15", "--period"),
    datetime_from: str = typer.Option(..., "--from", help="ISO datetime, inclusive."),
    datetime_to: str = typer.Option(..., "--to", help="ISO datetime, exclusive."),
    steps: int = typer.Option(8, "--steps", help="Decision points to evaluate."),
    bars_per_step: int = typer.Option(200, "--bars"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Replay historical bars through a provider and score the outcome.

    READ-ONLY: never places, modifies or closes an order (Section 71). Every
    record is marked SIMULATED TEST DATA. Future bars are read only AFTER each
    decision, to score it (Section 9).
    """
    from nexus_scalp.ai_providers.replay import BacktestConfig

    engine = _replay_engine(provider, "EXTERNAL_ONLY")
    cfg = BacktestConfig(
        symbol=symbol,
        period=period,
        datetime_from=datetime_from,
        datetime_to=datetime_to,
        provider=provider,
        max_steps=steps,
        bars_per_step=bars_per_step,
    )
    rows, summary = engine.run(cfg)
    out = {
        "summary": summary.to_dict(),
        "steps": [r.to_dict() for r in rows],
        "decision_horizon_bars": 12,
        "is_test_data": True,
    }
    _emit(out, json_out)


def register_ai_commands(app: typer.Typer) -> None:
    """Attach the ``nexus ai`` command tree (called from cli/app_factory.py)."""
    app.add_typer(ai_app, name="ai")
