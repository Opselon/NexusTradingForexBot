"""First-run model provisioning CLI — setup ceremony + PATH A / PATH B.

BUG-293 redesign (operator directive 2026-09-16): the first-run model story
is TWO explicit paths over ONE shared domain service
(``nexus_scalp.model_provisioning``) — CLI and Web expose the same
operations:

  ``nexus model-setup``             first-setup choice screen (status + paths)
  ``nexus model-official``          PATH A: download + verify + install the
                                    signed official Nexus bundle (no local
                                    training, no PyTorch requirement to
                                    install; verification chain is mandatory)
  ``nexus model-train-local``       PATH B: import the user's OWN broker
                                    export (CSV/Parquet), honest diagnostics,
                                    candle budget selection, canonical purged
                                    walk-forward, progress events, governed
                                    install (starter/empty slots only)
  ``nexus model-provision``         status / dev-starter provisioning
                                    (offline, CI, emergency recovery — the
                                    starter is ALWAYS labeled DEV STARTER)

``train-once`` remains as a compatibility alias of model-train-local (kept
for one release; deprecation surfaced in --help).

Heavy imports (torch/polars/MetaTrader5) stay function-local.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import typer

from nexus_scalp.cli.app_factory import app
from nexus_scalp.cli.styling import (
    _emit,
    _error_panel,
    _success_panel,
    console,
)
from nexus_scalp.model_provisioning import (
    OfficialBundleError,
    OfficialBundleSource,
)
from nexus_scalp.model_provisioning import service as prov
from nexus_scalp.release import exit_codes as xc


# ---------------------------------------------------------------------------
# nexus model-provision (status first; starter = explicit dev tool)
# ---------------------------------------------------------------------------
@app.command("model-provision")
def model_provision_cmd(
    status_only: bool = typer.Option(
        False, "--status", help="Classify the serving slot; never write."
    ),
    starter: bool = typer.Option(
        False,
        "--starter",
        help="Explicitly mint the DEV STARTER (offline/CI/emergency recovery "
        "only). A starter is labeled and never replaces official/user/governed "
        "bundles.",
    ),
    path: str = typer.Option("", "--path", help="Override the serving artifact path."),
    json_mode: bool = typer.Option(False, "--json"),
) -> None:
    """Serving-slot status (origin + state) and explicit dev-starter minting.

    Exit 0 when the slot is servable. The DEV STARTER never masquerades as
    production: provision it only with --starter, and first-setup always
    recommends replacing it."""
    from nexus_scalp.release import bootstrap as rb

    target = _resolve_target(path)
    cls = prov.classify_serving_slot(target)

    if status_only or not starter:
        payload = {"recommended": prov.FirstRunCoordinator().recommended_action(), **cls.as_dict()}
        if json_mode:
            _emit(payload, True)
        else:
            _print_slot(cls)
            rec = payload["recommended"]
            _print_recommendation(rec)
        raise typer.Exit(xc.EXIT_OK if cls.servable else xc.EXIT_RUNTIME)

    try:
        result: dict[str, Any] = rb.mint_starter_bundle(target)
    except rb.BootstrapError as e:
        msg = str(e)
        if json_mode:
            _emit({"error": msg, "path": str(target), "exit_code": xc.EXIT_RUNTIME}, True)
        else:
            console.print(
                _error_panel("Starter provisioning refused", msg, exit_code=xc.EXIT_RUNTIME)
            )
        raise typer.Exit(xc.EXIT_RUNTIME) from None
    if result.get("provisioned"):
        prov.record_install(
            model_path=target,
            origin=prov.ORIGIN_DEV_STARTER,
            model_sha256=rb.sha256_file(target),
            model_version="starter",
            bundle_id="dev-starter",
        )
    payload = {
        "path": str(target),
        **cls.as_dict(),
        "provisioned": result.get("provisioned", False),
        "origin": prov.ORIGIN_DEV_STARTER,
    }
    if json_mode:
        _emit(payload, True)
        raise typer.Exit(xc.EXIT_OK)
    console.print(
        _success_panel(
            "DEV STARTER provisioned (offline/CI/emergency recovery)",
            f"{target}\nNOT a production model — replace via `nexus model-official` "
            "or `nexus model-train-local`.",
        )
    )


# ---------------------------------------------------------------------------
# nexus model-setup — the first-setup ceremony (PATH A / PATH B choice)
# ---------------------------------------------------------------------------
@app.command("model-setup")
def model_setup_cmd(
    json_mode: bool = typer.Option(False, "--json"),
) -> None:
    """First setup: choose how to obtain the serving model.

    Two SEPARATE concerns (operator directive 2026-09-16):
      APPLICATION SETUP — how the engine gets a serving model:
        [1] DOWNLOAD OFFICIAL NEXUS MODEL  (recommended; verified install;
            no local training, no PyTorch needed)
        [3] DEV STARTER                    (offline simulation only — clearly
            labeled, never production)
      OPTIONAL TRAINING SETUP — train your own, on your own data:
        [2] TRAIN MY OWN MODEL             (local only; your data never
            leaves this machine; gated on the training-environment check —
            PyTorch is provisioned ONLY if you explicitly install it here,
            never at application startup)
    """
    coordinator = prov.FirstRunCoordinator()
    cls = coordinator.slot()
    rec = coordinator.recommended_action()
    official = OfficialBundleSource()

    if json_mode:
        _emit(
            {"slot": cls.as_dict(), "recommended": rec, "official_configured": official.configured},
            True,
        )
        raise typer.Exit(xc.EXIT_OK if cls.servable else xc.EXIT_RUNTIME)

    console.print(
        PanelBox(
            "NEXUS FIRST SETUP — model preparation\n\n"
            f"  serving slot: [bold]{cls.state.value}[/bold]"
            + (f" (origin {cls.origin})" if cls.origin else "")
            + f"\n  path: {cls.path}"
            + f"\n  official source configured: {'yes' if official.configured else 'NO (operator has not published one yet)'}"
        )
    )
    console.print(
        PanelBox(
            "  APPLICATION SETUP (no training stack needed)\n"
            "  [bold green][1] DOWNLOAD OFFICIAL MODEL[/bold green] — recommended\n"
            "      signed bundle + SHA256 + schema + integrity verification\n\n"
            "  OPTIONAL TRAINING SETUP (only if YOU choose to train)\n"
            "  [bold cyan][2] TRAIN MY OWN MODEL[/bold cyan] — uses YOUR broker data\n"
            "      CSV/Parquet import -> canonical 70D walk-forward training\n"
            "      [bold]your data never leaves this computer[/bold]; PyTorch is\n"
            "      checked first and installed only with your explicit OK\n\n"
            "  [bold yellow][3] DEV STARTER[/bold yellow] — offline/CI/emergency only\n"
            "      labeled starter, never presented as a production model",
            border="cyan" if not cls.servable else "green",
        )
    )
    if not cls.servable:
        choice = typer.prompt(
            "Choose [1/2/3]",
            default="1" if official.configured else "2" if _training_ready() else "3",
        )
    else:
        choice = typer.prompt("Current model is usable — re-prepare? [1/2/3/skip]", default="skip")
    if choice == "1":
        official_main(json_mode=False)
    elif choice == "2":
        train_local_main(json_mode=False)
    elif choice == "3":
        model_provision_cmd(status_only=False, starter=True, path="", json_mode=False)
    else:
        console.print("[dim]first-setup model step skipped (current state kept)[/dim]")


# ---------------------------------------------------------------------------
# nexus model-official — PATH A
# ---------------------------------------------------------------------------
@app.command("model-official")
def model_official_cmd(
    base_url: str = typer.Option(
        "", "--base-url", help="Official bundle HTTPS base URL (or drive:<fileid> archive)."
    ),
    json_mode: bool = typer.Option(False, "--json"),
) -> None:
    """PATH A — download the official Nexus model and install it VERIFIED.

    Chain: download -> SHA256 per file -> Ed25519 signature (embedded trust
    root) -> schema/dimension/architecture binding -> integrity gates ->
    atomic install -> serving-slot re-verify. An unverified model is never
    installed; failures exit non-zero with the exact step code."""
    official_main(base_url=base_url, json_mode=json_mode)


def official_main(*, base_url: str = "", json_mode: bool = False) -> None:
    source = OfficialBundleSource(base_url or None)
    coordinator = prov.FirstRunCoordinator(official=source)
    try:
        out = coordinator.download_official()
    except OfficialBundleError as e:
        msg = str(e)
        if json_mode:
            _emit({"error": msg, "code": e.code, "exit_code": xc.EXIT_RUNTIME}, True)
        else:
            console.print(
                _error_panel(
                    "Official model verification FAILED",
                    msg,
                    hint="Nothing was installed — the slot is unchanged.",
                    exit_code=xc.EXIT_RUNTIME,
                )
            )
        raise typer.Exit(xc.EXIT_RUNTIME) from None
    except (
        RuntimeError
    ) as e:  # NOT_CONFIGURED surfaces via OfficialBundleError; this is belt+braces
        if json_mode:
            _emit({"error": str(e), "exit_code": xc.EXIT_ENVIRONMENT}, True)
        else:
            console.print(
                _error_panel("Official source unavailable", str(e), exit_code=xc.EXIT_ENVIRONMENT)
            )
        raise typer.Exit(xc.EXIT_ENVIRONMENT) from None
    if json_mode:
        _emit(out, True)
        raise typer.Exit(xc.EXIT_OK if out.get("servable") else xc.EXIT_RUNTIME)
    console.print(
        _success_panel(
            "Official model installed (all verifications passed)",
            f"bundle {out.get('bundle_id', '')}\n{out.get('path', '')}",
        )
    )


# ---------------------------------------------------------------------------
# nexus model-train-local — PATH B
# ---------------------------------------------------------------------------
@app.command("model-train-local")
def model_train_local_cmd(
    input_file: Path | None = typer.Option(
        None,
        "--input",
        "-i",
        help="Your XAUUSD M1 CSV/TXT/Parquet export; required for unattended file source.",
    ),
    source: str = typer.Option(
        "file",
        "--source",
        help="file | broker. Broker reads the logged-in local MT5 terminal; no paper/synthetic fallback.",
    ),
    symbol: str = typer.Option("XAUUSD", "--symbol", help="Only XAUUSD is supported."),
    timeframe: str = typer.Option("M1", "--timeframe", help="Only closed M1 candles are accepted."),
    candles: int = typer.Option(
        0,
        "--candles",
        help="File: 0 = all, or >=3000 most recent candles. Broker: explicit 3000..100000; insufficient history fails.",
    ),
    folds: int = typer.Option(6, "--folds", min=2, help="Purged chronological walk-forward folds."),
    epochs: int = typer.Option(4, "--epochs", min=1, help="Training epochs per fold."),
    backend: str = typer.Option(
        "",
        "--backend",
        help="cpu | cuda; auto uses detected NVIDIA GPU. READY requires exact pins and a real tensor/CUDA test.",
    ),
    prepare_environment: bool = typer.Option(
        False,
        "--prepare-environment",
        help="Consent to prepare the managed environment and download required pinned packages if needed. Required for unattended first setup; interactive use prompts.",
    ),
    install: bool = typer.Option(
        True,
        "--install/--no-install",
        help="Install verified candidate over empty/DEV STARTER only; never replaces a governed champion. --no-install keeps the candidate.",
    ),
    json_mode: bool = typer.Option(
        False,
        "--json",
        help="One final JSON payload on stdout; no prompts. Progress/logs go to stderr.",
    ),
) -> None:
    """Train on YOUR real data; no uploads or silent synthetic history.

    Package preparation requires --prepare-environment or interactive consent.
    The same manager verifies and launches its READY interpreter automatically,
    including from the EXE. No Python found? Install a supported 64-bit Python
    from python.org, then re-check model-train-env. The EXE and Official Download
    need no separate training Python.

    CPU is supported; CUDA needs compatible NVIDIA hardware/driver and the
    pinned CUDA wheel (a system CUDA toolkit is normally unnecessary). Progress
    shows actual candles/features/epochs/loss/val_loss and measured ETA when
    available. Ctrl+C requests cooperative cancellation; wait for the
    provider/worker boundary. Training completion is NOT governed promotion.

    Examples:
      nexus model-train-local --input C:/data/XAUUSD_M1.csv --backend cpu --prepare-environment
      nexus model-train-local --source broker --candles 50000 --backend cuda --prepare-environment
      nexus model-train-local --input bars.parquet --candles 10000 --no-install --prepare-environment --json

    See docs/TRAINING_SETUP.md for formats, blockers and model governance.
    """
    train_local_main(
        input_file=input_file,
        source=source,
        timeframe=timeframe,
        candles=candles,
        folds=folds,
        epochs=epochs,
        install=install,
        json_mode=json_mode,
        symbol=symbol,
        backend=backend,
        prepare_environment=prepare_environment,
    )


def train_local_main(
    *,
    input_file: Path | None = None,
    candles: int = 0,
    folds: int = 6,
    epochs: int = 4,
    install: bool = True,
    json_mode: bool = False,
    symbol: str = "XAUUSD",
    backend: str = "",
    source: str = "file",
    timeframe: str = "M1",
    prepare_environment: bool = False,
) -> None:
    import contextlib
    import json
    import signal
    import sys

    from nexus_scalp.model_provisioning.dataset_source import (
        DatasetSourceError,
        prepare_training_dataset,
        validate_dataset_request,
    )
    from nexus_scalp.model_provisioning.pipeline import (
        TrainingCancelledError,
        TrainingRequest,
        train_local_model,
    )
    from nexus_scalp.model_provisioning.training_env import (
        TrainingEnvironmentError,
        TrainingEnvironmentManager,
    )
    from nexus_scalp.release.paths import get_runtime_workspace

    def fail(error: str, code: int, **details: Any) -> None:
        if json_mode:
            _emit({"error": error, "exit_code": code, **details}, True)
        else:
            console.print(
                _error_panel(
                    "Training not started", error, hint=details.get("remedy", ""), exit_code=code
                )
            )
        raise typer.Exit(code)

    interactive = not json_mode and sys.stdin.isatty()
    gate_backend = backend.strip().lower() or None
    if gate_backend not in (None, "cpu", "cuda"):
        fail("invalid --backend (accepted: cpu | cuda)", xc.EXIT_USAGE)
    if type(folds) is not int or folds < 2 or type(epochs) is not int or epochs < 1:
        fail("folds must be >=2 and epochs >=1", xc.EXIT_USAGE)
    if source == "file" and input_file is None and interactive:
        input_file = Path(
            typer.prompt("Path to your XAUUSD M1 broker export (CSV/TXT/Parquet)")
            .strip()
            .strip('"')
        )
    selected = None if candles == 0 else candles
    try:
        validate_dataset_request(
            source=source,
            source_file=input_file,
            symbol=symbol,
            timeframe=timeframe,
            candles=selected,
        )
    except DatasetSourceError as exc:
        fail(str(exc), xc.EXIT_USAGE)

    cancel = threading.Event()
    previous_handler: Any = None
    handler_installed = False
    started = time.monotonic()

    def callback(event: Any) -> None:
        payload: dict[str, Any] = (
            {"stage": "environment", "status": "progress", "message": event}
            if isinstance(event, str)
            else event
            if isinstance(event, dict)
            else event.as_dict()
        )
        if json_mode:
            print(json.dumps(payload, default=str), file=sys.stderr)
            return
        console.print(
            f"{payload.get('stage', 'environment')}:{payload.get('status', '')} {payload.get('message', '')}"
        )
        metrics: dict[str, Any] = dict(payload.get("metrics") or {})
        if metrics:
            console.print(
                "    "
                + "  ".join(f"{key}={value}" for key, value in metrics.items() if value is not None)
            )

    def interrupt(signum: int, frame: Any) -> None:
        cancel.set()
        print("Cancellation requested; waiting for the active operation boundary.", file=sys.stderr)

    if threading.current_thread() is threading.main_thread():
        previous_handler = signal.signal(signal.SIGINT, interrupt)
        handler_installed = True
    try:
        manager = TrainingEnvironmentManager(workspace=get_runtime_workspace())
        with contextlib.redirect_stdout(sys.stderr) if json_mode else contextlib.nullcontext():
            gate = manager.status(backend=gate_backend)
            if not gate.training_ready and not prepare_environment and interactive:
                console.print(_env_checklist_panel(gate))
                prepare_environment = typer.confirm(
                    "Prepare required training packages now? This downloads the pinned stack into a managed environment",
                    default=False,
                )
            if not gate.training_ready and prepare_environment and not cancel.is_set():
                gate = manager.install(backend=gate_backend, progress=callback)
        if cancel.is_set():
            raise TrainingCancelledError()
        if not gate.training_ready:
            if not json_mode:
                console.print(_env_checklist_panel(gate))
            fail(
                "TRAINING_ENV_BLOCKED",
                xc.EXIT_ENVIRONMENT,
                report=gate.as_dict(),
                remedy="Use --prepare-environment to authorize package preparation, or fix the reported checks and retry. No Python: install supported 64-bit Python from python.org and re-check.",
            )

        # Only a validated file crosses to the managed worker, never an adapter.
        with contextlib.redirect_stdout(sys.stderr) if json_mode else contextlib.nullcontext():
            prepared = prepare_training_dataset(
                source=source,
                source_file=input_file,
                symbol=symbol,
                timeframe=timeframe,
                candles=selected,
                progress=callback,
                cancel_event=cancel,
            )
            request = TrainingRequest(
                source_file=prepared,
                candles=selected,
                folds=folds,
                epochs=epochs,
                install=install,
                cancel_event=cancel,
                backend=gate.backend,
            )
            result = train_local_model(request, progress=callback)
    except TrainingEnvironmentError as exc:
        fail(exc.code.value, xc.EXIT_ENVIRONMENT, detail=exc.detail, remedy=exc.remedy)
    except DatasetSourceError as exc:
        fail(str(exc), xc.EXIT_USAGE if source == "file" else xc.EXIT_ENVIRONMENT)
    except (TrainingCancelledError, KeyboardInterrupt):
        result = {"outcome": "CANCELLED", "reason": "operator cancellation; no new model installed"}
    finally:
        if handler_installed:
            signal.signal(signal.SIGINT, previous_handler)
    if json_mode:
        _emit(result, True)
    else:
        console.print(
            f"outcome: {result.get('outcome')}  elapsed: {time.monotonic() - started:.0f}s"
        )
        console.print(result.get("reason") or result.get("candidate_model") or "")
    ok = result.get("outcome") in ("INSTALLED", "CANDIDATE")
    raise typer.Exit(xc.EXIT_OK if ok else xc.EXIT_RUNTIME)


# ---------------------------------------------------------------------------
# nexus train-once — compatibility alias (deprecated surface)
# ---------------------------------------------------------------------------
@app.command("train-once")
def train_once_cmd(
    symbol: str = typer.Option("XAUUSD", "--symbol"),
    timeframe: str = typer.Option("M1", "--timeframe"),
    bars: int = typer.Option(100_000, "--bars", help="Bars to download from the MT5 terminal."),
    folds: int = typer.Option(6, "--folds"),
    epochs: int = typer.Option(4, "--epochs"),
    install: bool = typer.Option(True, "--install/--no-install"),
    prepare_environment: bool = typer.Option(
        False, "--prepare-environment", help="Consent to prepare required training packages."
    ),
    backend: str = typer.Option("", "--backend", help="cpu | cuda"),
    json_mode: bool = typer.Option(False, "--json"),
) -> None:
    """DEPRECATED alias of model-train-local --source broker (download to a validated CSV, then the SAME local pipeline)."""
    console.print(
        "[yellow]train-once is deprecated — use `nexus model-train-local --source broker`[/yellow]"
    )
    train_local_main(
        input_file=None,
        candles=bars,
        folds=folds,
        epochs=epochs,
        install=install,
        json_mode=json_mode,
        symbol=symbol,
        timeframe=timeframe,
        backend=backend,
        source="broker",
        prepare_environment=prepare_environment,
    )


def data_fetch_into(
    out_path: Path, *, symbol: str, timeframe: str, count: int, json_mode: bool
) -> None:
    """MT5 terminal history -> parquet (reuses the adapter; explicit errors)."""
    import contextlib

    import polars as pl

    from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter
    from nexus_scalp.model_generation.bars_normalize import normalize_bars_frame

    try:
        adapter = DirectMT5Adapter()
        try:
            adapter.connect()
            rate_bars = adapter.get_rate_history(symbol, timeframe=timeframe, count=count)
        finally:
            with contextlib.suppress(Exception):
                adapter.disconnect()
        if not rate_bars:
            msg = "MT5 terminal returned 0 bars"
            raise RuntimeError(msg)
        frame = pl.DataFrame(
            {
                "time": [int(b.time or 0) for b in rate_bars],
                "open": [float(b.open or 0.0) for b in rate_bars],
                "high": [float(b.high or 0.0) for b in rate_bars],
                "low": [float(b.low or 0.0) for b in rate_bars],
                "close": [float(b.close or 0.0) for b in rate_bars],
                "tick_volume": [int(b.tick_volume or 0) for b in rate_bars],
                "spread": [int(b.spread or 0) for b in rate_bars],
                "real_volume": [int(b.real_volume or 0) for b in rate_bars],
                "time_utc": [b.time_utc for b in rate_bars],
            }
        )
        frame, _stats = normalize_bars_frame(frame)
        frame = frame.sort("time")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(out_path)
    except Exception as e:
        msg = f"MT5 history download failed: {e}"
        if json_mode:
            _emit({"error": msg, "exit_code": xc.EXIT_ENVIRONMENT}, True)
        else:
            console.print(
                _error_panel(
                    "MT5 not available",
                    msg,
                    hint="Start and log in to the MetaTrader 5 terminal, or pass --input "
                    "with your own broker export to model-train-local.",
                    exit_code=xc.EXIT_ENVIRONMENT,
                )
            )
        raise typer.Exit(xc.EXIT_ENVIRONMENT) from None


# ---------------------------------------------------------------------------
# presentation helpers
# ---------------------------------------------------------------------------
def _resolve_target(path_override: str) -> Path:
    if path_override:
        from nexus_scalp.release import bootstrap as rb

        return rb.resolve_model_path(path_override)
    return prov.serving_model_path()


def _training_ready() -> bool:
    """OPTIONAL TRAINING SETUP readiness (typed discovery, never installs)."""
    from nexus_scalp.model_provisioning.training_env import TrainingEnvironmentManager

    rep = TrainingEnvironmentManager().status()
    return bool(rep.training_ready and rep.in_process_ready)


def _print_slot(cls: prov.SlotClassification) -> None:
    origin_note = {
        prov.ORIGIN_DEV_STARTER: "[yellow]DEV STARTER — offline/CI/emergency only[/yellow]",
        prov.ORIGIN_OFFICIAL: "[green]official Nexus bundle (verified)[/green]",
        prov.ORIGIN_USER_TRAINED: "[green]your locally trained model[/green]",
        prov.ORIGIN_GOVERNED: "[green]governed champion (promotion lifecycle)[/green]",
        prov.ORIGIN_UNKNOWN: "[yellow]provenance unknown (no install record)[/yellow]",
        "": "[red]empty[/red]",
    }.get(cls.origin, cls.origin or "[red]empty[/red]")
    console.print(f"serving slot: [bold]{cls.state.value}[/bold]  {origin_note}\n  {cls.path}")


def _print_recommendation(rec: dict[str, Any]) -> None:
    action = rec.get("action")
    text = {
        "download_official": "-> recommended: `nexus model-official` (verified official bundle)",
        "train_local": "-> recommended: `nexus model-train-local` (train on your broker export)",
        "starter_offline": "-> no official source and no local PyTorch: `nexus model-provision --starter` (DEV STARTER — labeled, offline only)",
        "upgrade_from_starter": "-> slot runs the DEV STARTER: replace it via `nexus model-setup`",
        "none": "-> serving model is in place",
    }.get(str(action), f"-> action: {action}")
    console.print(text)


def PanelBox(body: str, *, border: str = "cyan") -> Any:
    from rich.panel import Panel

    return Panel(body, border_style=border)


def _env_checklist_panel(report: Any) -> Any:
    """Render the TrainingEnvironmentManager report as the directive's
    checklist: per-stage ✓/✗ + code + remedy (never raw exception text)."""
    lines = []
    for c in report.checks:
        mark = "[green]OK[/green]" if c.ok else "[red]--[/red]"
        line = f"  {mark} {c.stage:<14} {c.detail[:60]}"
        lines.append(line)
        if not c.ok and c.remedy:
            lines.append(f"      [dim]-> {c.remedy[:160]}[/dim]")
    body = "Training environment (checklist):\n" + "\n".join(lines)
    if not report.training_ready:
        body += "\n\n[bold red]Training is BLOCKED until every check passes.[/bold red]"
    elif not report.in_process_ready:
        body += (
            "\n\n[bold yellow]READY in a separate interpreter[/bold yellow] — run:\n"
            f"  [cyan]{report.training_command}[/cyan]"
        )
    border = "green" if (report.training_ready and report.in_process_ready) else "yellow"
    return PanelBox(body, border=border)


# ---------------------------------------------------------------------------
# nexus model-train-env — OPTIONAL TRAINING SETUP provisioning (explicit only)
# ---------------------------------------------------------------------------
@app.command("model-train-env")
def model_train_env_cmd(
    install: bool = typer.Option(
        False,
        "--install",
        help="Provision the training stack now (creates/reuses the training "
        "environment and pip-installs the CANONICAL pinned PyTorch variant). "
        "Without this flag the command only DISCOVERS (checks), never installs.",
    ),
    backend: str = typer.Option(
        "", "--backend", help="cpu | cuda (default: auto = NVIDIA GPU detected ? cuda : cpu)"
    ),
    json_mode: bool = typer.Option(False, "--json"),
) -> None:
    """Training Environment lifecycle (OPTIONAL TRAINING SETUP, BUG-301).

    Application setup never needs this: PAPER inference and PATH A work with
    no training stack. Checks the resolve ladder (python -> environment -> pip
    -> backend -> torch -> version -> smoke) and, ONLY with --install, runs
    the pinned provisioning against configs/training_environment.json — a
    real tensor/GPU allocation smoke test gates READY either way."""
    from nexus_scalp.model_provisioning.training_env import (
        TrainingEnvironmentError,
        TrainingEnvironmentManager,
    )
    from nexus_scalp.release.paths import get_runtime_workspace

    manager = TrainingEnvironmentManager(workspace=get_runtime_workspace())
    try:
        be = backend or None
        if install:
            report = manager.install(backend=be)
        else:
            report = manager.status(backend=be)
    except TrainingEnvironmentError as e:
        payload = {
            "error": e.code.value,
            "detail": e.detail,
            "remedy": e.remedy,
            "exit_code": xc.EXIT_ENVIRONMENT,
        }
        if json_mode:
            _emit(payload, True)
        else:
            console.print(
                _error_panel(
                    f"Training environment: {e.code.value}",
                    e.detail,
                    hint=e.remedy,
                    exit_code=xc.EXIT_ENVIRONMENT,
                )
            )
        raise typer.Exit(xc.EXIT_ENVIRONMENT) from None

    if json_mode:
        _emit(report.as_dict(), True)
    else:
        console.print(_env_checklist_panel(report))
    ok = report.training_ready and (report.in_process_ready or not install)
    raise typer.Exit(xc.EXIT_OK if ok else xc.EXIT_ENVIRONMENT)


__all__ = [
    "model_official_cmd",
    "model_provision_cmd",
    "model_setup_cmd",
    "model_train_env_cmd",
    "model_train_local_cmd",
    "train_once_cmd",
]
