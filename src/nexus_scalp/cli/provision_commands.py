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

    Shows the honest current state, then offers:
      [1] DOWNLOAD OFFICIAL NEXUS MODEL  (recommended; verified install;
          no local training needed)
      [2] TRAIN MY OWN MODEL             (your broker export, local only;
          data never leaves this machine)
      [3] DEV STARTER                    (offline simulation only — clearly
          labeled, never production)
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
            "  [bold green][1] DOWNLOAD OFFICIAL MODEL[/bold green] — recommended\n"
            "      signed bundle + SHA256 + schema + integrity verification\n"
            "      no Python/PyTorch training required on this machine\n\n"
            "  [bold cyan][2] TRAIN MY OWN MODEL[/bold cyan] — uses YOUR broker data\n"
            "      CSV/Parquet import -> canonical 70D walk-forward training\n"
            "      requires PyTorch locally · [bold]your data never leaves this computer[/bold]\n\n"
            "  [bold yellow][3] DEV STARTER[/bold yellow] — offline/CI/emergency only\n"
            "      labeled starter, never presented as a production model",
            border="cyan" if not cls.servable else "green",
        )
    )
    if not cls.servable:
        choice = typer.prompt(
            "Choose [1/2/3]", default="1" if official.configured else "2" if _has_torch() else "3"
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
    input_file: Path = typer.Option(
        Path(""),
        "--input",
        "-i",
        help="Broker export to train on: CSV or Parquet (OHLCV, MT5 columns accepted).",
    ),
    symbol: str = typer.Option("XAUUSD", "--symbol", help="Symbol label (recorded in provenance)."),
    candles: int = typer.Option(
        0,
        "--candles",
        help="Candles to use (0 = all detected). Chronological TAIL — most "
        "recent N bars; presets offered: 1,000 / 10,000 / 50,000 / all.",
    ),
    folds: int = typer.Option(6, "--folds"),
    epochs: int = typer.Option(4, "--epochs"),
    install: bool = typer.Option(
        True,
        "--install/--no-install",
        help="Install into the serving slot when it is empty or starter-only "
        "(a governed/official bundle is never displaced — use promotion).",
    ),
    json_mode: bool = typer.Option(False, "--json"),
) -> None:
    """PATH B — train a model locally from YOUR broker export.

    Your data is read and processed LOCALLY only — no uploads, no telemetry
    with market data, no cloud training. Progress and metrics are the
    engine's real measurements (never synthesized). The trained bundle is
    installed as a USER-TRAINED serving model only over an empty/starter
    slot; otherwise it remains a local candidate for governed promotion."""
    train_local_main(
        input_file=input_file,
        candles=candles,
        folds=folds,
        epochs=epochs,
        install=install,
        json_mode=json_mode,
        symbol=symbol,
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
) -> None:
    from nexus_scalp.model_provisioning.pipeline import detect_ml_environment, train_local_model

    env = detect_ml_environment()
    if not env.get("torch"):
        guidance = (
            "PyTorch is required for local training. Install the release "
            "extras (pip install torch) or choose PATH A (official download) "
            "instead — it needs no training stack."
        )
        if json_mode:
            _emit({"error": guidance, "environment": env, "exit_code": xc.EXIT_ENVIRONMENT}, True)
        else:
            console.print(
                _error_panel(
                    "Training environment missing", guidance, exit_code=xc.EXIT_ENVIRONMENT
                )
            )
        raise typer.Exit(xc.EXIT_ENVIRONMENT) from None

    if input_file is None or not Path(input_file).exists():
        try:
            raw = typer.prompt("Path to your broker export (CSV/Parquet)")
        except (typer.Abort, EOFError):
            raise typer.Exit(xc.EXIT_USAGE) from None
        input_file = Path(raw.strip().strip('"'))
    if not Path(input_file).exists():
        msg = f"input file not found: {input_file}"
        if json_mode:
            _emit({"error": msg, "exit_code": xc.EXIT_USAGE}, True)
        else:
            console.print(_error_panel("Input missing", msg, exit_code=xc.EXIT_USAGE))
        raise typer.Exit(xc.EXIT_USAGE) from None

    candles_sel: int | None = candles or None
    if not json_mode and candles == 0:
        console.print(
            PanelBox(
                "How many candles should training use?\n"
                "  [1] 1,000   (quick smoke — evidence only)\n"
                "  [2] 10,000  (~1 week of M1)\n"
                "  [3] 50,000  (~5 weeks of M1)\n"
                "  [4] ALL detected\n"
                "Selection takes the MOST RECENT N bars (chronological tail — "
                "time-series never take random rows)."
            )
        )
        pick = typer.prompt("Choice [1/2/3/4]", default="3")
        candles_sel = {"1": 1_000, "2": 10_000, "3": 50_000}.get(pick.strip(), None)

    from nexus_scalp.model_provisioning import TrainingRequest

    cancel = threading.Event()
    request = TrainingRequest(
        source_file=Path(input_file),
        candles=candles_sel,
        folds=folds,
        epochs=epochs,
        install=install,
        cancel_event=cancel,
    )

    started = time.monotonic()

    def _cb(ev: Any) -> None:
        if json_mode:
            return  # JSON contract emits ONE payload at the end
        frac = "" if ev.fraction is None else f" [{ev.fraction * 100:4.1f}%]"
        style = {"failed": "red", "cancelled": "yellow"}.get(ev.status, "cyan")
        console.print(f"[{style}]{ev.stage}:{ev.status}{frac}[/] {ev.message}")
        m = ev.metrics or {}
        if m.get("epoch") is not None:
            console.print(
                f"    epoch {m.get('epoch')}/{m.get('epochs', '?')}  "
                f"loss {m.get('loss', '—')}  val_loss {m.get('val_loss', '—')}  "
                f"elapsed {time.monotonic() - started:.0f}s"
            )

    console.print(
        PanelBox(
            f"training locally (symbol={symbol}) — device: "
            f"{'CUDA ' + str(env.get('gpu_name')) if env.get('cuda') else 'CPU'} · "
            "NO market data leaves this machine"
        )
    )
    result = train_local_model(request, progress=_cb)
    if json_mode:
        _emit(result, True)
    else:
        console.print(
            f"\n[bold]outcome: {result.get('outcome')}[/bold]  "
            f"candidate: {result.get('candidate_model', '')}"
            + (
                f"\ninstalled to: {result.get('serving_path', '')}"
                if result.get("installed")
                else ""
            )
            + (f"\n{result.get('reason') or result.get('install_skipped') or ''}")
        )
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
    json_mode: bool = typer.Option(False, "--json"),
) -> None:
    """DEPRECATED alias of model-train-local using the live MT5 terminal as
    the data source (download to parquet, then the SAME local pipeline)."""
    console.print(
        "[yellow]train-once is deprecated — use `nexus model-train-local` "
        "(and `data-fetch` if you need the MT5 terminal export)[/yellow]"
    )
    bars_path = Path(f"data/raw/{symbol.upper()}_{timeframe.upper()}.parquet")
    if not bars_path.exists():
        data_fetch_into(
            bars_path, symbol=symbol, timeframe=timeframe, count=bars, json_mode=json_mode
        )
    train_local_main(
        input_file=bars_path,
        candles=bars,
        folds=folds,
        epochs=epochs,
        install=install,
        json_mode=json_mode,
        symbol=symbol,
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


def _has_torch() -> bool:
    from nexus_scalp.model_provisioning.pipeline import detect_ml_environment

    return bool(detect_ml_environment().get("torch"))


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


__all__ = [
    "model_official_cmd",
    "model_provision_cmd",
    "model_setup_cmd",
    "model_train_local_cmd",
    "train_once_cmd",
]
